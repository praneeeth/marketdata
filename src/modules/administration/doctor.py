"""Command-line system self-check: `python -m src.modules.administration.doctor` or `make doctor`.

Runs the system items (DB/disk/scheduler) + data sources/AI/notifications in the terminal and prints results with fix hints.
The CLI process has no running scheduler, so the scheduler item is skipped gracefully (with a note, not a false alarm).
Exit code: 1 if anything failed, 0 if everything passed (for CI/scripts).
"""

from __future__ import annotations

import asyncio
import sys

from src.modules.administration.selfcheck import run_selfcheck

_ICON = {"ok": "✅", "slow": "⚠️", "fail": "❌"}
_CAT = {"system": "System", "datasource": "Data sources", "ai": "AI models", "notify": "Notification channels"}
_ORDER = ["system", "datasource", "ai", "notify"]


def _print_report(res: dict) -> None:
    s = res["summary"]
    print("\n===== Candlewise system self-check =====")
    print(f"{s['total']} items · ✅ ok {s['ok']} · ⚠️ slow {s['slow']} · ❌ failed {s['fail']}\n")
    items = res.get("items", [])
    for cat in _ORDER:
        cat_items = [i for i in items if i["category"] == cat]
        if not cat_items:
            continue
        print(f"[{_CAT.get(cat, cat)}]")
        for i in cat_items:
            icon = _ICON.get(i["status"], "?")
            grp = f"{i['group']} / " if i.get("group") else ""
            lat = f"  {i['latency_ms']}ms" if i.get("latency_ms") else ""
            print(f"  {icon} {grp}{i['name']}{lat}")
            if i["status"] == "fail":
                if i.get("error"):
                    print(f"       Error: {i['error']}")
                if i.get("hint"):
                    print(f"       Hint: {i['hint']}")
            elif i.get("note"):
                print(f"       {i['note']}")
        print()
    if s["fail"]:
        print(f"⚠️  {s['fail']} items failed; see the hints above.")
    else:
        print("✅ Everything is fine.")


def main() -> int:
    res = asyncio.run(run_selfcheck())
    _print_report(res)
    return 1 if res["summary"]["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
