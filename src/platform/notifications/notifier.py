import logging
import os
import re

import apprise
import asyncio
import httpx

from src.platform.compliance import ensure_guarded, guard_title, with_short_disclaimer

logger = logging.getLogger(__name__)


def get_global_proxy() -> str:
    """获取全局 HTTP 代理设置"""
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
    """清理内容以适配 Telegram（移除 HTML 和 Markdown 格式）"""
    # 移除 HTML 标签
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

    # 移除 Markdown 格式
    # markdown 链接 [label](url) → "label url":Telegram 内联链接对 localhost/IP:端口 等
    # 非公网地址不渲染(标签退化成纯文本点不了),裸 URL 则会被自动识别为可点击,更稳。
    content = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1 \2", content)
    content = re.sub(r"^#{1,6}\s*", "", content, flags=re.MULTILINE)  # 移除标题 #
    content = re.sub(r"\*\*(.+?)\*\*", r"\1", content)  # 移除粗体 **
    content = re.sub(r"\*(.+?)\*", r"\1", content)  # 移除斜体 *
    content = re.sub(r"__(.+?)__", r"\1", content)  # 移除粗体 __
    content = re.sub(r"_(.+?)_", r"\1", content)  # 移除斜体 _
    content = re.sub(r"~~(.+?)~~", r"\1", content)  # 移除删除线
    content = re.sub(r"`(.+?)`", r"\1", content)  # 移除行内代码
    content = re.sub(
        r"^\s*[-*+]\s+", "· ", content, flags=re.MULTILINE
    )  # 列表符号改为 ·
    content = re.sub(
        r"^\s*\d+\.\s+", "", content, flags=re.MULTILINE
    )  # 移除有序列表数字

    # 清理多余空白
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

# 通过 Apprise 支持的渠道类型（无代理配置时）
_APPRISE_TYPES = {"telegram", "discord", "pushover"}

# 自定义实现的渠道类型（带代理或特殊需求）
_CUSTOM_IMPL_TYPES: set[str] = set()

# 支持 Markdown 的渠道（不需要 sanitize）
_MARKDOWN_CHANNELS = {"discord"}

