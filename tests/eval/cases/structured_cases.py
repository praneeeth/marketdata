"""structured_output parsing golden set (pure rules, no model needed; runs with make test).

Reuses and extends the existing cases in tests/test_structured_output.py:
fence/prefix tolerance, alias normalisation, action allowlist rejection, and tag block extraction/stripping edge cases.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.modules.research.signals.structured_output import (
    TAG_END,
    TAG_START,
    strip_tagged_json,
    try_extract_tagged_json,
    try_parse_action_json,
)


@dataclass
class StructuredEvalCase:
    """One structured output parsing case.

    kind:
    - action: try_parse_action_json (JSON-only output)
    - tagged: try_extract_tagged_json (a tag block at the end of long text)
    - strip:  strip_tagged_json (the body left after stripping the tag block)
    """

    id: str
    text: str
    kind: str = "action"
    expect_parsed: bool = True
    # Field assertions: plain values are compared for equality; callables are used as predicates
    expect_fields: dict = field(default_factory=dict)
    # The expected body for kind=strip
    expect_stripped: str | None = None
    notes: str = ""


def check_structured_case(case: StructuredEvalCase) -> list[str]:
    """Run one case and return the failure reasons (empty = pass)."""
    failures: list[str] = []

    if case.kind == "strip":
        actual = strip_tagged_json(case.text)
        if case.expect_stripped is not None and actual != case.expect_stripped:
            failures.append(f"Stripped result doesn't match: expected {case.expect_stripped!r}, got {actual!r}")
        return failures

    if case.kind == "tagged":
        obj = try_extract_tagged_json(case.text)
    else:
        obj = try_parse_action_json(case.text)

    if case.expect_parsed and obj is None:
        failures.append("Expected a successful parse, got None")
        return failures
    if not case.expect_parsed:
        if obj is not None:
            failures.append(f"Expected a failed parse (None), got {obj!r}")
        return failures

    for key, expected in case.expect_fields.items():
        actual = obj.get(key)
        if callable(expected):
            if not expected(actual):
                failures.append(f"Field {key} check failed: got {actual!r}")
        elif actual != expected:
            failures.append(f"Field {key} doesn't match: expected {expected!r}, got {actual!r}")
    return failures


STRUCTURED_CASES: list[StructuredEvalCase] = [
    # ──────── try_parse_action_json ────────
    StructuredEvalCase(
        id="s-json-prefix",
        text='\njson\n{"action":"add","action_label":"Open position","reason":"breakout"}\n',
        expect_fields={"action": "add", "action_label": "Open position"},
        notes="Tolerates a bare json prefix line",
    ),
    StructuredEvalCase(
        id="s-fenced-json",
        text='```json\n{"action":"reduce","action_label":"Reduce"}\n```',
        expect_fields={"action": "reduce"},
        notes="Tolerates a ```json code fence",
    ),
    StructuredEvalCase(
        id="s-fenced-nolang",
        text='```\n{"action":"hold","confidence":0.7}\n```',
        expect_fields={"action": "hold", "confidence": 0.7},
        notes="A code fence without a language tag",
    ),
    StructuredEvalCase(
        id="s-alias-build",
        text='{"action":"build","action_label":"Open position"}',
        expect_fields={"action": "add"},
        notes="The build alias normalises to add",
    ),
    StructuredEvalCase(
        id="s-action-upper",
        text='{"action":"ADD","action_label":"Open position"}',
        expect_fields={"action": lambda v: str(v).lower() == "add"},
        notes="An upper-case action passes the allowlist (original case kept)",
    ),
    StructuredEvalCase(
        id="s-illegal-action",
        text='{"action":"yolo","reason":"all in"}',
        expect_parsed=False,
        notes="Actions outside the allowlist must be rejected",
    ),
    StructuredEvalCase(
        id="s-json-array",
        text='[{"action":"add"}]',
        expect_parsed=False,
        notes="A non-dict (array) must be rejected",
    ),
    StructuredEvalCase(
        id="s-empty",
        text="",
        expect_parsed=False,
        notes="Empty input returns None",
    ),
    StructuredEvalCase(
        id="s-broken-json",
        text='{"action":"add",',
        expect_parsed=False,
        notes="Truncated JSON returns None instead of raising",
    ),
    StructuredEvalCase(
        id="s-no-action-field",
        text='{"signal":"volume_spike","note":"volume up"}',
        expect_fields={"signal": "volume_spike"},
        notes="Valid JSON without an action field passes (an empty action isn't checked against the allowlist)",
    ),
    StructuredEvalCase(
        id="s-prose-not-json",
        text="The market was choppy today; best to wait and watch.",
        expect_parsed=False,
        notes="Plain natural language returns None",
    ),
    # ──────── try_extract_tagged_json ────────
    StructuredEvalCase(
        id="t-tagged-ok",
        text=f'The analysis text comes first.\n{TAG_START}\n{{"action":"watch","score":72}}\n{TAG_END}',
        kind="tagged",
        expect_fields={"action": "watch", "score": 72},
        notes="Extracts the tag block at the end of long text",
    ),
    StructuredEvalCase(
        id="t-tagged-take-last",
        text=(
            f'{TAG_START}\n{{"v":1}}\n{TAG_END}\nbody in between\n'
            f'{TAG_START}\n{{"v":2}}\n{TAG_END}'
        ),
        kind="tagged",
        expect_fields={"v": 2},
        notes="With several tag blocks, the last one wins (rfind)",
    ),
    StructuredEvalCase(
        id="t-tagged-missing-end",
        text=f'body\n{TAG_START}\n{{"v":1}}',
        kind="tagged",
        expect_parsed=False,
        notes="A missing end tag returns None",
    ),
    StructuredEvalCase(
        id="t-tagged-empty-payload",
        text=f"body\n{TAG_START}\n{TAG_END}",
        kind="tagged",
        expect_parsed=False,
        notes="An empty payload returns None",
    ),
    StructuredEvalCase(
        id="t-tagged-broken-payload",
        text=f"body\n{TAG_START}\n{{bad json}}\n{TAG_END}",
        kind="tagged",
        expect_parsed=False,
        notes="Invalid JSON inside the tags returns None",
    ),
    # ──────── strip_tagged_json ────────
    StructuredEvalCase(
        id="strip-ok",
        text=f'Conclusion text.\n{TAG_START}\n{{"action":"hold"}}\n{TAG_END}',
        kind="strip",
        expect_stripped="Conclusion text.",
        notes="Stripping the tag block leaves only the body",
    ),
    StructuredEvalCase(
        id="strip-no-tag",
        text="body text without a tag block",
        kind="strip",
        expect_stripped="body text without a tag block",
        notes="Returned unchanged without a tag block",
    ),
]
