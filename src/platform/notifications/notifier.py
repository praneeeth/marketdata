import logging
import os
import re

import apprise
import asyncio
import httpx

from src.platform.compliance import ensure_guarded, guard_title, with_short_disclaimer

logger = logging.getLogger(__name__)


def get_global_proxy() -> str:
    """Get the global HTTP proxy setting."""
    try:
        from src.platform.persistence.database import SessionLocal
        from src.platform.persistence.models import AppSettings

        db = SessionLocal()
        try:
            setting = (
                db.query(AppSettings).filter(AppSettings.key == "http_proxy").first()
            )
            return setting.value if setting and setting.value else ""
        finally:
            db.close()
    except Exception:
        return ""


def sanitize_for_telegram(content: str) -> str:
    """Clean content for Telegram (strip HTML and Markdown formatting)."""
    # Strip HTML tags
    content = re.sub(r"</?table[^>]*>", "", content)
    content = re.sub(r"</?thead[^>]*>", "", content)
    content = re.sub(r"</?tbody[^>]*>", "", content)
    content = re.sub(r"</?tr[^>]*>", "\n", content)
    content = re.sub(r"</?th[^>]*>", " | ", content)
    content = re.sub(r"</?td[^>]*>", " | ", content)
    content = re.sub(r"</?div[^>]*>", "", content)
    content = re.sub(r"</?span[^>]*>", "", content)
    content = re.sub(r"</?p[^>]*>", "\n", content)
    content = re.sub(r"<br\s*/?>", "\n", content)

    # Strip Markdown formatting
    # markdown link [label](url) -> "label url": Telegram does not render inline links to
    # non-public addresses such as localhost/IP:port (the label becomes unclickable text); a bare URL is auto-linked.
    content = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1 \2", content)
    content = re.sub(r"^#{1,6}\s*", "", content, flags=re.MULTILINE)  # headings #
    content = re.sub(r"\*\*(.+?)\*\*", r"\1", content)  # bold **
    content = re.sub(r"\*(.+?)\*", r"\1", content)  # italic *
    content = re.sub(r"__(.+?)__", r"\1", content)  # bold __
    content = re.sub(r"_(.+?)_", r"\1", content)  # italic _
    content = re.sub(r"~~(.+?)~~", r"\1", content)  # strikethrough
    content = re.sub(r"`(.+?)`", r"\1", content)  # inline code
    content = re.sub(
        r"^\s*[-*+]\s+", "· ", content, flags=re.MULTILINE
    )  # list bullets become ·
    content = re.sub(
        r"^\s*\d+\.\s+", "", content, flags=re.MULTILINE
    )  # strip ordered-list numbers

    # Collapse extra whitespace
    content = re.sub(r"\n\s*\n\s*\n", "\n\n", content)
    content = re.sub(r" +", " ", content)
    return content.strip()


# Channel types (label + form fields). Chinese channels (DingTalk, WeCom, Lark/Feishu,
# ServerChan, PushPlus, Bark) were removed (decision Q15); WhatsApp and email arrive in
# Phase 6.
CHANNEL_TYPES = {
    "telegram": {
        "label": "Telegram",
        "fields": ["bot_token", "chat_id", "proxy"],
    },
    "discord": {
        "label": "Discord",
        "fields": ["webhook_id", "webhook_token"],
    },
    "pushover": {
        "label": "Pushover",
        "fields": ["user_key", "app_token"],
    },
}

# Channel types sent through Apprise (when no proxy is configured)
_APPRISE_TYPES = {"telegram", "discord", "pushover"}

# Channel types with a custom implementation (proxy or special needs)
_CUSTOM_IMPL_TYPES: set[str] = set()

# Channels that support Markdown (no sanitising)
_MARKDOWN_CHANNELS = {"discord"}

# Channels without Markdown support (sanitised)
_PLAIN_TEXT_CHANNELS = {"telegram", "pushover"}

# Per-channel body budgets (characters, including the disclaimer). Content is truncated
# to fit before the disclaimer is appended, so channel-side truncation (e.g. Telegram's
# 3,900-character cut in _send_telegram) can never remove the disclaimer.
CHANNEL_TEXT_BUDGETS = {
    "telegram": 3500,
    "discord": 1800,
    "pushover": 900,
}
DEFAULT_TEXT_BUDGET = 4000


