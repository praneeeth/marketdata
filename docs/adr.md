# Architecture Decision Records

Each entry records one significant decision for the India fork: context, decision,
consequences. Status is one of `proposed`, `accepted` or `superseded by ADR-NNN`. The plan
and open questions live in [`docs/india-fork/PLAN.md`](india-fork/PLAN.md).

---

## ADR-001: Import upstream PanWatch by merging its history, pinned to `89bdf3f`

- **Status:** accepted (2026-09-24)
- **Context:** The fork repository started empty. Phase 0 analysed upstream
  `TNT-Likely/PanWatch` at `89bdf3f` (2026-09-21). The code has to be brought in with MIT
  attribution intact. The options were merging upstream history or a single snapshot
  commit.
- **Decision:** Fetch upstream and `git merge --allow-unrelated-histories 89bdf3f` into the
  fork. Keep upstream `LICENSE` text and copyright, and add a line for fork modifications.
  Put a fork/attribution header at the top of `README.md`. The import commit contains no
  functional changes.
- **Consequences:**
  - Upstream authorship and history are preserved, so `git blame` works and upstream
    fixes can be cherry-picked.
  - The repository grows by upstream's history, including screenshots.
  - The PR carrying the import must be merged with a merge commit, not squash.
  - Upstream CI workflows arrive with the import and are replaced in Phase 1 (ADR-006).

---

## ADR-002: Deterministic, fail-closed output guard in layers

- **Status:** accepted (2026-09-24)
- **Context:** In `research_only` mode no user-facing output may contain buy/sell/hold
  calls, price targets, entry levels, stop-losses, position sizes or "you should buy/sell"
  language. The brief requires this to be enforced in code, not only through prompts.
  Upstream has more than 20 paths from a model to a user: agents, notifications, the
  assistant (including streaming), history and PDF export, dashboard briefs and share
  cards. Prompts alone cannot hold, because models ignore instructions and users try
  jailbreaks. Q6(a) ruled out an LLM-based neutraliser for Phase 1.
