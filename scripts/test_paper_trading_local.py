#!/usr/bin/env python3
"""Local end-to-end test script for simulation notifications.

Usage:
    1. start the service first: python server.py
    2. run this script: python scripts/test_paper_trading_local.py <username> <password>
"""

import sys
import requests

BASE_URL = "http://localhost:8000"
TIMEOUT = 10
TOKEN = ""


def _api(method: str, path: str, **kwargs):
    url = f"{BASE_URL}{path}"
    headers = kwargs.pop("headers", {})
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    kwargs.setdefault("timeout", TIMEOUT)
    try:
        resp = getattr(requests, method)(url, headers=headers, **kwargs)
    except requests.Timeout:
        print(f"  {method.upper()} {path} -> TIMEOUT")
        return 0, {"error": "Request timed out"}
    except requests.ConnectionError:
        print(f"  {method.upper()} {path} -> CONNECTION ERROR")
        return 0, {"error": "Connection failed"}
    print(f"  {method.upper()} {path} -> {resp.status_code}")
    try:
        data = resp.json()
        # Unwrap the uniform response format {"code":0, "data": {...}}
        if isinstance(data, dict) and "data" in data and "code" in data:
            data = data["data"]
    except Exception:
        data = resp.text
    return resp.status_code, data


def step(name: str):
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")


def main():
    global TOKEN

    if len(sys.argv) < 3:
        print(f"Usage: python {sys.argv[0]} <username> <password>")
        sys.exit(1)

    username, password = sys.argv[1], sys.argv[2]

    # 0. Connect & sign in
    step("0. Connect & sign in")
    try:
        resp = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"username": username, "password": password},
            timeout=TIMEOUT,
        )
        print(f"  POST /api/auth/login -> {resp.status_code}")
        if resp.status_code == 200:
            body = resp.json()
            # Handles the wrapped {"data": {"token": ...}} and bare {"token": ...} responses
            TOKEN = (body.get("data") or body).get("token", "")
            print(f"  Signed in, token length: {len(TOKEN)}")
        else:
            print(f"  Sign-in failed: {resp.text}")
            sys.exit(1)
    except requests.ConnectionError:
        print("  Can't connect to the service; run python server.py first")
        sys.exit(1)
    except requests.Timeout:
        print("  Connection timed out")
        sys.exit(1)

    # Verify the token
    code, data = _api("get", "/api/paper-trading/account")
    if code == 401:
        print(f"  Auth failed: {data}")
        sys.exit(1)
    cap = data.get("current_capital", "?") if isinstance(data, dict) else "?"
    print(f"  Auth OK, account cash: {cap}")

    # 1. Reset the simulation
    step("1. Reset the simulation account")
    code, data = _api("post", "/api/paper-trading/account/reset")
    print(f"  Result: {data}")

    # 2. Test notification channel connectivity
    step("2. Test the notification channels")
    code, data = _api("post", "/api/paper-trading/notify-test")
    print(f"  Result: {data}")
    if code != 200:
        print("  Notification channels aren't reachable; later notifications may not be sent (set them up in the web UI)")

    # 3. Trigger the pre-market plan
    step("3. Trigger the pre-market plan notification")
    code, data = _api("post", "/api/paper-trading/premarket-plan")
    print(f"  Result: {data}")
    print("  -> Check the channel received the pre-market plan (verifies deduplication)")

    # 4. Trigger a scan (automatic entries)
    step("4. Trigger a scan")
    code, data = _api("post", "/api/paper-trading/scan", timeout=120)
    print(f"  Result: {data}")
    if isinstance(data, dict):
        opened = data.get("opened", 0)
        closed = data.get("closed", 0)
        print(f"  Opened: {opened}, closed: {closed}")
        if opened > 0:
            print("  -> Check the channel received the entry notification")

    # 5. Current positions
    step("5. Current positions")
    code, data = _api("get", "/api/paper-trading/positions")
    positions = data if isinstance(data, list) else data.get("positions", [])
    print(f"  Positions: {len(positions)}")
    for p in positions[:5]:
        if isinstance(p, dict):
            print(f"    {p.get('stock_name', '')} ({p.get('stock_symbol', '')}) "
                  f"entry: {p.get('entry_price', 0):.2f}")

    # 6. Close the first position manually
    if positions:
        step("6. Manual close")
        first = positions[0]
        pos_id = first.get("id") if isinstance(first, dict) else None
        if pos_id:
            code, data = _api("post", f"/api/paper-trading/positions/{pos_id}/close")
            print(f"  Result: {data}")
            print("  -> Check the channel received the exit notification")
    else:
        step("6. Manual close (skipped; no positions)")

    # 7. Trade history
    step("7. Trade history")
    code, data = _api("get", "/api/paper-trading/trades")
    trades = data.get("items", []) if isinstance(data, dict) else data
    print(f"  Trades: {len(trades)}")
    for t in trades[:5]:
        if isinstance(t, dict):
            pnl = t.get("pnl", 0)
            sign = "+" if pnl >= 0 else ""
            print(f"    {t.get('stock_name', '')} P&L: {sign}{pnl:.2f} ({t.get('exit_reason', '')})")

    # 8. Trigger the end-of-day summary
    step("8. Trigger the end-of-day summary notification")
    code, data = _api("post", "/api/paper-trading/daily-summary")
    print(f"  Result: {data}")
    print("  -> Check the channel received the end-of-day summary")

    step("Test complete")
    print("  Check the notification channels to confirm every notification arrived.\n")


if __name__ == "__main__":
    main()
