"""Federal phase: USLM Title 26 adapter, IRC rows, eCFR rows and IRS publications."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

import pytest

from scripts.ingest_statutes import (
    PUBLICATIONS_CATALOG,
    STATUTES_CATALOG,
    USLM_TITLE26_ALLOWLIST,
    apply_uslm_title26,
    main,
    parse_uslm_title26,
    run_ingest,
)
from taxcode_mcp.db import (
    ECFR_TITLE26_DATE,
    INITIAL_PUBLICATIONS,
    INITIAL_STATUTES,
    USLM_TITLE26_RELEASE_POINT,
    USLM_TITLE26_SOURCE_URL,
    TaxDatabase,
)

FIXTURE = Path(__file__).parent / "fixtures" / "usc26_sample.xml"
IRS_PUBS = {"17", "501", "505", "570", "1321", "54"}


def test_parse_uslm_extracts_allowlisted_sections_only() -> None:
    """The adapter keeps allowlisted sections and drops notes and source credits."""
    sections = parse_uslm_title26(FIXTURE, allowlist=("933", "9001"))
    assert set(sections) == {"933", "9001"}

    s933 = sections["933"]
    assert s933.heading == "Income from sources within Puerto Rico"
    assert s933.enacted_year == 1954
    assert s933.release_point == "119-121"
    assert s933.text.startswith("The following items shall not be included in gross income")
    assert "(1) Resident of Puerto Rico for entire taxable year\n" in s933.text
    assert "Stat." not in s933.text

    s9001 = sections["9001"]
    assert s9001.enacted_year == 2030
    assert "Income bracket | Rate\nNot over $10 | 1%" in s9001.text
    assert "(1)(A) First nested item." in s9001.text
    assert "Editorial note" not in s9001.text


def test_parse_uslm_rejects_missing_sections_and_years() -> None:
    """Missing allowlisted sections and source credits without a year fail loudly."""
    with pytest.raises(ValueError, match="sections missing from USLM file: 1, 61"):
        parse_uslm_title26(FIXTURE, allowlist=("933", "1", "61"))
    with pytest.raises(ValueError, match="no enactment year"):
        parse_uslm_title26(FIXTURE, allowlist=("9002",))


def test_parse_uslm_section_without_heading(tmp_path: Path) -> None:
    """A section with no heading parses to an empty heading."""
    xml = tmp_path / "t26.xml"
    xml.write_text(
        '<uscDoc xmlns="http://xml.house.gov/schemas/uslm/1.0"><meta>'
        "<docPublicationName>Online@119-121</docPublicationName></meta>"
        '<section identifier="/us/usc/t26/s5"><content>Body.</content>'
        "<sourceCredit>(Aug. 16, 1954.)</sourceCredit></section></uscDoc>",
        encoding="utf-8",
    )
    sec = parse_uslm_title26(xml, allowlist=("5",))["5"]
    assert sec.heading == ""
    assert sec.text == "Body."


def test_apply_uslm_matches_catalog_and_refreshes_copies() -> None:
    """Catalog §933 text equals the release point text; drifted rows are refreshed on a copy."""
    sections = parse_uslm_title26(FIXTURE, allowlist=("933",))
    refreshed, changed = apply_uslm_title26(sections, STATUTES_CATALOG)
    assert changed == 0
    assert refreshed == STATUTES_CATALOG

    stale = [replace(r, text_en="old") if r.id == "us-irc-933" else r for r in STATUTES_CATALOG]
    refreshed, changed = apply_uslm_title26(sections, stale)
    assert changed == 1
    row = next(r for r in refreshed if r.id == "us-irc-933")
    assert row.text_en == sections["933"].text
    assert next(r for r in stale if r.id == "us-irc-933").text_en == "old"

    sections["933"].release_point = "119-1"
    with pytest.raises(ValueError, match="does not match pinned"):
        apply_uslm_title26(sections, STATUTES_CATALOG)


def test_run_ingest_and_cli_accept_uslm_xml(tmp_path: Path) -> None:
    """run_ingest and the CLI refresh from a USLM file without network access."""
    stats = run_ingest(db_path=":memory:", sources="us", dry_run=True, uslm_xml=None)
    assert stats["statutes_considered"] > 0
    with pytest.raises(ValueError, match="sections missing"):
        run_ingest(db_path=":memory:", dry_run=True, uslm_xml=FIXTURE)
    with pytest.raises(ValueError, match="sections missing"):
        main(["--dry-run", "--uslm-xml", str(FIXTURE)])


def test_run_ingest_with_full_allowlist_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With every allowlisted section present, matching rows are not counted as refreshed."""
    import scripts.ingest_statutes as ingest

    sections = parse_uslm_title26(FIXTURE, allowlist=("933",))
    full = {
        num: replace(
            sections["933"],
            section=num,
            text=next(r.text_en for r in STATUTES_CATALOG if r.id == f"us-irc-{num}"),
            enacted_year=next(
                r.effective_year for r in STATUTES_CATALOG if r.id == f"us-irc-{num}"
            ),
        )
        for num in USLM_TITLE26_ALLOWLIST
    }
    monkeypatch.setattr(ingest, "parse_uslm_title26", lambda _path: full)
    db_file = str(tmp_path / "fed.duckdb")
    stats = run_ingest(db_path=db_file, sources="us", uslm_xml=FIXTURE)
    assert stats["statutes_upserted"] == stats["statutes_considered"]


