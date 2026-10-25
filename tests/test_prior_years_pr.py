"""Prior-year Puerto Rico statute versions (tax years 2019-2025).

Each dated row answers only the years inside its window, or a lookup by its own id. A query
with no tax year still gets exactly the row it got before these versions were added.
"""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Callable
from typing import Any

import pytest

from taxcode_mcp.catalog import load_catalog, window_span_errors
from taxcode_mcp.db import INITIAL_STATUTES, TaxDatabase, is_official_text, statute_labels
from taxcode_mcp.disclaimers import CONTENT_TYPE_REFERENCE_SUMMARY

YEARS = range(2015, 2029)
OGP = "https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF"
LEY60 = f"{OGP}/2019/0060-2019.pdf"
LEY40 = f"{OGP}/2020/0040-2020.pdf"
LEY93 = f"{OGP}/2020/0093-2026.pdf"

# id: (effective_year, superseded_year, sha256[:16] of text_en, sha256[:16] of text_es)
PR_ROWS: dict[str, tuple[int, int, str, str]] = {
    "pr-cri-1031-02-2019": (2019, 2026, "20921c2f7f8de81d", "76adf17df8dc9a5e"),
    "pr-cri-1062-01-2019": (2019, 2020, "35baf59ebe8c3308", "17602716421fca5e"),
    "pr-cri-1062-03-2019": (2019, 2020, "f39257191e5675fc", "5efa928cf7cb5d72"),
    "pr-ley60-1020-02-2019": (2019, 2020, "82cea6c48a6a65da", "9f01e7d1326bc077"),
    "pr-ley60-1020-02-2020": (2020, 2026, "aef2eb9451e76e64", "53a73d07edbfa982"),
    "pr-ley60-2022-02-2019": (2019, 2026, "6fa128e1f47de104", "c3c73ec41141e5f4"),
    "pr-ley60-6020-03-2019": (2019, 2026, "f1e8f6ad700311be", "04a3f3a8b95bbff7"),
}
PR_IDS = frozenset(PR_ROWS)
VERBATIM_IDS = frozenset(
    {"pr-ley60-1020-02-2019", "pr-ley60-2022-02-2019", "pr-ley60-6020-03-2019"}
)
SUMMARY_IDS = PR_IDS - VERBATIM_IDS
# id: (source_url, sha256[:16] of that file, enacting_law starts with, effective_date)
PR_SOURCES: dict[str, tuple[str, str, str, str]] = {
    "pr-cri-1031-02-2019": (LEY93, "9adcfbe63b8bc9f2", "Ley 93-2026, Sec. 1", "not_recorded"),
    "pr-cri-1062-01-2019": (LEY40, "39f6aac5879401fc", "Ley 40-2020, Sec. 33", "not_recorded"),
    "pr-cri-1062-03-2019": (LEY40, "39f6aac5879401fc", "Ley 40-2020, Sec. 35", "not_recorded"),
    "pr-ley60-1020-02-2019": (LEY60, "21cd65faf8bb83e0", "Ley 60-2019", "2019-07-01"),
    "pr-ley60-1020-02-2020": (LEY40, "39f6aac5879401fc", "Ley 40-2020, Sec. 74", "2020-04-16"),
    "pr-ley60-2022-02-2019": (LEY60, "21cd65faf8bb83e0", "Ley 60-2019", "2019-07-01"),
    "pr-ley60-6020-03-2019": (LEY60, "21cd65faf8bb83e0", "Ley 60-2019", "2019-07-01"),
}

