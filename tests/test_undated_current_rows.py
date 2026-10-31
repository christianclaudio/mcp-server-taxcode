"""Reject more than one current row in groups without dated rows (#52)."""

from __future__ import annotations

import copy
import shutil
from pathlib import Path
from typing import Any

import pytest

from taxcode_mcp.catalog import DATA_DIR, CatalogError, load_catalog, window_span_errors


def _rows_by_id() -> dict[str, dict[str, Any]]:
    return {s["id"]: copy.deepcopy(s) for s in load_catalog().statutes}


def _add_current(rows: dict[str, dict[str, Any]], row_id: str, new_id: str) -> None:
    rows[new_id] = {**rows[row_id], "id": new_id, "superseded_year": None}


def test_undated_group_with_one_current_row_passes() -> None:
    """A citation with only one current row and no dated rows stays valid."""
    catalog = load_catalog().statutes
    rows = [s for s in catalog if s["id"] == "ak-stat-43-05-080"]
    assert len(rows) == 1
    assert rows[0]["superseded_year"] is None
    # Empty spans: a lone undated current row must not trip the at-most-one rule.
    assert window_span_errors(rows, spans={}) == []
    assert window_span_errors(catalog) == []


def test_undated_group_rejects_two_current_rows() -> None:
    """Two current rows that share a citation and have no dated siblings are rejected."""
    by_id = _rows_by_id()
    _add_current(by_id, "ak-stat-43-05-080", "ak-stat-43-05-080-copy")
    errors = window_span_errors(list(by_id.values()))
    assert any(
        "Alaska Stat. § 43.05.080" in e and "2 current rows, expected at most 1" in e
        for e in errors
    ), errors


def test_dated_group_two_current_rule_unchanged() -> None:
    """Groups that already have dated rows still use the exact-one-current check."""
    by_id = _rows_by_id()
    _add_current(by_id, "us-irc-24", "us-irc-24-copy")
    errors = window_span_errors(list(by_id.values()))
    assert any("2 current rows, expected 1" in e for e in errors), errors
    assert not any("expected at most 1" in e for e in errors), errors


def test_load_catalog_refuses_two_current_rows_in_an_undated_group(tmp_path: Path) -> None:
    data = tmp_path / "data"
    shutil.copytree(DATA_DIR, data)
    path = data / "statutes" / "ak.json"
    text = path.read_text(encoding="utf-8")
    marker = '"id": "ak-stat-43-05-080"'
    assert text.count(marker) == 1
    # Duplicate the sole statute object with a new id (same citation, still current).
    start = text.index("    {\n", text.index('"statutes"'))
    end = text.index("\n  ]\n}", start)
    block = text[start:end]
    clone = block.replace(marker, '"id": "ak-stat-43-05-080-copy"', 1)
    path.write_text(text[:end] + ",\n" + clone + text[end:], encoding="utf-8")
    with pytest.raises(CatalogError, match="2 current rows, expected at most 1"):
        load_catalog(data)
