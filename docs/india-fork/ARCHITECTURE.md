# PanWatch → India fork: architecture map and China-specific assumptions

Phase 0 (discovery) deliverable. No product code was changed.

## 0. Provenance and scope

- **This repository is currently empty.** At the time of writing, `praneeeth/marketdata`
  `main` contains a single one-line `README.md`; no PanWatch code has been imported.
- Everything below was derived from **upstream `TNT-Likely/PanWatch` at commit
  `89bdf3f61e2b3e635e1427b3c5112ac488a7f436` (2026-09-21, `VERSION` = `dev`)**, cloned
  read-only. The latest upstream tag is `0.9.5`. Upstream is MIT-licensed
  (`Copyright (c) 2026 sunxiao0721`).
- All file paths below are relative to the upstream repository root at that commit.
  `path:line` references point at that snapshot.
- Upstream has no `CLAUDE.md` (it is git-ignored upstream). `AGENTS.md`,
  `CONTRIBUTING.md`, `README.md`, `src/ARCHITECTURE.md` and
  `packages/marketdata/README.md` were read. All of them are in Chinese.

### Size

| Area | Python LOC (excl. tests) |
| --- | --- |
| `src/` (backend) | 47,112 |
| `server.py` (entry point + seeds + agent registry) | 1,620 |
| `packages/marketdata` | 4,045 |
| `packages/pan-agent-runtime` | 1,821 |
| `packages/pan-agent-tool-research` | 687 |
| `packages/pan-agent-token-meter` | 100 |
| Backend tests (`tests/`) | 16,784 |
| Package tests | 4,341 |
| Frontend TS/TSX (`frontend/src`, `frontend/packages`) | ~27,000 |
| Prompts (`prompts/*.txt`) | 385 lines |

### Correction to the brief

The brief says the data layer "uses akshare". **It mostly does not.**
`packages/marketdata` is a set of hand-written `httpx` scrapers against Chinese
retail-finance websites: Tencent (`qt.gtimg.cn`), Sina, Eastmoney, Xueqiu, CLS and
Tonghuashun, plus Stooq and Yahoo for US data. `akshare` is imported in only three places:

1. `src/platform/scheduling/trading_calendar.py:47`: the A-share trading calendar
   (`tool_trade_date_hist_sina`).
2. `src/platform/marketdata/stock_list.py:248`: a fallback A-share symbol list.
3. `src/modules/automation/tradingagents/data_context.py:59`: A-share financial
   statements (`stock_financial_abstract`) fed to TradingAgents.

`efinance` is listed in `requirements.txt` but is never imported (only mentioned in a
comment). `yfinance` comes in as an optional extra of `packages/marketdata` and as a
transitive dependency of `tradingagents`.

---

## 1. System overview

