"""Head of household and married filing separately use sourced columns.

A missing column refuses. It does not fall through to single or joint. Oregon and
Michigan married filing separately answer at the default spouse inputs
(https://github.com/christianclaudio/mcp-server-taxcode/issues/103).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from taxcode_mcp.errors import SafetyViolationError
from taxcode_mcp.tools import compliance
from tests.test_rate_tables import _resident_tax, _tax


def test_alias_reads_the_named_status_and_a_missing_year_refuses() -> None:
    ky = compliance.RATE_TABLES["KY"]
    assert ky["deduction_hoh_2026"]["same_as"] == "single"
    assert "standard deduction" in ky["deduction_hoh_2026"]["quote"]
    assert _tax("KY", 2026, 50000.0, "head_of_household") == _tax("KY", 2026, 50000.0, "single")
    assert _tax("KY", 2023, 50000.0, "married_separate") == _tax("KY", 2023, 50000.0, "single")
    with pytest.raises(SafetyViolationError, match="not_recorded"):
        _tax("NY", 2026, 80000.0, "head of household")
    with pytest.raises(SafetyViolationError, match="not_recorded"):
        _tax("MA", 2026, 80000.0, "hoh")


def test_federal_head_of_household_deduction_is_status_specific() -> None:
    assert compliance.FEDERAL_STANDARD_DEDUCTION[2025]["hoh"] == 23625.0
    assert compliance.FEDERAL_STANDARD_DEDUCTION[2026]["mfs"] == 16100.0
    # Colorado subtracts the federal standard deduction. 2026 head of household is $24,150.
    assert _resident_tax("CO", 2026, 180000.0, "head_of_household")["taxable_net_income"] == (
        180000.0 - 24150.0
    )
    assert _resident_tax("CO", 2026, 180000.0, "married_separate")["taxable_net_income"] == (
        180000.0 - 16100.0
    )


def test_exemption_fixes_for_mississippi_kansas_and_missouri() -> None:
    ms = _resident_tax("MS", 2024, 50000.0, "head_of_household")
    assert ms["taxable_net_income"] == 50000.0 - 8000.0 - 3400.0
    # The $1,500 dependent is not folded into the $8,000 head-of-household exemption.
    assert compliance.RATE_TABLES["MS"]["exemption_hoh_2024"] == 8000.0
    ks = _resident_tax("KS", 2019, 40000.0, "hoh")
    assert ks["taxable_net_income"] == 40000.0 - 4500.0 - 5500.0
    mo = _resident_tax("MO", 2024, 50000.0, "head_of_household")
    assert mo["taxable_net_income"] == 50000.0 - 21900.0 - 1400.0


def test_oregon_and_michigan_married_separate_default_to_one_allowance() -> None:
    """Issue #103: the default spouse inputs match a single filer's allowance."""
    oregon = _resident_tax("OR", 2026, 80000.0, "married_separate")
    assert (
        oregon["taxable_net_income"]
        == _resident_tax("OR", 2026, 80000.0, "single")["taxable_net_income"]
    )
    assert oregon["standard_deduction"] == 2900.0
    assert oregon["spouse_itemizes"] is False
    michigan = _resident_tax("MI", 2026, 80000.0, "mfs")
    assert (
        michigan["taxable_net_income"]
        == _resident_tax("MI", 2026, 80000.0, "single")["taxable_net_income"]
    )
    assert michigan["personal_exemption"] == 5900.0
    assert michigan["spouse_has_no_gross_income"] is False


def test_connecticut_2024_head_of_household_uses_its_own_tables() -> None:
    # AGI under the $38,000 full-exemption ceiling: taxable income is AGI minus $19,000,
    # then the 3% head-of-household bracket (2% through $16,000).
    # Taxable income is $1,000. The 2% bracket is $20, and Table E's 0.75 credit leaves $5.
    tax = _tax("CT", 2024, 20000.0, "head_of_household")
    assert tax == 5.0
    with pytest.raises(SafetyViolationError, match="not_recorded"):
        _tax("CT", 2023, 20000.0, "hoh")
    with pytest.raises(SafetyViolationError, match="not_recorded"):
        _tax("CT", 2025, 20000.0, "married filing separately")


