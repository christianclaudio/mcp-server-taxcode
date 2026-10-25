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
    TABLE_KINDS,
    CatalogError,
    dump_json,
    federal_table_document,
    load_catalog,
    load_federal_standard_deduction,
    manifest_errors,
    rate_table_id,
    split_lines,
    statute_doc_path,
    statute_document_row,
)

REPO = Path(__file__).resolve().parent.parent

# The prior-year federal statute versions: each records the official file it was read from.
# Two are summaries of an enacted law; the rest quote an official edition word for word.
PRIOR_YEAR_SUMMARIES = {"us-irc-1-2019", "us-irc-62-2020"}
PRIOR_YEAR_VERBATIM = {
    "us-irc-1-2020",
    "us-irc-24-2019",
    "us-irc-24-2021",
    "us-irc-32-2019",
    "us-irc-62-2019",
    "us-irc-62-2021",
    "us-irc-63-2019",
    "us-irc-63-2021",
    "us-cfr-1-937-3-2019",
}
PRIOR_YEAR_IDS = PRIOR_YEAR_SUMMARIES | PRIOR_YEAR_VERBATIM
# The prior-year Puerto Rico statute versions: each records the act it was read from, as
# enacted. Three quote Ley 60-2019 as approved word for word; the rest are summaries.
PR_PRIOR_YEAR_SUMMARIES = {
    "pr-cri-1031-02-2019",
    "pr-cri-1062-01-2019",
    "pr-cri-1062-03-2019",
    "pr-ley60-1020-02-2020",
}
PR_PRIOR_YEAR_VERBATIM = {
    "pr-ley60-1020-02-2019",
    "pr-ley60-2022-02-2019",
    "pr-ley60-6020-03-2019",
    "pr-ley60-6020-10-2019-as-enacted",
}
PR_PRIOR_YEAR_IDS = PR_PRIOR_YEAR_SUMMARIES | PR_PRIOR_YEAR_VERBATIM
# Ley 60-2019 Sec. 6020.10 split at Ley 52-2022 and Ley 32-2024: each records the act it quotes.
PR_SPLIT_IDS = {"pr-ley60-6020-10-2019", "pr-ley60-6020-10-2022", "pr-ley60-6020-10-2024"}


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
        len(catalog.statutes)
        + len(catalog.publications)
        + len(catalog.rate_tables)
        + len(catalog.federal_tables)
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
    # Only the prior-year versions were verified word for word against a fetched copy.
    by_verification: dict[str, set[str]] = {}
    for e in catalog.manifest:
        by_verification.setdefault(e["verification"], set()).add(e["id"])
    assert set(by_verification) == {"summary", NOT_RECORDED, "verified_verbatim"}
    assert by_verification["verified_verbatim"] == PRIOR_YEAR_VERBATIM | PR_PRIOR_YEAR_VERBATIM
    assert PRIOR_YEAR_SUMMARIES | PR_PRIOR_YEAR_SUMMARIES <= by_verification["summary"]
    # Besides those statute versions, only a table whose figures were read from a fetched
    # official file, or a publication whose source page was fetched, records a hash.
    statutes_hashed = {
        e["id"]
        for e in catalog.manifest
        if e["kind"] == "statute" and e["content_sha256"] != NOT_RECORDED
    }
    assert statutes_hashed == PRIOR_YEAR_IDS | PR_PRIOR_YEAR_IDS | PR_SPLIT_IDS
    hashed = {
        e["kind"]
        for e in catalog.manifest
        if e["content_sha256"] != NOT_RECORDED
        and e["id"] not in PRIOR_YEAR_IDS | PR_PRIOR_YEAR_IDS | PR_SPLIT_IDS
    }
    assert hashed == {"rate_table", "federal_table", "publication"}
    hashed_pubs = {
        e["id"]
        for e in catalog.manifest
        if e["kind"] == "publication" and e["content_sha256"] != NOT_RECORDED
    }
    assert hashed_pubs == {"ga-dor-hb-149-pte-tax-faq"}


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


