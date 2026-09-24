"""No real order execution may exist anywhere in the product (PLAN.md Phase 1e).

The fork is research-only: broker integrations (Phase 2) are read-only by construction.
This test scans every Python source file for order-placement identifiers and broker order
endpoints, so an execution path cannot be added without failing CI.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCANNED = [ROOT / "src", *sorted((ROOT / "packages").glob("*/src")), ROOT / "server.py"]

FORBIDDEN_IDENTIFIER = re.compile(
    r"(?i)^(?:place_?orders?|modify_?orders?|cancel_?orders?|order_?place|exit_?orders?"
    r"|convert_?position|place_?gtt|modify_?gtt|place_?basket|submit_?orders?|execute_?trades?"
    r"|place_?trades?|send_?orders?)$"
)
FORBIDDEN_STRING = re.compile(
    r"(?i)(?:/orders?/(?:regular|amo|co|iceberg|auction|place|modify|cancel)\b"
    r"|/gtt/triggers|placeorder|modifyorder|cancelorder|/order/place|/v\d/order\b"
    r"|kite\.trade/orders|api\.upstox\.com/v\d/order)"
)


def _python_files() -> list[Path]:
    files: list[Path] = []
    for base in SCANNED:
        if base.is_file():
            files.append(base)
        elif base.is_dir():
            files.extend(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)
    return files


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        name = None
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            name = node.name
        elif isinstance(node, ast.Attribute):
            name = node.attr
        elif isinstance(node, ast.Name):
            name = node.id
        if name and FORBIDDEN_IDENTIFIER.match(name):
            found.append(f"{path.relative_to(ROOT)}:{node.lineno} identifier {name!r}")
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and FORBIDDEN_STRING.search(node.value)
        ):
            found.append(f"{path.relative_to(ROOT)}:{node.lineno} endpoint {node.value[:80]!r}")
    return found


def test_scanner_covers_the_codebase() -> None:
    files = _python_files()
    assert len(files) > 150
    assert any(p.name == "notifier.py" for p in files)


def test_detectors_catch_order_placement() -> None:
    assert FORBIDDEN_IDENTIFIER.match("place_order")
    assert FORBIDDEN_IDENTIFIER.match("placeOrder")
    assert FORBIDDEN_IDENTIFIER.match("cancel_orders")
    assert FORBIDDEN_STRING.search("https://api.kite.trade/orders/regular")
    assert FORBIDDEN_STRING.search("/rest/secure/angelbroking/order/v1/placeOrder")
    assert FORBIDDEN_STRING.search("https://api.upstox.com/v2/order/place")
    assert not FORBIDDEN_IDENTIFIER.match("order_by")
    assert not FORBIDDEN_STRING.search("/api/paper-trading/positions")


def test_no_order_execution_paths_exist() -> None:
    violations = [v for path in _python_files() for v in _violations(path)]
    assert violations == [], "order-execution code is not allowed:\n" + "\n".join(violations)