# (citation, bare section, current id) -> the row each tax year resolves to; a year not
# listed resolves to the current row, as every year did before these versions.
GROUPS: dict[tuple[str, str, str], dict[int, str]] = {
    ("PR CRI Sec. 1031.02", "1031.02", "pr-cri-1031-02"): dict.fromkeys(
        range(2019, 2026), "pr-cri-1031-02-2019"
    ),
    ("PR CRI Sec. 1062.01", "1062.01", "pr-cri-1062-01"): {2019: "pr-cri-1062-01-2019"},
    ("PR CRI Sec. 1062.03", "1062.03", "pr-cri-1062-03"): {2019: "pr-cri-1062-03-2019"},
    ("Ley 60-2019 Sec. 1020.02", "1020.02", "pr-ley60-1020-02"): {
        2019: "pr-ley60-1020-02-2019",
        **dict.fromkeys(range(2020, 2026), "pr-ley60-1020-02-2020"),
    },
    ("Ley 60-2019 Sec. 2022.02", "2022.02", "pr-ley60-2022-02"): dict.fromkeys(
        range(2019, 2026), "pr-ley60-2022-02-2019"
    ),
    ("Ley 60-2019 Sec. 6020.03", "6020.03", "pr-ley60-6020-03"): dict.fromkeys(
        range(2019, 2026), "pr-ley60-6020-03-2019"
    ),
}
# Before 2026 Ley 60-2019 sections are not in the catalog for 2015-2018; the Code sections
# (Ley 1-2011) are, through their current rows.
NOT_FOUND_BEFORE_2019 = frozenset({"pr-ley60-1020-02", "pr-ley60-2022-02", "pr-ley60-6020-03"})
NO_YEAR_QUERIES = ["", "exempt", "Puerto Rico", "residente", "ingreso", "exención", "retención"]
OGP_PREFIX_EN = "Unofficial OGP English translation; the official text is in Spanish."
SUMMARY_PREFIX_EN = "Summary; the text of this version is not in the catalog. Enacted by "
SUMMARY_PREFIX_ES = "Resumen; el texto de esta versión no está en el catálogo. Promulgado por "
SET_DATE = "starts in the year of the act's set start date ({y}): the act takes effect on {d}"
APPROVAL = "starts in the approval year ({y}): the act takes effect on approval"
TY2019 = "This version starts in tax year 2019, the first tax year the catalog versions"


def _sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _rows() -> dict[str, dict[str, Any]]:
    return {s["id"]: s for s in INITIAL_STATUTES}


def _notes() -> dict[str, str]:
    return {s["id"]: s["source_note"] for s in load_catalog().statutes}


def _manifest() -> dict[str, dict[str, Any]]:
    return {e["id"]: e for e in load_catalog(with_manifest=True).manifest}


@pytest.fixture(scope="module")
def db() -> TaxDatabase:
    return TaxDatabase(":memory:")


def _no_year_answers(db: TaxDatabase) -> list[Any]:
    """Every no-year lookup of a touched group and every no-year PR search, in order."""
    answers: list[Any] = []
    for citation, section, current in GROUPS:
        for query in (citation, section, current):
            for jurisdiction in ("PR", None):
                for lang in ("en", "es"):
                    row = db.lookup_statute(query, jurisdiction, None, lang)
                    assert row is not None
                    answers.append({k: v for k, v in row.items() if k != "available_versions"})
    for query in NO_YEAR_QUERIES:
        for jurisdiction in (None, "PR"):
            for lang in ("en", "es"):
                for offset in (0, 5):
                    answers.append(
                        db.search_statutes(
                            query, jurisdiction=jurisdiction, lang=lang, limit=50, offset=offset
                        )
                    )
    return answers


# --- No-year answers -----------------------------------------------------------------------


def test_a_lookup_with_no_year_returns_the_current_row(db: TaxDatabase) -> None:
    for citation, section, current in GROUPS:
        for query in (citation, section, current):
            for jurisdiction in ("PR", None):
                row = db.lookup_statute(query, jurisdiction)
                assert row is not None and row["id"] == current, (query, jurisdiction)
                assert row["is_current"]


def test_a_prior_year_id_with_no_year_names_itself(db: TaxDatabase) -> None:
    for row_id, (effective, superseded, _, _) in PR_ROWS.items():
        row = db.lookup_statute(row_id)
        assert row is not None and row["id"] == row_id
        assert (row["effective_year"], row["superseded_year"]) == (effective, superseded)
        assert not row["is_current"]


