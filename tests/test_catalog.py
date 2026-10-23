"""The data files are the single source of the rows, the manifest and the generated docs."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from scripts import build_catalog
from taxcode_mcp import db
from taxcode_mcp.catalog import (
    DATA_DIR,
    MANIFEST_FIELDS,
    NOT_RECORDED,
    PUBLICATION_FIELDS,
    RATE_TABLE_MODELS,
    STATUTE_FIELDS,
    CatalogError,
    dump_json,
    load_catalog,
    manifest_errors,
    rate_table_id,
    split_lines,
    statute_doc_path,
    statute_document_row,
)

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def tree(tmp_path: Path) -> tuple[Path, Path]:
    """A writable copy of the data files and the generated docs."""
    data = tmp_path / "data"
    docs = tmp_path / "docs"
    shutil.copytree(DATA_DIR, data)
    shutil.copytree(REPO / "docs", docs)
    return data, docs


def _edit(path: Path, change: Callable[[Any], None]) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    change(doc)
    path.write_text(dump_json(doc), encoding="utf-8")


def test_committed_catalog_is_current() -> None:
    """CI gate: data files canonical, manifest complete and matching, docs regenerated."""
    assert build_catalog.check() == []


def test_build_catalog_main_check_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert build_catalog.main(["--check"]) == 0
    assert "current" in capsys.readouterr().out


def test_rows_load_in_catalog_order_with_every_field() -> None:
    catalog = load_catalog(with_manifest=True)
    assert [s["id"] for s in db.INITIAL_STATUTES] == [s["id"] for s in catalog.statutes]
    order = {code: i for i, code in enumerate(catalog.jurisdictions)}
    positions = [order[s["jurisdiction"]] for s in catalog.statutes]
    assert positions == sorted(positions)
    assert all(list(s) == list(STATUTE_FIELDS) for s in catalog.statutes)
    assert all(list(p) == list(PUBLICATION_FIELDS) for p in catalog.publications)
    assert len(catalog.rate_tables) == 49
    assert len(catalog.manifest) == (
        len(catalog.statutes) + len(catalog.publications) + len(catalog.rate_tables)
    )


def test_text_is_stored_as_lines_and_joined_exactly() -> None:
    doc = json.loads((DATA_DIR / "statutes" / "us.json").read_text(encoding="utf-8"))
    raw = next(r for r in doc["statutes"] if r["id"] == "us-irc-933")
    loaded = next(s for s in db.INITIAL_STATUTES if s["id"] == "us-irc-933")
    assert raw["text_en"] == split_lines(loaded["text_en"]) and len(raw["text_en"]) > 1
    assert "\n".join(raw["text_en"]) == loaded["text_en"]
    assert split_lines("a\n\nb\n") == ["a", "", "b", ""]


def test_loaded_rows_convert_back_to_their_data_file_form() -> None:
    catalog = load_catalog()
    raw = [
        row
        for name, doc in catalog.documents.items()
        if name.startswith("statutes/")
        for row in doc["statutes"]
    ]
    assert [statute_document_row(s) for s in catalog.statutes] == raw


def test_manifest_records_unknowns_explicitly() -> None:
    catalog = load_catalog(with_manifest=True)
    for entry in catalog.manifest:
        keys = [k for k in entry if k != "_jurisdiction"]
        # A rate table may add its other tax years' sources as one last field.
        assert keys in (list(MANIFEST_FIELDS), [*MANIFEST_FIELDS, "year_sources"]), entry["id"]
    # Nothing in the existing rows was verified word for word against a fetched copy.
    assert {e["verification"] for e in catalog.manifest} <= {"summary", NOT_RECORDED}
    # Only a rate table whose schedule was read from a fetched official file records a hash.
    hashed = {e["kind"] for e in catalog.manifest if e["content_sha256"] != NOT_RECORDED}
    assert hashed == {"rate_table"}


def test_check_reports_docs_drift_and_write_repairs_it(tree: tuple[Path, Path]) -> None:
    data, docs = tree
    page = docs / "us" / "irc_933.md"
    page.write_text("edited by hand\n", encoding="utf-8")
    (docs / "us" / "irc_9999.md").write_text("orphan\n", encoding="utf-8")
    (docs / "ga" / "ocga_48_7_1.md").unlink()
    problems = build_catalog.check(data, docs)
    assert "docs: us/irc_933.md differs from the generated output" in problems
    assert "docs: us/irc_9999.md has no row in the data files" in problems
    assert "docs: ga/ocga_48_7_1.md is missing" in problems
    changed = build_catalog.write(data, docs)
    assert "docs/us/irc_9999.md (deleted)" in changed
    assert build_catalog.check(data, docs) == []
    assert build_catalog.write(data, docs) == []


def test_check_reports_non_canonical_data_and_write_reformats(tree: tuple[Path, Path]) -> None:
    data, docs = tree
    path = data / "statutes" / "ga.json"
    path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8"))), encoding="utf-8")
    assert build_catalog.check(data, docs) == ["data: statutes/ga.json is not in canonical form"]
    assert build_catalog.write(data, docs) == ["data/statutes/ga.json"]
    assert build_catalog.check(data, docs) == []


def _break_rate(doc: Any) -> None:
    doc["params"]["brackets"]["single"][0]["rate"] = 10.9


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        (lambda d: _edit(d / "rates" / "ny.json", _break_rate), "data: rates/ny.json params"),
        (lambda d: (d / "rates" / "oh.json").unlink(), "data: rates/oh.json: missing"),
        (
            lambda d: (d / "rates" / "ga.json").write_text("{", encoding="utf-8"),
            "data: ga.json: invalid JSON",
        ),
        (
            lambda d: shutil.copy(d / "rates" / "oh.json", d / "rates" / "fl.json"),
            "data: files for no rate table: ['rates/fl.json']",
        ),
        (lambda d: shutil.rmtree(d / "rates"), "data: rates/ga.json: missing"),
    ],
)
def test_check_fails_on_a_malformed_missing_or_stray_rate_table(
    tree: tuple[Path, Path], damage: Callable[[Path], Any], message: str
) -> None:
    data, docs = tree
    damage(data)
    problems = build_catalog.check(data, docs)
    assert len(problems) == 1 and problems[0].startswith(message), problems
    with pytest.raises(CatalogError):
        build_catalog.write(data, docs)


def test_check_reports_a_non_canonical_rate_table_and_write_reformats(
    tree: tuple[Path, Path],
) -> None:
    data, docs = tree
    path = data / "rates" / "va.json"
    path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8"))), encoding="utf-8")
    assert build_catalog.check(data, docs) == ["data: rates/va.json is not in canonical form"]
    assert build_catalog.write(data, docs) == ["data/rates/va.json"]
    assert build_catalog.check(data, docs) == []


def test_a_new_row_regenerates_only_its_pages(tree: tuple[Path, Path]) -> None:
    data, docs = tree
    source = "https://example.gov/rcw-82-87-050"

    def add_row(doc: Any) -> None:
        row = dict(doc["statutes"][0], id="wa-rcw-82-87-050", section="82.87.050")
        row["source_url"] = source
        doc["statutes"].append(row)

    def add_entry(doc: Any) -> None:
        doc["entries"].append(dict(doc["entries"][0], id="wa-rcw-82-87-050", url=source))

    _edit(data / "statutes" / "wa.json", add_row)
    _edit(data / "manifest" / "wa.json", add_entry)
    changed = build_catalog.write(data, docs)
    # Only Washington's full-text file and the two indexes change with it.
    assert changed == [
        "docs/wa/rcw_82_87_050.md",
        "docs/llms.txt",
        "docs/wa/llms-full.txt",
        "docs/llms-full.txt",
    ]


def test_llms_full_is_an_index_of_per_jurisdiction_files() -> None:
    catalog = load_catalog()
    index = (REPO / "docs" / "llms-full.txt").read_text(encoding="utf-8")
    lines = [line for line in index.splitlines() if line.startswith("- [")]
    assert [line.split("]")[0][3:] for line in lines] == catalog.jurisdictions
    for code in catalog.jurisdictions:
        rows = [s for s in catalog.statutes if s["jurisdiction"] == code]
        noun = "section" if len(rows) == 1 else "sections"
        assert f"- [{code}](./{code.lower()}/llms-full.txt): {len(rows)} {noun}" in lines
        text = (REPO / "docs" / code.lower() / "llms-full.txt").read_text(encoding="utf-8")
        assert text.startswith(f"# Full Statutory Corpus: {code}\n")
        assert text.count("\n### English\n") == len(rows)
        for s in rows:
            assert s["text_en"] in text and s["text_es"] in text


def test_a_removed_jurisdiction_full_text_file_is_deleted(tree: tuple[Path, Path]) -> None:
    data, docs = tree
    (docs / "zz").mkdir()
    (docs / "zz" / "llms-full.txt").write_text("stale\n", encoding="utf-8")
    assert "docs: zz/llms-full.txt has no row in the data files" in build_catalog.check(data, docs)
    assert build_catalog.write(data, docs) == ["docs/zz/llms-full.txt (deleted)"]


def test_check_reports_schema_and_manifest_errors(tree: tuple[Path, Path]) -> None:
    data, docs = tree
    _edit(data / "manifest" / "ga.json", lambda d: d["entries"].pop())
    problems = build_catalog.check(data, docs)
    assert any(p.endswith(": no manifest entry") for p in problems)
    (data / "statutes" / "ga.json").write_text("{", encoding="utf-8")
    assert build_catalog.check(data, docs)[0].startswith("data: ga.json: invalid JSON")


def test_build_catalog_main_reports_problems(
    tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    data, docs = tree
    monkeypatch.setattr(build_catalog, "check", lambda: ["docs: x is missing"])
    assert build_catalog.main(["--check"]) == 1
    assert "1 problem(s)" in capsys.readouterr().out
    monkeypatch.setattr(build_catalog, "write", lambda: ["docs/x"])
    assert build_catalog.main([]) == 0
    assert "1 file(s) changed" in capsys.readouterr().out
    # Write mode also reports manifest errors, which it can't fix itself.
    monkeypatch.setattr(build_catalog, "manifest_problems", lambda: ["manifest/ga.json: bad"])
    assert build_catalog.main([]) == 1
    out = capsys.readouterr().out
    assert "[x] manifest/ga.json: bad" in out and "1 manifest problem(s)" in out


def test_write_mode_reports_a_real_manifest_error(tree: tuple[Path, Path]) -> None:
    data, _ = tree
    _edit(data / "manifest" / "ga.json", lambda d: d["entries"][0].update(edition="x"))
    assert any("edition must be one of" in p for p in build_catalog.manifest_problems(data))
    assert build_catalog.manifest_problems() == []


def _statute(doc: Any) -> dict[str, Any]:
    row: dict[str, Any] = doc["statutes"][0]
    return row


@pytest.mark.parametrize(
    ("path", "change", "message"),
    [
        ("jurisdictions.json", lambda d: d.append("GA"), "duplicate code"),
        ("jurisdictions.json", lambda d: d.append(7), "list of jurisdiction codes"),
        ("statutes/ga.json", lambda d: d.update(schema=2), "schema must be 1"),
        ("statutes/ga.json", lambda d: d.update(jurisdiction="NY"), "jurisdiction must be"),
        ("statutes/ga.json", lambda d: d.update(statutes={}), "statutes must be a list"),
        ("statutes/ga.json", lambda d: d.pop("statutes"), "fields must be exactly"),
        ("statutes/ga.json", lambda d: _statute(d).pop("title"), "fields must be exactly"),
        ("statutes/ga.json", lambda d: _statute(d).update(title=1), "title must be a string"),
        (
            "statutes/ga.json",
            lambda d: _statute(d).update(effective_year="2024"),
            "effective_year must be an integer",
        ),
        (
            "statutes/ga.json",
            lambda d: _statute(d).update(superseded_year=True),
            "superseded_year must be an integer or null",
        ),
        (
            "statutes/ga.json",
            lambda d: _statute(d).update(text_en="one string"),
            "text_en must be a list of strings",
        ),
        (
            "statutes/ga.json",
            lambda d: _statute(d).update(jurisdiction="NY"),
            "jurisdiction must be 'GA'",
        ),
        (
            "statutes/ga.json",
            lambda d: d["statutes"].append(dict(_statute(d))),
            "duplicate statute id",
        ),
        (
            "publications/ga.json",
            lambda d: d["publications"][0].update(id="xx-1"),
            "id must start with ga-",
        ),
        (
            "publications/ga.json",
            lambda d: d["publications"].append(dict(d["publications"][0], doc_number="new")),
            "duplicate publication id",
        ),
        (
            "publications/ga.json",
            lambda d: d["publications"].append(dict(d["publications"][0], id="ga-new")),
            "duplicate doc_number",
        ),
        ("manifest/ga.json", lambda d: d["entries"][0].pop("licence"), "fields must be exactly"),
        ("statutes/ga.json", lambda d: d.clear() or d.update(x=1), "fields must be exactly"),
    ],
)
def test_schema_errors_are_refused(
    tree: tuple[Path, Path], path: str, change: Callable[[Any], None], message: str
) -> None:
    data, _ = tree
    _edit(data / path, change)
    with pytest.raises(CatalogError, match=message):
        load_catalog(data, with_manifest=True)


def test_top_level_must_be_an_object_and_files_must_be_listed(tree: tuple[Path, Path]) -> None:
    data, _ = tree
    (data / "statutes" / "zz.json").write_text("{}", encoding="utf-8")
    with pytest.raises(CatalogError, match=r"files for no listed jurisdiction: \['statutes/zz"):
        load_catalog(data)
    (data / "statutes" / "zz.json").unlink()
    (data / "statutes" / "ga.json").write_text("[]", encoding="utf-8")
    with pytest.raises(CatalogError, match="top level must be an object"):
        load_catalog(data)


def test_a_jurisdiction_may_have_no_files_and_folders_are_optional(
    tree: tuple[Path, Path],
) -> None:
    data, _ = tree
    _edit(data / "jurisdictions.json", lambda d: d.append("CAN"))
    shutil.rmtree(data / "manifest")
    catalog = load_catalog(data, with_manifest=True)
    assert catalog.jurisdictions[-1] == "CAN" and catalog.manifest == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"kind": "rule"}, "kind must be one of"),
        ({"licence": ""}, "licence must be a non-empty string"),
        ({"official": "yes"}, "official must be true, false or 'not_recorded'"),
        ({"retrieved": "21 Oct 2026"}, "retrieved must be YYYY-MM-DD"),
        ({"retrieved": "2025-02-30"}, "retrieved must be YYYY-MM-DD"),
        ({"retrieved": "2025-13-01"}, "retrieved must be YYYY-MM-DD"),
        ({"effective_date": "2025-00-10"}, "effective_date must be YYYY-MM-DD"),
        ({"effective_date": "20250101"}, "effective_date must be YYYY-MM-DD"),
        ({"effective_date": "2025-01-01\n"}, "effective_date must be YYYY-MM-DD"),
        ({"effective_date": 2026}, "effective_date must be YYYY-MM-DD"),
        ({"content_sha256": "ABC"}, "content_sha256 must be 64 lowercase hex"),
        ({"verification": "checked"}, "verification must be one of"),
        ({"edition": "as_amended"}, "edition must be one of"),
        ({"edition": ["as_enacted"]}, "edition must be one of"),
        ({"verification": "verified_verbatim"}, "verified_verbatim needs a retrieved date"),
        ({"url": "https://example.gov/other"}, "url 'https://example.gov/other' != row"),
        ({"id": "ga-unknown"}, "no statute row with this id"),
    ],
)
def test_manifest_entries_are_checked_against_their_rows(
    tree: tuple[Path, Path], change: dict[str, Any], message: str
) -> None:
    data, _ = tree
    _edit(data / "manifest" / "ga.json", lambda d: d["entries"][0].update(change))
    errors = manifest_errors(load_catalog(data, with_manifest=True))
    assert any(message in e for e in errors), errors


def test_a_manifest_entry_without_an_edition_is_rejected(tree: tuple[Path, Path]) -> None:
    data, _ = tree
    _edit(data / "manifest" / "ga.json", lambda d: d["entries"][0].pop("edition"))
    with pytest.raises(CatalogError, match="fields must be exactly"):
        load_catalog(data, with_manifest=True)


def test_act_22_cites_the_amended_compilation_and_no_other_edition_is_recorded() -> None:
    catalog = load_catalog(with_manifest=True)
    editions = {e["id"]: e["edition"] for e in catalog.manifest if e["kind"] != "rate_table"}
    assert len(editions) == 160
    assert editions.pop("pr-act22-2012") == "amended_compilation"
    assert set(editions.values()) == {NOT_RECORDED}


def test_rate_tables_have_one_source_record_each_with_nothing_invented() -> None:
    catalog = load_catalog(with_manifest=True)
    entries = [e for e in catalog.manifest if e["kind"] == "rate_table"]
    assert [(e["_jurisdiction"], e["id"]) for e in entries] == [
        (code, rate_table_id(code)) for code in catalog.jurisdictions if code in RATE_TABLE_MODELS
    ]
    # A table cites a source only once its brackets were read from that official file:
    # then it records the url, official, the fetch date and (when the bytes were kept) their
    # hash, and nothing else. Every other table records nothing at all.
    sourced = {e["_jurisdiction"]: e for e in entries if e["url"] != NOT_RECORDED}
    assert sorted(sourced) == SOURCED_RATE_TABLES
    recorded = ("url", "official", "retrieved", "content_sha256")
    for e in entries:
        unrecorded = [
            name
            for name in MANIFEST_FIELDS[2:]
            if e["_jurisdiction"] not in sourced or name not in recorded
        ]
        assert {e[name] for name in unrecorded} == {NOT_RECORDED}, e["id"]
    for code, e in sourced.items():
        assert e["url"].startswith("https://") and e["official"] is True, code
        assert e["retrieved"] == RETRIEVED.get(code, "2026-10-22"), code
        assert (e["content_sha256"] == NOT_RECORDED) == (code in READ_WITHOUT_BYTES), code
        assert catalog.rate_tables[code]["source_url"] == e["url"]
    assert {t["source_url"] for c, t in catalog.rate_tables.items() if c not in sourced} == {
        NOT_RECORDED
    }
    assert manifest_errors(catalog) == []


SOURCED_2026_10_23 = "AZ CO GA ID IL IN KS KY MA MI MS NC ND OH PA UT VA WA".split()
SOURCED_RATE_TABLES = sorted(
    "AL AR DC DE HI IA LA ME MN MO MT NE NM OK OR RI SC VT WI WV".split() + SOURCED_2026_10_23
)
RETRIEVED = dict.fromkeys(SOURCED_2026_10_23, "2026-10-23")
# Sources that plain curl could not download (refused or failed), read as text: no hash.
READ_WITHOUT_BYTES = {"ME", "VT", "KY", "MI"}


def test_rate_table_entries_are_checked_against_their_tables(tree: tuple[Path, Path]) -> None:
    data, _ = tree

    def pop_rates(doc: Any) -> None:
        doc["entries"] = [e for e in doc["entries"] if e["kind"] != "rate_table"]

    _edit(data / "manifest" / "ga.json", pop_rates)
    _edit(data / "rates" / "ny.json", lambda d: d.update(source_url="https://example.gov/ny"))
    errors = manifest_errors(load_catalog(data, with_manifest=True))
    assert "rate_table 'ga-rates': no manifest entry" in errors
    assert (
        "manifest/ny.json entry 'ny-rates': url 'not_recorded' != row source_url "
        "'https://example.gov/ny'"
    ) in errors


def test_every_rate_table_state_must_be_a_listed_jurisdiction(tree: tuple[Path, Path]) -> None:
    data, _ = tree
    _edit(data / "jurisdictions.json", lambda d: d.remove("WY"))
    shutil.rmtree(data / "statutes")
    shutil.rmtree(data / "publications")
    shutil.rmtree(data / "manifest")
    with pytest.raises(CatalogError, match="rate table state 'WY' is missing"):
        load_catalog(data, with_manifest=True)


def test_a_fully_recorded_verbatim_entry_passes(tree: tuple[Path, Path]) -> None:
    data, _ = tree
    record = {
        "official": True,
        "edition": "as_enacted",
        "retrieved": "2026-10-21",
        "content_sha256": "0" * 64,
        "effective_date": "2026-01-01",
        "verification": "verified_verbatim",
    }
    _edit(data / "manifest" / "ga.json", lambda d: d["entries"][0].update(record))
    assert manifest_errors(load_catalog(data, with_manifest=True)) == []
    # Leap days are real dates.
    _edit(data / "manifest" / "ga.json", lambda d: d["entries"][0].update(retrieved="2024-02-29"))
    assert manifest_errors(load_catalog(data, with_manifest=True)) == []


def test_manifest_duplicates_and_wrong_files_are_reported(tree: tuple[Path, Path]) -> None:
    data, _ = tree
    entry: dict[str, Any] = {}

    def take(doc: Any) -> None:
        entry.update(doc["entries"].pop(0))

    _edit(data / "manifest" / "ga.json", take)
    _edit(data / "manifest" / "ny.json", lambda d: d["entries"].extend([entry, entry]))
    errors = manifest_errors(load_catalog(data, with_manifest=True))
    assert f"manifest/ny.json entry {entry['id']!r}: belongs in manifest/ga.json" in errors
    assert f"manifest/ny.json entry {entry['id']!r}: duplicate entry" in errors


def test_year_sources_are_checked_against_the_rate_table(tree: tuple[Path, Path]) -> None:
    data, _ = tree
    catalog = load_catalog(data, with_manifest=True)
    ky = next(e for e in catalog.manifest if e["id"] == "ky-rates")
    assert [s["tax_year"] for s in ky["year_sources"]] == [2025, 2026, 2026]
    assert manifest_errors(catalog) == []

    def ky_source(change: Callable[[dict[str, Any]], None]) -> str:
        def edit(doc: Any) -> None:
            entry = next(e for e in doc["entries"] if e["id"] == "ky-rates")
            change(entry)

        _edit(data / "manifest" / "ky.json", edit)
        errors = manifest_errors(load_catalog(data, with_manifest=True))
        shutil.copy(DATA_DIR / "manifest" / "ky.json", data / "manifest" / "ky.json")
        return "\n".join(errors)

    where = "manifest/ky.json entry 'ky-rates' year_sources"
    first = f"{where}[0]"
    cases: list[tuple[Callable[[dict[str, Any]], None], str]] = [
        (
            lambda e: e["year_sources"][0]["values"].update(rate=0.035),
            f"{first}: values: 'rate' is 0.035 here but 0.04 in the table",
        ),
        (
            lambda e: e["year_sources"][0]["values"].update(rate_2025=0.04),
            f"{first}: values: 'rate_2025' is not in the rate table",
        ),
        (
            lambda e: e["year_sources"][0]["values"].update(deduction_single_2025=3270),
            f"{first}: values: 'deduction_single_2025' is 3270 here but 3270.0 in the table",
        ),
        (lambda e: e["year_sources"][0].update(values={}), f"{first}: values must be a non-empty"),
        (lambda e: e["year_sources"][0].pop("official"), f"{first}: fields must be exactly"),
        (lambda e: e["year_sources"][0].update(official=False), f"{first}: official must be true"),
        (lambda e: e["year_sources"][0].update(url="http://x"), f"{first}: url must be an https"),
        (lambda e: e["year_sources"][0].update(retrieved="x"), f"{first}: retrieved must be"),
        (
            lambda e: e["year_sources"][0].update(content_sha256="ABC"),
            f"{first}: content_sha256 must be 64 lowercase hex",
        ),
        (
            lambda e: e["year_sources"][1].update(tax_year=2024),
            f"{where}[1]: tax_year must be an integer, in ascending order",
        ),
        (lambda e: e.update(year_sources=[]), f"{where[:-13]}: year_sources must be a non-empty"),
    ]
    for change, message in cases:
        assert message in ky_source(change), message

    # A statute entry may not carry them, and a rate table path may index into steps.
    def statute(doc: Any) -> None:
        doc["entries"][0]["year_sources"] = ky["year_sources"]

    _edit(data / "manifest" / "ga.json", statute)
    errors = manifest_errors(load_catalog(data, with_manifest=True))
    assert any("year_sources belongs only on a rate_table entry" in e for e in errors), errors
    co = next(e for e in catalog.manifest if e["id"] == "co-rates")
    assert co["year_sources"][1]["values"] == {"rates[0].rate": 0.044}


def test_statute_doc_path_matches_the_committed_pages() -> None:
    for s in load_catalog().statutes:
        assert (REPO / "docs" / statute_doc_path(s)).is_file(), s["id"]
