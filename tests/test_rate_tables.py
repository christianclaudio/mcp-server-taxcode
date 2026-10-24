"""The rate tables under ``data/rates`` load, and every malformed file is rejected."""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from taxcode_mcp.catalog import (
    DATA_DIR,
    RATE_TABLE_MODELS,
    CatalogError,
    dump_json,
    load_rate_tables,
)
from taxcode_mcp.tools import compliance


@pytest.fixture
def rates_root(tmp_path: Path) -> Path:
    shutil.copytree(DATA_DIR / "rates", tmp_path / "rates")
    return tmp_path


def _edit(root: Path, code: str, change: Callable[[dict[str, Any]], Any]) -> None:
    path = root / "rates" / f"{code.lower()}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    replaced = change(doc)
    path.write_text(dump_json(doc if replaced is None else replaced), encoding="utf-8")


def test_every_state_has_a_valid_table_in_canonical_form() -> None:
    tables = load_rate_tables()
    assert list(tables) == list(RATE_TABLE_MODELS)
    assert {p.stem for p in (DATA_DIR / "rates").iterdir()} == {c.lower() for c in tables}
    for code in tables:
        path = DATA_DIR / "rates" / f"{code.lower()}.json"
        text = path.read_text(encoding="utf-8")
        assert text == dump_json(json.loads(text)), code


def test_compliance_reads_the_loaded_tables() -> None:
    assert compliance.RATE_TABLES == load_rate_tables()
    assert set(compliance.STATE_REGISTRY) == {
        c for c, m in RATE_TABLE_MODELS.items() if m == "registry"
    }
    assert len(compliance.SUPPORTED_STATE_JURISDICTIONS) == 51


def _set(path: str, value: Any) -> Callable[[dict[str, Any]], None]:
    """Set a dotted path (list indexes as numbers) in the document."""

    def change(doc: dict[str, Any]) -> None:
        *head, last = path.split(".")
        node: Any = doc
        for key in head:
            node = node[int(key)] if isinstance(node, list) else node[key]
        if isinstance(node, list):
            node[int(last)] = value
        else:
            node[last] = value

    return change


def _drop(path: str) -> Callable[[dict[str, Any]], None]:
    def change(doc: dict[str, Any]) -> None:
        *head, last = path.split(".")
        node: Any = doc
        for key in head:
            node = node[int(key)] if isinstance(node, list) else node[key]
        del node[last]

    return change


def _both(*changes: Callable[[dict[str, Any]], None]) -> Callable[[dict[str, Any]], None]:
    def change(doc: dict[str, Any]) -> None:
        for one in changes:
            one(doc)

    return change


def _strip_formula(doc: dict[str, Any]) -> None:
    """Drop the top/zero/middle formula keys so a table can carry ``brackets`` instead."""
    for key in [k for k in doc["params"] if k.startswith(("top_", "zero_", "middle_"))]:
        del doc["params"][key]


_STEPS = [{"over": 1000.0, "rate": 0.05}, {"over": None, "rate": 0.02}]
_BRACKETS = {"single": _STEPS, "mfj": _STEPS}
_BRACKETS_WITH_BASE = {"single": [{**_STEPS[0], "base": 20.0}, _STEPS[1]], "mfj": _STEPS}
_BRACKETS_UNORDERED = {"single": _STEPS[::-1], "mfj": _STEPS}
_BRACKETS_BAD_RATE = {"single": _STEPS, "mfj": [{"over": 1000.0, "rate": 5.0}, _STEPS[1]]}


def _rename_first(new: str) -> Callable[[dict[str, Any]], None]:
    def change(doc: dict[str, Any]) -> None:
        params = doc["params"]
        first = next(iter(params))
        doc["params"] = {(new if k == first else k): v for k, v in params.items()}

    return change


