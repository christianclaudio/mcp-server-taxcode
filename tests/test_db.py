"""Unit tests for the DuckDB database layer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from taxcode_mcp.db import (
    INITIAL_PUBLICATIONS,
    INITIAL_STATUTES,
    LEGACY_DATED_STATUTE_IDS,
    TaxDatabase,
)

# Rows a search or lookup without a tax year can return: every current row and the dated
# rows that predate the prior-year versions. Newer dated rows answer only for their years.
NO_YEAR_STATUTES = [
    s
    for s in INITIAL_STATUTES
    if s["superseded_year"] is None or s["id"] in LEGACY_DATED_STATUTE_IDS
]


def test_initial_db_seeds_statutes_and_publications() -> None:
    """A fresh in-memory database initializes with default seed data."""
    test_db = TaxDatabase(":memory:")
    statutes = test_db.search_statutes(query="", limit=500)
    assert len(statutes) == len(NO_YEAR_STATUTES)

    pubs = test_db.list_publications(limit=100)
    assert len(pubs) == len(INITIAL_PUBLICATIONS)

    # Test get_connection on memory mode
    conn = test_db.get_connection()
    assert conn is not None


def test_lookup_statute_by_citation_and_section() -> None:
    """Lookup finds statutes by exact citation, section, or normalized format."""
    test_db = TaxDatabase(":memory:")

    # Lookup 26 U.S.C. § 933
    res_en = test_db.lookup_statute("26 U.S.C. § 933", lang="en")
    assert res_en is not None
    assert res_en["citation"] == "26 U.S.C. § 933"
    assert res_en["section"] == "933"
    assert "bona fide resident" in res_en["operative_text"]
    assert res_en["language"] == "en"
    assert res_en["official_language"] == "en"
    assert res_en["content_type"] == "official_text"
    assert res_en["unofficial_summary"] is False
    assert "English statutory text controls" in res_en["translation_disclaimer"]

    # Spanish language lookup
    res_es = test_db.lookup_statute("933", lang="es")
    assert res_es is not None
    assert "residente de buena fe" in res_es["operative_text"]
    assert res_es["language"] == "es"

    # Normalized spacing lookup
    res_norm = test_db.lookup_statute("26U.S.C.§933")
    assert res_norm is not None

    # Puerto Rico official language flag
    res_pr = test_db.lookup_statute("PR CRI Sec. 1031.01")
    assert res_pr is not None
    assert res_pr["jurisdiction"] == "PR"
    assert res_pr["official_language"] == "es"

    # Lookup with jurisdiction filter
    res_filtered = test_db.lookup_statute("937", jurisdiction="US")
    assert res_filtered is not None
    assert res_filtered["jurisdiction"] == "US"

    res_miss_filter = test_db.lookup_statute("937", jurisdiction="PR")
    assert res_miss_filter is None

    # Georgia lookup
    res_ga = test_db.lookup_statute("O.C.G.A. § 48-7-1")
    assert res_ga is not None
    assert res_ga["jurisdiction"] == "GA"
    assert res_ga["corpus"] == "OCGA"
    assert "Georgia resident" in res_ga["operative_text"]

    # California lookup (Closest connection / non-transitory purpose)
    res_ca = test_db.lookup_statute("Cal. Rev. & Tax Code § 17014")
    assert res_ca is not None
    assert res_ca["jurisdiction"] == "CA"
    assert "temporary or transitory purpose" in res_ca["operative_text"]

    # New York lookup (Convenience of employer rule)
    res_ny = test_db.lookup_statute("20 NYCRR § 132.18")
    assert res_ny is not None
    assert res_ny["jurisdiction"] == "NY"
    assert "convenience" in res_ny["operative_text"]

    # Florida lookup (Declaration of domicile)
    res_fl = test_db.lookup_statute("Fla. Stat. § 222.17")
    assert res_fl is not None
    assert res_fl["jurisdiction"] == "FL"

    # Texas lookup (Constitutional prohibition)
    res_tx = test_db.lookup_statute("Tex. Const. art. VIII § 24-a")
    assert res_tx is not None
    assert res_tx["jurisdiction"] == "TX"

    # Washington lookup (Capital gains excise tax)
    res_wa = test_db.lookup_statute("RCW 82.87.040")
    assert res_wa is not None
    assert res_wa["jurisdiction"] == "WA"

    # Massachusetts lookup (Fair Share Amendment)
    res_ma = test_db.lookup_statute("Mass. Const. amend. art. XLIV")
    assert res_ma is not None
    assert res_ma["jurisdiction"] == "MA"

    # New Jersey lookup (Gross income tax rates)
    res_nj = test_db.lookup_statute("N.J.S.A. 54A:2-1")
    assert res_nj is not None
    assert res_nj["jurisdiction"] == "NJ"

    # Illinois lookup (Flat tax rate)
    res_il = test_db.lookup_statute("35 ILCS 5/201")
    assert res_il is not None
    assert res_il["jurisdiction"] == "IL"

    # Non-existent statute
    assert test_db.lookup_statute("Non-existent 9999") is None


def test_search_statutes_filters_and_limits() -> None:
    """Search matches keywords and respects jurisdiction, corpus, and limits."""
    test_db = TaxDatabase(":memory:")

    # General search
    results = test_db.search_statutes("residence", limit=3)
    assert 1 <= len(results) <= 3

    # Filter by jurisdiction and corpus simultaneously
    pr_results = test_db.search_statutes(
        "exención", jurisdiction="PR", corpus="LEY60", lang="es", limit=5
    )
    assert len(pr_results) >= 1
    assert all(r["jurisdiction"] == "PR" and r["corpus"] == "LEY60" for r in pr_results)


def test_list_publications_filters() -> None:
    """List publications filters by category and date."""
    test_db = TaxDatabase(":memory:")

    all_pubs = test_db.list_publications(limit=10)
    assert len(all_pubs) >= 1

    # Filter by category
    cc_pubs = test_db.list_publications(category="carta-circular")
    assert len(cc_pubs) >= 1
    assert all(p["category"] == "carta-circular" for p in cc_pubs)

    # Filter by since_date
    recent_pubs = test_db.list_publications(since_date="2025-01-01")
    assert len(recent_pubs) >= 1
    assert all(p["doc_date"] >= "2025-01-01" for p in recent_pubs)


def test_insert_and_delete_statute() -> None:
    """Insert and delete operations modify the database correctly."""
    test_db = TaxDatabase(":memory:")
    new_statute: dict[str, Any] = {
        "id": "custom-1",
        "jurisdiction": "US",
        "corpus": "IRC",
        "citation": "26 U.S.C. § 9999",
        "section": "9999",
        "title": "Test section",
    }

    test_db.insert_statute(new_statute)
    found = test_db.lookup_statute("26 U.S.C. § 9999")
    assert found is not None
    assert found["id"] == "custom-1"
    assert found["effective_year"] == 2026

    deleted = test_db.delete_statute("custom-1")
    assert deleted is True
    assert test_db.lookup_statute("26 U.S.C. § 9999") is None

    # Deleting non-existent ID returns False
    assert test_db.delete_statute("non-existent") is False


def test_file_based_duckdb_lifecycle(tmp_path: Path) -> None:
    """A file-backed DuckDB instance can be initialized, queried, modified, and closed."""
    db_file = str(tmp_path / "test_tax.duckdb")
    file_db = TaxDatabase(db_file)

    conn = file_db.get_connection()
    try:
        rows = conn.execute("SELECT COUNT(*) FROM statutes;").fetchone()
        assert rows is not None and rows[0] == len(INITIAL_STATUTES)
    finally:
        conn.close()

    # Re-instantiating on the same file does not re-insert duplicate seeds
    second_db = TaxDatabase(db_file)
    assert len(second_db.search_statutes(query="", limit=500)) == len(NO_YEAR_STATUTES)

    # Insert on file db
    second_db.insert_statute(
        {
            "id": "file-1",
            "jurisdiction": "US",
            "corpus": "IRC",
            "citation": "26 U.S.C. § 2",
            "section": "2",
            "title": "Definitions",
        }
    )
    assert second_db.lookup_statute("26 U.S.C. § 2") is not None
    assert len(second_db.list_publications(limit=100)) == len(INITIAL_PUBLICATIONS)
    assert second_db.delete_statute("file-1") is True

    # Test publication on file db
    second_db.upsert_publication(
        {
            "id": "file-pub-1",
            "category": "carta-circular",
            "doc_number": "CC FILE 1",
            "doc_date": "2026-02-01",
            "title": "File Pub",
        }
    )
    assert second_db.delete_publication("file-pub-1") is True


def test_upsert_statute_and_publication() -> None:
    """Upsert inserts new records and updates existing records seamlessly."""
    test_db = TaxDatabase(":memory:")

    # Upsert a new statute
    statute_payload: dict[str, Any] = {
        "id": "test-upsert-1",
        "jurisdiction": "US",
        "corpus": "IRC",
        "citation": "26 U.S.C. § 999",
        "section": "999",
        "title": "International boycott factor",
        "text_en": "Reports regarding participation in international boycotts.",
        "text_es": "Informes sobre participación en boicots internacionales.",
        "effective_year": 1976,
        "source_url": "https://uscode.house.gov/",
        "cross_references": ["26 U.S.C. § 908"],
    }
    test_db.upsert_statute(statute_payload)
    item = test_db.lookup_statute("26 U.S.C. § 999")
    assert item is not None
    assert item["title"] == "International boycott factor"

    # Modify and upsert the same statute ID
    statute_payload["title"] = "Updated International boycott factor"
    test_db.upsert_statute(statute_payload)
    item_updated = test_db.lookup_statute("26 U.S.C. § 999")
    assert item_updated is not None
    assert item_updated["title"] == "Updated International boycott factor"

    # Upsert new publication
    pub_payload: dict[str, Any] = {
        "id": "pub-test-1",
        "category": "boletin-informativo",
        "doc_number": "BI 26-01",
        "doc_date": "2026-01-15",
        "title": "Boletín sobre retención en el origen",
        "summary": "Resumen sobre tasas de retención.",
        "source_url": "https://hacienda.pr.gov/bi-26-01",
    }
    test_db.upsert_publication(pub_payload)
    pubs = test_db.list_publications(category="boletin-informativo")
    assert any(p["doc_number"] == "BI 26-01" for p in pubs)

    # Modify and upsert the same publication ID
    pub_payload["title"] = "Boletín Actualizado"
    test_db.upsert_publication(pub_payload)
    pubs_updated = test_db.list_publications(category="boletin-informativo")
    assert any(
        p["doc_number"] == "BI 26-01" and p["title"] == "Boletín Actualizado" for p in pubs_updated
    )

    # Delete publication
    assert test_db.delete_publication("pub-test-1") is True
    assert test_db.delete_publication("non-existent-pub") is False


def test_lookup_statute_temporal_versioning() -> None:
    """Temporal lookups return year-specific statutes or default to the latest version."""
    test_db = TaxDatabase(":memory:")

    # Default lookup without tax_year returns the latest (2026) version
    latest = test_db.lookup_statute("O.C.G.A. § 48-7-20")
    assert latest is not None
    assert latest["effective_year"] == 2026
    assert latest["superseded_year"] is None
    assert latest["is_current"] is True
    assert len(latest["available_versions"]) == 3

    # Point-in-time lookup for 2024 tax year audit returns 2024 flat rate (5.39%)
    statute_2024 = test_db.lookup_statute("O.C.G.A. § 48-7-20", tax_year=2024)
    assert statute_2024 is not None
    assert statute_2024["effective_year"] == 2024
    assert statute_2024["superseded_year"] == 2025
    assert statute_2024["is_current"] is False
    assert "5.39%" in statute_2024["title"]

    # Point-in-time lookup for 2025 tax year returns 2025 rate (5.19%)
    statute_2025 = test_db.lookup_statute("O.C.G.A. § 48-7-20", tax_year=2025)
    assert statute_2025 is not None
    assert statute_2025["effective_year"] == 2025
    assert statute_2025["superseded_year"] == 2026
    assert "5.19%" in statute_2025["title"]

    # Predecessor PR Act 22 lookup for 2015 tax year
    act22 = test_db.lookup_statute("Act 22-2012 Sec. 3", tax_year=2015)
    assert act22 is not None
    assert act22["effective_year"] == 2012
    assert act22["superseded_year"] == 2019
    assert act22["is_current"] is False

    # Act 22 in tax year 2022 was already superseded by Ley 60 (returns None)
    act22_superseded = test_db.lookup_statute("Act 22-2012 Sec. 3", tax_year=2022)
    assert act22_superseded is None


def test_search_statutes_temporal_and_descending_order() -> None:
    """Search results sort by effective_year descending and filter by tax_year."""
    test_db = TaxDatabase(":memory:")

    # Searching across GA returns newest versions first
    results = test_db.search_statutes("Flat Rate", jurisdiction="GA", limit=10)
    assert len(results) >= 3
    years = [r["effective_year"] for r in results if r["citation"] == "O.C.G.A. § 48-7-20"]
    assert years == sorted(years, reverse=True)

    # Searching with tax_year=2024 filters to versions valid in 2024
    results_2024 = test_db.search_statutes(
        "Individual Income Tax Rates", jurisdiction="GA", tax_year=2024, limit=10
    )
    for r in results_2024:
        if r["citation"] == "O.C.G.A. § 48-7-20":
            assert r["effective_year"] == 2024


def test_ley60_sections_amended_by_act_38_2026_are_current_from_2026() -> None:
    """The other sections Act 38-2026 amended carry its text from tax year 2026 on."""
    test_db = TaxDatabase(":memory:")
    expected = {
        "1020.02": "durante un mínimo de seis (6) años previos a la fecha de mudarse",
        "2022.02": "antes del 1 de enero de 2056, estará sujeta a una tasa fija preferencial",
        "6020.03": "(d) Reglas aplicables a decretos de Individuo Residente Inversionista.",
        "6020.10": "a nombre de un fideicomiso descrito en la Sección 2022.07.",
    }
    for section, phrase in expected.items():
        row = test_db.lookup_statute(f"Ley 60-2019 Sec. {section}", tax_year=2026)
        assert row is not None, section
        assert row["effective_year"] == 2026 and row["superseded_year"] is None, section
        assert phrase in row["text_es"], section
        assert "Ley 38-2026" in row["text_es"] and "Act 38-2026" in row["text_en"], section
        assert row["text_en"].startswith("English summary; the official text is in Spanish.")


def test_ley60_6020_10_lookup_by_tax_year_splits_on_ley_65_2025_and_act_38_2026() -> None:
    """2019 is Ley 60-2019 as approved; to 2023 the Ley 40-2020 row answers, in 2024 the
    Ley 32-2024 row, in 2025 the Ley 65-2025 row, and from 2026 the Act 38 row."""
    test_db = TaxDatabase(":memory:")
    for year in (2020, 2023):
        old = test_db.lookup_statute("Ley 60-2019 Sec. 6020.10", tax_year=year)
        assert old is not None, year
        assert old["id"] == "pr-ley60-6020-10-2019"
        assert old["effective_year"] == 2020 and old["superseded_year"] == 2024
        # Neither Ley 65-2025's (e) and (f) nor Act 38-2026's 2027 registry rule is in it.
        assert "\n(e) " not in old["text_es"] and "\n(f) " not in old["text_es"]
        assert "comenzados luego del 31 de diciembre de 2024" not in old["text_es"]
        assert "1 de enero de 2027" not in old["text_es"] and "Ley 38-2026" not in old["text_es"]
        # The fine wording from 2020 to Ley 65 is not on file; it is flagged, not reconstructed.
        assert "su texto desde 2020 hasta la Ley 65-2025 no se ha verificado" in old["text_es"]
        assert "Sección 89 de la Ley 40-2020" in old["text_es"]
        # (d) took effect April 16, 2020; 2019 is answered by Ley 60-2019 as approved.
        assert "vigente desde su aprobación el 16 de abril de 2020 (Sección 97)" in old["text_es"]
        assert "2019 se contesta con la Ley 60-2019 según aprobada" in old["text_es"]
        assert (
            "effective April 16, 2020 (in force on approval under its Section 97)" in old["text_en"]
        )
        assert "tax year 2019 is answered by Ley 60-2019 as approved" in old["text_en"]
        assert old["text_es"].endswith(
            "Versión sustituida en 2024 por la Ley 32-2024 (pr-ley60-6020-10-2024).]"
        )
        assert "its wording from 2020 until Ley 65-2025 is not on file" in old["text_en"]
        assert "Ley 40-2020, Section 89" in old["text_en"]
        assert "superseded in 2024 by the text of (b) as amended by Ley 32-2024" in old["text_en"]
        assert "December 31, 2024" not in old["text_en"]
    y19 = test_db.lookup_statute("Ley 60-2019 Sec. 6020.10", tax_year=2019)
    assert y19 is not None and y19["id"] == "pr-ley60-6020-10-2019-as-enacted"
    assert "\n(d) " not in y19["text_es"] and "un cincuenta por ciento (50%)" in y19["text_es"]
    y24 = test_db.lookup_statute("Ley 60-2019 Sec. 6020.10", tax_year=2024)
    assert y24 is not None
    assert y24["id"] == "pr-ley60-6020-10-2024"
    assert y24["effective_year"] == 2024 and y24["superseded_year"] == 2025
    assert "\n(e) " not in y24["text_es"] and "\n(f) " not in y24["text_es"]
    assert "1 de enero de 2027" not in y24["text_es"] and "Ley 38-2026" not in y24["text_es"]
    assert "Artículo 9 de la Ley 32-2024" in y24["text_es"]
    assert y24["text_es"].endswith(
        "Versión sustituida en 2025 por la Ley 65-2025 (pr-ley60-6020-10-2025).]"
    )
    assert "superseded in 2025 by the text as amended by Ley 65-2025" in y24["text_en"]
    mid = test_db.lookup_statute("Ley 60-2019 Sec. 6020.10", tax_year=2025)
    assert mid is not None
    assert mid["id"] == "pr-ley60-6020-10-2025"
    assert mid["effective_year"] == 2025 and mid["superseded_year"] == 2026
    assert "1 de enero de 2027" not in mid["text_es"] and "Ley 38-2026" not in mid["text_es"]
    new = test_db.lookup_statute("Ley 60-2019 Sec. 6020.10", tax_year=2026)
    assert new is not None
    assert new["id"] == "pr-ley60-6020-10"
    # Every summary of (c) carries the seller and ownership conditions of the Spanish text.
    for row in (old, y24, mid, new):
        assert "totalmente desvinculada y ajena" in row["text_es"]
        assert "exclusivo y completo dominio" in row["text_es"]
        assert "wholly unrelated to and independent of the decree holder" in row["text_en"]
        assert "keep exclusive and complete ownership" in row["text_en"]
    assert new["effective_year"] == 2026 and new["superseded_year"] is None
    assert "Act 38-2026" in new["text_en"]
    assert "1 de enero de 2027" in new["text_es"] and "\n(f)" in new["text_es"]


def test_ley60_6020_10_lookup_2025_carries_ley_65_2025_paragraphs() -> None:
    """The 2025 row holds Ley 65-2025's (d), (e) and (f), and says when they took effect."""
    test_db = TaxDatabase(":memory:")
    row = test_db.lookup_statute("Ley 60-2019 Sec. 6020.10", tax_year=2025)
    assert row is not None
    assert row["id"] == "pr-ley60-6020-10-2025"
    es, en = row["text_es"], row["text_en"]
    assert es.index("\n(d) ") < es.index("\n(e) ") < es.index("\n(f) ") < es.index("[Nota:")
    assert "comenzados luego del 31 de diciembre de 2024" in es
    assert "podrá imponer una multa administrativa" in es.split("\n(e) ")[1]
    assert "Artículo 31 de la Ley 65-2025" in es and "17 de julio de 2025" in es
    assert "no desde el 1 de enero de 2025" in es
    assert es.endswith("Versión sustituida en 2026.]")
    assert "taxable years beginning after December 31, 2024" in en
    assert "publication of general applicability (f)" in en
    assert "Orden Administrativa Núm. DDEC 2026-01 of February 5, 2026" in en
    assert "under (f) was not required for taxable year 2025" in en
    assert "Ley 65-2025 (approved July 17, 2025" in en and "Article 37" in en
    assert "split dates from then, not from January 1, 2025" in en
    assert "superseded in 2026 by the text as amended by Act 38-2026 (pr-ley60-6020-10)" in en
    new = test_db.lookup_statute("Ley 60-2019 Sec. 6020.10", tax_year=2026)
    assert new is not None and new["id"] == "pr-ley60-6020-10"

    # (d), (e) and (f) read the same in the 2025 row and the Act 38-2026 row.
    def tail(text: str) -> str:
        return text[text.index("\n(d) ") : text.index("\n[Nota")]

    assert tail(es) == tail(new["text_es"])