def test_a_search_with_no_year_lists_no_prior_year_version(db: TaxDatabase) -> None:
    for query in NO_YEAR_QUERIES:
        for jurisdiction in (None, "PR"):
            for lang in ("en", "es"):
                ids = {
                    r["id"] for r in db.search_statutes(query, jurisdiction, lang=lang, limit=500)
                }
                assert not ids & PR_IDS, (query, jurisdiction, lang)


def test_no_year_answers_match_a_database_without_the_new_rows(db: TaxDatabase) -> None:
    old = TaxDatabase(":memory:")
    for row_id in PR_IDS:
        assert old.delete_statute(row_id)
    assert _no_year_answers(db) == _no_year_answers(old)


def test_available_versions_list_every_version(db: TaxDatabase) -> None:
    for (citation, _, current), by_year in GROUPS.items():
        row = db.lookup_statute(citation, "PR")
        assert row is not None
        ids = [v["id"] for v in row["available_versions"]]
        assert sorted(ids) == sorted({current, *by_year.values()})


# --- Year pins -----------------------------------------------------------------------------


def test_each_tax_year_resolves_to_its_version(db: TaxDatabase) -> None:
    rows = _rows()
    for (citation, section, current), by_year in GROUPS.items():
        for year in YEARS:
            expected: str | None = by_year.get(year, current)
            if expected == current and year < 2019 and current in NOT_FOUND_BEFORE_2019:
                expected = None
            for query in (citation, section):
                for jurisdiction in ("PR", None):
                    for lang in ("en", "es"):
                        row = db.lookup_statute(query, jurisdiction, year, lang)
                        if expected is None:
                            assert row is None, (query, year)
                            continue
                        assert row is not None and row["id"] == expected, (query, year)
                        text = rows[expected]["text_en" if lang == "en" else "text_es"]
                        assert row["operative_text"] == text
            if expected in PR_ROWS:
                effective, superseded, sha_en, sha_es = PR_ROWS[expected]
                assert row is not None
                assert (row["effective_year"], row["superseded_year"]) == (effective, superseded)
                assert row["validity"]["en"] == f"Effective {effective}; superseded {superseded}"
                assert (_sha16(row["text_en"]), _sha16(row["text_es"])) == (sha_en, sha_es)


def test_an_id_keyed_lookup_of_a_current_row_still_names_it(db: TaxDatabase) -> None:
    rows = _rows()
    for _, _, current in GROUPS:
        for year in range(max(2015, rows[current]["effective_year"]), 2029):
            row = db.lookup_statute(current, "PR", year)
            assert row is not None and row["id"] == current


def test_a_search_for_a_window_year_finds_that_years_version(db: TaxDatabase) -> None:
    ids = {r["id"] for r in db.search_statutes("", "PR", tax_year=2019, limit=500)}
    assert PR_IDS - {"pr-ley60-1020-02-2020"} <= ids
    assert "pr-ley60-1020-02-2020" not in ids
    ids = {r["id"] for r in db.search_statutes("", "PR", tax_year=2022, limit=500)}
    assert {"pr-ley60-1020-02-2020", "pr-cri-1031-02-2019", "pr-ley60-2022-02-2019"} <= ids
    assert not {"pr-ley60-1020-02-2019", "pr-cri-1062-01-2019", "pr-cri-1062-03-2019"} & ids
    ids = {r["id"] for r in db.search_statutes("", "PR", tax_year=2026, limit=500)}
    assert not ids & PR_IDS


# --- Catalog invariant ---------------------------------------------------------------------


def _mutated(change: Callable[[dict[str, dict[str, Any]]], None]) -> list[str]:
    statutes = copy.deepcopy(load_catalog().statutes)
    by_id = {s["id"]: s for s in statutes}
    change(by_id)
    return window_span_errors(list(by_id.values()))


