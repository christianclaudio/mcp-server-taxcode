"""Prior-year federal statute versions (tax years 2019-2025).

Each dated row answers only the years inside its window, or a lookup by its own id. A query
with no tax year still gets exactly the row it got before these versions were added.
"""

from __future__ import annotations

import copy
import hashlib
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb
import pytest

from taxcode_mcp.catalog import (
    DATA_DIR,
    WINDOW_GROUPS_ENDED,
    WINDOW_SPANS,
    CatalogError,
    load_catalog,
    load_federal_standard_deduction,
    window_span_errors,
)
from taxcode_mcp.db import (
    INITIAL_STATUTES,
    LEGACY_DATED_STATUTE_IDS,
    USLM_TITLE26_SOURCE_URL,
    TaxDatabase,
    is_official_text,
    statute_labels,
    uscode_edition,
    validity_label,
)
from taxcode_mcp.disclaimers import (
    CONTENT_TYPE_OFFICIAL_TEXT,
    CONTENT_TYPE_REFERENCE_SUMMARY,
    OFFICIAL_TEXT_LABEL,
    reference_labels,
)

REPO = Path(__file__).resolve().parent.parent
YEARS = range(2015, 2029)

# id: (effective_year, superseded_year, sha256[:16] of text_en, sha256[:16] of text_es)
PRIOR_YEAR_ROWS: dict[str, tuple[int, int, str, str]] = {
    "us-irc-1-2019": (2019, 2020, "d0a425fab5f9fb03", "d53b16cfc91a8191"),
    "us-irc-1-2020": (2020, 2026, "3e087ab443ea1668", "d820e918ab3a7e9f"),
    "us-irc-24-2019": (2019, 2021, "485a262862c2cac3", "949c73d352e05a8d"),
    "us-irc-24-2021": (2021, 2025, "f28f0eabee9989ce", "7b700d59c33e6268"),
    "us-irc-32-2019": (2019, 2021, "5d630f149cbfbd06", "f5351ad3472c04b9"),
    "us-irc-62-2019": (2019, 2020, "8834f2ef888fdc48", "d0edd5aea9fd9fc7"),
    "us-irc-62-2020": (2020, 2021, "5f6e41a11bdf488c", "c08bd5076bfbe8c4"),
    "us-irc-62-2021": (2021, 2026, "defd4bd43551cd27", "7d006996b7053253"),
    "us-irc-63-2019": (2019, 2021, "161d94edf1be49c6", "fc5b0346a319ba3c"),
    "us-irc-63-2021": (2021, 2025, "836d7d175ef84468", "83c701dcda4895aa"),
    "us-cfr-1-937-3-2019": (2019, 2026, "3d3c73c8797b6244", "9c1c6b72ca7888fd"),
}
PRIOR_YEAR_IDS = frozenset(PRIOR_YEAR_ROWS)
# sha256[:16] of the official file each version was read from (its manifest content_sha256).
PRIOR_YEAR_SOURCES: dict[str, str] = {
    "us-cfr-1-937-3-2019": "d617a70245485679",
    "us-irc-1-2019": "d724cc183016bdd5",
    "us-irc-1-2020": "0a194dc47fb9037d",
    "us-irc-24-2019": "98c6ad10ebd32c01",
    "us-irc-24-2021": "474b065ef11635ea",
    "us-irc-32-2019": "d7944b6321518e0b",
    "us-irc-62-2019": "441dd4916f8c4cd1",
    "us-irc-62-2020": "67f5deb7607b8a4d",
    "us-irc-62-2021": "f9a4a32ac115ee3a",
    "us-irc-63-2019": "304ed0ae5ba981a9",
    "us-irc-63-2021": "563e6fda638fe6b2",
}
SUMMARY_IDS = frozenset({"us-irc-1-2019", "us-irc-62-2020"})
VERBATIM_IDS = PRIOR_YEAR_IDS - SUMMARY_IDS