CASES: list[tuple[str, Callable[[dict[str, Any]], Any], str]] = [
    # document
    ("NY", lambda d: [d], "top level must be an object"),
    ("NY", _drop("source_url"), "fields must be exactly"),
    ("NY", _set("schema", 2), "schema must be 1"),
    ("NY", _set("jurisdiction", "CA"), "jurisdiction must be 'NY'"),
    ("NY", _set("model", "flat"), "model must be 'brackets_whole_income'"),
    ("NY", _set("source_url", ""), "source_url must be a non-empty string"),
    ("NY", _set("params", []), "params must be an object"),
    # allowance and by-status amounts
    ("NY", _rename_first("credit"), "first key must be one of"),
    ("NY", _drop("params.brackets"), "fields must be exactly"),
    ("NY", _set("params.deduction", 1.0), "deduction: must be an object"),
    ("NY", _drop("params.deduction.mfj"), "deduction: fields must be exactly"),
    ("NY", _set("params.deduction.single", 8000), "must be a non-negative float"),
    ("NY", _set("params.deduction.single", -1.0), "must be a non-negative float"),
    # brackets
    ("NY", _set("params.brackets", []), "brackets: must be an object"),
    ("NY", _drop("params.brackets.mfj"), "brackets: fields must be exactly"),
    ("NY", _set("params.brackets.single", []), "must be a non-empty list"),
    ("NY", _set("params.brackets.single.0", 1.0), "single[0]: must be an object"),
    ("NY", _set("params.brackets.single.0", {"rate": 0.1}), "single[0]: fields must be exactly"),
    ("NY", _set("params.brackets.single.4.over", 1.0), "last step's over must be null"),
    ("NY", _set("params.brackets.single.1.over", None), "single[1]: steps out of order"),
    ("NY", lambda d: d["params"]["brackets"]["single"].reverse(), "single[0]: steps out of order"),
    ("NY", _set("params.brackets.single.1.over", 9e9), "sort by over from highest to lowest"),
    ("GA", _set("params.rates.1.from_year", 2099), "sort by from_year from highest to lowest"),
    ("NY", _set("params.brackets.single.2.over", "80650"), "single[2].over: must be a non-neg"),
    ("NY", _set("params.brackets.single.4.over", "80650"), "single[4].over: must be a non-neg"),
    ("GA", _set("params.rates.1.from_year", "2020"), "rates[1].from_year: must be an integer"),
    ("NY", _set("params.brackets.single.0.over", "5000000"), "over: must be a non-negative"),
    ("NY", _set("params.brackets.single.0.rate", 1.5), "rate: must be a float rate"),
    ("NY", _set("params.brackets.single.0.rate", 1), "rate: must be a float rate"),
    ("VA", _set("params.brackets.mfj.0.base", "720"), "base: must be a non-negative"),
    # per-year steps
    ("GA", _set("params.rates.0.from_year", "2026"), "from_year: must be an integer"),
    ("GA", _set("params.rates.0.rate", -0.1), "rate: must be a float rate"),
    ("NC", _set("params.rates.0.rate_desc", ""), "rate_desc: must be a non-empty string"),
    ("GA", _set("params.rates.0.law", ""), "rates[0].law: must be a non-empty string"),
    ("GA", _drop("params.rates.1.law"), "rates[1]: fields must be exactly"),
    ("CO", _drop("params.rates.0.rate_desc"), "rates[0]: fields must be exactly"),
    ("GA", _set("params.rates.2.from_year", 2024), "last step's from_year must be null"),
    # georgia retirement exclusion
    ("GA", _set("params.retirement_exclusion", {}), "must be a non-empty list"),
    ("GA", _set("params.retirement_exclusion.0", 65), "retirement_exclusion[0]: must be an obj"),
    ("GA", _drop("params.retirement_exclusion.0.amount"), "fields must be exactly"),
    ("GA", _set("params.retirement_exclusion.0.min_age", 65.0), "min_age: must be an integer"),
    ("GA", _set("params.retirement_exclusion.0.amount", 1), "amount: must be a non-negative"),
    ("GA", _set("params.retirement_exclusion.0.min_age", 60), "min_age must decrease"),
    ("GA", _set("params.standard_deduction.mfj", None), "must be a non-negative float"),
    # georgia per-year keys (HB 463 of 2026)
    ("GA", _set("params.standard_deduction_2026.mfj", None), "must be a non-negative float"),
    ("GA", _set("params.retirement_exclusion_2027.0.min_age", 60), "min_age must decrease"),
    ("GA", _set("params.rates_2027", []), "unknown key 'rates_2027'"),
    (
        "GA",
        _set("params.standard_deduction_2025", {"single": 1.0, "mfj": 1.0}),
        "standard_deduction_2025 out of order; years ascend",
    ),
    # maryland deduction
    ("MD", _set("params.standard_deduction", 1.0), "standard_deduction: must be an object"),
    ("MD", _drop("params.standard_deduction.minimum"), "fields must be exactly"),
    ("MD", _set("params.standard_deduction.percent_of_agi", 15.0), "must be a float rate"),
    ("MD", _set("params.standard_deduction.minimum.single", 1), "must be a non-negative"),
    ("MD", _set("params.standard_deduction.maximum.mfj", 1), "must be a non-negative"),
    # flat, surtax, capital gains
    ("IL", _set("params.rate", 4.95), "rate: must be a float rate"),
    ("PA", _set("params.rate", None), "rate: must be a float rate"),
    ("MA", _set("params.surtax", 0.04), "surtax: must be an object"),
    ("MA", _drop("params.surtax.rate"), "surtax: fields must be exactly"),
    ("MA", _set("params.surtax.over", 1000000), "surtax.over: must be a non-negative"),
    ("MA", _set("params.surtax.rate", 4.0), "surtax.rate: must be a float rate"),
    ("WA", _set("params.capital_gains_deduction", 262000), "must be a non-negative float"),
    ("WA", _set("params.capital_gains_deduction", float("inf")), "must be a non-negative"),
    # registry
    ("AL", _set("params.surprise", 1.0), "unknown keys ['surprise']"),
    ("AL", _drop("params.regime"), "missing keys ['regime']"),
    ("ND", _set("params.top_rate", 5), "top_rate: must be float"),
    ("ND", _set("params.top_rate", 5.0), "top_rate: must be a float rate"),
    ("AL", _set("params.deduction_single", -3000.0), "deduction_single: must be a non-negative"),
    ("AL", _set("params.regime", "progressive"), "regime: must be one of"),
    ("IA", _set("params.regime_2025", "linear"), "regime_2025: must be one of"),
    ("AL", _set("params.day_threshold", 0), "must be a day count from 1 to 366"),
    ("AL", _set("params.day_threshold", 400), "must be a day count from 1 to 366"),
    ("AL", _set("params.requires_abode", 1), "requires_abode: must be bool"),
    ("AL", _set("params.rate_desc", 2.0), "rate_desc: must be str"),
    # registry: how a graduated table taxes income below its top bracket
    ("AR", _set("params.middle_rate_estimate", False), "middle_rate_estimate: must be true"),
    ("AR", _set("params.middle_rate_estimate", 1), "middle_rate_estimate: must be bool"),
    ("AR", _drop("params.middle_rate_estimate"), "a graduated table needs exactly one of"),
    ("ND", _set("params.middle_rate_estimate", True), "a graduated table needs exactly one of"),
    ("IN", _set("params.middle_rate", 0.02), "['middle_rate'] apply only to a graduated table"),
    ("AR", _set("params.brackets", []), "brackets: must be dict"),
    ("UT", _drop("params.credit_phaseout_rate"), "a credit needs all of"),
    # registry: later-year brackets, prose and credit amounts
    ("ID", _set("params.brackets_2023", _BRACKETS), "unknown keys ['brackets_2023']"),
    ("ID", _set("params.rate_desc_2028", "x"), "unknown keys ['rate_desc_2028']"),
    ("UT", _set("params.credit_rate_2025", 0.06), "unknown keys ['credit_rate_2025']"),
    ("ID", _set("params.brackets_2025", []), "brackets_2025: must be dict"),
    ("ID", _set("params.rate_desc_2025", 5.3), "rate_desc_2025: must be str"),
    ("UT", _set("params.credit_base_mfj_2025", 31500), "credit_base_mfj_2025: must be float"),
    ("UT", _set("params.credit_phaseout_single_2025", -1.0), "must be a non-negative"),
    (
        "ID",
        _set("params.brackets_2025.single.0.rate", 5.3),
        "brackets_2025.single[0].rate: must be a float rate",
    ),
    (
        "ID",
        _set("params.brackets_2025.mfj", _STEPS[::-1]),
        "brackets_2025.mfj[0]: steps out of order",
    ),
    ("ID", _drop("params.brackets"), "a graduated table needs exactly one of"),
    ("UT", _set("params.brackets_2025", _BRACKETS), "brackets_2025 needs brackets first"),
    ("AL", _set("params.credit_base_single_2025", 1.0), "needs credit_base_single first"),
    (
        "ID",
        lambda d: d.update(params={"rate_desc_2025": "x", **d["params"]}),
        "rate_desc_YYYY keys must follow rate_desc, years ascending",
    ),
    (
        "NE",
        lambda d: d["params"].update(brackets_2024=d["params"].pop("brackets_2024")),
        "brackets_YYYY keys must follow brackets, years ascending",
    ),
    # later-year values of the non-registry models
    ("WA", _set("params.rate_2024", 0.07), "unknown key 'rate_2024'"),
    ("WA", _set("params.capital_gains_deduction_2024", -1.0), "must be a non-negative"),
    ("MA", _set("params.surtax_2024", {"over": 1.0}), "surtax_2024: fields must be exactly"),
    (
        "MA",
        _set("params.surtax_2022", {"over": 1.0, "rate": 0.04}),
        "surtax_2022 out of order; years ascend",
    ),
    ("CO", _set("params.rates_2024", []), "fields must be exactly"),
    ("CO", _set("params.deduction", "federal"), "deduction: must be an object"),
    ("WA", _set("params.tier", {"over": 1.0, "rate": 0.029}), "unknown key 'tier'"),
    ("WA", _set("params.tier_2025", 0.029), "tier_2025: must be an object"),
    ("WA", _drop("params.tier_2025.over"), "tier_2025: fields must be exactly"),
    ("WA", _set("params.tier_2025.rate", 2.9), "tier_2025.rate: must be a float rate"),
    ("WA", _set("params.tier_2025.over", 1000000), "tier_2025.over: must be a non-negative"),
    (
        "WA",
        _set("params.tier_2024", {"over": 1.0, "rate": 0.029}),
        "tier_2024 out of order; years ascend",
    ),
    (
        "WA",
        _set("params.capital_gains_deduction_2023", 1.0),
        "capital_gains_deduction_2023 out of order; years ascend",
    ),
    ("OH", _set("params.brackets_2024", _BRACKETS_WITH_BASE), "brackets_2024 out of order"),
    ("OH", _set("params.exemption_2025", {}), "unknown key 'exemption_2025'"),
    ("OH", _set("params.brackets_2026.single.0.base", 332), "base: must be a non-negative"),
    ("OH", _drop("params.brackets_2025.mfj.0.base"), "brackets_2025.mfj[0]: fields must be"),
    ("VA", _set("params.brackets_2025", _BRACKETS), "single[0]: fields must be exactly"),
    ("NE", _set("params.deduction_single_2023", 1.0), "unknown keys ['deduction_single_2023']"),
    ("UT", _set("params.credit_rate", 6.0), "credit_rate: must be a float rate"),
    ("UT", _set("params.credit_base_mfj", -1.0), "credit_base_mfj: must be a non-negative"),
    (
        "AR",
        _both(_drop("params.middle_rate_estimate"), _set("params.brackets", {"single": []})),
        "brackets: fields must be exactly",
    ),
    (
        "AR",
        _both(_drop("params.middle_rate_estimate"), _set("params.brackets", _BRACKETS)),
        "['top_bracket_mfj', 'top_bracket_single', 'top_rate'] are unused next to brackets",
    ),
    (
        "AR",
        _both(_strip_formula, _set("params.brackets", _BRACKETS_WITH_BASE)),
        "brackets.single[0]: fields must be exactly",
    ),
    (
        "AR",
        _both(_strip_formula, _set("params.brackets", _BRACKETS_UNORDERED)),
        "brackets.single[0]: steps out of order",
    ),
    (
        "AR",
        _both(_strip_formula, _set("params.brackets", _BRACKETS_BAD_RATE)),
        "brackets.mfj[0].rate: must be a float rate",
    ),
]


