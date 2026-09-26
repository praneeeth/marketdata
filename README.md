> **India fork (work in progress).** This repository is a fork of
> [PanWatch](https://github.com/TNT-Likely/PanWatch) by sunxiao0721, used under the MIT
> License (see [`LICENSE`](LICENSE)). It is being adapted into a research-only assistant
> for Indian retail investors (NSE/BSE). See [`docs/india-fork/PLAN.md`](docs/india-fork/PLAN.md)
> for the plan and [`docs/india-fork/ARCHITECTURE.md`](docs/india-fork/ARCHITECTURE.md)
> for the architecture map.
>
> Nothing in this project is investment advice.

---

# PanWatch (India fork)

**A self-hosted AI research assistant for Indian stocks (NSE/BSE)**, with multi-agent deep
research through [TradingAgents](https://github.com/TauricResearch/TradingAgents): live
quotes from your own broker, portfolio tracking, technical analysis, price alerts and
notifications.

The app runs in **research-only** mode by default (see `ADVISORY_MODE` in
[`.env.example`](.env.example)): it explains, summarises and monitors, but never tells you
what to buy or sell, sets price targets or suggests position sizes.

## Why self-hosted

- **Your data stays with you**: holdings, broker keys and history live in your own database.
- **AI-native**: the AI works with your holdings, watchlist and trading style, not just indicators.
- **Quick to start**: one Docker command and a few minutes of setup.

## Features

<details>
<summary><b>Agents</b></summary>

| Agent | When | What it does |
|-------|------|--------------|
| **Pre-market outlook** | Before the open | Combines the previous close, overnight global cues and news into a research note for the day |
| **Intraday monitor** | During market hours | Watches for unusual moves; alerts when RSI/KDJ/MACD line up |
| **Daily close report** | After the close | Reviews the day's moves and what to watch next |
| **News digest** | On a schedule | Collects news and picks out what matters for your holdings |
| **TradingAgents deep research** | On demand | Technical / sentiment / news / fundamentals analysts, a bull vs bear debate, risk review and a PM summary, in 3-5 minutes |

</details>

<details>
<summary><b>Technical analysis</b></summary>

- **Trend**: moving average alignment, MACD golden/death crosses, Bollinger band breaks
- **Momentum**: RSI overbought/oversold, KDJ
- **Price and volume**: volume ratio spikes, pullbacks on falling volume, breakouts on rising volume
- **Candlestick patterns**: hammer, engulfing, doji and more
- **Support and resistance**: multi-level levels calculated automatically

</details>

<details>
<summary><b>Market data and accounts</b></summary>

- **Market**: NSE/BSE, with quotes and K-lines from your own broker connection
  (see **Data sources** in the app)
- **Global cues**: world indices, Brent, gold and USD/INR for context (free, delayed data;
  see `GLOBAL_CUES_SOURCE`)
- **Accounts**: several brokerage accounts, tracked separately and summed up
- **Trading style**: short term / swing / long term per position

</details>

<details>
<summary><b>Notifications</b></summary>

Telegram / Discord / Pushover

</details>

<details>
<summary><b>Price alerts</b></summary>

- Price, change %, turnover and volume ratio conditions, combined with AND / OR
- Market hours only or all day, cooldowns, a daily trigger limit and repeat modes
- An optional expiry date; empty means it never expires
- Choose notification channels per rule, or use the system default

</details>

## Quick start

Build and run the image:

```bash
make build VERSION=dev
docker run -d \
  --name panwatch \
  -p 8000:8000 \
  -v panwatch_data:/app/data \
  panwatch:dev
```

Open `http://localhost:8000` and set a username and password on first use.

<details>
<summary>Docker Compose</summary>

```yaml
services:
  panwatch:
    image: panwatch:dev
    container_name: panwatch
    ports:
      - "8000:8000"
    volumes:
      - panwatch_data:/app/data
    environment:
      - TZ=Asia/Kolkata
    restart: unless-stopped

volumes:
  panwatch_data:
```

```bash
docker compose up -d
```

</details>

<details>
<summary>Environment variables</summary>

See [`.env.example`](.env.example) for the full list, including the compliance, broker and
market data settings.

| Variable | Description | Default |
|----------|-------------|---------|
| `AUTH_USERNAME` | Preset login username | set on first visit |
| `AUTH_PASSWORD` | Preset login password | set on first visit |
| `JWT_SECRET` | JWT signing key | generated automatically |
| `DATA_DIR` | Data directory | `./data` |
| `TZ` | App time zone (agent schedules and time display) | `Asia/Kolkata` |
| `LOG_LEVEL` | Console log level. `INFO` shows business events and errors; `DEBUG` adds scheduler heartbeats and collection details. The in-app log board always keeps the full record | `INFO` |
| `HTTP_PROXY` / `HTTPS_PROXY` / `http_proxy` | Outbound HTTP proxy. Set it with `export HTTP_PROXY=...` before starting, `http_proxy=http://host:port` in `.env`, or **Settings → Global HTTP proxy** in the UI. Priority: environment > UI > `.env`. Every httpx client then uses it; `NO_PROXY` includes `localhost,127.0.0.1` by default | not set |
| `CREDENTIALS_MASTER_KEY` | Encrypts broker API keys and tokens at rest; broker connections stay off until it is set | not set |
| `ADVISORY_MODE` | `research_only` (default) or `ra_registered` | `research_only` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OpenTelemetry OTLP endpoint (e.g. `http://jaeger:4318`). OTel export is only on when this is set and `requirements-otel.txt` is installed. See "OTel export" below | not set (off) |

</details>

<details>
<summary>First-time setup</summary>

1. Open the web UI and set up your login.
2. **Settings → AI providers**: add an OpenAI-compatible API (OpenAI / Anthropic via a gateway / DeepSeek / Ollama, etc.).
3. **Data sources**: connect your broker for NSE/BSE quotes.
4. **Settings → Notification channels**: add Telegram or another channel.
5. **Holdings → Add stock**: add stocks to your watchlist and enable the agents you want.

</details>

<details>
<summary>Local development</summary>

**Requirements**: Python 3.10+ / Node.js 18+ / pnpm

```bash
# One command each (recommended)
make dev-api          # backend (creates the venv and installs dependencies; listens on :8000)
make dev-web          # frontend (runs pnpm install; listens on :5183)

# Or by hand
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python server.py                              # backend :8000

cd frontend && pnpm install && pnpm dev       # frontend :5183
```

The frontend dev server runs at `http://localhost:5183` and proxies `/api` to
`127.0.0.1:8000`. It uses `:5183` rather than the default `:5173` to avoid clashing with
other local frontends.

</details>

<details>
<summary><b>Tech stack</b></summary>

**Backend**: FastAPI / SQLAlchemy / APScheduler / OpenAI SDK

**Frontend**: React 18 / TypeScript / Tailwind CSS / shadcn/ui

</details>

<details>
<summary><b>OTel export (optional, off by default)</b></summary>

PanWatch has its own observability built in (structured logs with a `trace_id` throughout,
the `agent_runs` table, and TradingAgents per-node progress and cost), with no external
components needed.

On top of that you can **optionally** add standard [OpenTelemetry](https://opentelemetry.io/)
export and send traces to Jaeger, Tempo, Langfuse or another APM. Three kinds of span:

- **One agent run** → a root span (linked by the `trace_id` of `agent_runs`)
- **One LLM call** → a `gen_ai` child span following the
  [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
  (`gen_ai.system` / `gen_ai.request.model` / `gen_ai.usage.input_tokens` /
  `gen_ai.usage.output_tokens` / `gen_ai.operation.name`), so APMs recognise it as a model call
- **TradingAgents nodes** → child spans (from the per-node progress callbacks)

**Off by default**: without the dependencies or an endpoint, the export layer is a no-op
and nothing changes.

**Turning it on**:

```bash
# 1. Install the optional dependencies
pip install -r requirements-otel.txt

# 2. Point it at your OTLP endpoint (collector / APM)
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
export OTEL_SERVICE_NAME=panwatch   # optional; panwatch by default

# 3. Start as usual; "OTel export enabled" in the startup log means it's on
python server.py
```

**Try it with a local Jaeger**:

```bash
docker run -d --name jaeger -p 16686:16686 -p 4318:4318 \
  jaegertracing/all-in-one:latest
# Run any agent, then open http://localhost:16686 and pick service=panwatch
```

Langfuse and Tempo work the same way: point `OTEL_EXPORTER_OTLP_ENDPOINT` at their OTLP
endpoint.

</details>

## Contributing

Issues and PRs are welcome. See the [contributing guide](CONTRIBUTING.md) for building
custom agents and data sources.

## Credits

Built on [PanWatch](https://github.com/TNT-Likely/PanWatch) by sunxiao0721 (MIT). If the
original project helps you, consider starring it.

## License

[MIT](LICENSE)