def test_federal_irc_rows_in_seeds_and_ingest() -> None:
    """Every allowlisted IRC row is in both seeds and ingest, pinned to the release point."""
    seeds = {s["id"]: {"superseded_year": None, **s} for s in INITIAL_STATUTES}
    catalog = {r.id: r for r in STATUTES_CATALOG}
    for num in USLM_TITLE26_ALLOWLIST:
        sid = f"us-irc-{num}"
        assert sid in seeds and sid in catalog, sid
        assert seeds[sid] == catalog[sid].to_dict(), sid
        assert seeds[sid]["source_url"] == USLM_TITLE26_SOURCE_URL
        assert " / " in seeds[sid]["title"], sid
        assert f"release point {USLM_TITLE26_RELEASE_POINT}" in catalog[sid].breadcrumbs
    assert USLM_TITLE26_RELEASE_POINT in USLM_TITLE26_SOURCE_URL


def test_treasury_regulation_rows_cite_ecfr_date() -> None:
    """The three §1.937 regulations cite the dated eCFR versioner XML for their section.

    The eCFR HTML pages (``/on/<date>/...``) redirect automated clients to a bot check,
    so the rows cite the official point-in-time API, which answers 200 without a redirect.
    """
    seeds = {s["id"]: s for s in INITIAL_STATUTES}
    for sid, section in (
        ("us-cfr-1-937-1", "1.937-1"),
        ("us-cfr-1-937-2", "1.937-2"),
        ("us-cfr-1-937-3", "1.937-3"),
    ):
        url = urlparse(seeds[sid]["source_url"])
        assert url.scheme == "https" and url.netloc == "www.ecfr.gov"
        assert url.path == f"/api/versioner/v1/full/{ECFR_TITLE26_DATE}/title-26.xml"
        assert url.query == f"section={section}"


def test_irs_publications_in_seeds_and_ingest() -> None:
    """The six IRS publications match in seeds and ingest and point at irs.gov."""
    seeds = {p["id"]: p for p in INITIAL_PUBLICATIONS if p["id"].startswith("us-irs-pub-")}
    catalog = {p.id: p.to_dict() for p in PUBLICATIONS_CATALOG if p.id.startswith("us-irs-pub-")}
    assert set(seeds) == {f"us-irs-pub-{n}" for n in IRS_PUBS}
    assert seeds == catalog
    for pub in seeds.values():
        assert pub["category"] == "irs-publication"
        assert urlparse(pub["source_url"]).netloc == "www.irs.gov"
        assert " / " in pub["title"] and " / " in pub["summary"]


def test_federal_rows_served_by_existing_tools() -> None:
    """lookup, search and list_publications serve the federal rows with US jurisdiction."""
    db = TaxDatabase(":memory:")
    row = db.lookup_statute("61", jurisdiction="US", tax_year=2026)
    assert row is not None
    assert row["text_en"].startswith("(a) General definition")
    assert row["validity"] == {
        "en": "Effective 1954; current",
        "es": "Vigente desde 1954; en vigor",
    }
    hits = db.search_statutes(query="net investment income", jurisdiction="US", limit=20)
    assert any(h["id"] == "us-irc-1411" for h in hits)
    pubs = db.list_publications(category="irs-publication", limit=20)
    assert {p["id"] for p in pubs} == {f"us-irs-pub-{n}" for n in IRS_PUBS}
