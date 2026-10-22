"""DuckDB database layer for statutory corpora and regulatory guidance."""

from __future__ import annotations

import logging
import re
import threading
from typing import Any

import duckdb

from taxcode_mcp.catalog import load_catalog, statute_db_row
from taxcode_mcp.config import settings
from taxcode_mcp.disclaimers import (
    STATUTE_TRANSLATION_DISCLAIMER,  # noqa: F401  (re-exported for compatibility)
    reference_labels,
)

logger = logging.getLogger(__name__)


# US Code Title 26 rows are pinned to one OLRC release point (USLM XML).
USLM_TITLE26_RELEASE_POINT = "119-121"
USLM_TITLE26_SOURCE_URL = (
    "https://uscode.house.gov/download/releasepoints/us/pl/119/121/xml_usc26@119-121.zip"
)
# eCFR point-in-time date cited by the Treasury Regulation rows.
ECFR_TITLE26_DATE = "2026-10-14"

# Seed rows come from the versioned data files (``taxcode_mcp/data``, see
# ``taxcode_mcp.catalog``); this module and the ingest script share one source.
_CATALOG = load_catalog()
INITIAL_STATUTES: list[dict[str, Any]] = [statute_db_row(s) for s in _CATALOG.statutes]
INITIAL_PUBLICATIONS: list[dict[str, Any]] = [dict(p) for p in _CATALOG.publications]


def is_official_text(source_url: str | None) -> bool:
    """True for rows holding official US Code text from the pinned OLRC release point."""
    return source_url == USLM_TITLE26_SOURCE_URL


def _year_is_known(value: Any) -> bool:
    """True when ``value`` is a year that parses as an integer."""
    try:
        int(str(value).strip())
    except (TypeError, ValueError):
        return False
    return True


def validity_label(effective_year: Any, superseded_year: int | None) -> dict[str, str]:
    """Bilingual effective/superseded label for a statute row.

    An empty, NULL or non-numeric ``effective_year`` gets an explicit
    "unknown" label instead of printing the raw value. Known years are unchanged.
    """
    if _year_is_known(effective_year):
        en_from = f"Effective {effective_year}"
        es_from = f"Vigente desde {effective_year}"
    else:
        en_from = "Effective year unknown"
        es_from = "Año de vigencia desconocido"
    if superseded_year is None:
        return {
            "en": f"{en_from}; current",
            "es": f"{es_from}; en vigor",
        }
    return {
        "en": f"{en_from}; superseded {superseded_year}",
        "es": f"{es_from}; sustituida en {superseded_year}",
    }


_CATEGORY_SEPARATORS = re.compile(r"[\s_-]+")


def normalize_category(category: str) -> str:
    """Fold a publication category to its stored slug form.

    Case, whitespace, hyphens and underscores are interchangeable, so
    ``"carta circular"``, ``"Carta-Circular"`` and ``"CARTA_CIRCULAR"`` all
    become ``"carta-circular"``.
    """
    return _CATEGORY_SEPARATORS.sub("-", category.strip().lower()).strip("-")


PUBLICATION_CATEGORIES: tuple[str, ...] = tuple(
    sorted({normalize_category(str(p["category"])) for p in INITIAL_PUBLICATIONS})
)

# Full names (lowercase, single-spaced) accepted wherever a jurisdiction code is.
JURISDICTION_NAMES: dict[str, str] = {
    "federal": "US",
    "united states": "US",
    "usa": "US",
    "puerto rico": "PR",
    "district of columbia": "DC",
    "washington dc": "DC",
    "washington d.c.": "DC",
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}

STATUTE_CORPORA: frozenset[str] = frozenset(str(s["corpus"]).upper() for s in INITIAL_STATUTES)
KNOWN_JURISDICTIONS: frozenset[str] = frozenset(JURISDICTION_NAMES.values())


def resolve_jurisdiction(value: str) -> str:
    """Map a jurisdiction code or full name to its code (``"washington"`` -> ``"WA"``).

    Unknown values are returned upper-cased so they simply match nothing.
    """
    key = " ".join(value.strip().lower().replace("_", " ").replace("-", " ").split())
    return JURISDICTION_NAMES.get(key, key.upper())


