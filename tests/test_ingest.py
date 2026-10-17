"""Unit tests for the statutory ingestion and update pipeline script."""

from __future__ import annotations

import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import duckdb
import pytest

from scripts.ingest_statutes import (
    PUBLICATIONS_CATALOG,
    STATUTES_CATALOG,
    check_upstream_endpoints,
    main,
    run_ingest,
)


def test_statute_and_publication_records_serialization() -> None:
    """Test data records serialization and content hashing."""
    st = STATUTES_CATALOG[0]
    st_dict = st.to_dict()
    assert st_dict["id"] == st.id
    assert st_dict["citation"] == st.citation
    h1 = st.content_hash()
    assert len(h1) == 64

    pub = PUBLICATIONS_CATALOG[0]
    pub_dict = pub.to_dict()
    assert pub_dict["id"] == pub.id
    assert pub_dict["doc_number"] == pub.doc_number
    h2 = pub.content_hash()
    assert len(h2) == 64


def test_run_ingest_dry_run() -> None:
    """Dry run validates records without executing database writes."""
    stats = run_ingest(db_path=":memory:", sources="all", dry_run=True)
    assert stats["statutes_considered"] == len(STATUTES_CATALOG)
    assert stats["statutes_upserted"] == 0
    assert stats["publications_considered"] == len(PUBLICATIONS_CATALOG)
    assert stats["publications_upserted"] == 0


def test_run_ingest_source_filters() -> None:
    """Filter by specific jurisdiction or corpus subsets."""
    us_stats = run_ingest(db_path=":memory:", sources="us", dry_run=True)
    assert us_stats["statutes_considered"] > 0
    assert us_stats["publications_considered"] == 6

    pr_stats = run_ingest(db_path=":memory:", sources="pr", dry_run=True)
    assert pr_stats["statutes_considered"] > 0
    assert pr_stats["publications_considered"] == 6

    cfr_stats = run_ingest(db_path=":memory:", sources="cfr", dry_run=True)
    assert cfr_stats["statutes_considered"] > 0
    assert cfr_stats["publications_considered"] == 0

    ga_stats = run_ingest(db_path=":memory:", sources="ga", dry_run=True)
    assert ga_stats["statutes_considered"] > 0
    assert ga_stats["publications_considered"] == 2

    ca_stats = run_ingest(db_path=":memory:", sources="ca", dry_run=True)
    assert ca_stats["statutes_considered"] == 4
    assert ca_stats["publications_considered"] == 0

    ny_stats = run_ingest(db_path=":memory:", sources="ny", dry_run=True)
    assert ny_stats["statutes_considered"] == 4
    assert ny_stats["publications_considered"] == 0

    fl_stats = run_ingest(db_path=":memory:", sources="fl", dry_run=True)
    assert fl_stats["statutes_considered"] == 3
    assert fl_stats["publications_considered"] == 0

    tx_stats = run_ingest(db_path=":memory:", sources="tx", dry_run=True)
    assert tx_stats["statutes_considered"] == 2
    assert tx_stats["publications_considered"] == 0

    wa_stats = run_ingest(db_path=":memory:", sources="wa", dry_run=True)
    assert wa_stats["statutes_considered"] == 3
    assert wa_stats["publications_considered"] == 0

    ma_stats = run_ingest(db_path=":memory:", sources="ma", dry_run=True)
    assert ma_stats["statutes_considered"] == 3
    assert ma_stats["publications_considered"] == 0

    nj_stats = run_ingest(db_path=":memory:", sources="nj", dry_run=True)
    assert nj_stats["statutes_considered"] == 3
    assert nj_stats["publications_considered"] == 0

    il_stats = run_ingest(db_path=":memory:", sources="il", dry_run=True)
    assert il_stats["statutes_considered"] == 3
    assert il_stats["publications_considered"] == 0

    pa_stats = run_ingest(db_path=":memory:", sources="pa", dry_run=True)
    assert pa_stats["statutes_considered"] == 3
    assert pa_stats["publications_considered"] == 1

    ct_stats = run_ingest(db_path=":memory:", sources="ct", dry_run=True)
    assert ct_stats["statutes_considered"] == 3
    assert ct_stats["publications_considered"] == 0

    nc_stats = run_ingest(db_path=":memory:", sources="nc", dry_run=True)
    assert nc_stats["statutes_considered"] == 2
    assert nc_stats["publications_considered"] == 0

    va_stats = run_ingest(db_path=":memory:", sources="va", dry_run=True)
    assert va_stats["statutes_considered"] == 3
    assert va_stats["publications_considered"] == 1

    md_stats = run_ingest(db_path=":memory:", sources="md", dry_run=True)
    assert md_stats["statutes_considered"] == 3
    assert md_stats["publications_considered"] == 1

    co_stats = run_ingest(db_path=":memory:", sources="co", dry_run=True)
    assert co_stats["statutes_considered"] == 2
    assert co_stats["publications_considered"] == 0

    az_stats = run_ingest(db_path=":memory:", sources="az", dry_run=True)
    assert az_stats["statutes_considered"] == 2
    assert az_stats["publications_considered"] == 0

    oh_stats = run_ingest(db_path=":memory:", sources="oh", dry_run=True)
    assert oh_stats["statutes_considered"] == 2
    assert oh_stats["publications_considered"] == 1

    pub_stats = run_ingest(db_path=":memory:", sources="publications", dry_run=True)
    assert pub_stats["statutes_considered"] == 0
    assert pub_stats["publications_considered"] == len(PUBLICATIONS_CATALOG)