# (citation, bare section, current id) -> the row each tax year resolves to; a year not
# listed resolves to the current row, as every year did before these versions.
GROUPS: dict[tuple[str, str, str], dict[int, str]] = {
    ("26 U.S.C. § 1", "1", "us-irc-1"): {
        2019: "us-irc-1-2019",
        **dict.fromkeys(range(2020, 2026), "us-irc-1-2020"),
    },
    ("26 U.S.C. § 24", "24", "us-irc-24"): {
        **dict.fromkeys(range(2019, 2021), "us-irc-24-2019"),
        **dict.fromkeys(range(2021, 2025), "us-irc-24-2021"),
    },
    ("26 U.S.C. § 32", "32", "us-irc-32"): dict.fromkeys(range(2019, 2021), "us-irc-32-2019"),
    ("26 U.S.C. § 62", "62", "us-irc-62"): {
        2019: "us-irc-62-2019",
        2020: "us-irc-62-2020",
        **dict.fromkeys(range(2021, 2026), "us-irc-62-2021"),
    },
    ("26 U.S.C. § 63", "63", "us-irc-63"): {
        **dict.fromkeys(range(2019, 2021), "us-irc-63-2019"),
        **dict.fromkeys(range(2021, 2025), "us-irc-63-2021"),
    },
    ("26 CFR § 1.937-3", "1.937-3", "us-cfr-1-937-3"): dict.fromkeys(
        range(2019, 2026), "us-cfr-1-937-3-2019"
    ),
}

# The statutes table exactly as f4f0d5d (the last commit before these versions) created it.
F4F0D5D_STATUTES_DDL = """
    CREATE TABLE IF NOT EXISTS statutes (
        id VARCHAR PRIMARY KEY,
        jurisdiction VARCHAR,
        corpus VARCHAR,
        citation VARCHAR,
        section VARCHAR,
        title VARCHAR,
        text_es VARCHAR,
        text_en VARCHAR,
        effective_year INTEGER,
        superseded_year INTEGER,
        source_url VARCHAR,
        cross_references VARCHAR[]
    );
"""
F4F0D5D_DATED_IDS = {
    "ga-ocga-48-7-20-2024",
    "ga-ocga-48-7-20-2025",
    "la-rev-stat-47-32",
    "nh-rev-stat-77-1",
    "pr-act22-2012",
    "pr-ley60-2022-01-2019",
    "pr-ley60-6020-10-2019",
    "pr-ley60-6020-10-2025",
}
OLD_STATUTES = [s for s in INITIAL_STATUTES if s["id"] not in PRIOR_YEAR_IDS]
NO_YEAR_QUERIES = ["", "income", "credit", "deduction", "residente", "deducción", "ingreso"]
FEDERAL_SUMMARY_PREFIX_EN = "Summary; the text of this version is not in the catalog. Enacted by "
FEDERAL_SUMMARY_PREFIX_ES = (
    "Resumen; el texto de esta versión no está en el catálogo. Promulgado por la Ley Pública "
)
FEDERAL_VERBATIM_PREFIX_ES = "Resumen en español; el texto oficial está en inglés."


def _sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _rows() -> dict[str, dict[str, Any]]:
    return {s["id"]: s for s in INITIAL_STATUTES}


def _manifest() -> dict[str, dict[str, Any]]:
    return {e["id"]: e for e in load_catalog(with_manifest=True).manifest}


def _no_year_answers(db: TaxDatabase) -> list[Any]:
    """Every no-year lookup of a touched group and every no-year search, in order."""
    answers: list[Any] = []
    for (citation, section, current), _ in GROUPS.items():
        for query in (citation, section, current):
            for jurisdiction in ("US", None):
                for lang in ("en", "es"):
                    row = db.lookup_statute(query, jurisdiction, None, lang)
                    # The version lists grew; everything else must match.
                    assert row is not None
                    answers.append({k: v for k, v in row.items() if k != "available_versions"})
    for query in NO_YEAR_QUERIES:
        for jurisdiction in (None, "US", "PR"):
            for lang in ("en", "es"):
                for offset in (0, 5):
                    answers.append(
                        db.search_statutes(
                            query, jurisdiction=jurisdiction, lang=lang, limit=50, offset=offset
                        )
                    )
    return answers