def _set(row_id: str, **values: Any) -> Callable[[dict[str, Any]], None]:
    return lambda rows: rows[row_id].update(values)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (_set("pr-ley60-1020-02-2019", effective_year=2020), "start in 2020, not 2019"),
        (_set("pr-cri-1062-01-2019", superseded_year=2021), "end in 2021, not 2020"),
        (_set("pr-cri-1031-02-2019", superseded_year=2025), "end in 2025, not 2026"),
        (
            _set("pr-ley60-1020-02-2020", effective_year=2019),
            "pr-ley60-1020-02-2019 and pr-ley60-1020-02-2020 overlap",
        ),
        (lambda rows: rows.pop("pr-ley60-1020-02-2019"), "start in 2020, not 2019"),
        (lambda rows: rows.pop("pr-cri-1062-03-2019"), "WINDOW_SPANS entry but no dated row"),
        (_set("pr-ley60-6020-03", effective_year=2025), "starts in 2025, inside the dated span"),
    ],
)
def test_each_broken_window_rule_is_reported(
    change: Callable[[dict[str, Any]], None], message: str
) -> None:
    errors = _mutated(change)
    assert any(message in e for e in errors), errors


# --- Labels, sources and languages ---------------------------------------------------------


def test_every_new_row_is_a_reference_summary_with_a_pinned_source() -> None:
    rows = _rows()
    manifest = _manifest()
    for row_id, (url, sha16, law, effective_date) in PR_SOURCES.items():
        entry = manifest[row_id]
        assert rows[row_id]["source_url"] == entry["url"] == url, row_id
        assert not is_official_text(url)
        labels = statute_labels(url)
        assert labels["content_type"] == CONTENT_TYPE_REFERENCE_SUMMARY, row_id
        assert entry["content_sha256"][:16] == sha16, row_id
        assert entry["enacting_law"].startswith(law), row_id
        assert entry["effective_date"] == effective_date, row_id
        assert entry["retrieved"] == "2026-10-25", row_id
        expected = "verified_verbatim" if row_id in VERBATIM_IDS else "summary"
        assert entry["verification"] == expected, row_id
        assert entry["edition"] == "as_enacted", row_id


def test_each_language_says_what_it_holds() -> None:
    rows = _rows()
    for row_id in VERBATIM_IDS:
        en, es = rows[row_id]["text_en"], rows[row_id]["text_es"]
        assert en.split("\n", 1)[0] == OGP_PREFIX_EN, row_id
        assert es.startswith("(a) "), row_id
    laws = {
        "pr-cri-1031-02-2019": ("Ley 1-2011, Section 1031.02", "Ley 1-2011, Sección 1031.02"),
        "pr-cri-1062-01-2019": ("Ley 257-2018, Section 57", "Ley 257-2018, Sección 57"),
        "pr-cri-1062-03-2019": ("Ley 60-2019, Section 6070.36", "Ley 60-2019, Sección 6070.36"),
        "pr-ley60-1020-02-2020": ("Ley 40-2020, Section 74", "Ley 40-2020, Sección 74"),
    }
    assert set(laws) == SUMMARY_IDS
    for row_id, (law_en, law_es) in laws.items():
        en, es = rows[row_id]["text_en"], rows[row_id]["text_es"]
        assert en.startswith(SUMMARY_PREFIX_EN) and es.startswith(SUMMARY_PREFIX_ES), row_id
        assert law_en in en.split("\n", 1)[0] and law_es in es.split("\n", 1)[0], row_id


def test_each_window_names_why_it_starts_and_ends() -> None:
    notes = _notes()
    for row_id in VERBATIM_IDS:
        assert (
            SET_DATE.format(y=2019, d="2019-07-01") + " and names no tax year (Sec. 6070.72)"
            in (notes[row_id])
        ), row_id
    assert (
        APPROVAL.format(y=2020) + " and names no tax year (Sec. 97)"
        in (notes["pr-ley60-1020-02-2020"])
    )
    for row_id in ("pr-ley60-1020-02-2020", "pr-ley60-2022-02-2019", "pr-ley60-6020-03-2019"):
        assert APPROVAL.format(y=2026) + " and names no tax year (Art. 8)" in notes[row_id]
    for row_id in ("pr-cri-1062-01-2019", "pr-cri-1062-03-2019"):
        assert TY2019 in notes[row_id]
        assert APPROVAL.format(y=2020) + " and names no tax year (Sec. 97)" in notes[row_id]
    assert TY2019 in notes["pr-cri-1031-02-2019"]
    assert (
        SET_DATE.format(y=2026, d="2026-07-01") + " and names no tax year (Sec. 2)"
        in (notes["pr-cri-1031-02-2019"])
    )


