"""Married-filing-separately spouse inputs, Louisiana's exemption credit, and SCIAD.

Oregon ORS 316.695, Michigan MCL 206.30(2)(a), IRC 63(c)(6)(A), RCW 82.87.060(1),
the Louisiana IT-540 tax tables for 2022-2024, and S.C. Code § 12-6-1140(15).
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
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
    assert plain["standard_deduction"] == 2900.0
    assert plain["spouse_itemizes"] is False
    assert itemized["standard_deduction"] == 0.0
    assert itemized["spouse_itemizes"] is True
    assert itemized["taxable_net_income"] == 80000.0
    assert itemized["estimated_state_tax"] > plain["estimated_state_tax"]
    assert "316.695" in itemized["rate_description"]


def test_oregon_2025_standard_deduction_carries_forward() -> None:
    """2025 OR-40 prints $2,835. The 2026 estimate prints $2,900.

    ``_for_year`` keeps the latest ``deduction_single_YYYY``, so 2026 and 2027
    use $2,900. 2025 stays on $2,835. 2024 stays on the unsuffixed $2,745.
    Married filing separately follows the single amount.
    """
    assert compliance.RATE_TABLES["OR"]["deduction_single_2025"] == 2835.0
    assert compliance.RATE_TABLES["OR"]["deduction_single_2026"] == 2900.0
    assert compliance._for_year(compliance.RATE_TABLES["OR"], "deduction_single", 2025) == 2835.0
    assert compliance._for_year(compliance.RATE_TABLES["OR"], "deduction_single", 2026) == 2900.0
    assert compliance._for_year(compliance.RATE_TABLES["OR"], "deduction_single", 2027) == 2900.0
    assert compliance._for_year(compliance.RATE_TABLES["OR"], "deduction_single", 2024) == 2745.0
    single_2025 = _call("OR", 2025, 80000.0, "single")
    separate_2025 = _call("OR", 2025, 80000.0, "married_separate")
    assert single_2025["taxable_net_income"] == 77165.0
    assert single_2025["estimated_state_tax"] == 6441.94
    assert separate_2025["standard_deduction"] == 2835.0
    assert separate_2025["taxable_net_income"] == 77165.0
    assert separate_2025["estimated_state_tax"] == 6441.94
    for year in (2026, 2027):
        single = _call("OR", year, 80000.0, "single")
        separate = _call("OR", year, 80000.0, "married_separate")
        assert single["taxable_net_income"] == 77100.0
        assert single["estimated_state_tax"] == 6426.75
        assert separate["standard_deduction"] == 2900.0
        assert separate["taxable_net_income"] == 77100.0
        assert separate["estimated_state_tax"] == 6426.75
    earlier = _call("OR", 2024, 80000.0, "married_separate")
    assert earlier["standard_deduction"] == 2745.0
    assert earlier["taxable_net_income"] == 77255.0
    assert earlier["estimated_state_tax"] == 6458.81


def test_oregon_2025_joint_and_surviving_spouse_deduction() -> None:
    """2025 OR-40 Table 5: joint and qualifying surviving spouse deduct $5,670.

    The 2026 estimate prints $5,800 for joint and for a qualifying surviving
    spouse. ``_for_year`` carries ``deduction_mfj_2026`` into 2027. 2025 stays
    on $5,670.
    """
    assert compliance.RATE_TABLES["OR"]["deduction_mfj_2025"] == 5670.0
    assert compliance.RATE_TABLES["OR"]["deduction_mfj_2026"] == 5800.0
    assert compliance._for_year(compliance.RATE_TABLES["OR"], "deduction_mfj", 2025) == 5670.0
    assert compliance._for_year(compliance.RATE_TABLES["OR"], "deduction_mfj", 2026) == 5800.0
    assert compliance._for_year(compliance.RATE_TABLES["OR"], "deduction_mfj", 2027) == 5800.0
    assert compliance._for_year(compliance.RATE_TABLES["OR"], "deduction_mfj", 2024) == 5495.0
    quote = compliance.RATE_TABLES["OR"]["deduction_mfs"]["quote"]
    assert "$" not in quote
    joint_2025 = _call("OR", 2025, 100000.0, "mfj")
    assert joint_2025["taxable_net_income"] == 94330.0
    assert joint_2025["estimated_state_tax"] == 7634.88
    for year in (2026, 2027):
        joint = _call("OR", year, 100000.0, "mfj")
        assert joint["taxable_net_income"] == 94200.0
        assert joint["estimated_state_tax"] == 7604.5
    assert _call("OR", 2024, 100000.0, "mfj")["taxable_net_income"] == 94505.0


def test_oregon_surviving_spouse_uses_chart_j() -> None:
    """2024 and 2025 OR-40 Chart J is the joint schedule, including a surviving spouse.

    The joint deduction is $5,495 in 2024 and $5,670 in 2025. The 2026 estimate
    prints $5,800, and 2027 uses that amount. A year before the pinned 2024
    instructions stays on the single schedule.
    """
    quote = compliance.RATE_TABLES["OR"]["brackets"]["hoh"]["quote"]
    assert quote == (
        "Chart J: For persons filing jointly, head of household, or qualifying surviving spouse."
    )
    info = compliance.RATE_TABLES["OR"]
    for year in (2024, 2025, 2026, 2027):
        assert compliance._extra_brackets(info, "mfj", year, "OR") == compliance._extra_brackets(
            info, "hoh", year, "OR"
        )
    expected = {
        2024: (94505.0, 7667.19),
        2025: (94330.0, 7634.88),
        2026: (94200.0, 7604.5),
        2027: (94200.0, 7604.5),
    }
    for year, (taxable, tax) in expected.items():
        joint = _call("OR", year, 100000.0, "mfj")
        for status in ("qualifying_surviving_spouse", "surviving spouse", "qualifying widow"):
            got = _call("OR", year, 100000.0, status)
            assert got["taxable_net_income"] == taxable == joint["taxable_net_income"]
            assert got["estimated_state_tax"] == tax == joint["estimated_state_tax"]
    earlier = _call("OR", 2023, 100000.0, "qualifying_surviving_spouse")
    assert earlier["taxable_net_income"] == 97255.0
    assert earlier["estimated_state_tax"] == 8208.81


def test_oregon_2025_rate_chart_bases() -> None:
    """2025 OR-40 Charts S and J. 2026 uses its own published chart.

    Chart S: $4,065 plus 8.75% over $50,000, and $10,627 plus 9.9% over $125,000.
    Chart J: $3,756 plus 8.75% over $50,000, and $21,256 plus 9.9% over $250,000.
    Married filing separately uses Chart S. Head of household uses Chart J with
    its own $4,560 deduction. A surviving spouse uses Chart J and the joint
    deduction. Income under $50,000 stays on the marginal schedule. 2024 does not
    pick up the 2025 bases.
    """
    chart_s = (
        (50000.0, 4065.0),
        (60000.0, 4940.0),
        (125000.0, 10627.5),
        (130000.0, 11122.0),
    )
    chart_j = (
        (50000.0, 3756.0),
        (60000.0, 4631.0),
        (250000.0, 21256.0),
        (260000.0, 22246.0),
    )
    schedules = (
        ("single", 2835.0, chart_s),
        ("married_separate", 2835.0, chart_s),
        ("mfj", 5670.0, chart_j),
        ("head_of_household", 4560.0, chart_j),
        ("qualifying_surviving_spouse", 5670.0, chart_j),
    )
    rows = compliance.RATE_TABLES["OR"]["brackets_2025"]
    assert rows["single"][0] == {"over": 125000.0, "base": 10627.0, "rate": 0.099}
    assert rows["single"][1] == {"over": 50000.0, "base": 4065.0, "rate": 0.0875}
    assert rows["single"][2]["over"] == 11100.0
    assert rows["single"][3]["over"] == 4400.0
    assert rows["mfj"][0] == {"over": 250000.0, "base": 21256.0, "rate": 0.099}
    assert rows["mfj"][1] == {"over": 50000.0, "base": 3756.0, "rate": 0.0875}
    assert rows["mfj"][2]["over"] == 22150.0
    assert rows["mfj"][3]["over"] == 8800.0
    assert "$4,065" in rows["mfs"]["quote"] and "$10,627" in rows["mfs"]["quote"]
    assert "$3,756" in rows["hoh"]["quote"] and "$21,256" in rows["hoh"]["quote"]
    for status, deduction, points in schedules:
        for taxable, tax in points:
            got = _call("OR", 2025, taxable + deduction, status)
            assert got["taxable_net_income"] == taxable
            assert got["estimated_state_tax"] == tax
    under = _call("OR", 2025, 20000.0 + 2835.0, "single")
    earlier_under = _call("OR", 2024, 20000.0 + 2745.0, "single")
    assert under["taxable_net_income"] == earlier_under["taxable_net_income"] == 20000.0
    assert under["estimated_state_tax"] == 1440.0
    assert earlier_under["estimated_state_tax"] == 1449.0
    single_2024 = _call("OR", 2024, 50000.0 + 2745.0, "single")
    joint_2024 = _call("OR", 2024, 50000.0 + 5495.0, "mfj")
    assert single_2024["estimated_state_tax"] == 4074.0
    assert joint_2024["estimated_state_tax"] == 3773.0
    spouse = _call("OR", 2025, 100000.0, "qualifying_surviving_spouse")
    assert spouse["taxable_net_income"] == 94330.0
    assert spouse["estimated_state_tax"] == 7634.88


def test_oregon_2026_rate_chart_and_2027_carry() -> None:
    """2026 Publication OR-ESTIMATE Charts S and J. 2027 uses those figures.

    Chart S tax at $50,000 is $4,055.50 ($678 plus 8.75% of the excess over
    $11,400). Chart J tax at $50,000 is $3,737 ($1,357 plus 8.75% of the excess
    over $22,800). The publication's worked example taxes $68,000 on Chart J
    at $5,312. Head of household's $4,650 deduction is an exact-year key, so
    2027 head of household still has no sourced amount.
    """
    rows = compliance.RATE_TABLES["OR"]["brackets_2026"]
    assert rows["single"][0] == {"over": 125000.0, "base": 10618.0, "rate": 0.099}
    assert rows["single"][1] == {"over": 11400.0, "base": 678.0, "rate": 0.0875}
    assert rows["single"][2] == {"over": 4550.0, "base": 216.0, "rate": 0.0675}
    assert rows["mfj"][0] == {"over": 250000.0, "base": 21237.0, "rate": 0.099}
    assert rows["mfj"][1] == {"over": 22800.0, "base": 1357.0, "rate": 0.0875}
    assert rows["mfj"][2] == {"over": 9100.0, "base": 432.0, "rate": 0.0675}
    assert "$4,550" in rows["mfs"]["quote"] and "$10,618" in rows["mfs"]["quote"]
    assert "$9,100" in rows["hoh"]["quote"] and "$21,237" in rows["hoh"]["quote"]
    assert compliance._for_year(compliance.RATE_TABLES["OR"], "brackets", 2026) == rows
    assert compliance._for_year(compliance.RATE_TABLES["OR"], "brackets", 2027) == rows
    chart_s = (
        (4550.0, 216.12),
        (11400.0, 678.38),
        (50000.0, 4055.5),
        (125000.0, 10618.0),
        (130000.0, 11113.0),
    )
    chart_j = (
        (9100.0, 432.25),
        (22800.0, 1356.75),
        (50000.0, 3737.0),
        (68000.0, 5312.0),
        (250000.0, 21237.0),
        (260000.0, 22227.0),
    )
    schedules = (
        ("single", 2900.0, chart_s),
        ("married_separate", 2900.0, chart_s),
        ("mfj", 5800.0, chart_j),
        ("qualifying_surviving_spouse", 5800.0, chart_j),
    )
    for year in (2026, 2027):
        for status, deduction, points in schedules:
            for taxable, tax in points:
                got = _call("OR", year, taxable + deduction, status)
                assert got["taxable_net_income"] == taxable
                assert got["estimated_state_tax"] == tax
    household = _call("OR", 2026, 50000.0 + 4650.0, "head_of_household")
    assert household["taxable_net_income"] == 50000.0
    assert household["estimated_state_tax"] == 3737.0
    with pytest.raises(SafetyViolationError, match="not_recorded"):
        _call("OR", 2027, 50000.0, "head_of_household")


def _oregon_cents(amount: Decimal) -> float:
    """A printed chart formula, in cents (half to even)."""
    return float(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


def test_oregon_chart_income_exactly_at_a_break_stays_on_the_lower_line() -> None:
    """Oregon's charts tax "Over $X" on the next line, so equality stays below it.

    Each expected amount is the printed lower-line formula at the break, and the
    upper line one dollar above it. 2025 Chart S breaks at $4,400, $11,100,
    $50,000 and $125,000. Chart J breaks at $8,800, $22,150, $50,000 and
    $250,000. 2026 Chart S breaks at $4,550, $11,400 and $125,000. Chart J
    breaks at $9,100, $22,800 and $250,000.
    """
    rate_475 = Decimal("0.0475")
    rate_675 = Decimal("0.0675")
    rate_875 = Decimal("0.0875")
    rate_990 = Decimal("0.099")

    def s_2025(income: Decimal) -> Decimal:
        b1, b2, b3, b4 = Decimal(4400), Decimal(11100), Decimal(50000), Decimal(125000)
        if income <= b1:
            return income * rate_475
        if income <= b2:
            return b1 * rate_475 + (income - b1) * rate_675
        if income <= b3:
            at = b1 * rate_475 + (b2 - b1) * rate_675
            return at + (income - b2) * rate_875
        if income <= b4:
            return Decimal(4065) + (income - b3) * rate_875
        return Decimal(10627) + (income - b4) * rate_990

    def j_2025(income: Decimal) -> Decimal:
        b1, b2, b3, b4 = Decimal(8800), Decimal(22150), Decimal(50000), Decimal(250000)
        if income <= b1:
            return income * rate_475
        if income <= b2:
            return b1 * rate_475 + (income - b1) * rate_675
        if income <= b3:
            at = b1 * rate_475 + (b2 - b1) * rate_675
            return at + (income - b2) * rate_875
        if income <= b4:
            return Decimal(3756) + (income - b3) * rate_875
        return Decimal(21256) + (income - b4) * rate_990

    def s_2026(income: Decimal) -> Decimal:
        b1, b2, b3 = Decimal(4550), Decimal(11400), Decimal(125000)
        if income <= b1:
            return income * rate_475
        if income <= b2:
            return Decimal(216) + (income - b1) * rate_675
        if income <= b3:
            return Decimal(678) + (income - b2) * rate_875
        return Decimal(10618) + (income - b3) * rate_990

    def j_2026(income: Decimal) -> Decimal:
        b1, b2, b3 = Decimal(9100), Decimal(22800), Decimal(250000)
        if income <= b1:
            return income * rate_475
        if income <= b2:
            return Decimal(432) + (income - b1) * rate_675
        if income <= b3:
            return Decimal(1357) + (income - b2) * rate_875
        return Decimal(21237) + (income - b3) * rate_990

    cases = (
        (2025, "single", 2835, s_2025, (4400, 11100, 50000, 125000)),
        (2025, "mfj", 5670, j_2025, (8800, 22150, 50000, 250000)),
        (2026, "single", 2900, s_2026, (4550, 11400, 125000)),
        (2026, "mfj", 5800, j_2026, (9100, 22800, 250000)),
    )
    for year, status, deduction, formula, breaks in cases:
        for threshold in breaks:
            at = Decimal(threshold)
            above = at + 1
            got = _call("OR", year, float(at) + deduction, status)
            nxt = _call("OR", year, float(above) + deduction, status)
            assert got["taxable_net_income"] == float(at)
            assert nxt["taxable_net_income"] == float(above)
            assert got["estimated_state_tax"] == _oregon_cents(formula(at))
            assert nxt["estimated_state_tax"] == _oregon_cents(formula(above))


def test_oregon_2025_tax_table_breaks() -> None:
    """2025 OR-40 tax table, midpoint of the band, nearest dollar, half up.

    Column S breaks at $4,400 and $11,100. Column J breaks at $8,800 and
    $22,150. Those four thresholds are what make Chart S's tax at $50,000
    equal $4,065 and Chart J's tax at $50,000 and $250,000 equal $3,756
    and $21,256. One printed row is pinned on each side of each break,
    including $4,400–$4,500 single = 212 and $22,100–$22,200 joint = 1,319.
    """
    # status, deduction, low, high, printed dollar
    rows = (
        ("single", 2835.0, 4300, 4400, 207),
        ("single", 2835.0, 4400, 4500, 212),
        ("single", 2835.0, 11000, 11100, 658),
        ("single", 2835.0, 11200, 11300, 674),
        ("married_separate", 2835.0, 4400, 4500, 212),
        ("mfj", 5670.0, 8700, 8800, 416),
        ("mfj", 5670.0, 8800, 8900, 421),
        ("mfj", 5670.0, 22000, 22100, 1312),
        ("mfj", 5670.0, 22100, 22200, 1319),
        ("mfj", 5670.0, 22400, 22500, 1345),
        ("head_of_household", 4560.0, 22100, 22200, 1319),
        ("qualifying_surviving_spouse", 5670.0, 22100, 22200, 1319),
    )
    for status, deduction, low, high, printed in rows:
        midpoint = (low + high) / 2
        got = _call("OR", 2025, midpoint + deduction, status)
        assert got["taxable_net_income"] == midpoint
        assert _dollars(got["estimated_state_tax"]) == printed
    single_49999 = _call("OR", 2025, 49999.0 + 2835.0, "single")
    single_50000 = _call("OR", 2025, 50000.0 + 2835.0, "single")
    joint_49999 = _call("OR", 2025, 49999.0 + 5670.0, "mfj")
    joint_50000 = _call("OR", 2025, 50000.0 + 5670.0, "mfj")
    assert single_49999["estimated_state_tax"] <= single_50000["estimated_state_tax"]
    assert joint_49999["estimated_state_tax"] <= joint_50000["estimated_state_tax"]
    assert single_50000["estimated_state_tax"] == 4065.0
    assert joint_50000["estimated_state_tax"] == 3756.0
    for status, deduction, breaks in (
        ("single", 2835.0, (4400.0, 11100.0)),
        ("mfj", 5670.0, (8800.0, 22150.0, 250000.0)),
    ):
        for threshold in breaks:
            at = _call("OR", 2025, threshold + deduction, status)["estimated_state_tax"]
            below = _call("OR", 2025, threshold - 0.01 + deduction, status)["estimated_state_tax"]
            above = _call("OR", 2025, threshold + 0.01 + deduction, status)["estimated_state_tax"]
            assert below <= at <= above


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
    (2022, "hoh", 9000, 9250, 2, 9000.0),
    (2022, "hoh", 50750, 51000, 1414, 9000.0),
    (2023, "hoh", 9000, 9250, 2, 9000.0),
    (2023, "hoh", 50750, 51000, 1414, 9000.0),
    (2024, "hoh", 9000, 9250, 2, 9000.0),
    (2024, "hoh", 20750, 21000, 358, 9000.0),
    (2024, "hoh", 50750, 51000, 1414, 9000.0),
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
    for year in (2019, 2020, 2021):
        with pytest.raises(SafetyViolationError, match="No sourced brackets value exists"):
            _call("LA", year, 20000.0, "single")


def test_louisiana_above_the_table_adds_the_top_rate() -> None:
    """Past the printed table, tax adds 4.25% of the excess over $51,000 or $101,000.

    2022 (``93f832da``), 2023 (``e183a0b6``), and 2024 (``f6be874f``) each print
    that line. Head of household is filing status 4 in the 2024 instructions
    (``46f455fe``). Each head-of-household table uses the $9,000 combined
    personal exemption-standard deduction as a credit at the single schedule's
    1.85% rate. The 2022 single and joint rows inside the table are pinned with
    the other printed rows; this checks the formula past the last row.
    """
    info = compliance.RATE_TABLES["LA"]
    single = compliance._extra_brackets(info, "single", 2024, "LA")
    assert single[-1]["rate"] == 0.0185
    # year, status, at the formula floor, $9,000 above it
    points = (
        (2022, "single", 51000.0, 60000.0),
        (2022, "mfj", 101000.0, 110000.0),
        (2022, "hoh", 51000.0, 60000.0),
        (2023, "hoh", 51000.0, 60000.0),
        (2024, "hoh", 51000.0, 60000.0),
    )
    for year, status, floor, above in points:
        at = _call("LA", year, floor, status)
        over = _call("LA", year, above, status)
        assert over["taxable_net_income"] == above
        assert over["estimated_state_tax"] == pytest.approx(at["estimated_state_tax"] + 382.5)
        schedule = "mfj" if status == "mfj" else "single"
        brackets = compliance._extra_brackets(info, schedule, year, "LA")
        if status == "hoh":
            assert compliance._extra_scalar(info, "deduction", "hoh", year, "LA") == 9000.0
            assert compliance._extra_brackets(info, "hoh", year, "LA") == single
            assert over["estimated_state_tax"] == compliance._la_exemption_credit_tax(
                single, above, 9000.0
            )
        else:
            assert over["estimated_state_tax"] == compliance._la_exemption_credit_tax(
                brackets, above, 4500.0 if status == "single" else 9000.0
            )


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