# 不支持 Markdown 的渠道（需要 sanitize）
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
    根据渠道类型和配置构建 Apprise URL

    Returns:
        Apprise URL 或 None（如果需要使用自定义方式发送，如带代理的 Telegram）
    """
    if channel_type == "telegram":
        bot_token = config.get("bot_token", "")
        chat_id = config.get("chat_id", "")
        if not bot_token or not chat_id:
            raise ValueError("Telegram 需要 bot_token 和 chat_id")
        # 如果配置了代理（渠道级或全局），返回 None，使用自定义方式发送
        proxy = config.get("proxy", "").strip() or get_global_proxy()
        if proxy:
            return None
        return f"tgram://{bot_token}/{chat_id}"

    elif channel_type == "discord":
        webhook_id = config.get("webhook_id", "")
        webhook_token = config.get("webhook_token", "")
        if not webhook_id or not webhook_token:
            raise ValueError("Discord 需要 webhook_id 和 webhook_token")
        return f"discord://{webhook_id}/{webhook_token}/"

    elif channel_type == "pushover":
        user_key = config.get("user_key", "")
        app_token = config.get("app_token", "")
        if not user_key or not app_token:
            raise ValueError("Pushover 需要 user_key 和 app_token")
        return f"pover://{user_key}@{app_token}/"

    else:
        raise ValueError(f"不支持的 Apprise 渠道类型: {channel_type}")


class NotifierManager:
    """通知管理器: Apprise 渠道 + 自定义渠道"""

    def __init__(self, policy=None):
        self._ap = apprise.Apprise()
        self._custom_channels: list[tuple[str, dict]] = []
        self._channel_count = 0
        self._channel_types: set[str] = set()
        self.policy = policy

    def add_channel(self, channel_type: str, config: dict):
        """添加通知渠道"""
        self._channel_types.add(channel_type)
        try:
            if channel_type in _APPRISE_TYPES:
                url = build_apprise_url(channel_type, config)
                if url is None:
                    # 需要自定义实现（如带代理的 Telegram）
                    self._custom_channels.append((channel_type, config))
                    self._channel_count += 1
                    logger.info(f"注册自定义通知渠道: {channel_type} (带代理)")
                elif self._ap.add(url):
                    self._channel_count += 1
                    logger.info(f"注册通知渠道: {channel_type}")
                else:
                    logger.error(f"注册通知渠道失败: {channel_type} (URL 无效)")
            else:
                self._custom_channels.append((channel_type, config))
                self._channel_count += 1
                logger.info(f"注册自定义通知渠道: {channel_type}")
        except ValueError as e:
            logger.error(f"注册通知渠道失败: {e}")

    async def notify(self, title: str, content: str, images: list[str] | None = None):
        """向所有已注册渠道发送通知（忽略错误）"""
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
            logger.warning("没有可用的通知渠道")
            return {"success": False, "error": "没有可用的通知渠道"}

        # Quiet hours
        try:
            if not bypass_quiet_hours and getattr(self, "policy", None):
                if self.policy.is_quiet_now():
                    logger.info("当前处于通知静默时段，跳过发送")
                    return {"success": False, "skipped": "quiet_hours"}
        except Exception:
            # do not block sends on policy errors
            pass

        # 准备纯文本版本（用于不支持 Markdown 的渠道）
        plain_content = sanitize_for_telegram(content)

        # 准备附件
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

        # Apprise 渠道（使用纯文本，因为 Telegram 等不支持 Markdown）
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
                        logger.info(f"Apprise 通知发送成功: {title}")
                        break
                    last_err = "Apprise 通知发送失败（可能是网络问题或配置错误）"
                    logger.error(f"{last_err}: {title}")
                except Exception as e:
                    last_err = f"Apprise 通知异常: {e}"
                    logger.error(last_err)
                if attempt < retry_attempts:
                    await _sleep_retry(attempt + 1)
            if not apprise_ok:
                errors.append(last_err or "Apprise 通知发送失败")

        # 自定义渠道（根据渠道类型自动选择格式）
        for ch_type, config in self._custom_channels:
            ch_ok = False
            last_err = ""
            for attempt in range(0, retry_attempts + 1):
                try:
                    # 支持 Markdown 的渠道使用原始内容，否则使用纯文本
                    ch_content = (
                        content if ch_type in _MARKDOWN_CHANNELS else plain_content
                    )
                    await self._send_custom(ch_type, config, title, ch_content)
                    ch_ok = True
                    break
                except Exception as e:
                    last_err = f"{ch_type} 发送失败: {e}"
                    logger.error(last_err)
                if attempt < retry_attempts:
                    await _sleep_retry(attempt + 1)
            if not ch_ok:
                errors.append(last_err or f"{ch_type} 发送失败")

        if errors:
            return {"success": False, "error": "; ".join(errors)}
        return {"success": True}

    async def _send_custom(self, ch_type: str, config: dict, title: str, content: str):
        """发送自定义渠道通知"""
        if ch_type == "telegram":
            await self._send_telegram(config, title, content)
        else:
            logger.warning(f"未知的自定义渠道类型: {ch_type}")

    async def _send_telegram(self, config: dict, title: str, content: str):
        """Telegram Bot API（支持代理）

        Telegram 老 Markdown 解析很脆弱:
        - 不认 `**粗体**`(只认 `*粗体*`),GitHub 风格会导致 Can't find end of entity
        - 不认 `### 标题`(把 # 当普通字符,但 ### 后面可能被截断)
        - 单条上限 4096 字符,超过会被截断破坏实体
        发送前做兼容性预处理 + 截断。
        """
        bot_token = config.get("bot_token", "")
        chat_id = config.get("chat_id", "")
        # 渠道级代理优先，否则使用全局代理
        proxy = config.get("proxy", "").strip() or get_global_proxy()

        if not bot_token or not chat_id:
            raise ValueError("Telegram 需要 bot_token 和 chat_id")

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        # 用现成的 sanitize_for_telegram 把 markdown 完全剥成纯文本,
        # 避免 `**粗体**` / `## 标题` / 未闭合实体导致 Telegram parse 失败。
        # 标题外层手动加 `*...*` 让其加粗(Telegram 老 Markdown 只认单星号)。
        safe_title = sanitize_for_telegram(title) if title else ""
        safe_content = sanitize_for_telegram(content)
        text = f"*{safe_title}*\n\n{safe_content}" if safe_title else safe_content
        # Telegram 单条上限 4096,留点 buffer 给末尾提示
        if len(text) > 3900:
            # 正文末尾若带详情链接(经 sanitize 后已是裸 URL),直接截断会把它砍掉 →
            # 用户点不到。先抽出来,截断正文后再拼回末尾。
            link_m = re.search(r"(https?://[^\s)]+)\s*$", text)
            if link_m:
                notice = f"\n\n…内容过长已截断,完整报告 👉 {link_m.group(1)}"
            else:
                notice = "\n\n…内容过长已截断,完整报告请在 PanWatch 查看"
            text = text[: 3900 - len(notice)].rstrip() + notice
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }

        # 配置代理
        transport = None
        if proxy:
            transport = httpx.AsyncHTTPTransport(proxy=proxy)
            logger.debug(f"Telegram 使用代理: {proxy}")

        try:
            async with httpx.AsyncClient(transport=transport, timeout=30) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
                if not data.get("ok"):
                    raise RuntimeError(f"Telegram API 错误: {data.get('description')}")
                logger.info(f"Telegram 通知发送成功: {title}")
        except httpx.ConnectError as e:
            if proxy:
                raise RuntimeError(f"连接代理失败 ({proxy}): {e}")
            else:
                raise RuntimeError(f"无法连接 Telegram API（可能需要配置代理）: {e}")
        except httpx.TimeoutException:
            raise RuntimeError("请求超时（网络问题或代理配置错误）")
        except Exception as e:
            if (
                "ConnectError" in str(type(e).__name__)
                or "connection" in str(e).lower()
            ):
                if not proxy:
                    raise RuntimeError(f"网络连接失败，建议配置代理: {e}")
            raise