@pytest.fixture(scope="module")
def db() -> TaxDatabase:
    return TaxDatabase(":memory:")


# --- No-year answers (R1) ------------------------------------------------------------------


def test_the_legacy_dated_ids_are_exactly_the_dated_rows_before_prior_years() -> None:
    assert LEGACY_DATED_STATUTE_IDS == F4F0D5D_DATED_IDS
    assert {s["id"] for s in OLD_STATUTES if s["superseded_year"] is not None} == F4F0D5D_DATED_IDS
    assert not LEGACY_DATED_STATUTE_IDS & PRIOR_YEAR_IDS


def test_a_lookup_with_no_year_returns_the_current_row(db: TaxDatabase) -> None:
    for citation, section, current in GROUPS:
        for query in (citation, section, current):
            for jurisdiction in ("US", None):
                row = db.lookup_statute(query, jurisdiction)
                assert row is not None and row["id"] == current, (query, jurisdiction)
                assert row["is_current"]


def test_a_prior_year_id_with_no_year_names_itself(db: TaxDatabase) -> None:
    for row_id, (effective, superseded, _, _) in PRIOR_YEAR_ROWS.items():
        row = db.lookup_statute(row_id)
        assert row is not None and row["id"] == row_id
        assert (row["effective_year"], row["superseded_year"]) == (effective, superseded)
        assert not row["is_current"]


def test_a_search_with_no_year_lists_no_prior_year_version(db: TaxDatabase) -> None:
    for query in NO_YEAR_QUERIES:
        for jurisdiction in (None, "US"):
            for lang in ("en", "es"):
                ids = {
                    r["id"] for r in db.search_statutes(query, jurisdiction, lang=lang, limit=500)
                }
                assert not ids & PRIOR_YEAR_IDS, (query, jurisdiction, lang)


def test_no_year_answers_match_a_database_without_the_prior_year_rows(db: TaxDatabase) -> None:
    old = TaxDatabase(":memory:")
    for row_id in PRIOR_YEAR_IDS:
        assert old.delete_statute(row_id)
    assert _no_year_answers(db) == _no_year_answers(old)


def test_available_versions_list_every_version(db: TaxDatabase) -> None:
    for (citation, _, current), by_year in GROUPS.items():
        row = db.lookup_statute(citation, "US")
        assert row is not None
        ids = [v["id"] for v in row["available_versions"]]
        assert sorted(ids) == sorted({current, *by_year.values()})


# --- Storage, upgrade and writers (R1a) ------------------------------------------------------


def _write_f4f0d5d_file(path: Path) -> None:
    conn = duckdb.connect(str(path))
    try:
        conn.execute(F4F0D5D_STATUTES_DDL)
        for s in OLD_STATUTES:
            conn.execute(
                "INSERT INTO statutes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    s["id"],
                    s["jurisdiction"],
                    s["corpus"],
                    s["citation"],
                    s["section"],
                    s["title"],
                    s["text_es"],
                    s["text_en"],
                    s["effective_year"],
                    s["superseded_year"],
                    s["source_url"],
                    s["cross_references"],
                ],
            )
    finally:
        conn.close()


def test_a_database_file_from_before_prior_years_upgrades_on_open(
    tmp_path: Path, db: TaxDatabase
) -> None:
    path = tmp_path / "f4f0d5d.duckdb"
    _write_f4f0d5d_file(path)
    upgraded = TaxDatabase(str(path))
    assert upgraded.get_stats()[0] == len(INITIAL_STATUTES)
    assert _no_year_answers(upgraded) == _no_year_answers(db)
    for (citation, _, _), by_year in GROUPS.items():
        for year, row_id in by_year.items():
            row = upgraded.lookup_statute(citation, "US", year)
            assert row is not None and row["id"] == row_id


