"""Unknown filing statuses are refused. Known aliases keep today's mapping."""

from __future__ import annotations

import pytest
from fastmcp import Client

from taxcode_mcp.errors import SafetyViolationError
from taxcode_mcp.server import create_server
from taxcode_mcp.tools import compliance

# Pinned independently of the tuple in compliance.py, so a wording change fails here.
_ACCEPTED = (
    "single, mfj, married_joint, married filing jointly, hoh, head of household, "
    "mfs, married separate, married filing separately, qualifying widow, "
    "qualifying surviving spouse, surviving spouse"
)


def _refusal(filing_status: str) -> str:
    return f"Unknown filing status {filing_status!r}. Accepted values: {_ACCEPTED}."


@pytest.mark.parametrize(
    ("filing_status", "key"),
    [
        ("single", "single"),
        ("SINGLE", "single"),
        ("  Single  ", "single"),
        ("", "single"),
        ("   ", "single"),
        ("mfj", "mfj"),
        ("MFJ", "mfj"),
        ("  MFJ ", "mfj"),
        ("married_joint", "mfj"),
        ("Married_Joint", "mfj"),
        ("married filing jointly", "mfj"),
        ("  Married Filing Jointly  ", "mfj"),
        ("hoh", "hoh"),
        ("HOH", "hoh"),
        ("head of household", "hoh"),
        ("  Head   Of   Household  ", "hoh"),
        ("head_of_household", "hoh"),
        ("head-of-household", "hoh"),
        ("mfs", "mfs"),
        ("MFS", "mfs"),
        ("married separate", "mfs"),
        ("married_separate", "mfs"),
        ("married-separate", "mfs"),
        ("married filing separately", "mfs"),
        ("married_filing_separately", "mfs"),
        ("married-filing-separately", "mfs"),
        ("  Married   Filing   Separately  ", "mfs"),
        ("qualifying widow", "single"),
        ("qualifying_widow", "single"),
        ("qualifying-widow", "single"),
        ("Qualifying Widow", "single"),
        ("qualifying surviving spouse", "single"),
        ("qualifying_surviving_spouse", "single"),
        ("surviving spouse", "single"),
        ("surviving_spouse", "single"),
        ("  Surviving   Spouse  ", "single"),
    ],
)
def test_known_filing_status_aliases_keep_their_keys(filing_status: str, key: str) -> None:
    assert compliance._filing_key(filing_status) == key


def test_accepted_list_matches_the_refusal_message() -> None:
    assert ", ".join(compliance._ACCEPTED_FILING_STATUSES) == _ACCEPTED


@pytest.mark.parametrize("filing_status", ["married", "zz", "married_filing_jointly", "---"])
def test_unknown_filing_status_is_refused(filing_status: str) -> None:
    with pytest.raises(SafetyViolationError) as raised:
        compliance._filing_key(filing_status)
    assert str(raised.value) == _refusal(filing_status)


def test_extra_filing_status_refuses_unknown_and_keeps_non_extra_none() -> None:
    assert compliance._extra_filing_status("  MFJ ") is None
    assert compliance._extra_filing_status("") is None
    assert compliance._extra_filing_status("qualifying_widow") is None
    assert compliance._extra_filing_status("head_of_household") == "hoh"
    assert compliance._extra_filing_status("married-filing-separately") == "mfs"
    with pytest.raises(SafetyViolationError) as raised:
        compliance._extra_filing_status("zz")
    assert str(raised.value) == _refusal("zz")


def test_omitted_filing_status_defaults_to_single() -> None:
    """A missing argument uses the parameter default. It is not an unknown status."""
    state_omitted = compliance.evaluate_state_residency(jurisdiction="FL", days_in_state=10)
    state_single = compliance.evaluate_state_residency(
        jurisdiction="FL", days_in_state=10, filing_status="single"
    )
    assert state_omitted == state_single
    georgia_omitted = compliance.evaluate_georgia_residency(days_present_ga=10)
    georgia_single = compliance.evaluate_georgia_residency(
        days_present_ga=10, filing_status="single"
    )
    assert georgia_omitted == georgia_single


def test_blank_filing_status_stays_on_the_single_path() -> None:
    blank = compliance.evaluate_state_residency(
        jurisdiction="FL", days_in_state=10, filing_status="   "
    )
    single = compliance.evaluate_state_residency(
        jurisdiction="FL", days_in_state=10, filing_status="single"
    )
    assert blank == single


def test_public_tools_treat_aliases_as_the_same_status() -> None:
    joint = compliance.evaluate_georgia_residency(
        days_present_ga=10, filing_status="married_joint"
    )
    spaced = compliance.evaluate_georgia_residency(
        days_present_ga=10, filing_status="  Married Filing Jointly  "
    )
    assert spaced == joint
    household = compliance.evaluate_state_residency(
        jurisdiction="NY",
        days_in_state=200,
        is_domiciled=True,
        federal_agi=80000.0,
        filing_status="hoh",
        tax_year=2024,
    )
    folded = compliance.evaluate_state_residency(
        jurisdiction="NY",
        days_in_state=200,
        is_domiciled=True,
        federal_agi=80000.0,
        filing_status="head-of-household",
        tax_year=2024,
    )
    single = compliance.evaluate_state_residency(
        jurisdiction="NY",
        days_in_state=200,
        is_domiciled=True,
        federal_agi=80000.0,
        filing_status="single",
        tax_year=2024,
    )
    assert folded == household
    assert household["tax_computation"]["estimated_state_tax"] != (
        single["tax_computation"]["estimated_state_tax"]
    )


@pytest.mark.parametrize("filing_status", ["married", "zz"])
@pytest.mark.parametrize(
    "call",
    [
        lambda status: compliance.evaluate_state_residency(
            jurisdiction="NOPE",
            days_in_state=-1,
            federal_agi=-1.0,
            filing_status=status,
        ),
        lambda status: compliance.evaluate_georgia_residency(
            days_present_ga=-1,
            federal_agi=-1.0,
            retirement_income=-1.0,
            filing_status=status,
        ),
        lambda status: compliance.evaluate_state_residency(
            jurisdiction="CAN",
            days_in_state=200,
            is_domiciled=True,
            federal_agi=50000.0,
            filing_status=status,
        ),
    ],
)
def test_public_tools_refuse_unknown_status_before_any_result(call, filing_status: str) -> None:
    with pytest.raises(SafetyViolationError) as raised:
        call(filing_status)
    assert str(raised.value) == _refusal(filing_status)


@pytest.mark.asyncio
@pytest.mark.parametrize("filing_status", ["married", "zz"])
@pytest.mark.parametrize(
    ("tool", "args"),
    [
        (
            "compliance_evaluate_state_residency",
            {"jurisdiction": "VT", "days_in_state": 200, "federal_agi": 80000.0},
        ),
        (
            "compliance_evaluate_georgia_residency",
            {"days_present_ga": 200, "federal_agi": 80000.0},
        ),
    ],
)
async def test_unknown_filing_status_is_a_tool_error(
    tool: str, args: dict[str, object], filing_status: str
) -> None:
    """The client receives isError and no computed payload."""
    async with Client(create_server()) as client:
        result = await client.call_tool(
            tool, {**args, "filing_status": filing_status}, raise_on_error=False
        )
    assert result.is_error
    assert result.structured_content is None
    text = result.content[0].text
    assert isinstance(text, str)
    assert text == _refusal(filing_status)
    assert "estimated_state_tax" not in text
    assert "estimated_georgia_tax" not in text
