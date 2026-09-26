"""Runtime failover for AI models.

Mirrors the mature fallback pattern on the data side (the `_FAIL_UNTIL` negative-cache cooldown in the
marketdata engine / kline_collector) to give AI calls "switch to a backup when the main model fails":

- **Candidate chain**: the main model plus backups in priority order. When a candidate fails, the error class decides
  between "retry the same model without the parameter / fall back to the next candidate / raise".
- **Error classes** (the key part):
  - incompatible parameter (e.g. a model that rejects temperature) -> drop temperature and retry **the same model** once;
  - timeout / 5xx / rate limit / quota / auth expired / service down -> **fall back to the next candidate** and put this one in cooldown;
  - prompt / content-policy errors -> **raise without retrying** (another model would fail the same way).
- **Negative-cache cooldown**: copied from `kline_collector._FAIL_UNTIL`: a failed candidate enters a cooldown window and
  is skipped without any network call; once the window expires it is tried again, which is the "recovery probe".
- **Observable**: the model actually used is recorded in `used_model_label` (saved to agent_runs); a switch
  logs a warning (with trace_id).

`FailoverAIClient` exposes the same `chat / chat_multi / chat_with_tools /
chat_stream` signatures as `AIClient`, so it can replace a single client in place, and passes through `base_url / api_key / model /
total_tokens_used` and other attributes for callers such as TradingAgents that need the underlying config.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)

from src.platform.ai.ai_client import AIClient
from src.platform.observability.log_context import get_log_context

from src.platform.compliance import guard_chat_stream, guard_text

logger = logging.getLogger(__name__)

# ── Error classes ─────────────────────────────────────────────────────
ERR_PARAM = "param"    # incompatible parameter: drop it and retry the same model
ERR_SWITCH = "switch"  # recoverable: next candidate + cooldown
ERR_FATAL = "fatal"    # not recoverable: raise (prompt/content errors)

# ── Negative-cache cooldown (same pattern as kline_collector) ─────────
# key = model label (e.g. "DeepSeek/deepseek-chat"); value = monotonic timestamp when the cooldown ends.
_AI_FAIL_UNTIL: dict[str, float] = {}
_AI_FAIL_COOLDOWN_S = 60.0


def clear_ai_failover_state() -> None:
    """Clear cooldown state (for test isolation)."""
    _AI_FAIL_UNTIL.clear()


def _is_cooling(label: str) -> bool:
    return time.monotonic() < _AI_FAIL_UNTIL.get(label, 0.0)


def _mark_fail(label: str) -> None:
    _AI_FAIL_UNTIL[label] = time.monotonic() + _AI_FAIL_COOLDOWN_S


def _mark_ok(label: str) -> None:
    # Success clears the cooldown (recovery).
    _AI_FAIL_UNTIL.pop(label, None)


def _looks_like_param_error(exc: Exception) -> bool:
    """Whether a 400/422 is an "incompatible parameter" (retry without it) rather than a content problem."""
    msg = str(exc).lower()
    keywords = (
        "temperature",
        "unsupported parameter",
        "unsupported value",
        "does not support",
        "unknown parameter",
        "extra fields",
        "not supported",
    )
    return any(k in msg for k in keywords)


def classify_ai_error(exc: Exception) -> str:
    """Classify an AI call exception as ERR_PARAM / ERR_SWITCH / ERR_FATAL."""
    # Network / timeout / 5xx / rate limit / auth expired / permission -> switch models
    if isinstance(
        exc,
        (
            APITimeoutError,
            APIConnectionError,
            RateLimitError,
            InternalServerError,
            AuthenticationError,
            PermissionDeniedError,
        ),
    ):
        return ERR_SWITCH
    # 400 / 422: tell "incompatible parameter" (retry without it) from "content/prompt problem" (raise)
    if isinstance(exc, BadRequestError):
        return ERR_PARAM if _looks_like_param_error(exc) else ERR_FATAL
    # Other exceptions with an HTTP status: 5xx is recoverable, 4xx is fatal
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return ERR_SWITCH if status >= 500 else ERR_FATAL
    # Unknown exceptions: fall back conservatively (the next candidate may be another provider, or the chain ends and raises)
    return ERR_SWITCH


class FailoverAIClient:
    """AI client wrapper that tries the candidate chain in order.

    Args:
        candidates: [(AIClient, model label), ...]; item 0 is the main model.
        on_switch: optional callback (from_label, exc), called on a fallback switch,
            so the chat SSE endpoint can push the failover event to the frontend (optional).
    """

    def __init__(
        self,
        candidates: list[tuple[AIClient, str]],
        on_switch: Callable[[str, Exception], None] | None = None,
    ):
        if not candidates:
            raise ValueError("FailoverAIClient needs at least one candidate model")
        self.candidates = candidates
        self.on_switch = on_switch
        # Label of the model actually used; the main model by default, updated to the one that succeeded.
        self.used_model_label = candidates[0][1]

    # ── Passed-through attributes (for callers that treat this as a plain AIClient) ──
    @property
    def _primary(self) -> AIClient:
        return self.candidates[0][0]

    @property
    def client(self):
        return self._primary.client

    @property
    def base_url(self) -> str:
        return self._primary.base_url

    @property
    def api_key(self) -> str:
        return self._primary.api_key

    @property
    def model(self) -> str:
        return self._primary.model

    @property
    def total_tokens_used(self) -> int:
        return sum(c.total_tokens_used for c, _ in self.candidates)

    @property
    def last_usage(self):
        """Expose the latest provider usage from the model that last ran."""
        for client, label in self.candidates:
            if label == self.used_model_label:
                return getattr(client, "last_usage", None)
        return None

    async def list_models(self) -> list[str]:
        return await self._primary.list_models()

    # ── Candidate selection: non-cooling first; if all are cooling, probe the main one ──
    def _iter_candidates(self) -> list[tuple[AIClient, str]]:
        live = [(c, lbl) for c, lbl in self.candidates if not _is_cooling(lbl)]
        if live:
            return live
        # Everything is in cooldown: return the main candidate (ignoring cooldown) as a recovery probe instead of failing.
        return self.candidates[:1]

    def _log_switch(self, label: str, exc: Exception) -> None:
        trace_id = get_log_context().get("trace_id") or "-"
        logger.warning(
            "[%s] AI failover: model %s failed; falling back to the next candidate: %s",
            trace_id,
            label,
            exc,
        )
        if self.on_switch is not None:
            try:
                self.on_switch(label, exc)
            except Exception:  # noqa: BLE001 — the callback must not affect the main flow
                logger.debug("on_switch callback raised (ignored)", exc_info=True)

    async def _run(self, method_name: str, *args, temperature, **kwargs):
        """Shared failover runner for the non-streaming methods."""
        last_exc: Exception | None = None
        for client, label in self._iter_candidates():
            method = getattr(client, method_name)
            try:
                result = await method(*args, temperature=temperature, **kwargs)
                _mark_ok(label)
                self.used_model_label = label
                return result
            except Exception as exc:  # noqa: BLE001
                kind = classify_ai_error(exc)
                if kind == ERR_PARAM:
                    # Drop temperature and retry the same model once
                    try:
                        retry_kwargs = dict(kwargs)
                        retry_kwargs["temperature"] = None
                        # Some OpenAI-compatible services reject max_tokens;
                        # a summary can still fall back to local normalization.
                        retry_kwargs.pop("max_tokens", None)
                        result = await method(*args, **retry_kwargs)
                        _mark_ok(label)
                        self.used_model_label = label
                        return result
                    except Exception as exc2:  # noqa: BLE001
                        exc = exc2
                        kind = classify_ai_error(exc2)
                if kind == ERR_FATAL:
                    raise
                # ERR_SWITCH: record the cooldown, log, and try the next candidate
                _mark_fail(label)
                last_exc = exc
                self._log_switch(label, exc)
                continue
        raise last_exc or RuntimeError("All candidate models failed")

    # Every model output leaving this client passes the compliance guard (ADR-002):
    # this is the default layer for all non-TradingAgents LLM calls. Sinks guard again.

    async def chat(
        self,
        system_prompt: str,
        user_content: str,
        images: list[str] | None = None,
        temperature: float | None = 0.4,
    ) -> str:
        content = await self._run(
            "chat", system_prompt, user_content, images=images, temperature=temperature
        )
        return guard_text(content, surface="ai_chat").text

    async def chat_multi(
        self,
        messages: list[dict],
        temperature: float | None = 0.4,
        max_tokens: int | None = None,
    ) -> str:
        kwargs = {"max_tokens": max_tokens} if max_tokens is not None else {}
        content = await self._run(
            "chat_multi", messages, temperature=temperature, **kwargs
        )
        return guard_text(content, surface="ai_chat").text

    async def chat_with_tools(
        self, messages: list[dict], tools: list[dict], temperature: float | None = 0.4
    ):
        message = await self._run(
            "chat_with_tools", messages, tools, temperature=temperature
        )
        if getattr(message, "content", None):
            message.content = guard_text(message.content, surface="ai_chat").text
        return message

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = 0.4,
        tool_choice: str | None = None,
    ):
        """Guarded stream: tokens are released sentence by sentence after checking."""
        async for event in guard_chat_stream(
            self._chat_stream_raw(
                messages, tools=tools, temperature=temperature, tool_choice=tool_choice
            ),
            surface="ai_stream",
        ):
            yield event

    async def _chat_stream_raw(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = 0.4,
        tool_choice: str | None = None,
    ):
        """Streaming failover.

        Note: once a token has been produced, a later failure can't be rolled back (it has reached the frontend), so failover
        only covers failures "before the first token"; errors after that propagate.
        """
        last_exc: Exception | None = None
        for client, label in self._iter_candidates():
            started = False
            try:
                stream_kwargs = {"tools": tools, "temperature": temperature}
                if tool_choice is not None:
                    stream_kwargs["tool_choice"] = tool_choice
                async for ev in client.chat_stream(messages, **stream_kwargs):
                    started = True
                    yield ev
                _mark_ok(label)
                self.used_model_label = label
                return
            except Exception as exc:  # noqa: BLE001
                if started:
                    raise
                kind = classify_ai_error(exc)
                if kind == ERR_PARAM:
                    try:
                        retry_kwargs = {"tools": tools, "temperature": None}
                        if tool_choice is not None:
                            retry_kwargs["tool_choice"] = tool_choice
                        async for ev in client.chat_stream(messages, **retry_kwargs):
                            started = True
                            yield ev
                        _mark_ok(label)
                        self.used_model_label = label
                        return
                    except Exception as exc2:  # noqa: BLE001
                        if started:
                            raise
                        exc = exc2
                        kind = classify_ai_error(exc2)
                if kind == ERR_FATAL:
                    raise
                _mark_fail(label)
                last_exc = exc
                self._log_switch(label, exc)
                continue
        raise last_exc or RuntimeError("All candidate models failed (streaming)")


def _make_client(base_url: str, api_key: str, model: str, proxy: str) -> AIClient:
    return AIClient(base_url=base_url, api_key=api_key, model=model, proxy=proxy)


def build_failover_client(
    primary_model,
    primary_service,
    proxy: str = "",
    *,
    db=None,
    settings=None,
    max_fallbacks: int = 3,
) -> FailoverAIClient:
    """Build the candidate chain from the main model plus the other models in the DB.

    Args:
        primary_model / primary_service: the main model already chosen by the upper four/three-level routing (may be detached
            ORM objects; fields are only read, no lazy load). If either is empty, environment variables provide the main candidate.
        proxy: HTTP proxy.
        db: optional Session; reused when given, otherwise a read-only session is opened to query backups.
        settings: optional Settings (for the environment-variable fallback).
        max_fallbacks: maximum number of backups besides the main model.

    Consistent with four-level routing: the main candidate uses the result already resolved above; backups follow `is_default`
    first and then id order, reusing the existing AIService/AIModel config with no new global config.
    """
    from src.platform.runtime.config import Settings

    candidates: list[tuple[AIClient, str]] = []
    primary_model_id = None

    if primary_model and primary_service:
        candidates.append(
            (
                _make_client(
                    primary_service.base_url,
                    primary_service.api_key,
                    primary_model.model,
                    proxy,
                ),
                f"{primary_service.name}/{primary_model.model}",
            )
        )
        primary_model_id = getattr(primary_model, "id", None)
    else:
        s = settings or Settings()
        candidates.append(
            (
                _make_client(s.ai_base_url, s.ai_api_key, s.ai_model, proxy),
                f"env/{s.ai_model}",
            )
        )

    # Add the backup candidates
    own_session = False
    if db is None:
        from src.platform.persistence.database import SessionLocal

        db = SessionLocal()
        own_session = True
    try:
        from src.platform.persistence.models import AIModel, AIService

        rows = (
            db.query(AIModel)
            .order_by(AIModel.is_default.desc(), AIModel.id.asc())
            .all()
        )
        for m in rows:
            if len(candidates) >= max_fallbacks + 1:
                break
            if primary_model_id is not None and m.id == primary_model_id:
                continue
            svc = db.query(AIService).filter(AIService.id == m.service_id).first()
            if not svc:
                continue
            candidates.append(
                (
                    _make_client(svc.base_url, svc.api_key, m.model, proxy),
                    f"{svc.name}/{m.model}",
                )
            )
    except Exception:  # noqa: BLE001 — a failed backup query doesn't stop the main candidate
        logger.warning("Failed to build failover backups; using the main model only", exc_info=True)
    finally:
        if own_session:
            db.close()

    return FailoverAIClient(candidates)


def get_configured_failover_client(db, model_id: int | None = None) -> FailoverAIClient:
    """Select the requested/default persisted model and build its failover chain.

    HTTP routers in several business modules need an AI client.  Model selection is
    infrastructure composition, so callers use this platform function instead of
    borrowing a helper from the assistant's HTTP router.
    """
    from src.platform.persistence.models import AIModel, AIService

    model = None
    if model_id:
        model = db.query(AIModel).filter(AIModel.id == model_id).first()
    if not model:
        model = db.query(AIModel).filter(AIModel.is_default == True).first()  # noqa: E712
    if not model:
        model = db.query(AIModel).first()
    service = db.query(AIService).filter(AIService.id == model.service_id).first() if model else None
    return build_failover_client(model, service, db=db)
