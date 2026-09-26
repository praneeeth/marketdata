# Deployment

**Read this first.** Candlewise is currently **single-user**: one login, one SQLite
database, and the owner's broker keys ([ARCHITECTURE.md](ARCHITECTURE.md#multi-tenancy)).
So "hosting it for real users" today means **one installation per user**. A shared
multi-user service needs Phase 5 first: Postgres, real auth, tenant isolation, and DPDP
features. Also read [GO-LIVE-CHECKLIST.md](GO-LIVE-CHECKLIST.md) and
[COMPLIANCE.md](COMPLIANCE.md) before inviting anyone.

**The Docker image has never been built**, so everything below is UNVERIFIED until it has.

## Options

| Option | Fits today? | Notes |
| --- | --- | --- |
| **A. One small VPS per user, running Docker** | Yes, recommended | Simplest. The SQLite file lives on a persistent disk, and a reverse proxy adds HTTPS |
| B. A managed container platform (e.g. Render, Railway, Fly.io) | Possible | Needs a persistent volume for `/app/data` and **a single instance**: SQLite and the in-process schedulers must not run twice |
| C. A shared multi-user cloud service | **No** | Blocked on Phase 5 (Postgres, auth, isolation, a scheduler leader) |
| D. Kubernetes / autoscaling | **No** | The schedulers run inside the API process. More than one replica would duplicate every job |

Pick a region near the users, e.g. Mumbai or Bangalore for Indian users. Where the data may
be stored for DPDP purposes is a question for the lawyer ([COMPLIANCE.md](COMPLIANCE.md)).

## Option A step by step (UNVERIFIED)

1. **Server.** Ubuntu LTS, 1–2 vCPU and 2 GB RAM, which should be enough for one user
   (UNVERIFIED, not load-tested). Enable automatic security updates and a firewall that
   allows only 22, 80 and 443.
2. **Docker.** Install Docker Engine and the Compose plugin.
3. **Code and image:**
   ```bash
   git clone https://github.com/praneeeth/marketdata.git candlewise && cd candlewise
   git checkout <release branch or tag>
   make build VERSION=<version>
   ```
4. **Secrets** (see [Secrets](#secrets)). Create `.env` with mode `600`, containing:
   - `AI_*`;
   - `CREDENTIALS_MASTER_KEY`;
   - `JWT_SECRET`;
   - `AUTH_USERNAME` and `AUTH_PASSWORD`, so the API is never open;
   - `APP_ENV=production`;
   - `ALLOW_UNOFFICIAL_DATA=false`;
   - `CANDLEWISE_BASE_URL=https://<domain>`.
5. **Compose.** Use the file from [SETUP.md](SETUP.md#11-docker-and-compose-unverified).
   Bind the app to `127.0.0.1:8000` only, not `0.0.0.0`.
6. **HTTPS and domain.**
   - Point an `A` record at the server.
   - Put a reverse proxy in front: Caddy obtains Let's Encrypt certificates automatically,
     or use Nginx with certbot.
   - Proxy `https://<domain>` to `127.0.0.1:8000`, including `/api` and the SSE routes
     (disable buffering for SSE).
7. **Broker redirect URLs.** Update the Kite/Upstox app redirect URLs to
   `https://<domain>/api/brokers/<kite|upstox>/callback`.
8. **First login.** Log in with the preset credentials, confirm the disclaimer, and check
   that the self-check (account menu) passes.

## Database

- **SQLite** in WAL mode at `/app/data/candlewise.db`, on the Docker volume. This is fine
  for one user.
- **Postgres** isn't supported (Phase 5).
- **Migrations** run automatically at start. A `candlewise.db.bak.<timestamp>` copy is
  written before pending migrations.

## Secrets

| Secret | Where | Notes |
| --- | --- | --- |
| `CREDENTIALS_MASTER_KEY` | `.env` | **Back it up separately from the database.** Without it, stored broker keys can't be decrypted. Rotate with `python -m src.modules.market.brokers rotate-keys` |
| `JWT_SECRET` | `.env`, or generated into the database | Set it explicitly so a database restore keeps logins valid |
| `AI_API_KEY`, notification tokens | `.env` or Settings (stored in the database, masked in the API) | |
| `AUTH_PASSWORD` | `.env` | Stored as unsalted SHA-256, which is weak (bug 1 in [STATUS.md](STATUS.md)). Use a long, unique password |

Keep `.env` out of git; it's already git-ignored. Don't put secrets in the compose file.

## Backups

There's **no built-in scheduled backup**. Suggested approach (UNVERIFIED):

- **Daily online backup:**
  `sqlite3 /app/data/candlewise.db ".backup '/backups/candlewise-$(date +%F).db'"`,
  run inside the container or from the host against the volume.
- **Off-site copy:** encrypt it (e.g. with `age` or `gpg`) and send it off-site with
  `rclone` (S3, B2 or similar).
- **Retention:** keep 14–30 days.
- **Master key:** store `CREDENTIALS_MASTER_KEY` in a password manager, **not** next to
  the backups.
- **Test a restore every month:** stop the container, replace the file, start, then log
  in.

## Monitoring

- **Uptime.** Check `https://<domain>/api/health` every 1–5 minutes with an uptime service.
  The container healthcheck uses the same endpoint.
- **Logs.** Use the in-app log viewer (stored in the database), `docker logs`, and
  `LOG_LEVEL=INFO`.
- **Traces (optional).** OpenTelemetry to Jaeger, Tempo or Langfuse
  (`OTEL_EXPORTER_OTLP_ENDPOINT`).
- **Watch for:**
  - disk space on the data volume;
  - daily broker session expiry: users must log in again each day;
  - failed agent runs (Agents page, `agent_runs`);
  - LLM spend, since deep research records `cost_usd` per run;
  - compliance events, and spikes in redactions or blocks.

## Estimated monthly costs by component

**Every figure here is an UNVERIFIED estimate.** Check current vendor pricing; nothing
below comes from the code or from invoices.

| Component | Options | Rough monthly cost (UNVERIFIED) |
| --- | --- | --- |
| VPS (1–2 vCPU, 2 GB) | DigitalOcean (Bangalore), AWS Lightsail (Mumbai), Hetzner | ~₹400–1,200 (≈ US$5–15) |
| Domain | `.com` / `.in` | ~₹50–100 per month (billed yearly) |
| HTTPS certificate | Let's Encrypt via Caddy or certbot | ₹0 |
| Off-site backups | S3 / B2, a few GB | ~₹0–100 |
| Uptime monitoring | free tiers of common services | ₹0 |
| LLM | e.g. `gpt-4o-mini`. Depends on agents enabled, deep-research runs and chat use; measure with the `cost_usd` recorded per run | ~₹100–2,000 for one active user |
| Broker market data | Kite Connect needs a paid market-data add-on (in-app note). Upstox and Angel pricing unknown | Kite: check Zerodha's current price. Paid **by each user** under bring-your-own-key |
| Notifications | Telegram, Discord and Pushover are free or have one-off app fees. WhatsApp isn't built; Meta charges per conversation or message | Telegram ₹0 |
| Global cues data | Yahoo (free, delayed, unofficial). **Must be replaced by a licensed feed before public launch** | licensed feed: unknown |

For one self-hosted user, expect roughly **₹600–3,500 per month** plus that user's broker
data fee (UNVERIFIED).
