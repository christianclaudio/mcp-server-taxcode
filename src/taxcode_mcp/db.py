"""DuckDB database layer for statutory corpora and regulatory guidance."""

from __future__ import annotations

import logging
import threading
from typing import Any

import duckdb

from taxcode_mcp.config import settings

logger = logging.getLogger(__name__)

# Canonical initial seed data for US Federal and Puerto Rico tax provisions
INITIAL_STATUTES: list[dict[str, Any]] = [
    {
        "id": "us-irc-933",
        "jurisdiction": "US",
        "corpus": "IRC",
        "citation": "26 U.S.C. § 933",
        "section": "933",
        "title": "Income from sources within Puerto Rico",
        "text_es": (
            "En el caso de un individuo que sea residente de buena fe de Puerto Rico durante todo "
            "el año tributario, el ingreso bruto no incluirá ingresos derivados de fuentes dentro "
            "de Puerto Rico (excepto cantidades por servicios prestados a los Estados Unidos)."
        ),
        "text_en": (
            "In the case of an individual who is a bona fide resident of Puerto Rico during the "
            "entire taxable year, gross income does not include income derived from sources within "
            "Puerto Rico (except amounts received for services performed as an employee of the US)."
        ),
        "effective_year": 1954,
        "source_url": "https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title26-section933",
        "cross_references": ["26 U.S.C. § 937", "26 CFR § 1.937-1", "26 U.S.C. § 865"],
    },
    {
        "id": "us-irc-937",
        "jurisdiction": "US",
        "corpus": "IRC",
        "citation": "26 U.S.C. § 937",
        "section": "937",
        "title": "Residence and source rules involving possessions",
        "text_es": (
            "Establece los requisitos estatutarios para determinar residencia de buena fe en "
            "posesiones (presencia física, centro tributario 'tax home' y conexión más cercana), "
            "así como las reglas de fuente de ingresos aplicables a posesiones."
        ),
        "text_en": (
            "Rules for determining bona fide residency in possessions (presence test, tax home "
            "test, and closer connection test) and statutory principles for sourcing income."
        ),
        "effective_year": 2004,
        "source_url": "https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title26-section937",
        "cross_references": ["26 U.S.C. § 933", "26 CFR § 1.937-1", "26 CFR § 1.937-2"],
    },
    {
        "id": "us-irc-865",
        "jurisdiction": "US",
        "corpus": "IRC",
        "citation": "26 U.S.C. § 865",
        "section": "865",
        "title": "Source rules for personal property sales",
        "text_es": (
            "Salvo disposición expresa en contrario, el ingreso derivado de la venta de bienes "
            "personales (incluyendo valores negociables y acciones) se atribuirá a la residencia "
            "del vendedor."
        ),
        "text_en": (
            "Except as otherwise provided in this section, income from the sale of personal "
            "property (including corporate stock and marketable securities) is sourced to the "
            "residence of the seller."
        ),
        "effective_year": 1986,
        "source_url": "https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title26-section865",
        "cross_references": ["26 U.S.C. § 937", "26 CFR § 1.937-2"],
    },
    {
        "id": "us-cfr-1-937-1",
        "jurisdiction": "US",
        "corpus": "CFR",
        "citation": "26 CFR § 1.937-1",
        "section": "1.937-1",
        "title": "Bona fide residency in a possession",
        "text_es": (
            "Reglamentación del Tesoro que articula las cinco alternativas de presencia física "
            "(regla de 183 días, regla de 549 días en 3 años, límite de 90 días en EE.UU.), "
            "el requisito de no mantener tax home fuera, y los factores de conexión más cercana."
        ),
        "text_en": (
            "Treasury Regulation detailing the presence test alternatives (183-day rule, 549-day "
            "3-year rule, 90-day US presence limit), the tax home rule, and closer connection."
        ),
        "effective_year": 2006,
        "source_url": "https://www.ecfr.gov/current/title-26/section-1.937-1",
        "cross_references": ["26 U.S.C. § 937", "26 CFR § 1.937-2"],
    },
    {
        "id": "us-cfr-1-937-2",
        "jurisdiction": "US",
        "corpus": "CFR",
        "citation": "26 CFR § 1.937-2",
        "section": "1.937-2",
        "title": "Income from sources within a possession",
        "text_es": (
            "Reglas de fuente para posesiones y regla de lookback de 10 años para ganancias "
            "acumuladas (built-in gains) sobre bienes adquiridos antes de establecer residencia."
        ),
        "text_en": (
            "Sourcing rules for possessions and 10-year built-in gain lookback rules under "
            "Treas. Reg. § 1.937-2(f) for property held prior to establishing bona fide residency."
        ),
        "effective_year": 2006,
        "source_url": "https://www.ecfr.gov/current/title-26/section-1.937-2",
        "cross_references": ["26 U.S.C. § 937", "26 CFR § 1.937-1"],
    },
    {
        "id": "pr-cri-1010-01",
        "jurisdiction": "PR",
        "corpus": "CRI",
        "citation": "PR CRI Sec. 1010.01",
        "section": "1010.01",
        "title": "Definiciones generales / General Definitions",
        "text_es": (
            "Define términos fundamentales bajo el Código de Rentas Internas de Puerto Rico "
            "(Ley 1-2011), incluyendo individuo residente, corporación foránea y año contributivo."
        ),
        "text_en": (
            "Defines fundamental statutory terms under the PR Internal Revenue Code (Ley 1-2011), "
            "including resident individual, foreign corporation, taxable year, and person."
        ),
        "effective_year": 2011,
        "source_url": "https://hacienda.pr.gov/publicaciones/codigo-de-rentas-internas-de-puerto-rico-de-2011",
        "cross_references": ["PR CRI Sec. 1031.01", "PR CRI Sec. 1062.01"],
    },
    {
        "id": "pr-cri-1031-01",
        "jurisdiction": "PR",
        "corpus": "CRI",
        "citation": "PR CRI Sec. 1031.01",
        "section": "1031.01",
        "title": "Ingreso bruto / Gross Income",
        "text_es": (
            "Incluye ganancias, beneficios e ingresos derivados de sueldos, profesiones, "
            "comercio o ventas en propiedad, y excluye partidas estatutariamente exentas."
        ),
        "text_en": (
            "Includes gains, profits, and income derived from salaries, professions, commerce, "
            "or sales of property, while excluding statutory exclusions."
        ),
        "effective_year": 2011,
        "source_url": "https://hacienda.pr.gov/publicaciones/codigo-de-rentas-internas-de-puerto-rico-de-2011",
        "cross_references": ["PR CRI Sec. 1010.01", "PR CRI Sec. 1031.02"],
    },
    {
        "id": "pr-ley60-2021-01",
        "jurisdiction": "PR",
        "corpus": "LEY60",
        "citation": "Ley 60-2019 Sec. 2021.01",
        "section": "2021.01",
        "title": "Individuos Residentes Inversionistas - Cualificación",
        "text_es": (
            "Establece requisitos para acogerse al Capítulo 2 del Código de Incentivos, "
            "incluyendo no haber sido residente de PR en los 10 años anteriores y "
            "adquirir una residencia principal."
        ),
        "text_en": (
            "Establishes eligibility criteria for Individual Resident Investors under Act 60 "
            "Chapter 2 (formerly Act 22), including 10-year non-residency and home purchase."
        ),
        "effective_year": 2019,
        "source_url": "https://bvirtualogp.pr.gov/",
        "cross_references": ["Ley 60-2019 Sec. 2022.01", "26 U.S.C. § 933"],
    },
    {
        "id": "pr-ley60-2022-01",
        "jurisdiction": "PR",
        "corpus": "LEY60",
        "citation": "Ley 60-2019 Sec. 2022.01",
        "section": "2022.01",
        "title": "Exención Contributiva sobre Ganancias de Capital e Intereses",
        "text_es": (
            "Dispone exención total (100%) sobre ganancias de capital e intereses para decretos "
            "previos a 2027 (y tasa del 4% para decretos solicitados desde 2027)."
        ),
        "text_en": (
            "Provides 100% tax exemption on capital gains, dividends, and interest post-residency "
            "(4% capital gains rate for decrees requested on or after Jan 1, 2027)."
        ),
        "effective_year": 2019,
        "source_url": "https://bvirtualogp.pr.gov/",
        "cross_references": ["Ley 60-2019 Sec. 2021.01", "26 CFR § 1.937-2"],
    },
    {
        "id": "pr-ley60-2031-01",
        "jurisdiction": "PR",
        "corpus": "LEY60",
        "citation": "Ley 60-2019 Sec. 2031.01",
        "section": "2031.01",
        "title": "Exportación de Servicios y Comercio - Cualificación",
        "text_es": (
            "Cualifica negocios dedicados a la exportación de servicios elegibles prestados "
            "desde Puerto Rico hacia mercados exteriores sin nexo previo en la isla (ex-Ley 20)."
        ),
        "text_en": (
            "Qualifies businesses engaged in export services conducted from Puerto Rico to "
            "foreign or mainland markets with no prior nexus in the island (formerly Act 20)."
        ),
        "effective_year": 2019,
        "source_url": "https://bvirtualogp.pr.gov/",
        "cross_references": ["Ley 60-2019 Sec. 2032.01", "26 U.S.C. § 957"],
    },
    {
        "id": "pr-ley60-2032-01",
        "jurisdiction": "PR",
        "corpus": "LEY60",
        "citation": "Ley 60-2019 Sec. 2032.01",
        "section": "2032.01",
        "title": "Tasa Fija Preferencial de 4% sobre Ingreso de Desarrollo",
        "text_es": (
            "Fija una tasa del 4% sobre el ingreso neto de desarrollo para exportación, "
            "100% de exención sobre dividendos, y 75% de exención en contribución "
            "sobre la propiedad."
        ),
        "text_en": (
            "Establishes a 4% corporate rate on export net income, 100% exemption on dividends, "
            "and 75% exemption on property taxes."
        ),
        "effective_year": 2019,
        "source_url": "https://bvirtualogp.pr.gov/",
        "cross_references": ["Ley 60-2019 Sec. 2031.01", "PR CRI Sec. 1010.01"],
    },
]