def build_apprise_url(channel_type: str, config: dict) -> str | None:
    """
    Build the Apprise URL for a channel type and config.

    Returns:
        The Apprise URL, or None when a custom sender is needed (e.g. Telegram through a proxy)
    """
    if channel_type == "telegram":
        bot_token = config.get("bot_token", "")
        chat_id = config.get("chat_id", "")
        if not bot_token or not chat_id:
            raise ValueError("Telegram needs bot_token and chat_id")
        # With a proxy configured (per channel or global), return None and use the custom sender
        proxy = config.get("proxy", "").strip() or get_global_proxy()
        if proxy:
            return None
        return f"tgram://{bot_token}/{chat_id}"

    elif channel_type == "discord":
        webhook_id = config.get("webhook_id", "")
        webhook_token = config.get("webhook_token", "")
        if not webhook_id or not webhook_token:
            raise ValueError("Discord needs webhook_id and webhook_token")
        return f"discord://{webhook_id}/{webhook_token}/"

    elif channel_type == "pushover":
        user_key = config.get("user_key", "")
        app_token = config.get("app_token", "")
        if not user_key or not app_token:
            raise ValueError("Pushover needs user_key and app_token")
        return f"pover://{user_key}@{app_token}/"

    else:
        raise ValueError(f"Unsupported Apprise channel type: {channel_type}")