```text
                ┌──────────────────────────── one Python process (server.py) ─────────────────────────────┐
 Browser (SPA)  │  FastAPI app (src/bootstrap/application.py)                                              │
 React 18 ──────┼─► /api/*  ── ResponseWrapperMiddleware ── module routers (src/modules/*/api)            │
  JWT in        │  /mcp      ── JSON-RPC, PAT auth (read-only tools)                                       │
  localStorage  │  /{path}   ── static SPA files (only if ./static exists)                                 │
                │                                                                                          │
                │  lifespan(): init_db → migrations → seed agents/datasources/strategies/sample stocks     │
                │              → AgentScheduler (cron agents) → PriceAlertScheduler (60 s)                 │
                │              → PaperTradingScheduler (60 s) → ContextMaintenanceScheduler (03:00/04:15)  │
                │              → background: stock-list refresh thread, trading-calendar prefetch          │
                │                                                                                          │
                │  Agents ─► MarketData (packages/marketdata) ─► httpx scrapers ─► Tencent/Sina/Eastmoney… │
                │        ─► AIClient / FailoverAIClient (OpenAI SDK, any OpenAI-compatible base URL)       │
                │        ─► NotifierManager (apprise + custom HTTP) ─► Telegram/WeCom/DingTalk/…           │
                │        ─► TradingAgents (LangGraph, monkeypatched data routing)                          │
                │  SQLite (data/panwatch.db, WAL, NullPool) – 53 tables, custom versioned migrations       │
                └──────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Deployment.** A single Docker image (`Dockerfile`: node build stage, then
  `python:3.11-slim` with CJK fonts and Chromium system libs for Playwright). Upstream
  publishes to Docker Hub `sunxiao0721/panwatch` via `.github/workflows/release.yml` on
  tag push.
- **Tenancy.** Single user, single process, single SQLite file. No horizontal scaling:
  schedulers are in-process singletons.
- **Configuration.** Environment variables and `.env` go through pydantic-settings
  (`src/platform/runtime/config.py`). The `app_settings` key/value table holds UI
  overrides such as the proxy, quiet hours and base URL. `data_sources`, `agent_configs`,
  `ai_services`/`ai_models` and `notify_channels` are DB-driven.

## 2. Code layout and layering rules

Upstream describes itself as a **modular monolith** (`src/ARCHITECTURE.md`):

```text
src/
├── bootstrap/   application.py – the only place the FastAPI app is created and routers mounted
├── platform/    technical capabilities, no product/investment decisions
│   ├── ai/             AIClient (OpenAI SDK), FailoverAIClient (multi-model fallback)
│   ├── events/         SSE transport
│   ├── marketdata/     host wiring for packages/marketdata, collectors, MarketDef/sessions, stock list
│   ├── notifications/  NotifierManager, NotifyPolicy (quiet hours/retry), dedupe
│   ├── observability/  log context (trace_id), DB log handler, optional OTel
│   ├── persistence/    engine/Session/Base, models.py (all 53 tables), migrations.py
│   ├── runtime/        Settings (env), watchlist YAML loader
│   ├── scheduling/     cron parser (POSIX DOW), trading calendar, timezone helpers
│   └── tasking/        task protocol contracts
├── modules/     product capabilities (each owns its api/ routers)
│   ├── administration/ auth, settings, providers, channels, datasources, logs, PATs, MCP, selfcheck, update checker
│   ├── assistant/      navigation-level AI assistant (pan_agent runtime), legacy chat, tools, approvals
│   ├── automation/     BaseAgent + 5 scheduled/capability agents, AgentScheduler, TradingAgents integration, suggestion pool
│   ├── market/         stocks, quotes, klines, news, discovery, price alerts (engine + scheduler)
│   ├── portfolio/      accounts, positions, dashboard, history, diagnostics, benchmark
│   ├── research/       analysis history, context builder/store/scheduler, insights, evaluations, recommendations API
│   ├── strategy/       factor engine, entry candidates, strategy signals, calibration, backtest
│   ├── paper_trading/  simulated trading engine, scheduler, notifier
│   └── reporting/      PDF export (WeasyPrint / xhtml2pdf)
└── web/         response wrapper middleware
```

Rules enforced by `tests/test_architecture_boundaries.py` and
`tests/test_module_ownership.py`:

- `platform` must not import `modules`.
- Modules must not import each other's `models.py`, `repository.py` or `api/`.

This matters for Phase 1: the notification choke point (`platform/notifications`) cannot
import a guard that lives in `modules/`. The guard has to live in `platform/` or be
injected.

### Module sizes (LOC)

| Module | LOC | Notes |
| --- | --- | --- |
| automation | 10,910 | agents, TradingAgents adapter (≈4,100 LOC), agent API (1,236) |
| strategy | 5,898 | `strategy_engine.py` 2,346, `entry_candidates.py` 2,075 |
| assistant | 5,829 | `tools.py` 1,351 |
| persistence | 4,119 | `migrations.py` 2,084, `models.py` 1,332 |
| market | 3,893 | `data_collector.py` 1,050 |
| research | 3,765 | |
| platform/marketdata | 2,725 | `kline_collector.py` 775 (technical indicators live here) |
| administration | 2,666 | |
| portfolio | 2,033 | `accounts.py` 862 |
| paper_trading | 1,849 | |

## 3. Workspace packages (`packages/`)

| Package | Import name | Role | Depends on host? |
| --- | --- | --- | --- |
| `marketdata` | `marketdata` | Pluggable data layer: `MarketData` facade → per-datatype `Engine` (priority failover, TTL cache, metrics) → `Vendor` (fetch + parse into typed dataclasses) → `market_get` (httpx with per-host throttle and retry) | No. It uses two injected ports: `ConfigProvider` and `MetricsSink` |
| `pan-agent-runtime` | `pan_agent` | Bounded tool-calling agent loop, contracts, context compaction, checkpoints. Must not import FastAPI, SQLAlchemy, `src.*` or LangChain | No |
| `pan-agent-token-meter` | `pan_agent_token_meter` | Heuristic token estimates + provider usage normalisation | No |
| `pan-agent-tool-research` | `pan_agent_tool_research` | Tool retrieval/selection plugin (runs in shadow mode by default) | No |

### `packages/marketdata` in detail

- **Ports** (`ports.py`): `SourceConfig{vendor, priority, enabled, config, supports_batch}`,
  `ConfigProvider.sources_for(datatype, market)` and `MetricsSink.record(...)`. The host
  implements `DbConfigProvider` (`src/platform/marketdata/marketdata_client.py`), which
  reads the **global** `data_sources` table on every call.
- **Registry** (`registry.py`): `VENDOR_CLASSES_BY_TYPE` is the single source of truth for
  12 datatypes: quote, kline, capital_flow, events, fundamentals, flash_news, news,
  dragon_tiger, margin, shareholders, dividend, northbound.
- **Types** (`types.py`): `Quote`, `Bar` (daily only), `CapitalFlow`, `EventItem`,
  `FlashNews`, `NewsArticle`, `Fundamentals`, `DragonTigerItem`, `MarginItem`,
  `ShareholderItem`, `DividendItem`, `NorthboundItem`, `HotStock`, `HotBoard`.
- **Symbol** (`symbol.py`): `Market` enum = `CN | HK | US`. `Symbol.parse` auto-detects the
  market from the code's shape (6 digits → CN, 5 digits → HK, letters → US) and converts to
  Tencent, Eastmoney-secid and yfinance forms.
- **Engine** (`engine.py`): tries vendors in priority order and returns the first
  non-empty result. The cache key is the request only. **Caches are process-wide, with no
  notion of which credential fetched the data.** Phase 2 has to change this for BYOK (see
  PLAN §Phase 2).
- **Missing capabilities for India**: intraday candles (only daily `Bar`), instrument
  master, corporate actions (only CN dividends), option chain, and a per-user credential
  context.

## 4. Persistence

- SQLite at `data/panwatch.db` (`src/platform/persistence/database.py:19`), WAL mode,
  `NullPool`, 5 s busy timeout.
- 53 tables in `src/platform/persistence/models.py`. 26 idempotent versioned migrations in
  `migrations.py`, run at startup.
- **No table has a `user_id` or tenant column.** The PAT model docstring says so
  explicitly: "单用户应用,不设 user_id" ("single-user app, no user_id",
  `models.py:1294`).
- **Secrets are stored in plaintext columns and returned by the API**:
  - `ai_services.api_key` is returned verbatim by `GET /api/providers`
    (`modules/administration/api/providers.py:63`).
  - `notify_channels.config` holds bot tokens and webhook keys.
  - `data_sources.config` holds vendor tokens and cookies and is returned verbatim
    (`api/datasources.py:110`).
  - `app_settings` stores `jwt_secret` and `auth_password_hash`.

  No redaction exists anywhere in the API or logging layers.

Table groups, with the eventual per-user ownership needed in Phase 5:

| Group | Tables | Owner in multi-user world |
| --- | --- | --- |
| Identity/auth | `app_settings` (auth keys), `personal_access_tokens`, `mcp_call_logs` | user (split global vs user settings) |
| Portfolio | `accounts`, `stocks`, `positions` | user |
| Agents | `agent_configs`, `stock_agents`, `agent_runs`, `agent_context_runs`, `analysis_history`, `stock_context_snapshots`, `news_topic_snapshots`, `agent_prediction_outcomes` | user (agent_configs: global defaults + per-user overrides) |
| Recommendations | `stock_suggestions`, `suggestion_feedback`, `entry_candidates`, `entry_candidate_feedback`, `entry_candidate_outcomes`, `market_scan_snapshots` | disabled in research_only; else user |
| Strategy | `strategy_catalog`, `strategy_signal_runs`, `strategy_outcomes`, `strategy_weights(_history)`, `factor_weights(_history)`, `market_regime_snapshots`, `strategy_factor_snapshots`, `portfolio_risk_snapshots`, `backtest_runs` | disabled in research_only / global |
| Alerts | `price_alert_rules`, `price_alert_hits` | user |
| Paper trading | `paper_trading_account`, `paper_trading_positions`, `paper_trading_trades` | user |
| Assistant | `chat_conversations`, `chat_messages`, `assistant_*` (8 tables) | user |
| Config | `ai_services`, `ai_models`, `notify_channels`, `data_sources` | `notify_channels`/`data_sources` → user (BYOK); AI → platform or user (open question) |
| Ops | `log_entries`, `notify_throttle`, `news_cache` | global (with retention) / user |

## 5. Agent and AI pipeline

### 5.1 Scheduled agents

`server.py` builds `AGENT_REGISTRY` (`server.py:1102`). `AgentScheduler`
(`modules/automation/agent_scheduler.py`) registers one APScheduler job per enabled
workflow agent, using the timezone from `Settings.app_timezone`.

Seeded agents (`modules/automation/agent_catalog.py`):

| Agent | Seed schedule (TZ = `Asia/Shanghai`) | Mode | Default |
| --- | --- | --- | --- |
| `premarket_outlook` 盘前分析 | `0 9 * * 1-5` (:64) | batch | off |
| `intraday_monitor` 盘中监测 | `*/5 9-15 * * 1-5` (:75) | single (per stock, skipped outside session) | off |
| `daily_report` 收盘复盘 | `30 15 * * 1-5` (:94) | batch | **on** |
| `news_digest` | none (capability, deprecated) | batch | off |
| `chart_analyst` | none (capability, needs Playwright screenshots) | single | off |
| `tradingagents` | manual, or auto-trigger from intraday on a big move | single | off |

`BaseAgent.run` (`modules/automation/base.py`) runs `collect` → `build_prompt` (reads
`prompts/<agent>.txt`) → `analyze` (`ai_client.chat`, then appends `AI: <model>`) →
`should_notify` → quiet hours → dedupe → `notifier.notify_with_result`. Subclasses
(`daily_report`, `premarket_outlook`, `intraday_monitor`, `news_digest`, `chart_analyst`)
override `analyze` and parse a trailing
`<!--PANWATCH_JSON-->{"suggestions":[{action,…}]}<!--/PANWATCH_JSON-->` block into
`stock_suggestions` (`automation/suggestion_pool.py`).

### 5.2 TradingAgents integration (`modules/automation/tradingagents/`)

- Pinned `tradingagents @ git+https://github.com/TauricResearch/TradingAgents.git@v0.5.0`.
  It pulls in about 115 packages (langchain, langgraph, yfinance and more).
