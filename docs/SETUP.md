# Setup: running Candlewise from a fresh machine

This guide is written for macOS or Linux. Windows has a `scripts/build.ps1`, but I haven't
tested it (UNVERIFIED).

Anything marked UNVERIFIED hasn't been checked against the real service. Broker developer
portals change often, so confirm each step against the broker's own documentation.

## 1. Prerequisites

| Tool | Version | Why |
| --- | --- | --- |
| Git | any recent | to clone the repository |
| Python | 3.11 (the Docker image and CI use 3.11; the README says 3.10+) | backend |
| Node.js | 24.14.0 (`frontend/package.json` `engines`, `.nvmrc`) | frontend |
| pnpm | 9.15.9 | frontend package manager. If `corepack` is missing, use `npx -y pnpm@9.15.9` |
| System libraries for PDF export | pango, cairo, gdk-pixbuf (see the `Dockerfile` `apt-get` list) | WeasyPrint PDF export; only needed for PDF features and their tests |

## 2. Get the code

```bash
git clone https://github.com/praneeeth/marketdata.git candlewise
cd candlewise
git checkout feat/ui-lamplight   # latest work; nothing is merged to main yet
```

## 3. Backend

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env              # then edit it (section 4)
set -a; . ./.env; set +a          # export .env into the shell
python server.py                  # http://127.0.0.1:8000
```

`make dev-api` does the virtualenv, install and start in one step.

**What happens on first start:**

- `data/candlewise.db` is created (an old `data/panwatch.db` is adopted).
- Migrations run.
- Agents are seeded.
- The schedulers start.

## 4. Environment variables

Settings are read from `.env` and the environment. Some can also be changed in the UI
under **Settings**.

### AI

| Variable | Meaning | Default |
| --- | --- | --- |
| `AI_BASE_URL` | Any OpenAI-compatible endpoint (OpenAI, a gateway to other models, DeepSeek, a local Ollama) | `https://api.openai.com/v1` |
| `AI_API_KEY` | The key for that endpoint | empty |
| `AI_MODEL` | The model name | `gpt-4o-mini` |

AI providers and models can also be added in the UI (**Settings → AI services**), with
failover between them.

The code also reads `OPENAI_API_KEY`, `DEEPSEEK_API_KEY` and `OPENROUTER_API_KEY`. Where
exactly they're used, apparently the TradingAgents configuration, is UNVERIFIED.

### Assistant context (optional)

| Variable | Meaning | Default |
| --- | --- | --- |
| `CONTEXT_MAX_TOKENS` | Total context budget | 12,000 |
| `CONTEXT_SOFT_LIMIT_TOKENS` | Soft limit | 8,400 |
| `CONTEXT_HARD_LIMIT_TOKENS` | Hard limit | 10,200 |
| `CONTEXT_KEEP_RECENT_MESSAGES` | Recent messages kept in full | 8 |
| `CONTEXT_SUMMARY_MAX_TOKENS` | Summary size | 800 |
| `CONTEXT_COMPRESSION_MODEL_ID` | The model used for compression | none |
| `CONTEXT_COMPRESSION_TEMPERATURE` | Its temperature | 0.1 |

### Login

| Variable | Meaning |
| --- | --- |
| `AUTH_USERNAME`, `AUTH_PASSWORD` | Optional. Preset the single login. Otherwise the first visitor sets it, and **until then the API is open** |
| `JWT_SECRET` | Signing key for login tokens. Generated and stored in the database if unset |

### Compliance

| Variable | Meaning | Default |
| --- | --- | --- |
| `ADVISORY_MODE` | `research_only` or `ra_registered`. Environment only; there's no UI setting | `research_only` |
| `RA_REGISTRATION_NUMBER`, `RA_NAME` | Required for `ra_registered` (format `INH` + 9 digits), or startup fails. `ra_registered` currently behaves like `research_only` | empty |
| `RA_CONTACT`, `RA_DISCLOSURE_URL` | Optional RA details | empty |
| `MCP_ENABLED` | Mount the MCP server and personal access tokens | `false` |

### Market data

