"""Alabama personal exemption, Maryland capital-gains surtax, California top-rate prose."""

from __future__ import annotations

from taxcode_mcp.tools import compliance


def _tax(code: str, tax_year: int, agi: float, status: str = "single", **kwargs: object) -> dict:
    return compliance.evaluate_state_residency(
        jurisdiction=code,
        days_in_state=300,
        is_domiciled=True,
        federal_agi=agi,
        filing_status=status,
        tax_year=tax_year,
        **kwargs,  # type: ignore[arg-type]
    )["tax_computation"]


def test_alabama_applies_personal_exemption_and_max_standard_deduction() -> None:
    # Form 40 2024: max std ded $3,000 single / $8,500 joint; personal ex $1,500 / $3,000.
    # AGI phase-down of the standard deduction is not modeled (max used).
    single = _tax("AL", 2024, 20000.0)
    assert single["taxable_net_income"] == 20000.0 - 3000.0 - 1500.0
    joint = _tax("AL", 2024, 40000.0, "married_joint")
    assert joint["taxable_net_income"] == 40000.0 - 8500.0 - 3000.0
    floor = _tax("AL", 2024, 4500.0)
    assert floor["taxable_net_income"] == 0.0
    assert floor["estimated_state_tax"] == 0.0
    desc = single["rate_description"]
    assert "phase-down chart is not modeled" in desc
    assert "federal income tax deduction" in desc
    assert "$1,500 single" in desc
    assert "$2,500 single / $7,500 joint for 2019-2021" in desc
    assert "Tax year 2018 uses the 2022 Form 40 standard-deduction maximum as an estimate" in desc
    y19 = _tax("AL", 2019, 20000.0)
    assert y19["taxable_net_income"] == 20000.0 - 2500.0 - 1500.0
    y21 = _tax("AL", 2021, 20000.0)
    assert y21["taxable_net_income"] == 20000.0 - 2500.0 - 1500.0
    y18 = _tax("AL", 2018, 20000.0)
    assert y18["taxable_net_income"] == 20000.0 - 3000.0 - 1500.0
    y20 = _tax("AL", 2020, 20000.0)
    assert y20["taxable_net_income"] == 20000.0 - 2500.0 - 1500.0
    y20j = _tax("AL", 2020, 40000.0, "married_joint")
    assert y20j["taxable_net_income"] == 40000.0 - 7500.0 - 3000.0


def test_maryland_capital_gains_surtax_2025_through_2028() -> None:
    # Chapter 604 / HB 352: 2% of net capital gain when FAGI > $350,000; years 2025-2028.
    base = _tax("MD", 2025, 400000.0)
    with_gain = _tax("MD", 2025, 400000.0, capital_gains=10000.0)
    assert with_gain["estimated_state_tax"] == round(base["estimated_state_tax"] + 200.0, 2)
    low = _tax("MD", 2025, 350000.0, capital_gains=10000.0)
    low_base = _tax("MD", 2025, 350000.0)
    assert low["estimated_state_tax"] == low_base["estimated_state_tax"]
    y24 = _tax("MD", 2024, 400000.0, capital_gains=10000.0)
    y24_base = _tax("MD", 2024, 400000.0)
    assert y24["estimated_state_tax"] == y24_base["estimated_state_tax"]
    # 2028 is the last year capital_gains_surtax_2025.through_year covers (rate from the table).
    surtax = compliance._for_year(compliance.RATE_TABLES["MD"], "capital_gains_surtax", 2028)
    assert isinstance(surtax, dict)
    assert int(surtax["through_year"]) == 2028
    gains = 10000.0
    surtax_delta = round(gains * float(surtax["rate"]), 2)
    y28_base = _tax("MD", 2028, 400000.0)
    y28 = _tax("MD", 2028, 400000.0, capital_gains=gains)
    assert y28["estimated_state_tax"] == round(y28_base["estimated_state_tax"] + surtax_delta, 2)
    assert "2.00% on net capital gain" in y28_base["rate_description"]
    y29 = _tax("MD", 2029, 400000.0, capital_gains=gains)
    y29_base = _tax("MD", 2029, 400000.0)
    assert y29["estimated_state_tax"] == y29_base["estimated_state_tax"]
    assert "2.00% on net capital gain" not in y29_base["rate_description"]
    assert "2.00% on net capital gain" in base["rate_description"]
    assert "Act exclusions not modeled" in base["rate_description"]
    assert "2.00% on net capital gain" not in y24_base["rate_description"]


def test_california_top_rate_prose_names_twelve_three_plus_one() -> None:
    y24 = _tax("CA", 2024, 50000.0)
    assert "13.3% is 12.3% plus the 1% Mental Health Services Tax" in y24["rate_description"]
    assert "applied once" in y24["rate_description"]
    y25 = _tax("CA", 2025, 50000.0)
    assert "13.3% is 12.3% plus the 1% Behavioral Health Services Tax" in y25["rate_description"]
    # Combined top rate is embedded once: income over $1M uses the 13.3% slice rate.
    ded = float(compliance._for_year(compliance.RATE_TABLES["CA"], "deduction", 2024)["single"])
    # Taxable income just above $1,000,000.
    agi = ded + 1000000.0 + 100.0
    high = _tax("CA", 2024, agi)
    mid = _tax("CA", 2024, ded + 1000000.0)
    assert high["estimated_state_tax"] > mid["estimated_state_tax"]
    delta_tax = high["estimated_state_tax"] - mid["estimated_state_tax"]
    assert abs(delta_tax - 100.0 * 0.133) < 0.02
