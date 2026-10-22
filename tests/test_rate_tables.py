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
    ("GA", _set("params.rates.2.from_year", 2024), "last step's from_year must be null"),
    # georgia retirement exclusion
    ("GA", _set("params.retirement_exclusion", {}), "must be a non-empty list"),
    ("GA", _set("params.retirement_exclusion.0", 65), "retirement_exclusion[0]: must be an obj"),
    ("GA", _drop("params.retirement_exclusion.0.amount"), "fields must be exactly"),
    ("GA", _set("params.retirement_exclusion.0.min_age", 65.0), "min_age: must be an integer"),
    ("GA", _set("params.retirement_exclusion.0.amount", 1), "amount: must be a non-negative"),
    ("GA", _set("params.retirement_exclusion.0.min_age", 60), "min_age must decrease"),
    ("GA", _set("params.standard_deduction.mfj", None), "must be a non-negative float"),
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
