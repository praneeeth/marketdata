# PanWatch backend architecture

## Goal and shape

PanWatch is a **modular monolith**: one FastAPI app and one shared database, with the code
organised along stable business boundaries. The goal isn't a microservice per domain; it is
that every piece of business code has a clear owner, dependency direction and test boundary,
so an unbounded `core` directory never forms again.

```text
src/
├── bootstrap/       # app startup and dependency wiring
├── platform/        # technical capabilities with no business meaning
├── modules/         # product capabilities
└── web/             # HTTP middleware and response adapters shared across modules
```

`collectors/`, `models/` and `compat/` have been folded in and deleted. Collectors live in
`platform/marketdata/collectors/`, and the shared quote value objects live in
`platform/marketdata/models.py`; don't recreate these root directories.

## Dependency direction

```text
web ───────────────► modules ───────────────► platform
 │                    │                         │
 │                    └─ public service / DTO ──┘
 └────────────────────────► platform
```

- `platform` must not import `modules`, and makes no product or investment decisions.
- `modules` may use `platform`, but must not import another module's ORM models or repository directly.
- A module must not import another module's `api/` router or `*_api.py`; cross-module HTTP code
  must call the target module's public service, DTO or supported tool boundary instead.
- Cross-module collaboration goes through the owning module's public service, DTO or event.
- `web` only maps HTTP input/output and wires services; it holds no complex SQL, agent tool loops or strategy logic.
- `bootstrap` only wires things at startup and implements no business flow.

`tests/test_architecture_boundaries.py` guards these rules. When a module boundary changes,
update the architecture test and this document together.

## `platform/`: the technical platform

Platform code describes "how to connect or run", never "what the user should do".

| Subdirectory | Responsibility | Must not contain |
| --- | --- | --- |
| `persistence/` | engine, Session, ORM Base, every table definition, versioned migrations | business logic such as positions or strategies |
| `ai/` | AI provider clients, failover, model transport adapters | prompts, tool authorisation |
| `marketdata/` | external market data clients, collectors, quote value objects, symbol normalisation, vendor routing, data normalisation | alert thresholds, stock selection rules |
| `events/` | event transport such as SSE | the business meaning of events |
| `scheduling/` | cron parsing, trading calendar, registries | the agent scheduling flow |
| `notifications/` | channel delivery, basic dedupe and policy | which business events should notify |
| `observability/` | log context, traces, metric export | deriving domain metrics |
| `runtime/` | process config, environment variables and other cross-cutting runtime settings | product business rules |

## `modules/`: business capabilities

Each top-level directory owns one product capability. Recommended, not mandatory, internal layout:

```text
<module>/
├── api.py          # optional: the module's router
├── service.py      # use-case orchestration, and the preferred public boundary
├── repository.py   # optional: persistence queries and writes
├── models.py       # optional: references to the domain/ORM models the module uses
├── schemas.py      # optional: DTOs, commands and response models
└── ...             # domain-specific implementation
```

Don't create empty layers for the sake of form. Other modules call the service rather than
importing the repository around it.

| Module | Owns | Typical public boundary |
| --- | --- | --- |
| `assistant` | chat, task snapshots, the PanAgent host adapter, approved tools | `AssistantService` |
| `automation` | scheduled analysis agents, run records, TradingAgents, AgentScheduler | agent service / scheduler |
| `market` | instruments, collection orchestration, news, K-line context, price alerts | market/alert service |
| `portfolio` | accounts, positions, diagnostics, benchmarks | `PortfolioService` |
| `research` | analysis history, context, evidence, outcome evaluation, signals | research/context service |
| `strategy` | factors, signals, screening candidates, calibration, backtest | strategy service |
| `paper_trading` | simulation execution, ledger, allocation, notifications | paper-trading service |
| `reporting` | rendering reports and artefacts such as PDFs | render/export function |
| `administration` | health checks, settings, PATs, update checks | administration service |

## `bootstrap/`: app wiring