# Very common words that would match nearly every row; dropped from multi-term search.
SEARCH_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "under",
        "about",
        "what",
        "del",
        "las",
        "los",
        "para",
        "por",
        "con",
        "que",
        "una",
    }
)
_TERM_STRIP = ".,;:!?\"'()[]{}"


def year_sort_key(value: Any) -> tuple[int, int]:
    """Sort key for ``effective_year``: newest first, empty or non-numeric last.

    ``effective_year`` can be NULL (the column is nullable) or, in an older or
    hand-built database, a non-numeric value; neither may crash the search.
    """
    try:
        return (0, -int(str(value).strip()))
    except (TypeError, ValueError):
        return (1, 0)


def search_terms(query: str) -> list[str]:
    """Split a query into distinct lowercase terms for ranked matching.

    A leading ``§`` is stripped from each term (``"§933"`` -> ``"933"``; a lone
    ``"§"`` is dropped). Terms shorter than 3 characters (unless they contain a
    digit, e.g. ``"1"``) and :data:`SEARCH_STOPWORDS` are dropped so they don't
    flood results. Order of first appearance is kept.
    """
    terms: list[str] = []
    for raw in query.lower().split():
        # A leading section sign is not part of the term: '§933' -> '933', '§' -> ''.
        term = raw.strip(_TERM_STRIP).lstrip("§").lstrip().strip(_TERM_STRIP)
        if not term or term in SEARCH_STOPWORDS:
            continue
        if len(term) < 3 and not any(ch.isdigit() for ch in term):
            continue
        if term not in terms:
            terms.append(term)
    return terms


# Search results carry a short snippet instead of the full text (the full text
# of one statute can run 10-23K tokens); statutes_lookup_statute returns it by id.
SNIPPET_MAX_CHARS = 300
SNIPPET_LEAD_CHARS = 80
_ELLIPSIS = "\u2026"


def _first_match(text_lower: str, phrase: str, terms: list[str]) -> tuple[int, int] | None:
    """Return ``(position, length)`` of the phrase, else of the earliest matched term."""
    if phrase:
        pos = text_lower.find(phrase)
        if pos >= 0:
            return pos, len(phrase)
    hits = [(text_lower.find(t), len(t)) for t in terms]
    found = [h for h in hits if h[0] >= 0]
    return min(found) if found else None


def make_snippet(
    text: str | None, phrase: str, terms: list[str], max_chars: int = SNIPPET_MAX_CHARS
) -> tuple[str, bool]:
    """Return ``(snippet, matched)``: up to ``max_chars`` of ``text`` around the first match.

    Whitespace is collapsed. The window opens about :data:`SNIPPET_LEAD_CHARS`
    before the whole phrase, or else the earliest matched term, and is cut on word
    boundaries; ``\u2026`` marks text cut at either end and counts toward
    ``max_chars``. With no match in ``text`` the snippet is its opening words and
    ``matched`` is ``False``.
    """
    flat = " ".join((text or "").split())
    match = _first_match(flat.lower(), phrase, terms)
    if len(flat) <= max_chars:
        return flat, match is not None
    pos, length = match or (0, 0)
    budget = max_chars - 2 * len(_ELLIPSIS)
    start = max(0, pos - SNIPPET_LEAD_CHARS)
    if length > budget:
        start = pos  # a match longer than the window is cut at the window's end
    elif pos + length > start + budget:
        start = pos + length - budget
    # Near the end of the text, open earlier so the window stays full.
    start = min(start, len(flat) - budget)
    if start > 0 and flat[start - 1] != " ":
        space = flat.find(" ", start, pos)
        if space >= 0:
            start = space + 1
    end = start + budget
    if end < len(flat) and flat[end] != " ":
        space = flat.rfind(" ", max(pos + length, start), end)
        if space >= 0:
            end = space
    body = flat[start:end].strip()
    prefix = _ELLIPSIS if start > 0 else ""
    suffix = _ELLIPSIS if end < len(flat) else ""
    return f"{prefix}{body}{suffix}", match is not None