def test_every_writer_keeps_a_prior_year_rows_superseded_year(db: TaxDatabase) -> None:
    written = TaxDatabase(":memory:")
    rows = _rows()
    for row_id in PRIOR_YEAR_IDS:
        assert written.delete_statute(row_id)
    for n, row_id in enumerate(sorted(PRIOR_YEAR_IDS - {"us-irc-24-2021"})):
        writer = written.insert_statute if n % 2 else written.upsert_statute
        writer(dict(rows[row_id]))
    # A re-upsert takes the ON CONFLICT ... DO UPDATE path, and must carry superseded_year
    # over a stored copy that lacks it (else the window row would turn current).
    written.upsert_statute({**rows["us-irc-24-2021"], "superseded_year": None})
    written.upsert_statute(dict(rows["us-irc-24-2021"]))
    for row_id, (effective, superseded, _, _) in PRIOR_YEAR_ROWS.items():
        row = written.lookup_statute(row_id)
        assert row is not None
        assert (row["effective_year"], row["superseded_year"]) == (effective, superseded)
    assert _no_year_answers(written) == _no_year_answers(db)


# --- Year pins ---------------------------------------------------------------------------


def test_each_tax_year_resolves_to_its_version(db: TaxDatabase) -> None:
    rows = _rows()
    for (citation, section, current), by_year in GROUPS.items():
        for year in YEARS:
            expected = by_year.get(year, current)
            for query in (citation, section):
                for jurisdiction in ("US", None):
                    for lang in ("en", "es"):
                        row = db.lookup_statute(query, jurisdiction, year, lang)
                        assert row is not None and row["id"] == expected, (query, year)
                        text = rows[expected]["text_en" if lang == "en" else "text_es"]
                        assert row["operative_text"] == text
            if expected in PRIOR_YEAR_ROWS:
                effective, superseded, sha_en, sha_es = PRIOR_YEAR_ROWS[expected]
                assert (row["effective_year"], row["superseded_year"]) == (effective, superseded)
                assert row["validity"] == validity_label(effective, superseded)
                assert row["validity"]["en"] == f"Effective {effective}; superseded {superseded}"
                assert row["validity"]["es"] == (
                    f"Vigente desde {effective}; sustituida en {superseded}"
                )
                assert (_sha16(row["text_en"]), _sha16(row["text_es"])) == (sha_en, sha_es)


def test_an_id_keyed_lookup_of_a_current_row_still_names_it(db: TaxDatabase) -> None:
    for _, _, current in GROUPS:
        for year in YEARS:
            row = db.lookup_statute(current, "US", year)
            assert row is not None and row["id"] == current


def test_a_search_for_a_window_year_finds_that_years_version(db: TaxDatabase) -> None:
    ids = {r["id"] for r in db.search_statutes("", "US", tax_year=2020, limit=500)}
    assert {"us-irc-1-2020", "us-irc-24-2019", "us-irc-62-2020", "us-irc-63-2019"} <= ids
    # Current rows stay listed too (they hold for every year since they took effect); a
    # version for other years does not.
    assert "us-irc-24" in ids
    assert not {"us-irc-1-2019", "us-irc-24-2021", "us-irc-62-2021"} & ids
    ids = {r["id"] for r in db.search_statutes("", "US", tax_year=2026, limit=500)}
    assert not ids & PRIOR_YEAR_IDS


# --- Catalog invariant (R4, R4a) -------------------------------------------------------------


def test_the_real_catalog_keeps_every_window_rule() -> None:
    assert window_span_errors(load_catalog().statutes) == []
    assert WINDOW_GROUPS_ENDED <= set(WINDOW_SPANS)


