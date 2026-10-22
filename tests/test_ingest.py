"""Unit tests for the statutory ingestion and update pipeline script."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest

from scripts.ingest_statutes import (
    PUBLICATIONS_CATALOG,
    STATUTES_CATALOG,
    UPSTREAM_ENDPOINTS,
    _probe_upstream_endpoints,
    check_upstream_endpoints,
    main,
    run_ingest,
)
from tests.fake_network import FakeNetwork, answer


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
    assert pr_stats["publications_considered"] == 26

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
    assert "(./us/llms-full.txt): " in llms_full_txt.read_text(encoding="utf-8")
    assert "26 U.S.C. § 933" in (docs_out / "us" / "llms-full.txt").read_text(encoding="utf-8")
    assert "O.C.G.A. § 48-7-1" in (docs_out / "ga" / "llms-full.txt").read_text(encoding="utf-8")

    # Verify markdown subdirectories
    assert (docs_out / "us" / "irc_933.md").exists()
    assert (docs_out / "pr" / "ley60_2021_01.md").exists()
    # A row with a source note says where its text came from; rows without one keep the bare URL.
    source_2019 = next(
        line
        for line in (docs_out / "pr" / "ley60_6020_10_2019.md")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.startswith("- **Source:**")
    )
    assert (
        "— Text taken from the OGP compilation of Ley 60-2019 as revised with later" in source_2019
    )
    assert "Ley 65-2025's (e) and (f) removed; (d) checked against Ley 40-2020" in source_2019
    assert "which added it effective April 16, 2020 (Section 97)" in source_2019
    assert source_2019.endswith("its exact earlier wording is not on file.")
    source_2025 = next(
        line
        for line in (docs_out / "pr" / "ley60_6020_10_2025.md")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.startswith("- **Source:**")
    )
    assert "(d), (e) and (f) checked against Ley 65-2025, Article 31" in source_2025
    source_2026 = next(
        line
        for line in (docs_out / "pr" / "ley60_6020_10.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("- **Source:**")
    )
    assert source_2026.endswith("60-2019.pdf)")
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


def test_2026_publications_cite_the_issuing_agency_pdf() -> None:
    """Every 2026 Hacienda and DDEC publication in the seeds matches the catalog and its PDF."""
    from taxcode_mcp.db import INITIAL_PUBLICATIONS

    pubs = {p["id"]: p for p in INITIAL_PUBLICATIONS}
    pub_catalog = {p.id: p.to_dict() for p in PUBLICATIONS_CATALOG}
    hacienda = [f"pr-cc-ri-26-{n:02d}" for n in range(1, 14)]
    hacienda += [f"pr-bi-26-{n:02d}" for n in range(1, 6)] + ["pr-da-26-01", "pr-da-26-02"]
    ddec = ["pr-ddec-cc-2026-001", "pr-ddec-ib-2026-004"]
    for pid in hacienda + ddec:
        assert pubs[pid] == pub_catalog[pid], pid
        assert pubs[pid]["doc_date"].startswith("2026-"), pid
        assert pubs[pid]["source_url"].endswith(".pdf"), pid
    for pid in hacienda:
        assert pubs[pid]["source_url"].startswith(
            "https://hacienda.pr.gov/sites/default/files/publicaciones/2026/"
        ), pid
    for pid in ddec:
        assert pubs[pid]["source_url"].startswith("https://docs.pr.gov/files/DDEC/"), pid
    assert "Act No. 38-2026" in pubs["pr-ddec-ib-2026-004"]["summary"]


UPSTREAM_HOSTS = [
    "uscode.house.gov",
    "www.ecfr.gov",
    "hacienda.pr.gov",
    "bvirtualogp.pr.gov",
    "dor.georgia.gov",
]
PUBLIC_IP = "93.184.215.14"


def test_upstream_endpoints_and_fixed_sources_avoid_dead_and_redirecting_urls() -> None:
    """Pins that replaced a 404, a deprecated API or a redirect on main stay replaced."""
    from taxcode_mcp.db import INITIAL_STATUTES

    assert UPSTREAM_ENDPOINTS["OGP_PR"] == (
        "https://bvirtualogp.pr.gov/ogp/Bvirtual/Pages/default.aspx"
    )
    assert UPSTREAM_ENDPOINTS["eCFR_API"] == "https://www.ecfr.gov/api/versioner/v1/titles.json"
    dead = {
        "https://bvirtualogp.pr.gov/",
        "https://dor.georgia.gov/taxes/individual-income-tax",
    }
    for row in INITIAL_STATUTES:
        assert row["source_url"] not in dead, row["id"]
        assert "www.ecfr.gov/on/" not in row["source_url"], row["id"]
    seeds = {s["id"]: s for s in INITIAL_STATUTES}
    assert seeds["pr-act22-2012"]["source_url"] == (
        "https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF/"
        "Desarrollo%20Econ%C3%B3mico/22-2012/22-2012.pdf"
    )
    for sid in (
        "ga-ocga-48-7-20-2024",
        "ga-ocga-48-7-20-2025",
        "ga-ocga-48-7-20-2026",
        "ga-ocga-48-7-27",
    ):
        assert seeds[sid]["source_url"] == "https://dor.georgia.gov/taxes/taxes-individuals", sid


def test_check_upstream_endpoints_connects_only_to_checked_addresses() -> None:
    """Each host is looked up once and dialled at the checked IP; SNI and Host keep the name."""
    net = FakeNetwork()
    with net.patches(), patch("socket.getaddrinfo", return_value=answer(PUBLIC_IP)) as dns:
        res = check_upstream_endpoints()
    assert set(res) == set(UPSTREAM_ENDPOINTS)
    assert all(r["status"] == "reachable" and r["code"] == 200 for r in res.values())
    assert [c.args[0] for c in dns.call_args_list] == UPSTREAM_HOSTS
    # One connection per hostname, even though every host shares the address.
    assert net.connects == [(PUBLIC_IP, 443)] * len(UPSTREAM_HOSTS)
    assert net.tls_server_names == UPSTREAM_HOSTS
    for host in UPSTREAM_HOSTS:
        assert f"\r\nHost: {host}\r\n".encode() in net.sent
    assert b"\r\nUser-Agent: TaxCode-MCP-Sync/1.0 " in net.sent


def test_check_upstream_endpoints_ignores_environment_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.vendor.test:3128")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.vendor.test:3128")
    net = FakeNetwork()
    with net.patches(), patch("socket.getaddrinfo", return_value=answer(PUBLIC_IP)):
        res = check_upstream_endpoints()
    assert all(r["status"] == "reachable" for r in res.values())
    assert net.connects == [(PUBLIC_IP, 443)] * len(UPSTREAM_HOSTS)
    assert b"CONNECT " not in net.sent


@pytest.mark.parametrize("reserved", ["10.0.0.5", "198.18.0.1", "169.254.169.254"])
def test_check_upstream_endpoints_refuses_reserved_addresses(reserved: str) -> None:
    """A host resolving to a non-global address is reported blocked and never dialled."""
    net = FakeNetwork()
    with net.patches(), patch("socket.getaddrinfo", return_value=answer(PUBLIC_IP, reserved)):
        res = check_upstream_endpoints()
    assert net.attempts == []
    for r in res.values():
        assert r["status"] == "unreachable_or_blocked"
        assert "SSRF validation blocked request" in r["error"] and reserved in r["error"]


def test_check_upstream_endpoints_refuses_a_literal_reserved_url() -> None:
    """The URL check runs before the transport: a literal metadata address is never fetched."""
    net = FakeNetwork()
    endpoints = {"meta": "http://169.254.169.254/latest/meta-data"}
    with net.patches(), patch("socket.getaddrinfo") as dns:
        res = asyncio.run(_probe_upstream_endpoints(endpoints))
    dns.assert_not_called()
    assert net.attempts == []
    assert res["meta"]["status"] == "unreachable_or_blocked"


def test_check_upstream_endpoints_does_not_follow_redirects() -> None:
    """A 3xx is reported, not followed to a target the SSRF check never saw."""
    net = FakeNetwork(status=302)
    with net.patches(), patch("socket.getaddrinfo", return_value=answer(PUBLIC_IP)) as dns:
        res = check_upstream_endpoints()
    assert dns.call_count == len(UPSTREAM_HOSTS)
    assert len(net.connects) == len(UPSTREAM_HOSTS)
    assert len(net.requests_by_connection()[0]) == 1
    for r in res.values():
        assert r["status"] == "redirect_not_followed" and r["code"] == 302


def test_check_upstream_endpoints_reports_http_errors() -> None:
    net = FakeNetwork(status=503)
    with net.patches(), patch("socket.getaddrinfo", return_value=answer(PUBLIC_IP)):
        res = check_upstream_endpoints()
    for r in res.values():
        assert r["status"] == "unreachable_or_blocked"
        assert "503" in r["error"]


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


def test_pr_act60_sources_use_pdf_urls() -> None:
    """Act 60 records cite the OGP PDFs, not the library homepage."""
    for rec in STATUTES_CATALOG:
        if rec.citation.startswith("Ley 60-2019"):
            assert rec.source_url.endswith(".pdf"), rec.id


def test_formerly_catalog_only_entries_are_seeded_from_pinned_sources() -> None:
    """Entries seeded or corrected from pinned sources match the catalog and cite those sources."""
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
        "us-cfr-1-937-3": (
            "https://www.ecfr.gov/api/versioner/v1/full/"
            f"{ECFR_TITLE26_DATE}/title-26.xml?section=1.937-3"
        ),
        "pr-cri-1031-02": cri_pdf,
        "pr-cri-1062-01": cri_pdf,
        "pr-cri-1062-03": cri_pdf,
        "pr-cri-1010-01": cri_pdf,
        "pr-cri-1031-01": cri_pdf,
        "pr-ley60-6070-55": "https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF/60-2019.pdf",
        "pr-ley60-6020-10": "https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF/60-2019.pdf",
    }
    seeds = {s["id"]: {"superseded_year": None, **s} for s in INITIAL_STATUTES}
    catalog = {r.id: r.to_dict() for r in STATUTES_CATALOG}
    for sid, url in statute_sources.items():
        assert seeds[sid] == catalog[sid], sid
        assert seeds[sid]["source_url"] == url, sid
    for sid, seed in seeds.items():
        if seed["corpus"] == "CRI":
            assert seed["source_url"] == cri_pdf, sid
    # T.D. 9391, 73 FR 19350 (Apr. 9, 2008), adopted § 1.937-3; (f) keys on the same date.
    assert seeds["us-cfr-1-937-3"]["effective_year"] == 2008
    assert "ending after April 9, 2008" in seeds["us-cfr-1-937-3"]["text_en"]
    assert seeds["us-cfr-1-937-3"]["text_en"].endswith(
        "\nAmendment history (eCFR source note): adopted by T.D. 9391, 73 FR 19374, Apr. 9, 2008; "
        "amended by T.D. 9391, 73 FR 27728, May 14, 2008 (correction), T.D. 9921, 85 FR 79853, "
        "Dec. 11, 2020, and T.D. 10022, 90 FR 3003, Jan. 14, 2025."
    )
    assert seeds["us-cfr-1-937-3"]["text_es"].endswith(
        "Historial de enmiendas (nota de fuente del eCFR): adoptada por T.D. 9391, 73 FR 19374, "
        "9 de abril de 2008; enmendada por T.D. 9391, 73 FR 27728, 14 de mayo de 2008 "
        "(corrección), T.D. 9921, 85 FR 79853, 11 de diciembre de 2020, y T.D. 10022, 90 FR 3003, "
        "14 de enero de 2025."
    )

    # English summaries state the conditions their sources set.
    summaries = {sid: seeds[sid]["text_en"] for sid in seeds}
    assert "by a United States citizen who establishes" in summaries["pr-cri-1031-02"]
    assert "nothing prevents the entire (100%) annual contribution" in summaries["pr-ley60-6020-10"]
    assert "may postpone this requirement by one additional year" in summaries["pr-ley60-6020-10"]
    assert (
        "Núm. 26-01 / Orden Administrativa Núm. DDEC 2026-01 of February 5, 2026"
        in (summaries["pr-ley60-6020-10"])
    )
    assert "beginning after December 31, 2025" in summaries["pr-ley60-6020-10"]
    assert (
        "payments made after December 31, 2021 for services performed by a remote worker"
        in (summaries["pr-cri-1062-01"])
    )
    assert "2003.." not in seeds["pr-cri-1031-02"]["text_es"]

    pubs = {p["id"]: p for p in INITIAL_PUBLICATIONS}
    pub_catalog = {p.id: p.to_dict() for p in PUBLICATIONS_CATALOG}
    for pid in (
        "pr-bi-26-02",
        "pr-cc-ri-25-12",
        "pr-cc-ri-21-10",
        "pr-cc-ri-26-13",
        "pr-da-21-04",
        "pr-da-23-04",
    ):
        assert pubs[pid] == pub_catalog[pid], pid
        assert pubs[pid]["source_url"].startswith(
            "https://hacienda.pr.gov/sites/default/files/publicaciones/"
        )
        assert pubs[pid]["source_url"].endswith(".pdf")