- **Decision:**
  - **Detector** (`src/platform/compliance/detector.py`, `rules.py`, `normalize.py`):
    - The detector is a pure function over normalised text. Normalisation covers NFKC,
      invisible characters, homoglyphs, markdown, spaced-out letters and case.
    - 39 regex rules in eight categories: directives, ratings, targets, stop-losses,
      entry levels, position sizes, timing and return promises. Rules cover English,
      romanised Hindi and the Chinese phrasings upstream prompts produce.
    - A few rules have a sentence-scoped veto window for factual phrasing ("support is
      near 1,480").
    - Text is split into sentences in an abbreviation-aware way, so "Rs. 1,250" does not
      split a sentence.
  - **Guard** (`guard.py`):
    - `guard_text` redacts offending sentences with a visible marker.
    - It blocks the whole output (replacing it with `BLOCKED_MESSAGE`) when any of these
      hold:
      - more than 30% of sentences would be redacted;
      - a finding spans a sentence boundary;
      - redaction leaves a residual finding;
      - the caller disallows redaction (titles, short fields).
    - The guard is idempotent and never raises.
    - `guard_title` falls back to "Research update".
  - **Provenance:**
    - The guard returns `GuardedText`, a `str` subclass that only the guard can construct.
    - Sinks call `ensure_guarded`, which re-guards anything that is not already
      `GuardedText`.
    - Copying or pickling a `GuardedText` yields a plain `str`, so provenance cannot
      survive serialisation and be trusted later.
  - **Layers:**
    1. **AI client.** `FailoverAIClient` guards `chat`, `chat_multi` and
       `chat_with_tools`. `chat_stream` passes tokens through `GuardedTokenStream`, which
       releases text only after a whole sentence has been checked.
    2. **Sinks** (ARCHITECTURE §5.4):
       - `NotifierManager.notify_with_result`, which also appends the disclaimer within
         each channel's text budget.
       - `AnalysisResult.__setattr__`.
       - `save_analysis` plus `sanitize_payload`, which drops recommendation keys and
         buy/sell action values from stored JSON.
       - Assistant message persistence.
       - PDF export.
       - Read APIs, for rows stored before the guard existed.
    3. **Structural** (ADR-004). Recommendation features are switched off, so there is
       nothing to guard.
  - **Assistant input.** A user-input screen flags advice requests and jailbreaks and
    adds a turn-level instruction, but the output guard is what enforces the rule.
  - **Audit and mode:**
    - Every redaction or block is recorded as a `ComplianceEvent`: surface, rule ids, and
      the original text truncated to 20,000 characters. Events are purged after 180 days
      (Q19).
    - `ADVISORY_MODE` is read from the environment only (Q18).
    - An invalid `ra_registered` configuration stops startup.
- **Consequences:**
  - The guard produces false positives on some legitimate phrasing (e.g. quoted brokerage
    targets are blocked by design, Q7a). The adversarial corpus (81 forbidden, 50 allowed)
    and hypothesis properties pin the trade-off, and new phrasings are added as tests.
  - Regexes cannot understand meaning. Advice worded to avoid every rule can pass. The
    layers, the research-only prompts (ADR-003) and structural removal (ADR-004) reduce
    this. Real-model red-teaming is still needed (not possible in the sandbox).
  - Streaming shows text a sentence at a time rather than a token at a time.
  - `recommendations_publishable` is always `False` in Phase 1. `ra_registered` therefore
    behaves like `research_only` until the Phase 5 review queue exists.

---

## ADR-003: Research-only output contract for agents

- **Status:** accepted (2026-09-24)
- **Context:** Upstream agent prompts are Chinese and ask the model for an action per
  stock (`action`, `action_label`, `suggestions`). The TradingAgents PM decision is also a
  rating. Guarding free text afterwards would mean redacting most of every report. The
  brief replaces the decision with a neutral "Research summary": bull case, bear case, key
  risks, factual support/resistance levels, upcoming events, and sources with timestamps.
- **Decision:**
  - **Shared rules.** Every agent prompt includes the same rules block
    (`prompts/_research_rules.txt`, loaded by `load_research_prompt`). It forbids ratings,
    targets, entries, stops, sizes and timing, and asks for sources.
  - **Report agents.** `daily_report`, `premarket_outlook` and `news_digest` return JSON
    `{"research": [...]}`. Each item carries these fields:
    - `symbol` and `summary`;
    - `bull_points`, `bear_points` and `key_risks`;
    - `technical_levels.support` and `technical_levels.resistance`, which are numbers
      only;
    - `upcoming_events`;
    - `sources`, each with `title`, `url` and `published_at`.
  - **Parsing.** `parse_research_items`:
    - validates types and drops symbols outside the watchlist;
    - keeps only positive numeric levels and sources that have a title;
    - guards every string.

    The parsed list is stored in `raw_data["research"]`.
  - **Intraday monitor.** Returns `{"notable", "headline", "observations",
    "technical_levels", "key_risks"}` and is rendered by `render_intraday_observation`.
    The intraday scan API returns `observation` instead of `suggestion`.
  - **Chart analyst and assistant.** Both use the same rules in English.
  - **Prompt language.** All rewritten prompts are English (Q6b); the remaining UI copy
    is Phase 4.
- **Consequences:**
  - The report agents no longer produce `suggestions`. Every UI element built on them is
    switched off (ADR-004).
  - Support and resistance levels are shown as facts derived from price history. They are
    not labelled as entries or stops, and the guard still checks the surrounding text.
  - The contract is exercised with fixtures and a stub model only. How reliably real
    models follow it is unverified in the sandbox.

---

## ADR-004: Disposition of upstream recommendation features in research_only

- **Status:** accepted (2026-09-24)
- **Context:** Upstream ships recommendation machinery beyond LLM text: a suggestion
  pool with feedback, prediction tracking and evaluations (hit rates), entry candidates
  and strategy signals with scores, an add-position calculator, AI-driven paper trading,
  share cards, and a K-line scorer that computes buy/sell/hold labels in the browser.
  Guarding text does not help when the output is a structured action or a number.
- **Decision:**
  - **Feature gates.** Each surface is a `Feature` in `src/platform/compliance/features.py`.
    Each is enabled only when `recommendations_publishable`, which is never true in
    Phase 1.
  - **Backend.**
    - Routers are not mounted: suggestions, feedback, evaluations, recommendations and
      factors.
    - Routes return HTTP 403 with an `ADVISORY_MODE_RESTRICTED:` message:
      add-position evaluation, AI paper-trading controls and TradingAgents history
      comparison.
    - Writers become no-ops: `save_suggestion`, prediction saves, opportunity jobs and the
      paper-trading scheduler.
    - The dashboard overview returns no opportunities, risk calls or win rates.
  - **Frontend.**
    - `ComplianceProvider` reads `/api/compliance/status` and fails closed until it
      answers.
    - Nav entries and routes for Opportunities and Evaluations are hidden.
    - Action badges are hidden, both AI and K-line; only the neutral "indicators" badge
      remains.
    - Also hidden: the K-line scoring rules, the add-position calculator, dashboard
      opportunities, deep-research ratings and history-vs-returns tables.
    - Share cards are replaced by a notice.
  - **Paper trading** (Q3):
    - The simulation account and its history stay, labelled "Simulation" with a notice.
    - AI trade entry, scans, plans and notifications are gated.
  - **No order execution.** An AST test (`test_no_execution_paths.py`) fails the build if
    order-placement calls or broker order endpoints appear in `src/`.
  - **MCP server** (Q13): mounted only when `MCP_ENABLED=true`.
- **Consequences:**
  - The code for these features stays in the tree, dormant, so `ra_registered` can revive
    it behind the Phase 5 review queue. Dormant code is a regression risk. The gate tests
    (`test_feature_gates.py` and the vitest compliance suite) are the safeguard.
  - Some upstream pages lose content in research-only mode (Stocks badges, the insight
    modal's suggestions tab). Phase 4 revisits the UX.

---

## ADR-005: TradingAgents neutralised by replacing the graph after the debate

- **Status:** accepted (2026-09-24)
- **Context:** TradingAgents (v0.5.0, LangGraph) runs analysts, a bull/bear debate, a
  research manager, a trader, a risk debate and a portfolio manager. The last three
  produce the trade plan, sizing and rating. Q14 preferred overriding graph setup to
  discarding their output afterwards, since discarded text would still be generated,
  billed, and could leak through logs.
- **Decision:**
  - When `tradingagents_rating` is disabled, `install_research_only_workflow` builds a
    new `StateGraph`. It keeps the configured analysts and the bull/bear debate, then
    ends with a neutral summariser (`create_research_summarizer`) registered under the
    research-manager node name.
  - The trader, risk and portfolio-manager nodes are never added (`FORBIDDEN_NODES`,
    asserted in tests).
  - The summariser prompt includes the research rules. It writes the summary to
    `final_trade_decision`, the field upstream reads, and blanks the plan fields.
  - `map_state_to_research_result` stores only `mode`, `research_summary`, `cost_usd`,
    `analyst_reports` and `debate_history`, all guarded.
  - PDF export and the UI use a research layout. They never default to "hold" when no
    decision exists.
- **Consequences:**
  - Runs are cheaper, because the three decision stages are skipped.
  - The override depends on TradingAgents internals: `GraphSetup` attributes, node names
    and `ConditionalLogic`. A version bump must re-run `test_research_only_graph_has_no_decision_nodes`.
  - Keep versus replace for TradingAgents is re-evaluated in Phase 4.

---

## ADR-006: Tooling and CI for the fork

- **Status:** accepted (2026-09-24)
- **Context:** Upstream has no linter, type checker or coverage gate. Its CI builds and
  publishes Docker images (`release.yml`) and runs a third-party agent workflow
  (`pullfrog.yml`). The brief requires ruff, mypy strict, pytest, 90% coverage on new
  code and property-based tests. Q16 approved the dev dependencies and a PR workflow.
- **Decision:**
  - **Dev dependencies.** `requirements-dev.txt` pins `ruff`, `mypy`, `pytest-cov` and
    `hypothesis`.
  - **Scope.** Ruff and mypy (strict) apply to new or rewritten code through explicit
    include lists in `pyproject.toml`. Upstream files join the list as they are rewritten.
  - **Coverage.** Measured on the same new-code globs, with `fail_under = 90`.
  - **CI.** `.github/workflows/ci.yml` runs on every pull request:
    - backend: ruff, ruff format check, mypy, pytest with coverage, and the
      `packages/` test suites;
    - frontend: pnpm install, vitest, and `pnpm build` (`tsc -b` plus the Vite build).

    `release.yml` and `pullfrog.yml` are removed.
- **Consequences:**
  - Upstream code outside the include lists is not linted or type-checked yet.
  - The frontend has no lint step; adding ESLint needs approval.
  - Node 24 (upstream's pinned engine) and Docker builds were not exercised in the
    sandbox. CI is the first run on Node 24.

---

## ADR-007: India market data layer with bring-your-own-key brokers

- **Status:** accepted (2026-09-25)
- **Context:** Upstream fetches data by scraping Chinese sites, which is unauthenticated
  and shared across users. The India fork uses the user's own broker account (Kite,
  Upstox, Angel One), with no central redistribution (Q9, Q10). Broker APIs need a
  per-user login, their sessions expire every day, and each broker spells instruments
  differently.
- **Decision:**
  - **A new `marketdata.india` subpackage** sits beside the upstream vendors rather than
    retrofitting them. It provides typed data (`Decimal` prices, timezone-aware
    timestamps) and a read-only `MarketDataProvider` protocol with no order methods.
  - **Adapters call REST over `httpx`, with no broker SDKs.** A shared transport handles
    per-credential throttling, retries and typed errors (`SessionExpired`, `RateLimited`,
    `ProviderUnavailable`, `BadResponse`, `NotSupported`, `InstrumentNotResolved`).
    Exceptions never carry headers, bodies or credentials.
  - **Sessions.** `ProviderSession` and `Secret` never render or pickle a secret.
  - **Contract suite.** One suite runs every adapter against fixtures written by hand
    from each provider's docs; they are not recordings of live traffic.
  - **Cross-provider identity.** Derivatives are keyed by their contract terms
    (`NFO:NIFTY:2026-10-27:25000:CE`), because brokers spell symbols differently.
  - **Option chains.** For Kite and Angel they are built from the instrument master plus
    quotes; Upstox has a native endpoint.
  - **`IndiaMarketData` service.** It fails over only among one user's sessions and keys
    every cache entry, including instrument masters, by credential ID. Expired sessions
    are skipped and reported through `needs_reconnect`.
  - **Credentials at rest.** `CredentialVault` uses AES-256-GCM with key IDs for
    rotation. The associated data binds each value to `user|connection|field`.
    `broker_connections` stores only vault tokens plus a masked hint.
  - **Logins.** Kite (request token) and Upstox (OAuth code) use single-use, 10-minute
    state tokens on public callbacks. Angel One takes a TOTP typed at login, and no seed
    is stored.
  - **yfinance** is development-only. It is enabled only by `ALLOW_UNOFFICIAL_DATA=true`
    plus an explicitly non-production `APP_ENV`, and everything it returns is tagged
    `UNOFFICIAL_DELAYED`.
- **Consequences:**
  - None of the adapters has been exercised against live broker APIs. Endpoints, field
    names, limits and token expiry times are marked *(verify)* in code.
  - Each user pays for their own broker data plan (Kite's is paid).
  - A Kite or Upstox session lasts one day, so features must degrade and prompt
    "reconnect".
  - The upstream CN/HK/US vendors stay until the removal step. Host features still read
    from them until they are switched to `IndiaMarketData`.
