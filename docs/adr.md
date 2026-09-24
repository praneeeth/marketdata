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