def pick_snippet(
    texts: list[tuple[str, str | None]], phrase: str, terms: list[str]
) -> tuple[str, str]:
    """Return ``(snippet, language)`` from the first text that matches, else the first non-empty.

    ``texts`` is ``[(language, text), ...]`` in preference order. A row can match
    on its title, citation or other-language text only, so the fallback is the
    opening of the preferred text.
    """
    built = [(lang, *make_snippet(text, phrase, terms)) for lang, text in texts]
    for lang, snippet, matched in built:
        if matched:
            return snippet, lang
    for lang, snippet, _ in built:
        if snippet:
            return snippet, lang
    return "", texts[0][0]


class TaxDatabase:
    """Thread-safe DuckDB manager for tax statutes and administrative guidance."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._mem_conn: duckdb.DuckDBPyConnection | None = None
        if self.db_path == ":memory:":
            self._mem_conn = duckdb.connect(":memory:")
        self._init_db()

    def _get_write_conn(self) -> duckdb.DuckDBPyConnection:
        if self._mem_conn is not None:
            return self._mem_conn
        return duckdb.connect(self.db_path)

    def _get_read_conn(self) -> duckdb.DuckDBPyConnection:
        if self._mem_conn is not None:
            return self._mem_conn
        return duckdb.connect(self.db_path, read_only=True)

    def _init_db(self) -> None:
        """Create tables and seed initial data if required."""
        with self._lock:
            conn = self._get_write_conn()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS statutes (
                        id VARCHAR PRIMARY KEY,
                        jurisdiction VARCHAR,
                        corpus VARCHAR,
                        citation VARCHAR,
                        section VARCHAR,
                        title VARCHAR,
                        text_es VARCHAR,
                        text_en VARCHAR,
                        effective_year INTEGER,
                        superseded_year INTEGER,
                        source_url VARCHAR,
                        cross_references VARCHAR[]
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS publications (
                        id VARCHAR PRIMARY KEY,
                        category VARCHAR,
                        doc_number VARCHAR UNIQUE,
                        doc_date VARCHAR,
                        title VARCHAR,
                        summary VARCHAR,
                        source_url VARCHAR
                    );
                    """
                )

                # Upsert seed statutes by id so file-backed DBs pick up changed seed rows
                for s in INITIAL_STATUTES:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO statutes (
                            id, jurisdiction, corpus, citation, section,
                            title, text_es, text_en, effective_year,
                            superseded_year, source_url, cross_references
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        [
                            s["id"],
                            s["jurisdiction"],
                            s["corpus"],
                            s["citation"],
                            s["section"],
                            s["title"],
                            s["text_es"],
                            s["text_en"],
                            s["effective_year"],
                            s["superseded_year"],
                            s["source_url"],
                            s["cross_references"],
                        ],
                    )

                # Add seed publications missing from the table, so existing file-backed DBs
                # pick up new seed rows. Rows already present (same id or doc_number) are kept.
                for p in INITIAL_PUBLICATIONS:
                    conn.execute(
                        """
                        INSERT INTO publications (
                            id, category, doc_number, doc_date, title, summary, source_url
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT DO NOTHING;
                        """,
                        [
                            p["id"],
                            p["category"],
                            p["doc_number"],
                            p["doc_date"],
                            p["title"],
                            p["summary"],
                            p["source_url"],
                        ],
                    )
            finally:
                if self._mem_conn is None:
                    conn.close()

    def get_connection(self) -> duckdb.DuckDBPyConnection:
        """Open a DuckDB connection or in-memory cursor.

        Uses ``read_only=True`` for non-memory files to allow multiple concurrent readers.
        """
        if self._mem_conn is not None:
            return self._mem_conn.cursor()
        return duckdb.connect(self.db_path, read_only=True)

    def get_stats(self) -> tuple[int, int]:
        """Return the count of indexed (statutes, publications)."""
        with self._lock:
            conn = self._get_read_conn()
            try:
                s_count = conn.execute("SELECT COUNT(*) FROM statutes;").fetchone()
                p_count = conn.execute("SELECT COUNT(*) FROM publications;").fetchone()
                return (
                    s_count[0] if s_count else 0,
                    p_count[0] if p_count else 0,
                )
            finally:
                if self._mem_conn is None:
                    conn.close()

    def lookup_statute(
        self,
        citation_or_section: str,
        jurisdiction: str | None = None,
        tax_year: int | None = None,
        lang: str = "en",
    ) -> dict[str, Any] | None:
        """Find a statute by record id, exact citation or section code, and optional tax year.

        The record id is the ``id`` that :meth:`search_statutes` returns; it names
        one version, so it is preferred over a citation match.
        """
        query_val = citation_or_section.strip()
        with self._lock:
            conn = self._get_read_conn()
            try:
                sql = """
                    SELECT id, jurisdiction, corpus, citation, section, title,
                           text_es, text_en, effective_year, superseded_year,
                           source_url, cross_references
                    FROM statutes
                    WHERE (
                        LOWER(id) = LOWER(?)
                        OR LOWER(citation) = LOWER(?)
                        OR LOWER(section) = LOWER(?)
                        OR LOWER(REPLACE(citation, ' ', '')) = LOWER(REPLACE(?, ' ', ''))
                    )
                """
                params: list[Any] = [query_val, query_val, query_val, query_val]
                if jurisdiction:
                    sql += " AND UPPER(jurisdiction) = UPPER(?)"
                    params.append(jurisdiction.strip())

                if tax_year is not None:
                    sql += (
                        " AND effective_year <= ? "
                        "AND (superseded_year IS NULL OR ? < superseded_year)"
                    )
                    params.extend([tax_year, tax_year])

                # An exact record id (from search results) names one version; it wins.
                sql += (
                    " ORDER BY (LOWER(id) = LOWER(?)) DESC, effective_year DESC, "
                    "(superseded_year IS NULL) DESC LIMIT 1;"
                )
                params.append(query_val)
                result = conn.execute(sql, params).fetchone()
                if not result:
                    return None

                text = result[7] if lang.lower() == "en" else result[6]
                matched_citation = result[3]

                history_rows = conn.execute(
                    """
                    SELECT effective_year, superseded_year, id
                    FROM statutes
                    WHERE LOWER(citation) = LOWER(?)
                    ORDER BY effective_year DESC, (superseded_year IS NULL) DESC;
                    """,
                    [matched_citation],
                ).fetchall()
                available_versions = [
                    {
                        "effective_year": h[0],
                        "superseded_year": h[1],
                        "is_current": h[1] is None,
                        "validity": validity_label(h[0], h[1]),
                        "id": h[2],
                    }
                    for h in history_rows
                ]

                return {
                    "id": result[0],
                    "jurisdiction": result[1],
                    "corpus": result[2],
                    "citation": result[3],
                    "section": result[4],
                    "title": result[5],
                    "operative_text": text,
                    "text_es": result[6],
                    "text_en": result[7],
                    "effective_year": result[8],
                    "superseded_year": result[9],
                    "is_current": result[9] is None,
                    "validity": validity_label(result[8], result[9]),
                    "available_versions": available_versions,
                    "source_url": result[10],
                    "cross_references": result[11] or [],
                    "language": lang.lower(),
                    "official_language": "es" if result[1] == "PR" else "en",
                    **reference_labels(official_text=is_official_text(result[10])),
                }
            finally:
                if self._mem_conn is None:
                    conn.close()

    def search_statutes(
        self,
        query: str,
        jurisdiction: str | None = None,
        corpus: str | None = None,
        tax_year: int | None = None,
        lang: str = "en",
        limit: int = 5,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Search statutes across title, citation, and multilingual operative text.

        Ranking: rows containing the whole query phrase come first, then rows by
        how many distinct query terms (see :func:`search_terms`) they contain,
        then newest ``effective_year``, then ``id``. A row must match the phrase
        or at least one term. ``jurisdiction`` accepts codes or full names, and a
        ``corpus`` that is not a known corpus code but names a jurisdiction
        (``"washington"``) filters by that jurisdiction instead.

        Each row carries a ``snippet`` (see :func:`make_snippet`) in place of the
        full text, plus ``snippet_language``; ``lookup_statute(id)`` returns the
        full text. Matching and ranking read the full text as before. ``offset``
        skips that many ranked rows (paging).
        """
        phrase = " ".join(query.strip().lower().split())
        terms = search_terms(phrase)
        # An empty query keeps the old behaviour of matching every row ("%%").
        patterns = [f"%{t}%" for t in dict.fromkeys([phrase, *terms])]
        jur = resolve_jurisdiction(jurisdiction) if jurisdiction else None
        corpus_code = corpus.strip().upper() if corpus else None
        if corpus and corpus_code not in STATUTE_CORPORA:
            named = resolve_jurisdiction(corpus)
            if named in KNOWN_JURISDICTIONS:
                # A jurisdiction passed as corpus; conflicting filters match nothing.
                jur = named if jur in (None, named) else ""
                corpus_code = None
        with self._lock:
            conn = self._get_read_conn()
            try:
                haystack = (
                    "LOWER(COALESCE(title, '') || ' ' || COALESCE(citation, '') || ' ' || "
                    "COALESCE(text_en, '') || ' ' || COALESCE(text_es, ''))"
                )
                sql = f"""
                    SELECT id, jurisdiction, corpus, citation, section, title,
                           text_es, text_en, effective_year, superseded_year,
                           source_url, cross_references, {haystack} AS hay
                    FROM statutes
                    WHERE ({" OR ".join(f"{haystack} LIKE ?" for _ in patterns)})
                """
                params: list[Any] = list(patterns)
                if jur is not None:
                    sql += " AND UPPER(jurisdiction) = ?"
                    params.append(jur)
                if corpus_code:
                    sql += " AND UPPER(corpus) = ?"
                    params.append(corpus_code)
                if tax_year is not None:
                    sql += (
                        " AND effective_year <= ? "
                        "AND (superseded_year IS NULL OR ? < superseded_year)"
                    )
                    params.extend([tax_year, tax_year])

                rows = conn.execute(sql, params).fetchall()

                def rank(r: tuple[Any, ...]) -> tuple[int, int, tuple[int, int], str]:
                    hay: str = r[12]
                    hits = sum(1 for t in terms if t in hay)
                    return (0 if phrase in hay else 1, -hits, year_sort_key(r[8]), str(r[0]))

                rows.sort(key=rank)
                output: list[dict[str, Any]] = []
                preferred = "en" if lang.lower() == "en" else "es"
                start = max(offset, 0)
                for r in rows[start : start + max(limit, 0)]:
                    by_lang = {"en": r[7], "es": r[6]}
                    other = "es" if preferred == "en" else "en"
                    snippet, snippet_lang = pick_snippet(
                        [(preferred, by_lang[preferred]), (other, by_lang[other])],
                        phrase,
                        terms,
                    )
                    output.append(
                        {
                            "id": r[0],
                            "jurisdiction": r[1],
                            "corpus": r[2],
                            "citation": r[3],
                            "section": r[4],
                            "title": r[5],
                            "snippet": snippet,
                            "snippet_language": snippet_lang,
                            "effective_year": r[8],
                            "superseded_year": r[9],
                            "is_current": r[9] is None,
                            "validity": validity_label(r[8], r[9]),
                            "source_url": r[10],
                            "cross_references": r[11] or [],
                            **reference_labels(official_text=is_official_text(r[10])),
                        }
                    )
                return output
            finally:
                if self._mem_conn is None:
                    conn.close()

    def list_publications(
        self,
        category: str | None = None,
        since_date: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List administrative circulars and determinations, newest first, from ``offset``."""
        with self._lock:
            conn = self._get_read_conn()
            try:
                sql = """
                    SELECT id, category, doc_number, doc_date, title, summary, source_url
                    FROM publications
                    WHERE 1=1
                """
                params: list[Any] = []
                if category:
                    sql += (
                        " AND TRIM(REGEXP_REPLACE(LOWER(category), '[\\s_-]+', '-', 'g'), '-') = ?"
                    )
                    params.append(normalize_category(category))
                if since_date:
                    sql += " AND doc_date >= ?"
                    params.append(since_date.strip())

                # ``id`` breaks doc_date ties so pages never repeat or skip a row.
                sql += " ORDER BY doc_date DESC, id LIMIT ? OFFSET ?;"
                params.extend([limit, max(offset, 0)])

                rows = conn.execute(sql, params).fetchall()
                return [
                    {
                        "id": r[0],
                        "category": r[1],
                        "doc_number": r[2],
                        "doc_date": r[3],
                        "title": r[4],
                        "summary": r[5],
                        "source_url": r[6],
                        **reference_labels(),
                    }
                    for r in rows
                ]
            finally:
                if self._mem_conn is None:
                    conn.close()

    def insert_statute(self, statute: dict[str, Any]) -> None:
        """Insert a statute into the database."""
        with self._lock:
            conn = self._get_write_conn()
            try:
                conn.execute(
                    """
                    INSERT INTO statutes (
                        id, jurisdiction, corpus, citation, section,
                        title, text_es, text_en, effective_year,
                        superseded_year, source_url, cross_references
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    [
                        statute["id"],
                        statute["jurisdiction"],
                        statute["corpus"],
                        statute["citation"],
                        statute["section"],
                        statute["title"],
                        statute.get("text_es", ""),
                        statute.get("text_en", ""),
                        statute.get("effective_year", 2026),
                        statute.get("superseded_year"),
                        statute.get("source_url", ""),
                        statute.get("cross_references", []),
                    ],
                )
            finally:
                if self._mem_conn is None:
                    conn.close()

    def upsert_statute(self, statute: dict[str, Any]) -> None:
        """Insert or update a statute in the database."""
        with self._lock:
            conn = self._get_write_conn()
            try:
                conn.execute(
                    """
                    INSERT INTO statutes (
                        id, jurisdiction, corpus, citation, section,
                        title, text_es, text_en, effective_year,
                        superseded_year, source_url, cross_references
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO UPDATE SET
                        jurisdiction = EXCLUDED.jurisdiction,
                        corpus = EXCLUDED.corpus,
                        citation = EXCLUDED.citation,
                        section = EXCLUDED.section,
                        title = EXCLUDED.title,
                        text_es = EXCLUDED.text_es,
                        text_en = EXCLUDED.text_en,
                        effective_year = EXCLUDED.effective_year,
                        superseded_year = EXCLUDED.superseded_year,
                        source_url = EXCLUDED.source_url,
                        cross_references = EXCLUDED.cross_references;
                    """,
                    [
                        statute["id"],
                        statute["jurisdiction"],
                        statute["corpus"],
                        statute["citation"],
                        statute["section"],
                        statute["title"],
                        statute.get("text_es", ""),
                        statute.get("text_en", ""),
                        statute.get("effective_year", 2026),
                        statute.get("superseded_year"),
                        statute.get("source_url", ""),
                        statute.get("cross_references", []),
                    ],
                )
            finally:
                if self._mem_conn is None:
                    conn.close()

    def upsert_publication(self, pub: dict[str, Any]) -> None:
        """Insert or update an administrative publication in the database."""
        with self._lock:
            conn = self._get_write_conn()
            try:
                conn.execute(
                    """
                    INSERT INTO publications (
                        id, category, doc_number, doc_date, title, summary, source_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO UPDATE SET
                        category = EXCLUDED.category,
                        doc_number = EXCLUDED.doc_number,
                        doc_date = EXCLUDED.doc_date,
                        title = EXCLUDED.title,
                        summary = EXCLUDED.summary,
                        source_url = EXCLUDED.source_url;
                    """,
                    [
                        pub["id"],
                        pub["category"],
                        pub["doc_number"],
                        pub["doc_date"],
                        pub["title"],
                        pub.get("summary", ""),
                        pub.get("source_url", ""),
                    ],
                )
            finally:
                if self._mem_conn is None:
                    conn.close()

    def delete_statute(self, statute_id: str) -> bool:
        """Delete a statute by ID."""
        with self._lock:
            conn = self._get_write_conn()
            try:
                res = conn.execute(
                    "DELETE FROM statutes WHERE id = ? RETURNING id;", [statute_id]
                ).fetchone()
                return res is not None
            finally:
                if self._mem_conn is None:
                    conn.close()

    def delete_publication(self, pub_id: str) -> bool:
        """Delete a publication by ID."""
        with self._lock:
            conn = self._get_write_conn()
            try:
                res = conn.execute(
                    "DELETE FROM publications WHERE id = ? RETURNING id;", [pub_id]
                ).fetchone()
                return res is not None
            finally:
                if self._mem_conn is None:
                    conn.close()


# Global default database instance configured via settings.DB_PATH
db = TaxDatabase(settings.DB_PATH)