def test_ley60_2022_01_lookup_by_tax_year_splits_on_act_38_2026() -> None:
    test_db = TaxDatabase(":memory:")
    old = test_db.lookup_statute("Ley 60-2019 Sec. 2022.01", tax_year=2020)
    assert old is not None
    assert old["id"] == "pr-ley60-2022-01-2019"
    assert old["effective_year"] == 2019 and old["superseded_year"] == 2026
    assert "before January 1, 2036" in old["text_en"]
    assert "Capital gains are governed by Section 2022.02" in old["text_en"]
    new = test_db.lookup_statute("Ley 60-2019 Sec. 2022.01", tax_year=2027)
    assert new is not None
    assert new["id"] == "pr-ley60-2022-01"
    assert new["effective_year"] == 2026 and new["superseded_year"] is None
    assert "Act 38-2026" in new["text_en"]


def test_file_db_refreshes_changed_seed_rows_on_startup(tmp_path: Path) -> None:
    db_file = str(tmp_path / "stale.duckdb")
    TaxDatabase(db_file)
    conn = duckdb.connect(db_file)
    conn.execute(
        "UPDATE statutes SET text_en = 'stale', text_es = 'viejo' WHERE id = 'pr-ley60-2022-01';"
    )
    conn.execute("DELETE FROM statutes WHERE id = 'pr-ley60-2022-01-2019';")
    conn.close()
    refreshed = TaxDatabase(db_file)
    seeds = {s["id"]: s for s in INITIAL_STATUTES}
    row = refreshed.lookup_statute("Ley 60-2019 Sec. 2022.01", tax_year=2027)
    assert row is not None
    assert row["text_en"] == seeds["pr-ley60-2022-01"]["text_en"]
    assert row["text_es"] == seeds["pr-ley60-2022-01"]["text_es"]
    old = refreshed.lookup_statute("Ley 60-2019 Sec. 2022.01", tax_year=2020)
    assert old is not None and old["id"] == "pr-ley60-2022-01-2019"


