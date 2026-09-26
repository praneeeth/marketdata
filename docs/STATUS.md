# Status

As of 2026-09-27, branch `feat/ui-lamplight` plus these docs. Nothing is merged to `main`
([BUILD-HISTORY.md](BUILD-HISTORY.md)).

**Legend:**

- **Done:** built, tested, and exercised in the running app.
- **Built, unverified:** code and tests exist, but it has never run against the real
  external system.
- **Partial:** some of the planned scope exists.
- **Not started.**

## Features

### Core app

| Feature | Status | Notes |
| --- | --- | --- |
| Web app (FastAPI + React) | Done | Run locally on macOS: backend on :8000, frontend dev server on :5183 |
| Single-user login (username + password, JWT) | Done, **not production-safe** | Unsalted SHA-256 hash, JWT in `localStorage` for 30 days, API open until a password is set, CORS `*`, no rate limiting |
| Disclaimer consent (versioned, stored on the server) | Done | Blocks the app until acknowledged |
| English-only UI and backend | Done | Chinese remains only as guard detection data and in checksummed migrations |
| "Lamplight" design, light and dark, mobile-first | Done | Checked by screenshot at 390 px and 1280 px. Not tested on real phones |
| Loading, empty and error states on every screen | Done | 2026-09-27 |
| INR (lakh/crore) and IST formatting | Done (frontend) | The backend has no shared INR formatter. Agent context formats Cr/L itself |
| Hindi / i18n catalogue | Not started | `react-i18next` approved (Q12) but not added |

### Market data

| Feature | Status | Notes |
| --- | --- | --- |
| Zerodha Kite Connect adapter | **Built, unverified** | Tested only with fixtures written from Kite's docs. Needs the paid market-data add-on (per the in-app note) |
| Upstox adapter | **Built, unverified** | Fixtures only |
| Angel One SmartAPI adapter | **Built, unverified** | Fixtures only; TOTP at each login |
| Broker login flows (redirect / TOTP) | **Built, unverified** | The OAuth redirect and callback have never completed against a real broker |
| Encrypted credential vault + key rotation | Done | AES-256-GCM, unit-tested; rotation CLI in `src/modules/market/brokers.py` |
| Yahoo dev source (NSE/BSE) | Done (dev only) | Unofficial and delayed; blocked unless `APP_ENV` is a development value and `ALLOW_UNOFFICIAL_DATA=true` |
| Global markets panel | Done | Free, delayed Yahoo data labelled "Delayed / unofficial". Terms not reviewed |
| Stock search | Partial | Uses the broker instrument list. Without one, it offers the typed symbol marked unverified |
| Indian news, NSE/BSE filings, fundamentals | Not started | Q8 open; agents say "not available" |
| GIFT Nifty, FII/DII flows | Not started | Q8 open |
| NSE holiday / special-session calendar | Not started | Only weekends count as closed (Q11 open) |
| Circuit bands, F&O expiries from the instrument master | Not started | Phase 3 |

### Research and AI

| Feature | Status | Notes |
| --- | --- | --- |
| Output guard (detector, redact/block, `GuardedText`, audit) | Done | Adversarial, property-based and end-to-end suites. **Never red-teamed with real models** |
| Research-only prompts and output contract | Done | Exercised with stub models; real-model compliance unverified |
| Recommendation features gated off | Done | Gate tests in `tests/compliance/test_feature_gates.py` and the vitest compliance suite |
| No-order-execution AST test | Done | `tests/compliance/test_no_execution_paths.py` |
| Agents: pre-market, intraday, daily close, news digest | Partial | They run; the news digest has no Indian news source. Only the daily close report is on by default |
| TradingAgents deep research (research-only graph) | Built, lightly verified | Unit tests with stubs; no deep-research run exists in the local database |
| Stock page research summary | Done (UI) | Parses deep-research Markdown. Research items from the daily agents aren't exposed by any API |
| Research assistant (chat with tools, approvals) | Done | Guarded, including streaming |
| MCP server + personal access tokens | Built, off by default | `MCP_ENABLED=true` to mount |
| LLM usage and cost tracking per user, quotas | Not started | Phase 5 |
| `ra_registered` mode with analyst review queue | Not started | The mode validates the RA config but behaves like research-only; the `ra_review_items` table is unused |

### Portfolio, alerts, simulation