def test_each_summary_lists_the_amendments_that_carry_their_own_dates() -> None:
    notes = _notes()
    dated = {
        "pr-cri-1031-02-2019": (
            "Ley 79-2019",
            "Ley 40-2020, Section 13",
            "Ley 78-2022",
            "Ley 4-2023",
            "Ley 97-2023",
        ),
        "pr-cri-1062-01-2019": (
            "Ley 257-2018, Section 57",
            "Ley 40-2020, Section 33",
            "Ley 52-2022, Article 61",
        ),
        "pr-cri-1062-03-2019": ("Ley 257-2018 ((a) 7 percent",),
    }
    for row_id, laws in dated.items():
        _, _, tail = notes[row_id].partition("Amendments that carry their own dates")
        assert tail, row_id
        for law in laws:
            assert law in tail.split("Later:")[0], (row_id, law)


def test_no_earlier_year_is_given_the_current_text() -> None:
    rows = _rows()
    for (_, _, current), by_year in GROUPS.items():
        for row_id in set(by_year.values()):
            for key in ("text_en", "text_es"):
                assert " ".join(rows[row_id][key].split()) != " ".join(rows[current][key].split())


# --- Ley 60-2019 Sec. 6020.10(b), split at Ley 32-2024 ----------------------------------------

FONDO = "Fondo Especial para la Igualdad Social"
SPLIT_2019 = "pr-ley60-6020-10-2019"
SPLIT_2024 = "pr-ley60-6020-10-2024"
SPLIT_2022 = "pr-ley60-6020-10-2022"
SPLIT_2019_ENACTED = "pr-ley60-6020-10-2019-as-enacted"
LEY32 = f"{OGP}/2020/0032-2024.pdf"
LEY52 = f"{OGP}/2020/0052-2022.pdf"
LEY52_SHA256 = "6028882550a25e6d8e78a24f281e99b6b23d957f7d3c99e7c2375d82b58801d8"
LEY188 = f"{OGP}/2020/0188-2024.pdf"
LEY188_SHA256 = "f05a1389c9173063df2d3a82db042c4189f52bf986445e4f96f74fa13db5f190"
# Tax year -> the 6020.10 row it resolves to.
SPLIT_YEARS: dict[int, str] = {
    2019: SPLIT_2019_ENACTED,
    **dict.fromkeys(range(2020, 2022), SPLIT_2019),
    **dict.fromkeys(range(2022, 2024), SPLIT_2022),
    2024: SPLIT_2024,
    2025: "pr-ley60-6020-10-2025",
    **dict.fromkeys(range(2026, 2029), "pr-ley60-6020-10"),
}


def _b(row: dict[str, Any]) -> str:
    (b,) = [p for p in row["text_es"].split("\n") if p.startswith("(b) ")]
    return b


def test_6020_10_resolves_each_tax_year_to_its_row(
    db: TaxDatabase,
) -> None:
    rows = _rows()
    for year in YEARS:
        expected = SPLIT_YEARS.get(year)
        for query in ("Ley 60-2019 Sec. 6020.10", "6020.10"):
            for jurisdiction in ("PR", None):
                for lang in ("en", "es"):
                    found = db.lookup_statute(query, jurisdiction, year, lang)
                    if expected is None:
                        assert found is None, (query, year)
                        continue
                    assert found is not None and found["id"] == expected, (query, year)
                    text = rows[expected]["text_en" if lang == "en" else "text_es"]
                    assert found["operative_text"] == text
    assert (rows[SPLIT_2019]["effective_year"], rows[SPLIT_2019]["superseded_year"]) == (2020, 2022)
    assert (rows[SPLIT_2022]["effective_year"], rows[SPLIT_2022]["superseded_year"]) == (2022, 2024)
    enacted = rows[SPLIT_2019_ENACTED]
    assert (enacted["effective_year"], enacted["superseded_year"]) == (2019, 2020)
    assert (rows[SPLIT_2024]["effective_year"], rows[SPLIT_2024]["superseded_year"]) == (2024, 2025)
    for row_id in (SPLIT_2019_ENACTED, SPLIT_2019, SPLIT_2022, SPLIT_2024):
        row = db.lookup_statute(row_id)
        assert row is not None and row["id"] == row_id and not row["is_current"]
    row = db.lookup_statute("Ley 60-2019 Sec. 6020.10", "PR")
    assert row is not None and row["id"] == "pr-ley60-6020-10"
    assert [v["id"] for v in row["available_versions"]] == [
        "pr-ley60-6020-10",
        "pr-ley60-6020-10-2025",
        SPLIT_2024,
        SPLIT_2022,
        SPLIT_2019,
        SPLIT_2019_ENACTED,
    ]