def test_file_db_upgrade_adds_new_seed_rows_without_duplicates(tmp_path: Path) -> None:
    """An older file DB gains seed rows added since it was built, once, without clobbering."""
    db_file = str(tmp_path / "older.duckdb")
    TaxDatabase(db_file)
    new_statutes = ("pr-ley60-6020-10", "pr-cri-1062-03")
    new_pubs = ("pr-bi-26-02", "pr-cc-ri-25-12")
    conn = duckdb.connect(db_file)
    for sid in new_statutes:
        conn.execute("DELETE FROM statutes WHERE id = ?;", [sid])
    for pid in new_pubs:
        conn.execute("DELETE FROM publications WHERE id = ?;", [pid])
    conn.execute("UPDATE publications SET summary = 'nota local' WHERE id = 'pr-cc-ri-21-10';")
    conn.execute(
        "INSERT INTO publications VALUES ('local-1', 'carta-circular', 'CC RI 99-01', "
        "'2026-01-01', 'Local', 'Local row', 'https://example.org/local');"
    )
    assert conn.execute("SELECT COUNT(*) FROM publications;").fetchone() == (
        len(INITIAL_PUBLICATIONS) - len(new_pubs) + 1,
    )
    conn.close()

    TaxDatabase(db_file)
    TaxDatabase(db_file)

    conn = duckdb.connect(db_file)
    statute_ids = [r[0] for r in conn.execute("SELECT id FROM statutes;").fetchall()]
    pub_ids = [r[0] for r in conn.execute("SELECT id FROM publications;").fetchall()]
    doc_numbers = [r[0] for r in conn.execute("SELECT doc_number FROM publications;").fetchall()]
    local_summary = conn.execute(
        "SELECT summary FROM publications WHERE id = 'pr-cc-ri-21-10';"
    ).fetchone()
    conn.close()
    assert set(new_statutes) <= set(statute_ids)
    assert set(new_pubs) <= set(pub_ids)
    assert len(statute_ids) == len(set(statute_ids)) == len(INITIAL_STATUTES)
    assert len(pub_ids) == len(set(pub_ids)) == len(INITIAL_PUBLICATIONS) + 1
    assert len(doc_numbers) == len(set(doc_numbers))
    assert local_summary == ("nota local",)


