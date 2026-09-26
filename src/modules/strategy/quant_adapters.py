"""Quant framework adapter interface (reserved for Phase 4; lightweight).

Defines one back-test backend protocol so different implementations can plug in later without changing the layers above:
- built-in (default, always available): src/core/backtest (pure Python lightweight core, Phase 0)
- optional upgrades (per the roadmap; not installed by default, keeping self-hosting light):
    · vectorbt: vectorised batch back-tests / factor grid search
    · rqalpha: high-fidelity A-share cost matching (stamp duty / price limits / trading calendar); not India-specific
    · qlib: ML factor research (Alpha158/360 + LightGBM, etc.)

Only the interface and "which backends are installed" detection live here; real integrations each write an adapter implementing this protocol.
See .docs/quant-framework-comparison.md for the selection rationale.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BacktestAdapter(Protocol):
    """Unified back-test backend interface. The built-in backtest.engine.Backtester already satisfies run()."""

    name: str

    def run(self, signals: list, bars_by_symbol: dict):  # noqa: D401
        """Back-test a batch of signals and return a result object with metrics."""
        ...


_OPTIONAL_BACKENDS = (
    ("vectorbt", "vectorbt"),
    ("rqalpha", "rqalpha"),
    ("qlib", "qlib"),
)


def available_backends() -> dict[str, bool]:
    """Detect available back-test backends. The built-in one is always available; optional heavy dependencies are reported if installed.

    For the UI / docs to show which backends this environment has; never installs anything.
    """
    backends: dict[str, bool] = {"builtin": True}
    for module_name, key in _OPTIONAL_BACKENDS:
        try:
            __import__(module_name)
            backends[key] = True
        except Exception:
            backends[key] = False
    return backends