@pytest.mark.parametrize(("code", "change", "message"), CASES)
def test_malformed_table_is_rejected(
    rates_root: Path, code: str, change: Callable[[dict[str, Any]], Any], message: str
) -> None:
    _edit(rates_root, code, change)
    with pytest.raises(CatalogError, match=re.escape(message)):
        load_rate_tables(rates_root)


def test_missing_table_is_rejected(rates_root: Path) -> None:
    (rates_root / "rates" / "oh.json").unlink()
    with pytest.raises(CatalogError, match=r"rates/oh\.json: missing"):
        load_rate_tables(rates_root)


def test_stray_table_is_rejected(rates_root: Path) -> None:
    shutil.copy(rates_root / "rates" / "oh.json", rates_root / "rates" / "fl.json")
    with pytest.raises(CatalogError, match=r"files for no rate table: \['rates/fl\.json'\]"):
        load_rate_tables(rates_root)


def test_invalid_json_is_rejected(rates_root: Path) -> None:
    (rates_root / "rates" / "ga.json").write_text("{", encoding="utf-8")
    with pytest.raises(CatalogError, match=r"ga\.json: invalid JSON"):
        load_rate_tables(rates_root)


def test_a_registry_table_with_brackets_is_valid(rates_root: Path) -> None:
    _edit(rates_root, "AR", _both(_strip_formula, _set("params.brackets", _BRACKETS)))
    assert load_rate_tables(rates_root)["AR"]["brackets"] == _BRACKETS


def test_marginal_tax_taxes_each_slice_at_its_rate() -> None:
    steps = [
        {"over": 10000.0, "rate": 0.06},
        {"over": 2000.0, "rate": 0.04},
        {"over": None, "rate": 0.02},
    ]
    assert compliance._marginal_tax(steps, 0.0) == 0.0
    assert compliance._marginal_tax(steps, 1500.0) == 30.0
    assert compliance._marginal_tax(steps, 2000.0) == 40.0
    assert compliance._marginal_tax(steps, 12000.0) == 40.0 + 320.0 + 120.0


def _registry_answer(code: str, tax_year: int = 2024) -> dict[str, Any]:
    result: dict[str, Any] = compliance.evaluate_state_residency(
        jurisdiction=code,
        days_in_state=250,
        maintains_permanent_abode=True,
        federal_agi=120000.0,
        filing_status="single",
        tax_year=tax_year,
    )
    return result


def test_the_middle_rate_estimate_is_labeled_in_the_answer() -> None:
    estimated = [
        code for code, table in compliance.RATE_TABLES.items() if table.get("middle_rate_estimate")
    ]
    assert estimated
    for code in estimated:
        tax = _registry_answer(code)["tax_computation"]
        assert tax["middle_rate_is_estimate"] is True, code
        assert tax["rate_description"].endswith(compliance.MIDDLE_RATE_ESTIMATE_NOTE), code
    for code in ("ND", "IN", "AK"):
        tax = _registry_answer(code)["tax_computation"]
        assert tax["middle_rate_is_estimate"] is False, code
        assert "ESTIMATE" not in tax["rate_description"], code


# The tax at a bracket's lower edge as the state's own 2024 schedule prints it. Schedules that
# print whole dollars round or truncate, so those compare within a dollar.
OFFICIAL_2024_BASES = [
    ("DC", "single", 250000.0, 19650.0, 0.0),
    ("DC", "mfj", 1000000.0, 91525.0, 0.0),
    ("DE", "single", 25000.0, 1001.0, 0.0),
    ("DE", "mfj", 60000.0, 2943.5, 0.0),
    ("HI", "single", 200000.0, 16379.0, 1.0),
    ("HI", "mfj", 96000.0, 6427.0, 1.0),
    ("IA", "single", 31050.0, 1470.53, 0.0),
    ("IA", "mfj", 12420.0, 546.48, 0.0),
    ("KS", "single", 23000.0, 1196.0, 0.0),
    ("KS", "mfj", 46000.0, 2392.0, 0.0),
    ("ME", "single", 61600.0, 3911.0, 1.0),
    ("ME", "mfj", 52100.0, 3022.0, 1.0),
    ("MO", "single", 8911.0, 248.0, 1.0),
    ("MO", "mfj", 5092.0, 95.0, 1.0),
    ("NE", "single", 37670.0, 1495.77, 0.0),
    ("NE", "mfj", 75340.0, 2991.34, 0.0),
    ("NM", "single", 210000.0, 10010.5, 0.0),
    ("NM", "mfj", 315000.0, 15027.0, 0.0),
    ("OK", "single", 7200.0, 153.5, 0.0),
    ("OK", "mfj", 14400.0, 307.0, 0.0),
    ("OR", "single", 10750.0, 639.0, 1.0),
    ("OR", "mfj", 250000.0, 21274.0, 1.0),
    ("RI", "single", 176050.0, 7587.88, 0.0),
    ("RI", "mfj", 77450.0, 2904.38, 0.0),
    ("VT", "single", 242000.0, 15675.0, 1.0),
    ("VT", "mfj", 193300.0, 10159.0, 1.0),
    ("WI", "single", 28640.0, 1131.0, 1.0),
    ("WI", "mfj", 420420.0, 21767.0, 1.0),
    ("WV", "single", 40000.0, 1239.5, 0.0),
    ("WV", "mfj", 60000.0, 2183.5, 0.0),
]


@pytest.mark.parametrize(("code", "status", "income", "printed", "slack"), OFFICIAL_2024_BASES)
def test_brackets_reproduce_the_printed_2024_schedule(
    code: str, status: str, income: float, printed: float, slack: float
) -> None:
    # NE holds the 2024 schedule as brackets_2024 (its unsuffixed brackets are 2023's).
    brackets = compliance._for_year(compliance.RATE_TABLES[code], "brackets", 2024)[status]
    assert abs(compliance._marginal_tax(brackets, income) - printed) <= slack + 0.005