def _mutated(change: Callable[[dict[str, dict[str, Any]]], None]) -> list[str]:
    statutes = copy.deepcopy(load_catalog().statutes)
    by_id = {s["id"]: s for s in statutes}
    change(by_id)
    return window_span_errors(list(by_id.values()))


def _add_current(row_id: str, new_id: str, effective: int) -> Callable[[dict[str, Any]], None]:
    def change(rows: dict[str, Any]) -> None:
        rows[new_id] = {**rows[row_id], "id": new_id, "effective_year": effective}
        rows[new_id]["superseded_year"] = None

    return change


def _set(row_id: str, **values: Any) -> Callable[[dict[str, Any]], None]:
    return lambda rows: rows[row_id].update(values)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        # M1, rule 3: a window starts before its span.
        (_set("us-irc-1-2019", effective_year=2018), "start in 2018, not 2019"),
        # M2, rule 3: a window ends after its span.
        (_set("us-irc-24-2021", superseded_year=2026), "end in 2026, not 2025"),
        # M6, rule 1: two windows overlap.
        (_set("us-irc-62-2020", effective_year=2019), "us-irc-62-2019 and us-irc-62-2020 overlap"),
        # M14, rule 3: the first window is gone, so its years fall through to current text.
        (lambda rows: rows.pop("us-irc-24-2019"), "start in 2021, not 2019"),
        # M16, rule 3 (and rule 4: 2025 uncovered).
        (_set("us-irc-62-2021", superseded_year=2025), "end in 2025, not 2026"),
        # M17, rule 3: the current row lies underneath, so only the span's end catches it.
        (_set("us-irc-63-2021", superseded_year=2026), "end in 2026, not 2025"),
        # M22, rule 3: TY2025 would fall through to the current regulation.
        (_set("us-cfr-1-937-3-2019", superseded_year=2025), "end in 2025, not 2026"),
        # Rule 4: a gap inside the span.
        (lambda rows: rows.pop("us-irc-62-2020"), "tax year 2020 is covered by 0 dated rows"),
        # Rule 2: two current rows, and a current row in an ended group.
        (_add_current("us-irc-24", "us-irc-24-copy", 1997), "2 current rows"),
        (_add_current("nh-rev-stat-77-1", "nh-rev-stat-77-1-now", 2025), "but has a current row"),
        # Rule 5: a current row starting inside the span.
        (_set("us-irc-24", effective_year=2022), "starts in 2022, inside the dated span"),
        # Rule 6: a dated row in a group the spans do not list.
        (_set("us-irc-911", superseded_year=2030), "dated rows but no WINDOW_SPANS entry"),
        # Rule 6: a span whose group has no dated row left.
        (lambda rows: rows.pop("us-irc-32-2019"), "WINDOW_SPANS entry but no dated row"),
    ],
)
def test_each_broken_window_rule_is_reported(
    change: Callable[[dict[str, Any]], None], message: str
) -> None:
    errors = _mutated(change)
    assert any(message in e for e in errors), errors


def test_a_current_row_that_chains_at_the_span_end_passes_rule_5() -> None:
    # M11: current section 24 starting in 2025 is a valid chain, so the window rules accept
    # it; the existing-years pin is what catches the 2015-2018 answers it would change.
    assert _mutated(_set("us-irc-24", effective_year=2025)) == []


def test_load_catalog_refuses_a_broken_window(tmp_path: Path) -> None:
    data = tmp_path / "data"
    shutil.copytree(DATA_DIR, data)
    path = data / "statutes" / "us.json"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            '"superseded_year": 2025,\n      "source_url": "https://www.govinfo.gov/content/pkg/'
            "USCODE-2021-title26/html/USCODE-2021-title26-subtitleA-chap1-subchapA-partIV-"
            'subpartA-sec24.htm"',
            '"superseded_year": 2026,\n      "source_url": "https://www.govinfo.gov/content/pkg/'
            "USCODE-2021-title26/html/USCODE-2021-title26-subtitleA-chap1-subchapA-partIV-"
            'subpartA-sec24.htm"',
        ),
        encoding="utf-8",
    )
    with pytest.raises(CatalogError, match="statute versions: .*end in 2026, not 2025"):
        load_catalog(data)


