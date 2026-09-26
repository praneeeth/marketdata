# Build history

This page lists what was built, phase by phase, with links to PRs, branches, decisions and
ADRs. Sources: `git log`, the GitHub API (PRs and Actions runs), [`adr.md`](adr.md) and
[`india-fork/PLAN.md`](india-fork/PLAN.md) (the plan, the phase reports in §8 and the
decision log in §9).

## Where the code lives

**Nothing has been merged into `main` yet.** `main` still holds only the repository's
initial commit. The work is on stacked branches, each built on the one before:

| Branch | Built on | PR | CI |
| --- | --- | --- | --- |
| `claude/india-market-stock-research-wuzxp1` (Phase 0, upstream import, Phase 1) | `main` | [#1](https://github.com/praneeeth/marketdata/pull/1), **open, not merged** | 3 runs on 2026-09-24, all passed |
| `phase-2/india-market-data` (Phase 2, English-only) | the Phase 1 branch | none opened | never run (CI runs on pull requests) |
| `chore/rename-candlewise` (rename) | `phase-2/india-market-data` | none opened | never run |
| `feat/ui-lamplight` (UI redesign) | `chore/rename-candlewise` | none opened | never run |
| `docs/project-documentation` (these docs) | `feat/ui-lamplight` | see the PR for this branch | never run |

Compare views:
[Phase 2](https://github.com/praneeeth/marketdata/compare/claude/india-market-stock-research-wuzxp1...phase-2/india-market-data),
[rename](https://github.com/praneeeth/marketdata/compare/phase-2/india-market-data...chore/rename-candlewise),
[UI redesign](https://github.com/praneeeth/marketdata/compare/chore/rename-candlewise...feat/ui-lamplight).

**How to merge.** PR #1 must be merged with **"Create a merge commit", not squash**. A
squash would collapse the imported upstream history that ADR-001 keeps for attribution.

## Summary

| Phase | Scope (from PLAN.md) | State | Date |
| --- | --- | --- | --- |
| 0 | Architecture map, China-specific assumptions, phased plan | Done | 2026-09-24 |
| Upstream import | Merge PanWatch history at `89bdf3f` | Done | 2026-09-24 |
| 1 | Compliance guard, research-only contracts, feature gates, security fixes, CI | Done | 2026-09-24 |
| 2 | India market data (brokers, vault), India-only app, global cues | Done | 2026-09-24 to 26 |
| 3 | NSE calendar, sessions, circuits, INR, Indian charges/T+1, schedules | **Not started**; a few pieces landed elsewhere | — |
| 4 | Filings and news, English prompts and UI, branding | **Partial:** English and branding done; news/filings and the i18n library not started | 2026-09-26 |
| 5 | Postgres, multi-user auth, tenant isolation, admin, RA queue, DPDP | **Not started** | — |
| 6 | WhatsApp, Telegram platform bot, notification disclaimers | **Partial:** disclaimers (Phase 1) and CN channel removal (Phase 2) done; WhatsApp and the platform bot not started | — |
| Rename | PanWatch → Candlewise | Done | 2026-09-26 |
| UI redesign | "Lamplight" design, stock page, states | Done | 2026-09-27 |

## Phase 0: analysis and plan (2026-09-24)

- **What:** [`india-fork/ARCHITECTURE.md`](india-fork/ARCHITECTURE.md) maps upstream
  PanWatch at `89bdf3f` and catalogues its China-specific assumptions.
  [`india-fork/PLAN.md`](india-fork/PLAN.md) sets out Phases 1–6, a risk register and open
  questions Q1–Q20.
- **Baseline measured on upstream:**

  | Suite | Result |
  | --- | --- |
  | Backend | 784 passed, 3 skipped |
  | ruff | 1,026 findings |
  | mypy `--strict` | 2,065 errors |
  | Frontend vitest | 43 passed |

- **Commits:** `1f6353a`, `d172e6f` (on PR #1).
- **Key findings:**
  - Upstream has no order execution, but it has plenty of recommendation machinery.
  - The notification path has one choke point; the AI output paths don't (20 surfaces).
  - A static-file path traversal could expose the database.
  - Passwords were unsalted SHA-256, APIs were open until first setup, and secrets were
    returned in plain text.

## Upstream import (2026-09-24)

- **Decision:** Q1 option A, merging PanWatch history pinned to `89bdf3f`
  ([ADR-001](adr.md#adr-001-import-upstream-panwatch-by-merging-its-history-pinned-to-89bdf3f)).
- **Where:** PR #1.

## Phase 1: compliance guard (2026-09-24, PR #1)

**What was built** (commits `dd4c5f8` to `efef0db`):

- **Output guard** with a fail-closed `GuardedText` type on every AI output path
  ([ADR-002](adr.md#adr-002-deterministic-fail-closed-output-guard-in-layers)).
- **Research-only output contract:** agents return `research` items (bull/bear/risks/
  levels/events/sources), and prompts are English
  ([ADR-003](adr.md#adr-003-research-only-output-contract-for-agents)).
- **Recommendation features gated:** routers not mounted or returning 403, UI hidden,
  MCP off by default
  ([ADR-004](adr.md#adr-004-disposition-of-upstream-recommendation-features-in-research_only)).
- **TradingAgents graph replaced after the debate:** the trader, risk and PM nodes are
  never built
  ([ADR-005](adr.md#adr-005-tradingagents-neutralised-by-replacing-the-graph-after-the-debate)).
- **Tooling and CI:** ruff, mypy `--strict` and a 90% coverage gate on new code, plus a
  PR workflow ([ADR-006](adr.md#adr-006-tooling-and-ci-for-the-fork)).
- **Security fixes** (Q-sec): the static-file path traversal is blocked, and stored
  secrets are masked in API responses.
- **Consent and labels:** a disclaimer footer, an onboarding disclaimer, versioned consent,
  and paper trading labelled "Simulation".
- **Local fixes (2026-09-24, on the same branch):** the welcome guide now opens only after
  the disclaimer is accepted (`efef0db`), and a macOS temp-path test was fixed (`c8e47d2`).

**Results (PLAN §8.1):**

| Suite | Result |
| --- | --- |
| Backend | 1,068 passed, 3 skipped |
| Coverage on new code | 97.9% |
| Frontend vitest | 64 passed |

**Decisions:** Q1–Q20 recorded on 2026-09-24 (PLAN §9). The key ones:

- Q3: no AI paper-trading entries in research-only mode.
- Q4/Q5: opportunities and evaluations are off.
- Q6: the guard is deterministic.
- Q13: MCP is off.
- Q16: the dev dependencies are approved.
- Q18: `ADVISORY_MODE` is set from the environment only.
- Q19: retention periods.

## Phase 2: India market data (2026-09-24 to 26, branch `phase-2/india-market-data`)

**What was built** (commits `935dbce` to `10ea135`):

- **`marketdata.india`:** Kite Connect v3, Upstox and Angel One SmartAPI adapters over
  `httpx` (no broker SDKs), plus a dev-only yfinance adapter, all run through one
  adapter contract suite ([ADR-007](adr.md#adr-007-india-market-data-layer-with-bring-your-own-key-brokers)).
- **`IndiaMarketData` service** with per-credential caches, so one user's data is never
  served with another's key.
- **Credential storage:** an AES-256-GCM `CredentialVault` with key rotation, the
  `broker_connections` table (migration 128), the `/api/brokers` API and a broker panel in
  the UI.
- **India-only app** (owner decision, 2026-09-25): `MarketCode.IN` is the only market. The
  Chinese/HK/US vendors, akshare, efinance, Playwright, the Chinese market features and
  the CN notification channels were removed (migration 129).
- **Global markets panel:** free, delayed Yahoo data, labelled "Delayed / unofficial".
- **Fixes found while running the app:**
  - Restarting after migration 129 crashed.
  - Schedules ran in China time; the default timezone is now IST.
  - Search returned nothing without a broker instrument list.
  - Yahoo float noise wasn't rounded.
- **Review fixes:** code review findings 1–9, each with a regression test (`85f5dfb`,
  `bb74a45`).

**Results (PLAN §8.2):**

| Suite | Result |
| --- | --- |
| Backend | 1,071 passed, 3 skipped |
| Coverage on new code | 98% |
| marketdata package | 173 passed, 4 skipped |
| Frontend vitest | 81 passed |

**Decisions:**

- 2026-09-24: `cryptography` pinned; Phase 2 on its own stacked branch; English
  translation right after Phase 2.
- 2026-09-25: India-only; a global cues panel with an interim free source (Q8, partly
  decided).

## English-only (Phase 4, first part; 2026-09-26, branch `phase-2/india-market-data`)

**What was built** (commits `e76225f`, `e1b856a`, `8b912c7`):

- **English everywhere:** the backend, packages, tests, frontend, docs and build scripts.
  Chinese remains only as detection data in the compliance guard and in checksummed
  released migrations.
- **Migration 130** translates stored labels.
- **Shared vocabulary:** one set of English K-line status words for the backend and
  frontend.
- **Conventions:** green means up, lakh/crore units, `TZ=Asia/Kolkata` in Docker, and
  OpenAI as the default AI endpoint.
- **Guard rules** for English decision labels.
- **Upstream assets:** donation QR codes and Chinese screenshots removed.

**Not done from Phase 4:**

- the Indian news and filings feed;
- the `react-i18next` catalogue (the dependency was approved, Q12, but not added);
- a Hindi scaffold;
- keep-versus-replace for TradingAgents (Q14).

## Rename to Candlewise (2026-09-26, branch `chore/rename-candlewise`)

- **What:** commits `025e454`, `3ef02f9` and `7239b6e`.
  - Every PanWatch identifier, string, package alias, image and volume name was renamed.
  - Backward compatibility: `data/panwatch.db` is adopted as `data/candlewise.db`,
    migration 131 moves the setting key, `PANWATCH_BASE_URL` still works, the old AI JSON
    tag still parses, and browser storage keys are migrated.
  - A placeholder candlestick-and-flame logo and the tagline were added.
  - The update checker is off unless `UPDATE_CHECK_DOCKER_REPO` is set.
- **Decisions:**
  - Q17: the name is Candlewise
    ([ADR-008](adr.md#adr-008-rename-the-product-to-candlewise)).
  - The Python package is metadata only (`pyproject.toml`); the code stays in `src/`.
  - The `pan-agent-*` package names are kept.
- **Results:**

  | Suite | Result |
  | --- | --- |
  | Backend | 1,104 passed, 3 skipped |
  | Coverage on new code | 98.65% |
  | Frontend vitest | 83 passed |

## UI redesign "Lamplight" (2026-09-27, branch `feat/ui-lamplight`)

- **What:** commits `57e3d04` to `9ee13ec`. The owner chose direction A, "Lamplight", from
  three proposals.
  - **Design tokens** for light and dark themes: warm paper colours with amber as the only
    accent.
  - **Shared formatting:** `lib/format.ts` for rupees in lakh/crore and IST times.
  - **The `<Change>` component** pairs colour with an arrow and a sign.
  - **Every screen** has loading, empty and error states.
  - **Phone navigation:** a bottom tab bar plus a More sheet, and dialogs open as bottom
    sheets.
  - **A new `/stock/:symbol` page.** It holds the research summary, parsed from the
    deep-research Markdown sections.
- **Bugs fixed:**
  - red "up" arrows on the Portfolio P&L cards;
  - Portfolio stuck on its skeleton when the first load failed;
  - Home saying "No holdings yet" when the server was unreachable;
  - the inverted Simulation return-curve colours;
  - a red "buy" chip;
  - raw ISO timestamps in History.
- **Results:** frontend vitest 112 passed; build OK. There were no backend changes.

## ADR index

| ADR | Title | Status |
| --- | --- | --- |
| [001](adr.md#adr-001-import-upstream-panwatch-by-merging-its-history-pinned-to-89bdf3f) | Import upstream by merging history, pinned to `89bdf3f` | accepted 2026-09-24 |
| [002](adr.md#adr-002-deterministic-fail-closed-output-guard-in-layers) | Deterministic, fail-closed output guard in layers | accepted 2026-09-24 |
| [003](adr.md#adr-003-research-only-output-contract-for-agents) | Research-only output contract for agents | accepted 2026-09-24 |
| [004](adr.md#adr-004-disposition-of-upstream-recommendation-features-in-research_only) | Disposition of recommendation features | accepted 2026-09-24 |
| [005](adr.md#adr-005-tradingagents-neutralised-by-replacing-the-graph-after-the-debate) | TradingAgents neutralised by replacing the graph | accepted 2026-09-24 |
| [006](adr.md#adr-006-tooling-and-ci-for-the-fork) | Tooling and CI | accepted 2026-09-24 |
| [007](adr.md#adr-007-india-market-data-layer-with-bring-your-own-key-brokers) | India market data with bring-your-own-key brokers | accepted 2026-09-25 |
| [008](adr.md#adr-008-rename-the-product-to-candlewise) | Rename to Candlewise | accepted 2026-09-26 |

PLAN.md §4.3 lists ADR topics planned for later phases, numbered differently: calendar
format, news sourcing, i18n, Postgres, tenant isolation, LLM billing and WhatsApp. None of
them has been written. ADR-008 is used for the rename, not for the credential vault the
plan listed under that number. The vault decision is recorded inside ADR-007.

## Open decisions (PLAN §9)

- **Q7(b):** the LLM billing model.
- **Q8:** sources for GIFT Nifty, FII/DII flows, filings and fundamentals.
- **Q11:** the NSE holiday and special-session circulars.
- **Q-sec:** disclosure of the path traversal to the upstream maintainers.
- **Q-legal:** a legal review of scope, disclaimer, RA obligations and data sourcing.
- **Simulation:** it still uses the A-share cost model and a 100-share lot.
