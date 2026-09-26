# Go-live checklist

This lists everything still needed before inviting real users, ordered by priority.

**Owners:**

- **me:** the project owner.
- **lawyer:** a SEBI/DPDP/IP lawyer.
- **RA:** a SEBI-registered research analyst.
- **Claude Code:** engineering work.

"Real users" means anyone other than you. The current code is single-user, so until P1 is
done, each invited user needs **their own installation** ([DEPLOYMENT.md](DEPLOYMENT.md)).

Status column: ☐ open · ◐ partly done · ☑ done.

## P0: legal and regulatory (don't invite anyone before these)

| # | Item | Owner | How to verify | Status |
| --- | --- | --- | --- | --- |
| 1 | Written legal opinion on whether the research-only product (stock-specific bull/bear summaries, key levels, alerts) needs SEBI RA registration or falls under other rules ([COMPLIANCE.md §6](COMPLIANCE.md#6-what-needs-a-sebi-registered-ra-or-a-lawyer)) | lawyer | Opinion on file; answers recorded in PLAN.md §9 (Q-legal closed) | ☐ |
| 2 | Choose the operating model: self-hosted per user, hosted for friends and family, or a public service. Each has different obligations | me + lawyer | Decision in PLAN.md §9 | ☐ |
| 3 | Disclaimer, consent wording and "Simulation" notice approved | lawyer | New text shipped with a bumped disclaimer version; `/api/compliance/status` returns it, and users are asked to acknowledge again | ☐ |
| 4 | Broker API terms (Kite Connect, Upstox, Angel SmartAPI) reviewed for use inside a hosted app with bring-your-own-key | lawyer + me | A written note per broker; STATUS.md updated | ☐ |
| 5 | Global cues data: replace Yahoo with a licensed feed, or turn it off (`GLOBAL_CUES_SOURCE=off`) | me (choose a vendor) + Claude Code | Panel hidden or licensed; the source named on the Data sources page | ☐ |
| 6 | Privacy policy, terms of use, and a grievance contact (DPDP) published and linked from the login page | lawyer + me; Claude Code adds the links | Pages reachable from the login screen | ☐ |
| 7 | If `ra_registered` is ever used: RA review workflow, record keeping, AI-use disclosure | RA + lawyer + Claude Code (Phase 5) | The queue exists; nothing is published without approval (tests) | ☐ (not needed for research-only) |

## P0: security and engineering

| # | Item | Owner | How to verify | Status |
| --- | --- | --- | --- | --- |
| 8 | Replace unsalted SHA-256 passwords with argon2id or scrypt, and migrate existing hashes | Claude Code | Unit tests; no `hashlib.sha256(password` left in the code | ☐ |
| 9 | Close "open until a password is set": require `AUTH_*` or a one-time setup token | Claude Code | A test proves the API returns 401 on a fresh install without setup | ☐ |
| 10 | Restrict CORS to the domain; move tokens to httpOnly cookies with CSRF; rate-limit login; allow token revocation | Claude Code | Tests; a manual check with browser dev tools | ☐ |
| 11 | A security review of the whole app before exposing it to the internet; disclose the path-traversal fix upstream (Q-sec) | Claude Code + me | A review report; issues fixed; upstream contacted | ☐ |
| 12 | Merge the stacked work: PR #1 with a **merge commit**, then PRs for Phase 2, the rename, the UI and the docs, with CI green on each | me + Claude Code | `main` contains all commits; GitHub Actions green | ◐ (PR #1 open, CI green) |
| 13 | Verify each broker adapter with a real account: login, quotes, candles, instrument search, daily re-login | me (accounts and keys) + Claude Code (fixes) | Stock page shows live prices for each broker; STATUS.md changed from "built, unverified" | ☐ |
| 14 | Build and run the Docker image; compose smoke test; healthcheck | Claude Code + me | `docker compose up` works; `/api/health` is 200; the data survives a restart | ☐ |
| 15 | Red-team the guard with real models (agents, deep research, assistant, jailbreaks) | Claude Code; RA reviews samples | Zero advice leaks in a recorded sample; new phrasings added to the corpus | ☐ |
| 16 | Isolate backend tests from the dev database (bug 5 in STATUS.md) | Claude Code | The suite passes whether or not `data/candlewise.db` has a password | ☐ |
| 17 | Test on real phones: iOS Safari and Android Chrome (safe areas, bottom sheets) | me | Screenshots or notes per device | ☐ |

## P1: needed for a hosted, multi-user service

| # | Item | Owner | How to verify | Status |
| --- | --- | --- | --- | --- |
| 18 | Phase 5a: Postgres, user accounts, tenant isolation (fail-closed filter), a single scheduler leader | Claude Code | Isolation test matrix and property tests pass on Postgres | ☐ |
| 19 | Phase 5b: DPDP consent at signup, data export, account deletion, retention, breach runbook, admin panel | Claude Code + lawyer | Export and delete work end to end; runbook in `docs/` | ☐ |
| 20 | LLM billing model (Q7b), per-user usage tracking and quotas | me (decide) + Claude Code | Usage page; quota enforced in tests | ☐ |
| 21 | Scheduled off-site encrypted backups and a monthly restore drill ([DEPLOYMENT.md](DEPLOYMENT.md#backups)) | me | A restore performed and logged | ☐ |
| 22 | Monitoring: uptime on `/api/health`, disk, failed agent runs, LLM spend, compliance events | me | Alerts arrive in a test | ☐ |
| 23 | NSE holiday and special-session calendar (Q11) | me (supply the circular) + Claude Code | Agents skip a known holiday in a test | ☐ |
| 24 | Indian news and filings source (Q8), then implementation | me + lawyer (terms) + Claude Code | The news digest shows sourced Indian items with timestamps | ☐ |
| 25 | Simulation: Indian charges (STT etc.) and lot = 1, or user-entered simulated orders (Q3), or hide the page | Claude Code | Charges golden tests; STATUS.md updated | ☐ |
| 26 | Real delivery test for Telegram, including the disclaimer | me | Message received with the disclaimer | ☐ |
| 27 | Expose the daily agents' structured research items to the stock page (a small read-only API addition) | me (approve) + Claude Code | Stock page shows daily research with sources | ☐ |

## P2: brand, domain and trademark for "Candlewise"

| # | Item | Owner | How to verify | Status |
| --- | --- | --- | --- | --- |
| 28 | Trademark search for "Candlewise" at IP India (software, financial information and SaaS classes; the right classes to be confirmed by the lawyer), plus a common-law search | lawyer | Search report; no blocking marks | ☐ |
| 29 | File the trademark application (word mark; logo later) | lawyer + me | Application number received | ☐ |
| 30 | Check conflicts: company names (MCA), app stores, GitHub and social handles | me | A list of checked names | ☐ |
| 31 | Register the domain (e.g. `candlewise.in` / `.com` / `.app`; availability UNVERIFIED), with auto-renew and registrar 2FA | me | WHOIS shows you; renewal on | ☐ |
| 32 | DNS and HTTPS for the app, plus a support or grievance email on the domain (SPF, DKIM, DMARC) | me + Claude Code | HTTPS A+ on an SSL test; test email delivered | ☐ |
| 33 | Final logo to replace the placeholder candlestick-and-flame (neutral colours, no up/down colour), favicon, PWA icons, social images | designer/me + Claude Code | Assets in `frontend/public/`; README logo updated | ☐ |
| 34 | Optionally rename the GitHub repository `marketdata` → `candlewise`, and update links (`REPO_URL` in `branding.py` and `brand.ts`) | me + Claude Code | Links resolve; old URL redirects | ☐ |
| 35 | Publish release images and set `UPDATE_CHECK_DOCKER_REPO` | Claude Code + me | The Settings update check shows the latest version | ☐ |
| 36 | A support channel and onboarding material ([USER-GUIDE.md](USER-GUIDE.md)) | me | Link shown in the app or README | ◐ (guide written) |

## Nice to have after launch

- WhatsApp (Phase 6, with Meta template approval) and email notifications.
- A Hindi UI (i18n catalogue, Q12).
- Keep-versus-replace decision for TradingAgents (Q14).
- Web fonts for headings (needs dependency approval).