# --- Labels (R6) ---------------------------------------------------------------------------


def test_every_summary_is_labelled_a_reference_summary() -> None:
    rows = _rows()
    for row_id, entry in _manifest().items():
        if entry["kind"] == "statute" and entry["verification"] == "summary":
            labels = statute_labels(rows[row_id]["source_url"])
            assert labels["content_type"] == CONTENT_TYPE_REFERENCE_SUMMARY, row_id


def test_official_text_rows_were_verified_word_for_word() -> None:
    manifest = _manifest()
    for s in INITIAL_STATUTES:
        if uscode_edition(s["source_url"]) is not None:
            assert manifest[s["id"]]["verification"] == "verified_verbatim", s["id"]
    # Every prior-year row quoting a code edition is labelled official text.
    rows = _rows()
    for row_id in VERBATIM_IDS - {"us-cfr-1-937-3-2019"}:
        labels = statute_labels(rows[row_id]["source_url"])
        assert labels["content_type"] == CONTENT_TYPE_OFFICIAL_TEXT, row_id


def test_prior_year_summaries_cite_the_public_law_and_no_code_edition() -> None:
    rows = _rows()
    manifest = _manifest()
    for row_id in PRIOR_YEAR_IDS:
        url = rows[row_id]["source_url"]
        assert manifest[row_id]["url"] == url
        if manifest[row_id]["verification"] == "summary":
            assert "USCODE-" not in url and "PLAW-" in url, row_id
            assert not is_official_text(url)
    assert {i for i in PRIOR_YEAR_IDS if manifest[i]["verification"] == "summary"} == SUMMARY_IDS


@pytest.mark.parametrize(
    "url",
    [
        "https://www.govinfo.gov/content/pkg/PLAW-116publ136/pdf/PLAW-116publ136.pdf",
        "https://www.govinfo.gov/content/pkg/USCODE-2018-title26/html/USCODE-2018-title26.htm",
        "https://www.govinfo.gov/content/pkg/",
        "https://www.govinfo.gov/content/pkg/USCODE-2019-title26/pdf/USCODE-2019-title26.pdf",
        "",
        None,
    ],
)
def test_the_official_text_allowlist_rejects_other_sources(url: str | None) -> None:
    assert not is_official_text(url)
    assert uscode_edition(url) is None


def test_edition_rows_name_their_edition_and_current_rows_keep_their_label(
    db: TaxDatabase,
) -> None:
    row = db.lookup_statute("us-irc-24-2021")
    assert row is not None and row["content_type"] == CONTENT_TYPE_OFFICIAL_TEXT
    assert row["content_label"] == {
        "en": "Official statutory text (US Code, 2021 edition, GPO).",
        "es": "Texto estatutario oficial (Código de los EE. UU., edición de 2021, GPO).",
    }
    row = db.lookup_statute("26 U.S.C. § 63", "US", 2019)
    assert row is not None and "2019 edition" in row["content_label"]["en"]
    row = db.lookup_statute("us-irc-24")
    assert row is not None and row["content_label"] == OFFICIAL_TEXT_LABEL
    assert is_official_text(USLM_TITLE26_SOURCE_URL)
    assert reference_labels(official_text=True)["content_label"] == OFFICIAL_TEXT_LABEL
    results = db.search_statutes("credit", "US", tax_year=2019, limit=50)
    labels = {r["id"]: r["content_label"]["en"] for r in results}
    assert "2019 edition" in labels["us-irc-24-2019"]


# --- Languages -----------------------------------------------------------------------------