def test_act_22_and_the_prior_year_versions_alone_record_an_edition() -> None:
    catalog = load_catalog(with_manifest=True)
    editions = {e["id"]: e["edition"] for e in catalog.manifest if e["kind"] not in TABLE_KINDS}
    assert len(editions) == 180
    assert editions.pop("pr-act22-2012") == "amended_compilation"
    # The prior-year federal versions name theirs: an annual code edition (or the eCFR) for
    # the verbatim ones, the enacted public law for the summaries.
    for row_id in sorted(PRIOR_YEAR_IDS):
        expected = "as_enacted" if row_id in PRIOR_YEAR_SUMMARIES else "amended_compilation"
        assert editions.pop(row_id) == expected, row_id
    # The prior-year Puerto Rico versions name the act they were read from, as enacted.
    for row_id in sorted(PR_PRIOR_YEAR_IDS | PR_SPLIT_IDS):
        assert editions.pop(row_id) == "as_enacted", row_id
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
    # Georgia's table also names the law that last amended it (HB 463 of 2026) and the first
    # tax year that law applies to.
    recorded_by_code = {"GA": (*recorded, "enacting_law", "effective_date")}
    for e in entries:
        unrecorded = [
            name
            for name in MANIFEST_FIELDS[2:]
            if e["_jurisdiction"] not in sourced
            or name not in recorded_by_code.get(e["_jurisdiction"], recorded)
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
    assert [s["tax_year"] for s in ky["year_sources"]] == [2025, 2026, 2026, 2026]
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
            lambda e: e["year_sources"][0]["values"].update({"rate[0]": 0.04}),
            f"{first}: values: 'rate[0]' is not in the rate table",
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
    belongs = "year_sources belongs only on a rate_table or federal_table entry"
    assert any(belongs in e for e in errors), errors
    co = next(e for e in catalog.manifest if e["id"] == "co-rates")
    assert co["year_sources"][4]["values"] == {"rates[0].rate": 0.044}
    # The #32 record for 2022 still names rates[2], now the 2022 step itself.
    assert co["year_sources"][3]["values"] == {"rates[2].rate": 0.044}
    assert catalog.rate_tables["CO"]["params"]["rates"][2]["from_year"] == 2022


def test_statute_doc_path_matches_the_committed_pages() -> None:
    for s in load_catalog().statutes:
        assert (REPO / "docs" / statute_doc_path(s)).is_file(), s["id"]


def test_federal_standard_deduction_holds_each_years_irs_figure() -> None:
    # Rev. Procs. 2018-57, 2019-44, 2020-45, 2021-45, 2022-38, 2023-34 and 2025-32; for 2025,
    # P.L. 119-21 § 70102 (the TY2025 Form 1040 instructions print $15,750 / $31,500).
    assert load_federal_standard_deduction() == {
        2019: {"single": 12200.0, "mfj": 24400.0},
        2020: {"single": 12400.0, "mfj": 24800.0},
        2021: {"single": 12550.0, "mfj": 25100.0},
        2022: {"single": 12950.0, "mfj": 25900.0},
        2023: {"single": 13850.0, "mfj": 27700.0},
        2024: {"single": 14600.0, "mfj": 29200.0},
        2025: {"single": 15750.0, "mfj": 31500.0},
        2026: {"single": 16100.0, "mfj": 32200.0},
    }
    doc = federal_table_document()
    assert (doc["jurisdiction"], doc["authority"]) == ("US", "26 U.S.C. § 63(c)")
    path = DATA_DIR / "federal" / "standard_deduction.json"
    assert path.read_text(encoding="utf-8") == dump_json(doc)


def _federal(data: Path, change: Callable[[Any], Any]) -> str:
    path = data / "federal" / "standard_deduction.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    replaced = change(doc)  # a list replaces the document; other return values are ignored
    path.write_text(dump_json(replaced if isinstance(replaced, list) else doc), encoding="utf-8")
    with pytest.raises(CatalogError) as raised:
        federal_table_document(data)
    shutil.copy(DATA_DIR / "federal" / "standard_deduction.json", path)
    return str(raised.value)


def test_malformed_federal_table_is_rejected(tree: tuple[Path, Path]) -> None:
    data, _ = tree
    where = "federal/standard_deduction.json"
    cases: list[tuple[Callable[[Any], Any], str]] = [
        (lambda d: [d], f"{where}: top level must be an object"),
        (lambda d: d.pop("authority"), f"{where}: fields must be exactly"),
        (lambda d: d.update(schema=2), f"{where}: schema must be 1"),
        (lambda d: d.update(jurisdiction="PR"), f"{where}: jurisdiction must be 'US'"),
        (lambda d: d.update(authority=""), f"{where}: authority must be a string"),
        (lambda d: d.update(source_url=None), f"{where}: source_url must be a string"),
        (lambda d: d.update(years=[]), f"{where}: years must be a non-empty list"),
        (lambda d: d["years"].__setitem__(0, 1), f"{where} years[0]: must be an object"),
        (lambda d: d["years"][1].pop("mfj"), f"{where} years[1]: fields must be exactly"),
        (lambda d: d["years"][0].update(tax_year="2019"), "years[0].tax_year: must be an int"),
        (lambda d: d["years"].pop(3), "years[3].tax_year: years must be consecutive"),
        (lambda d: d["years"].reverse(), "years[1].tax_year: years must be consecutive"),
        (lambda d: d["years"][6].update(single=15750), "years[6].single: must be a non-neg"),
    ]
    for change, message in cases:
        assert message in _federal(data, change), message
    (data / "federal" / "standard_deduction.json").unlink()
    with pytest.raises(CatalogError, match=f"{where}: missing"):
        federal_table_document(data)
    (data / "federal" / "extra.json").write_text("{}", encoding="utf-8")
    with pytest.raises(CatalogError, match=r"files for no federal table: \['federal/extra"):
        federal_table_document(data)


def test_georgia_rate_table_record_cites_hb_463_for_2026_and_2027(
    tree: tuple[Path, Path],
) -> None:
    data, _ = tree
    catalog = load_catalog(data, with_manifest=True)
    entry = next(e for e in catalog.manifest if e["id"] == "ga-rates")
    # The entry keeps the 2024 IT-511 (the unsuffixed values); HB 463 of 2026 last amended it.
    assert entry["url"] == (
        "https://dor.georgia.gov/document/document/2024-it-511-individual-income-tax-booklet/download"
    )
    assert entry["content_sha256"] == (
        "5907211db514b3a5c485bf372aac2b8be6f61aa61e6995b2d541005d63e8b481"
    )
    assert entry["enacting_law"] == "HB 463 of 2026"
    assert entry["effective_date"] == "2026-01-01"
    sources = entry["year_sources"]
    assert [s["tax_year"] for s in sources] == [2025, 2026, 2027]
    assert sources[0]["url"] == (
        "https://dor.georgia.gov/document/document/2025-it-511-individual-income-tax-booklet/download"
    )
    for source in sources[1:]:
        assert source["url"] == (
            "https://gov.georgia.gov/document/2026-signed-legislation/hb-463/download"
        )
        assert source["content_sha256"] == (
            "1c5fa7abcb2ab99964c09144384d774bceea9a1531ea235c7e637925459f6066"
        )
    assert manifest_errors(catalog) == []

    def edit(doc: Any) -> None:
        ga = next(e for e in doc["entries"] if e["id"] == "ga-rates")
        ga["year_sources"][1]["values"]["standard_deduction_2026.mfj"] = 31000.0

    _edit(data / "manifest" / "ga.json", edit)
    errors = manifest_errors(load_catalog(data, with_manifest=True))
    assert errors == [
        "manifest/ga.json entry 'ga-rates' year_sources[1]: values: "
        "'standard_deduction_2026.mfj' is 31000.0 here but 30000.0 in the table"
    ]


def test_outyear_rate_records_cite_their_sources(tree: tuple[Path, Path]) -> None:
    data, _ = tree
    catalog = load_catalog(data, with_manifest=True)
    sources = {
        e["id"]: e["year_sources"]
        for e in catalog.manifest
        if e["id"] in ("ms-rates", "ut-rates", "ne-rates")
    }
    dor = "https://www.dor.ms.gov/forms-resources/laws-regulations/2025-legislation"
    hb1 = "https://billstatus.ls.state.ms.us/documents/2025/html/HB/0001-0099/HB0001SG.htm"
    # Two sources per year from 2028 to 2030: the DOR page first, then HB 1 of 2025.
    ms = sources["ms-rates"][4:]
    assert [s["tax_year"] for s in ms] == [2028, 2028, 2029, 2029, 2030, 2030]
    assert [s["url"] for s in ms] == [dor, hb1] * 3
    assert [s["content_sha256"] for s in ms] == [
        "984bf9bb5898fb0018274747876e47b016cb31d60f3f808ee9681943c6c9d4da",
        "not_recorded",
    ] * 3
    assert [s["values"]["brackets_2029.single[0].rate"] for s in ms[2:4]] == [0.0325, 0.0325]
    ut = sources["ut-rates"][3:]
    assert [(s["tax_year"], s["values"]) for s in ut] == [(2026, {"rate_2026": 0.0445})] * 2
    assert ut[0]["url"] == "https://le.utah.gov/~2026/bills/sbillenr/SB0060.pdf"
    ne = sources["ne-rates"][-1]
    assert ne["tax_year"] == 2027 and ne["content_sha256"] == "not_recorded"
    assert set(ne["values"].values()) == {0.0399}
    assert manifest_errors(catalog) == []

    def edit(doc: Any) -> None:
        ms_rates = next(e for e in doc["entries"] if e["id"] == "ms-rates")
        ms_rates["year_sources"][6]["values"]["brackets_2029.single[0].rate"] = 0.03

    _edit(data / "manifest" / "ms.json", edit)
    assert manifest_errors(load_catalog(data, with_manifest=True)) == [
        "manifest/ms.json entry 'ms-rates' year_sources[6]: values: "
        "'brackets_2029.single[0].rate' is 0.03 here but 0.0325 in the table"
    ]
    _edit(data / "rates" / "ut.json", lambda d: d["params"].pop("rate_2026"))
    errors = manifest_errors(load_catalog(data, with_manifest=True))
    for i in (3, 4):
        assert (
            f"manifest/ut.json entry 'ut-rates' year_sources[{i}]: values: "
            "'rate_2026' is not in the rate table"
        ) in errors


def test_federal_table_has_one_manifest_record_with_a_source_per_year(
    tree: tuple[Path, Path],
) -> None:
    data, _ = tree
    catalog = load_catalog(data, with_manifest=True)
    entries = [e for e in catalog.manifest if e["kind"] == "federal_table"]
    assert [(e["_jurisdiction"], e["id"]) for e in entries] == [("US", "us-standard-deduction")]
    entry = entries[0]
    assert entry["url"] == "https://www.irs.gov/pub/irs-drop/rp-25-32.pdf"
    assert entry["url"] == catalog.federal_tables["us-standard-deduction"]["source_url"]
    assert entry["official"] is True and entry["retrieved"] == "2026-10-23"
    sources = entry["year_sources"]
    assert [s["tax_year"] for s in sources] == list(range(2019, 2027))
    assert sources[6]["url"] == "https://www.irs.gov/pub/irs-pdf/i1040gi.pdf"
    assert sources[6]["values"] == {"years[6].single": 15750.0, "years[6].mfj": 31500.0}
    assert catalog.documents["federal/standard_deduction"] is catalog.federal_tables[entry["id"]]
    assert manifest_errors(catalog) == []

    def us(change: Callable[[dict[str, Any]], None]) -> list[str]:
        def edit(doc: Any) -> None:
            change(next(e for e in doc["entries"] if e["kind"] == "federal_table"))

        _edit(data / "manifest" / "us.json", edit)
        errors = manifest_errors(load_catalog(data, with_manifest=True))
        shutil.copy(DATA_DIR / "manifest" / "us.json", data / "manifest" / "us.json")
        return errors

    where = "manifest/us.json entry 'us-standard-deduction'"
    errors = us(lambda e: e["year_sources"][6]["values"].update({"years[6].single": 15000.0}))
    assert errors == [
        f"{where} year_sources[6]: values: 'years[6].single' is 15000.0 here but 15750.0 in "
        "the table"
    ]
    errors = us(lambda e: e.update(url="https://www.irs.gov/pub/irs-drop/rp-24-40.pdf"))
    assert errors == [
        f"{where}: url 'https://www.irs.gov/pub/irs-drop/rp-24-40.pdf' != row source_url "
        "'https://www.irs.gov/pub/irs-drop/rp-25-32.pdf'"
    ]
    errors = us(lambda e: e.update(id="us-other"))
    assert errors == [
        "manifest/us.json entry 'us-other': no federal_table row with this id",
        "federal_table 'us-standard-deduction': no manifest entry",
    ]
