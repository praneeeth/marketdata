# Candlewise: overview

> **Read the market. Decide for yourself.**

Candlewise is a self-hosted AI research assistant for Indian stocks listed on NSE and BSE.
It shows your holdings and watchlist with prices from your own broker account. It explains
what the data and news say about a stock, summarises it as a bull case, bear case, risks,
levels, events and sources, and notifies you about price moves you asked to hear about.

It never tells you what to buy or sell.

## Who it is for

- **Indian retail investors** who hold or follow NSE/BSE stocks and want a structured,
  sourced summary of each stock without being told what to do.
- **Self-hosters.** Today each installation serves **one person**: one login, one
  database, the owner's own broker keys. Multi-user hosting is planned (Phase 5) but not
  built. See [STATUS.md](STATUS.md).
- **A SEBI-registered research analyst (RA)**, eventually. There is an `ra_registered` mode
  in the code, but it currently behaves exactly like research-only mode. The analyst review
  queue it depends on isn't built.

## Research-only positioning

Research-only is the default (`ADVISORY_MODE=research_only`), and it's enforced in code, not
only in prompts:

- **An output guard** checks every AI output path. It redacts or blocks buy/sell/hold
  calls, ratings, price targets, entry levels, stop-losses, position sizes, timing advice
  and return promises. Each redaction is recorded for audit.
- **Recommendation features are switched off.** These came from upstream: a suggestion pool,
  entry candidates, strategy signals, evaluations and hit rates, a position calculator,
  AI-driven paper trading, and share cards. Their API routes aren't mounted or return 403,
  and the UI hides them.
- **No order execution.** Broker integrations are read-only, and a test fails the build if
  order-placement code appears.
- **Disclaimers are always visible:** in the footer of every page, on every research
  summary, in onboarding, and appended to every notification. Before first use, the user
  must acknowledge a versioned disclaimer, which is stored on the server.

Details are in [COMPLIANCE.md](COMPLIANCE.md). **That document is not legal advice, and
nothing in this project has had a legal review yet.**

## What it does today

| Area | What you get |
| --- | --- |
| Portfolio | Accounts, positions and a watchlist, with P&L in rupees (lakh/crore) |
| Market data | NSE/BSE quotes and daily candles from your own Zerodha Kite, Upstox or Angel One API connection. A delayed Yahoo source exists for development only |
| Stock page | Price, key figures, a research summary, a chart, a descriptive technical snapshot and news |
| Deep research | A multi-agent run (TradingAgents: analysts plus a bull/bear debate), ending in a neutral research summary |
| Agents | Pre-market outlook, intraday monitor, daily close report and a news digest capability, scheduled in IST |
| Assistant | A chat assistant with tools over your data, guarded like every other AI output |
| Alerts | Price, change %, turnover and volume-ratio rules, checked every minute |
| Global cues | World indices, crude, gold and USD/INR for context (free, delayed, labelled "Delayed / unofficial") |
| Simulation | The inherited paper-trading account, always labelled "Simulation". In research-only mode no new AI trades are opened |
| Notifications | Telegram, Discord and Pushover |

What is missing or unverified is listed in [STATUS.md](STATUS.md). The most important
gaps: no Indian news or filings feed, no NSE holiday calendar, a single-user
architecture, broker adapters tested only against documentation-derived fixtures, and no
legal review.

## Fork attribution

Candlewise is a fork of [PanWatch](https://github.com/TNT-Likely/PanWatch) by TNT-Likely
(copyright holder: sunxiao0721), used under the MIT License.

- **How upstream was brought in:** its history was merged at commit `89bdf3f`
  ([ADR-001](adr.md)), so authorship and `git blame` are preserved.
- **Where the attribution appears:** in [`LICENSE`](../LICENSE), the
  [README](../README.md), [`AGENTS.md`](../AGENTS.md) and the in-app Settings footer.
- **What the fork changed:** it removed PanWatch's China-specific markets, data vendors
  and notification channels. It then added the compliance guard, the India market data
  layer, an English-only UI and backend, the Candlewise name and a new UI design
  ([BUILD-HISTORY.md](BUILD-HISTORY.md)).

## Where to go next

- **To run it:** [SETUP.md](SETUP.md).
- **To use it:** [USER-GUIDE.md](USER-GUIDE.md).
- **Before letting anyone else use it:** [GO-LIVE-CHECKLIST.md](GO-LIVE-CHECKLIST.md).
