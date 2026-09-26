# Agent and contributor conventions (India fork)

This repository is **Candlewise**, an India-focused fork of
[PanWatch](https://github.com/TNT-Likely/PanWatch) by TNT-Likely (MIT). Keep that
attribution in `LICENSE` and the README. Product name, tagline and links live in
`src/platform/branding.py` and `frontend/src/lib/brand.ts`. Read `docs/india-fork/PLAN.md`
and `docs/adr.md` before changing behaviour.

## Non-negotiable rules

- `ADVISORY_MODE=research_only` is the default. No user-facing output may contain
  buy/sell/hold calls, ratings, price targets, stop-losses, entry levels or position
  sizes. Every AI output path must go through `src/platform/compliance` (guard,
  disclaimer, feature gates). Never bypass `NotifierManager.notify_with_result`.
- No order execution. Broker integrations are read-only; `tests/compliance/` fails on
  order-placement code.
- Credentials are masked in API responses and never logged.
- Ask the repository owner before adding a dependency or changing CI.

## Commits and pull requests

- Conventional Commits in English: `<type>(<scope>): <subject>`, e.g.
  `feat(compliance): add output guard`. Common types: feat, fix, refactor, perf, test,
  docs, chore, build, ci.
- One phase per pull request; one concern per commit.
- PR description: what changed, test/lint results, what could not be verified in the
  sandbox, open questions.
- Merge pull requests that contain imported upstream history with a merge commit, not
  squash.

## Checks to run before pushing

```bash
pip install -r requirements.txt -r requirements-dev.txt
ruff check . && ruff format --check .   # new code only (see pyproject.toml)
mypy                                     # --strict, new code only
python -m pytest tests/ -q --cov         # 90% coverage gate on new code
(cd frontend && pnpm exec vitest run && pnpm build)
git diff --check
```