def test_a_search_with_no_year_does_not_list_the_2024_row(db: TaxDatabase) -> None:
    for query in NO_YEAR_QUERIES:
        for jurisdiction in (None, "PR"):
            ids = {r["id"] for r in db.search_statutes(query, jurisdiction, limit=500)}
            assert SPLIT_2024 not in ids, (query, jurisdiction)
            assert SPLIT_2022 not in ids, (query, jurisdiction)
            assert SPLIT_2019_ENACTED not in ids, (query, jurisdiction)
    ids = {r["id"] for r in db.search_statutes("", "PR", tax_year=2024, limit=500)}
    assert SPLIT_2024 in ids and SPLIT_2019 not in ids
    ids = {r["id"] for r in db.search_statutes("", "PR", tax_year=2023, limit=500)}
    assert SPLIT_2022 in ids and SPLIT_2019 not in ids and SPLIT_2024 not in ids
    ids = {r["id"] for r in db.search_statutes("", "PR", tax_year=2021, limit=500)}
    assert SPLIT_2019 in ids and SPLIT_2022 not in ids
    ids = {r["id"] for r in db.search_statutes("", "PR", tax_year=2019, limit=500)}
    assert SPLIT_2019_ENACTED in ids and SPLIT_2019 not in ids and SPLIT_2024 not in ids


def test_before_ley_32_2024_6020_10_b_is_ley_40_2020s_text() -> None:
    rows = _rows()
    row = rows[SPLIT_2019]
    b = _b(row)
    # Ley 40-2020, Sec. 89, word for word; it names no Fondo Especial para la Igualdad Social.
    assert _sha16(b) == "f49a2b7a1a0ef719"
    assert FONDO not in b and "cinco mil dólares ($5,000)" in b
    assert "$2,500 to the Fondo" not in row["text_en"]
    assert (
        "superseded in 2022 by the text of (a)(4)(i) and (ii) as amended by Ley 52-2022"
        in row["text_en"]
    )
    assert (
        "Versión sustituida en 2022 por la Ley 52-2022 (pr-ley60-6020-10-2022)." in (row["text_es"])
    )
    assert row["source_url"] == LEY40
    assert "(pr-ley60-6020-10-2024)" in _notes()[SPLIT_2019]
    # Ley 52-2022 printed (b) only as an ellipsis, so 2022-2023 keep Ley 40-2020's (b).
    assert _b(rows[SPLIT_2022]) == b


def test_from_ley_32_2024_6020_10_b_is_article_9s_text() -> None:
    rows = _rows()
    row = rows[SPLIT_2024]
    b = _b(row)
    # Ley 32-2024, Art. 9, word for word, read on the certified copy pages 7-8.
    assert _sha16(b) == "6d09f6a1101713e0"
    assert b.endswith(
        "Los restantes dos mil quinientos (2,500) dólares deberán ser destinados al "
        "Fondo Especial para la Igualdad Social."
    )
    assert "Dos mil quinientos (2,500) dólares deberán ser destinados a cualquier otra entidad" in b
    assert row["source_url"] == LEY32
    en = row["text_en"]
    assert en.startswith("English summary; the official text is in Spanish.")
    assert "the remaining $2,500 to the Fondo Especial para la Igualdad Social" in en
    assert "as amended by Ley 32-2024, Article 9, in force on approval on January 18, 2024" in en
    assert "superseded in 2025 by the text as amended by Ley 65-2025" in en
    note = _notes()[SPLIT_2024]
    assert APPROVAL.format(y=2024) + " and names no tax year (Art. 11)" in note
    assert "still governed January 1-17, 2024" in note
    # The later versions keep the same (b).
    for later in ("pr-ley60-6020-10-2025", "pr-ley60-6020-10"):
        assert _b(rows[later]) == b, later


