#!/usr/bin/env python3
"""Statutory Ingestion and Update Pipeline for US and Puerto Rico Tax Codes.

Ingests, updates, parses, and synchronizes statutory provisions (Title 26 U.S.C.,
26 C.F.R., Ley 1-2011, Ley 60-2019) and administrative publications into DuckDB,
and generates standardized llms.txt documentation files.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx

from taxcode_mcp.catalog import load_catalog, render_docs
from taxcode_mcp.client import SSRFSafeAsyncTransport
from taxcode_mcp.db import (
    USLM_TITLE26_RELEASE_POINT,
    TaxDatabase,
)
from taxcode_mcp.ssrf import validate_outbound_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ingest_statutes")


@dataclass
class StatuteRecord:
    """Represents an authoritative statutory section."""

    id: str
    jurisdiction: str
    corpus: str
    citation: str
    section: str
    title: str
    text_en: str
    text_es: str
    effective_year: int
    source_url: str
    superseded_year: int | None = None
    cross_references: list[str] = field(default_factory=list)
    breadcrumbs: str = ""
    # Export-only provenance note appended to the docs Source line; not stored in the database.
    source_note: str = ""

    def content_hash(self) -> str:
        """Compute a SHA-256 checksum of the bilingual text payload."""
        payload = (
            f"{self.citation}|{self.title}|{self.text_en}|{self.text_es}|"
            f"{self.effective_year}|{self.superseded_year}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """Convert record to database dictionary format."""
        return {
            "id": self.id,
            "jurisdiction": self.jurisdiction,
            "corpus": self.corpus,
            "citation": self.citation,
            "section": self.section,
            "title": self.title,
            "text_en": self.text_en,
            "text_es": self.text_es,
            "effective_year": self.effective_year,
            "superseded_year": self.superseded_year,
            "source_url": self.source_url,
            "cross_references": self.cross_references,
        }


@dataclass
class PublicationRecord:
    """Represents an administrative guidance publication."""

    id: str
    category: str
    doc_number: str
    doc_date: str
    title: str
    summary: str
    source_url: str

    def content_hash(self) -> str:
        """Compute a SHA-256 checksum of the publication payload."""
        payload = f"{self.doc_number}|{self.doc_date}|{self.title}|{self.summary}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """Convert publication to database dictionary format."""
        return {
            "id": self.id,
            "category": self.category,
            "doc_number": self.doc_number,
            "doc_date": self.doc_date,
            "title": self.title,
            "summary": self.summary,
            "source_url": self.source_url,
        }


# Authoritative statutory corpus definitions, loaded from the versioned data files
# (``src/taxcode_mcp/data``); ``taxcode_mcp.db`` seeds the database from the same files.
_CATALOG = load_catalog()
STATUTES_CATALOG: list[StatuteRecord] = [StatuteRecord(**s) for s in _CATALOG.statutes]
PUBLICATIONS_CATALOG: list[PublicationRecord] = [
    PublicationRecord(**p) for p in _CATALOG.publications
]


# ---------------------------------------------------------------------------
# USLM adapter for Title 26 (Office of the Law Revision Counsel, uscode.house.gov)
# ---------------------------------------------------------------------------

USLM_NS = "{http://xml.house.gov/schemas/uslm/1.0}"
XHTML_NS = "{http://www.w3.org/1999/xhtml}"
# Only these Title 26 sections are ingested from the USLM release point.
USLM_TITLE26_ALLOWLIST: tuple[str, ...] = (
    "1",
    "24",
    "32",
    "61",
    "62",
    "63",
    "865",
    "911",
    "933",
    "937",
    "957",
    "1411",
    "6012",
    "6688",
)
_USLM_SKIP = {f"{USLM_NS}notes", f"{USLM_NS}note", f"{USLM_NS}sourceCredit"}
_USLM_BLOCKS = {
    f"{USLM_NS}{tag}"
    for tag in (
        "subsection",
        "paragraph",
        "subparagraph",
        "clause",
        "subclause",
        "item",
        "subitem",
        "chapeau",
        "content",
        "continuation",
        "p",
    )
} | {f"{XHTML_NS}tr"}
_USLM_LEVEL_TAGS = {
    f"{USLM_NS}{tag}"
    for tag in ("subsection", "paragraph", "subparagraph", "clause", "subclause", "item", "subitem")
}
_NUM_ONLY_RE = re.compile(r"^\s*(\([0-9A-Za-z]+\)\s*)+$")
_YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")


@dataclass
class UslmSection:
    """One Title 26 section parsed from a USLM release point."""

    section: str
    heading: str
    text: str
    enacted_year: int
    release_point: str


def _uslm_render(el: ET.Element, lines: list[str], buf: list[str]) -> None:
    """Append the operative text of ``el`` to ``buf``, breaking lines at block elements."""
    if el.tag in _USLM_SKIP:
        return
    block = el.tag in _USLM_BLOCKS
    if block:
        if _NUM_ONLY_RE.match("".join(buf)):
            if el.tag not in _USLM_LEVEL_TAGS:
                buf.append(" ")
        else:
            _uslm_flush(lines, buf)
    if el.tag in (f"{XHTML_NS}td", f"{XHTML_NS}th") and buf and buf[-1].strip():
        buf.append(" | ")
    if el.tag == f"{XHTML_NS}p":
        buf.append(" ")
    if el.text:
        buf.append(el.text)
    for child in el:
        _uslm_render(child, lines, buf)
        if child.tail:
            buf.append(child.tail)
    if block:
        _uslm_flush(lines, buf)


def _uslm_flush(lines: list[str], buf: list[str]) -> None:
    line = " ".join("".join(buf).split())
    if line:
        lines.append(line)
    buf.clear()


def section_text(el: ET.Element) -> str:
    """Operative statute text of a USLM section, without heading, notes or source credit."""
    lines: list[str] = []
    buf: list[str] = []
    for child in el:
        if child.tag not in (f"{USLM_NS}num", f"{USLM_NS}heading"):
            _uslm_render(child, lines, buf)
        if child.tail:
            buf.append(child.tail)
    _uslm_flush(lines, buf)
    return "\n".join(lines)


def parse_uslm_title26(
    xml_path: Path, allowlist: tuple[str, ...] = USLM_TITLE26_ALLOWLIST
) -> dict[str, UslmSection]:
    """Stream a USLM Title 26 XML file and return the allowlisted sections."""
    wanted = {f"/us/usc/t26/s{num}": num for num in allowlist}
    found: dict[str, UslmSection] = {}
    release_point = ""
    for _event, el in ET.iterparse(xml_path):
        local = el.tag.removeprefix(USLM_NS)
        if local == "docPublicationName" and el.text:
            release_point = el.text.split("@")[-1].strip()
        elif local == "section" and el.get("identifier") in wanted:
            num = wanted[el.get("identifier", "")]
            heading_el = el.find(f"{USLM_NS}heading")
            credit_el = el.find(f"{USLM_NS}sourceCredit")
            credit = "".join(credit_el.itertext()) if credit_el is not None else ""
            year = _YEAR_RE.search(credit)
            if year is None:
                raise ValueError(f"no enactment year in source credit for section {num}")
            found[num] = UslmSection(
                section=num,
                heading=" ".join("".join(heading_el.itertext()).split())
                if heading_el is not None
                else "",
                text=section_text(el),
                enacted_year=int(year.group(1)),
                release_point=release_point,
            )
            el.clear()
    missing = [num for num in allowlist if num not in found]
    if missing:
        raise ValueError(f"sections missing from USLM file: {', '.join(missing)}")
    return found


def apply_uslm_title26(
    sections: dict[str, UslmSection], catalog: list[StatuteRecord]
) -> tuple[list[StatuteRecord], int]:
    """Return a copy of ``catalog`` with allowlisted IRC rows refreshed from USLM text.

    The second value counts rows whose text or enactment year changed.
    """
    refreshed: list[StatuteRecord] = []
    changed = 0
    for rec in catalog:
        sec = sections.get(rec.section)
        if rec.jurisdiction != "US" or rec.corpus != "IRC" or sec is None:
            refreshed.append(rec)
            continue
        if sec.release_point != USLM_TITLE26_RELEASE_POINT:
            raise ValueError(
                f"release point {sec.release_point} does not match pinned "
                f"{USLM_TITLE26_RELEASE_POINT}"
            )
        if rec.text_en != sec.text or rec.effective_year != sec.enacted_year:
            changed += 1
            rec = replace(rec, text_en=sec.text, effective_year=sec.enacted_year)
        refreshed.append(rec)
    return refreshed, changed


UPSTREAM_ENDPOINTS: dict[str, str] = {
    "OLRC_USLM": "https://uscode.house.gov/",
    "eCFR_API": "https://www.ecfr.gov/api/versioner/v1/titles.json",
    "Hacienda_PR": "https://hacienda.pr.gov/",
    "OGP_PR": "https://bvirtualogp.pr.gov/ogp/Bvirtual/Pages/default.aspx",
    "Georgia_DOR": "https://dor.georgia.gov/",
}
UPSTREAM_USER_AGENT = (
    "TaxCode-MCP-Sync/1.0 (+https://github.com/christianclaudio/mcp-server-taxcode)"
)
UPSTREAM_TIMEOUT_SECONDS = 5.0


async def _probe_upstream_endpoints(endpoints: dict[str, str]) -> dict[str, Any]:
    """Probe each endpoint once through the client's SSRF transport.

    Every URL passes ``validate_outbound_url`` first. The transport then checks every
    address the host resolves to and connects only to a checked one, keeping the
    hostname for the Host header, TLS SNI and certificate verification. Redirects are
    not followed: a 3xx target would skip the check, so it is reported, not fetched.
    httpx ignores env proxies whenever a transport is passed
    (``allow_env_proxies = trust_env and transport is None``), so the probe always
    connects direct through the pin; egress-proxy deployments need a proxy-aware
    transport, which isn't provided. ``trust_env=False`` is belt-and-braces.
    """
    results: dict[str, Any] = {}
    async with httpx.AsyncClient(
        transport=SSRFSafeAsyncTransport(verify=True),
        timeout=UPSTREAM_TIMEOUT_SECONDS,
        follow_redirects=False,
        trust_env=False,
        headers={"User-Agent": UPSTREAM_USER_AGENT},
    ) as client:
        for name, url in endpoints.items():
            try:
                safe_url = validate_outbound_url(url)
                resp = await client.get(safe_url)
                if resp.is_redirect:
                    results[name] = {
                        "status": "redirect_not_followed",
                        "code": resp.status_code,
                        "location": resp.headers.get("location", ""),
                        "url": url,
                    }
                    continue
                resp.raise_for_status()
                results[name] = {"status": "reachable", "code": resp.status_code, "url": url}
            except Exception as exc:
                results[name] = {"status": "unreachable_or_blocked", "error": str(exc), "url": url}
    return results


def check_upstream_endpoints() -> dict[str, Any]:
    """Verify live connectivity to official digital tax publishing authorities.

    Each probe goes through the pinned, redirect-free fetch in
    ``_probe_upstream_endpoints``; a refused host is reported as blocked.
    """
    return asyncio.run(_probe_upstream_endpoints(UPSTREAM_ENDPOINTS))


def _statute_doc_fields(s: StatuteRecord) -> dict[str, Any]:
    """A record's database fields plus the docs-only breadcrumbs and source note."""
    return {**s.to_dict(), "breadcrumbs": s.breadcrumbs, "source_note": s.source_note}


