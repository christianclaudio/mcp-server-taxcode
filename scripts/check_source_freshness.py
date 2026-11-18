#!/usr/bin/env python3
"""Check that pinned official sources still match the manifest, and that no newer
IRS inflation revenue procedure or Hacienda publication is missing from the catalog.

The OpenAPI drift script does not watch tax sources. This one does. It runs on the
weekly ``drift-check`` job, not on pull requests.

Exit 0 means fresh. Exit 1 means at least one ``❌`` line (a pinned file changed or
disappeared, or a newer publication is missing from the catalog). Exit 2 means the
monitor itself failed; that is printed as ``::error::`` and is not reported as a
changed source. The last line is ``checked N, changed N, gone N, unpinned N, newer N``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from taxcode_mcp.client import SSRFSafeAsyncTransport
from taxcode_mcp.errors import TemplateError
from taxcode_mcp.ssrf import SSRFValidationError, validate_outbound_url

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DATA = REPO / "src" / "taxcode_mcp" / "data"
DEFAULT_WATCH = Path(__file__).resolve().parent / "source_watch.json"

_WATCH_FIELDS = (
    "irs_newsroom_url",
    "hacienda_listing_url",
    "hacienda_since",
    "hacienda_seen",
)
_SINCE_RE = re.compile(r"^\d{2}-\d{2}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_NOT_RECORDED = "not_recorded"
_NOT_FOUND_PATH = "/pagina-no-encontrada"
_CANONICAL_RE = re.compile(
    r"""<link[^>]*rel=["']canonical["'][^>]*href=["']([^"']+)["']"""
    r"""|<link[^>]*href=["']([^"']+)["'][^>]*rel=["']canonical["']""",
    re.IGNORECASE,
)
_IRS_LINK_RE = re.compile(
    r"""href=["']([^"']*?/newsroom/irs-releases-tax-inflation-adjustments-for-tax-year-(\d{4})[^"']*)["']""",
    re.IGNORECASE,
)
_ANY_NEWSROOM_RE = re.compile(r"""href=["']([^"']*?/newsroom/[^"']*)["']""", re.IGNORECASE)
_HACIENDA_SLUG_RE = re.compile(
    r"""href=["']([^"']*?/publicaciones/"""
    r"""(carta-circular-de-rentas-internas|boletin-informativo-de-rentas-internas|"""
    r"""determinacion-administrativa)-num-(\d{2})-(\d{2})[^"']*)["']""",
    re.IGNORECASE,
)
_HACIENDA_KIND = {
    "carta-circular-de-rentas-internas": "CC RI",
    "boletin-informativo-de-rentas-internas": "BI RI",
    "determinacion-administrativa": "DA",
}
_FETCH_TIMEOUT = 30.0
_GLOBAL_CONCURRENCY = 4


def _today() -> date:
    """The calendar day the backstop uses. Tests replace this."""
    return date.today()


@dataclass(frozen=True)
class Pinned:
    """One manifest record whose bytes are hashed."""

    manifest_id: str
    url: str
    sha256: str
    retrieved: str
    body_class: str


@dataclass(frozen=True)
class Unpinned:
    manifest_id: str
    url: str


@dataclass
class FetchResult:
    status: int | None = None
    body: bytes = b""
    error: str | None = None


@dataclass
class Report:
    lines: list[str]
    checked: int = 0
    changed: int = 0
    gone: int = 0
    unpinned: int = 0
    newer: int = 0
    failed: str | None = None

    def summary(self) -> str:
        return (
            f"checked {self.checked}, changed {self.changed}, gone {self.gone}, "
            f"unpinned {self.unpinned}, newer {self.newer}"
        )


def _fail(report: Report, reason: str) -> int:
    """Exit 2. ``❌`` lines are dropped so a monitor failure is not a changed source."""
    print(f"::error::freshness monitor failed: {reason}")
    kept = [line for line in report.lines if not line.startswith("❌")]
    for line in kept:
        print(line)
    print(report.summary())
    return 2


def _body_class(url: str) -> str:
    """``pdf`` and ``bytes`` fail on any byte change. ``html`` only warns."""
    parsed = urlparse(url)
    path = parsed.path.lower()
    host = parsed.netloc.lower()
    if path.endswith(".pdf"):
        return "pdf"
    if path.endswith((".zip", ".xml")) or host == "raw.githubusercontent.com":
        return "bytes"
    return "html"


def _load_watch(path: Path) -> tuple[Mapping[str, Any] | None, str | None]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"malformed source_watch.json: {exc}"
    if not isinstance(raw, dict) or set(raw) != set(_WATCH_FIELDS):
        return None, f"malformed source_watch.json: fields must be exactly {list(_WATCH_FIELDS)}"
    for key in ("irs_newsroom_url", "hacienda_listing_url"):
        if not (isinstance(raw[key], str) and raw[key].startswith("https://")):
            return None, f"malformed source_watch.json: {key} must be an https URL"
    since = raw["hacienda_since"]
    if not (isinstance(since, str) and _SINCE_RE.fullmatch(since)):
        return None, "malformed source_watch.json: hacienda_since must be NN-NN"
    seen = raw["hacienda_seen"]
    if not isinstance(seen, list) or not all(isinstance(item, str) for item in seen):
        return None, "malformed source_watch.json: hacienda_seen must be a list of strings"
    return raw, None


def _iter_manifest(data_dir: Path) -> tuple[list[Pinned], list[Unpinned]]:
    pinned: list[Pinned] = []
    unpinned: list[Unpinned] = []
    folder = data_dir / "manifest"
    if not folder.is_dir():
        return pinned, unpinned
    for path in sorted(folder.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        for entry in document.get("entries", []):
            if not isinstance(entry, dict):
                continue
            entry_id = str(entry.get("id", path.stem))
            retrieved = str(entry.get("retrieved", _NOT_RECORDED))
            _take(
                entry_id,
                entry.get("url", ""),
                entry.get("content_sha256"),
                pinned,
                unpinned,
                retrieved=retrieved,
            )
            for source in entry.get("year_sources") or []:
                if isinstance(source, dict):
                    _take(
                        entry_id,
                        source.get("url", ""),
                        source.get("content_sha256"),
                        pinned,
                        unpinned,
                        retrieved=str(source.get("retrieved", _NOT_RECORDED)),
                    )
            french = entry.get("french_source")
            if isinstance(french, dict):
                _take(
                    entry_id,
                    french.get("url", ""),
                    french.get("content_sha256"),
                    pinned,
                    unpinned,
                    retrieved=retrieved,
                )
    return pinned, unpinned


def _take(
    manifest_id: str,
    url: Any,
    sha: Any,
    pinned: list[Pinned],
    unpinned: list[Unpinned],
    retrieved: str | None = None,
) -> None:
    if not isinstance(url, str) or not url:
        return
    if sha == _NOT_RECORDED or not isinstance(sha, str) or not _SHA_RE.fullmatch(sha):
        unpinned.append(Unpinned(manifest_id, url))
        return
    pinned.append(
        Pinned(
            manifest_id=manifest_id,
            url=url,
            sha256=sha,
            retrieved=retrieved if retrieved is not None else _NOT_RECORDED,
            body_class=_body_class(url),
        )
    )


def _newest_federal_year(data_dir: Path) -> int | None:
    path = data_dir / "federal" / "standard_deduction.json"
    if not path.is_file():
        return None
    document = json.loads(path.read_text(encoding="utf-8"))
    years = [
        row["tax_year"]
        for row in document.get("years", [])
        if isinstance(row, dict) and isinstance(row.get("tax_year"), int)
    ]
    return max(years) if years else None


def _known_hacienda_numbers(data_dir: Path) -> set[str]:
    path = data_dir / "publications" / "pr.json"
    if not path.is_file():
        return set()
    document = json.loads(path.read_text(encoding="utf-8"))
    known: set[str] = set()
    for row in document.get("publications", []):
        if not isinstance(row, dict):
            continue
        number = row.get("doc_number")
        if not isinstance(number, str):
            continue
        known.add(number.strip())
        known.add(number.split(" / ", 1)[0].strip())
    return known


def _seen_numbers(lines: list[str]) -> set[str]:
    seen: set[str] = set()
    for line in lines:
        head = line.split("#", 1)[0].strip()
        if head:
            seen.add(head)
    return seen


def _canonical_path(body: bytes) -> str | None:
    match = _CANONICAL_RE.search(body.decode("utf-8", "replace"))
    if match is None:
        return None
    href = match.group(1) or match.group(2)
    if href.startswith("http://") or href.startswith("https://"):
        return urlparse(href).path
    if href.startswith("/"):
        return href.split("?", 1)[0]
    return "/" + href.split("?", 1)[0]


def _is_not_found_page(body: bytes) -> bool:
    path = _canonical_path(body)
    return path is not None and path.rstrip("/") == _NOT_FOUND_PATH


def _abs_irs(href: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return "https://www.irs.gov" + href
    return "https://www.irs.gov/" + href


def _hacienda_link(href: str) -> tuple[str, str, tuple[int, int]] | None:
    """``(doc number, absolute publicaciones URL, (year, number))`` from one href."""
    absolute = href if href.startswith("http") else "https://hacienda.pr.gov" + href
    match = _HACIENDA_SLUG_RE.search(f"href='{absolute}'")
    if match is None:
        return None
    kind, year_s, number_s = match.group(2), match.group(3), match.group(4)
    slug = urlparse(absolute).path.split("/publicaciones/", 1)[1]
    doc_number = f"{_HACIENDA_KIND[kind]} {year_s}-{number_s}"
    return doc_number, f"https://hacienda.pr.gov/publicaciones/{slug}", (int(year_s), int(number_s))


async def _get_once(client: httpx.AsyncClient, url: str) -> FetchResult:
    try:
        safe = validate_outbound_url(url)
    except SSRFValidationError as exc:
        return FetchResult(error=f"refused: {exc}")
    last_error: str | None = None
    response: httpx.Response | None = None
    for attempt in range(2):
        try:
            response = await client.get(safe)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = f"connection failed: {exc}"
            if attempt == 0:
                continue
            return FetchResult(error=last_error)
        except TemplateError as exc:
            return FetchResult(error=f"refused: {exc}")
        if response.status_code >= 500 and attempt == 0:
            continue
        return FetchResult(status=response.status_code, body=response.content)
    if response is None:
        return FetchResult(error=last_error or "connection failed")
    return FetchResult(status=response.status_code, body=response.content)


async def _fetch_all(urls: list[str]) -> dict[str, FetchResult]:
    """GET each URL once. At most four at a time, and one at a time per host."""
    global_sem = asyncio.Semaphore(_GLOBAL_CONCURRENCY)
    host_sems: dict[str, asyncio.Semaphore] = {}

    async def one(client: httpx.AsyncClient, url: str) -> tuple[str, FetchResult]:
        host = urlparse(url).hostname or ""
        host_sem = host_sems.setdefault(host, asyncio.Semaphore(1))
        async with host_sem:
            async with global_sem:
                return url, await _get_once(client, url)

    async with httpx.AsyncClient(
        transport=SSRFSafeAsyncTransport(verify=True),
        timeout=_FETCH_TIMEOUT,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        pairs = await asyncio.gather(*(one(client, url) for url in urls))
    return dict(pairs)


def _classify_pinned(record: Pinned, fetched: FetchResult, report: Report) -> None:
    if fetched.error is not None or fetched.status is None:
        return
    report.checked += 1
    status = fetched.status
    if status in (404, 410):
        report.gone += 1
        report.lines.append(f"❌ Source gone: {record.manifest_id} {record.url} ({status})")
        return
    if status != 200:
        report.failed = f"unexpected HTTP {status} for {record.url}"
        return
    if record.body_class == "pdf" and not fetched.body.startswith(b"%PDF"):
        report.gone += 1
        report.lines.append(f"❌ Source gone: {record.manifest_id} {record.url} (not a pdf)")
        return
    if record.body_class == "html" and _is_not_found_page(fetched.body):
        report.gone += 1
        report.lines.append(
            f"❌ Source gone: {record.manifest_id} {record.url} (canonical {_NOT_FOUND_PATH})"
        )
        return
    digest = hashlib.sha256(fetched.body).hexdigest()
    if digest == record.sha256:
        return
    recorded, now = record.sha256[:16], digest[:16]
    if record.body_class == "html":
        report.lines.append(
            f"⚠️ HTML changed: {record.manifest_id} {record.url} (recorded {recorded}, now {now})"
        )
        return
    report.changed += 1
    report.lines.append(
        f"❌ Source changed: {record.manifest_id} {record.url} "
        f"(recorded {recorded}, now {now}, retrieved {record.retrieved})"
    )


def _check_irs(body: bytes, newest: int, report: Report) -> str | None:
    text = body.decode("utf-8", "replace")
    if not _ANY_NEWSROOM_RE.search(text):
        return "zero matching links parsed from the IRS newsroom"
    seen: set[int] = set()
    for href, year_s in _IRS_LINK_RE.findall(text):
        year = int(year_s)
        if year <= newest or year in seen:
            continue
        seen.add(year)
        report.newer += 1
        report.lines.append(
            "❌ Newer publication: IRS inflation adjustments for tax year "
            f"{year} ({_abs_irs(href)})"
        )
    return None


def _check_hacienda(
    pages: list[bytes],
    since: tuple[int, int],
    known: set[str],
    excluded: set[str],
    report: Report,
) -> str | None:
    found = False
    emitted: set[str] = set()
    for body in pages:
        if _is_not_found_page(body):
            return f"watcher page canonical is {_NOT_FOUND_PATH}"
        text = body.decode("utf-8", "replace")
        for href, *_rest in _HACIENDA_SLUG_RE.findall(text):
            parsed = _hacienda_link(href)
            if parsed is None:
                continue
            doc_number, link, key = parsed
            found = True
            if key < since or doc_number in known or doc_number in excluded:
                continue
            if doc_number in emitted:
                continue
            emitted.add(doc_number)
            report.newer += 1
            report.lines.append(f"❌ Newer publication: Hacienda {doc_number} ({link})")
    if not found:
        return "zero matching slugs parsed from the Hacienda listing"
    return None


def _calendar_backstop(newest: int, report: Report) -> None:
    """From 1 December of year Y, tax year Y+1 should be in the table; before that, tax year Y."""
    today = _today()
    expected = today.year + 1 if today.month == 12 else today.year
    if newest < expected:
        report.newer += 1
        report.lines.append(
            "❌ Expected publication missing: IRS inflation revenue procedure "
            f"for tax year {expected}"
        )


def _watcher_pages(watch: Mapping[str, Any]) -> list[str]:
    listing = str(watch["hacienda_listing_url"])
    joiner = "&" if "?" in listing else "?"
    return [
        str(watch["irs_newsroom_url"]),
        f"{listing}{joiner}page=0",
        f"{listing}{joiner}page=1",
    ]


async def run_check(data_dir: Path, watch_path: Path) -> int:
    watch, problem = _load_watch(watch_path)
    report = Report(lines=[])
    if watch is None:
        return _fail(report, problem or "malformed source_watch.json")
    newest = _newest_federal_year(data_dir)
    if newest is None:
        return _fail(report, "federal standard deduction table is missing")
    pinned, unpinned = _iter_manifest(data_dir)
    for row in unpinned:
        report.unpinned += 1
        report.lines.append(f"⚠️ unpinned: {row.manifest_id} {row.url}")
    pages = _watcher_pages(watch)
    fetched = await _fetch_all(pages)
    for url in pages:
        result = fetched[url]
        if result.error is not None or result.status != 200:
            detail = result.error or f"HTTP {result.status}"
            return _fail(report, f"watcher page fetch failed: {url} ({detail})")
        if _is_not_found_page(result.body):
            return _fail(report, f"watcher page canonical is {_NOT_FOUND_PATH}: {url}")
    irs_problem = _check_irs(fetched[pages[0]].body, newest, report)
    if irs_problem:
        return _fail(report, irs_problem)
    since_text = str(watch["hacienda_since"])
    since = tuple(int(part) for part in since_text.split("-"))
    excluded = _seen_numbers(list(watch["hacienda_seen"]))
    known = _known_hacienda_numbers(data_dir)
    hacienda_problem = _check_hacienda(
        [fetched[pages[1]].body, fetched[pages[2]].body],
        (since[0], since[1]),
        known,
        excluded,
        report,
    )
    if hacienda_problem:
        return _fail(report, hacienda_problem)
    _calendar_backstop(newest, report)
    unique = list(dict.fromkeys(row.url for row in pinned))
    bodies = await _fetch_all(unique) if unique else {}
    connection_failures = [
        url for url, result in bodies.items() if result.error is not None and result.status is None
    ]
    if unique and len(connection_failures) == len(unique):
        return _fail(report, "every request failed at the connection level")
    if connection_failures:
        return _fail(report, f"connection failed: {connection_failures[0]}")
    for row in pinned:
        _classify_pinned(row, bodies[row.url], report)
        if report.failed:
            return _fail(report, report.failed)
    for line in report.lines:
        print(line)
    print(report.summary())
    return 1 if any(line.startswith("❌") for line in report.lines) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--watch", type=Path, default=DEFAULT_WATCH)
    args = parser.parse_args(argv)
    return asyncio.run(run_check(args.data_dir, args.watch))


if __name__ == "__main__":
    sys.exit(main())
