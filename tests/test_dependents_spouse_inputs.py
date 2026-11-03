"""Dependents, Mississippi spouse split, and Utah itemized inputs for evaluate_state_residency."""

from __future__ import annotations

import pytest

from taxcode_mcp.errors import SafetyViolationError
from taxcode_mcp.tools import compliance


def _tax(
    code: str,
    tax_year: int,
    agi: float,
    status: str = "single",
    **kwargs: object,
) -> dict:
    return compliance.evaluate_state_residency(
        jurisdiction=code,
        days_in_state=300,
        is_domiciled=True,
        federal_agi=agi,
        filing_status=status,
        tax_year=tax_year,
        **kwargs,  # type: ignore[arg-type]
    )["tax_computation"]


def test_defaults_match_zero_dependents() -> None:
    for code in ("KS", "MS", "OH", "GA", "UT"):
        bare = _tax(code, 2024, 80000.0)
        zero = _tax(code, 2024, 80000.0, dependents=0)
        assert bare == zero, code


def test_kansas_dependents_reduce_taxable_income() -> None:
    # 2024: $2,320 per dependent (ip24 / Notice 24-08).
    none = _tax("KS", 2024, 50000.0)
    one = _tax("KS", 2024, 50000.0, dependents=1)
    two = _tax("KS", 2024, 50000.0, dependents=2)
    assert one["taxable_net_income"] == none["taxable_net_income"] - 2320.0
    assert two["taxable_net_income"] == none["taxable_net_income"] - 4640.0
    assert one["estimated_state_tax"] < none["estimated_state_tax"]
    # 2018-2023: $2,250 per dependent (ip18-ip23).
    none18 = _tax("KS", 2018, 40000.0)
    one18 = _tax("KS", 2018, 40000.0, dependents=1)
    assert one18["taxable_net_income"] == none18["taxable_net_income"] - 2250.0


def test_mississippi_dependents_and_combined_split() -> None:
    # $1,500 per dependent (Form 80-105 line 10).
    none = _tax("MS", 2024, 100000.0)
    one = _tax("MS", 2024, 100000.0, dependents=1)
    assert one["taxable_net_income"] == none["taxable_net_income"] - 1500.0
    # Combined path: spouse_agi splits income; each column gets a $10,000 zero band.
    joint = _tax("MS", 2024, 100000.0, "married_joint")
    combined = _tax("MS", 2024, 100000.0, "married_joint", spouse_agi=50000.0)
    assert combined["estimated_state_tax"] < joint["estimated_state_tax"]
    # Even split of $4,600 ded and $12,000 exemption; each column $50,000 AGI.
    # col taxable = 50000 - 2300 - 6000 = 41700; tax = (41700-10000)*0.047 each.
    expected = round(2 * (31700.0 * 0.047), 2)
    assert combined["estimated_state_tax"] == expected


def test_ohio_dependents_use_magi_tier() -> None:
    # MAGI <= $40,000: $2,400 each (IT 1040 2024).
    none = _tax("OH", 2024, 30000.0)
    one = _tax("OH", 2024, 30000.0, dependents=1)
    assert one["taxable_net_income"] == none["taxable_net_income"] - 2400.0
    two = _tax("OH", 2024, 30000.0, dependents=2)
    assert two["taxable_net_income"] == none["taxable_net_income"] - 4800.0


def test_georgia_dependents_graduated_and_flat() -> None:
    # 2023 graduated: $3,000; 2024 flat: $4,000 (IT-511).
    none23 = _tax("GA", 2023, 80000.0)
    one23 = _tax("GA", 2023, 80000.0, dependents=1)
    assert one23["taxable_net_income"] == none23["taxable_net_income"] - 3000.0
    none24 = _tax("GA", 2024, 80000.0)
    one24 = _tax("GA", 2024, 80000.0, dependents=1)
    assert one24["taxable_net_income"] == none24["taxable_net_income"] - 4000.0
    with pytest.raises(SafetyViolationError, match="No per-dependent exemption"):
        _tax("GA", 2018, 80000.0, dependents=1)
    assert (
        _tax("GA", 2018, 80000.0, dependents=0)["estimated_state_tax"]
        == _tax("GA", 2018, 80000.0)["estimated_state_tax"]
    )


def test_utah_dependents_and_itemized_credit_base() -> None:
    # 2024: $2,046 per dependent added to credit base (TC-40 line 11).
    none = _tax("UT", 2024, 30000.0)
    one = _tax("UT", 2024, 30000.0, dependents=1)
    assert one["estimated_state_tax"] < none["estimated_state_tax"]
    # Itemized path: larger credit base lowers tax vs standard at this AGI.
    itemized = _tax("UT", 2024, 30000.0, itemized_deductions=20000.0)
    assert itemized["estimated_state_tax"] <= none["estimated_state_tax"]
    with pytest.raises(SafetyViolationError, match="No per-dependent exemption"):
        _tax("UT", 2026, 30000.0, dependents=1)
    assert (
        _tax("UT", 2026, 30000.0, dependents=0)["estimated_state_tax"]
        == _tax("UT", 2026, 30000.0)["estimated_state_tax"]
    )


def test_input_validations() -> None:
    with pytest.raises(SafetyViolationError, match="Dependent count cannot be negative"):
        _tax("KS", 2024, 10000.0, dependents=-1)
    with pytest.raises(SafetyViolationError, match="Spouse AGI cannot be negative"):
        _tax("MS", 2024, 10000.0, "married_joint", spouse_agi=-1.0)
    with pytest.raises(SafetyViolationError, match="spouse_agi applies only"):
        _tax("MS", 2024, 10000.0, spouse_agi=1000.0)
    with pytest.raises(SafetyViolationError, match="cannot exceed federal AGI"):
        _tax("MS", 2024, 10000.0, "married_joint", spouse_agi=10001.0)
    with pytest.raises(SafetyViolationError, match="itemized_deductions applies only to Utah"):
        _tax("KS", 2024, 10000.0, itemized_deductions=100.0)
    with pytest.raises(SafetyViolationError, match="Itemized deductions cannot be negative"):
        _tax("UT", 2024, 10000.0, itemized_deductions=-1.0)
