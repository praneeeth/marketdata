# Contributing

Thanks for your interest in Candlewise! This guide explains how to contribute, in particular
how to write agents and market data providers.

Candlewise is a fork of [PanWatch](https://github.com/TNT-Likely/PanWatch) (MIT) for Indian
markets. The app is research-only by default (see `ADVISORY_MODE` in
[`.env.example`](.env.example) and `docs/adr.md`). New features must not produce buy/sell/hold
calls, price targets, entry levels, stop-losses or position sizes in research-only mode; the
compliance guard in `src/platform/compliance/` enforces this, and new user-facing text should
pass through it.

## Contents

- [Project layout](#project-layout)
- [Development setup](#development-setup)
- [Writing an agent](#writing-an-agent)
- [Adding a market data provider](#adding-a-market-data-provider)
- [Commit conventions](#commit-conventions)

---

## Project layout

```
.
├── src/
│   ├── bootstrap/        # app startup and wiring
│   ├── platform/         # technical capabilities (persistence, AI, market data, scheduling, notifications, compliance)
│   ├── modules/          # product capabilities (automation agents, market, portfolio, research, strategy, ...)
│   └── web/              # shared HTTP middleware
├── packages/
│   ├── marketdata/       # standalone market data package (India broker providers, global cues)
│   └── pan-agent-*/      # the bounded agent runtime and its plugins
├── prompts/              # AI prompt templates
├── frontend/             # React frontend
└── server.py             # entry point
```

See [`src/ARCHITECTURE.md`](src/ARCHITECTURE.md) for the module boundaries.

---

## Development setup

```bash
# Backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python server.py

# Frontend
cd frontend
pnpm install
pnpm dev
```

Or use `make dev-api` and `make dev-web`.

---

## Writing an agent

An agent is Candlewise's core analysis unit: it collects data, calls the AI and sends
notifications.

### 1. Create the agent file

Create a new file in `src/modules/automation/`, for example `my_agent.py`:

```python
import logging
from datetime import datetime
from pathlib import Path

from src.modules.automation.base import BaseAgent, AgentContext, AnalysisResult

logger = logging.getLogger(__name__)

# Prompt file path
PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "my_agent.txt"


class MyAgent(BaseAgent):
    """My custom agent."""

    # Required: the agent id (used in the database and API)
    name = "my_agent"

    # Required: the display name shown in the UI
    display_name = "My agent"

    # Required: a description
    description = "An example of a custom agent"

    async def collect(self, context: AgentContext) -> dict:
        """
        Collect data.

        Args:
            context: includes watchlist (the watchlist stocks), portfolio (holdings), etc.

        Returns:
            A dict of collected data, passed to build_prompt
        """
        data = {
            "stocks": [],
            "timestamp": datetime.now().isoformat(),
        }

        # Collect data for each watchlist stock
        for stock in context.watchlist:
            # stock.symbol: the NSE symbol, e.g. INFY
            # stock.name: the company name
            # stock.market: the market (IN)
            pass

        # Holdings
        # context.portfolio.all_positions: every position
        # context.portfolio.get_aggregated_position(symbol): one stock's combined position

        return data

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        """
        Build the AI prompt.

        Args:
            data: what collect() returned
            context: the agent context

        Returns:
            A (system_prompt, user_content) tuple
        """
        # Read the prompt template
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

        # Build the user input
        lines = []
        lines.append("## Data")
        # ... format the data

        user_content = "\n".join(lines)
        return system_prompt, user_content

    async def should_notify(self, result: AnalysisResult) -> bool:
        """
        Whether to send a notification (optional override).

        Returns True by default; decide from the analysis result if needed.
        """
        # e.g. only notify on important signals
        # return "important" in result.content
        return True
```

### 2. Create the prompt template

Create the matching prompt file `prompts/my_agent.txt`. Keep it research-only: describe
and explain, never recommend trades.

```
You are a careful equity research assistant for Indian markets (NSE/BSE).

## Task
Analyse the data provided...

## Output format
1. Overview
2. Detailed analysis
3. What to research next
```

### 3. Register the agent

Register it in `server.py`, and add a seed spec in `src/modules/automation/agent_catalog.py`:

```python
# server.py
from src.modules.automation.my_agent import MyAgent

AGENT_REGISTRY: dict[str, type] = {
    "daily_report": DailyReportAgent,
    # ...
    "my_agent": MyAgent,  # add this line
}
```

```python
# agent_catalog.py: add an AgentSeedSpec to AGENT_SEED_SPECS
AgentSeedSpec(
    name="my_agent",
    display_name="My agent",
    description="A custom agent",
    enabled=False,               # off by default; the user enables it
    schedule="0 16 * * 1-5",     # cron expression (IST)
    execution_mode="batch",      # batch: analysed together / single: one stock at a time
    kind="workflow",             # workflow (scheduled) or capability (internal)
    visible=True,
)
```

### 4. The agent context

`AgentContext` provides:

| Attribute | Type | Description |
|------|------|------|
| `watchlist` | `list[StockConfig]` | the linked watchlist stocks |
| `portfolio` | `PortfolioInfo` | holdings |
| `ai_client` | `AIClient` | the AI client |
| `notifier` | `NotifierManager` | the notification manager |
| `model_label` | `str` | the label of the model in use |

### 5. Run modes

- **batch**: all stocks analysed together; suits reports
- **single**: one stock at a time; suits live monitoring

---

## Adding a market data provider

Quotes and K-lines for NSE/BSE come from the user's own broker connection, through the
standalone `packages/marketdata` package.

### 1. Implement the provider

Add a module under `packages/marketdata/src/marketdata/india/` that implements the
`MarketDataProvider` protocol in `provider.py` (`quotes`, `candles`, `instruments`,
`corporate_actions`, `option_chain`). `kite.py` (Zerodha Kite Connect) and `angel.py`
(Angel One SmartAPI) are the reference implementations. Raise the typed errors from
`marketdata.india.errors` rather than returning empty data, so callers can tell "no data" from
"failed".

### 2. Wire up the broker connection

Broker connections (API keys, login, encrypted token storage) are managed by
`src/modules/market/brokers.py` (`BrokerManager`). Add the provider there so it shows up
under **Data sources** in the app. Credentials are encrypted at rest with
`CREDENTIALS_MASTER_KEY`.

### 3. Data terms

Only use data you're allowed to use. Unofficial sources (such as Yahoo Finance) are for
development only and must stay behind `ALLOW_UNOFFICIAL_DATA`; say in the PR where the data
comes from and what its limits are.

---

## Commit conventions

### Commit format

```
<type>: <subject>

<body>
```

**Types:**
- `feat`: a new feature
- `fix`: a bug fix
- `docs`: documentation
- `refactor`: refactoring
- `style`: formatting
- `test`: tests

**Example:**
```
feat: add the intraday monitor agent

- detect unusual price moves
- detect unusual volume
- let the AI decide whether a notification is needed
```

### PR requirements

1. Lint passes (`ruff check .`, `ruff format --check .`, `mypy`), and tests pass (`make test`, frontend `pnpm test`).
2. New features update the documentation.
3. Agents come with a prompt template.
4. Market data providers state their data source and its limits.

---

## Questions

Open an issue or a PR.
