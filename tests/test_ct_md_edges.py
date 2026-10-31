"""Official CT Table C/D and MD deduction/exemption edge cases (taxcode #29 batch 3)."""

from __future__ import annotations

import pytest

from taxcode_mcp.tools import compliance


def _tax(state: str, agi: float, status: str = "single", year: int = 2024) -> float:
    answer = compliance.evaluate_state_residency(
        jurisdiction=state,
        days_in_state=200,
        maintains_permanent_abode=True,
        federal_agi=agi,
        filing_status=status,
        tax_year=year,
    )
    return float(answer["tax_computation"]["estimated_state_tax"])


@pytest.mark.parametrize(
    "agi, expected",
    [
        (56500.0, 2121.75),  # Table C below; Table E .10
        (59000.0, 2268.00),  # Table C inside; Table E .10
        (101500.01, 5090.00),  # Table C past
        (105000.0, 5300.00),  # Table D below
        (107500.0, 5475.00),  # Table D inside
        (540000.01, 37696.00),  # Table D past
    ],
)
def test_ct_table_c_and_d_edges_single(agi: float, expected: float) -> None:
    assert _tax("CT", agi) == expected
    assert _tax("CT", agi, year=2025) == expected


@pytest.mark.parametrize(
    "agi, expected",
    [
        (100500.0, 3987.23),  # Table C below; Table E .01
        (103000.0, 4215.00),
        (145500.01, 7002.50),
        (210000.0, 10600.00),
        (215000.0, 10950.00),
        (1080000.01, 75392.00),
    ],
)
def test_ct_table_c_and_d_edges_mfj(agi: float, expected: float) -> None:
    assert _tax("CT", agi, "married_joint") == expected


@pytest.mark.parametrize(
    "agi, expected",
    [
        (15000.0, 0.0),  # Table E below (decimal 0; exemption zeros tax)
        (20000.0, 40.0),  # Table E inside (.60)
        (64500.01, 2847.50),  # Table E past (.00)
    ],
)
def test_ct_table_e_edges_single(agi: float, expected: float) -> None:
    assert _tax("CT", agi) == expected
    assert _tax("CT", agi, year=2025) == expected


@pytest.mark.parametrize(
    "agi, expected",
    [
        (24000.0, 0.0),  # Table E below
        (32000.0, 72.0),  # Table E inside (.55)
        (100500.01, 4077.50),  # Table E past (.00)
    ],
)
def test_ct_table_e_edges_mfj(agi: float, expected: float) -> None:
    assert _tax("CT", agi, "married_joint") == expected


def test_md_standard_deduction_min_max_and_exemption_phaseout() -> None:
    # Min single ded: AGI $12,000 → 15% would be $1,800 exactly (floor).
    assert _tax("MD", 12000.0) == round(
        # taxable = 12000 - 1800 - 3200 = 7000; Schedule I: 90 + 4.75%*(7000-3000)
        90.0 + 0.0475 * 4000.0,
        2,
    )
    # Max single ded at AGI $18,000: ded 2700 + ex 3200; taxable 12100
    assert _tax("MD", 18000.0) == round(90.0 + 0.0475 * (12100.0 - 3000.0), 2)
    # Exemption half at FAGI just over $100,000: each $1,600; ded max 2700
    # taxable = 100000.01 - 2700 - 1600 = 95700.01
    assert _tax("MD", 100000.01) == round(90.0 + 0.0475 * (95700.01 - 3000.0), 2)
    # Exemption gone for single over $150,000; taxable 147,300.01 in 5.25% band
    assert _tax("MD", 150000.01) == round(
        5947.50 + 0.0525 * ((150000.01 - 2700.0) - 125000.0),
        2,
    )
    # Top Schedule I band: AGI $260,000 → ded 2700, ex 0; taxable 257300
    assert _tax("MD", 260000.0) == round(12760.0 + 0.0575 * (257300.0 - 250000.0), 2)
    # MFJ min ded $3,650 at AGI $24,333; two exemptions $6,400
    # taxable = 24333 - 3650 - 6400 = 14283
    assert _tax("MD", 24333.0, "married_joint") == round(
        90.0 + 0.0475 * (14283.0 - 3000.0),
        2,
    )
    # MFJ max ded $5,450 at AGI $36,333; taxable = 36333 - 5450 - 6400 = 24483
    assert _tax("MD", 36333.0, "married_joint") == round(
        90.0 + 0.0475 * (24483.0 - 3000.0),
        2,
    )