- Graph: 4 analysts (market / sentiment / news / fundamentals) → bull/bear debate →
  research manager → **trader** → risk debate → **portfolio manager** →
  `final_trade_decision`.
- `toolkit_adapter.py` **monkeypatches `route_to_vendor` and `load_ohlcv`** at every import
  site. A-share and HK symbols are served from PanWatch's own data. **Everything else falls
  through to upstream vendors, i.e. yfinance, which means Indian tickers would bypass any
  BYOK broker connection.**
- `decision.py` maps the PM text to a 5-tier rating (`buy/overweight/hold/underweight/sell`,
  labels 买入/增持/持有/减持/卖出) and a 3-tier action, and renders Markdown plus a
  notification body titled `【深度】name(symbol):<rating>`.
- `emit_paper_trading_signal` (off by default) writes BUY decisions into
  `strategy_signal_runs`, which the paper-trading engine consumes.
- Cost tracking: a LangChain callback estimates USD per run, with a monthly budget in the
  agent config (`monthly_budget_usd`, default $10). `agent_runs` itself has **no
  token/cost columns**.
- `output_language: "Chinese"` (`agent_catalog.py:147`).

### 5.3 Interactive assistant

- `/api/assistant`: `AssistantService` → `pan_agent.AgentRuntime` → `llm_adapter`
  (streaming) + an approved `ToolRegistry` → events persisted to `assistant_task_*` and
  streamed to the UI over SSE.
- Tools (`assistant/tool_descriptors.py`): 14 read tools (portfolio, quotes,
  **`find_research_candidates`**, K-line summary, news, search, market status, hot
  stocks/boards/board stocks, fundamentals, capital flow, dragon-tiger, alerts) and 3
  **write** tools (create/update/delete price alert), which need user approval.
  `find_research_candidates` returns "候选标的、评分、风险和入场计划" (candidates, scores,
  risks and **entry plans**); see §5.5.
- `/api/chat` (`assistant/chat_api.py`) is the legacy tool-calling chat, with streaming.
  `chat_planner.py` is a multi-step "portfolio diagnosis" planner.
