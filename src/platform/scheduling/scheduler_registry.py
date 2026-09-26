"""Lightweight registry of running schedulers, read by the system self-check for health.

Each scheduler registers its APScheduler (with .running / .get_jobs()) in start();
the self-check's probe_scheduler uses it to tell whether schedulers are running. Processes without schedulers (such as the CLI) have an empty registry and are skipped gracefully.
"""

from __future__ import annotations

_REGISTRY: dict[str, object] = {}


def register(name: str, scheduler: object) -> None:
    _REGISTRY[name] = scheduler


def get_all() -> dict[str, object]:
    return dict(_REGISTRY)


def clear() -> None:
    _REGISTRY.clear()
