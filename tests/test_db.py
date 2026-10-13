"""Unit tests for the DuckDB database layer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from taxcode_mcp.db import INITIAL_PUBLICATIONS, INITIAL_STATUTES, TaxDatabase


def test_initial_db_seeds_statutes_and_publications() -> None:
    """A fresh in-memory database initializes with default seed data."""
    test_db = TaxDatabase(":memory:")
    statutes = test_db.search_statutes(query="", limit=100)
    assert len(statutes) == len(INITIAL_STATUTES)

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
        "citation": "26 U.S.C. § 1",
        "section": "1",
        "title": "Tax imposed",
    }

    test_db.insert_statute(new_statute)
    found = test_db.lookup_statute("26 U.S.C. § 1")
    assert found is not None
    assert found["id"] == "custom-1"
    assert found["effective_year"] == 2026

    deleted = test_db.delete_statute("custom-1")
    assert deleted is True
    assert test_db.lookup_statute("26 U.S.C. § 1") is None

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
    assert len(second_db.search_statutes(query="", limit=100)) == len(INITIAL_STATUTES)

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
    assert len(second_db.list_publications()) == len(INITIAL_PUBLICATIONS)
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

    # Point-in-time lookup for 2024 tax year audit returns 2024 flat rate (5.49%)
    statute_2024 = test_db.lookup_statute("O.C.G.A. § 48-7-20", tax_year=2024)
    assert statute_2024 is not None
    assert statute_2024["effective_year"] == 2024
    assert statute_2024["superseded_year"] == 2025
    assert statute_2024["is_current"] is False
    assert "5.49%" in statute_2024["title"]

    # Point-in-time lookup for 2025 tax year returns 2025 rate (5.39%)
    statute_2025 = test_db.lookup_statute("O.C.G.A. § 48-7-20", tax_year=2025)
    assert statute_2025 is not None
    assert statute_2025["effective_year"] == 2025
    assert statute_2025["superseded_year"] == 2026
    assert "5.39%" in statute_2025["title"]

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