| Feature | Status | Notes |
| --- | --- | --- |
| Accounts, positions, watchlist, P&L | Done | Manual entry; no broker holdings import |
| Price alerts | Done | Checked every 60 s, market-hours gate, cooldowns, daily limit |
| Simulation (paper trading) | Partial | Account, history and reset. **In research-only mode no new trades can be opened** (AI entries are gated; manual order entry, Q3, isn't built). Still uses the A-share cost model and a 100-share lot |
| Backtest module | Built, dormant | Not reachable in research-only mode |

### Notifications

| Feature | Status | Notes |
| --- | --- | --- |
| Telegram, Discord, Pushover | **Built, unverified** | Unit-tested. Real delivery hasn't been verified in this fork |
| Disclaimer appended to every notification | Done | Per-channel text budget, so truncation never removes it |
| WhatsApp (Meta Cloud API) | Not started | Phase 6 |
| Email | Not started | Phase 6 |
| Telegram platform bot with account linking | Not started | Phase 6 |

### Platform and operations

| Feature | Status | Notes |
| --- | --- | --- |
| Multi-user accounts, tenant isolation | Not started | Phase 5. One installation per person |
| Postgres | Not started | SQLite only |
| DPDP: data export, account deletion, consent records | Not started | Phase 5 |
| Admin panel | Not started | Phase 5 |
| Retention jobs (MCP logs, context, compliance events) | Done | Per Q19 |
| Scheduled database backups | Not started | Only a `.bak` copy before migrations |
| Docker image | **Built, unverified** | `Dockerfile` exists; **the image has never been built** (Docker Hub was blocked in the cloud sandbox; not tried locally) |
| Docker Compose | Not started as a file | A sample is in the README and [SETUP.md](SETUP.md); unverified |
| CI (GitHub Actions) | Partial | Ran only for PR #1 (3 green runs, 2026-09-24). Later branches have no PR, so CI has never run on them |
| OpenTelemetry export | Built, unverified | Optional |
| Update checker | Off by default | Needs `UPDATE_CHECK_DOCKER_REPO` |

## Tests and quality (measured 2026-09-27 on this branch)

| Suite | Result |
| --- | --- |
| Backend `pytest tests/` | **1,104 passed, 3 skipped**, run with the local `data/` database moved aside (see bug 1) |
| Coverage, new-code gate (modules listed in `pyproject.toml`) | **98.65%** (gate 90%) |
| Coverage, whole `src/` (line + branch) | **50%**; measured once for this report, not a gate |
| `packages/marketdata` | 173 passed, 4 skipped |
| `packages/pan-agent-runtime` | 39 passed |
| `packages/pan-agent-token-meter` | 3 passed |
| `packages/pan-agent-tool-research` | 8 passed |
| Frontend vitest | **112 passed** (28 files) |
| Frontend `tsc` and `pnpm build` | clean / OK |
| `ruff check` | clean on the new-code scope |
| `mypy --strict` | clean (43 files in the strict scope) |
| Compliance adversarial corpus | 81 forbidden and 50 allowed phrases at Phase 1 (ADR-002), plus later English-label cases |

## Known bugs and TODOs found in the code

**Security (go-live blockers):**

1. **Passwords** are unsalted SHA-256 (`hash_password`, `src/modules/administration/api/auth.py`).
2. **The API is open until a password is set** (`get_current_user` returns `None` when no
   hash exists).
3. **CORS** allows all origins (`src/bootstrap/application.py`).
4. **JWT** lives in `localStorage` for 30 days with subject `"user"`; there is no
   revocation and no rate limiting.

**Behaviour:**

5. **Tests aren't isolated from the dev database.** Several compliance tests use the real
   `data/candlewise.db`. If that database has a login password, 16 tests fail with 401. The
   same happens on earlier commits.
6. **Flaky test:** `tests/test_sse_endpoints.py` is known flaky upstream. One intermittent
   failure was seen in a coverage run on 2026-09-27 and passed on rerun; which test it was
   is UNVERIFIED.
7. **Holidays:** scheduled agents run on NSE holidays (weekends only in
   `trading_calendar.py`).
8. **Simulation costs** still use the A-share model and a 100-share lot
   (`paper_trading_engine.py`, lines 26–29).
9. **Schedule times** differ from the plan: pre-market at 09:00 rather than 08:45, daily
   close at 15:30 rather than 16:00.
10. **`DAILY_REPORT_CRON`** is read into `Settings` but no code reads the setting.
    Scheduling uses the agent's stored cron. It appears unused.
11. **TODO:** the AI client has no proxy-aware `httpx` client
    (`src/platform/ai/ai_client.py`, line 22).
12. **TODO:** the backtest doesn't model price-band limits
    (`src/modules/strategy/backtest/engine.py`, line 12).
13. **Unused variables:** the CI workflow still sets two `PLAYWRIGHT_*` variables (PLAN
    §8.2; left because CI changes need approval).
14. **`DATA_DIR`** controls avatars and the intraday event-gate state, but not the database
    path. The database is always `<app root>/data/candlewise.db`. The two agree in Docker,
    where both are `/app/data`.
15. **Slow first Portfolio load:** in a phone-width screenshot run with the Yahoo dev
    source, Portfolio still showed its skeleton after 25 s, while the desktop run loaded.
    The cause is UNVERIFIED.
16. **Research items unreachable:** research items from the daily agents are stored
    (`raw_data.research`) but no API returns them.
17. **Upstream disclosure:** the path-traversal fix hasn't been disclosed to the upstream
    maintainers (Q-sec).