def test_south_carolina_brackets_match_the_published_shortcut() -> None:
    # SC1040TT / SC4972 (2024): 3% of income less $104 in the middle band, 6.2% less $659 above.
    brackets = compliance.RATE_TABLES["SC"]["brackets"]["single"]
    for income, shortcut in (
        (10000.0, 0.03 * 10000.0 - 104.0),
        (100000.0, 0.062 * 100000.0 - 659.0),
    ):
        assert abs(compliance._marginal_tax(brackets, income) - shortcut) <= 1.0


def test_idaho_brackets_match_the_2024_worksheet() -> None:
    # Form 40 instructions (2024): subtract $4,673 ($9,346 joint), multiply by 5.695%.
    for status, exempt in (("single", 4673.0), ("mfj", 9346.0)):
        brackets = compliance.RATE_TABLES["ID"]["brackets"][status]
        assert compliance._marginal_tax(brackets, exempt) == 0.0
        assert compliance._marginal_tax(brackets, 50000.0) == round((50000.0 - exempt) * 0.05695, 2)


def test_ohio_bases_match_the_2024_schedule() -> None:
    # IT 1040 instructions and R.C. 5747.02(A)(3)(a), 2024: $360.69 plus 2.75% over $26,050;
    # $2,394.32 plus 3.50% over $100,000.
    for status in ("single", "mfj"):
        brackets = compliance.RATE_TABLES["OH"]["brackets"][status]
        assert compliance._cumulative_tax(brackets, 26050.0) == 0.0
        assert compliance._cumulative_tax(brackets, 26050.01) == 360.69
        assert compliance._cumulative_tax(brackets, 100000.01) == 2394.32


# Flat rates and allowances as each state's 2024 return instructions print them.
OFFICIAL_2024_FIGURES = [
    ("UT", ("rate",), 0.0455),
    ("UT", ("credit_rate",), 0.06),
    ("UT", ("credit_base_single",), 14600.0),
    ("UT", ("credit_base_mfj",), 29200.0),
    ("UT", ("credit_phaseout_single",), 17652.0),
    ("UT", ("credit_phaseout_mfj",), 35304.0),
    ("UT", ("credit_phaseout_rate",), 0.013),
    ("MA", ("surtax_2024", "over"), 1053750.0),
    ("WA", ("capital_gains_deduction_2024",), 270000.0),
    ("KY", ("deduction_single",), 3160.0),
    ("KY", ("deduction_mfj",), 3160.0),
    ("KS", ("deduction_single",), 3605.0),
    ("KS", ("deduction_mfj",), 8240.0),
    ("NE", ("deduction_single_2024",), 8350.0),
    ("NE", ("deduction_mfj_2024",), 16700.0),
    ("RI", ("deduction_single",), 10550.0),
    ("RI", ("deduction_mfj",), 21150.0),
]


@pytest.mark.parametrize(("code", "path", "printed"), OFFICIAL_2024_FIGURES)
def test_tables_hold_the_printed_2024_figures(
    code: str, path: tuple[str, ...], printed: float
) -> None:
    node: Any = compliance.RATE_TABLES[code]
    for key in path:
        node = node[key]
    assert node == printed


def test_georgia_rates_follow_hb_1015_and_hb_111() -> None:
    # HB 1015 (2024): 5.39% for 2024. HB 111 (2025): 5.19% for 2025.
    rates = compliance.RATE_TABLES["GA"]["rates"]
    assert compliance._year_step(rates, 2024)["rate"] == 0.0539
    assert compliance._year_step(rates, 2025)["rate"] == 0.0519


def _resident_tax(code: str, tax_year: int, agi: float, status: str = "single") -> dict[str, Any]:
    result: dict[str, Any] = compliance.evaluate_state_residency(
        jurisdiction=code,
        days_in_state=300,
        is_domiciled=True,
        maintains_permanent_abode=True,
        federal_agi=agi,
        filing_status=status,
        tax_year=tax_year,
    )
    tax: dict[str, Any] = result["tax_computation"]
    return tax


def test_colorado_rate_follows_the_tax_year() -> None:
    # DOR income tax guide: 4.5% (2019), 4.55% (2020), 4.5% (2021), 4.4% (2022, 2023), 4.25%
    # (2024, SB 24-228), 4.4% (2025). The deduction is that year's federal standard deduction.
    rates = compliance.RATE_TABLES["CO"]["rates"]
    for tax_year, rate in (
        (2019, 0.045),
        (2020, 0.0455),
        (2021, 0.045),
        (2022, 0.044),
        (2023, 0.044),
        (2024, 0.0425),
        (2025, 0.044),
    ):
        assert compliance._year_step(rates, tax_year)["rate"] == rate, tax_year
    assert compliance.RATE_TABLES["CO"]["deduction"] == "federal_standard_deduction"
    # 100,000 - 14,600 = 85,400 taxable (2024); 100,000 - 13,850 = 86,150 (2023);
    # 100,000 - 15,750 = 84,250 (2025).
    assert _resident_tax("CO", 2024, 100000.0)["estimated_state_tax"] == 3629.50
    for tax_year, tax in ((2023, 3790.60), (2025, 3707.00)):
        answer = _resident_tax("CO", tax_year, 100000.0)
        assert answer["estimated_state_tax"] == tax, tax_year
        assert answer["rate_description"] == "Flat 4.40% rate (Proposition 121)", tax_year
    assert _resident_tax("CO", 2024, 1.0)["rate_description"] == (
        "Flat 4.25% rate (2024 TABOR reduction, SB 24-228)"
    )


def _georgia_answers(tax_year: int) -> tuple[str, str]:
    """GA's rate prose from both tools: the state tool's description and the GA authority."""
    state = _resident_tax("GA", tax_year, 100000.0)["rate_description"]
    ga = compliance.evaluate_georgia_residency(
        days_present_ga=300, federal_agi=100000.0, tax_year=tax_year
    )
    return state, ga["tax_computation"]["statutory_rate_authority"]


def test_georgia_prose_cites_the_law_that_set_the_years_rate() -> None:
    # HB 1015 (2024) sets 5.39% for 2024, HB 111 (2025) 5.19% for 2025, HB 463 (2026) 4.99%
    # from 2026, each as signed (gov.georgia.gov).
    for tax_year, rate, law in (
        (2024, "5.39%", "HB 1015 of 2024"),
        (2025, "5.19%", "HB 111 of 2025"),
        (2026, "4.99%", "HB 463 of 2026"),
    ):
        state, authority = _georgia_answers(tax_year)
        assert state == f"Flat {rate} rate ({law})", tax_year
        assert authority == f"O.C.G.A. § 48-7-20 (flat tax, {law})", tax_year
    # From 2027 the deduction and rate depend on HB 463's revenue tests, so both are labeled.
    note = compliance._ga_revenue_conditional_note(compliance.RATE_TABLES["GA"])
    for tax_year in (2027, 2028, 2030):
        state, authority = _georgia_answers(tax_year)
        assert state == f"Flat 4.99% rate (HB 463 of 2026){note}", tax_year
        assert authority == f"O.C.G.A. § 48-7-20 (flat tax, HB 463 of 2026){note}", tax_year


def test_georgia_years_before_the_flat_rate_are_labeled_estimates() -> None:
    for tax_year in (2020, 2023):
        state, authority = _georgia_answers(tax_year)
        assert "[ESTIMATE: " in state and "[ESTIMATE: " in authority, tax_year
        assert state == f"Flat 5.39% rate (HB 1015 of 2024){compliance.GA_PRE_FLAT_ESTIMATE_NOTE}"
        assert authority.endswith(compliance.GA_PRE_FLAT_ESTIMATE_NOTE), tax_year
    for tax_year in (2024, 2025, 2026):
        assert not any("ESTIMATE" in text for text in _georgia_answers(tax_year)), tax_year
    note = compliance._ga_revenue_conditional_note(compliance.RATE_TABLES["GA"])
    for tax_year in (2027, 2028, 2030):
        for text in _georgia_answers(tax_year):
            assert text.endswith(note), tax_year
            assert compliance.GA_PRE_FLAT_ESTIMATE_NOTE not in text, tax_year


