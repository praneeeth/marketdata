# Compliance

> **This is not legal advice.** It describes what the code does and lists questions for
> qualified professionals. Nothing in Candlewise has had a legal review (PLAN.md §9,
> Q-legal: **open**). Regulatory statements come from the working notes in PLAN.md, which
> marks them *(verify)*. They are **UNVERIFIED** until a SEBI/DPDP lawyer confirms them.

## 1. `ADVISORY_MODE`

`ADVISORY_MODE` is set from the environment only; there's no UI toggle (Q18, ADR-002). It's
read by `src/platform/compliance/settings.py`.

| Mode | What it does today |
| --- | --- |
| `research_only` (default) | Research and education only. The output guard is on, recommendation features are off, and disclaimers are everywhere |
| `ra_registered` | Requires `RA_REGISTRATION_NUMBER` (`INH` + 9 digits) and `RA_NAME`, or **startup fails**. `recommendations_publishable` is still always `False`, so it **behaves exactly like `research_only`** until the analyst review queue (Phase 5) exists. The `ra_review_items` table exists but nothing writes to it |

An invalid mode value also stops startup (`ComplianceConfigError`). That's intentional:
the app fails closed.

## 2. Output guard

Design: [ADR-002](adr.md#adr-002-deterministic-fail-closed-output-guard-in-layers).
Code: `src/platform/compliance/`.

### Blocked categories

- directives ("buy", "sell", "accumulate", "exit");
- ratings;
- price targets;
- stop-losses;
- entry levels;
- position sizes and tranches;
- timing calls;
- return promises.

**Languages covered:** English, romanised Hindi, and the Chinese phrasings the upstream
prompts produced. English decision labels ("Final trade decision: Buy", "Infosys: Buy")
were added on 2026-09-26.

### How it decides

- **Normalisation** runs first: NFKC, invisible characters, homoglyphs, Markdown,
  spaced-out letters.
- **Redaction:** offending sentences are replaced with a visible marker.
- **Blocking:** the whole output is replaced when any of these holds:
  - more than 30% of sentences would be redacted;
  - a finding spans a sentence boundary;
  - a finding survives redaction;
  - the field can't be redacted (titles, short fields).
- **Provenance:** only the guard can create `GuardedText`. Sinks re-guard anything that
  isn't `GuardedText`.

### Where it runs

1. **In the AI client.** Every call, including streaming, which releases text only after
   a whole sentence has been checked.
2. **At every sink:**
   - notifications (`NotifierManager.notify_with_result`, which is the only notification
     path);
   - saved analyses, where recommendation keys are also stripped from the stored JSON;
   - assistant messages;
   - PDF export;
   - read APIs, for rows stored before the guard existed.
3. **Structurally** ([ADR-004](adr.md#adr-004-disposition-of-upstream-recommendation-features-in-research_only)).
   These features are off:
   - suggestion pool, prediction tracking and evaluations;
   - entry candidates and strategy signals;
   - the position calculator;
   - AI paper trading;
   - share cards;
   - the TradingAgents rating;
   - MCP.

   Their routers aren't mounted, or return 403 `ADVISORY_MODE_RESTRICTED`. The UI hides
   them, and `ComplianceProvider` fails closed.

### Around the guard

- **Research-only output contract**
  ([ADR-003](adr.md#adr-003-research-only-output-contract-for-agents)): agents return
  bull points, bear points, key risks, numeric levels, events and sources. They never
  return actions.
- **TradingAgents**
  ([ADR-005](adr.md#adr-005-tradingagents-neutralised-by-replacing-the-graph-after-the-debate)):
  the trader, risk and portfolio-manager nodes are never built. A neutral summariser ends
  the graph.
- **No order execution.** Broker adapters are read-only, and
  `tests/compliance/test_no_execution_paths.py` fails on order-placement code.
- **Assistant input screening:** requests for advice and jailbreak attempts are flagged,
  but the output guard is what enforces the rule.

### Audit

Every redaction or block writes a `ComplianceEvent`: surface, rule ids, and the original
text truncated to 20,000 characters. Events are purged after 180 days by the maintenance
job (Q19).

### Limits (ADR-002)

- Regexes don't understand meaning. Advice worded to avoid every rule can get through.
- **The guard has never been red-teamed against real models**, only against stubs and an
  adversarial corpus.
- There are known false positives; for example, quoted brokerage targets are blocked by
  design (Q7a).

## 3. Disclaimers

| Where | How |
| --- | --- |
| Every page | Footer note (`DisclaimerFooter`); the text comes from `/api/compliance/status` |
| First use | A blocking consent dialog. The user must tick "I understand…". The acknowledged version is stored on the server; a new version asks again |
| Onboarding | The disclaimer shown as a note |
| Every research summary | `DisclaimerNote`, always rendered, including in empty and error states |
| Research report page, PDF export | The disclaimer is included |
| Every notification | Appended by the notifier after the content is cut to the channel's budget, so it's never truncated |
| Simulation | The "Simulation" label and notice: "no real orders, no money at risk" |

The disclaimer text itself hasn't had a legal review.

## 4. Data and privacy (DPDP Act)

**What the app stores** (single-user; everything is in the owner's SQLite database):

- the login;
- the broker API keys and session tokens, encrypted with AES-256-GCM;
- holdings, watchlist, alerts, chats and analyses;
- logs, including the `trace_id`;
- compliance events, which contain AI text;
- consent records.

**What leaves the machine:**

- Prompts go to the configured LLM provider. They contain your holdings, watchlist and
  market data.
- Notifications go to Telegram, Discord or Pushover.
- Broker API calls.
- Yahoo requests for global cues.

**Implemented:**

- credential encryption and masking;
- secrets never logged or returned in plain text (Phase 1 fix);
- retention jobs for logs, context, compliance events and MCP logs;
- versioned disclaimer consent.

**Not implemented, all planned for Phase 5:**

- a DPDP consent notice at signup, with its purpose;
- self-serve data export;
- account deletion;
- a grievance contact;
- a breach runbook;
- multi-user separation.

**Status of the rules.** The DPDP Act 2023 applies once others' personal data is processed.
PLAN.md notes the DPDP Rules were notified in 2025 with phased commencement; which duties
apply at launch is **UNVERIFIED**, a question for a lawyer.

**Two practical points:**

- **Self-hosting.** When one person runs Candlewise for themselves, they are the only data
  subject.
- **Hosting for others** makes you a data fiduciary, and Phase 5 then becomes a
  prerequisite ([GO-LIVE-CHECKLIST.md](GO-LIVE-CHECKLIST.md)).

## 5. Broker and data-source terms to check

All of these are **UNVERIFIED** and need reading against the current documents.

| Source | Question |
| --- | --- |
| Zerodha Kite Connect | Does the API agreement allow using data inside a third-party hosted app, even when each user brings their own key? Is redistribution or display prohibited? Is the market-data add-on per user? |
| Upstox | The same questions for the Upstox developer API terms |
| Angel One SmartAPI | The same, plus the rules on TOTP-based login automation. Candlewise never stores the TOTP secret (Q10) |
| Yahoo (global cues, dev source) | The terms likely don't allow commercial display or redistribution. The plan says to switch to a licensed feed before any public launch (Q8) |
| NSE/BSE data and websites | Exchange data licensing and automated-access terms, needed before any filings or news work (Phase 4) |
| News publishers | Plan: headline, link and short snippet only; no full-text scraping |
| LLM provider | Data use and retention terms for prompts that contain user portfolios |

**Design choices already made to lower risk:**

- bring-your-own-key, with per-credential caches, so one user's data is never served to
  another (ADR-007, Q9);
- instrument masters cached per user;
- delayed and unofficial data labelled as such.

## 6. What needs a SEBI-registered RA or a lawyer

These are questions, not conclusions:

1. **Is a stock-specific AI "research summary" a research report?** The summary has a bull
   case, bear case, risks and support/resistance levels. Could it count as a research
   report or as advice under the SEBI (Research Analysts) Regulations, even without
   buy/sell words? This decides whether a hosted service needs RA registration. **This is
   the key question.**
2. **Showing support and resistance levels.** Is it acceptable to show computed levels next
   to a stock in research-only mode?
3. **Wording.** Are the disclaimer, the "research only" positioning and the consent
   wording adequate?
4. **The `ra_registered` mode:** the review workflow, record keeping, AI-use disclosure,
   and the RA identity shown on content (PLAN.md §5, Phase 5, *(verify)*).
5. **Paid tiers.** Would a paid plan change the analysis?
6. **Communications rules.** Any rules on unregistered entities discussing specific
   securities, and on who may promote the product.
7. **DPDP obligations** at launch, and cross-border transfer of prompts to foreign LLM
   providers.
8. **Broker and exchange data licences** (section 5).
9. **WhatsApp/Meta policy** on financial content (Phase 6).
10. **Trademark clearance** for "Candlewise" ([GO-LIVE-CHECKLIST.md](GO-LIVE-CHECKLIST.md)).

Until these are answered, the safe operating mode is the one the code enforces:
**research-only, self-hosted, for the operator's own use.**