def test_each_language_says_what_it_holds() -> None:
    rows = _rows()
    for row_id in VERBATIM_IDS:
        assert rows[row_id]["text_es"].startswith(FEDERAL_VERBATIM_PREFIX_ES), row_id
        assert not rows[row_id]["text_en"].startswith("Summary"), row_id
    laws = {
        "us-irc-62-2020": ("Pub. L. 116-136, §2204.", "Ley Pública 116-136, §2204."),
        "us-irc-1-2019": (
            "Pub. L. 116-94, div. O, §501(a).",
            "Ley Pública 116-94, div. O, §501(a).",
        ),
    }
    for row_id, (law_en, law_es) in laws.items():
        en, es = rows[row_id]["text_en"], rows[row_id]["text_es"]
        assert en.startswith(FEDERAL_SUMMARY_PREFIX_EN), row_id
        assert es.startswith(FEDERAL_SUMMARY_PREFIX_ES), row_id
        assert law_en in en.split("\n", 1)[0] and law_es in es.split("\n", 1)[0], row_id


def _normalized(text: str) -> str:
    return " ".join(text.split())


def copies_current_text(row: dict[str, Any], current: dict[str, Any]) -> bool:
    """True when a prior-year row holds the current row's text in either language."""
    return any(_normalized(row[k]) == _normalized(current[k]) for k in ("text_en", "text_es"))


def test_no_earlier_year_is_given_the_current_text() -> None:
    rows = _rows()
    for (_, _, current), by_year in GROUPS.items():
        for row_id in set(by_year.values()):
            assert not copies_current_text(rows[row_id], rows[current]), row_id
    # M3: a current text copied into a window row is caught.
    copied = {**rows["us-irc-63-2019"], "text_en": rows["us-irc-63"]["text_en"]}
    assert copies_current_text(copied, rows["us-irc-63"])


# --- Figures -------------------------------------------------------------------------------


def test_standard_deduction_figures_and_their_sources_are_pinned() -> None:
    table = load_federal_standard_deduction()
    assert {y: (v["single"], v["mfj"]) for y, v in table.items() if y <= 2025} == {
        2019: (12200.0, 24400.0),
        2020: (12400.0, 24800.0),
        2021: (12550.0, 25100.0),
        2022: (12950.0, 25900.0),
        2023: (13850.0, 27700.0),
        2024: (14600.0, 29200.0),
        2025: (15750.0, 31500.0),
    }
    sources = {
        s["tax_year"]: s["content_sha256"][:16]
        for s in _manifest()["us-standard-deduction"]["year_sources"]
    }
    assert sources == {
        2019: "179b2103dc52faf1",
        2020: "4128892f6a94ff00",
        2021: "12cfb00115b86e3b",
        2022: "405535647b628710",
        2023: "6c20f8a6c9a3bdf4",
        2024: "544ba41c7ba64f77",
        2025: "482e9c487c608f1b",
        2026: "e9ada115fb43a4af",
    }


def test_each_section_63_window_holds_its_own_figures() -> None:
    rows = _rows()
    old = rows["us-irc-63-2019"]["text_en"]
    assert "$12,000" in old and "2018 through 2025" in old
    assert "2018 through 2025" in rows["us-irc-63-2021"]["text_en"]
    assert "2018 through 2025" not in rows["us-irc-63"]["text_en"]


def test_prior_year_sources_are_pinned() -> None:
    manifest = _manifest()
    assert {
        i: manifest[i]["content_sha256"][:16] for i in sorted(PRIOR_YEAR_IDS)
    } == PRIOR_YEAR_SOURCES


# --- README --------------------------------------------------------------------------------


def test_readme_counts_match_the_database(db: TaxDatabase) -> None:
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    found = re.findall(r"\((\d+) Statutes, (\d+) Publications,", readme)
    assert found == [tuple(str(n) for n in db.get_stats())]
