"""Full regression of the structured_output parsing golden set (pure rules; runs with make test)."""

import pytest

from tests.eval.cases.structured_cases import STRUCTURED_CASES, check_structured_case


@pytest.mark.parametrize("case", STRUCTURED_CASES, ids=[c.id for c in STRUCTURED_CASES])
def test_structured_golden_case(case):
    """Structured output parsing golden set, case by case."""
    failures = check_structured_case(case)
    assert failures == [], f"[{case.id}] {case.notes}: {failures}"