def test_search_without_tax_year_shows_years_on_both_2022_01_rows() -> None:
    """Both Sec. 2022.01 rows return with years; the 2019 row reads superseded 2026."""
    test_db = TaxDatabase(":memory:")
    results = test_db.search_statutes("Sec. 2022.01", jurisdiction="PR", limit=50)
    rows = {r["id"]: r for r in results if r["section"] == "2022.01"}
    assert set(rows) == {"pr-ley60-2022-01", "pr-ley60-2022-01-2019"}

    old = rows["pr-ley60-2022-01-2019"]
    assert old["effective_year"] == 2019
    assert old["superseded_year"] == 2026
    assert old["is_current"] is False
    assert old["validity"] == {
        "en": "Effective 2019; superseded 2026",
        "es": "Vigente desde 2019; sustituida en 2026",
    }

    new = rows["pr-ley60-2022-01"]
    assert new["effective_year"] == 2026
    assert new["superseded_year"] is None
    assert new["validity"] == {
        "en": "Effective 2026; current",
        "es": "Vigente desde 2026; en vigor",
    }


def test_lookup_versions_carry_validity_labels() -> None:
    """Lookup output and each available version carry the bilingual validity label."""
    test_db = TaxDatabase(":memory:")
    res = test_db.lookup_statute("Ley 60-2019 Sec. 2022.01", tax_year=2020)
    assert res is not None
    assert res["validity"]["en"] == "Effective 2019; superseded 2026"
    labels = {v["id"]: v["validity"]["es"] for v in res["available_versions"]}
    assert labels["pr-ley60-2022-01"] == "Vigente desde 2026; en vigor"
    assert labels["pr-ley60-2022-01-2019"] == "Vigente desde 2019; sustituida en 2026"


def test_available_versions_prefer_unsuperseded_on_equal_effective_year() -> None:
    """available_versions orders by effective_year desc, then current before superseded."""
    test_db = TaxDatabase(":memory:")
    base = dict(INITIAL_STATUTES[0])
    citation = "Test Tie Sec. 1"
    for rid, sup in (("tie-old", 2030), ("tie-new", None)):
        test_db.upsert_statute(
            {
                **base,
                "id": rid,
                "citation": citation,
                "section": "tie-1",
                "effective_year": 2025,
                "superseded_year": sup,
            }
        )
    res = test_db.lookup_statute(citation)
    assert res is not None
    assert res["id"] == "tie-new"
    assert [v["id"] for v in res["available_versions"]] == ["tie-new", "tie-old"]