| Variable | Meaning | Default |
| --- | --- | --- |
| `CREDENTIALS_MASTER_KEY` | Encrypts broker keys and tokens. **Broker connections stay disabled until it's set.** Generate one with `python -m src.platform.security.credential_vault` (format `<key-id>:<base64>`; several comma-separated for rotation). Losing every key means re-entering all broker keys | unset |
| `APP_ENV` | `development` / `dev` / `local` / `test`, or anything else, which counts as production. **Unset counts as production** | unset |
| `ALLOW_UNOFFICIAL_DATA` | Enables the delayed Yahoo stock source. Only honoured in a development `APP_ENV`; never in production | `false` |
| `GLOBAL_CUES_SOURCE` | `yahoo` (free, delayed, labelled) or `off` | `yahoo` |

### Notifications

| Variable | Meaning |
| --- | --- |
| `NOTIFY_TELEGRAM_BOT_TOKEN`, `NOTIFY_TELEGRAM_CHAT_ID` | Seed a Telegram channel on first start. Channels are then managed in **Settings → Notification channels** |
| Quiet hours, retries, dedupe | Set in **Settings** |

### Network

| Variable | Meaning |
| --- | --- |
| `HTTP_PROXY`, `HTTPS_PROXY`, `http_proxy` | Outbound proxy. Priority: environment, then the UI setting, then `.env` |
| `CA_CERT_FILE` | A corporate CA certificate (e.g. behind Zscaler) |
| `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE` | Also read |

### Runtime

| Variable | Meaning | Default |
| --- | --- | --- |
| `TZ`, `APP_TIMEZONE` | Timezone for schedules and display | `Asia/Kolkata` |
| `DATA_DIR` | Avatars and intraday state. The database is always `data/candlewise.db` under the app root | `./data` |
| `LOG_LEVEL` | `INFO` or `DEBUG` | `INFO` |
| `DEV_RELOAD` | Autoreload for development | UNVERIFIED |
| `CANDLEWISE_BASE_URL` | Public URL used in notification links (`PANWATCH_BASE_URL` still works) | unset |
| `UPDATE_CHECK_DOCKER_REPO`, `UPDATE_CHECK_DISABLE` | Update check; off unless a repository is set | unset |
| `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME` | Optional OpenTelemetry. Needs `pip install -r requirements-otel.txt` | off |

### Dead setting

`DAILY_REPORT_CRON` is listed in `.env.example`, but it appears unused
([STATUS.md](STATUS.md), bug 10). Change schedules on the **Agents** page instead.

### Minimal `.env` for local development

```dotenv
AI_BASE_URL=https://api.openai.com/v1
AI_API_KEY=sk-...
AI_MODEL=gpt-4o-mini
APP_ENV=development
ALLOW_UNOFFICIAL_DATA=true      # dev only: delayed Yahoo prices without a broker
CREDENTIALS_MASTER_KEY=<output of python -m src.platform.security.credential_vault>
```

## 5. Frontend

```bash
cd frontend
pnpm install          # or: npx -y pnpm@9.15.9 install
pnpm dev              # http://localhost:5183, proxies /api to 127.0.0.1:8000
```

`make dev-web` does the same. Open http://localhost:5183 and set your username and
password. **Do this straight away**: the API is open until you do.

## 6. LLM key

1. Create an API key with your provider, e.g. OpenAI's API dashboard.
2. Put it in `AI_API_KEY`, or add it under **Settings → AI services**.
3. Use **Settings** to test the service.

The pricing and rate limits of each provider are outside this repository (UNVERIFIED).

## 7. Broker API keys

Each user brings their own broker API app. Keys are entered in **Data sources**, encrypted
with `CREDENTIALS_MASTER_KEY`, and never shown again, except as a masked hint.

**All three adapters are tested only against fixtures written from the brokers'
documentation. None has been used with a live account** ([STATUS.md](STATUS.md)).

The in-app notes (`src/modules/market/brokers.py`) say the following about each broker.
The developer-portal steps are UNVERIFIED; follow the broker's current docs.

### Zerodha Kite Connect

- Create a Kite Connect app. The docs linked in the code are at
  `https://kite.trade/docs/connect/v3/`.
- The in-app note says the app needs the **market data add-on (paid)**. The current price
  is UNVERIFIED.
- Set the app's **redirect URL** to `<your server>/api/brokers/kite/callback`, for example
  `http://127.0.0.1:8000/api/brokers/kite/callback` locally.
