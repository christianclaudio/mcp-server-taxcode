"""Married-filing-separately spouse inputs, Louisiana's exemption credit, and SCIAD.

Oregon ORS 316.695, Michigan MCL 206.30(2)(a), IRC 63(c)(6)(A), RCW 82.87.060(1),
the Louisiana IT-540 tax tables for 2022-2024, and S.C. Code § 12-6-1140(15).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import pytest

from taxcode_mcp.catalog import FEDERAL_ALLOWANCE
from taxcode_mcp.errors import SafetyViolationError
from taxcode_mcp.tools import compliance


def _call(
    code: str, year: int, agi: float, status: str = "single", **kwargs: Any
) -> dict[str, Any]:
    resident = kwargs.pop("resident", True)
    return compliance.evaluate_state_residency(
        jurisdiction=code,
        days_in_state=300 if resident else 0,
        is_domiciled=resident,
        maintains_permanent_abode=resident,
        federal_agi=agi,
        filing_status=status,
        tax_year=year,
        **kwargs,
    )["tax_computation"]


def _dollars(amount: float) -> int:
    """Nearest dollar, half up, which is how the Louisiana tax tables round."""
    return int(Decimal(str(amount)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def test_spouse_inputs_are_refused_outside_married_filing_separately() -> None:
    with pytest.raises(SafetyViolationError, match="spouse_itemizes applies only"):
        _call("OR", 2024, 10000.0, spouse_itemizes=True)
    with pytest.raises(SafetyViolationError, match="spouse_has_no_gross_income applies only"):
        _call("MI", 2024, 10000.0, spouse_has_no_gross_income=True)
    with pytest.raises(SafetyViolationError, match="applies only to married filing separately"):
        _call("WA", 2024, 0.0, spouse_wa_capital_gains_deduction=1.0)
    with pytest.raises(SafetyViolationError, match="cannot be negative"):
        _call("WA", 2024, 0.0, "mfs", spouse_wa_capital_gains_deduction=-1.0)
    with pytest.raises(SafetyViolationError, match="applies only to Washington"):
        _call("OR", 2024, 10000.0, "mfs", spouse_wa_capital_gains_deduction=1.0)


def test_oregon_spouse_itemizes_zeros_the_standard_deduction() -> None:
    plain = _call("OR", 2026, 80000.0, "married_separate")
    itemized = _call("OR", 2026, 80000.0, "married_separate", spouse_itemizes=True)
    assert plain["standard_deduction"] == 2835.0
    assert plain["spouse_itemizes"] is False
    assert itemized["standard_deduction"] == 0.0
    assert itemized["spouse_itemizes"] is True
    assert itemized["taxable_net_income"] == 80000.0
    assert itemized["estimated_state_tax"] > plain["estimated_state_tax"]
    assert "316.695" in itemized["rate_description"]


def test_oregon_2025_standard_deduction_carries_forward() -> None:
    """2025 OR-40 prints $2,835 for single and for married filing separately.

    ``_for_year`` keeps the latest ``deduction_single_YYYY`` for later years, so
    2026 uses $2,835 as well. 2024 stays on the unsuffixed $2,745.
    """
    assert compliance.RATE_TABLES["OR"]["deduction_single_2025"] == 2835.0
    assert compliance._for_year(compliance.RATE_TABLES["OR"], "deduction_single", 2025) == 2835.0
    assert compliance._for_year(compliance.RATE_TABLES["OR"], "deduction_single", 2026) == 2835.0
    assert compliance._for_year(compliance.RATE_TABLES["OR"], "deduction_single", 2024) == 2745.0
    for year in (2025, 2026):
        single = _call("OR", year, 80000.0, "single")
        separate = _call("OR", year, 80000.0, "married_separate")
        assert single["taxable_net_income"] == 77165.0
        assert single["estimated_state_tax"] == 6450.94
        assert separate["standard_deduction"] == 2835.0
        assert separate["taxable_net_income"] == 77165.0
        assert separate["estimated_state_tax"] == 6450.94
    earlier = _call("OR", 2024, 80000.0, "married_separate")
    assert earlier["standard_deduction"] == 2745.0
    assert earlier["taxable_net_income"] == 77255.0
    assert earlier["estimated_state_tax"] == 6458.81


def test_oregon_2025_joint_and_surviving_spouse_deduction() -> None:
    """2025 OR-40 Table 5: joint and qualifying surviving spouse deduct $5,670.

    ``_filing_key`` leaves a surviving spouse on single brackets. From 2025 the
    Oregon deduction uses ``deduction_mfj``, and ``_for_year`` carries
    ``deduction_mfj_2025`` forward, so 2026 joint is the same $5,670.
    """
    assert compliance.RATE_TABLES["OR"]["deduction_mfj_2025"] == 5670.0
    assert compliance._for_year(compliance.RATE_TABLES["OR"], "deduction_mfj", 2025) == 5670.0
    assert compliance._for_year(compliance.RATE_TABLES["OR"], "deduction_mfj", 2026) == 5670.0
    assert compliance._for_year(compliance.RATE_TABLES["OR"], "deduction_mfj", 2024) == 5495.0
    quote = compliance.RATE_TABLES["OR"]["deduction_mfs"]["quote"]
    assert "$" not in quote
    for year in (2025, 2026):
        joint = _call("OR", year, 100000.0, "mfj")
        assert joint["taxable_net_income"] == 94330.0
        assert joint["estimated_state_tax"] == 7651.88
    surviving = _call("OR", 2025, 100000.0, "qualifying_surviving_spouse")
    assert surviving["taxable_net_income"] == 94330.0
    assert surviving["estimated_state_tax"] == 7952.87
    assert _call("OR", 2025, 100000.0, "surviving spouse")["estimated_state_tax"] == 7952.87
    assert _call("OR", 2024, 100000.0, "mfj")["taxable_net_income"] == 94505.0


def test_michigan_spouse_with_no_income_adds_one_exemption() -> None:
    plain = _call("MI", 2026, 80000.0, "mfs")
    claimed = _call("MI", 2026, 80000.0, "mfs", spouse_has_no_gross_income=True)
    joint = _call("MI", 2026, 80000.0, "mfj")
    assert plain["personal_exemption"] == 5900.0
    assert plain["spouse_has_no_gross_income"] is False
    assert claimed["personal_exemption"] == 11800.0
    assert claimed["spouse_has_no_gross_income"] is True
    assert claimed["taxable_net_income"] == joint["taxable_net_income"]
    assert "206.30" in claimed["rate_description"]


def test_federal_standard_deduction_is_zero_when_the_spouse_itemizes() -> None:
    federal = compliance.FEDERAL_STANDARD_DEDUCTION[2024]["mfs"]
    for code in ("AZ", "CO", "SC"):
        plain = _call(code, 2024, 80000.0, "mfs")
        zeroed = _call(code, 2024, 80000.0, "mfs", spouse_itemizes=True)
        assert plain["taxable_net_income"] == 80000.0 - federal
        assert zeroed["taxable_net_income"] == 80000.0
        assert zeroed["standard_deduction"] == 0.0
        assert zeroed["spouse_itemizes"] is True
        assert "63(c)(6)(A)" in zeroed["rate_description"]
    # Maine 2025 stores a dollar amount, not the federal token, so the flag does not zero it.
    maine = _call("ME", 2025, 40000.0, "mfs")
    flagged = _call("ME", 2025, 40000.0, "mfs", spouse_itemizes=True)
    assert flagged == maine
    assert flagged["taxable_net_income"] == 40000.0 - 15000.0


def test_utah_credit_base_is_zero_when_the_spouse_itemizes() -> None:
    plain = _call("UT", 2024, 30000.0, "mfs")
    zeroed = _call("UT", 2024, 30000.0, "mfs", spouse_itemizes=True)
    assert zeroed["estimated_state_tax"] > plain["estimated_state_tax"]
    assert zeroed["standard_deduction"] == 0.0
    assert zeroed["spouse_itemizes"] is True
    itemized = _call("UT", 2024, 30000.0, "mfs", itemized_deductions=20000.0)
    both = _call(
        "UT",
        2024,
        30000.0,
        "mfs",
        itemized_deductions=20000.0,
        spouse_itemizes=True,
    )
    assert both["estimated_state_tax"] == itemized["estimated_state_tax"]
    assert "spouse_itemizes" not in both


def test_washington_spouses_share_one_standard_deduction() -> None:
    gains = {"capital_gains": 400000.0}
    full = _call("WA", 2024, 0.0, "mfs", **gains)
    single = _call("WA", 2024, 0.0, "single", **gains)
    assert full == single
    shared = _call("WA", 2024, 0.0, "mfs", spouse_wa_capital_gains_deduction=100000.0, **gains)
    assert shared["standard_deduction"] == 170000.0
    assert shared["spouse_wa_capital_gains_deduction"] == 100000.0
    assert shared["taxable_net_income"] == 230000.0
    assert shared["estimated_state_tax"] == 16100.0
    claimed_all = _call("WA", 2024, 0.0, "mfs", spouse_wa_capital_gains_deduction=270000.0, **gains)
    assert claimed_all["standard_deduction"] == 0.0
    assert claimed_all["taxable_net_income"] == 400000.0
    with pytest.raises(SafetyViolationError, match="cannot exceed"):
        _call("WA", 2024, 0.0, "mfs", spouse_wa_capital_gains_deduction=270000.01, **gains)


def test_mfs_federal_cell_shapes() -> None:
    assert compliance._mfs_cell_is_federal_standard(compliance.RATE_TABLES["CO"], "deduction", 2026)
    assert compliance._mfs_cell_is_federal_standard(compliance.RATE_TABLES["ME"], "deduction", 2024)
    assert not compliance._mfs_cell_is_federal_standard(
        compliance.RATE_TABLES["ME"], "deduction", 2025
    )
    assert not compliance._mfs_cell_is_federal_standard(
        compliance.RATE_TABLES["ME"], "deduction", 2026
    )
    direct = {"deduction_mfs": FEDERAL_ALLOWANCE}
    assert compliance._mfs_cell_is_federal_standard(direct, "deduction", 2024)
    numbered = {
        "deduction_mfs": {"same_as": "single", "quote": "a fixed amount"},
        "deduction_single": 1000.0,
        "deduction": 1000.0,
    }
    assert not compliance._mfs_cell_is_federal_standard(numbered, "deduction", 2024)
    assert not compliance._mfs_cell_is_federal_standard({}, "deduction", 2024)


# Printed IT-540 tax-table rows, one exemption (single and head of household) or two
# (joint). Read from the hashed tables. The model is checked at the row midpoint.
_LA_ROWS = (
    # year, status, low, high, printed tax, exemption
    (2022, "single", 5000, 5250, 12, 4500.0),
    (2022, "single", 16000, 16250, 275, 4500.0),
    (2022, "single", 50750, 51000, 1498, 4500.0),
    (2022, "mfs", 5000, 5250, 12, 4500.0),
    (2022, "mfs", 16000, 16250, 275, 4500.0),
    (2022, "mfs", 50750, 51000, 1498, 4500.0),
    (2022, "mfj", 0, 9000, 0, 9000.0),
    (2022, "mfj", 20000, 20250, 206, 9000.0),
    (2022, "mfj", 100000, 100250, 2926, 9000.0),
    (2023, "single", 5000, 5250, 12, 4500.0),
    (2023, "single", 16000, 16250, 275, 4500.0),
    (2023, "single", 50750, 51000, 1498, 4500.0),
    (2023, "mfs", 5000, 5250, 12, 4500.0),
    (2023, "mfs", 16000, 16250, 275, 4500.0),
    (2023, "mfs", 50750, 51000, 1498, 4500.0),
    (2023, "mfj", 0, 9000, 0, 9000.0),
    (2023, "mfj", 20000, 20250, 206, 9000.0),
    (2023, "mfj", 100000, 100250, 2926, 9000.0),
    (2024, "single", 5000, 5250, 12, 4500.0),
    (2024, "single", 16000, 16250, 275, 4500.0),
    (2024, "single", 50750, 51000, 1498, 4500.0),
    (2024, "mfs", 5000, 5250, 12, 4500.0),
    (2024, "mfs", 16000, 16250, 275, 4500.0),
    (2024, "mfs", 50750, 51000, 1498, 4500.0),
    (2024, "mfj", 0, 9000, 0, 9000.0),
    (2024, "mfj", 20000, 20250, 206, 9000.0),
    (2024, "mfj", 100000, 100250, 2926, 9000.0),
    (2024, "hoh", 9000, 9250, 2, 9000.0),
    (2024, "hoh", 20750, 21000, 358, 9000.0),
)


@pytest.mark.parametrize(("year", "status", "low", "high", "printed", "exemption"), _LA_ROWS)
def test_louisiana_credit_matches_the_printed_tax_table(
    year: int, status: str, low: int, high: int, printed: int, exemption: float
) -> None:
    midpoint = (low + high) / 2
    tax = _call("LA", year, midpoint, status)
    assert tax["taxable_net_income"] == midpoint
    assert _dollars(tax["estimated_state_tax"]) == printed
    assert "nonrefundable credit" in tax["rate_description"]
    assert exemption in {4500.0, 9000.0}


def test_louisiana_credit_is_nonrefundable_and_later_years_still_deduct() -> None:
    wiped = _call("LA", 2024, 1000.0, "single")
    assert wiped["estimated_state_tax"] == 0.0
    assert wiped["taxable_net_income"] == 1000.0
    absent = _call("LA", 2024, 16125.0, "single", resident=False)
    assert absent["estimated_state_tax"] == 0.0
    assert absent["taxable_net_income"] == 16125.0
    later = _call("LA", 2025, 20000.0, "single")
    assert later["taxable_net_income"] == 20000.0 - 12500.0
    assert "nonrefundable credit" not in later["rate_description"]
    with pytest.raises(SafetyViolationError, match="No sourced brackets value exists"):
        _call("LA", 2021, 20000.0, "single")


def test_south_carolina_income_adjusted_deduction_phaseout() -> None:
    # status, threshold, one cent over, mid AGI, mid deduction, phase-out AGI
    cases = (
        ("single", 40000.0, 40000.01, 50000.0, 12280.0, 95000.0),
        ("mfs", 40000.0, 40000.01, 50000.0, 12280.0, 95000.0),
        ("head_of_household", 60000.0, 60000.01, 100000.0, 11600.0, 142500.0),
        ("mfj", 80000.0, 80000.01, 100000.0, 24550.0, 190000.0),
    )
    bases = {"single": 15000.0, "mfs": 15000.0, "head_of_household": 22500.0, "mfj": 30000.0}
    for status, threshold, penny, mid_agi, mid_deduction, phase_out in cases:
        at = _call("SC", 2026, threshold, status)
        assert at["taxable_net_income"] == threshold - bases[status]
        over = _call("SC", 2026, penny, status)
        assert over["taxable_net_income"] == pytest.approx(at["taxable_net_income"] + 0.01)
        mid = _call("SC", 2026, mid_agi, status)
        assert mid["taxable_net_income"] == mid_agi - mid_deduction
        ended = _call("SC", 2026, phase_out, status)
        assert ended["taxable_net_income"] == phase_out
        above = _call("SC", 2027, phase_out + 1000.0, status)
        assert above["taxable_net_income"] == phase_out + 1000.0
    # Reduction of exactly $30 is already a multiple of $10.
    exact = _call("SC", 2026, 40110.0, "single")
    assert exact["taxable_net_income"] == 40110.0 - 14970.0
    # 2027 uses the same unindexed amounts.
    assert _call("SC", 2027, 50000.0, "single")["taxable_net_income"] == 50000.0 - 12280.0
    # Act 110 does not adopt the federal deduction, so the spouse flag is ignored.
    flagged = _call("SC", 2026, 50000.0, "mfs", spouse_itemizes=True)
    assert flagged["taxable_net_income"] == 50000.0 - 12280.0
    assert "spouse_itemizes" not in flagged
    widow = _call("SC", 2026, 80000.0, "qualifying_widow")
    assert widow["taxable_net_income"] == 80000.0 - 30000.0
    surviving = _call("SC", 2026, 80000.0, "surviving spouse")
    assert surviving["taxable_net_income"] == widow["taxable_net_income"]


def test_canada_ignores_a_married_separate_spouse_itemizes_flag() -> None:
    plain = compliance.evaluate_state_residency(
        jurisdiction="CAN",
        days_in_state=200,
        is_domiciled=True,
        federal_agi=50000.0,
        filing_status="married_separate",
        tax_year=2024,
    )
    flagged = compliance.evaluate_state_residency(
        jurisdiction="CAN",
        days_in_state=200,
        is_domiciled=True,
        federal_agi=50000.0,
        filing_status="married_separate",
        tax_year=2024,
        spouse_itemizes=True,
    )
    assert flagged == plain