def test_the_6020_10_split_pins_its_sources() -> None:
    manifest = _manifest()
    rows = _rows()
    expected = {
        SPLIT_2019: (LEY40, "39f6aac5879401fc", "Ley 40-2020, Sec. 89", "2020-04-16"),
        SPLIT_2024: (LEY32, "03cc877429b4d794", "Ley 32-2024, Art. 9", "2024-01-18"),
        SPLIT_2022: (
            LEY52,
            LEY52_SHA256[:16],
            "Ley 40-2020, Sec. 89; Ley 52-2022, Art. 33",
            "2022-06-30",
        ),
    }
    for row_id, (url, sha16, law, effective_date) in expected.items():
        entry = manifest[row_id]
        assert rows[row_id]["source_url"] == entry["url"] == url, row_id
        assert entry["content_sha256"][:16] == sha16, row_id
        assert entry["enacting_law"].startswith(law), row_id
        assert entry["effective_date"] == effective_date, row_id
        assert (entry["edition"], entry["retrieved"]) == ("as_enacted", "2026-10-25"), row_id
        # Only some paragraphs were checked against the acts; the rest is from the compilation.
        assert entry["verification"] == "not_recorded", row_id
    enacted = manifest[SPLIT_2019_ENACTED]
    assert rows[SPLIT_2019_ENACTED]["source_url"] == enacted["url"] == LEY60
    assert enacted["content_sha256"][:16] == "21cd65faf8bb83e0"
    assert (enacted["enacting_law"], enacted["effective_date"]) == ("Ley 60-2019", "2019-07-01")
    assert (enacted["edition"], enacted["retrieved"]) == ("as_enacted", "2026-10-25")
    assert enacted["verification"] == "verified_verbatim"


def test_the_2020_1020_02_row_pins_ley_47_2020() -> None:
    law = _manifest()["pr-ley60-1020-02-2020"]["enacting_law"]
    assert f"Ley 47-2020, Art. 1 ({OGP}/2020/0047-2020.pdf, sha256 " in law
    assert law.endswith("be8d5537f35687a7e576712200499b4834e4224206d8f3a3c4c142f0644e3290)")


def test_in_2019_6020_10_is_ley_60_2019_as_approved() -> None:
    rows = _rows()
    row = rows[SPLIT_2019_ENACTED]
    en, es = row["text_en"], row["text_es"]
    # Ley 60-2019 as approved, word for word (certified copy pages 300-302, OGP translation).
    assert (_sha16(en), _sha16(es)) == ("053f25170a0654d7", "2f9cd5765023927f")
    assert en.split("\n", 1)[0] == OGP_PREFIX_EN
    assert es.startswith("(a) Informes Requeridos a Negocios Exentos y a sus Accionistas:")
    b = _b(row)
    assert _sha16(b) == "57dae27fc0f0d3d9"
    assert "un cincuenta por ciento (50%)" in b and "cinco mil dólares ($5,000)" not in b
    # Ley 40-2020, Sec. 89 rewrote (a)(3) and (b) and added (d) from April 16, 2020.
    assert "\n(d) " not in es and "(5,000)" not in es
    assert "\n(3) El Negocio Exento que posea un Decreto bajo este Código, tendrá la" in es
    # The DDEC fine is an unlettered paragraph after (c).
    assert es.split("\n")[-1].startswith("El Secretario del DDEC, luego de serle informado")
    note = _notes()[SPLIT_2019_ENACTED]
    assert "certified copy, pages 300-302" in note
    assert SET_DATE.format(y=2019, d="2019-07-01") + " and names no tax year (Sec. 6070.72)" in note
    assert "(pr-ley60-6020-10-2019)" in note
    later = _notes()[SPLIT_2019]
    assert f"({SPLIT_2019_ENACTED})" in later and "January 1-April 15, 2020" in later
    assert APPROVAL.format(y=2020) + " and names no tax year (Sec. 97)" in later
    for row_id in (SPLIT_2019, SPLIT_2022, SPLIT_2024):
        assert "no consta en las fuentes" not in rows[row_id]["text_es"], row_id
        assert "exact earlier wording is not on file" not in rows[row_id]["text_en"], row_id
        assert f"consta en {SPLIT_2019_ENACTED}." in rows[row_id]["text_es"], row_id