- In **Data sources → Zerodha Kite Connect**, enter the **API key** and **API secret**,
  then **Log in**.
- Tokens expire daily, so you log in again each day.

### Upstox

- Create an Upstox developer app. The docs linked in the code are at
  `https://upstox.com/developer/api-documentation/`.
- Set its redirect URL to `<your server>/api/brokers/upstox/callback`.
- Enter the **client id**, **client secret** and **the same redirect URI** in **Data
  sources**, then log in.
- Log in again each day.

### Angel One SmartAPI

- Create a SmartAPI app. The docs linked in the code are at
  `https://smartapi.angelbroking.com/docs`, which may now be under an angelone.in domain
  (UNVERIFIED).
- Enable TOTP on your Angel One account.
- Enter the **API key**, **client code** and **PIN** in **Data sources**.
- Each login asks for the 6-digit **TOTP** code. It's used once and never stored.

### Development only: Yahoo

Set `APP_ENV=development` and `ALLOW_UNOFFICIAL_DATA=true`, then connect **Yahoo Finance
(development only)**. Its data is delayed and unofficial, and its terms don't allow
redistribution. Never use it in production.

## 8. Telegram bot

1. In Telegram, open **@BotFather**, send `/newbot` and copy the **bot token**.
2. Send any message to your new bot.
3. Find your **chat id**. One common way is to open
   `https://api.telegram.org/bot<token>/getUpdates` and read `message.chat.id`.
4. Add the channel in **Settings → Notification channels → Telegram**, with the bot token
   and chat id, or set `NOTIFY_TELEGRAM_BOT_TOKEN` and `NOTIFY_TELEGRAM_CHAT_ID` before the
   first start.
5. Use **Test** on the channel.

Real delivery hasn't been verified in this fork (UNVERIFIED). Discord needs a webhook id
and token; Pushover needs a user key and app token.

## 9. WhatsApp Business: not available yet

**WhatsApp isn't implemented** (Phase 6). There's no code to configure. When Phase 6
starts, the plan (PLAN.md §5, Phase 6) expects:

- a Meta Business account and a WhatsApp Business Platform (Cloud API) phone number;
- approved message templates, with the disclaimer as static template body text;
- a webhook with a verify token and `X-Hub-Signature-256` validation;
- per-user phone verification and opt-in records.

Meta's current pricing and its policy on financial content are UNVERIFIED and need
checking before any work starts. Until then, use Telegram.

## 10. Running the tests

```bash
source .venv/bin/activate
ruff check . && ruff format --check .
mypy
python -m pytest tests/ -q --cov      # backend + 90% gate on new code
for p in packages/*/; do (cd "$p" && python -m pytest tests -q); done
cd frontend && pnpm exec vitest run && pnpm build
git diff --check
```

**Known issue:** some backend tests use the real `data/candlewise.db`. If it has a login
password, 16 compliance tests fail with 401. Move the database aside while testing and
restore it afterwards:

```bash
mkdir -p /tmp/cw && mv data/candlewise.db* /tmp/cw/
python -m pytest tests/ -q --cov
rm -f data/candlewise.db*; mv /tmp/cw/candlewise.db* data/
```

## 11. Docker and Compose (UNVERIFIED)

**The Docker image has never been built**, and there's no `docker-compose.yml` in the
repository. The commands below follow the `Dockerfile`, the `Makefile` and the README, but
are untested.

```bash
make build VERSION=dev        # runs build.sh -> image candlewise:dev
docker run -d --name candlewise -p 8000:8000 \
  -v candlewise_data:/app/data --env-file .env candlewise:dev
```

`docker-compose.yml` (from the README, with `env_file` added):

```yaml
services:
  candlewise:
    image: candlewise:dev
    container_name: candlewise
    ports:
      - "8000:8000"
    volumes:
      - candlewise_data:/app/data
    env_file: .env
    environment:
      - TZ=Asia/Kolkata
    restart: unless-stopped
volumes:
  candlewise_data:
```

About the image:

- The frontend is built into it and served at `/`.
- The healthcheck calls `/api/health`.
- If you're coming from PanWatch, mount the old volume: `panwatch.db` is renamed on start.