# HB 463 of 2026 (signed act, gov.georgia.gov): O.C.G.A. § 48-7-27(a)(1)(B) is $15,000
# single and $30,000 joint for taxable years beginning on or after January 1, 2026 (§ 5-1),
# and the new (a)(5)(A)(xiv) raises the 65-and-older retirement exclusion to $70,000 from
# 2027, with $35,000 kept for ages 62 to 64. 2024 and 2025 are the IT-511 figures.
# Each pin is (deduction or exclusion, taxable income, tax) for 2024, 2025, 2026, 2027,
# 2028 and 2030.
GA_YEARS = (2024, 2025, 2026, 2027, 2028, 2030)
GA_PINS: list[tuple[str, dict[str, Any], str, tuple[tuple[float, float, float], ...]]] = [
    (
        "single, age 40, AGI 62,000",
        {"federal_agi": 62000.0},
        "standard_deduction",
        (
            (12000.0, 50000.0, 2695.00),
            (12000.0, 50000.0, 2595.00),
            (15000.0, 47000.0, 2345.30),
            (15000.0, 47000.0, 2345.30),
            (15000.0, 47000.0, 2345.30),
            (15000.0, 47000.0, 2345.30),
        ),
    ),
    (
        "mfj, age 40, AGI 124,000",
        {"federal_agi": 124000.0, "filing_status": "mfj"},
        "standard_deduction",
        (
            (24000.0, 100000.0, 5390.00),
            (24000.0, 100000.0, 5190.00),
            (30000.0, 94000.0, 4690.60),
            (30000.0, 94000.0, 4690.60),
            (30000.0, 94000.0, 4690.60),
            (30000.0, 94000.0, 4690.60),
        ),
    ),
    (
        "single, age 66, AGI 100,000, retirement 80,000",
        {"federal_agi": 100000.0, "age": 66, "retirement_income": 80000.0},
        "retirement_exclusion_applied",
        (
            (65000.0, 23000.0, 1239.70),
            (65000.0, 23000.0, 1193.70),
            (65000.0, 20000.0, 998.00),
            (70000.0, 15000.0, 748.50),
            (70000.0, 15000.0, 748.50),
            (70000.0, 15000.0, 748.50),
        ),
    ),
    (
        "mfj, age 63, AGI 100,000, retirement 50,000",
        {"federal_agi": 100000.0, "filing_status": "mfj", "age": 63, "retirement_income": 50000.0},
        "retirement_exclusion_applied",
        (
            (35000.0, 41000.0, 2209.90),
            (35000.0, 41000.0, 2127.90),
            (35000.0, 35000.0, 1746.50),
            (35000.0, 35000.0, 1746.50),
            (35000.0, 35000.0, 1746.50),
            (35000.0, 35000.0, 1746.50),
        ),
    ),
]


def _georgia_tax(tool: str, tax_year: int, **args: Any) -> dict[str, Any]:
    """A Georgia resident's tax computation from either tool, under common key names."""
    if tool == "georgia":
        out = compliance.evaluate_georgia_residency(
            days_present_ga=300, is_domiciled_in_ga=True, tax_year=tax_year, **args
        )["tax_computation"]
        return {
            "standard_deduction": out["standard_deduction"],
            "retirement_exclusion_applied": out["retirement_exclusion_applied"],
            "taxable": out["georgia_taxable_net_income"],
            "tax": out["estimated_georgia_tax"],
            "rate": out["applicable_rate"],
            "prose": out["statutory_rate_authority"],
        }
    out = compliance.evaluate_state_residency(
        jurisdiction="GA", days_in_state=300, is_domiciled=True, tax_year=tax_year, **args
    )["tax_computation"]
    return {
        "taxable": out["taxable_net_income"],
        "tax": out["estimated_state_tax"],
        "prose": out["rate_description"],
    }


@pytest.mark.parametrize("tool", ["georgia", "state"])
@pytest.mark.parametrize(("case", "args", "field", "pins"), GA_PINS, ids=[p[0] for p in GA_PINS])
def test_georgia_deduction_and_exclusion_follow_hb_463(
    tool: str,
    case: str,
    args: dict[str, Any],
    field: str,
    pins: tuple[tuple[float, float, float], ...],
) -> None:
    for tax_year, (amount, taxable, tax) in zip(GA_YEARS, pins, strict=True):
        answer = _georgia_tax(tool, tax_year, **args)
        if tool == "georgia":
            assert answer[field] == amount, (case, tax_year)
        assert answer["taxable"] == taxable, (case, tax_year)
        assert answer["tax"] == tax, (case, tax_year)
        assert ("[ESTIMATE: " in answer["prose"]) is (tax_year >= 2027), (case, tax_year)


@pytest.mark.parametrize("tool", ["georgia", "state"])
def test_georgia_deduction_steps_at_the_2026_edge(tool: str) -> None:
    assert _georgia_tax(tool, 2026, federal_agi=15000.0)["taxable"] == 0.0
    assert _georgia_tax(tool, 2026, federal_agi=15000.0)["tax"] == 0.0
    # 15,000 - 12,000 = 3,000 taxable at 5.19% in 2025.
    assert _georgia_tax(tool, 2025, federal_agi=15000.0)["taxable"] == 3000.0
    assert _georgia_tax(tool, 2025, federal_agi=15000.0)["tax"] == 155.70


@pytest.mark.parametrize("tool", ["georgia", "state"])
def test_georgia_rate_stays_4_99_percent_from_2026(tool: str) -> None:
    # HB 463's 0.125-percent annual cuts from 2027 depend on its revenue tests, so no later
    # year is held; 4.99% is carried, as before this change.
    # The state tool reports no rate field, so its rate is read from the prose and the tax.
    for tax_year in range(2026, 2036):
        answer = _georgia_tax(tool, tax_year, federal_agi=62000.0)
        if tool == "georgia":
            assert answer["rate"] == 0.0499, tax_year
        else:
            assert answer["prose"].startswith("Flat 4.99% rate (HB 463 of 2026)"), tax_year
        assert answer["tax"] == round(answer["taxable"] * 0.0499, 2) == 2345.30, tax_year


def test_georgia_revenue_conditional_note_text() -> None:
    note = compliance._ga_revenue_conditional_note(compliance.RATE_TABLES["GA"])
    assert note == (
        " [ESTIMATE: from tax year 2027, HB 463 of 2026 raises Georgia's standard deduction "
        "and lowers its rate only in years its revenue tests are met (O.C.G.A. §§ 48-7-20(a.1), "
        "48-7-27(a)(1.1)), so the 2026 figures ($15,000 single, $30,000 joint, 4.99%) are used]"
    )
    # § 48-7-20(a.1) has no paragraphs since HB 463, and the act is "HB 463 of 2026".
    assert "(a.1)(" not in note
    assert "HB 463 (2026)" not in note
    assert compliance._ga_estimate_note(2027) == note
    assert compliance._ga_estimate_note(2026) == ""