def _a4(row: dict[str, Any]) -> tuple[str, str]:
    paragraphs = row["text_es"].split("\n")
    (i,) = [p for p in paragraphs if p.startswith("(i) ")]
    (ii,) = [p for p in paragraphs if p.startswith("(ii) ")]
    return i, ii


def _body(row: dict[str, Any]) -> list[str]:
    """The Spanish paragraphs other than (a)(4)(i), (a)(4)(ii) and the notes."""
    skip = ("(i) ", "(ii) ", "[Nota")
    return [p for p in row["text_es"].split("\n") if not p.startswith(skip)]


def test_6020_10_a4_follows_ley_52_2022_and_ley_188_2024() -> None:
    rows = _rows()
    enacted, y20, y22, y24 = (
        _a4(rows[r]) for r in (SPLIT_2019_ENACTED, SPLIT_2019, SPLIT_2022, SPLIT_2024)
    )
    # 2020-2021: Ley 60-2019 as approved; Ley 40-2020, Sec. 89 printed no text for (a)(4).
    assert y20 == enacted
    # 2022-2023: Ley 52-2022, Art. 33, word for word (certified copy pages 58-59).
    assert (_sha16(y22[0]), _sha16(y22[1])) == ("7caa76b629708c57", "fb210cc8a368f5b5")
    assert "empleo promedio" in y22[0]
    assert y22[1].endswith("Certificado de Cumplimiento relacionado.")
    # 2024: (i) stays Ley 52-2022's; (ii) is Ley 188-2024, Art. 5 (certified copy pages 6-7).
    assert y24[0] == y22[0]
    assert y24[1] == y22[1].removesuffix(".") + (
        ", excepto en el caso de los Negocios Exentos exceptuados del cumplimiento con el "
        "apartado (c) de la Sección 6011.04."
    )
    # Ley 52-2022 printed no other new text, so the rest of the 2022 row is the 2020 row.
    assert _body(rows[SPLIT_2022]) == _body(rows[SPLIT_2019])
    assert rows[SPLIT_2022]["source_url"] == LEY52
    en = rows[SPLIT_2022]["text_en"]
    assert en.startswith("English summary; the official text is in Spanish.")
    assert "superseded in 2024 by the text of (b) as amended by Ley 32-2024" in en
    note = _notes()[SPLIT_2022]
    assert APPROVAL.format(y=2022) + " and names no tax year (Art. 89)" in note
    assert "still governed January 1-June 29, 2022" in note
    assert "so it changed no other text." in note
    # Ley 188-2024 was approved August 27, 2024 and took effect on approval (Art. 7).
    note = _notes()[SPLIT_2024]
    assert "Ley 188-2024, Article 5" in note and "from January 1 to August 26, 2024" in note
    assert "Ley 188-2024, Article 5" in rows[SPLIT_2024]["text_en"]
    es = rows[SPLIT_2024]["text_es"]
    assert "Artículo 5 de la Ley 188-2024" in es
    assert "Del 1 de enero al 26 de agosto de 2024" in es
    law = _manifest()[SPLIT_2024]["enacting_law"]
    assert f"Ley 188-2024, Art. 5 ((a)(4)(ii); {LEY188}, sha256 " in law
    assert f"{LEY188}, sha256 {LEY188_SHA256})" in law
    assert f"Ley 52-2022, Art. 33 ((a)(4)(i); {LEY52}, sha256 {LEY52_SHA256})" in law