def test_north_dakota_head_of_household_uses_its_own_thresholds() -> None:
    # 2024: 0% through $63,175, then 1.95%. 2026 stays unrecorded (estimate schedule).
    assert _tax("ND", 2024, 63175.0 + 14600.0, "head_of_household") == 0.0
    with pytest.raises(SafetyViolationError, match="not_recorded"):
        _tax("ND", 2026, 100000.0, "hoh")
    with pytest.raises(SafetyViolationError, match="not_recorded"):
        _tax("ND", 2019, 100000.0, "mfs")


def test_phase_down_note_and_wisconsin_married_separate_brackets() -> None:
    note = _resident_tax("AL", 2024, 50000.0, "head_of_household")["rate_description"]
    assert "maximum" in note
    assert "phase-down" in note
    # Lower married-filing-separately bracket edges are not printed.
    with pytest.raises(SafetyViolationError, match="not_recorded"):
        _tax("WI", 2024, 80000.0, "married_separate")
    assert compliance.RATE_TABLES["WI"]["deduction_mfs_2024"] == 11630.0


def test_maine_2025_schedules_are_literal_and_2026_head_of_household_refuses() -> None:
    me = compliance.RATE_TABLES["ME"]
    assert [row["over"] for row in me["brackets_2025"]["single"]] == [63450.0, 26800.0, None]
    assert [row["over"] for row in me["brackets_2025"]["mfj"]] == [126900.0, 53600.0, None]
    assert [row["over"] for row in me["brackets_2025"]["hoh"]] == [95150.0, 40200.0, None]
    assert me["deduction_single_2025"] == 15000.0
    assert me["deduction_hoh_2025"] == 22500.0
    assert me["deduction_mfj_2025"] == 30000.0
    assert _resident_tax("ME", 2025, 40000.0, "single")["taxable_net_income"] == 25000.0
    with pytest.raises(SafetyViolationError, match="not_recorded"):
        _tax("ME", 2026, 40000.0, "head_of_household")


def test_california_2024_head_of_household_schedule_and_joint_deduction() -> None:
    ca = compliance.RATE_TABLES["CA"]
    assert ca["brackets_2024"]["hoh"][0] == {"over": 1000000.0, "rate": 0.133}
    assert ca["deduction_2024"]["hoh"]["same_as"] == "mfj"
    assert _resident_tax("CA", 2024, 20000.0, "hoh")["taxable_net_income"] == 20000.0 - 11080.0


def test_new_york_2022_and_2023_head_of_household_are_sourced() -> None:
    assert compliance.RATE_TABLES["NY"]["deduction_2022"]["hoh"] == 11200.0
    assert compliance.RATE_TABLES["NY"]["deduction_2023"]["hoh"] == 11200.0
    assert compliance.RATE_TABLES["NY"]["brackets_2022"]["hoh"][0]["rate"] == 0.109
    assert _tax("NY", 2022, 20000.0, "hoh") != _tax("NY", 2022, 20000.0, "single")


def test_montana_2026_head_of_household_comes_from_publication_1() -> None:
    rows = compliance.RATE_TABLES["MT"]["brackets_2026"]["hoh"]
    assert rows[0] == {"over": 71250.0, "rate": 0.0565}
    assert rows[-1] == {"over": None, "rate": 0.047}


def test_zero_tax_head_of_household_stays_zero_and_unknown_status_stays_single() -> None:
    assert _tax("AK", 2026, 180000.0, "head_of_household") == 0.0
    assert _tax("FL", 2026, 180000.0, "married_separate") == 0.0
    assert _tax("VT", 2025, 100000.0, "qualifying_widow") == _tax("VT", 2025, 100000.0, "single")


def test_maryland_head_of_household_uses_the_joint_column() -> None:
    # The phase-out column and the brackets are the joint ones. The exemption count stays 1.
    joint = _resident_tax("MD", 2024, 80000.0, "married_joint")
    hoh = _resident_tax("MD", 2024, 80000.0, "head_of_household")
    single = _resident_tax("MD", 2024, 80000.0, "single")
    assert hoh["taxable_net_income"] != single["taxable_net_income"]
    assert hoh["estimated_state_tax"] != joint["estimated_state_tax"]
    with pytest.raises(SafetyViolationError, match="not_recorded"):
        _tax("MD", 2026, 80000.0, "hoh")


def test_utah_head_of_household_credit_uses_the_federal_base() -> None:
    ut = compliance.RATE_TABLES["UT"]
    assert ut["credit_base_hoh_2024"] == "federal_standard_deduction"
    assert ut["credit_phaseout_hoh_2024"] == 26478.0
    assert _tax("UT", 2024, 50000.0, "head_of_household") >= 0.0
    with pytest.raises(SafetyViolationError, match="not_recorded"):
        _tax("UT", 2026, 50000.0, "hoh")