def export_documentation(output_dir: Path) -> tuple[int, int]:
    """Write the generated Markdown pages, llms.txt and llms-full.txt into ``output_dir``.

    The files are the ones ``scripts/build_catalog.py`` keeps in ``docs/``
    (``taxcode_mcp.catalog.render_docs``).
    """
    files = render_docs(
        [_statute_doc_fields(s) for s in STATUTES_CATALOG],
        [p.to_dict() for p in PUBLICATIONS_CATALOG],
    )
    for rel, content in files.items():
        target = output_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return len(STATUTES_CATALOG), len(PUBLICATIONS_CATALOG)


def run_ingest(
    db_path: str = ":memory:",
    sources: str = "all",
    dry_run: bool = False,
    export_docs: Path | None = None,
    uslm_xml: Path | None = None,
) -> dict[str, int]:
    """Ingest statutes and publications into DuckDB and optionally export docs."""
    logger.info(
        "Initializing ingestion pipeline (sources=%s, db_path=%s, dry_run=%s)",
        sources,
        db_path,
        dry_run,
    )

    catalog = STATUTES_CATALOG
    if uslm_xml is not None:
        catalog, refreshed = apply_uslm_title26(parse_uslm_title26(uslm_xml), catalog)
        logger.info(
            "USLM Title 26 release point %s: %d IRC rows refreshed.",
            USLM_TITLE26_RELEASE_POINT,
            refreshed,
        )

    selected_statutes: list[StatuteRecord] = []
    for s in catalog:
        if sources == "all":
            selected_statutes.append(s)
        elif sources == "cfr" and s.corpus == "CFR":
            selected_statutes.append(s)
        elif sources.lower() == s.jurisdiction.lower():
            selected_statutes.append(s)

    selected_publications: list[PublicationRecord] = []
    for pub in PUBLICATIONS_CATALOG:
        if sources in ("all", "publications"):
            selected_publications.append(pub)
        elif sources.lower() == pub.id.split("-")[0].lower():
            selected_publications.append(pub)

    stats = {
        "statutes_considered": len(selected_statutes),
        "statutes_upserted": 0,
        "publications_considered": len(selected_publications),
        "publications_upserted": 0,
    }

    if not dry_run:
        target_db = TaxDatabase(db_path)
        for st in selected_statutes:
            target_db.upsert_statute(st.to_dict())
            stats["statutes_upserted"] += 1
        for pub in selected_publications:
            target_db.upsert_publication(pub.to_dict())
            stats["publications_upserted"] += 1

        total_statutes, total_pubs = target_db.get_stats()
        logger.info(
            "Database upsert complete: %d statutes, %d publications in index.",
            total_statutes,
            total_pubs,
        )
    else:
        logger.info(
            "[DRY RUN] Validated %d statutes and %d publications.",
            len(selected_statutes),
            len(selected_publications),
        )

    if export_docs:
        exported_statutes, exported_pubs = export_documentation(export_docs)
        logger.info(
            "Documentation exported to %s: %d statutes, %d publications.",
            export_docs,
            exported_statutes,
            exported_pubs,
        )

    return stats


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for statutory ingestion pipeline."""
    parser = argparse.ArgumentParser(
        description="Ingest, parse, and synchronize US, Puerto Rico & State Tax Codes into DuckDB.",
    )
    parser.add_argument(
        "--db-path",
        default=os.getenv("TAXCODE_DB_PATH", ":memory:"),
        help="Target DuckDB database path (defaults to TAXCODE_DB_PATH or :memory:).",
    )
    parser.add_argument(
        "--sources",
        default="all",
        help="Subsets of statutory sources to process (default: all).",
    )
    parser.add_argument(
        "--export-docs",
        type=Path,
        default=None,
        help="Optional directory to export llms.txt and markdown documentation.",
    )
    parser.add_argument(
        "--uslm-xml",
        type=Path,
        default=None,
        help="Optional USLM Title 26 XML (usc26.xml) to refresh allowlisted IRC sections from.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate ingestion without writing to DuckDB or disk.",
    )
    parser.add_argument(
        "--check-upstream",
        action="store_true",
        help="Check connectivity to official government publishing endpoints.",
    )

    args = parser.parse_args(argv)

    if args.check_upstream:
        logger.info("Checking upstream publishing authority endpoints...")
        results = check_upstream_endpoints()
        print(json.dumps(results, indent=2))
        return 0

    stats = run_ingest(
        db_path=args.db_path,
        sources=args.sources,
        dry_run=args.dry_run,
        export_docs=args.export_docs,
        uslm_xml=args.uslm_xml,
    )
    logger.info("Pipeline executed successfully: %s", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