`bootstrap/application.py` is the only place that creates the FastAPI app and registers
routers. It may depend on the public HTTP entry points of `web`, `modules` and `platform`, but
holds no domain rules, SQL or agent run loops. `bootstrap` only keeps files with a real
startup responsibility; "containers" or forwarding facades with no callers shouldn't exist to
reserve structure.

## Modules own their HTTP routers

App assembly is done by `bootstrap/application.py`; business routers live in each module's
`api/` package rather than a new central `web/api` directory. For example,
`modules/market/api/` owns the quote, K-line, instrument, news and price alert endpoints, and
`modules/portfolio/api/` owns the account, history and dashboard endpoints.

`web/` only keeps HTTP technical components shared across modules, such as the response
wrapper middleware. Each business router only:

1. validates HTTP input and builds a command/DTO;
2. gets the module service;
3. maps the service result to an HTTP response, SSE or error code.

The database, ORM models and migrations all live in `platform/persistence/`. Don't bring back
`src/web/api/`, `src/web/app.py`, `src/web/database.py`, `src/web/models.py` or
`src/web/migrations.py`.

| Module | HTTP router location | Endpoints |
| --- | --- | --- |
| `administration` | `modules/administration/api/` | auth, settings, health checks, logs, data sources, PATs, MCP |
| `assistant` | `modules/assistant/api.py`, `chat_api.py` | the navigation-level assistant and the compatible chat API; shared legacy chat tools are in `legacy_chat_tools.py` |
| `automation` | `modules/automation/api/` | agents, suggestion pool, templates |
| `market` | `modules/market/api/` | instruments, quotes, K-lines, news, discovery, price alerts |
| `portfolio` | `modules/portfolio/api/` | accounts, position history, dashboard |
| `research` | `modules/research/api/` | context, insights, evaluations, feedback, recommendations |
| `strategy` | `modules/strategy/api/` | factor endpoints |
| `paper_trading` | `modules/paper_trading/api/` | simulation endpoints |

## Key flows

### Navigation-level assistant

```text
/assistant page
  → /api/assistant router
  → AssistantService
  → AgentRuntime (packages/pan-agent-runtime)
  → ModelPort + approved ToolRegistry
  → runtime events → task persistence + SSE → UI
```

`pan-agent-runtime` is imported as `pan_agent`. It only defines the bounded run loop,
resource limits and portable events, and must not import FastAPI, SQLAlchemy, `src.*` or
LangChain. PanWatch's adapters, tools and persistence belong to `modules/assistant`.

### Cross-module calls

If `strategy` needs a holdings summary, it must not import `portfolio.repository` or
`portfolio.models`; `portfolio` should expose a dedicated service/DTO. Work that is async,
deferrable or involves several owners should publish a domain event for subscribing modules
to handle.

## Persistence and migrations

Every SQLAlchemy table is registered in `platform/persistence/models.py`; the engine, `Base`,
Session and `get_db` are in `database.py`; versioned migrations are in `migrations.py`.

For a new schema: first settle the behaviour and tests in the owning module; then register the
table and add a new, re-runnable versioned migration. Never edit a released migration, and
never change the schema from a router.

## Where new code goes

| Need | Location |
| --- | --- |
| A new AI, market data or notification vendor | the matching `platform/*` adapter |
| A new investment, analysis or user workflow | the matching `modules/<domain>` service |
| A new API | the owning module's `api/` package; a single `api.py` only when the module really has one router |
| A new background job | business execution in the module; cron/calendar from `platform/scheduling` |
| A new ORM table or migration | `platform/persistence`, used by the owning module's service |
| A helper with business meaning | its owning module; never a new `core` |

## Not allowed

- Bringing back `src/core`, `src/agents`, `src/web/api` or the old `src/web` persistence files;
- letting `platform` import `modules`;
- importing `models.py` or `repository.py` across modules;
- putting business rules, SQL or tool loops in HTTP routers;
- letting `pan_agent` depend on PanWatch, the database or a specific AI SDK;
- creating ownerless root modules in the name of "general helpers".

These rules aren't about adding layers; they make every piece of code's ownership,
dependencies and evolution clear.
