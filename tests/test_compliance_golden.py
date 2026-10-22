"""Every compliance tool and the prompt must keep answering exactly as the golden files say.

The files were captured before the rate tables moved out of ``compliance.py``; see
``tests/golden_compliance.py`` for the format and how to regenerate them.
"""

from __future__ import annotations

import json

from tests.golden_compliance import (
    GOLDEN_DIR,
    PROMPT,
    TOOLS,
    capture,
    constants_record,
    read_cases,
    read_lines,
)


def test_placeholder_constants_unchanged() -> None:
    pinned = json.loads((GOLDEN_DIR / "constants.json").read_text(encoding="utf-8"))
    assert constants_record() == pinned


def test_grid_covers_every_tool_and_jurisdiction() -> None:
    from taxcode_mcp.tools.compliance import SUPPORTED_STATE_JURISDICTIONS

    cases = read_cases()
    assert set(cases) == set(TOOLS)
    assert sum(len(v) for v in cases.values()) == 25605
    seen = {str(c["jurisdiction"]).upper() for c in cases["compliance_evaluate_state_residency"]}
    assert set(SUPPORTED_STATE_JURISDICTIONS) <= seen
    statuses = {c.get("filing_status") for c in cases["compliance_evaluate_state_residency"]}
    assert {"single", "mfj"} <= statuses


async def test_outputs_match_golden_files() -> None:
    live = await capture(read_cases())
    assert set(live) == {*TOOLS, PROMPT}
    for stem, lines in live.items():
        golden = read_lines(stem)
        assert len(lines) == len(golden), stem
        for got, want in zip(lines, golden, strict=True):
            assert got == want, f"{stem}: {json.loads(want)['args']}"
