"""Agent process evaluation set (golden set + rule assertions + LLM-as-judge framework).

Complements outcome evaluation (factor_eval and friends, which "judge results"); this "judges the process":
tool choice / arguments / grounding / parseable structured output / the action allowlist.

- pure rule cases (structured_output parsing) run with make test;
- chat tool-loop cases that need a real model run through make eval (config in run_eval.py).
"""