- System prompt (`assistant/prompt.py`) is Chinese and says "给出明确的观点和理由" ("give
  clear opinions and reasons") and "涉及买卖建议时说明风险" ("mention risks when giving
  buy/sell suggestions").

### 5.4 Inventory of AI output surfaces

Every place where LLM text reaches a user, a file or a third party. **Phase 1 must put the
output guard in front of all of these.**

| # | Producer | Call site | Sink(s) | Streaming |
| --- | --- | --- | --- | --- |
| 1 | BaseAgent default analyze | `automation/base.py:194` | `analysis_history`, UI, notification | no |
| 2 | Daily report | `automation/daily_report.py:571` | same + `stock_suggestions` | no |
| 3 | Premarket outlook | `automation/premarket_outlook.py:664` | same + `stock_suggestions` | no |
| 4 | Intraday monitor | `automation/intraday_monitor.py:760` | JSON → suggestion, own notification path (`:1037`) | no |
| 5 | News digest | `automation/news_digest.py:469` | same | no |
| 6 | Chart analyst (vision) | `automation/chart_analyst.py:196` | own notification path (`:255`) | no |
| 7 | TradingAgents graph | `tradingagents/agent.py:580` (`graph.propagate`) via LangChain LLMs, **not** AIClient | `analysis_history`, `stock_suggestions`, notification, UI deep-analysis modal, detail page, paper-trading signal | progress only |
| 8 | Agent API ad-hoc analysis | `automation/api/agents.py:1166` | HTTP response | no |
| 9 | Stock insight | `research/api/insights.py:254` | HTTP → stock insight modal | no |
| 10 | Announcement interpretation | `research/api/insights.py:341` | HTTP | no |
| 11 | Portfolio AI check-up + **rebalancing advice** | `portfolio/api/accounts.py:858` | HTTP | no |
| 12 | Dashboard "what to watch" | `portfolio/api/dashboard.py:432` | HTTP | no |
| 13 | Assistant runtime | `assistant/llm_adapter.py:73` | SSE deltas → UI, `chat_messages`, artifacts | **yes** |
| 14 | Legacy chat | `assistant/chat_api.py:299,420` | SSE → UI | **yes** |
| 15 | Diagnosis planner | `assistant/chat_planner.py:257` | SSE → UI | **yes** |
| 16 | Context summariser | `assistant/context_summarizer.py` | internal, re-fed to the model | no |
| 17 | PDF export | `reporting/pdf_export.py` | downloadable PDF of analysis | n/a |
| 18 | Share cards | `frontend/src/components/*ShareCard*.tsx` | PNG images users post externally | n/a |
| 19 | Paper-trading notifier | `paper_trading/paper_trading_notifier.py` | "跟单通知" (copy-trade notifications) with entry, stop-loss and target prices (template text, driven by AI signals) | n/a |
| 20 | MCP server | `administration/api/mcp.py` | exposes 5 legacy tools (`get_portfolio`, `get_stock_quote`, `get_technical_analysis`, `get_stock_suggestions`, `get_watchlist`) to external MCP clients; `get_stock_suggestions` returns stored AI actions and reasons | n/a |

### 5.5 Recommendation machinery (compliance-relevant)

These parts of the product exist to generate buy/sell calls. All of them conflict with
`research_only`:

- **Structured action taxonomy** `buy/add/reduce/sell/hold/watch/alert/avoid`, with Chinese
  labels such as 建仓/加仓/减仓/清仓/考虑止损 ("open position / add / reduce / exit / consider
  stop-loss"). It is required in every prompt's JSON contract and persisted in
  `stock_suggestions.action`.
- **Entry candidates / Opportunities page** (`strategy/entry_candidates.py`,
  `research/api/recommendations.py`, `frontend/src/pages/Opportunities.tsx`): "AI scoring
  stock picking", with `entry_low/entry_high/stop_loss/target_price` columns.
- **Assistant tool `find_research_candidates`** (`assistant/tool_descriptors.py:41`):
  exposes the entry-candidate engine, including entry plans, to the chat assistant.
- **MCP tool `get_stock_suggestions`** (`assistant/legacy_chat_tools.py:64`, served by
  `administration/api/mcp.py`): hands the AI suggestion pool (actions + reasons) to
  external MCP clients.
- **Opportunity refresh jobs**: `ContextMaintenanceScheduler` refreshes opportunities at
  09:15, 13:30 and 22:00 (`research/context_scheduler.py:291-300`).
- **Strategy signals** (`strategy/strategy_engine.py`, `strategy_signal_runs`): per-stock
  action, entry range, stop, target and holding days. These feed paper trading.
- **Paper trading** (`paper_trading/`): **there is no manual order entry.** Positions are
  opened only from AI strategy signals (`_check_entries`), sized by signal strength, with
  SL/target/trailing stops. It pushes "premarket plan" and "daily summary" notifications
  listing entries, stops and targets.
- **Add-position calculator** (`biz-ui/.../add-position-calculator.tsx`): position sizing
  with an "AI 结论" ("AI conclusion").
- **Portfolio check-up** (#11 above): prompt demands "可执行调仓建议" ("actionable
  rebalancing suggestions").
- **Evaluations** (`research/api/evaluations.py`, `automation/agent_prediction_evaluation.py`,
  `frontend/src/pages/Evaluations.tsx`): tracks hit-rate and returns of AI calls. Shown to
  users, these are performance claims.
- **Intraday monitor thresholds** `stop_loss_warning` / `take_profit_warning`
  (`intraday_monitor.py:80-81`). They are user-configured P&L % thresholds, but they are
  worded as 止损预警 ("stop-loss warning") and 止盈提醒 ("take-profit reminder").

### 5.6 Order execution

**Upstream has no real order-execution path.** There is no broker SDK and no order
endpoint. The only execution-like paths are simulated or advisory:

1. The paper-trading auto-entry and exit engine.
2. TradingAgents `emit_paper_trading_signal`.
3. The assistant's write tools, which are limited to the user's own price alerts.

The "portfolio diagnostics" module is explicitly "只读不下单" ("read-only, never places
orders"). Phase 1's "disable execution" is therefore mostly about **keeping it that way
structurally**: read-only broker adapters, plus an architecture test. The simulated paths
also need re-scoping (see PLAN open questions).

## 6. Notifications

- A single choke point: `NotifierManager.notify_with_result(title, content, images)`
  (`platform/notifications/notifier.py:241`). `notify()` delegates to it. **Every
  notification in the codebase goes through this method**: base agent, intraday, chart
  analyst, price alerts, paper trading, selfcheck and channel test. This is where the
  guard and the disclaimer go.
- Channels (`CHANNEL_TYPES`, `notifier.py:69-110`): Telegram, Bark, DingTalk, WeCom,
  Lark/Feishu, ServerChan, PushPlus, Discord and Pushover. They are sent via apprise or
  custom httpx code, and content is sanitised to plain text for Telegram.
- Policy: quiet hours, retry/backoff and per-agent dedupe TTL (`notify_policy.py`,
  `notify_dedupe.py`). Per-stock throttle for intraday lives in the `notify_throttle` table.
- Channel selection: 4-level routing, agent-level or stock-agent-level channel ids, falling
  back to the default channel (`server.py:978`).

## 7. Scheduling and market-time model

- `MarketDef` / `MARKETS` (`platform/marketdata/models.py:21-83`): CN `Asia/Shanghai`
  09:30–11:30 + 13:00–15:00; HK `Asia/Hong_Kong` 09:30–12:00 + 13:00–16:00; US
  `America/New_York` 09:30–16:00. There are no pre-open, auction or special-session concepts.
- `is_trading_day` (`platform/scheduling/trading_calendar.py`): CN uses the akshare calendar
  (holidays included). HK/US check weekends only.
- `timezone.py` (`beijing_now()` and friends) defaults to `Asia/Shanghai`.
- Other jobs:
  - Price alerts: every 60 s.
  - Paper trading scan: every 60 s, only when any market is in session.
  - Context maintenance: 03:00 trading-calendar refresh, 04:15 retention cleanup,
    opportunity refresh at 09:15/13:30/22:00, plus eval jobs.
  - MCP log cleanup: 04:00.

## 8. Auth and security posture (single-user)

| Aspect | Upstream behaviour | Location |
| --- | --- | --- |
| Accounts | One username/password in `app_settings` (or from `AUTH_USERNAME`/`AUTH_PASSWORD` env) | `administration/api/auth.py` |
| Password hashing | **Unsalted SHA-256** | `auth.py:78-80` |
| Before first setup | **All protected APIs are open** (`get_current_user` returns `None`) | `auth.py:166-169` |
| Session | HS256 JWT, 30-day expiry, `sub="user"`, no revocation; stored in `localStorage` | `auth.py`, `frontend/src/App.tsx` |
| CORS | `allow_origins=["*"]` with `allow_credentials=True` | `bootstrap/application.py:58` |
| Secrets at rest | Plaintext in SQLite; API returns them unmasked | §4 |
| MCP | PAT (`pwmcp_` prefix, sha256, constant-time compare, `mcp:read` scope), audited | `administration/pat.py`, `api/mcp.py` |
| Static files | **Path traversal**: `/%2e%2e/<file>` escapes `static/` (e.g. could read `data/panwatch.db`, which holds API keys and the JWT secret). Reproduced in a scratch copy of the route during Phase 0. Only affects deployments where `static/` exists (the Docker image) | `server.py` `serve_spa` |
| Error handling | 371 `except Exception` blocks (ruff BLE001), 59 `try/except/pass`. The codebase habitually fails open | whole `src/` |

## 9. Frontend

- React 18, TypeScript 5, Vite 5, Tailwind 3 and shadcn/Radix. pnpm workspace with
  `packages/api` (typed API client + SSE), `packages/base-ui` (shadcn primitives) and
  `packages/biz-ui` (domain components). App code is in `frontend/src`.
- Routes (`frontend/src/App.tsx:286-298`): `/` Dashboard, `/opportunities`, `/portfolio`
  (Stocks, 3,210 LOC), `/agents`, `/evaluations`, `/history`, `/paper-trading`, `/alerts`,
  `/assistant[/:id]`, `/datasources`, `/settings`, `/analysis/:symbol/:date`, `/login`.
- **No i18n library.** 2,797 lines containing Chinese characters across 88 TS/TSX files.
  `index.html` is `lang="zh-CN"`, and dates use `toLocaleString('zh-CN')`
  (`src/lib/utils.ts:45,65,87` and others).
- **Colour convention is Chinese**: red/rose = up, green/emerald = down, hard-coded in
  about 30 places, with no central token.
- Onboarding dialog (`biz-ui/.../onboarding.tsx`) covers welcome → AI → notify → done.
  There is no consent step. Completion is stored in `localStorage`.
- Existing disclaimers are ad hoc: "仅供参考,不构成投资建议" ("for reference only, not
  investment advice") in the PDF footer, the deep-analysis modal, share cards and
  `AnalysisDetail`. There is no global footer.
- PWA-installable.

## 10. Tooling, tests and CI baseline (measured in this sandbox)

The upstream checkout was installed in a scratch venv (Python 3.11) and with pnpm on Node
22; upstream pins Node 24.14.0.

| Check | Result |
| --- | --- |
| `pip install -r requirements.txt` (incl. TradingAgents from GitHub) | OK |
| `pytest tests/` | **784 passed, 3 skipped** (≈30 s) |
| `packages/marketdata` tests | 188 passed |
| `packages/pan-agent-runtime` tests | 39 passed |
| `packages/pan-agent-token-meter` tests | 3 passed |
| `packages/pan-agent-tool-research` tests | 8 passed |
| Frontend `vitest run` | 43 passed (18 files) |
| Frontend `tsc -b` | clean |
| Frontend `vite build` | OK |
| `ruff check --isolated` (ruff 0.16.8 defaults) on `src server.py packages` | 1,026 findings (top: BLE001 ×371, B008 ×143, I001 ×115) |
| `ruff format --check` | 167 of 255 files would be reformatted |
| `mypy --strict --ignore-missing-imports src` | 2,065 errors in 121 files |
| `mypy --strict … packages/marketdata/src` | 109 errors in 19 files |

- Upstream has **no ruff, mypy, coverage or hypothesis configuration**, and none of them are
  in `requirements.txt`. `pyproject.toml` only configures pytest.
- Upstream CI runs only on tag push (`release.yml`: tests, then Docker build and push to
  Docker Hub, then a Telegram notification). `pullfrog.yml` is a manual AI-agent workflow.
  **Nothing runs on pull requests.**
- `tests/eval/` contains an agent evaluation harness with rule-based checks and optional
  LLM-as-judge. It can host the adversarial guard suite.
- `tests/conftest.py` auto-mocks `NotifierManager.notify*`. Tests use the real SQLite file
  under `data/`.
- `AGENTS.md` conventions: Conventional Commits with **Chinese** subjects, `codex/` branch
  prefix, squash merge. These need replacing for the fork.

---

## 11. Catalogue of China-specific assumptions

Every assumption found, grouped, with the India replacement and the phase that owns it.
"Remove" means delete rather than port.

### 11.1 Time, calendar and sessions

| # | Assumption | Where | India replacement | Phase |
| --- | --- | --- | --- | --- |
| T1 | Default TZ `Asia/Shanghai` | `platform/runtime/config.py:62-65`, `platform/scheduling/timezone.py:15`, `trading_calendar.py:35`, `.env.example`, README | `Asia/Kolkata` default; always store UTC | 3 |
| T2 | Helpers named `beijing_now/to_beijing/format_beijing` | `platform/scheduling/timezone.py` (used widely) | rename to `market_now/to_local` (mechanical) | 3 |
| T3 | CN sessions 09:30–11:30, 13:00–15:00 (lunch break) | `platform/marketdata/models.py:55-63` | NSE/BSE pre-open 09:00–09:15, normal 09:15–15:30, closing session ~15:40–16:00, no lunch break | 3 |
| T4 | HK/US sessions and markets | `models.py:64-83` | remove unless HK/US is kept (open question) | 2/3 |
| T5 | Holiday calendar from akshare (Sina) | `trading_calendar.py:45-58` | yearly-editable NSE calendar file with special sessions (Muhurat, Saturday sessions) | 3 |
| T6 | HK/US: weekends only | `trading_calendar.py:123-147` | n/a | 3 |
| T7 | Agent cron times assume CN hours: premarket 09:00, intraday `*/5 9-15`, daily report 15:30, `DAILY_REPORT_CRON=30 15 * * 1-5` | `automation/agent_catalog.py:64,75,94`, `runtime/config.py:57`, `.env.example` | premarket 08:45, intraday 09:15–15:30 (every 5 min), post-market 16:00 IST; trading-day guarded | 3 |
| T8 | `any_market_trading_day` iterates CN/HK/US | `trading_calendar.py:150`, `paper_trading_scheduler.py:16` | NSE/BSE only | 3 |
| T9 | Maintenance jobs at 03:00/04:00/04:15 local; opportunity refresh at 09:15/13:30/22:00 (CN session-aligned) | `research/context_scheduler.py:262-300`, `server.py:1157` | night jobs fine in IST; opportunity refresh disabled in research_only | 1/3 |

### 11.2 Symbols, exchanges and markets

| # | Assumption | Where | India replacement | Phase |
| --- | --- | --- | --- | --- |
| S1 | `Market` / `MarketCode` enums = `CN/HK/US` | `packages/marketdata/src/marketdata/symbol.py:10`, `platform/marketdata/models.py:7` | `NSE`, `BSE` (+ segments: EQ, FO, CDS?, index) | 2 |
| S2 | Market auto-detected from code shape (6 digits → CN, 5 → HK, letters → US) | `symbol.py:16-30` | explicit exchange + tradingsymbol; ISIN as the cross-exchange identity; no shape guessing (`INFY`, `TCS`, `M&M`, `BAJAJ-AUTO`) | 2 |
| S3 | CN exchange inference SH/SZ/BJ from code prefix | `symbol.py:33-39`, `platform/marketdata/cn_symbol.py` (18 call sites) | remove | 2 |
| S4 | Vendor symbol encodings (Tencent `sh600519`, Eastmoney secid `1.600519`, `.HK`) | `symbol.py:54-71` | broker encodings: Kite `NSE:INFY` + `instrument_token`; Upstox `NSE_EQ\|INE009A01021`; Angel `symboltoken` + exchange; yfinance `INFY.NS`/`.BO` | 2 |
| S5 | Symbol regexes `^[036]\d{5}$`, `^\d{5}$`, `^[A-Z]{1,5}$` | `platform/marketdata/models.py:63,73,82` | tradingsymbol charset `[A-Z0-9&-]` and BSE numeric scrip codes | 2 |
| S6 | Stock universe list from Eastmoney clist (CN/HK/US/BJ), cached 7 days; akshare fallback; Eastmoney search API | `platform/marketdata/stock_list.py` | broker instrument master (per-user fetch) → local search index | 2 |
| S7 | TradingAgents routing predicates `is_a_share` / `is_hk_share` | `tradingagents/toolkit_adapter.py:121-160` | `is_indian_instrument` → route to the user's provider; never fall through to yfinance in production | 2/4 |
| S8 | Code-shape checks in prompts ("A 股 6 位数字；港股 5 位数字…" = "A-shares 6 digits; HK 5 digits…") | `prompts/*.txt` | NSE tradingsymbol rules | 4 |
| S9 | `config/watchlist.yaml` sample: 贵州茅台 (Kweichow Moutai), 平安银行 (Ping An Bank), 腾讯 (Tencent) | `config/watchlist.yaml` | Indian examples or remove (legacy YAML path) | 2 |

### 11.3 Data vendors and data types

| # | Assumption | Where | India replacement | Phase |
| --- | --- | --- | --- | --- |
| D1 | Quotes from Tencent / Sina / Eastmoney / yfinance | `packages/marketdata/src/marketdata/vendors/{tencent,sina,eastmoney,yfinance}.py`, seeds `server.py:463-500` | Kite → Upstox → Angel (BYOK); yfinance dev-only | 2 |
| D2 | Daily K-lines from Tencent / Eastmoney / Stooq / Yahoo; no intraday | `vendors/kline.py` | broker historical candles (minute…day) | 2 |
| D3 | 主力资金流 ("main-force capital flow", super/big/mid/small orders) from Eastmoney/Sina | `vendors/capital_flow.py`, `platform/marketdata/collectors/capital_flow_collector.py`, prompts, UI | no retail equivalent. Remove. Market-level FII/DII flows instead (source TBD) | 2/3 |
| D4 | 北向资金 (Northbound Stock Connect) from Tonghuashun | `vendors/northbound.py` | remove; FII/DII daily provisional as the concept analogue | 2/3 |
| D5 | 龙虎榜 (dragon-tiger list) | `vendors/market_flow.py`, assistant tool `get_dragon_tiger` | remove; bulk/block deals are the closest analogue (source TBD) | 2/4 |
| D6 | 融资融券 (CN margin trading) | `vendors/market_flow.py` | remove (India MTF/pledge data comes from filings; defer) | 2 |
| D7 | 股东户数 (shareholder count) | `vendors/market_flow.py` | remove; quarterly shareholding pattern from exchange filings (defer) | 2/4 |
| D8 | CN dividend schema (`PRETAX_BONUS_RMB`, 每10股转增 "bonus shares per 10") | `vendors/market_flow.py:232` | corporate actions (dividend/bonus/split/rights) with ex/record dates | 2 |
| D9 | Announcements from Eastmoney; full text via `np-cnotice-stock.eastmoney.com` | `vendors/events.py`, `vendors/news.py`, `collectors/events_collector.py:24` | NSE/BSE corporate announcements | 4 |
| D10 | News from Xueqiu, Eastmoney search, CLS/Sina/Eastmoney flash news; keyword search in Chinese | `vendors/news.py`, `vendors/flash_news.py`, `client.py:293` | Indian financial RSS + exchange filings | 4 |
| D11 | Fundamentals from Tencent/Eastmoney; TradingAgents financial statements from akshare | `vendors/fundamentals.py`, `tradingagents/data_context.py` | source TBD (broker APIs do not provide financial statements) | 2/4 |
| D12 | Hot stocks/boards (Eastmoney 热门榜/板块, "hot lists / sectors") | `vendors/discovery.py`, `market/api/discovery.py`, `DiscoveryPanel.tsx` | top gainers/losers by NSE sector index from the user's provider, or remove | 2 |
| D13 | K-line screenshots scraped with Playwright from Xueqiu/Eastmoney/Sina | `platform/marketdata/collectors/screenshot_collector.py`, `server.py:211,627-650` | remove (also drops the Chromium dependency); render charts from OHLC if vision analysis is kept | 2 |
| D14 | `DbConfigProvider` special case: Tencent US K-line returns 501 on "current network" | `platform/marketdata/marketdata_client.py:39-47` | remove | 2 |
| D15 | Datasource seeds, test symbols `600519/601127/00700/00386/AAPL/NVDA` | `server.py:355-650`, `market/data_collector.py:18-27` | Indian seeds + `RELIANCE`, `INFY`, `HDFCBANK`… | 2 |
| D16 | Market-scan universe (CN/HK/US blue chips) | `strategy/entry_candidates.py:68-110` | disabled in research_only (see 5.5) | 1 |
| D17 | Global HTTP proxy + corporate CA plumbing meant for reaching Telegram/Yahoo from mainland China | `server.py:57-140`, README | keep (harmless) but not needed in India | — |

### 11.4 Indices, benchmarks and global cues

| # | Assumption | Where | India replacement | Phase |
| --- | --- | --- | --- | --- |
| I1 | Header indices: 上证 (SSE Composite) / 深成指 (SZSE Component) / 创业板 (ChiNext) / 恒生 (Hang Seng) / NASDAQ / Dow | `market/api/market.py:22-32` | Nifty 50, Sensex, Nifty Bank, India VIX, Nifty Midcap/Smallcap; sector indices | 3 |
| I2 | `INDEX_SECID` / `INDEX_TENCENT` maps | `packages/marketdata/src/marketdata/client.py:36-58` | broker index instruments (e.g. `NSE:NIFTY 50`) | 2 |
| I3 | Daily report fetches CN indices only | `automation/daily_report.py:39` | Nifty/Sensex + sector indices | 3 |
| I4 | Premarket "overnight US" = DJI/IXIC/INX via Tencent | `automation/premarket_outlook.py:78-94` | GIFT Nifty, US close, Asian open, crude, USD/INR, US 10Y (source TBD) | 3 |
| I5 | Relative-strength benchmark 沪深300 (CSI 300) / 恒生 / 标普500 (S&P 500) | `research/context_builder.py:29-35` | Nifty 50 (or sector index) | 3 |

### 11.5 Trading rules, costs and money

| # | Assumption | Where | India replacement | Phase |
| --- | --- | --- | --- | --- |
| R1 | Lot = 100 shares (A-share 一手, "one board lot") | `paper_trading/paper_trading_engine.py:29-30,58-74` | equity cash lot = 1; F&O lot sizes from the instrument master | 3 |
| R2 | T+1: cannot sell on buy day | `strategy/backtest/engine.py:7,126` | India allows same-day sell (intraday) and BTST; T+1 applies to **settlement** (funds/securities), which matters for P&L/cash availability | 3 |
| R3 | CN costs: stamp duty 0.05% sell-side, commission 2.5 bp min ¥5, transfer fee | `strategy/backtest/cost_model.py:3-24` | STT, exchange txn charges, SEBI fee, stamp duty (buy), GST, DP charges, all as config (rates change) | 3 |
| R4 | Price limits (±10%/20%) not modelled (TODO) | `strategy/backtest/engine.py:12` | NSE price bands (2/5/10/20%, none for F&O stocks with dynamic bands) + index circuit breakers; take per-stock limits from broker quotes where available | 3 |
| R5 | Base currency CNY; HKD/USD→CNY FX from Sina with hard-coded fallbacks 0.92/7.25 | `portfolio/api/accounts.py:22-75,493,579,634-841` | INR single currency (drop FX unless US kept) | 3 |
| R6 | Units 万 (10⁴) / 亿 (10⁸) in UI and prompts; "Turnover (CNY)" | `frontend/src/pages/DataSources.tsx:697-729`, `tradingagents/toolkit_adapter.py:992`, `Fundamentals` docstrings (亿) | lakh / crore formatting (`₹12,34,567.89`, `₹12.35 L`, `₹1.23 Cr`) | 3/4 |
| R7 | Paper-trading capital ¥1,000,000; allocations `{"CN":0.5,"HK":0.3,"US":0.2}` | `persistence/models.py:1009`, `paper_trading_engine.py:111` | INR amount; single market | 3 |
| R8 | Account name examples 招商证券/华泰证券 (Chinese brokers) | `persistence/models.py:71` | n/a (cosmetic) | 4 |
| R9 | No F&O concepts (expiry, lot, strike, OI) anywhere | — | new: instrument master, option chain, expiry calendar from the instrument master (not hard-coded; weekly expiry days were changed in 2024–25) | 2/3 |

### 11.6 Language and content

| # | Assumption | Where | India replacement | Phase |
| --- | --- | --- | --- | --- |
| L1 | All 5 prompt files in Chinese, A-share framing (上证/创业板/主力资金/隔夜美股 = SSE/ChiNext/main-force flows/overnight US) | `prompts/*.txt` | English, Indian context, research-only | 1 (output contract) / 4 (content) |
| L2 | Inline Chinese system prompts | `research/api/insights.py:244,336`, `portfolio/api/accounts.py:850`, `portfolio/api/dashboard.py:424`, `assistant/chat_planner.py:40,150,152`, `assistant/context_summarizer.py:12`, `assistant/prompt.py:5` | same | 1/4 |
| L3 | TradingAgents `output_language: "Chinese"` and Chinese rating labels | `agent_catalog.py:147`, `tradingagents/decision.py:30-51` | English; ratings removed in research_only | 1 |
| L4 | Chinese notification titles and templates (`【盘中监测】…`, `【模拟盘建仓】`, "open position" etc.) | agents, `paper_trading_notifier.py`, `price_alert_engine.py` | English templates via i18n keys | 4 |
| L5 | Frontend strings (2,797 lines / 88 files), `lang="zh-CN"`, `zh-CN` date formatting | `frontend/**` | i18n catalogs (en-IN first, hi-IN later) | 4 |
| L6 | API error messages in Chinese (`"未登录"` "not logged in", `"登录已过期"` "login expired", …) | all routers | English, with stable machine-readable error codes | 4 |
| L7 | Logs and docstrings in Chinese | everywhere | logs → English (operability); docstrings opportunistically | 4 |
| L8 | Chinese fonts in Docker/PDF (`fonts-noto-cjk`, STSong fallback) | `Dockerfile`, `reporting/pdf_export.py`, `requirements.txt` comments | Noto Sans (+ Devanagari later) | 4 |
| L9 | Eval cases in Chinese | `tests/eval/cases/*.py` | English cases + adversarial suite | 1/4 |

### 11.7 Ecosystem, integrations and branding

| # | Assumption | Where | India replacement | Phase |
| --- | --- | --- | --- | --- |
| E1 | Default LLM `https://open.bigmodel.cn` (Zhipu) `glm-4`; cost table keyed on `deepseek-chat` | `runtime/config.py:20-22`, `.env.example`, `tradingagents/observability.py:591-607`, `automation/api/agents.py:636` | provider-neutral default; decision pending (open question) | 5 |
| E2 | Notification channels WeCom, DingTalk, Lark, ServerChan, PushPlus, Bark | `platform/notifications/notifier.py:69-120` | keep Telegram; add WhatsApp; probably email; remove CN channels | 6 |
| E3 | Stock links to Xueqiu (`stock_link_platform` default `xueqiu`) | `administration/stock_link.py:16-73` | link to NSE/BSE quote pages or none | 4 |
| E4 | Update checker polls Docker Hub `sunxiao0721/panwatch` | `administration/update_checker.py` | point to fork releases or disable | 4 |
| E5 | Donation QR codes (WeChat/Alipay), Telegram community link, upstream Docker badges | `README.md`, `docs/donate/` | replace (keep MIT attribution) | 4 |
| E6 | Release workflow pushes to upstream Docker Hub + Telegram | `.github/workflows/release.yml` | replace (needs CI approval) | 1 |
| E7 | Branding 盯盘侠 PanWatch in UI, PDF, share cards, MCP `SERVER_INFO` | many | new product name (open question) | 4 |
| E8 | Colour convention red = up / green = down | ~30 sites in `frontend/**` (e.g. `DiscoveryPanel.tsx:286`, `DataSources.tsx:633`) | green = up / red = down via central tokens | 4 |

### 11.8 Not China-specific but blocking for a public Indian product

| # | Issue | Where | Phase |
| --- | --- | --- | --- |
| X1 | Prompts and JSON contracts demand buy/sell/hold, entry/SL/target, position sizing | `prompts/*.txt`, inline prompts, §5.5 | 1 |
| X2 | Recommendation machinery (suggestions, entry candidates, strategy signals, evaluations, calculator, share cards) | §5.5 | 1 |
| X3 | Single-user auth, unsalted SHA-256, open-before-setup, CORS `*`, JWT in localStorage | §8 | 5 (hot fixes in 1) |
| X4 | Static-file path traversal | `server.py` `serve_spa` | 1 (security hotfix) |
| X5 | Secrets stored and returned in plaintext; no log redaction | §4 | 5 (masking in 1) |
| X6 | Global (process-wide) data-source config and caches; no credential context | `marketdata_client.py`, `engine.py` | 2 |
| X7 | SQLite + in-process schedulers (no multi-worker) | `persistence/database.py`, `server.py` lifespan | 5 |
| X8 | TradingAgents defaults to yfinance for non-CN tickers (bypasses BYOK) | `toolkit_adapter.py` | 2/4 |
| X9 | Fail-open error handling culture (371 blind excepts) | `src/**` | ongoing; guard must fail closed |
