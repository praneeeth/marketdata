"""Remove recommendation fields from structured payloads before they are stored or shown.

In research_only mode no payload that reaches a user may carry a buy/sell/hold action,
rating, entry range, stop-loss, target or quantity. Text values can also be guarded
recursively (``guard_strings=True``) for payloads rendered in the UI.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from src.platform.compliance.guard import ensure_guarded

ADVISORY_KEYS: frozenset[str] = frozenset(
    {
        "suggestion",
        "suggestions",
        "action_label",
        "rating",
        "rating_raw",
        "rating_label",
        "decision",
        "upstream_decision",
        "final_trade_decision",
        "trader_investment_plan",
        "investment_plan",
        "risk_judgment",
        "risk_debate",
        "risk_debate_state",
        "judge_decision",
        "stop_loss",
        "target_price",
        "entry_low",
        "entry_high",
        "entry_price_range",
        "position_size",
        "suggested_quantity",
        "quantity_suggestion",
        "take_profit",
        "invalidation",
        "invalidations",
        "triggers",
        "should_alert",
    }
)

ADVISORY_ACTION_VALUES: frozenset[str] = frozenset(
    {
        "buy",
        "add",
        "reduce",
        "sell",
        "hold",
        "watch",
        "alert",
        "avoid",
        "build",
        "strong_buy",
        "strong buy",
        "overweight",
        "underweight",
        "accumulate",
        # English action labels used by the agents (compared case-folded).
        "open position",
        "plan to open",
        "prepare to open",
        "plan to add",
        "plan to reduce",
        "prepare to add",
        "prepare to reduce",
        "consider exiting",
        "keep holding",
        "consider adding",
        "consider reducing",
        "consider stop loss",
        "exit",
        "watch tomorrow",
        "avoid for now",
        "set alert",
        # Chinese labels are kept on purpose: models can still answer in Chinese, and rows
        # stored before the English-only change carry them. This is detection data, not UI text.
        "买入",
        "卖出",
        "建仓",
        "加仓",
        "减仓",
        "清仓",
        "持有",
        "观望",
        "回避",
        "增持",
        "减持",
        "继续持有",
        "考虑加仓",
        "考虑减仓",
        "考虑止损",
        "明日关注",
        "暂时回避",
        "准备建仓",
        "准备加仓",
        "准备减仓",
        "设置预警",
        "关注",
    }
)

_URLISH = re.compile(r"^\s*(?:https?://|/)\S*\s*$")


def _is_advisory_action(key: str, value: object) -> bool:
    return (
        key == "action"
        and isinstance(value, str)
        and value.strip().casefold() in ADVISORY_ACTION_VALUES
    )


def sanitize_payload(obj: Any, *, guard_strings: bool = False, surface: str = "payload") -> Any:
    """Return a copy of ``obj`` without recommendation fields.

    Dict keys in :data:`ADVISORY_KEYS` are dropped, as is ``action`` when its value is a
    buy/sell/hold-style action. With ``guard_strings`` every string value (except bare
    URLs) passes through the output guard.
    """
    if isinstance(obj, Mapping):
        out: dict[Any, Any] = {}
        for key, value in obj.items():
            key_str = str(key)
            if key_str in ADVISORY_KEYS or _is_advisory_action(key_str, value):
                continue
            out[key] = sanitize_payload(value, guard_strings=guard_strings, surface=surface)
        return out
    if isinstance(obj, list | tuple):
        items = [sanitize_payload(v, guard_strings=guard_strings, surface=surface) for v in obj]
        return items if isinstance(obj, list) else tuple(items)
    if guard_strings and isinstance(obj, str) and obj.strip() and not _URLISH.match(obj):
        return ensure_guarded(obj, surface=surface)
    return obj