def test_georgia_last_fixed_year_matches_the_table() -> None:
    # A later fixed deduction key or rate step must move GA_LAST_FIXED_YEAR with it.
    ga = compliance.RATE_TABLES["GA"]
    deduction_years = [int(k[-4:]) for k in ga if k.startswith("standard_deduction_")]
    assert compliance.GA_LAST_FIXED_YEAR == max(deduction_years) == 2026
    rate_years = [s["from_year"] for s in ga["rates"] if s["from_year"] is not None]
    assert compliance.GA_LAST_FIXED_YEAR == max(rate_years)


def test_mississippi_taxes_only_income_over_the_10000_band() -> None:
    # Form 80-100 instructions (2024): "taxable income in excess of $10,000" at 4.7% for 2024.
    # A joint return puts all income in Column A of the Tax Computation Schedule, so it has one
    # $10,000 band (Filing Status for Married Persons, item 1; Line 17). A combined return with
    # both spouses' columns positive applies the schedule to each column, which needs the
    # spouses' split that the tool does not take.
    for status, deduction in (("single", 2300.0), ("married_joint", 4600.0)):
        assert _resident_tax("MS", 2024, deduction + 10000.0, status)["estimated_state_tax"] == 0.0
        tax = _resident_tax("MS", 2024, deduction + 50000.0, status)
        assert tax["estimated_state_tax"] == round(40000.0 * 0.047, 2), status
        assert tax["middle_rate_is_estimate"] is False


def test_utah_takes_the_taxpayer_tax_credit_off_the_tax() -> None:
    # TC-40 instructions (2024), lines 9-22: 4.55% of Utah taxable income, less the taxpayer
    # tax credit of 6% of the federal standard deduction (lines 15-16), reduced by 1.3% of
    # income over $17,652 single / $35,304 joint (lines 17-20), never below zero.
    def tax(agi: float, status: str) -> float:
        return _resident_tax("UT", 2024, agi, status)["estimated_state_tax"]

    assert tax(10000.0, "single") == 0.0
    assert tax(17652.0, "single") == 0.0  # 803.17 tax, 876 credit
    assert tax(30000.0, "single") == round(1365.0 - (876.0 - 0.013 * 12348.0), 2)
    assert tax(60000.0, "married_joint") == round(2730.0 - (1752.0 - 0.013 * 24696.0), 2)
    # The credit is gone once the phase-out passes it: 876 / 0.013 = 67,385 over the base.
    assert tax(200000.0, "single") == 9100.0
    table = compliance.RATE_TABLES["UT"]
    assert "deduction_single" not in table and "deduction_mfj" not in table


def test_kentucky_allows_one_standard_deduction_per_joint_return() -> None:
    # Form 740 instructions, Line 10: "If filing a joint return, only one $3,160 standard
    # deduction is allowed" (2024); $3,270 for 2025; DOR announced $3,360 for 2026 (KRS 141.081).
    # The rate is 4% for 2024 and 2025 and 3.5% for 2026 (2026 Withholding Tax Formula).
    for year, deduction, tax_on_10000 in (
        (2024, 3160.0, 400.0),
        (2025, 3270.0, 400.0),
        (2026, 3360.0, 350.0),
    ):
        for status in ("single", "married_joint"):
            assert _resident_tax("KY", year, deduction, status)["estimated_state_tax"] == 0.0
            tax = _resident_tax("KY", year, deduction + 10000.0, status)["estimated_state_tax"]
            assert tax == tax_on_10000, (year, status)


def test_rhode_island_2025_standard_deduction() -> None:
    # 2025 Tax Rate and Worksheets, Standard Deduction Worksheet: $10,900 single, $21,800 joint.
    # The 2024 figures (10,550 / 21,150) stay for 2024 and earlier.
    for status, deduction in (("single", 10900.0), ("married_joint", 21800.0)):
        assert _resident_tax("RI", 2025, deduction, status)["estimated_state_tax"] == 0.0
        assert _resident_tax("RI", 2025, deduction + 1000.0, status)["estimated_state_tax"] == 37.5
    assert _resident_tax("RI", 2024, 10550.0 + 1000.0, "single")["estimated_state_tax"] == 37.5


def test_2023_keeps_the_2023_figures() -> None:
    # NE: 2023 Tax Calculation Schedule and the DOR rate chronology, 7,900 / 15,800 for 2023
    # (8,350 / 16,700 for 2024). WA: DOR "Do you owe capital gains tax", $262,000 for 2023
    # ($270,000 for 2024). MA: DOR 4% surtax page, $1,000,000 for 2023 ($1,053,750 for 2024).
    for year, single, joint in ((2023, 7900.0, 15800.0), (2024, 8350.0, 16700.0)):
        assert _resident_tax("NE", year, single, "single")["estimated_state_tax"] == 0.0
        assert _resident_tax("NE", year, single + 1000.0, "single")["estimated_state_tax"] == 24.6
        assert _resident_tax("NE", year, joint, "married_joint")["estimated_state_tax"] == 0.0
    for year, deduction in ((2023, 262000.0), (2024, 270000.0)):
        result = compliance.evaluate_state_residency(
            jurisdiction="WA",
            days_in_state=300,
            is_domiciled=True,
            capital_gains=deduction + 10000.0,
            tax_year=year,
        )["tax_computation"]
        assert result["estimated_state_tax"] == 700.0, year
        assert f"${deduction:,.0f}" in result["rate_description"], year
    for year, threshold in ((2023, 1000000.0), (2024, 1053750.0)):
        agi = 4400.0 + threshold + 10000.0
        tax = _resident_tax("MA", year, agi)
        assert tax["estimated_state_tax"] == round((threshold + 10000.0) * 0.05 + 400.0, 2)
        assert f"${threshold:,.0f}" in tax["rate_description"], year
    # NE 2023 Tax Calculation Schedule: 2.46% to $3,700, 3.51% to $22,170, 5.01% to $35,730,
    # 6.64% above (joint 7,390 / 44,350 / 71,460). The 2024 schedule is brackets_2024.
    ne = compliance.RATE_TABLES["NE"]
    assert ne["brackets"]["single"] == [
        {"over": 35730.0, "rate": 0.0664},
        {"over": 22170.0, "rate": 0.0501},
        {"over": 3700.0, "rate": 0.0351},
        {"over": None, "rate": 0.0246},
    ]
    assert [step["over"] for step in ne["brackets"]["mfj"]] == [71460.0, 44350.0, 7390.0, None]
    assert _resident_tax("NE", 2023, 7900.0 + 50000.0)["estimated_state_tax"] == 2366.20
    joint = _resident_tax("NE", 2023, 15800.0 + 100000.0, "married_joint")
    assert joint["estimated_state_tax"] == 4732.36
    assert joint["rate_description"] == "Graduated top rate 6.64% (Convenience Rule State)"