def test_run_ingest_live_db_and_export_docs(tmp_path: Path) -> None:
    """Live run upserts data into DuckDB and exports documentation."""
    db_file = str(tmp_path / "tax.duckdb")
    docs_out = tmp_path / "docs"

    stats = run_ingest(
        db_path=db_file,
        sources="all",
        dry_run=False,
        export_docs=docs_out,
    )
    assert stats["statutes_upserted"] == len(STATUTES_CATALOG)
    assert stats["publications_upserted"] == len(PUBLICATIONS_CATALOG)

    # Verify documentation files
    llms_txt = docs_out / "llms.txt"
    llms_full_txt = docs_out / "llms-full.txt"
    assert llms_txt.exists()
    assert llms_full_txt.exists()
    assert "TaxCode MCP Intelligence Corpus" in llms_txt.read_text(encoding="utf-8")
    assert "26 U.S.C. § 933" in llms_full_txt.read_text(encoding="utf-8")
    assert "O.C.G.A. § 48-7-1" in llms_full_txt.read_text(encoding="utf-8")

    # Verify markdown subdirectories
    assert (docs_out / "us" / "irc_933.md").exists()
    assert (docs_out / "pr" / "ley60_2021_01.md").exists()
    assert (docs_out / "ga" / "ocga_48_7_1.md").exists()
    assert (docs_out / "ca" / "rtc_17014.md").exists()
    assert (docs_out / "ny" / "nytax_605.md").exists()
    assert (docs_out / "fl" / "flstat_222_17.md").exists()
    assert (docs_out / "tx" / "txconst_art__viii_sec__24_a.md").exists()
    assert (docs_out / "wa" / "rcw_82_87_040.md").exists()
    assert (docs_out / "ma" / "maconst_art__xliv.md").exists()
    assert (docs_out / "nj" / "njsa_54a_2_1.md").exists()
    assert (docs_out / "il" / "ilcs_5_201.md").exists()
    assert (docs_out / "pa" / "p.s._7302.md").exists()
    assert (docs_out / "ct" / "c.g.s._12_700.md").exists()
    assert (docs_out / "nc" / "n.c.g.s._105_153_7.md").exists()
    assert (docs_out / "va" / "va. code_58_1_320.md").exists()
    assert (docs_out / "md" / "md. code_10_105.md").exists()
    assert (docs_out / "co" / "c.r.s._39_22_104.md").exists()
    assert (docs_out / "az" / "a.r.s._43_1011.md").exists()
    assert (docs_out / "oh" / "r.c._5747_02.md").exists()
    assert (docs_out / "ak" / "alaska stat._43_05_080.md").exists()
    assert (docs_out / "de" / "del. code_1102.md").exists()
    assert (docs_out / "ut" / "utah code_59_10_104.md").exists()
    assert (docs_out / "wy" / "wyo. const._art__15,_§_18.md").exists()
    assert (docs_out / "la" / "la. r.s._47_32.md").exists()
    assert (docs_out / "la" / "la. r.s._47_32_2022.md").exists()


