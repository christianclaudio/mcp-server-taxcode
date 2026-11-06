"""Canada federal (CAN): the ITA and ITR rows in English and French, their labels, the CRA
guidance, the canada_federal rate table, and the Canada answer of evaluate_state_residency."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from scripts import extract_canada_lims
from scripts.ingest_statutes import StatuteRecord
from taxcode_mcp.catalog import (
    COUNTRY_RATE_TABLES,
    DATA_DIR,
    FRENCH_DOCS_NOTE,
    STATUTE_DB_FIELDS,
    CatalogError,
    dump_json,
    load_catalog,
    manifest_errors,
    render_docs,
    statute_db_row,
    statute_doc_path,
    statute_document_row,
)
from taxcode_mcp.db import (
    TaxDatabase,
    is_official_text,
    justice_canada_commit,
    language_fallback,
    snippet_texts,
)
from taxcode_mcp.disclaimers import CANADA_TRANSLATION_DISCLAIMER, reference_labels
from taxcode_mcp.errors import SafetyViolationError
from taxcode_mcp.server import mcp
from taxcode_mcp.tools import compliance
from taxcode_mcp.tools.canada import basic_personal_amount, part_i_tax

REPO = Path(__file__).resolve().parent.parent
HEAD = "0507465393c224c0491fc8c17c88340217acc656"
RAW = "https://raw.githubusercontent.com/justicecanada/laws-lois-xml"
ITA_SECTIONS = ["2", "38", "114", "115", "117", "117.1", "118", "128.1", "153", "212", "250"]
ITR_SECTIONS = ["100", "102", "103", "105", "202", "805", "2600", "2601"]
# Prior-year versions, each read from the laws-lois-xml commit current in its first year.
PRIOR_ROWS = {
    "can-ita-117-2019": (2019, 2023, "1fb8069ccea31210a735312bf041770d11454c0f"),
    "can-ita-117-2023": (2023, 2025, "b88cac88557ac3f214f16c66b684036e8a2b2c2c"),
    "can-ita-118-2019": (2019, 2020, "1fb8069ccea31210a735312bf041770d11454c0f"),
    "can-ita-118-2020": (2020, 2023, "d67469e2aaf0f752b3dd7597cf48b32edc032be4"),
    "can-ita-118-2023": (2023, 2025, "b88cac88557ac3f214f16c66b684036e8a2b2c2c"),
}
CRA_PUBLICATIONS = {
    "can-cra-folio-s5-f1-c1",
    "can-cra-t1161",
    "can-cra-t1243",
    "can-cra-t1244",
    "can-cra-leaving-canada",
}
CATALOG = load_catalog(with_manifest=True)
ROWS = {s["id"]: s for s in CATALOG.statutes if s["jurisdiction"] == "CAN"}
MANIFEST = {e["id"]: e for e in CATALOG.manifest if e["_jurisdiction"] == "CAN"}
PARAMS = CATALOG.rate_tables["CAN"]["params"]
YEARS = {row["tax_year"]: row for row in PARAMS["years"]}


@pytest.fixture
def db() -> TaxDatabase:
    return TaxDatabase(":memory:")


@pytest.fixture
def data(tmp_path: Path) -> Path:
    """A writable copy of the data files."""
    root = tmp_path / "data"
    shutil.copytree(DATA_DIR, root)
    return root


def _edit(path: Path, change: Callable[[Any], None]) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    change(doc)
    path.write_text(dump_json(doc), encoding="utf-8")


def _can(**kwargs: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "jurisdiction": "CAN",
        "days_in_state": 365,
        "is_domiciled": True,
        "federal_agi": 56867.0,
        "tax_year": 2024,
    }
    args.update(kwargs)
    result: dict[str, Any] = compliance.evaluate_state_residency(**args)
    return result


def _leave(properties: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    """The departure tax answer of a 2026 emigrant with $50,000 of taxable income."""
    args: dict[str, Any] = {"federal_agi": 50000.0, "tax_year": 2026, "event": "emigration"}
    args.update(kwargs)
    departure: dict[str, Any] = _can(emigration_properties=properties, **args)["departure_tax"]
    return departure


# --- The statute rows -------------------------------------------------------------------


def test_the_canada_rows_are_the_named_ita_and_itr_sections() -> None:
    assert len(ROWS) == 24
    current = [s for s in ROWS.values() if s["superseded_year"] is None]
    assert [s["section"] for s in current if s["corpus"] == "ITA"] == ITA_SECTIONS
    assert [s["section"] for s in current if s["corpus"] == "ITR"] == ITR_SECTIONS
    assert {s["id"] for s in ROWS.values() if s["superseded_year"] is not None} == set(PRIOR_ROWS)
    for s in ROWS.values():
        prefix = "ITA s. " if s["corpus"] == "ITA" else "ITR s. "
        assert s["citation"] == prefix + s["section"], s["id"]
        assert s["text_en"].strip() and s["text_fr"].strip(), s["id"]
        assert s["text_es"].startswith("Resumen no oficial en español"), s["id"]
        assert justice_canada_commit(s["source_url"]) is not None, s["id"]
    assert "CAN" in CATALOG.jurisdictions and "CA" in CATALOG.jurisdictions


def test_the_prior_year_rows_quote_a_historical_commit() -> None:
    for row_id, (effective, superseded, commit) in PRIOR_ROWS.items():
        row = ROWS[row_id]
        assert (row["effective_year"], row["superseded_year"]) == (effective, superseded)
        assert row["source_url"] == f"{RAW}/{commit}/eng/acts/I-3.3.xml"
        assert MANIFEST[row_id]["french_source"]["url"] == f"{RAW}/{commit}/fra/lois/I-3.3.xml"
    assert ROWS["can-ita-117"]["effective_year"] == ROWS["can-ita-118"]["effective_year"] == 2025


def test_every_statute_row_is_verified_verbatim_with_both_files_hashed() -> None:
    for row_id, row in ROWS.items():
        entry = MANIFEST[row_id]
        assert entry["edition"] == "amended_compilation", row_id
        assert entry["official"] is True and entry["verification"] == "verified_verbatim"
        assert len(entry["content_sha256"]) == 64 and entry["retrieved"] == "2026-11-05"
        assert "SI/97-5" in entry["licence"]
        english, french = row["source_url"], entry["french_source"]["url"]
        assert french == english.replace("/eng/acts/", "/fra/lois/").replace(
            "/eng/regulations/C.R.C.,_c._945.xml", "/fra/reglements/C.R.C.,_ch._945.xml"
        )
        assert len(entry["french_source"]["content_sha256"]) == 64
    assert manifest_errors(CATALOG) == []


def test_section_128_1_holds_the_departure_rules_in_both_languages() -> None:
    en = ROWS["can-ita-128-1"]["text_en"].split("\n")
    fr = ROWS["can-ita-128-1"]["text_fr"]
    for start in (
        "(4) For the purposes of this Act, where at a particular time a taxpayer ceases",
        "(i) real or immovable property situated in Canada, a Canadian resource property",
        "(ii) capital property used in, property included in Class 14.1",
        "(iii) an excluded right or interest of the taxpayer,",
        "(iv) if the taxpayer is not a trust and was not, during the 120-month period",
        "(v) any property in respect of which the taxpayer elects under paragraph (6)(a)",
        "(9) An individual who ceases at a particular time in a taxation year",
        "(10) The definitions in this subsection apply in this section.",
    ):
        assert any(line.startswith(start) for line in en), start
    assert any("for more than 60 months" in line for line in en)
    assert any("is greater than $25,000" in line for line in en)
    assert "plus de 60 mois au cours de la période de 120 mois" in fr
    assert "excède 25" in fr


# --- Labels, lookups and the French text ----------------------------------------------


def test_only_a_pinned_justice_canada_file_is_official_text() -> None:
    url = f"{RAW}/{HEAD}/fra/reglements/C.R.C.,_ch._945.xml"
    assert justice_canada_commit(url) == HEAD and is_official_text(url)
    for other in (
        f"{RAW}/main/eng/acts/I-3.3.xml",
        f"{RAW}/{HEAD[:7]}/eng/acts/I-3.3.xml",
        f"{RAW}/{HEAD}/eng/acts/I-3.3.pdf",
        f"{RAW}/{HEAD}/eng/other/I-3.3.xml",
        "https://laws-lois.justice.gc.ca/eng/acts/I-3.3/",
        None,
    ):
        assert justice_canada_commit(other) is None, other
        assert not is_official_text(other), other
    # Without a Canada commit the labels are the US ones, unchanged.
    us = reference_labels(official_text=True)
    assert reference_labels(official_text=True, canada_commit=None) == us
    assert reference_labels(canada_commit=HEAD) == reference_labels()


def test_a_canada_lookup_carries_the_si_97_5_label(db: TaxDatabase) -> None:
    row = db.lookup_statute("can-ita-128-1")
    assert row is not None
    assert row["content_type"] == "official_text" and row["unofficial_summary"] is False
    en = row["content_label"]["en"]
    for words in (
        "Justice Canada consolidation, laws-lois-xml commit 0507465",
        "English and French versions are equally authoritative",
        "Charter s. 18(1); Official Languages Act s. 13",
        "Reproduction of Federal Law Order (SI/97-5)",
        "due diligence as to accuracy",
        "not an official version (Legislation Revision and Consolidation Act s. 31)",
        "not made in affiliation with, or with the endorsement of, the Government of Canada",
        "Spanish text is an unofficial summary",
    ):
        assert words in en, words
    assert "TR/97-5" in row["content_label"]["es"]
    assert "resumen no oficial" in row["content_label"]["es"]
    assert "TR/97-5" in row["content_label"]["fr"] and "0507465" in row["content_label"]["fr"]
    assert row["translation_disclaimer"] == CANADA_TRANSLATION_DISCLAIMER
    old = db.lookup_statute("can-ita-117-2019")
    assert old is not None and "commit 1fb8069" in old["content_label"]["en"]


@pytest.mark.parametrize("lang", ["en", "fr", "es", "EN", "Fr"])
def test_a_canada_lookup_answers_in_english_french_or_spanish(db: TaxDatabase, lang: str) -> None:
    row = db.lookup_statute("ITA s. 128.1", "CAN", lang=lang)
    assert row is not None and row["id"] == "can-ita-128-1"
    source = ROWS["can-ita-128-1"]
    field = {"en": "text_en", "fr": "text_fr", "es": "text_es"}[lang.lower()]
    assert row["language"] == lang.lower()
    assert row["operative_text"] == source[field]
    assert (row["text_en"], row["text_fr"], row["text_es"]) == (
        source["text_en"],
        source["text_fr"],
        source["text_es"],
    )
    assert row["official_language"] == "en" and row["official_languages"] == ["en", "fr"]
    assert "language_fallback" not in row


def test_a_lookup_by_year_picks_the_canada_version(db: TaxDatabase) -> None:
    for citation, year, expected in (
        ("ITA s. 117", 2019, "can-ita-117-2019"),
        ("ITA s. 117", 2022, "can-ita-117-2019"),
        ("117", 2023, "can-ita-117-2023"),
        ("ITA s. 117", 2025, "can-ita-117"),
        ("ITA s. 118", 2019, "can-ita-118-2019"),
        ("ITA s. 118", 2021, "can-ita-118-2020"),
        ("ITA s. 118", 2024, "can-ita-118-2023"),
        ("ITA s. 118", None, "can-ita-118"),
    ):
        row = db.lookup_statute(citation, "CAN", tax_year=year, lang="fr")
        assert row is not None and row["id"] == expected, (citation, year)
        assert row["operative_text"] == ROWS[expected]["text_fr"]
    # "CA" stays California.
    ca = db.lookup_statute("17041", "CA")
    assert ca is not None and ca["jurisdiction"] == "CA"


@pytest.mark.parametrize(("row_id", "used"), [("us-irc-933", "en"), ("pr-ley60-6020-03", "es")])
def test_french_on_a_row_without_french_falls_back_to_its_official_language(
    db: TaxDatabase, row_id: str, used: str
) -> None:
    fr = db.lookup_statute(row_id, lang="fr")
    official = db.lookup_statute(row_id, lang=used)
    assert fr is not None and official is not None
    assert fr.pop("language_fallback") == language_fallback(used)
    assert fr == official
    assert "text_fr" not in fr and "official_languages" not in fr
    note = language_fallback(used)
    assert (note["requested"], note["used"]) == ("fr", used)
    assert all(f"{used!r}" in note[k] for k in ("en", "es", "fr"))


def test_the_snippet_language_order() -> None:
    assert snippet_texts("en", "CAN", "E", "S", "F") == [("en", "E"), ("fr", "F"), ("es", "S")]
    assert snippet_texts("es", "CAN", "E", "S", "F") == [("es", "S"), ("en", "E"), ("fr", "F")]
    assert snippet_texts("fr", "CAN", "E", "S", "F") == [("fr", "F"), ("en", "E"), ("es", "S")]
    assert snippet_texts("fr", "US", "E", "S", None) == [("en", "E"), ("es", "S")]
    assert snippet_texts("fr", "PR", "E", "S", "") == [("es", "S"), ("en", "E")]
    assert snippet_texts("en", "US", "E", "S", None) == [("en", "E"), ("es", "S")]
    assert snippet_texts("xx", "US", "E", "S", None) == [("es", "S"), ("en", "E")]


def test_search_reads_the_french_text_and_picks_the_snippet_language(db: TaxDatabase) -> None:
    # "contribuable" (taxpayer) is in the French text of the Canada rows only.
    for lang in ("fr", "en", "es"):
        rows = db.search_statutes("contribuable", lang=lang, limit=25)
        assert rows and {r["jurisdiction"] for r in rows} == {"CAN"}, lang
        assert {r["snippet_language"] for r in rows} == {"fr"}, lang
        assert all("contribuable" in r["snippet"].lower() for r in rows), lang
    english = db.search_statutes("reportable property", jurisdiction="Canada", lang="en")
    assert english[0]["id"] == "can-ita-128-1" and english[0]["snippet_language"] == "en"
    french = db.search_statutes("biens à déclarer", jurisdiction="CAN", lang="fr")
    assert french[0]["id"] == "can-ita-128-1" and french[0]["snippet_language"] == "fr"
    spanish = db.search_statutes("impuesto de salida", jurisdiction="CAN", lang="es")
    assert spanish[0]["id"] == "can-ita-128-1" and spanish[0]["snippet_language"] == "es"
    # Elsewhere French answers like the official language: English, or Spanish in PR.
    for query, jurisdiction, used in (
        ("bona fide residence", "US", "en"),
        ("residente", "PR", "es"),
    ):
        fr = db.search_statutes(query, jurisdiction=jurisdiction, lang="fr")
        assert fr == db.search_statutes(query, jurisdiction=jurisdiction, lang=used)


def test_the_french_text_lives_in_its_own_table(db: TaxDatabase) -> None:
    conn = db._get_read_conn()
    count = conn.execute("SELECT COUNT(*) FROM statutes_fr").fetchone()
    assert count == (24,)
    columns = conn.execute("SELECT * FROM statutes LIMIT 0").description
    assert [c[0] for c in columns] == list(STATUTE_DB_FIELDS)
    row = dict(statute_db_row(ROWS["can-ita-2"]), id="can-ita-test", citation="ITA s. test")
    db.insert_statute(row)
    found = db.lookup_statute("can-ita-test", lang="fr")
    assert found is not None and found["operative_text"] == ROWS["can-ita-2"]["text_fr"]
    db.upsert_statute({**row, "text_fr": ""})
    cleared = db.lookup_statute("can-ita-test", lang="fr")
    assert cleared is not None and cleared["language_fallback"]["used"] == "en"
    db.upsert_statute(row)
    again = db.lookup_statute("can-ita-test", lang="fr")
    assert again is not None and again["language"] == "fr"
    assert db.delete_statute("can-ita-test")
    gone = conn.execute("SELECT COUNT(*) FROM statutes_fr WHERE id = ?", ["can-ita-test"])
    assert gone.fetchone() == (0,)


def test_db_and_document_rows_carry_text_fr_only_when_present() -> None:
    us = next(s for s in CATALOG.statutes if s["id"] == "us-irc-933")
    assert us["text_fr"] == ""
    assert list(statute_db_row(us)) == list(STATUTE_DB_FIELDS)
    assert "text_fr" not in statute_document_row(us)
    can = ROWS["can-ita-2"]
    assert statute_db_row(can)["text_fr"] == can["text_fr"]
    assert statute_document_row(can)["text_fr"] == can["text_fr"].split("\n")


async def test_lookup_pages_the_french_text_with_its_length() -> None:
    source = ROWS["can-ita-212"]
    args: dict[str, Any] = {"citation_or_section": "can-ita-212", "lang": "fr"}
    pages: list[dict[str, Any]] = []
    async with Client(mcp) as client:
        while True:
            result = await client.call_tool("statutes_lookup_statute", args)
            assert isinstance(result.structured_content, dict)
            data = result.structured_content["data"]
            pages.append(data)
            if not data["next_cursor"]:
                break
            args = {"citation_or_section": "ignored", "page_cursor": data["next_cursor"]}
        us = await client.call_tool("statutes_lookup_statute", {"citation_or_section": "933"})
    assert len(pages) > 1
    for name in ("text_en", "text_fr", "text_es"):
        assert "".join(p[name] for p in pages) == source[name], name
    assert "".join(p["operative_text"] for p in pages) == source["text_fr"]
    assert all(p["language"] == "fr" for p in pages)
    page = pages[0]["text_page"]
    assert page["text_fr_chars"] == len(source["text_fr"])
    assert page["text_en_chars"] == len(source["text_en"])
    assert isinstance(us.structured_content, dict)
    assert "text_fr_chars" not in us.structured_content["data"]["text_page"]


async def test_search_tool_takes_lang_fr() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "statutes_search_tax_code",
            {"query": "biens à déclarer", "jurisdiction": "CAN", "lang": "fr"},
        )
    assert isinstance(result.structured_content, dict)
    first = result.structured_content["results"][0]
    assert (first["id"], first["snippet_language"]) == ("can-ita-128-1", "fr")


# --- Docs, CRA guidance and the rate table -----------------------------------------------


def test_a_canada_docs_page_holds_the_english_french_and_spanish_texts() -> None:
    files = render_docs(CATALOG.statutes, CATALOG.publications)
    row = ROWS["can-ita-128-1"]
    page = files[statute_doc_path(row)]
    assert statute_doc_path(row) == "can/ita_128_1.md"
    assert f"- **Official Text:** {FRENCH_DOCS_NOTE}" in page
    english = page.index("## Operative Statutory Language (English)")
    french = page.index("## Texte législatif officiel (Français)")
    spanish = page.index("## Resumen no oficial (Español)")
    assert english < french < spanish
    assert row["text_fr"] in page and row["text_es"] in page
    us = files[statute_doc_path(next(s for s in CATALOG.statutes if s["id"] == "us-irc-933"))]
    assert "Français" not in us and "## Texto Estatutario Oficial (Español)" in us
    assert "### Français" in files["can/llms-full.txt"]
    assert "### Français" not in files["us/llms-full.txt"]
    assert "> Canada (CAN): federal Income Tax Act" in files["llms.txt"]
    for name in ("llms.txt", "can/llms-full.txt", "can/ita_128_1.md"):
        assert (REPO / "docs" / name).read_text(encoding="utf-8") == files[name], name


def test_the_cra_guidance_is_a_link_and_a_summary(db: TaxDatabase) -> None:
    pubs = {p["id"]: p for p in CATALOG.publications if p["id"].startswith("can-")}
    assert set(pubs) == CRA_PUBLICATIONS
    for pub_id, pub in pubs.items():
        assert pub["source_url"].startswith("https://www.canada.ca/"), pub_id
        assert "Unofficial summary written for this catalog" in pub["summary"], pub_id
        entry = MANIFEST[pub_id]
        assert entry["verification"] == "summary" and len(entry["content_sha256"]) == 64
    listed = {p["id"] for p in db.list_publications(limit=100)}
    assert CRA_PUBLICATIONS <= listed
    assert "$25,000" in pubs["can-cra-t1161"]["summary"]


def test_the_rate_table_holds_2019_to_2026_from_cra_sources() -> None:
    assert sorted(YEARS) == list(range(2019, 2027))
    assert COUNTRY_RATE_TABLES == {"CAN"}
    assert "CAN" not in compliance.SUPPORTED_STATE_JURISDICTIONS
    assert "CA" in compliance.SUPPORTED_STATE_JURISDICTIONS
    for year, row in YEARS.items():
        lowest = row["brackets"][-1]["rate"]
        assert lowest == row["credit_rate"], year
        expected = {2025: 0.145, 2026: 0.14}.get(year, 0.15)
        assert lowest == expected, year
        assert [b["rate"] for b in row["brackets"][:-1]] == [0.33, 0.29, 0.26, 0.205], year
    assert YEARS[2025]["brackets"][-2]["over"] == 57375.0
    assert (PARAMS["capital_gains_inclusion_rate"], PARAMS["sojourner_days"]) == (0.5, 183)
    assert (PARAMS["t1161_threshold"], PARAMS["short_term_resident_months"]) == (25000.0, 60)
    entry = MANIFEST["can-rates"]
    assert entry["verification"] == "summary"
    assert sorted({s["tax_year"] for s in entry["year_sources"]}) == list(range(2019, 2027))


# --- Catalog validation -----------------------------------------------------------------


def _first_row(change: Callable[[dict[str, Any]], None]) -> Callable[[Any], None]:
    return lambda doc: change(doc["statutes"][0])


@pytest.mark.parametrize(
    "change",
    [
        lambda row: row.pop("text_fr"),
        lambda row: row.update(text_fr=[]),
        lambda row: row.update(text_fr=["", "  "]),
    ],
)
def test_a_canada_row_needs_its_french_text(data: Path, change: Callable[[Any], None]) -> None:
    _edit(data / "statutes" / "can.json", _first_row(change))
    with pytest.raises(CatalogError, match=r"text_fr \(the official French text\) is required"):
        load_catalog(data)


def test_french_text_elsewhere_is_optional_but_needs_its_source(data: Path) -> None:
    _edit(data / "statutes" / "us.json", _first_row(lambda row: row.update(text_fr=["Texte"])))
    catalog = load_catalog(data, with_manifest=True)
    first = next(s for s in catalog.statutes if s["jurisdiction"] == "US")
    assert first["text_fr"] == "Texte"
    assert any("needs french_source" in e for e in manifest_errors(catalog))


def _french_source(entry_id: str, value: Any) -> Callable[[Any], None]:
    def change(doc: Any) -> None:
        entry = next(e for e in doc["entries"] if e["id"] == entry_id)
        entry["french_source"] = value

    return change


@pytest.mark.parametrize(
    ("entry_id", "value", "message"),
    [
        ("can-cra-t1161", {"url": "https://x", "content_sha256": "0" * 64}, "only on a statute"),
        ("can-ita-2", {"url": "https://x"}, "fields must be exactly"),
        ("can-ita-2", "https://x", "fields must be exactly"),
        ("can-ita-2", {"url": "http://x", "content_sha256": "0" * 64}, "url must be an https"),
        ("can-ita-2", {"url": 5, "content_sha256": "0" * 64}, "url must be an https"),
        ("can-ita-2", {"url": "https://x", "content_sha256": "AB"}, "64 lowercase hex"),
        ("can-ita-2", {"url": "https://x", "content_sha256": None}, "64 lowercase hex"),
    ],
)
def test_a_french_source_is_checked(data: Path, entry_id: str, value: Any, message: str) -> None:
    _edit(data / "manifest" / "can.json", _french_source(entry_id, value))
    errors = manifest_errors(load_catalog(data, with_manifest=True))
    assert any(message in e and entry_id in e for e in errors), errors


def test_a_canada_row_without_its_french_source_is_reported(data: Path) -> None:
    def drop(doc: Any) -> None:
        next(e for e in doc["entries"] if e["id"] == "can-ita-2").pop("french_source")

    _edit(data / "manifest" / "can.json", drop)
    errors = manifest_errors(load_catalog(data, with_manifest=True))
    assert any("can-ita-2" in e and "needs french_source" in e for e in errors), errors


def _year(i: int, change: Callable[[dict[str, Any]], None]) -> Callable[[Any], None]:
    return lambda doc: change(doc["params"]["years"][i])


def _params(change: Callable[[dict[str, Any]], None]) -> Callable[[Any], None]:
    return lambda doc: change(doc["params"])


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (_params(lambda p: p.update(years=[])), "years: must be a non-empty list"),
        (_params(lambda p: p.update(years={})), "years: must be a non-empty list"),
        (_params(lambda p: p["years"].__setitem__(0, 5)), "years[0]: must be an object"),
        (_params(lambda p: p.pop("sojourner_days")), "params"),
        (_year(0, lambda y: y.update(tax_year="2019")), "tax_year: must be an integer"),
        (_year(1, lambda y: y.update(tax_year=2019)), "years must be consecutive, ascending"),
        (_year(0, lambda y: y.pop("credit_rate")), "years[0]"),
        (_year(0, lambda y: y["brackets"][0].update(over=-1.0)), "brackets[0].over"),
        (_year(0, lambda y: y["brackets"][0].update(rate=2.0)), "brackets[0].rate"),
        (_year(0, lambda y: y["brackets"].reverse()), "steps out of order"),
        (_year(0, lambda y: y.update(basic_personal_amount=[])), "must be an object"),
        (_year(0, lambda y: y["basic_personal_amount"].pop("minimum")), "basic_personal_amount"),
        (
            _year(0, lambda y: y["basic_personal_amount"].update(maximum=-5.0)),
            "basic_personal_amount.maximum",
        ),
        (
            _year(0, lambda y: y["basic_personal_amount"].update(minimum=99999.0)),
            "minimum must not exceed maximum",
        ),
        (
            _year(0, lambda y: y["basic_personal_amount"].update(phaseout_end=1.0)),
            "phaseout_start must be below phaseout_end",
        ),
        (_year(0, lambda y: y.update(credit_rate=1.5)), "credit_rate"),
        (_params(lambda p: p.update(capital_gains_inclusion_rate=2.0)), "inclusion_rate"),
        (_params(lambda p: p.update(sojourner_days=0)), "sojourner_days: must be 1 to 366"),
        (_params(lambda p: p.update(sojourner_days=True)), "sojourner_days: must be 1 to 366"),
        (
            _params(lambda p: p.update(short_term_resident_months=121)),
            "short_term_resident_months: must be 1 to 120",
        ),
        (_params(lambda p: p.update(t1161_threshold=-1.0)), "t1161_threshold"),
        (_params(lambda p: p.update(personal_use_minimum="1000")), "personal_use_minimum"),
    ],
)
def test_the_canada_federal_params_are_checked(
    data: Path, change: Callable[[Any], None], message: str
) -> None:
    _edit(data / "rates" / "can.json", change)
    with pytest.raises(CatalogError) as raised:
        load_catalog(data, with_manifest=True)
    assert message in str(raised.value)


# --- evaluate_state_residency for Canada ------------------------------------------------


def test_a_factual_resident_pays_the_brackets_less_the_basic_personal_credit() -> None:
    result = _can()
    assert (result["status"], result["jurisdiction"], result["is_resident"]) == (
        "success",
        "CAN",
        True,
    )
    tests = result["residency_tests"]
    assert tests["residency_status"] == "factual_resident" and tests["factual_residence_ties"]
    tax = result["tax_computation"]
    # 2024: 15% of $55,867 plus 20.5% of $1,000, less 15% of the $15,705 basic personal amount.
    assert tax["graduated_tax"] == pytest.approx(8585.05)
    assert tax["basic_personal_amount"] == 15705.0
    assert tax["basic_personal_amount_credit"] == pytest.approx(2355.75)
    assert tax["estimated_federal_tax"] == pytest.approx(6229.30)
    assert tax["estimated_state_tax"] == tax["estimated_federal_tax"]
    assert tax["currency"] == "CAD" and "15% to 33%" in tax["rate_description"]
    assert "departure_tax" not in result and result["ignored_inputs"] == []
    assert result["references"]["statutes"] == [
        "can-ita-2",
        "can-ita-114",
        "can-ita-117",
        "can-ita-118",
        "can-ita-250",
    ]
    assert result["references"]["publications"] == ["can-cra-folio-s5-f1-c1"]
    assert all(ref in ROWS for ref in result["references"]["statutes"])
    assert _can(jurisdiction="Canada") == _can(jurisdiction=" can ") == result
    assert _can(is_domiciled=False, maintains_permanent_abode=True, days_in_state=10)["is_resident"]


def test_a_sojourner_of_183_days_is_deemed_resident() -> None:
    args: dict[str, Any] = {"is_domiciled": False, "federal_agi": 50000.0, "tax_year": 2026}
    deemed = _can(days_in_state=183, **args)
    assert deemed["residency_tests"]["residency_status"] == "deemed_resident_sojourner"
    assert deemed["residency_tests"]["sojourner_days_threshold"] == 183
    # 2026: 14% of $50,000 less 14% of the $16,452 basic personal amount.
    assert deemed["tax_computation"]["estimated_federal_tax"] == pytest.approx(4696.72)
    assert "250(1)(a)" in deemed["tax_computation"]["residency_note"]
    away = _can(days_in_state=182, **args)
    assert away["residency_tests"]["residency_status"] == "non_resident"
    assert not away["is_resident"]
    tax = away["tax_computation"]
    assert (tax["estimated_federal_tax"], tax["graduated_tax"]) == (0.0, 0.0)
    assert tax["basic_personal_amount_credit"] == 0.0 and "s. 212" in tax["residency_note"]


def test_income_tax_by_year_and_the_basic_personal_amount_phaseout() -> None:
    for year in range(2019, 2027):
        tax = _can(tax_year=year, federal_agi=100000.0)["tax_computation"]
        assert tax["estimated_federal_tax"] == part_i_tax(YEARS[year], 100000.0)[
            "estimated_federal_tax"
        ]
    assert "14.5%" in _can(tax_year=2025)["tax_computation"]["rate_description"]
    assert "from 14% to 33%" in _can(tax_year=2026)["tax_computation"]["rate_description"]
    bpa = YEARS[2024]["basic_personal_amount"]
    assert basic_personal_amount(bpa, bpa["phaseout_start"]) == bpa["maximum"]
    assert basic_personal_amount(bpa, bpa["phaseout_end"]) == bpa["minimum"]
    assert basic_personal_amount(bpa, 1e9) == bpa["minimum"]
    middle = (bpa["phaseout_start"] + bpa["phaseout_end"]) / 2
    assert basic_personal_amount(bpa, middle) == pytest.approx(
        (bpa["maximum"] + bpa["minimum"]) / 2, abs=0.01
    )
    # The credit can exceed the bracket tax; the estimate never goes below zero.
    assert _can(federal_agi=10000.0)["tax_computation"]["estimated_federal_tax"] == 0.0
    with_gains = _can(federal_agi=50000.0, capital_gains=10000.0)["tax_computation"]
    assert with_gains["taxable_net_income"] == 55000.0
    for year in (2018, 2027):
        with pytest.raises(SafetyViolationError, match="held for tax years 2019-2026"):
            _can(tax_year=year)


def test_ties_flags_and_ignored_inputs() -> None:
    flagged = _can(has_active_driver_license=True, has_active_voter_registration=True)
    risk = flagged["domicile_audit_risk"]
    assert risk["risk_level"] == "high" and len(risk["flags"]) == 2
    assert _can()["domicile_audit_risk"] == {"risk_level": "standard", "flags": []}
    leaving = _can(event="emigration", maintains_permanent_abode=True)
    assert leaving["domicile_audit_risk"]["flags"][0].startswith("CRITICAL")
    ignored = _can(filing_status="married_joint", dependents=2, age=70)["ignored_inputs"]
    assert ignored == ["age", "dependents", "filing_status"]


def test_departure_tax_follows_section_128_1() -> None:
    properties = [
        {"description": "shares", "fair_market_value": 100000.0, "adjusted_cost_base": 40000.0},
        {"category": "canadian_real_or_resource", "fair_market_value": 500000.0},
        {"category": "canadian_business_pe", "fair_market_value": 20000.0},
        {"category": "excluded_right_or_interest", "fair_market_value": 200000.0},
        {"category": "excluded_right_reportable", "fair_market_value": 5000.0},
        {"category": "canadian_cash", "fair_market_value": 30000.0},
        *(
            {"category": "personal_use_property", "fair_market_value": v, "adjusted_cost_base": c}
            for v, c in ((8000.0, 500.0), (900.0, 2000.0), (15000.0, 20000.0))
        ),
    ]
    result = _can(
        federal_agi=50000.0, tax_year=2026, event="Emigration", emigration_properties=properties
    )
    assert result["residency_tests"]["residency_status"] == "part_year_emigrant"
    assert "can-ita-128-1" in result["references"]["statutes"]
    assert {"can-cra-t1161", "can-cra-t1243", "can-cra-t1244", "can-cra-leaving-canada"} <= set(
        result["references"]["publications"]
    )
    departure = result["departure_tax"]
    rows = departure["properties"]
    assert [r["exclusion"] for r in rows] == [
        None,
        "ITA s. 128.1(4)(b)(i)",
        "ITA s. 128.1(4)(b)(ii)",
        "ITA s. 128.1(4)(b)(iii)",
        "ITA s. 128.1(4)(b)(iii)",
        None,
        None,
        None,
        None,
    ]
    # Personal-use property: cost and proceeds at least $1,000 each, and no loss.
    assert [r["deemed_gain"] for r in rows] == [60000.0, 0, 0, 0, 0, 0, 7000.0, 0, 0]
    reportable = [True, True, True, False, True, False, False, False, True]
    assert [r["reportable"] for r in rows] == reportable
    assert "paragraph (a)" in rows[5]["reportable_basis"]
    assert "paragraph (b)" in rows[3]["reportable_basis"]
    assert "paragraph (d)" in rows[6]["reportable_basis"]
    assert departure["deemed_capital_gain"] == 67000.0
    assert departure["taxable_capital_gain"] == 33500.0
    row = YEARS[2026]
    expected = (
        part_i_tax(row, 83500.0)["estimated_federal_tax"]
        - part_i_tax(row, 50000.0)["estimated_federal_tax"]
    )
    assert departure["estimated_departure_tax"] == pytest.approx(expected, abs=0.01)
    assert departure["reportable_fair_market_value"] == 640000.0
    assert departure["t1161_required"] is True
    forms = {f["form"]: f["required"] for f in departure["forms"]}
    assert forms == {"T1243": True, "T1161": True, "T1244": False}
    assert departure["short_term_resident_rule"]["applies"] is None


def test_the_t1161_test_is_more_than_25000_of_reportable_property() -> None:
    at = _leave([{"fair_market_value": 25000.0, "adjusted_cost_base": 25000.0}])
    assert (at["t1161_required"], at["estimated_departure_tax"]) == (False, 0.0)
    over = _leave([{"fair_market_value": 25000.01, "adjusted_cost_base": 25000.0}])
    assert over["t1161_required"] is True
    assert at["t1161_threshold"] == 25000.0
    # A deemed loss on capital property offsets the year's other gains (net, never below zero),
    # so the departure tax is negative: the tax the loss saves.
    loss = _leave(
        [{"fair_market_value": 1000.0, "adjusted_cost_base": 5000.0}], capital_gains=2000.0
    )
    assert loss["deemed_capital_gain"] == -4000.0
    assert loss["taxable_capital_gain"] == -1000.0
    assert loss["estimated_departure_tax"] == pytest.approx(-140.0)
    nothing = _leave([{"category": "canadian_real_or_resource", "fair_market_value": 1.0}])
    assert {f["form"]: f["required"] for f in nothing["forms"]}["T1243"] is False
    assert _leave([])["properties"] == []


def test_a_short_term_resident_keeps_what_they_brought() -> None:
    brought = [
        {
            "fair_market_value": 90000.0,
            "adjusted_cost_base": 10000.0,
            "owned_at_last_immigration_or_inherited": True,
        },
        {
            "category": "personal_use_property",
            "fair_market_value": 50000.0,
            "owned_at_last_immigration_or_inherited": True,
        },
        {
            "category": "canadian_real_or_resource",
            "fair_market_value": 300000.0,
            "owned_at_last_immigration_or_inherited": True,
        },
        {"fair_market_value": 20000.0, "adjusted_cost_base": 15000.0},
    ]
    short = _leave(brought, months_resident_in_last_120=60)
    assert [r["exclusion"] for r in short["properties"]] == [
        "ITA s. 128.1(4)(b)(iv)",
        "ITA s. 128.1(4)(b)(iv)",
        "ITA s. 128.1(4)(b)(i)",
        None,
    ]
    assert [r["reportable"] for r in short["properties"]] == [False, False, True, True]
    assert "paragraph (c)" in short["properties"][0]["reportable_basis"]
    assert short["deemed_capital_gain"] == 5000.0
    assert short["short_term_resident_rule"] == {
        "months_resident_in_last_120": 60,
        "threshold_months": 60,
        "applies": True,
        "basis": "ITA s. 128.1(4)(b)(iv)",
    }
    long = _leave(brought, months_resident_in_last_120=61)
    assert [r["exclusion"] for r in long["properties"]] == [
        None,
        None,
        "ITA s. 128.1(4)(b)(i)",
        None,
    ]
    assert long["deemed_capital_gain"] == 80000.0 + 49000.0 + 5000.0
    assert long["short_term_resident_rule"]["applies"] is False


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"event": "immigration"}, "Unsupported event"),
        ({"emigration_properties": []}, "need event"),
        ({"months_resident_in_last_120": 10}, "need event"),
        ({"event": "emigration", "months_resident_in_last_120": 121}, "between 0 and 120"),
        ({"event": "emigration", "months_resident_in_last_120": -1}, "between 0 and 120"),
        ({"event": "emigration", "is_domiciled": False}, "needs a factual resident"),
        (
            {"event": "emigration", "emigration_properties": [{"fair_market_value": -1.0}]},
            "emigration_properties[0]: fair_market_value",
        ),
        (
            {
                "event": "emigration",
                "emigration_properties": [{"fair_market_value": 1.0, "owner": "x"}],
            },
            "emigration_properties[0]: owner",
        ),
        (
            {"event": "emigration", "emigration_properties": [{"category": "gold"}]},
            "emigration_properties[0]: category",
        ),
        ({"days_in_state": 367}, "Day count"),
        ({"days_in_state": -1}, "Day count"),
        ({"federal_agi": -1.0}, "cannot be negative"),
        ({"capital_gains": -1.0}, "Capital gains cannot be negative"),
    ],
)
def test_canada_input_errors(kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(SafetyViolationError) as raised:
        _can(**kwargs)
    assert message in str(raised.value)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"event": "emigration"},
        {"emigration_properties": []},
        {"months_resident_in_last_120": 30},
    ],
)
def test_the_canada_inputs_are_refused_for_a_state(kwargs: dict[str, Any]) -> None:
    with pytest.raises(SafetyViolationError, match="apply only to jurisdiction .CAN."):
        _can(jurisdiction="CA", **kwargs)
    california = _can(jurisdiction="CA", federal_agi=100000.0)
    assert "currency" not in california["tax_computation"]


async def test_the_tool_surface_stays_at_eight_and_takes_the_canada_inputs() -> None:
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
        result = await client.call_tool(
            "compliance_evaluate_state_residency",
            {
                "jurisdiction": "Canada",
                "days_in_state": 100,
                "is_domiciled": True,
                "federal_agi": 50000.0,
                "tax_year": 2026,
                "event": "emigration",
                "emigration_properties": [{"fair_market_value": 30000.0}],
                "months_resident_in_last_120": 120,
            },
        )
    assert len(tools) == 8
    schema = tools["compliance_evaluate_state_residency"].input_schema["properties"]
    assert {"event", "emigration_properties", "months_resident_in_last_120"} <= set(schema)
    assert isinstance(result.structured_content, dict)
    departure = result.structured_content["departure_tax"]
    assert departure["deemed_capital_gain"] == 30000.0 and departure["t1161_required"] is True


# --- Ingest records and the LIMS renderer -------------------------------------------------


def test_an_ingest_record_hashes_and_exports_text_fr_only_when_set() -> None:
    fields: dict[str, Any] = {
        "id": "can-ita-2",
        "jurisdiction": "CAN",
        "corpus": "ITA",
        "citation": "ITA s. 2",
        "section": "2",
        "title": "Tax payable",
        "text_en": "English",
        "text_es": "Resumen",
        "effective_year": 2004,
        "source_url": f"{RAW}/{HEAD}/eng/acts/I-3.3.xml",
    }
    plain = StatuteRecord(**fields)
    french = StatuteRecord(**fields, text_fr="Français")
    assert plain.content_hash() != french.content_hash()
    assert plain.content_hash() == StatuteRecord(**fields, text_fr="").content_hash()
    assert "text_fr" not in plain.to_dict()
    assert french.to_dict()["text_fr"] == "Français"


LIMS = (
    "<Statute><Body>"
    "<Section><MarginalNote>Tax payable</MarginalNote><Label>9</Label>"
    "<Subsection><Label>(1)</Label>"
    "<Text>Hello <FootnoteRef>1</FootnoteRef>world<HistoricalNote>R.S., c. 1</HistoricalNote>"
    "</Text>"
    "<Paragraph><Label>(a)</Label><Text>first</Text></Paragraph></Subsection>"
    "<Subsection><Label>(2)</Label><Text>See<Repealed>[Repealed]</Repealed></Text></Subsection>"
    "</Section>"
    "<Section><Label>10</Label><Subsection><Label>(1)</Label><Text>The amount is</Text>"
    "<Formula><FormulaText>A - B</FormulaText></Formula>"
    "<FormulaConnector>where</FormulaConnector>"
    "<FormulaDefinition><FormulaTerm>A</FormulaTerm><Text>is the income.</Text>"
    "</FormulaDefinition></Subsection></Section>"
    "<Section><Label>11</Label></Section>"
    "</Body></Statute>"
)


def test_the_lims_renderer_turns_a_section_into_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "act.xml"
    path.write_text(LIMS, encoding="utf-8")
    assert extract_canada_lims.section_lines(str(path), "9") == [
        "Tax payable",
        "9 (1) Hello world",
        "(a) first",
        "(2) See [Repealed]",
    ]
    assert extract_canada_lims.section_lines(str(path), "10") == [
        "10 (1) The amount is",
        "A - B",
        "where",
        "A is the income.",
    ]
    assert extract_canada_lims.section_lines(str(path), "11") == ["11"]
    assert extract_canada_lims.flat(None) == ""
    with pytest.raises(SystemExit, match="no section"):
        extract_canada_lims.section_lines(str(path), "12")
    assert extract_canada_lims.main([str(path), "9"]) == 0
    assert json.loads(capsys.readouterr().out)[1] == "9 (1) Hello world"
    assert extract_canada_lims.main([]) == 2
    assert "usage" in capsys.readouterr().err