def test_colorado_years_off_the_held_figures_are_labeled_estimates() -> None:
    # The table holds Colorado's rate for 2019-2025 and the federal standard deduction for
    # 2019-2026. A later year's rate isn't published (it may be cut under C.R.S. 39-22-627),
    # and a year outside the federal table uses its nearest year, so those are labeled.
    def label(tax_year: int) -> str:
        return str(_resident_tax("CO", tax_year, 100000.0)["rate_description"])

    prop_121 = "Flat 4.40% rate (Proposition 121)"
    deduction = "tax year {}'s federal figure is not in this table, so the {} figure is used"
    early = (
        "this table holds Colorado's rate from tax year 2019; an earlier year's rate is not "
        "in it, so the 4.40% rate is used"
    )
    late = (
        "Colorado's tax year {} rate is not published; it may be reduced under C.R.S. "
        "39-22-627 (TABOR), so the 4.40% rate is used"
    )
    for tax_year in (2015, 2018):
        expected = f"{prop_121} [ESTIMATE: {deduction.format(tax_year, 2019)}; {early}]"
        assert label(tax_year) == expected, tax_year
    assert label(2019) == "Flat 4.50% rate (tax year 2019)"
    assert label(2020) == "Flat 4.55% rate (tax year 2020)"
    assert label(2021) == "Flat 4.50% rate (tax year 2021)"
    for tax_year in (2022, 2023, 2025):
        assert label(tax_year) == prop_121, tax_year
    assert label(2024) == "Flat 4.25% rate (2024 TABOR reduction, SB 24-228)"
    assert label(2026) == f"{prop_121} [ESTIMATE: {late.format(2026)}]"
    for tax_year in (2027, 2030):
        reasons = f"{deduction.format(tax_year, 2026)}; {late.format(tax_year)}"
        assert label(tax_year) == f"{prop_121} [ESTIMATE: {reasons}]", tax_year
    assert compliance._co_estimate_note(2024, 0.0425) == ""


# Year pins. Every expected value is a literal worked from the official figure, never read
# from the tables, so a figure put under the wrong year fails here.


def _tax(code: str, tax_year: int, agi: float, status: str = "single") -> float:
    return float(_resident_tax(code, tax_year, agi, status)["estimated_state_tax"])


def _gains_tax(tax_year: int, gains: float) -> dict[str, Any]:
    result: dict[str, Any] = compliance.evaluate_state_residency(
        jurisdiction="WA",
        days_in_state=300,
        is_domiciled=True,
        maintains_permanent_abode=True,
        capital_gains=gains,
        tax_year=tax_year,
    )["tax_computation"]
    return result


def test_massachusetts_surtax_threshold_by_year() -> None:
    # DOR 4% surtax page: $1,000,000 (2023), $1,053,750 (2024), $1,083,150 (2025; also the
    # 2025 Form 1 instructions), $1,107,750 (2026). Taxable $1,500,000 after the $4,400.
    for tax_year, tax, threshold in (
        (2023, 95000.00, "$1,000,000"),
        (2024, 92850.00, "$1,053,750"),
        (2025, 91674.00, "$1,083,150"),
        (2026, 90690.00, "$1,107,750"),
        (2027, 90690.00, "$1,107,750"),
    ):
        answer = _resident_tax("MA", tax_year, 1504400.0)
        assert answer["estimated_state_tax"] == tax, tax_year
        assert answer["rate_description"].endswith(f"income over {threshold}"), tax_year
    # At the 2025 threshold: no surtax in 2025, $1,176.00 of surtax over the 2024 one.
    assert _tax("MA", 2024, 4400.0 + 1083150.0) == 55333.50
    assert _tax("MA", 2025, 4400.0 + 1083150.0) == 54157.50
    assert _tax("MA", 2025, 4400.0 + 1084150.0) == 54247.50


def test_washington_deduction_and_tier_by_year() -> None:
    # DOR "Do you owe": deduction $262,000 (2023), $270,000 (2024), $278,000 (2025). RCW
    # 82.87.040(1)(b): from 2025, an additional 2.9% on Washington capital gains over
    # $1,000,000 (gains after the deduction, RCW 82.87.020).
    for tax_year, on_500k, on_1_5m in (
        (2023, 16660.00, 86660.00),
        (2024, 16100.00, 86100.00),
        (2025, 15540.00, 91978.00),
        (2026, 15540.00, 91978.00),
    ):
        assert _gains_tax(tax_year, 500000.0)["estimated_state_tax"] == on_500k, tax_year
        assert _gains_tax(tax_year, 1500000.0)["estimated_state_tax"] == on_1_5m, tax_year
    assert _gains_tax(2024, 1500000.0)["rate_description"] == (
        "No ordinary income tax; 7.00% excise tax on capital gains over the $270,000 deduction"
    )
    assert _gains_tax(2025, 1500000.0)["rate_description"] == (
        "No ordinary income tax; 7.00% excise tax on capital gains over the $278,000 "
        "deduction, plus 2.90% on Washington capital gains over $1,000,000"
    )
    # At the deduction plus $1,000,000, and $1,000 over it.
    assert _gains_tax(2024, 1270000.0)["estimated_state_tax"] == 70000.00
    assert _gains_tax(2024, 1271000.0)["estimated_state_tax"] == 70070.00
    assert _gains_tax(2025, 1278000.0)["estimated_state_tax"] == 70000.00
    assert _gains_tax(2025, 1279000.0)["estimated_state_tax"] == 70099.00


def test_utah_rate_and_credit_by_year() -> None:
    # TC-40 instructions: 4.55% (2024) and 4.5% (2025, HB 106 of 2025); the credit is 6% of
    # the federal standard deduction (14,600 / 29,200; 15,750 / 31,500), less 1.3% of income
    # over 17,652 / 35,304 (2024) or 18,213 / 36,426 (2025).
    for tax_year, pins in (
        (2024, (649.52, 2404.52, 1299.05, 4550.00)),
        (2025, (558.23, 2298.23, 1116.46, 4500.00)),
        (2026, (558.23, 2298.23, 1116.46, 4500.00)),
    ):
        got = (
            _tax("UT", tax_year, 30000.0),
            _tax("UT", tax_year, 60000.0),
            _tax("UT", tax_year, 60000.0, "married_joint"),
            _tax("UT", tax_year, 100000.0),
        )
        assert got == pins, tax_year
    assert _resident_tax("UT", 2025, 1.0)["rate_description"] == (
        "Flat 4.50% rate (HB 106 of 2025) less the taxpayer tax credit: 6% of the $15,750 "
        "($31,500 joint) federal standard deduction, reduced by 1.3% of income over $18,213 "
        "($36,426 joint)"
    )
    # AGI 40,000: 2024 1,820.00 less (876 - 1.3% of 22,348); 2025 1,800.00 less (945 - 1.3%
    # of 21,787).
    assert _tax("UT", 2024, 40000.0) == 1234.52
    assert _tax("UT", 2025, 40000.0) == 1138.23


def test_utah_credit_base_is_the_federal_standard_deduction() -> None:
    # TC-40 line 12 takes the federal standard deduction from the federal return.
    ut = compliance.RATE_TABLES["UT"]
    federal = compliance.FEDERAL_STANDARD_DEDUCTION
    assert (ut["credit_base_single"], ut["credit_base_mfj"]) == (14600.0, 29200.0)
    assert federal[2024] == {"single": 14600.0, "mfj": 29200.0}
    assert federal[2025] == {"single": 15750.0, "mfj": 31500.0}
    assert ut["credit_base_single_2025"] == federal[2025]["single"]
    assert ut["credit_base_mfj_2025"] == federal[2025]["mfj"]


def test_idaho_schedule_and_deduction_by_year() -> None:
    # 2024: 5.695% over $4,673 ($9,346 joint), deduction 14,600 / 29,200. 2025 (HB 40; Form
    # 40 instructions; HB 559 conformity): 5.3% over $4,811 ($9,622), deduction 15,750 / 31,500.
    for tax_year, single, joint in (
        (2024, 4597.40, 3499.81),
        (2025, 4210.27, 3120.53),
        (2026, 4210.27, 3120.53),
    ):
        assert _tax("ID", tax_year, 100000.0) == single, tax_year
        assert _tax("ID", tax_year, 100000.0, "married_joint") == joint, tax_year
    # AGI 15,750 + 4,811: 2024 taxes 1,288 at 5.695%; 2025 taxes nothing, then 5.3%.
    assert _tax("ID", 2024, 20561.0) == 73.35
    assert _tax("ID", 2025, 20561.0) == 0.0
    assert _tax("ID", 2025, 21561.0) == 53.00
    assert _resident_tax("ID", 2024, 1.0)["rate_description"] == (
        "5.695% on taxable income over $4,673 ($9,346 joint)"
    )
    assert _resident_tax("ID", 2025, 1.0)["rate_description"] == (
        "5.3% on taxable income over $4,811 ($9,622 joint)"
    )


