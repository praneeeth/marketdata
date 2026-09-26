# Candlewise documentation

Candlewise is a research-only AI assistant for Indian stocks (NSE/BSE), forked from
[PanWatch](https://github.com/TNT-Likely/PanWatch) (MIT).

These docs are based on the code, the git history, the ADRs and `india-fork/*`. Anything
not confirmed is marked **UNVERIFIED**.

**Last updated:** 2026-09-27, branch `docs/project-documentation`, stacked on
`feat/ui-lamplight`.

## Start here

| Document | For | What's in it |
| --- | --- | --- |
| [OVERVIEW.md](OVERVIEW.md) | everyone | What Candlewise is, who it's for, the research-only positioning, and fork attribution |
| [USER-GUIDE.md](USER-GUIDE.md) | end users | Signing in, connecting a broker, watchlist, reading a research summary, alerts, simulation |
| [SETUP.md](SETUP.md) | developers and self-hosters | A fresh-machine setup: every `.env` variable, broker keys, the LLM key, Telegram, WhatsApp (not built), tests, Docker |
| [DEPLOYMENT.md](DEPLOYMENT.md) | operators | Hosting options, database, HTTPS, secrets, backups, monitoring, estimated costs |

## Project state

| Document | What's in it |
| --- | --- |
| [STATUS.md](STATUS.md) | Every feature (Done / Partial / Not started / Built but unverified), test counts, coverage, known bugs and TODOs |
| [BUILD-HISTORY.md](BUILD-HISTORY.md) | What was built in Phases 0–6, the rename and the UI redesign, with PRs, branches, decisions and ADRs |
| [GO-LIVE-CHECKLIST.md](GO-LIVE-CHECKLIST.md) | Everything still needed before real users, by priority, with owners and verification |

## Design and compliance

| Document | What's in it |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | The current architecture, with a Mermaid diagram: backend, frontend, agents and schedules, market-data adapters, compliance guard, (lack of) multi-tenancy, notifications, database |
| [COMPLIANCE.md](COMPLIANCE.md) | `ADVISORY_MODE`, the output guard, disclaimers, DPDP, broker terms, and what needs an RA or a lawyer. **Not legal advice** |
| [adr.md](adr.md) | Architecture decision records ADR-001 to ADR-008 |
| [india-fork/PLAN.md](india-fork/PLAN.md) | The original phased plan (Phases 1–6), risk register, open questions, phase reports and decision log |
| [india-fork/ARCHITECTURE.md](india-fork/ARCHITECTURE.md) | Phase 0 analysis of upstream PanWatch and its China-specific assumptions |

## Elsewhere in the repository

| File | What's in it |
| --- | --- |
| [`../README.md`](../README.md) | The project README |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | Writing agents and data providers |
| [`../AGENTS.md`](../AGENTS.md) | Rules for contributors and AI agents |
| [`../src/ARCHITECTURE.md`](../src/ARCHITECTURE.md) | Backend module boundaries |
| [`../packages/marketdata/README.md`](../packages/marketdata/README.md) | The India market-data package |
| [`../packages/pan-agent-runtime/README.md`](../packages/pan-agent-runtime/README.md) | The agent runtime used by the assistant |