def test_lookup_edges_refuse_a_bad_alias_and_a_missing_exclusion() -> None:
    with pytest.raises(SafetyViolationError, match="not_recorded"):
        compliance._under_zero_liability(
            {"low_income_exclusion": {"single": 1.0, "mfj": 2.0}},
            False,
            1.0,
            2024,
            status="hoh",
            state="KS",
        )
    with pytest.raises(SafetyViolationError, match="not_recorded"):
        compliance._extra_scalar(
            {"deduction_hoh": {"same_as": "single", "quote": "no target"}},
            "deduction",
            "hoh",
            2024,
            "ZZ",
        )
    with pytest.raises(SafetyViolationError, match="not_recorded"):
        compliance._extra_scalar(
            {"deduction_hoh": "not-a-number"},
            "deduction",
            "hoh",
            2024,
            "ZZ",
        )
    with pytest.raises(SafetyViolationError, match="not_recorded"):
        compliance._extra_rows({"brackets": {"hoh": 1.0}}, "brackets", "hoh", 2024, "ZZ")
    shared = {"single": [{"over": None, "rate": 0.01}], "mfj": [{"over": None, "rate": 0.02}]}
    assert (
        compliance._extra_brackets(
            {"brackets_mfs_2024": {"same_as": "single", "quote": "same"}, "brackets": shared},
            "mfs",
            2024,
            "ZZ",
        )
        == shared["single"]
    )
    with pytest.raises(SafetyViolationError, match="not_recorded"):
        compliance._extra_table({}, "brackets", "hoh", 2024, "ZZ")
    assert compliance._cell_is_alias(None, "hoh") is False
    assert compliance._table_has_status(1, "hoh") is False
    # The default residency rule requires an abode; this call only exercises that branch.
    present = compliance._evaluate_extra_filing_status(
        state="VT",
        status="hoh",
        days_in_state=0,
        is_domiciled=True,
        maintains_permanent_abode=False,
        federal_agi=0.0,
        tax_year=2024,
        audit_flags=[],
    )
    assert present["is_resident"] is True


def test_connecticut_alias_columns_validate(tmp_path: Path) -> None:
    """An alias is accepted on each Connecticut table, not only on brackets."""
    import json

    from taxcode_mcp.catalog import DATA_DIR, rate_table_document

    doc = json.loads((DATA_DIR / "rates" / "ct.json").read_text())
    alias = {"same_as": "single", "quote": "Same as single."}
    for name in ("exemption", "phase_out_addback", "tax_recapture", "personal_tax_credit"):
        doc["params"][name]["mfs"] = alias
    path = tmp_path / "ct-alias.json"
    path.write_text(json.dumps(doc))
    loaded = rate_table_document(path, "CT")
    assert loaded["params"]["exemption"]["mfs"]["same_as"] == "single"


_SC_2025_SINGLE = (
    "No sourced brackets value exists for tax year 2025. "
    "SC1040TT 2025 (sha256 bf27744d0b7fba462b7ff9f69ec9b66af21073de8b95485b2d50e95bc36726b5) "
    "prints a top rate of 6% (minus $642) and approximate edges (about $3,560 and $17,830). "
    "The dollar edges are not exact, so the schedule is not stored."
)


def test_south_carolina_2026_uses_the_income_adjusted_deduction() -> None:
    """SC 2026 answers with the Income Adjusted Deduction (#106). 2025 still refuses."""
    # Single AGI 50,000: reduction 2,727.27 rounds down to 2,720, so the deduction is 12,280.
    single = _resident_tax("SC", 2026, 50000.0, "single")
    assert single["taxable_net_income"] == 50000.0 - 12280.0
    assert "12-6-1140(15)" in single["rate_description"]
    with pytest.raises(SafetyViolationError) as prior:
        _resident_tax("SC", 2025, 50000.0, "single")
    assert str(prior.value) == _SC_2025_SINGLE


def test_kansas_low_income_exclusion_covers_head_of_household() -> None:
    # Taxable income at the non-joint ceiling is zero. Just under it stays zero.
    assert _tax("KS", 2019, 5500.0 + 4500.0 + 2500.0, "head_of_household") == 0.0