def test_idaho_brackets_match_the_2025_worksheet() -> None:
    # Form 40 instructions (2025): subtract $4,811 ($9,622 joint), multiply by 5.3%.
    for status, exempt in (("single", 4811.0), ("mfj", 9622.0)):
        brackets = compliance._for_year(compliance.RATE_TABLES["ID"], "brackets", 2025)[status]
        assert compliance._marginal_tax(brackets, exempt) == 0.0
        assert compliance._marginal_tax(brackets, 50000.0) == round((50000.0 - exempt) * 0.053, 2)


def test_mississippi_rate_by_year() -> None:
    # DOR: over $10,000 at 4.7% (2024), 4.4% (2025), 4% (2026), 3.75% (2027, HB 1 of 2025).
    for tax_year, single, joint, rate in (
        (2024, 4121.90, 4013.80, "4.70%"),
        (2025, 3858.80, 3757.60, "4.40%"),
        (2026, 3508.00, 3416.00, "4.00%"),
        (2027, 3288.75, 3202.50, "3.75%"),
    ):
        answer = _resident_tax("MS", tax_year, 100000.0)
        assert answer["estimated_state_tax"] == single, tax_year
        assert _tax("MS", tax_year, 100000.0, "married_joint") == joint, tax_year
        assert f", {rate} above" in answer["rate_description"], tax_year
        assert "phasing" not in answer["rate_description"]
    for tax_year, over_band in ((2025, 44.00), (2026, 40.00), (2027, 37.50)):
        assert _tax("MS", tax_year, 12300.0) == 0.0, tax_year
        assert _tax("MS", tax_year, 13300.0) == over_band, tax_year


def test_ohio_schedule_by_year() -> None:
    # R.C. 5747.02(A)(3): (a) 2024 $360.69 + 2.75% over $26,050, $2,394.32 + 3.5% over
    # $100,000; (b) 2025 $342.00 + 2.75%, $2,394.32 + 3.125%; (c) 2026 on $332.00 + 2.75%
    # over $26,050. AGI is taxable income plus the $2,400 exemption.
    for tax_year, pins in (
        (2024, (0.0, 1460.69, 4006.07)),
        (2025, (0.0, 1442.00, 3833.38)),
        (2026, (0.0, 1432.00, 3632.00)),
        (2027, (0.0, 1432.00, 3632.00)),
    ):
        got = tuple(_tax("OH", tax_year, taxable + 2400.0) for taxable in (26050, 66050, 146050))
        assert got == pins, tax_year
    assert _resident_tax("OH", 2025, 1.0)["rate_description"] == (
        "Graduated brackets: 0.00% up to $26,050, 2.75% to $100,000, 3.125% over $100,000"
    )
    assert _resident_tax("OH", 2026, 1.0)["rate_description"] == (
        "Graduated brackets: 0.00% up to $26,050, 2.75% over $26,050"
    )


def test_ohio_bases_match_the_2025_and_2026_schedules() -> None:
    oh = compliance.RATE_TABLES["OH"]
    for status in ("single", "mfj"):
        brackets = compliance._for_year(oh, "brackets", 2025)[status]
        assert compliance._cumulative_tax(brackets, 26050.0) == 0.0
        assert compliance._cumulative_tax(brackets, 26050.01) == 342.00
        assert compliance._cumulative_tax(brackets, 100000.01) == 2394.32
        brackets = compliance._for_year(oh, "brackets", 2026)[status]
        assert compliance._cumulative_tax(brackets, 26050.01) == 332.00
        assert compliance._cumulative_tax(brackets, 66050.0) == 1432.00


def test_rhode_island_brackets_and_deduction_by_year() -> None:
    # 2024: 77,450 / 176,050, deduction 10,550. 2025 worksheet: 79,900 / 181,650, deduction
    # 10,900. ADV 2025-22 (2026): 82,050 / 186,450, deduction 11,200 / 22,400.
    for tax_year, deduction, at_100k, at_200k in (
        (2024, 10550.0, 3975.50, 9022.48),
        (2025, 10900.0, 3951.00, 8928.54),
        (2026, 11200.0, 3929.50, 8847.52),
    ):
        assert _tax("RI", tax_year, deduction + 100000.0) == at_100k, tax_year
        assert _tax("RI", tax_year, deduction + 200000.0) == at_200k, tax_year
    for status, deduction in (("single", 11200.0), ("married_joint", 22400.0)):
        assert _tax("RI", 2026, deduction, status) == 0.0
        assert _tax("RI", 2026, deduction + 1000.0, status) == 37.50
    assert _tax("RI", 2025, 11200.0) == 11.25


def test_nebraska_brackets_and_deduction_by_year() -> None:
    # Tax Calculation Schedules 2023, 2024 and 2025; DOR rate chronology (Rev. 2-2026) for
    # 2026. Taxable 50,000 single and 100,000 joint after that year's deduction.
    for tax_year, deduction, joint_deduction, single, joint, top in (
        (2023, 7900.0, 15800.0, 2366.20, 4732.36, "6.64%"),
        (2024, 8350.0, 16700.0, 2215.84, 4431.48, "5.84%"),
        (2025, 8600.0, 17200.0, 2122.03, 4244.14, "5.20%"),
        (2026, 8850.0, 17700.0, 1974.13, 3948.26, "4.55%"),
    ):
        answer = _resident_tax("NE", tax_year, deduction + 50000.0)
        assert answer["estimated_state_tax"] == single, tax_year
        assert _tax("NE", tax_year, joint_deduction + 100000.0, "married_joint") == joint
        assert answer["rate_description"] == f"Graduated top rate {top} (Convenience Rule State)"
    # The zero band before 2024 is set by the 7,900 deduction, unchanged by the 2023 brackets.
    for tax_year in (2019, 2022):
        assert _tax("NE", tax_year, 7900.0) == 0.0, tax_year
        assert _tax("NE", tax_year, 7901.0) == 0.02, tax_year


def test_colorado_deduction_follows_the_federal_figure() -> None:
    # Federal standard deduction (Rev. Procs. 2018-57 to 2025-32; P.L. 119-21 § 70102 for
    # 2025) times that year's Colorado rate, on AGI 100,000.
    for tax_year, single, joint in (
        (2019, 3951.00, 3402.00),
        (2020, 3985.80, 3421.60),
        (2021, 3935.25, 3370.50),
        (2022, 3830.20, 3260.40),
        (2023, 3790.60, 3181.20),
        (2024, 3629.50, 3009.00),
        (2025, 3707.00, 3014.00),
        (2026, 3691.60, 2983.20),
        (2027, 3691.60, 2983.20),
    ):
        assert _tax("CO", tax_year, 100000.0) == single, tax_year
        assert _tax("CO", tax_year, 100000.0, "married_joint") == joint, tax_year
    # Before 2019, the 2019 deduction (12,200) and the 4.40% stand-in rate.
    assert _tax("CO", 2018, 12200.0) == 0.0
    assert _tax("CO", 2018, 13000.0) == 35.20