INITIAL_PUBLICATIONS: list[dict[str, Any]] = [
    {
        "id": "pr-cc-ri-26-13",
        "category": "carta-circular",
        "doc_number": "CC RI 26-13",
        "doc_date": "2026-06-30",
        "title": "Radicación electrónica obligatoria de planillas de individuos",
        "summary": (
            "Instrucciones y requisitos técnicos para la radicación compulsoria a través de SURI "
            "de las planillas de contribución sobre ingresos de individuos."
        ),
        "source_url": "https://hacienda.pr.gov/publicaciones/carta-circular-de-rentas-internas-num-26-13",
    },
    {
        "id": "pr-da-23-04",
        "category": "determinacion-administrativa",
        "doc_number": "DA 23-04",
        "doc_date": "2023-08-15",
        "title": "Tratamiento contributivo de transferencias de propiedad bajo Ley 60",
        "summary": (
            "Determinación administrativa sobre exenciones contributivas en la enajenación de "
            "activos de capital por parte de inversionistas residentes con decreto bajo Ley 60."
        ),
        "source_url": "https://hacienda.pr.gov/publicaciones/determinacion-administrativa-num-23-04",
    },
    {
        "id": "pr-da-21-04",
        "category": "determinacion-administrativa",
        "doc_number": "DA 21-04",
        "doc_date": "2021-04-12",
        "title": "Procedimiento de cumplimiento anual para decretos de individuos",
        "summary": (
            "Directrices para la presentación de informes anuales de cumplimiento y comprobantes "
            "de donaciones benéficas requeridas por el Código de Incentivos de Puerto Rico."
        ),
        "source_url": "https://hacienda.pr.gov/publicaciones/determinacion-administrativa-num-21-04",
    },
]


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
                        citation VARCHAR UNIQUE,
                        section VARCHAR,
                        title VARCHAR,
                        text_es VARCHAR,
                        text_en VARCHAR,
                        effective_year INTEGER,
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

                # Seed initial statutes if table is empty
                statute_count = conn.execute("SELECT COUNT(*) FROM statutes;").fetchone()
                if statute_count and statute_count[0] == 0:
                    for s in INITIAL_STATUTES:
                        conn.execute(
                            """
                            INSERT INTO statutes (
                                id, jurisdiction, corpus, citation, section,
                                title, text_es, text_en, effective_year,
                                source_url, cross_references
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
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
                                s["source_url"],
                                s["cross_references"],
                            ],
                        )

                # Seed initial publications if table is empty
                pub_count = conn.execute("SELECT COUNT(*) FROM publications;").fetchone()
                if pub_count and pub_count[0] == 0:
                    for p in INITIAL_PUBLICATIONS:
                        conn.execute(
                            """
                            INSERT INTO publications (
                                id, category, doc_number, doc_date, title, summary, source_url
                            ) VALUES (?, ?, ?, ?, ?, ?, ?);
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
        lang: str = "en",
    ) -> dict[str, Any] | None:
        """Find a statute by exact citation or section code."""
        query_val = citation_or_section.strip()
        with self._lock:
            conn = self._get_read_conn()
            try:
                sql = """
                    SELECT id, jurisdiction, corpus, citation, section, title,
                           text_es, text_en, effective_year, source_url, cross_references
                    FROM statutes
                    WHERE (
                        LOWER(citation) = LOWER(?)
                        OR LOWER(section) = LOWER(?)
                        OR LOWER(REPLACE(citation, ' ', '')) = LOWER(REPLACE(?, ' ', ''))
                    )
                """
                params: list[Any] = [query_val, query_val, query_val]
                if jurisdiction:
                    sql += " AND UPPER(jurisdiction) = UPPER(?)"
                    params.append(jurisdiction.strip())

                sql += " LIMIT 1;"
                result = conn.execute(sql, params).fetchone()
                if not result:
                    return None

                text = result[7] if lang.lower() == "en" else result[6]
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
                    "source_url": result[9],
                    "cross_references": result[10] or [],
                    "language": lang.lower(),
                    "official_language": "es" if result[1] == "PR" else "en",
                }
            finally:
                if self._mem_conn is None:
                    conn.close()

    def search_statutes(
        self,
        query: str,
        jurisdiction: str | None = None,
        corpus: str | None = None,
        lang: str = "en",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Search statutes across title, citation, and multilingual operative text."""
        q = f"%{query.strip().lower()}%"
        with self._lock:
            conn = self._get_read_conn()
            try:
                sql = """
                    SELECT id, jurisdiction, corpus, citation, section, title,
                           text_es, text_en, effective_year, source_url, cross_references
                    FROM statutes
                    WHERE (
                        LOWER(title) LIKE ?
                        OR LOWER(citation) LIKE ?
                        OR LOWER(text_en) LIKE ?
                        OR LOWER(text_es) LIKE ?
                    )
                """
                params: list[Any] = [q, q, q, q]
                if jurisdiction:
                    sql += " AND UPPER(jurisdiction) = UPPER(?)"
                    params.append(jurisdiction.strip())
                if corpus:
                    sql += " AND UPPER(corpus) = UPPER(?)"
                    params.append(corpus.strip())

                sql += " ORDER BY id LIMIT ?;"
                params.append(limit)

                rows = conn.execute(sql, params).fetchall()
                output: list[dict[str, Any]] = []
                for r in rows:
                    text = r[7] if lang.lower() == "en" else r[6]
                    output.append(
                        {
                            "id": r[0],
                            "jurisdiction": r[1],
                            "corpus": r[2],
                            "citation": r[3],
                            "section": r[4],
                            "title": r[5],
                            "operative_text": text,
                            "effective_year": r[8],
                            "source_url": r[9],
                            "cross_references": r[10] or [],
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
    ) -> list[dict[str, Any]]:
        """List administrative circulars and determinations."""
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
                    sql += " AND LOWER(category) = LOWER(?)"
                    params.append(category.strip())
                if since_date:
                    sql += " AND doc_date >= ?"
                    params.append(since_date.strip())

                sql += " ORDER BY doc_date DESC LIMIT ?;"
                params.append(limit)

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
                        source_url, cross_references
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
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
                        source_url, cross_references
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO UPDATE SET
                        jurisdiction = EXCLUDED.jurisdiction,
                        corpus = EXCLUDED.corpus,
                        citation = EXCLUDED.citation,
                        section = EXCLUDED.section,
                        title = EXCLUDED.title,
                        text_es = EXCLUDED.text_es,
                        text_en = EXCLUDED.text_en,
                        effective_year = EXCLUDED.effective_year,
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