def test_check_upstream_endpoints() -> None:
    """Test upstream endpoint health check with mocked responses."""
    mock_resp = MagicMock()
    mock_resp.getcode.return_value = 200
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = check_upstream_endpoints()
        assert "OLRC_USLM" in res
        assert "Georgia_DOR" in res
        assert res["OLRC_USLM"]["status"] == "reachable"
        assert res["Georgia_DOR"]["status"] == "reachable"

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
        res_err = check_upstream_endpoints()
        assert "OLRC_USLM" in res_err
        assert res_err["OLRC_USLM"]["status"] == "unreachable_or_blocked"


def test_main_cli_invocations(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test CLI commands execution via main()."""
    # Dry run
    code_dry = main(["--dry-run", "--sources", "us"])
    assert code_dry == 0

    # Check upstream
    with patch(
        "scripts.ingest_statutes.check_upstream_endpoints",
        return_value={"test": {"status": "reachable"}},
    ):
        code_check = main(["--check-upstream"])
        assert code_check == 0
        captured = capsys.readouterr()
        assert "test" in captured.out

    # Full execution with docs
    db_file = str(tmp_path / "cli_tax.duckdb")
    docs_dir = str(tmp_path / "cli_docs")
    code_full = main(["--db-path", db_file, "--export-docs", docs_dir])
    assert code_full == 0
    assert (tmp_path / "cli_docs" / "llms.txt").exists()


def test_ingest_writes_both_2022_01_rows_and_lookup_prefers_current(tmp_path: Path) -> None:
    """Ingest into a file DB writes both 2022.01 rows; lookups pick by tax year."""
    from taxcode_mcp.db import TaxDatabase

    db_file = tmp_path / "ingest.duckdb"
    run_ingest(db_path=str(db_file), sources="pr", dry_run=False)

    # Read ingest's rows straight from DuckDB before TaxDatabase reopens and re-seeds.
    raw = duckdb.connect(str(db_file), read_only=True)
    try:
        raw_rows = raw.execute(
            "SELECT id, effective_year, superseded_year, source_url, text_en, cross_references "
            "FROM statutes WHERE section = '2022.01' ORDER BY id;"
        ).fetchall()
    finally:
        raw.close()
    expected = {r.id: r for r in STATUTES_CATALOG if r.section == "2022.01"}
    assert [r[0] for r in raw_rows] == ["pr-ley60-2022-01", "pr-ley60-2022-01-2019"]
    for rid, eff, sup, url, text_en, xrefs in raw_rows:
        rec = expected[rid]
        assert (eff, sup, url, text_en) == (
            rec.effective_year,
            rec.superseded_year,
            rec.source_url,
            rec.text_en,
        )
        assert list(xrefs) == rec.cross_references

    target = TaxDatabase(str(db_file))

    current = target.lookup_statute("2022.01", jurisdiction="PR")
    assert current is not None
    assert current["id"] == "pr-ley60-2022-01"
    assert current["superseded_year"] is None
    assert "Act 38-2026" in current["text_en"]

    old = target.lookup_statute("2022.01", jurisdiction="PR", tax_year=2020)
    assert old is not None
    assert old["id"] == "pr-ley60-2022-01-2019"
    assert old["superseded_year"] == 2026
    assert "4%" not in old["text_en"] and "4%" not in old["text_es"]
    assert old["cross_references"] == ["Ley 60-2019 Sec. 2022.02", "26 CFR § 1.937-2"]

    later = target.lookup_statute("2022.01", jurisdiction="PR", tax_year=2027)
    assert later is not None
    assert later["id"] == "pr-ley60-2022-01"


def test_seed_and_ingest_records_match_for_shared_ids() -> None:
    """Every statute id present in both the seeds and ingest has identical fields."""
    from taxcode_mcp.db import INITIAL_STATUTES

    seeds = {s["id"]: {"superseded_year": None, **s} for s in INITIAL_STATUTES}
    shared = 0
    for rec in STATUTES_CATALOG:
        if rec.id in seeds:
            shared += 1
            assert seeds[rec.id] == rec.to_dict(), rec.id
    assert shared > 0


def test_pr_act60_sources_use_pdf_urls() -> None:
    """Act 60 records cite the OGP PDFs, not the library homepage."""
    for rec in STATUTES_CATALOG:
        if rec.citation.startswith("Ley 60-2019"):
            assert rec.source_url.endswith(".pdf"), rec.id


def test_catalog_ids_equal_seed_ids() -> None:
    """The ingest catalog and the db.py seeds hold exactly the same ids, both ways."""
    from taxcode_mcp.db import INITIAL_PUBLICATIONS, INITIAL_STATUTES

    seed_statutes = {s["id"] for s in INITIAL_STATUTES}
    catalog_statutes = {r.id for r in STATUTES_CATALOG}
    assert catalog_statutes - seed_statutes == set()
    assert seed_statutes - catalog_statutes == set()
    assert len(seed_statutes) == len(INITIAL_STATUTES)
    assert len(catalog_statutes) == len(STATUTES_CATALOG)

    seed_pubs = {p["id"] for p in INITIAL_PUBLICATIONS}
    catalog_pubs = {p.id for p in PUBLICATIONS_CATALOG}
    assert catalog_pubs - seed_pubs == set()
    assert seed_pubs - catalog_pubs == set()
    assert len(seed_pubs) == len(INITIAL_PUBLICATIONS)
    assert len(catalog_pubs) == len(PUBLICATIONS_CATALOG)


def test_formerly_catalog_only_entries_are_seeded_from_pinned_sources() -> None:
    """The eight entries once missing from the seeds match the catalog and cite pinned sources."""
    from taxcode_mcp.db import (
        ECFR_TITLE26_DATE,
        INITIAL_PUBLICATIONS,
        INITIAL_STATUTES,
        USLM_TITLE26_SOURCE_URL,
    )

    cri_pdf = (
        "https://hacienda.pr.gov/sites/default/files/"
        "codigo_de_rentas_internas_2011_segun_enmendado_28.08.2026.pdf"
    )
    statute_sources = {
        "us-irc-957": USLM_TITLE26_SOURCE_URL,
        "us-irc-6688": USLM_TITLE26_SOURCE_URL,
        "us-cfr-1-937-3": (f"https://www.ecfr.gov/on/{ECFR_TITLE26_DATE}/title-26/section-1.937-3"),
        "pr-cri-1031-02": cri_pdf,
        "pr-cri-1062-01": cri_pdf,
        "pr-ley60-6070-55": "https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF/60-2019.pdf",
    }
    seeds = {s["id"]: {"superseded_year": None, **s} for s in INITIAL_STATUTES}
    catalog = {r.id: r.to_dict() for r in STATUTES_CATALOG}
    for sid, url in statute_sources.items():
        assert seeds[sid] == catalog[sid], sid
        assert seeds[sid]["source_url"] == url, sid

    pubs = {p["id"]: p for p in INITIAL_PUBLICATIONS}
    pub_catalog = {p.id: p.to_dict() for p in PUBLICATIONS_CATALOG}
    for pid in ("pr-bi-26-02", "pr-cc-ri-25-12"):
        assert pubs[pid] == pub_catalog[pid], pid
        assert pubs[pid]["source_url"].startswith(
            "https://hacienda.pr.gov/sites/default/files/publicaciones/"
        )
        assert pubs[pid]["source_url"].endswith(".pdf")
