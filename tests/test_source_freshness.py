"""Offline tests for the official-source freshness check.

HTTP is the fake socket layer. Nothing here opens a real connection.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from tests.fake_network import FakeNetwork, answer

scripts_dir = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(scripts_dir))

import check_source_freshness as freshness  # noqa: E402

PUBLIC_IP = "93.184.215.14"
PDF_URL = "https://files.vendor.test/sample.pdf"
HTML_URL = "https://pages.vendor.test/guide"
OPEN_URL = "https://files.vendor.test/unpinned.pdf"
NEWS_URL = "https://news.vendor.test/newsroom/news-releases-for-current-month"
LIST_URL = "https://hacienda.vendor.test/publicaciones-oficiales"
PDF_BODY = b"%PDF-1.4\nfreshness-match\n"
OTHER_PDF = b"%PDF-1.4\nfreshness-other\n"


class RoutedNetwork(FakeNetwork):
    """One canned response per path, optionally a sequence so a retry can be seen."""

    def __init__(self, routes: dict[str, list[tuple[int, bytes]]]) -> None:
        super().__init__()
        self.routes = routes
        self.hits: dict[str, int] = {}

    def _on_write(self, written: bytearray, buffer: bytes, pending: bytearray) -> None:
        line = buffer.split(b"\r\n", 1)[0]
        if line.endswith(b" HTTP/1.1"):
            path = line.split(b" ", 2)[1].decode()
            sequence = self.routes.get(path, [(404, b"missing")])
            index = min(self.hits.get(path, 0), len(sequence) - 1)
            self.hits[path] = self.hits.get(path, 0) + 1
            status, body = sequence[index]
            reason = b"OK" if status == 200 else b"Error"
            self.response = (
                b"HTTP/1.1 %d %s\r\nContent-Length: %d\r\nConnection: close\r\n\r\n"
                % (status, reason, len(body))
            ) + body
        super()._on_write(written, buffer, pending)


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _watch(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "irs_newsroom_url": NEWS_URL,
        "hacienda_listing_url": LIST_URL,
        "hacienda_since": "26-01",
        "hacienda_seen": [],
    }
    document.update(overrides)
    return document


def _newsroom(year: int | None = 2027) -> bytes:
    link = ""
    if year is not None:
        link = (
            f'<a href="/newsroom/irs-releases-tax-inflation-adjustments-for-tax-year-{year}">'
            "adjustments</a>"
        )
    return (f'<html><a href="/newsroom/some-other-release">other</a>{link}</html>').encode()


def _listing(slug: str | None) -> bytes:
    if slug is None:
        return b"<html><p>no publications</p></html>"
    return f'<html><a href="/publicaciones/{slug}">row</a></html>'.encode()


def _not_found() -> bytes:
    return b'<html><link rel="canonical" href="/pagina-no-encontrada"></html>'


KNOWN_SLUG = "boletin-informativo-de-rentas-internas-num-26-01-bi-ri-26-01"


def _tree(
    tmp_path: Path,
    *,
    sha: str | None = None,
    extra_entries: list[dict[str, Any]] | None = None,
    publications: list[dict[str, Any]] | None = None,
    watch: dict[str, Any] | None = None,
    watch_text: str | None = None,
    newest: int = 2027,
) -> tuple[Path, Path]:
    data = tmp_path / "data"
    (data / "manifest").mkdir(parents=True, exist_ok=True)
    (data / "federal").mkdir(exist_ok=True)
    (data / "publications").mkdir(exist_ok=True)
    digest = sha if sha is not None else _sha(PDF_BODY)
    entries: list[dict[str, Any]] = [
        {
            "kind": "publication",
            "id": "us-sample",
            "url": PDF_URL,
            "content_sha256": digest,
            "retrieved": "2026-11-18",
        }
    ]
    if extra_entries:
        entries.extend(extra_entries)
    (data / "manifest" / "us.json").write_text(
        json.dumps({"schema": 1, "jurisdiction": "US", "entries": entries}),
        encoding="utf-8",
    )
    (data / "federal" / "standard_deduction.json").write_text(
        json.dumps({"years": [{"tax_year": newest}]}),
        encoding="utf-8",
    )
    rows = publications if publications is not None else [{"doc_number": "BI RI 26-01"}]
    (data / "publications" / "pr.json").write_text(
        json.dumps({"publications": rows}),
        encoding="utf-8",
    )
    watch_path = tmp_path / "source_watch.json"
    if watch_text is not None:
        watch_path.write_text(watch_text, encoding="utf-8")
    else:
        payload = json.dumps(watch if watch is not None else _watch())
        watch_path.write_text(payload, encoding="utf-8")
    return data, watch_path


def _routes(
    pdf: list[tuple[int, bytes]] | None = None,
    news: bytes | None = None,
    page0: bytes | None = None,
    page1: bytes | None = None,
    html: list[tuple[int, bytes]] | None = None,
) -> dict[str, list[tuple[int, bytes]]]:
    news_body = news if news is not None else _newsroom()
    first = page0 if page0 is not None else _listing(KNOWN_SLUG)
    second = page1 if page1 is not None else _listing(KNOWN_SLUG)
    routes = {
        "/sample.pdf": pdf or [(200, PDF_BODY)],
        "/newsroom/news-releases-for-current-month": [(200, news_body)],
        "/publicaciones-oficiales?page=0": [(200, first)],
        "/publicaciones-oficiales?page=1": [(200, second)],
    }
    if html is not None:
        routes["/guide"] = html
    return routes


# A fixed date so results never depend on the real calendar (the backstop reads it).
DEFAULT_TODAY = date(2026, 11, 18)


def _run(
    tmp_path: Path,
    routes: dict[str, list[tuple[int, bytes]]],
    *,
    sha: str | None = None,
    extra_entries: list[dict[str, Any]] | None = None,
    publications: list[dict[str, Any]] | None = None,
    watch: dict[str, Any] | None = None,
    watch_text: str | None = None,
    today: date = DEFAULT_TODAY,
    fail: set[str] | None = None,
) -> tuple[int, str, RoutedNetwork]:
    data, watch_path = _tree(
        tmp_path,
        sha=sha,
        extra_entries=extra_entries,
        publications=publications,
        watch=watch,
        watch_text=watch_text,
    )
    net = RoutedNetwork(routes)
    if fail:
        net.fail = set(fail)
    freshness._today = lambda: today  # type: ignore[method-assign]
    try:
        with net.patches(), pytest.MonkeyPatch.context() as monkey:
            monkey.setattr("socket.getaddrinfo", lambda *a, **k: answer(PUBLIC_IP))
            code = freshness.main(["--data-dir", str(data), "--watch", str(watch_path)])
    finally:
        freshness._today = date.today  # type: ignore[method-assign]
    return code, "", net


def _output(capsys: pytest.CaptureFixture[str]) -> str:
    captured = capsys.readouterr()
    return captured.out + captured.err


def test_matching_hash_is_fresh(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, net = _run(tmp_path, _routes())
    text = _output(capsys)
    assert code == 0
    assert text.splitlines()[-1] == "checked 1, changed 0, gone 0, unpinned 0, newer 0"
    assert "❌" not in text
    assert net.hits["/sample.pdf"] == 1


def test_changed_pdf_byte_is_stale(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, _net = _run(tmp_path, _routes(pdf=[(200, OTHER_PDF)]))
    text = _output(capsys)
    assert code == 1
    assert "❌ Source changed: us-sample https://files.vendor.test/sample.pdf" in text
    recorded = _sha(PDF_BODY)[:16]
    now = _sha(OTHER_PDF)[:16]
    assert f"recorded {recorded}, now {now}, retrieved 2026-11-18" in text


def test_pdf_404_is_gone(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, _net = _run(tmp_path, _routes(pdf=[(404, b"missing")]))
    text = _output(capsys)
    assert code == 1
    assert "❌ Source gone: us-sample https://files.vendor.test/sample.pdf (404)" in text


def test_not_found_canonical_is_gone(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    extra = [
        {
            "kind": "publication",
            "id": "us-page",
            "url": HTML_URL,
            "content_sha256": _sha(b"old-page"),
            "retrieved": "2026-11-18",
        }
    ]
    code, _, _net = _run(
        tmp_path,
        _routes(html=[(200, _not_found())]),
        extra_entries=extra,
    )
    text = _output(capsys)
    assert code == 1
    assert (
        "❌ Source gone: us-page https://pages.vendor.test/guide (canonical /pagina-no-encontrada)"
        in text
    )


def test_html_hash_change_is_a_warning(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    extra = [
        {
            "kind": "publication",
            "id": "us-page",
            "url": HTML_URL,
            "content_sha256": _sha(b"old-page"),
            "retrieved": "2026-11-18",
        }
    ]
    code, _, _net = _run(
        tmp_path,
        _routes(html=[(200, b"<html><p>new chrome</p></html>")]),
        extra_entries=extra,
    )
    text = _output(capsys)
    assert code == 0
    assert "⚠️" in text
    assert "❌" not in text


def test_unpinned_record_is_never_fetched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    extra = [
        {
            "kind": "publication",
            "id": "us-open",
            "url": OPEN_URL,
            "content_sha256": "not_recorded",
            "retrieved": "not_recorded",
        }
    ]
    code, _, net = _run(tmp_path, _routes(), extra_entries=extra)
    text = _output(capsys)
    assert code == 0
    assert "⚠️ unpinned: us-open https://files.vendor.test/unpinned.pdf" in text
    assert text.splitlines()[-1] == "checked 1, changed 0, gone 0, unpinned 1, newer 0"
    assert "/unpinned.pdf" not in net.hits


def test_newer_irs_year_is_stale(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, _net = _run(tmp_path, _routes(news=_newsroom(2028)))
    text = _output(capsys)
    assert code == 1
    assert "❌ Newer publication: IRS inflation adjustments for tax year 2028 (" in text


def test_calendar_backstop_december_and_november(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, _net = _run(tmp_path, _routes(), today=date(2027, 12, 2))
    text = _output(capsys)
    assert code == 1
    assert (
        "❌ Expected publication missing: IRS inflation revenue procedure for tax year 2028" in text
    )
    code, _, _net = _run(tmp_path, _routes(), today=date(2027, 11, 30))
    text = _output(capsys)
    assert code == 0
    assert "Expected publication missing" not in text
    code, _, _net = _run(tmp_path, _routes(), today=date(2028, 1, 1))
    text = _output(capsys)
    assert code == 1
    assert (
        "❌ Expected publication missing: IRS inflation revenue procedure for tax year 2028" in text
    )


def test_unknown_hacienda_bulletin_is_newer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    slug = "boletin-informativo-de-rentas-internas-num-26-07-bi-ri-26-07"
    code, _, _net = _run(tmp_path, _routes(page0=_listing(slug), page1=_listing(KNOWN_SLUG)))
    text = _output(capsys)
    assert code == 1
    assert (
        f"❌ Newer publication: Hacienda BI RI 26-07 (https://hacienda.pr.gov/publicaciones/{slug})"
    ) in text


def test_known_hacienda_doc_number_is_fresh(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, _net = _run(tmp_path, _routes())
    assert code == 0
    assert "Newer publication" not in _output(capsys)


def test_hacienda_seen_exclusion_is_fresh(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    slug = "boletin-informativo-de-rentas-internas-num-26-07-bi-ri-26-07"
    watch = _watch(hacienda_seen=["BI RI 26-07  # excluded: outside the catalog on purpose"])
    code, _, _net = _run(
        tmp_path,
        _routes(page0=_listing(slug), page1=_listing(KNOWN_SLUG)),
        watch=watch,
    )
    assert code == 0
    assert "Newer publication" not in _output(capsys)


def test_compound_doc_number_matches_on_its_first_part(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    slug = "determinacion-administrativa-num-26-01-da-26-01"
    code, _, _net = _run(
        tmp_path,
        _routes(page0=_listing(slug), page1=_listing(KNOWN_SLUG)),
        publications=[{"doc_number": "DA 26-01 / OA DDEC 2026-01"}, {"doc_number": "BI RI 26-01"}],
    )
    assert code == 0
    assert "Newer publication" not in _output(capsys)


def test_number_below_since_is_fresh(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    slug = "boletin-informativo-de-rentas-internas-num-25-08-bi-ri-25-08"
    code, _, _net = _run(tmp_path, _routes(page0=_listing(slug), page1=_listing(KNOWN_SLUG)))
    assert code == 0
    assert "Newer publication" not in _output(capsys)


def test_listing_not_found_page_fails_the_monitor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, _net = _run(tmp_path, _routes(page0=_not_found()))
    text = _output(capsys)
    assert code == 2
    assert "::error::freshness monitor failed:" in text
    assert "❌ Source changed" not in text


def test_zero_slugs_fail_the_monitor(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, _net = _run(tmp_path, _routes(page0=_listing(None), page1=_listing(None)))
    text = _output(capsys)
    assert code == 2
    assert "zero matching slugs" in text


def test_every_connection_error_fails_the_monitor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, _net = _run(tmp_path, _routes(), fail={PUBLIC_IP})
    text = _output(capsys)
    assert code == 2
    assert "::error::freshness monitor failed:" in text
    assert "❌ Source changed" not in text


def test_malformed_watch_file_fails_before_the_network(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data, watch_path = _tree(tmp_path, watch_text="{")
    net = RoutedNetwork(_routes())
    with net.patches(), pytest.MonkeyPatch.context() as monkey:
        monkey.setattr("socket.getaddrinfo", lambda *a, **k: answer(PUBLIC_IP))
        code = freshness.main(["--data-dir", str(data), "--watch", str(watch_path)])
    text = _output(capsys)
    assert code == 2
    assert "malformed source_watch.json" in text
    assert net.attempts == []


def test_a_5xx_is_retried_once(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, net = _run(tmp_path, _routes(pdf=[(503, b"busy"), (200, PDF_BODY)]))
    assert code == 0
    assert net.hits["/sample.pdf"] == 2
    assert "❌" not in _output(capsys)