class NotifierManager:
    """Notification manager: Apprise channels plus custom channels."""

    def __init__(self, policy=None):
        self._ap = apprise.Apprise()
        self._custom_channels: list[tuple[str, dict]] = []
        self._channel_count = 0
        self._channel_types: set[str] = set()
        self.policy = policy

    def add_channel(self, channel_type: str, config: dict):
        """Add a notification channel."""
        self._channel_types.add(channel_type)
        try:
            if channel_type in _APPRISE_TYPES:
                url = build_apprise_url(channel_type, config)
                if url is None:
                    # Needs the custom implementation (e.g. Telegram through a proxy)
                    self._custom_channels.append((channel_type, config))
                    self._channel_count += 1
                    logger.info(f"Registered custom notification channel: {channel_type} (proxy)")
                elif self._ap.add(url):
                    self._channel_count += 1
                    logger.info(f"Registered notification channel: {channel_type}")
                else:
                    logger.error(f"Failed to register notification channel: {channel_type} (invalid URL)")
            else:
                self._custom_channels.append((channel_type, config))
                self._channel_count += 1
                logger.info(f"Registered custom notification channel: {channel_type}")
        except ValueError as e:
            logger.error(f"Failed to register notification channel: {e}")

    async def notify(self, title: str, content: str, images: list[str] | None = None):
        """Send to every registered channel (errors ignored)."""
        await self.notify_with_result(title, content, images)

    def _text_budget(self) -> int:
        budgets = [CHANNEL_TEXT_BUDGETS.get(t, DEFAULT_TEXT_BUDGET) for t in self._channel_types]
        return min(budgets) if budgets else DEFAULT_TEXT_BUDGET

    async def notify_with_result(
        self,
        title: str,
        content: str,
        images: list[str] | None = None,
        *,
        bypass_quiet_hours: bool = False,
    ) -> dict:
        """Guard, append the disclaimer, then deliver to every channel.

        This is the only public send path: every notification in the app passes the
        compliance guard here and carries the short disclaimer (ADR-002).
        """
        safe_title = guard_title(title, surface="notification_title")
        safe_content = ensure_guarded(content, surface="notification")
        budget = self._text_budget() - len(safe_title)
        final_content = with_short_disclaimer(safe_content, max_chars=max(budget, 400))
        return await self._deliver(
            safe_title,
            final_content,
            images,
            bypass_quiet_hours=bypass_quiet_hours,
        )

    async def _deliver(
        self,
        title: str,
        content: str,
        images: list[str] | None = None,
        *,
        bypass_quiet_hours: bool = False,
    ) -> dict:
        """Transport only. Never call directly; use notify_with_result."""
        if self._channel_count == 0:
            logger.warning("No notification channel available")
            return {"success": False, "error": "No notification channel available"}

        # Quiet hours
        try:
            if not bypass_quiet_hours and getattr(self, "policy", None):
                if self.policy.is_quiet_now():
                    logger.info("Inside the notification quiet hours; not sending")
                    return {"success": False, "skipped": "quiet_hours"}
        except Exception:
            # do not block sends on policy errors
            pass

        # Plain-text version (for channels without Markdown)
        plain_content = sanitize_for_telegram(content)

        # Attachments
        attachments = None
        if images:
            attachments = apprise.AppriseAttachment()
            for img_path in images:
                if img_path and os.path.exists(img_path):
                    attachments.add(img_path)

        errors = []

        retry_attempts = 0
        backoff = 0.0
        try:
            if getattr(self, "policy", None):
                retry_attempts = max(0, int(self.policy.retry_attempts))
                backoff = float(self.policy.retry_backoff_seconds or 0.0)
        except Exception:
            retry_attempts = 0
            backoff = 0.0

        async def _sleep_retry(i: int):
            if backoff <= 0:
                return
            await asyncio.sleep(backoff * (2 ** max(0, i - 1)))

        # Apprise channels (plain text, since Telegram and others don't support Markdown)
        if len(self._ap) > 0:
            apprise_ok = False
            last_err = ""
            for attempt in range(0, retry_attempts + 1):
                try:
                    success = await self._ap.async_notify(
                        title=title,
                        body=plain_content,
                        body_format=apprise.NotifyFormat.TEXT,
                        attach=attachments,
                    )
                    if success:
                        apprise_ok = True
                        logger.info(f"Apprise notification sent: {title}")
                        break
                    last_err = "Apprise notification failed (network problem or bad config)"
                    logger.error(f"{last_err}: {title}")
                except Exception as e:
                    last_err = f"Apprise notification error: {e}"
                    logger.error(last_err)
                if attempt < retry_attempts:
                    await _sleep_retry(attempt + 1)
            if not apprise_ok:
                errors.append(last_err or "Apprise notification failed")

        # Custom channels (format chosen by channel type)
        for ch_type, config in self._custom_channels:
            ch_ok = False
            last_err = ""
            for attempt in range(0, retry_attempts + 1):
                try:
                    # Markdown channels get the original content, others plain text
                    ch_content = (
                        content if ch_type in _MARKDOWN_CHANNELS else plain_content
                    )
                    await self._send_custom(ch_type, config, title, ch_content)
                    ch_ok = True
                    break
                except Exception as e:
                    last_err = f"{ch_type} send failed: {e}"
                    logger.error(last_err)
                if attempt < retry_attempts:
                    await _sleep_retry(attempt + 1)
            if not ch_ok:
                errors.append(last_err or f"{ch_type} send failed")

        if errors:
            return {"success": False, "error": "; ".join(errors)}
        return {"success": True}

    async def _send_custom(self, ch_type: str, config: dict, title: str, content: str):
        """Send through a custom channel."""
        if ch_type == "telegram":
            await self._send_telegram(config, title, content)
        else:
            logger.warning(f"Unknown custom channel type: {ch_type}")

    async def _send_telegram(self, config: dict, title: str, content: str):
        """Telegram Bot API (proxy supported).

        Telegram's legacy Markdown parser is fragile:
        - it doesn't understand `**bold**` (only `*bold*`); GitHub style causes "Can't find end of entity"
        - it doesn't understand `### heading` (# is a plain character, and text after ### may be cut)
        - one message is limited to 4096 characters; longer text is cut and breaks entities
        So the content is made compatible and truncated before sending.
        """
        bot_token = config.get("bot_token", "")
        chat_id = config.get("chat_id", "")
        # A per-channel proxy wins; otherwise the global proxy
        proxy = config.get("proxy", "").strip() or get_global_proxy()

        if not bot_token or not chat_id:
            raise ValueError("Telegram needs bot_token and chat_id")

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        # sanitize_for_telegram strips markdown to plain text so that
        # `**bold**` / `## heading` / unclosed entities can't break Telegram parsing.
        # The title is wrapped in `*...*` to make it bold (legacy Markdown only knows single asterisks).
        safe_title = sanitize_for_telegram(title) if title else ""
        safe_content = sanitize_for_telegram(content)
        text = f"*{safe_title}*\n\n{safe_content}" if safe_title else safe_content
        # Telegram's limit is 4096; leave a buffer for the closing notice
        if len(text) > 3900:
            # If the body ends with a details link (a bare URL after sanitising), truncation would cut it
            # and the user couldn't open it. Pull it out, truncate the body, then append it again.
            link_m = re.search(r"(https?://[^\s)]+)\s*$", text)
            if link_m:
                notice = f"\n\n…Message too long and was truncated. Full report 👉 {link_m.group(1)}"
            else:
                notice = "\n\n…Message too long and was truncated. See the full report in the app"
            text = text[: 3900 - len(notice)].rstrip() + notice
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }

        # Proxy
        transport = None
        if proxy:
            transport = httpx.AsyncHTTPTransport(proxy=proxy)
            logger.debug(f"Telegram using proxy: {proxy}")

        try:
            async with httpx.AsyncClient(transport=transport, timeout=30) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
                if not data.get("ok"):
                    raise RuntimeError(f"Telegram API error: {data.get('description')}")
                logger.info(f"Telegram notification sent: {title}")
        except httpx.ConnectError as e:
            if proxy:
                raise RuntimeError(f"Could not connect to the proxy ({proxy}): {e}")
            else:
                raise RuntimeError(f"Could not reach the Telegram API (a proxy may be needed): {e}")
        except httpx.TimeoutException:
            raise RuntimeError("Request timed out (network problem or bad proxy config)")
        except Exception as e:
            if (
                "ConnectError" in str(type(e).__name__)
                or "connection" in str(e).lower()
            ):
                if not proxy:
                    raise RuntimeError(f"Network connection failed; consider configuring a proxy: {e}")
            raise
