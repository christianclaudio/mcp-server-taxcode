"""Hard response-size cap and paging for the statutes tools and statute resource.

Every statutes tool result stays under :data:`RESPONSE_MAX_CHARS` characters of JSON,
and every statute resource read under that many characters of text.

- ``statutes_lookup_statute`` returns at most :data:`TEXT_PAGE_CHARS` characters of each
  text field (``operative_text``, ``text_en``, ``text_es``) per call, all cut at the same
  offset. ``text_page`` reports the offset and full lengths; ``next_cursor`` is an opaque
  token for the next page, or ``None`` on the last page. Joining the pages in order gives
  back the exact text.
- ``statutes_search_tax_code`` and ``statutes_list_publications`` return at most
  :data:`LIST_MAX_LIMIT` items per call (a larger ``limit`` is lowered and reported in
  ``limit_applied``); ``next_offset`` is the ``offset`` for the next page, or ``None``.

- The ``taxcode://statutes/{jurisdiction}/{section}{?page_cursor}`` resource returns the
  same :data:`TEXT_PAGE_CHARS` window of the operative text and ends a non-final page with
  the ``page_cursor`` query for the next one (the same cursor the tool uses).

If a result would still exceed the cap (an unusually large title or list of versions),
the text window or the item count shrinks until it fits.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from typing import Any

RESPONSE_MAX_CHARS = 32_000
TEXT_PAGE_CHARS = 8_000
MIN_TEXT_PAGE_CHARS = 500
LIST_MAX_LIMIT = 25
_CURSOR_VERSION = 1
_TEXT_FIELDS = ("text_en", "text_es")


class PageCursorError(ValueError):
    """A ``page_cursor`` that this server did not issue or cannot read."""


def response_chars(payload: Any) -> int:
    """Size of a tool result as the characters of its JSON form."""
    return len(json.dumps(payload, ensure_ascii=False, default=str))


def encode_cursor(record_id: str, lang: str, offset: int) -> str:
    """Opaque cursor naming one statute version, its language and a text offset."""
    raw = json.dumps(
        {"v": _CURSOR_VERSION, "id": record_id, "lang": lang, "offset": offset},
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, str, int]:
    """Return ``(record_id, lang, offset)`` from :func:`encode_cursor`; raise on anything else."""
    try:
        padded = cursor.strip() + "=" * (-len(cursor.strip()) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeError, binascii.Error):
        raise PageCursorError("page_cursor is not a cursor from statutes_lookup_statute") from None
    if not (
        isinstance(data, dict)
        and data.get("v") == _CURSOR_VERSION
        and isinstance(data.get("id"), str)
        and isinstance(data.get("lang"), str)
        and type(data.get("offset")) is int
        and data["offset"] >= 0
    ):
        raise PageCursorError("page_cursor is not a cursor from statutes_lookup_statute")
    return data["id"], data["lang"], data["offset"]


def _text_page(record: dict[str, Any], offset: int, window: int) -> dict[str, Any]:
    lengths = {name: len(record[name] or "") for name in _TEXT_FIELDS}
    total = max(lengths.values())
    end = offset + window
    page = dict(record)
    for name in _TEXT_FIELDS:
        page[name] = (record[name] or "")[offset:end]
    page["operative_text"] = (record["operative_text"] or "")[offset:end]
    more = end < total
    page["text_page"] = {
        "offset": offset,
        "page_chars": window,
        "text_en_chars": lengths["text_en"],
        "text_es_chars": lengths["text_es"],
        "is_last_page": not more,
    }
    page["next_cursor"] = encode_cursor(record["id"], record["language"], end) if more else None
    return page


def page_statute(
    record: dict[str, Any], offset: int, wrap: Callable[[dict[str, Any]], dict[str, Any]]
) -> dict[str, Any]:
    """Cut a full ``lookup_statute`` record to one page that fits the response cap.

    ``wrap`` builds the whole tool result around the paged record, so the cap is
    measured on exactly what the client receives.
    """
    window = TEXT_PAGE_CHARS
    page = _text_page(record, offset, window)
    while response_chars(wrap(page)) > RESPONSE_MAX_CHARS and window > MIN_TEXT_PAGE_CHARS:
        window = max(window // 2, MIN_TEXT_PAGE_CHARS)
        page = _text_page(record, offset, window)
    return page


def applied_limit(limit: int) -> int:
    """``limit`` clamped to ``0..LIST_MAX_LIMIT``."""
    return min(max(limit, 0), LIST_MAX_LIMIT)


def fit_items(
    items: list[dict[str, Any]],
    has_more: bool,
    offset: int,
    build: Callable[[list[dict[str, Any]], int | None], dict[str, Any]],
) -> dict[str, Any]:
    """Build a list result from as many leading ``items`` as fit the response cap.

    ``build(page_items, next_offset)`` returns the tool result. Items dropped to fit
    the cap are reachable through ``next_offset`` like any later page.
    """
    count = len(items)
    while True:
        more = has_more or count < len(items)
        result = build(items[:count], offset + count if more else None)
        if count <= 1 or response_chars(result) <= RESPONSE_MAX_CHARS:
            return result
        count -= 1


def page_resource_text(
    header: str, text: str, offset: int, next_cursor: Callable[[int], str]
) -> str:
    """One page of a statute resource: ``header``, a window of ``text``, and a page note.

    A text that fits one page comes back whole with no note. Otherwise the note gives
    the character range and, unless this is the last page, the ``page_cursor`` query
    (``next_cursor(end)``) to read the next page. The note names the query rather than
    a full URI because the URI a client sees depends on where the server is mounted.
    """
    window = TEXT_PAGE_CHARS
    while True:
        end = min(offset + window, len(text))
        body = header + text[offset:end]
        if offset or end < len(text):
            body += f"\n\n[Text page: characters {offset}-{end} of {len(text)}. "
            body += (
                f"Next page: read this resource again with ?page_cursor={next_cursor(end)}]"
                if end < len(text)
                else "Last page.]"
            )
        if len(body) <= RESPONSE_MAX_CHARS or window <= MIN_TEXT_PAGE_CHARS:
            return body
        window = max(window // 2, MIN_TEXT_PAGE_CHARS)
