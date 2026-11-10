"""Statute and publication rows, loaded from the versioned data files.

The data files under ``taxcode_mcp/data`` are the single source of truth for every
row the server serves and every generated docs page:

- ``jurisdictions.json``: the jurisdiction codes in catalog order. Rows load in this
  order, then in file order, so the database and the docs keep a stable order.
- ``statutes/<code>.json``: one jurisdiction's statute rows. ``text_en`` and
  ``text_es`` are stored as lists of lines (joined with ``"\\n"`` on load), so a
  change to one line of a long section shows as a one-line diff.
- ``publications/<code>.json``: publications whose id starts with ``<code>-``.
- ``manifest/<code>.json``: one source record per statute and publication row and
  per rate table of that jurisdiction (see :data:`MANIFEST_FIELDS`).
- ``rates/<code>.json``: the rate table the compliance tools compute that state's tax
  from (deductions, brackets, rates, per-year values). :data:`RATE_TABLE_MODELS` names
  every state that has one and the shape of its ``params`` (see :func:`load_rate_tables`).
- ``federal/standard_deduction.json``: the federal standard deduction by tax year, which a
  rate table may name as its allowance (see :func:`load_federal_standard_deduction`).

Every file is written in one canonical form (:func:`dump_json`), and
``scripts/build_catalog.py --check`` fails when a file (rate tables included) is
malformed, missing or not canonical, or a generated docs page or a manifest entry is
out of step.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"
SCHEMA_VERSION = 1
NOT_RECORDED = "not_recorded"
# Optional list on a rate table's ``params`` and on its manifest entry. The key is the
# unknown-value token; the value is a list of ``{"param", "tax_year", "reason"}`` objects,
# one per year the lookup must not carry forward.
UNSOURCED_YEARS = NOT_RECORDED
UNSOURCED_FIELDS: tuple[str, ...] = ("param", "tax_year", "reason")

# Field order is the order written to the data files.
STATUTE_FIELDS: tuple[str, ...] = (
    "id",
    "jurisdiction",
    "corpus",
    "citation",
    "section",
    "title",
    "effective_year",
    "superseded_year",
    "source_url",
    "cross_references",
    "breadcrumbs",
    "source_note",
    "text_en",
    "text_es",
)
# Optional last field of a statute row: its official French text, as lines. Rows of a
# jurisdiction in FRENCH_TEXT_JURISDICTIONS must have it, non-empty: Canada enacts its federal
# law in English and French, and both versions are equally authoritative. Elsewhere it is
# optional and, when absent, loads as an empty text.
TEXT_FR = "text_fr"
FRENCH_TEXT_JURISDICTIONS: frozenset[str] = frozenset({"CAN"})
# The columns stored in the ``statutes`` table; ``breadcrumbs`` and ``source_note``
# appear only in the generated docs.
STATUTE_DB_FIELDS: tuple[str, ...] = (
    "id",
    "jurisdiction",
    "corpus",
    "citation",
    "section",
    "title",
    "text_es",
    "text_en",
    "effective_year",
    "superseded_year",
    "source_url",
    "cross_references",
)
PUBLICATION_FIELDS: tuple[str, ...] = (
    "id",
    "category",
    "doc_number",
    "doc_date",
    "title",
    "summary",
    "source_url",
)
MANIFEST_FIELDS: tuple[str, ...] = (
    "kind",
    "id",
    "url",
    "edition",
    "official",
    "licence",
    "retrieved",
    "content_sha256",
    "enacting_law",
    "effective_date",
    "verification",
)
MANIFEST_KINDS = frozenset({"statute", "publication", "rate_table", "federal_table"})
# Kinds whose entries describe a data table and so may carry ``year_sources``.
TABLE_KINDS = ("rate_table", "federal_table")
# Optional last field of a table entry (a kind in TABLE_KINDS): the sources of the figures
# for other tax years or corroborating second sources on ``url``'s year. Each names the
# table values it confirms by their path in the rate table's ``params`` (or in the federal
# table's document).
YEAR_SOURCES = "year_sources"
# Optional field of a ``rate_table`` whose ``verification`` is still ``not_recorded``:
# why that table could not be confirmed. It sits after ``verification`` and before
# ``year_sources``.
VERIFICATION_NOTE = "verification_note"
# Optional field of a ``statute`` entry, after ``verification``: the file its row's ``text_fr``
# was read from (``url`` and ``content_sha256``). Every row with a French text needs one.
FRENCH_SOURCE = "french_source"
FRENCH_SOURCE_FIELDS: tuple[str, ...] = ("url", "content_sha256")
YEAR_SOURCE_FIELDS: tuple[str, ...] = (
    "tax_year",
    "values",
    "url",
    "official",
    "retrieved",
    "content_sha256",
)
_PARAM_PATH_RE = re.compile(r"([a-z][a-z0-9_]*)((?:\[\d+\])*)")
VERIFICATION_STATUSES = frozenset({"verified_verbatim", "summary", NOT_RECORDED})
# Which text the source at ``url`` is: the law as enacted, or a compilation with its
# later amendments folded in (such as an OGP "según enmendada" text).
EDITIONS = frozenset({"as_enacted", "amended_compilation", NOT_RECORDED})

_STR_FIELDS = frozenset(
    {"id", "jurisdiction", "corpus", "citation", "section", "title", "source_url"}
    | {"breadcrumbs", "source_note", "category", "doc_number", "doc_date", "summary"}
)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


# State -> shape of its rate table's ``params``. Every state listed needs a
# ``rates/<code>.json`` file; the compliance tools read nothing else for these numbers.
RATE_TABLE_MODELS: dict[str, str] = {
    "GA": "georgia",
    "NY": "brackets_marginal",
    "CA": "brackets_marginal",
    "WA": "capital_gains_excise",
    "MA": "flat_with_surtax",
    "NJ": "brackets_marginal",
    "IL": "flat",
    "PA": "flat_no_deduction",
    "CT": "connecticut",
    "NC": "flat_by_year",
    "VA": "brackets_cumulative",
    "MD": "maryland",
    "CO": "flat_by_year",
    "AZ": "flat",
    "OH": "brackets_cumulative",
    **dict.fromkeys(
        "AK AL AR DC DE HI ID IN IA KS KY LA ME MI MN MS MO MT NE NV NH NM ND OK OR RI SC "
        "SD TN UT VT WV WI WY".split(),
        "registry",
    ),
    "CAN": "canada_federal",
}
# Rate tables of a country, not a US state: evaluate_state_residency answers them with their
# own model (Canada: federal Part I tax and the departure tax on emigration).
COUNTRY_RATE_TABLES: frozenset[str] = frozenset({"CAN"})
RATE_TABLE_FIELDS: tuple[str, ...] = ("schema", "jurisdiction", "model", "source_url", "params")
# Field order of ``params`` per model. ``allowance`` is the filing-status amount taken off
# income first, stored under its statutory name: ``deduction`` or ``exemption``.
RATE_MODEL_PARAMS: dict[str, tuple[str, ...]] = {
    "georgia": ("standard_deduction", "retirement_exclusion", "rates", "brackets", "exemption"),
    "brackets_marginal": ("allowance", "brackets"),
    "brackets_cumulative": ("allowance", "brackets"),
    "maryland": ("standard_deduction", "personal_exemption", "brackets"),
    "connecticut": (
        "exemption",
        "brackets",
        "phase_out_addback",
        "tax_recapture",
        "personal_tax_credit",
    ),
    "flat": ("allowance", "rate"),
    "flat_no_deduction": ("rate",),
    "flat_by_year": ("allowance", "rates"),
    "flat_with_surtax": ("allowance", "rate", "surtax"),
    "capital_gains_excise": ("capital_gains_deduction", "rate"),
    "canada_federal": (
        "years",
        "capital_gains_inclusion_rate",
        "sojourner_days",
        "t1161_threshold",
        "personal_use_reporting_threshold",
        "personal_use_minimum",
        "short_term_resident_months",
    ),
}
# One ``canada_federal`` tax year: its brackets (``over`` descending, ``null`` last, the
# lowest rate), the basic personal amount (the maximum, reduced to the minimum as income
# rises from ``phaseout_start`` to ``phaseout_end``, ITA s. 118(1.1)) and the credit rate.
CANADA_YEAR_FIELDS: tuple[str, ...] = (
    "tax_year",
    "brackets",
    "basic_personal_amount",
    "credit_rate",
)
CANADA_BPA_FIELDS: tuple[str, ...] = ("maximum", "minimum", "phaseout_start", "phaseout_end")
# Model params that may also carry later-year values as ``<name>_<YYYY>`` keys after the
# model's own fields, years ascending. Each holds the value from that tax year on, and the
# unsuffixed key covers the years before (the registry's convention).
YEARLY_PARAMS: dict[str, tuple[str, ...]] = {
    "georgia": ("standard_deduction", "retirement_exclusion", "brackets", "dependent_exemption"),
    "flat_with_surtax": ("surtax",),
    "capital_gains_excise": ("capital_gains_deduction", "tier"),
    "brackets_cumulative": ("brackets", "deduction", "exemption"),
    "brackets_marginal": ("brackets", "deduction", "exemption"),
    "maryland": ("standard_deduction", "personal_exemption", "brackets", "capital_gains_surtax"),
    # Arizona's flat tax starts in 2023. Earlier years are not that one rate; ``rate_YYYY``
    # and ``deduction_YYYY`` hold the year the flat figures begin, and a ``not_recorded``
    # marker covers a year the flat table does not have.
    "flat": ("deduction", "rate"),
}
# ``tier`` has no unsuffixed value (an unsuffixed ``tier`` is an unknown key): before its
# first ``_YYYY`` key it doesn't apply. It is an additional rate on the taxable amount over
# ``over`` (Washington's 2.9% on capital gains over $1,000,000 from 2025, RCW 82.87.040(1)(b)).
# ``capital_gains_surtax`` is likewise yearly-only (Maryland's 2% net-capital-gain surtax for
# FAGI over $350,000 in tax years 2025-2028).

# A flat, flat_by_year, or registry deduction / credit_base may name the federal standard
# deduction instead of holding amounts: the figure is then that tax year's federal standard
# deduction (Colorado; Arizona and the registry states that subtract it; Utah's taxpayer
# tax credit base).
FEDERAL_ALLOWANCE = "federal_standard_deduction"
_YEARLY_KEY_RE = re.compile(r"^(?P<name>[a-z_]+?)_(?P<year>\d{4})$")


def series_param(name: str) -> str:
    """The marker ``param`` for a ``_for_year`` series name.

    ``deduction_single`` and ``credit_phaseout_mfj`` name the base (``deduction``,
    ``credit_phaseout``). A name with no filing-status suffix is itself. The suffix
    list is :data:`FILING_STATUSES`.
    """
    for status in FILING_STATUSES:
        suffix = f"_{status}"
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


# The text each per-year step carries: ``flat_by_year`` its whole ``rate_desc``; ``georgia``
# the ``law`` that set that year's rate, which the rate prose cites.
YEAR_STEP_TEXT: dict[str, str] = {"flat_by_year": "rate_desc", "georgia": "law"}
ALLOWANCE_NAMES = ("deduction", "exemption")
FILING_STATUSES = ("single", "mfj")
REGIMES = frozenset({"none", "flat", "graduated"})
# Georgia's graduated schedules start in 2018. A year before that reads the 2024 flat-rate
# standard deduction, and the revenue-test note for a year after 2026 reads the 2026
# deduction and rate step. ``compliance.py`` imports these names. A ``not_recorded`` marker
# on a year in :data:`MARKER_ANCHORS`, or on Georgia ``standard_deduction`` before
# :data:`GA_FIRST_GRADUATED_YEAR`, is rejected at load.
GA_FIRST_GRADUATED_YEAR = 2018
GA_FLAT_RATE_FIRST_YEAR = 2024
GA_LAST_FIXED_YEAR = 2026
MARKER_ANCHORS: tuple[tuple[str, str, int], ...] = (
    ("GA", "standard_deduction", GA_LAST_FIXED_YEAR),
    ("GA", "rates", GA_LAST_FIXED_YEAR),
    ("GA", "standard_deduction", GA_FLAT_RATE_FIRST_YEAR),
)
# The years a registry table's ``brackets_YYYY`` and ``rate_desc_YYYY`` keys may name.
# 2019-2023 are the earlier schedules a table holds when they differ from 2024.
# 2018 is a bracket year too (Mississippi's pre-2019 schedule). ``rate_desc`` already
# allowed 2018.
REGISTRY_YEARS = (2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026, 2027, 2028, 2029, 2030)
REGISTRY_BRACKET_YEARS = (2018, *REGISTRY_YEARS)
# Registry tables keep the original key names. A ``_YYYY`` suffix is the value from that
# tax year on; the unsuffixed key covers the years before.
_REGISTRY_REQUIRED = ("statutory_basis", "rate_authority", "rate_desc", "regime")
_REGISTRY_TYPES: dict[str, type] = {
    "statutory_basis": str,
    "rate_authority": str,
    "rate_desc": str,
    "regime": str,
    "regime_2025": str,
    "day_threshold": int,
    "requires_abode": bool,
    "inclusive_threshold": bool,
    # Deduction and exemption ``_YYYY`` keys. The unsuffixed key, when present, covers
    # the years before the first suffix. Kansas holds one of each for 2018-2024.
    **dict.fromkeys(
        (
            f"deduction_{status}{year}"
            for status in FILING_STATUSES
            for year in (
                "",
                "_2018",
                "_2019",
                "_2020",
                "_2021",
                "_2022",
                "_2023",
                "_2024",
                "_2025",
                "_2026",
            )
        ),
        float,
    ),
    **dict.fromkeys(
        (
            f"{name}_{status}{year}"
            for name in ("top_bracket", "zero_bracket")
            for status in FILING_STATUSES
            for year in ("", "_2025")
        ),
        float,
    ),
    **dict.fromkeys(
        (
            f"exemption_{status}{year}"
            for status in FILING_STATUSES
            for year in (
                "",
                "_2018",
                "_2019",
                "_2020",
                "_2021",
                "_2022",
                "_2023",
                "_2024",
                "_2025",
                "_2026",
            )
        ),
        float,
    ),
    **dict.fromkeys(
        (
            f"dependent_exemption{year}"
            for year in (
                "",
                "_2018",
                "_2019",
                "_2020",
                "_2021",
                "_2022",
                "_2023",
                "_2024",
                "_2025",
                "_2026",
            )
        ),
        float,
    ),
    **dict.fromkeys(
        (
            "top_rate",
            "middle_rate",
            "rate",
            # Kentucky's flat rate by year (KRS 141.020): 5% from 2018, 4.5% for 2023,
            # 4% from 2024, 3.5% from 2026. ``rate_2025`` and ``rate_2027`` are the
            # other flat-rate years a registry table may name.
            "rate_2018",
            "rate_2023",
            "rate_2024",
            "rate_2025",
            "rate_2026",
            "rate_2027",
        ),
        float,
    ),
    **dict.fromkeys(
        (
            "credit_base_single",
            "credit_base_mfj",
            "credit_rate",
            "credit_phaseout_single",
            "credit_phaseout_mfj",
            "credit_phaseout_rate",
        ),
        float,
    ),
    "brackets": dict,
    # Later-year values of the bracket schedule, its prose and the credit's base and
    # phase-out amounts. Each needs its unsuffixed key, placed after it, years ascending.
    # Kansas low-income exclusion (K.S.A. 79-32,110(e)): a filing-status taxable-income
    # ceiling at or below which the tax is zero. A later year's null ends it. Above the
    # ceiling the ordinary brackets apply to all taxable income.
    **dict.fromkeys((f"low_income_exclusion_{year}" for year in range(2018, 2027)), dict),
    **dict.fromkeys((f"brackets_{year}" for year in REGISTRY_BRACKET_YEARS), dict),
    **dict.fromkeys((f"rate_desc_{year}" for year in (2018, *REGISTRY_YEARS)), str),
    **dict.fromkeys(
        (
            f"credit_{name}_{status}_2025"
            for name in ("base", "phaseout")
            for status in FILING_STATUSES
        ),
        float,
    ),
}
# Registry params whose ``_YYYY`` keys need their unsuffixed key first, years ascending.
_REGISTRY_YEARLY_BASES = (
    "brackets",
    "rate",
    "rate_desc",
    *(f"credit_{name}_{status}" for name in ("base", "phaseout") for status in FILING_STATUSES),
)
# How a graduated registry table taxes income below its top bracket. ``brackets`` is the
# state's official marginal schedule; ``middle_rate`` is one statutory rate. Exactly one
# must be present.
_REGISTRY_GRADUATED = ("brackets", "middle_rate")
# A nonrefundable credit taken off the computed tax (Utah's taxpayer tax credit):
# ``credit_rate`` times the filing status's ``credit_base``, less ``credit_phaseout_rate``
# times the taxable income over its ``credit_phaseout`` amount, never below zero. All or none.
_REGISTRY_CREDIT_KEYS = tuple(
    name
    for name in _REGISTRY_TYPES
    if name.startswith("credit_") and not _YEARLY_KEY_RE.match(name)
)
# Keys of the top/zero/middle formula, which a table with ``brackets`` does not use.
_REGISTRY_FORMULA_KEYS = frozenset(
    name for name in _REGISTRY_TYPES if name.startswith(("top_", "zero_", "middle_"))
)


class CatalogError(ValueError):
    """A data file breaks the catalog schema."""


@dataclass
class Catalog:
    """Rows loaded from the data files, in catalog order."""

    jurisdictions: list[str]
    statutes: list[dict[str, Any]] = field(default_factory=list)
    publications: list[dict[str, Any]] = field(default_factory=list)
    manifest: list[dict[str, Any]] = field(default_factory=list)
    # State code -> whole ``rates/<code>.json`` document (loaded with the manifest).
    rate_tables: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Manifest id -> whole federal table document (loaded with the manifest).
    federal_tables: dict[str, dict[str, Any]] = field(default_factory=dict)
    # File stem -> parsed JSON document, for the canonical-form check.
    documents: dict[str, Any] = field(default_factory=dict)


def file_stem(code: str) -> str:
    """File name stem for a jurisdiction code (``"GA"`` -> ``"ga"``)."""
    return code.lower()


def dump_json(document: Any) -> str:
    """Canonical text of a data file: 2-space indent, UTF-8 kept, trailing newline."""
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def split_lines(text: str) -> list[str]:
    """Store a text as its lines; ``"\\n".join`` gives back the exact text."""
    return text.split("\n")


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise CatalogError(message)


def _check_keys(row: Mapping[str, Any], fields: Sequence[str], where: str) -> None:
    keys = list(row)
    _check(keys == list(fields), f"{where}: fields must be exactly {list(fields)}, got {keys}")


def _check_types(row: Mapping[str, Any], where: str) -> None:
    for name, value in row.items():
        if name in _STR_FIELDS:
            _check(isinstance(value, str), f"{where}: {name} must be a string")
        elif name == "effective_year":
            _check(type(value) is int, f"{where}: effective_year must be an integer")
        elif name == "superseded_year":
            _check(
                value is None or type(value) is int,
                f"{where}: superseded_year must be an integer or null",
            )
        else:  # cross_references, text_en, text_es
            _check(
                isinstance(value, list) and all(isinstance(v, str) for v in value),
                f"{where}: {name} must be a list of strings",
            )


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CatalogError(f"{path.name}: invalid JSON ({exc})") from None


def _document(path: Path, code: str, key: str) -> tuple[Any, list[Any]]:
    doc = _read(path)
    where = f"{path.parent.name}/{path.name}"
    _check(isinstance(doc, dict), f"{where}: top level must be an object")
    _check_keys(doc, ("schema", "jurisdiction", key), where)
    _check(doc["schema"] == SCHEMA_VERSION, f"{where}: schema must be {SCHEMA_VERSION}")
    _check(doc["jurisdiction"] == code, f"{where}: jurisdiction must be {code!r}")
    rows = doc[key]
    _check(isinstance(rows, list), f"{where}: {key} must be a list")
    return doc, rows


def _stray_files(root: Path, kind: str, stems: set[str]) -> list[str]:
    folder = root / kind
    if not folder.is_dir():
        return []
    return sorted(
        f"{kind}/{p.name}"
        for p in folder.iterdir()
        if not (p.suffix == ".json" and p.stem in stems)
    )


def load_catalog(root: Path = DATA_DIR, *, with_manifest: bool = False) -> Catalog:
    """Load and validate every row under ``root``; raise :class:`CatalogError` on a bad file.

    ``with_manifest`` also loads the manifest entries (schema only; see
    :func:`manifest_errors` for the cross-check against the rows) and the rate tables
    they describe.
    """
    codes = _read(root / "jurisdictions.json")
    _check(
        isinstance(codes, list) and all(isinstance(c, str) and c for c in codes),
        "jurisdictions.json: must be a list of jurisdiction codes",
    )
    _check(len(set(codes)) == len(codes), "jurisdictions.json: duplicate code")
    catalog = Catalog(jurisdictions=list(codes))
    stems = {file_stem(c) for c in codes}
    kinds = ("statutes", "publications", *(("manifest",) if with_manifest else ()))
    stray = [f for kind in kinds for f in _stray_files(root, kind, stems)]
    _check(not stray, f"files for no listed jurisdiction: {stray}")
    statute_ids: set[str] = set()
    pub_ids: set[str] = set()
    doc_numbers: set[str] = set()
    for code in codes:
        stem = file_stem(code)
        path = root / "statutes" / f"{stem}.json"
        if path.is_file():
            catalog.documents[f"statutes/{stem}"], rows = _document(path, code, "statutes")
            for row in rows:
                where = f"statutes/{stem}.json row {row.get('id', '?')!r}"
                french = (TEXT_FR,) if TEXT_FR in row else ()
                _check_keys(row, (*STATUTE_FIELDS, *french), where)
                _check_types(row, where)
                _check(row["jurisdiction"] == code, f"{where}: jurisdiction must be {code!r}")
                if code in FRENCH_TEXT_JURISDICTIONS:
                    _check(
                        any(line.strip() for line in row.get(TEXT_FR, [])),
                        f"{where}: text_fr (the official French text) is required",
                    )
                _check(row["id"] not in statute_ids, f"{where}: duplicate statute id")
                statute_ids.add(row["id"])
                loaded = dict(row)
                loaded["text_en"] = "\n".join(row["text_en"])
                loaded["text_es"] = "\n".join(row["text_es"])
                loaded[TEXT_FR] = "\n".join(row.get(TEXT_FR, []))
                catalog.statutes.append(loaded)
        path = root / "publications" / f"{stem}.json"
        if path.is_file():
            catalog.documents[f"publications/{stem}"], rows = _document(path, code, "publications")
            for row in rows:
                where = f"publications/{stem}.json row {row.get('id', '?')!r}"
                _check_keys(row, PUBLICATION_FIELDS, where)
                _check_types(row, where)
                _check(row["id"].startswith(f"{stem}-"), f"{where}: id must start with {stem}-")
                _check(row["id"] not in pub_ids, f"{where}: duplicate publication id")
                _check(row["doc_number"] not in doc_numbers, f"{where}: duplicate doc_number")
                pub_ids.add(row["id"])
                doc_numbers.add(row["doc_number"])
                catalog.publications.append(dict(row))
        path = root / "manifest" / f"{stem}.json"
        if with_manifest and path.is_file():
            catalog.documents[f"manifest/{stem}"], entries = _document(path, code, "entries")
            for entry in entries:
                where = f"manifest/{stem}.json entry {entry.get('id', '?')!r}"
                fields = (
                    *MANIFEST_FIELDS,
                    *((FRENCH_SOURCE,) if FRENCH_SOURCE in entry else ()),
                    *((VERIFICATION_NOTE,) if VERIFICATION_NOTE in entry else ()),
                    *((YEAR_SOURCES,) if YEAR_SOURCES in entry else ()),
                    *((UNSOURCED_YEARS,) if UNSOURCED_YEARS in entry else ()),
                )
                _check_keys(entry, fields, where)
                catalog.manifest.append({**entry, "_jurisdiction": code})
    if with_manifest:
        stray = _stray_files(root, "rates", {file_stem(c) for c in RATE_TABLE_MODELS})
        _check(not stray, f"files for no rate table: {stray}")
        for code in RATE_TABLE_MODELS:
            _check(code in codes, f"jurisdictions.json: rate table state {code!r} is missing")
            stem = file_stem(code)
            doc = rate_table_document(root / "rates" / f"{stem}.json", code)
            catalog.rate_tables[code] = catalog.documents[f"rates/{stem}"] = doc
        federal = federal_table_document(root)
        catalog.federal_tables[FEDERAL_TABLE_ID] = federal
        catalog.documents[FEDERAL_STANDARD_DEDUCTION] = federal
    problems = window_span_errors(catalog.statutes)
    _check(not problems, "statute versions: " + "; ".join(problems))
    return catalog


def rate_table_id(code: str) -> str:
    """Manifest ``id`` of a state's rate table (``"GA"`` -> ``"ga-rates"``)."""
    return f"{file_stem(code)}-rates"


def _is_amount(value: Any) -> bool:
    """A non-negative finite float (an int would print differently in tool output)."""
    return type(value) is float and 0.0 <= value < float("inf")


def _is_rate(value: Any) -> bool:
    return type(value) is float and 0.0 <= value < 1.0


def _check_amount(value: Any, where: str) -> None:
    _check(_is_amount(value), f"{where}: must be a non-negative float such as 1000.0")


def _check_rate(value: Any, where: str) -> None:
    _check(_is_rate(value), f"{where}: must be a float rate from 0.0 up to 1.0")


def _check_by_status(value: Any, where: str) -> None:
    _check(isinstance(value, dict), f"{where}: must be an object")
    _check_keys(value, FILING_STATUSES, where)
    for status in FILING_STATUSES:
        _check_amount(value[status], f"{where}.{status}")


def _check_steps(
    steps: Any,
    fields: Sequence[str],
    key: str,
    where: str,
    check_row: Callable[[Mapping[str, Any], str], None],
) -> None:
    """Steps tried in order: ``key`` strictly decreasing, and ``null`` (the rest) last.

    ``key`` may be ``null`` only on the last step, and must be there when ``key`` is a
    threshold (``over``/``from_year``) so every input meets exactly one step.
    """
    _check(isinstance(steps, list) and len(steps) > 0, f"{where}: must be a non-empty list")
    previous: Any = None
    for i, step in enumerate(steps):
        at = f"{where}[{i}]"
        _check(isinstance(step, dict), f"{at}: must be an object")
        _check_keys(step, fields, at)
        check_row(step, at)
        bound = step[key]
        order = f"{at}: steps out of order; sort by {key} from highest to lowest, null last"
        if bound is None:
            _check(i == len(steps) - 1, order)
        else:
            _check(previous is None or bound < previous, order)
            _check(i < len(steps) - 1, f"{at}: the last step's {key} must be null")
            previous = bound


def _check_zero_liability(value: Any, where: str) -> None:
    """A zero-tax ceiling by filing status, or null when that year's exclusion has ended."""
    if value is None:
        return
    _check(isinstance(value, dict), f"{where}: must be an object or null")
    _check_keys(value, FILING_STATUSES, where)
    for status in FILING_STATUSES:
        _check_amount(value[status], f"{where}.{status}")


def _check_brackets(value: Any, fields: Sequence[str], where: str) -> None:
    _check(isinstance(value, dict), f"{where}: must be an object")
    _check_keys(value, FILING_STATUSES, where)

    def row(step: Mapping[str, Any], at: str) -> None:
        if step["over"] is not None:
            _check_amount(step["over"], f"{at}.over")
        _check_rate(step["rate"], f"{at}.rate")
        if "base" in step:
            _check_amount(step["base"], f"{at}.base")

    for status in FILING_STATUSES:
        _check_steps(value[status], fields, "over", f"{where}.{status}", row)


def _check_year_steps(value: Any, fields: Sequence[str], where: str) -> None:
    def row(step: Mapping[str, Any], at: str) -> None:
        if step["from_year"] is not None:
            _check(type(step["from_year"]) is int, f"{at}.from_year: must be an integer")
        _check_rate(step["rate"], f"{at}.rate")
        for text in ("rate_desc", "law"):
            if text in step:
                _check(
                    isinstance(step[text], str) and bool(step[text]),
                    f"{at}.{text}: must be a non-empty string",
                )

    _check_steps(value, fields, "from_year", where, row)


def _check_registry(params: Mapping[str, Any], where: str) -> None:
    unknown = sorted(set(params) - set(_REGISTRY_TYPES))
    _check(not unknown, f"{where}: unknown keys {unknown}")
    missing = [k for k in _REGISTRY_REQUIRED if k not in params]
    _check(not missing, f"{where}: missing keys {missing}")
    for name, value in params.items():
        if name.startswith("low_income_exclusion_"):
            _check_zero_liability(value, f"{where}.{name}")
            continue
        expected = _REGISTRY_TYPES[name]
        if value == FEDERAL_ALLOWANCE and (
            name.startswith("deduction_") or name.startswith("credit_base_")
        ):
            continue  # that tax year's federal standard deduction
        _check(type(value) is expected, f"{where}.{name}: must be {expected.__name__}")
        if name.startswith("brackets"):
            _check_brackets(value, ("over", "rate"), f"{where}.{name}")
        elif expected is float:
            check = _check_rate if "rate" in name else _check_amount
            check(value, f"{where}.{name}")
        elif name.startswith("regime"):
            _check(value in REGIMES, f"{where}.{name}: must be one of {sorted(REGIMES)}")
        elif expected is int:
            _check(0 < value <= 366, f"{where}.{name}: must be a day count from 1 to 366")
    basis = [k for k in _REGISTRY_GRADUATED if k in params]
    if "graduated" in (params["regime"], params.get("regime_2025")):
        _check(
            len(basis) == 1,
            f"{where}: a graduated table needs exactly one of {list(_REGISTRY_GRADUATED)}",
        )
    else:
        _check(not basis, f"{where}: {basis} apply only to a graduated table")
    credit = [k for k in _REGISTRY_CREDIT_KEYS if k in params]
    _check(
        credit in ([], list(_REGISTRY_CREDIT_KEYS)),
        f"{where}: a credit needs all of {list(_REGISTRY_CREDIT_KEYS)}, got {credit}",
    )
    if "brackets" in params:
        unused = sorted(_REGISTRY_FORMULA_KEYS & set(params))
        _check(not unused, f"{where}: {unused} are unused next to brackets")
    keys = list(params)
    exclusion = [key for key in keys if key.startswith("low_income_exclusion_")]
    exclusion_years = [int(key[-4:]) for key in exclusion]
    _check(
        exclusion_years == sorted(exclusion_years),
        f"{where}: low_income_exclusion_YYYY keys must be years ascending",
    )
    if exclusion:
        _check(
            isinstance(params[exclusion[0]], dict),
            f"{where}: {exclusion[0]} must state the threshold",
        )
    for base in _REGISTRY_YEARLY_BASES:
        later = [k for k in keys if _YEARLY_KEY_RE.match(k) and k.rsplit("_", 1)[0] == base]
        if not later:
            continue
        years = [int(k[-4:]) for k in later]
        # A flat ``rate_YYYY`` may be the first rate when the unsuffixed regime is
        # graduated (Iowa and Louisiana until that year). A flat regime needs ``rate``.
        if base not in params:
            _check(
                base == "rate" and params.get("regime") == "graduated" and years == sorted(years),
                f"{where}: {later[0]} needs {base} first",
            )
            continue
        _check(
            years == sorted(years) and keys.index(later[0]) > keys.index(base),
            f"{where}: {base}_YYYY keys must follow {base}, years ascending",
        )


def _check_canada_bracket(step: Mapping[str, Any], at: str) -> None:
    if step["over"] is not None:
        _check_amount(step["over"], f"{at}.over")
    _check_rate(step["rate"], f"{at}.rate")


def _check_canada_federal(params: Mapping[str, Any], where: str) -> None:
    """``canada_federal`` params: consecutive tax years ascending, then the Act's constants."""
    _check_keys(params, RATE_MODEL_PARAMS["canada_federal"], where)
    years = params["years"]
    _check(isinstance(years, list) and len(years) > 0, f"{where}.years: must be a non-empty list")
    for i, row in enumerate(years):
        at = f"{where}.years[{i}]"
        _check(isinstance(row, dict), f"{at}: must be an object")
        _check_keys(row, CANADA_YEAR_FIELDS, at)
        _check(type(row["tax_year"]) is int, f"{at}.tax_year: must be an integer")
        _check(
            i == 0 or row["tax_year"] == years[i - 1]["tax_year"] + 1,
            f"{at}.tax_year: years must be consecutive, ascending",
        )
        _check_steps(
            row["brackets"], ("over", "rate"), "over", f"{at}.brackets", _check_canada_bracket
        )
        bpa, bpa_at = row["basic_personal_amount"], f"{at}.basic_personal_amount"
        _check(isinstance(bpa, dict), f"{bpa_at}: must be an object")
        _check_keys(bpa, CANADA_BPA_FIELDS, bpa_at)
        for name in CANADA_BPA_FIELDS:
            _check_amount(bpa[name], f"{bpa_at}.{name}")
        _check(bpa["minimum"] <= bpa["maximum"], f"{bpa_at}: minimum must not exceed maximum")
        _check(
            bpa["phaseout_start"] < bpa["phaseout_end"],
            f"{bpa_at}: phaseout_start must be below phaseout_end",
        )
        _check_rate(row["credit_rate"], f"{at}.credit_rate")
    _check_rate(params["capital_gains_inclusion_rate"], f"{where}.capital_gains_inclusion_rate")
    for name, top in (("sojourner_days", 366), ("short_term_resident_months", 120)):
        value = params[name]
        _check(type(value) is int and 0 < value <= top, f"{where}.{name}: must be 1 to {top}")
    for name in ("t1161_threshold", "personal_use_reporting_threshold", "personal_use_minimum"):
        _check_amount(params[name], f"{where}.{name}")


def _year_keyed_bases(params: Mapping[str, Any]) -> set[str]:
    """Series a table can mark: ``_YYYY`` bases, plus ``rates`` when that key is present.

    Model validation has already required a ``rates`` value to be ``from_year`` steps.
    """
    found: set[str] = set()
    for key in params:
        match = _YEARLY_KEY_RE.match(key)
        if match is not None:
            found.add(series_param(match["name"]))
    if "rates" in params:
        found.add("rates")
    return found


def _sourced_for_year(params: Mapping[str, Any], param: str, year: int) -> bool:
    """Whether ``param`` already holds a real value for ``year``."""
    keys = (
        f"{param}_{year}",
        *(f"{param}_{status}_{year}" for status in FILING_STATUSES),
    )
    if any(key in params for key in keys):
        return True
    if param != "rates":
        return False
    return any(step["from_year"] == year for step in params["rates"])


def _check_not_recorded(code: str, params: Mapping[str, Any], markers: Any, where: str) -> None:
    """Reject a malformed ``not_recorded`` list, or one the lookup would misread.

    ``param`` is the base series (:func:`series_param`). A marker on a
    :data:`MARKER_ANCHORS` year is rejected because compliance reads that year
    while answering a different one. A Georgia ``standard_deduction`` marker
    before :data:`GA_FIRST_GRADUATED_YEAR` is rejected because that path reads
    :data:`GA_FLAT_RATE_FIRST_YEAR` instead.
    """
    at = f"{where}.{UNSOURCED_YEARS}"
    _check(isinstance(markers, list) and bool(markers), f"{at}: must be a non-empty list")
    bases = _year_keyed_bases(params)
    previous: int | None = None
    seen: set[tuple[str, int]] = set()
    for index, marker in enumerate(markers):
        item = f"{at}[{index}]"
        _check(
            isinstance(marker, dict) and list(marker) == list(UNSOURCED_FIELDS),
            f"{item}: fields must be exactly {list(UNSOURCED_FIELDS)}",
        )
        param = marker["param"]
        year = marker["tax_year"]
        reason = marker["reason"]
        if not isinstance(param, str) or not param:
            _check(False, f"{item}.param: must be a non-empty string")
        elif series_param(param) != param:
            _check(
                False,
                f"{item}: param {param!r} names one filing status; use {series_param(param)!r}",
            )
        if type(year) is not int:
            _check(False, f"{item}.tax_year: must be an integer")
        elif previous is not None and year < previous:
            _check(False, f"{item}.tax_year: must be in ascending order")
        else:
            previous = year
        if not isinstance(reason, str) or not reason:
            _check(False, f"{item}.reason: must be a non-empty string")
        _check((param, year) not in seen, f"{item}: duplicate {param} for tax year {year}")
        seen.add((param, year))
        _check(
            param in bases,
            f"{item}: param {param!r} is not a year-keyed series on this table",
        )
        if (code, param, year) in MARKER_ANCHORS:
            _check(
                False,
                f"{item}: cannot mark {param} for tax year {year}; "
                "compliance reads that year for other tax years",
            )
        if code == "GA" and param == "standard_deduction" and year < GA_FIRST_GRADUATED_YEAR:
            _check(
                False,
                f"{item}: cannot mark standard_deduction for tax year {year}; "
                f"a year before {GA_FIRST_GRADUATED_YEAR} reads the "
                f"{GA_FLAT_RATE_FIRST_YEAR} standard deduction",
            )
        _check(
            not _sourced_for_year(params, param, year),
            f"{item}: tax year {year} already has a sourced {param} value",
        )


def _check_params(model: str, params: Any, where: str, *, code: str) -> None:
    _check(isinstance(params, dict), f"{where}: params must be an object")
    where = f"{where} params"
    has_markers = UNSOURCED_YEARS in params
    markers = params[UNSOURCED_YEARS] if has_markers else None
    if has_markers:
        params = {key: value for key, value in params.items() if key != UNSOURCED_YEARS}
    if model == "registry":
        _check_registry(params, where)
    elif model == "canada_federal":
        _check_canada_federal(params, where)
    else:
        _check_model_params(model, params, where)
    if has_markers:
        _check_not_recorded(code, params, markers, where)


def _check_model_params(model: str, params: Mapping[str, Any], where: str) -> None:
    fields = list(RATE_MODEL_PARAMS[model])
    if fields[0] == "allowance":
        first = next(iter(params), None)
        _check(first in ALLOWANCE_NAMES, f"{where}: first key must be one of {ALLOWANCE_NAMES}")
        fields[0] = str(first)
    yearly = list(params)[len(fields) :] if model in YEARLY_PARAMS else []
    _check_keys(dict(list(params.items())[: len(params) - len(yearly)]), fields, where)
    last_year: dict[str, int] = {}
    for key in yearly:
        match = _YEARLY_KEY_RE.match(key)
        _check(
            match is not None and match["name"] in YEARLY_PARAMS[model],
            f"{where}: unknown key {key!r}; only {list(YEARLY_PARAMS[model])} take a _YYYY value",
        )
        name, year = (match["name"], int(match["year"])) if match else ("", 0)
        _check(year > last_year.get(name, 0), f"{where}: {key} out of order; years ascend")
        last_year[name] = year
    for key in [*fields, *yearly]:
        value, at = params[key], f"{where}.{key}"
        name = key.rsplit("_", 1)[0] if key in yearly else key
        if model in ("flat_by_year", "flat") and name == "deduction" and value == FEDERAL_ALLOWANCE:
            pass  # that tax year's federal standard deduction (load_federal_standard_deduction)
        elif (
            name in (*ALLOWANCE_NAMES, "standard_deduction")
            and model not in ("maryland", "connecticut")
            and not (model == "brackets_cumulative" and name == "exemption")
        ):
            _check_by_status(value, at)
        elif name == "standard_deduction":  # maryland: a share of AGI between two limits
            _check(isinstance(value, dict), f"{at}: must be an object")
            _check_keys(value, ("percent_of_agi", "minimum", "maximum"), at)
            _check_rate(value["percent_of_agi"], f"{at}.percent_of_agi")
            _check_by_status(value["minimum"], f"{at}.minimum")
            _check_by_status(value["maximum"], f"{at}.maximum")
        elif name == "personal_exemption":  # maryland: fixed amount with FAGI phase-out
            _check(isinstance(value, dict), f"{at}: must be an object")
            _check_keys(value, ("amount", "phaseout"), at)
            _check_amount(value["amount"], f"{at}.amount")
            phase = value["phaseout"]
            _check(isinstance(phase, dict), f"{at}.phaseout: must be an object")
            _check_keys(phase, FILING_STATUSES, f"{at}.phaseout")

            def _phase_row(step: Mapping[str, Any], step_at: str) -> None:
                if step["over"] is not None:
                    _check_amount(step["over"], f"{step_at}.over")
                _check_amount(step["amount"], f"{step_at}.amount")

            for status in FILING_STATUSES:
                _check_steps(
                    phase[status],
                    ("over", "amount"),
                    "over",
                    f"{at}.phaseout.{status}",
                    _phase_row,
                )
                base = next(step for step in phase[status] if step["over"] is None)
                _check(
                    base["amount"] == value["amount"],
                    f"{at}.phaseout.{status}: over-null amount must equal {at}.amount",
                )
        elif name == "exemption" and model == "brackets_cumulative":
            _check(isinstance(value, dict), f"{at}: must be an object")
            _check_keys(value, ("amount", "phaseout"), at)
            _check_amount(value["amount"], f"{at}.amount")
            phase = value["phaseout"]
            _check(isinstance(phase, dict), f"{at}.phaseout: must be an object")
            _check_keys(phase, FILING_STATUSES, f"{at}.phaseout")

            def _oh_phase_row(step: Mapping[str, Any], step_at: str) -> None:
                if step["over"] is not None:
                    _check_amount(step["over"], f"{step_at}.over")
                _check_amount(step["amount"], f"{step_at}.amount")

            for status in FILING_STATUSES:
                _check_steps(
                    phase[status],
                    ("over", "amount"),
                    "over",
                    f"{at}.phaseout.{status}",
                    _oh_phase_row,
                )
                base = next(step for step in phase[status] if step["over"] is None)
                _check(
                    base["amount"] == value["amount"],
                    f"{at}.phaseout.{status}: over-null amount must equal {at}.amount",
                )
        elif name == "exemption" and model == "connecticut":
            _check(isinstance(value, dict), f"{at}: must be an object")
            _check_keys(value, FILING_STATUSES, at)
            for status in FILING_STATUSES:
                spec, sat = value[status], f"{at}.{status}"
                _check(isinstance(spec, dict), f"{sat}: must be an object")
                _check_keys(spec, ("full", "full_until", "step_agi", "step_amount"), sat)
                for field in ("full", "full_until", "step_agi", "step_amount"):
                    _check_amount(spec[field], f"{sat}.{field}")
                _check(spec["step_agi"] > 0.0, f"{sat}.step_agi: must be greater than 0.0")
        elif name == "phase_out_addback":
            _check(isinstance(value, dict), f"{at}: must be an object")
            _check_keys(value, FILING_STATUSES, at)
            for status in FILING_STATUSES:
                spec, sat = value[status], f"{at}.{status}"
                _check(isinstance(spec, dict), f"{sat}: must be an object")
                _check_keys(spec, ("start", "step_agi", "step_amount", "maximum"), sat)
                for field in ("start", "step_agi", "step_amount", "maximum"):
                    _check_amount(spec[field], f"{sat}.{field}")
                _check(spec["step_agi"] > 0.0, f"{sat}.step_agi: must be greater than 0.0")
        elif name == "tax_recapture":
            _check(isinstance(value, dict), f"{at}: must be an object")
            _check_keys(value, FILING_STATUSES, at)

            def _recapture_row(step: Mapping[str, Any], step_at: str) -> None:
                if step["over"] is not None:
                    _check_amount(step["over"], f"{step_at}.over")
                _check_amount(step["amount"], f"{step_at}.amount")

            for status in FILING_STATUSES:
                _check_steps(
                    value[status],
                    ("over", "amount"),
                    "over",
                    f"{at}.{status}",
                    _recapture_row,
                )
        elif name == "personal_tax_credit":
            _check(isinstance(value, dict), f"{at}: must be an object")
            _check_keys(value, FILING_STATUSES, at)

            def _credit_row(step: Mapping[str, Any], step_at: str) -> None:
                if step["over"] is not None:
                    _check_amount(step["over"], f"{step_at}.over")
                _check_rate(step["decimal"], f"{step_at}.decimal")

            for status in FILING_STATUSES:
                _check_steps(
                    value[status],
                    ("over", "decimal"),
                    "over",
                    f"{at}.{status}",
                    _credit_row,
                )
        elif name == "brackets":
            cumulative = model == "brackets_cumulative"
            _check_brackets(value, ("over", "base", "rate") if cumulative else ("over", "rate"), at)
        elif name == "rates":
            _check_year_steps(value, ("from_year", "rate", YEAR_STEP_TEXT[model]), at)
        elif name == "retirement_exclusion":
            _check(isinstance(value, list) and len(value) > 0, f"{at}: must be a non-empty list")
            for i, step in enumerate(value):
                _check(isinstance(step, dict), f"{at}[{i}]: must be an object")
                _check_keys(step, ("min_age", "amount"), f"{at}[{i}]")
                _check(type(step["min_age"]) is int, f"{at}[{i}].min_age: must be an integer")
                _check_amount(step["amount"], f"{at}[{i}].amount")
            ages = [step["min_age"] for step in value]
            _check(ages == sorted(set(ages), reverse=True), f"{at}: min_age must decrease")
        elif name in ("surtax", "tier"):
            _check(isinstance(value, dict), f"{at}: must be an object")
            _check_keys(value, ("over", "rate"), at)
            _check_amount(value["over"], f"{at}.over")
            _check_rate(value["rate"], f"{at}.rate")
        elif name == "capital_gains_surtax":
            _check(isinstance(value, dict), f"{at}: must be an object")
            _check_keys(value, ("rate", "fagi_over", "through_year"), at)
            _check_rate(value["rate"], f"{at}.rate")
            _check_amount(value["fagi_over"], f"{at}.fagi_over")
            _check(
                type(value["through_year"]) is int,
                f"{at}.through_year: must be an int",
            )
            if key in yearly:
                key_year = int(key[-4:])
                _check(
                    value["through_year"] >= key_year,
                    f"{at}.through_year: must be >= {key_year}",
                )
        elif name == "rate":
            _check_rate(value, at)
        else:  # capital_gains_deduction
            _check_amount(value, at)


def rate_table_document(path: Path, code: str) -> dict[str, Any]:
    """Load and validate one ``rates/<code>.json`` file; return the whole document."""
    where = f"rates/{path.name}"
    _check(path.is_file(), f"{where}: missing (every state in RATE_TABLE_MODELS needs one)")
    doc = _read(path)
    _check(isinstance(doc, dict), f"{where}: top level must be an object")
    _check_keys(doc, RATE_TABLE_FIELDS, where)
    _check(doc["schema"] == SCHEMA_VERSION, f"{where}: schema must be {SCHEMA_VERSION}")
    _check(doc["jurisdiction"] == code, f"{where}: jurisdiction must be {code!r}")
    model = RATE_TABLE_MODELS[code]
    _check(doc["model"] == model, f"{where}: model must be {model!r}")
    _check(
        isinstance(doc["source_url"], str) and bool(doc["source_url"]),
        f"{where}: source_url must be a non-empty string or {NOT_RECORDED!r}",
    )
    _check_params(model, doc["params"], where, code=code)
    document: dict[str, Any] = doc
    return document


def load_rate_tables(root: Path = DATA_DIR) -> dict[str, dict[str, Any]]:
    """Every state's rate table ``params``, keyed by code in :data:`RATE_TABLE_MODELS` order.

    Raises :class:`CatalogError` when a file is missing, malformed or a stray file sits in
    ``rates/``.
    """
    stems = {file_stem(code) for code in RATE_TABLE_MODELS}
    stray = _stray_files(root, "rates", stems)
    _check(not stray, f"files for no rate table: {stray}")
    return {
        code: rate_table_document(root / "rates" / f"{file_stem(code)}.json", code)["params"]
        for code in RATE_TABLE_MODELS
    }


FEDERAL_TABLE_FIELDS: tuple[str, ...] = (
    "schema",
    "jurisdiction",
    "authority",
    "source_url",
    "years",
)
FEDERAL_YEAR_FIELDS: tuple[str, ...] = ("tax_year", *FILING_STATUSES)
FEDERAL_STANDARD_DEDUCTION = "federal/standard_deduction"
FEDERAL_TABLE_ID = "us-standard-deduction"


def federal_table_document(root: Path = DATA_DIR) -> dict[str, Any]:
    """Load and validate ``federal/standard_deduction.json``; return the whole document.

    ``years`` lists each tax year's ``single`` and ``mfj`` amounts, consecutive years
    ascending, so every year from the first to the last is held exactly once.
    """
    where = f"{FEDERAL_STANDARD_DEDUCTION}.json"
    stray = _stray_files(root, "federal", {"standard_deduction"})
    _check(not stray, f"files for no federal table: {stray}")
    path = root / f"{FEDERAL_STANDARD_DEDUCTION}.json"
    _check(path.is_file(), f"{where}: missing")
    doc = _read(path)
    _check(isinstance(doc, dict), f"{where}: top level must be an object")
    _check_keys(doc, FEDERAL_TABLE_FIELDS, where)
    _check(doc["schema"] == SCHEMA_VERSION, f"{where}: schema must be {SCHEMA_VERSION}")
    _check(doc["jurisdiction"] == "US", f"{where}: jurisdiction must be 'US'")
    for name in ("authority", "source_url"):
        _check(isinstance(doc[name], str) and bool(doc[name]), f"{where}: {name} must be a string")
    years = doc["years"]
    _check(isinstance(years, list) and len(years) > 0, f"{where}: years must be a non-empty list")
    for i, row in enumerate(years):
        at = f"{where} years[{i}]"
        _check(isinstance(row, dict), f"{at}: must be an object")
        _check_keys(row, FEDERAL_YEAR_FIELDS, at)
        _check(type(row["tax_year"]) is int, f"{at}.tax_year: must be an integer")
        _check(
            i == 0 or row["tax_year"] == years[i - 1]["tax_year"] + 1,
            f"{at}.tax_year: years must be consecutive, ascending",
        )
        for status in FILING_STATUSES:
            _check_amount(row[status], f"{at}.{status}")
    document: dict[str, Any] = doc
    return document


def load_federal_standard_deduction(root: Path = DATA_DIR) -> dict[int, dict[str, float]]:
    """The federal standard deduction by tax year: ``{2024: {"single": ..., "mfj": ...}}``."""
    return {
        row["tax_year"]: {status: row[status] for status in FILING_STATUSES}
        for row in federal_table_document(root)["years"]
    }


# Prior-year versions. Every (jurisdiction, citation) group that has a dated row (one with a
# superseded_year) is listed with (start, end): the first tax year its dated rows cover, and
# the year they stop, which is the year the current text first applies or, for a group in
# WINDOW_GROUPS_ENDED (no current row), the year its law ended. See window_span_errors.
WINDOW_SPANS: dict[tuple[str, str], tuple[int, int]] = {
    ("US", "26 U.S.C. § 1"): (2019, 2026),
    ("US", "26 U.S.C. § 24"): (2019, 2025),
    ("US", "26 U.S.C. § 32"): (2019, 2021),
    ("US", "26 U.S.C. § 62"): (2019, 2026),
    ("US", "26 U.S.C. § 63"): (2019, 2025),
    ("US", "26 CFR § 1.937-3"): (2019, 2026),
    ("PR", "PR CRI Sec. 1031.02"): (2019, 2026),
    ("PR", "PR CRI Sec. 1062.01"): (2019, 2020),
    ("PR", "PR CRI Sec. 1062.03"): (2019, 2020),
    ("PR", "Ley 60-2019 Sec. 1020.02"): (2019, 2026),
    ("PR", "Ley 60-2019 Sec. 2022.01"): (2019, 2026),
    ("PR", "Ley 60-2019 Sec. 2022.02"): (2019, 2026),
    ("PR", "Ley 60-2019 Sec. 6020.03"): (2019, 2026),
    ("PR", "Ley 60-2019 Sec. 6020.10"): (2019, 2026),
    ("PR", "Act 22-2012 Sec. 3"): (2012, 2019),
    ("GA", "O.C.G.A. § 48-7-20"): (2024, 2026),
    ("NJ", "N.J.S.A. 54A:5-8"): (2022, 2023),
    ("CA", "Cal. Rev. & Tax Code § 17043"): (2024, 2025),
    ("MA", "M.G.L. c. 62 § 3(B)(a)(9)"): (2022, 2023),
    ("LA", "La. Rev. Stat. Ann. § 47:32"): (2022, 2025),
    ("NH", "N.H. Rev. Stat. Ann. § 77:1"): (2024, 2025),
    ("CAN", "ITA s. 117"): (2019, 2025),
    ("CAN", "ITA s. 118"): (2019, 2025),
}
# Groups whose law ended: dated rows only, no current row.
WINDOW_GROUPS_ENDED: frozenset[tuple[str, str]] = frozenset(
    {("PR", "Act 22-2012 Sec. 3"), ("NH", "N.H. Rev. Stat. Ann. § 77:1")}
)


def window_span_errors(
    statutes: Sequence[Mapping[str, Any]],
    spans: Mapping[tuple[str, str], tuple[int, int]] = WINDOW_SPANS,
    ended: frozenset[tuple[str, str]] = WINDOW_GROUPS_ENDED,
) -> list[str]:
    """Check statute version groups against the committed spans and current-row rules.

    Per (jurisdiction, citation) group: (0) at most one current row, including groups that
    have no dated row; and for every group with a dated row: (1) dated rows never overlap;
    (2) exactly one current row, or none in an ended group; (3) the earliest dated
    ``effective_year`` is the span's start and the latest ``superseded_year`` its end;
    (4) each year from start to end is covered by exactly one dated row; (5) a current row
    starts no later than the span (layered on older text) or exactly at its end (a chain);
    (6) ``spans`` lists exactly the groups that have a dated row.
    """
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for s in statutes:
        groups.setdefault((str(s["jurisdiction"]), str(s["citation"])), []).append(s)
    dated_groups = {
        k for k, rows in groups.items() if any(r["superseded_year"] is not None for r in rows)
    }
    errors = [
        f"{j} {c}: dated rows but no WINDOW_SPANS entry"
        for j, c in sorted(dated_groups - set(spans))
    ]
    errors += [
        f"{j} {c}: WINDOW_SPANS entry but no dated row"
        for j, c in sorted(set(spans) - dated_groups)
    ]
    for key in sorted(dated_groups & set(spans)):
        start, end = spans[key]
        where = f"{key[0]} {key[1]}"
        rows = groups[key]
        dated = sorted(
            (r for r in rows if r["superseded_year"] is not None),
            key=lambda r: (r["effective_year"], str(r["id"])),
        )
        current = [r for r in rows if r["superseded_year"] is None]
        for a, b in zip(dated, dated[1:], strict=False):
            if b["effective_year"] < a["superseded_year"]:
                errors.append(f"{where}: {a['id']} and {b['id']} overlap")
        if key in ended and current:
            errors.append(f"{where}: ended in {end} but has a current row")
        elif key not in ended and len(current) != 1:
            errors.append(f"{where}: {len(current)} current rows, expected 1")
        first = min(r["effective_year"] for r in dated)
        last = max(r["superseded_year"] for r in dated)
        if first != start:
            errors.append(f"{where}: dated rows start in {first}, not {start}")
        if last != end:
            errors.append(f"{where}: dated rows end in {last}, not {end}")
        for year in range(start, end):
            covering = [r for r in dated if r["effective_year"] <= year < r["superseded_year"]]
            if len(covering) != 1:
                errors.append(f"{where}: tax year {year} is covered by {len(covering)} dated rows")
        for r in current:
            if not (r["effective_year"] <= start or r["effective_year"] == end):
                errors.append(
                    f"{where}: current row {r['id']} starts in {r['effective_year']}, "
                    f"inside the dated span {start}-{end}"
                )
    # Groups with no dated row skipped the loop above; still reject two or more currents.
    for key in sorted(
        k for k, rows in groups.items() if all(r["superseded_year"] is None for r in rows)
    ):
        where = f"{key[0]} {key[1]}"
        n = len(groups[key])
        if n > 1:
            errors.append(f"{where}: {n} current rows, expected at most 1")
    return errors


def statute_db_row(statute: Mapping[str, Any]) -> dict[str, Any]:
    """The ``statutes`` table columns of a loaded statute row."""
    row = {name: statute[name] for name in STATUTE_DB_FIELDS}
    if statute.get(TEXT_FR):
        row[TEXT_FR] = statute[TEXT_FR]  # stored in the statutes_fr table
    return row


def statute_document_row(statute: Mapping[str, Any]) -> dict[str, Any]:
    """A loaded statute row in data-file form (texts as lists of lines)."""
    row = {name: statute[name] for name in STATUTE_FIELDS}
    row["text_en"] = split_lines(statute["text_en"])
    row["text_es"] = split_lines(statute["text_es"])
    if statute.get(TEXT_FR):
        row[TEXT_FR] = split_lines(statute[TEXT_FR])
    return row


def publication_stem(publication: Mapping[str, Any]) -> str:
    """File stem of a publication: the id's prefix before the first hyphen."""
    return str(publication["id"]).split("-", 1)[0]


def _is_calendar_date(value: Any) -> bool:
    """``YYYY-MM-DD`` naming a real day: 2025-02-30 and 2025-13-01 are rejected."""
    if not (isinstance(value, str) and _DATE_RE.fullmatch(value)):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _entry_errors(entry: Mapping[str, Any]) -> list[str]:
    where = f"manifest/{file_stem(entry['_jurisdiction'])}.json entry {entry['id']!r}"
    errors: list[str] = []
    if entry["kind"] not in MANIFEST_KINDS:
        errors.append(f"{where}: kind must be one of {sorted(MANIFEST_KINDS)}")
    for name in ("id", "url", "licence", "enacting_law"):
        if not isinstance(entry[name], str) or not entry[name]:
            errors.append(f"{where}: {name} must be a non-empty string or {NOT_RECORDED!r}")
    if not (isinstance(entry["edition"], str) and entry["edition"] in EDITIONS):
        errors.append(f"{where}: edition must be one of {sorted(EDITIONS)}")
    if not (isinstance(entry["official"], bool) or entry["official"] == NOT_RECORDED):
        errors.append(f"{where}: official must be true, false or {NOT_RECORDED!r}")
    for name in ("retrieved", "effective_date"):
        value = entry[name]
        if value != NOT_RECORDED and not _is_calendar_date(value):
            errors.append(f"{where}: {name} must be YYYY-MM-DD or {NOT_RECORDED!r}")
    sha = entry["content_sha256"]
    if sha != NOT_RECORDED and not (isinstance(sha, str) and _SHA256_RE.match(sha)):
        errors.append(f"{where}: content_sha256 must be 64 lowercase hex or {NOT_RECORDED!r}")
    if entry["verification"] not in VERIFICATION_STATUSES:
        errors.append(f"{where}: verification must be one of {sorted(VERIFICATION_STATUSES)}")
    elif entry["verification"] == "verified_verbatim" and NOT_RECORDED in (
        entry["retrieved"],
        sha,
    ):
        errors.append(f"{where}: verified_verbatim needs a retrieved date and content_sha256")
    note = entry.get(VERIFICATION_NOTE)
    if entry["kind"] == "rate_table" and entry["verification"] == NOT_RECORDED:
        if not (isinstance(note, str) and note.strip()):
            errors.append(f"{where}: verification_note must be a non-empty string")
    elif VERIFICATION_NOTE in entry and entry["kind"] != "rate_table":
        errors.append(f"{where}: verification_note belongs only on a rate_table")
    if FRENCH_SOURCE in entry:
        errors.extend(_french_source_errors(entry, where))
    return errors


def _french_source_errors(entry: Mapping[str, Any], where: str) -> list[str]:
    source = entry[FRENCH_SOURCE]
    if entry["kind"] != "statute":
        return [f"{where}: {FRENCH_SOURCE} belongs only on a statute entry"]
    if not (isinstance(source, dict) and list(source) == list(FRENCH_SOURCE_FIELDS)):
        return [f"{where}: {FRENCH_SOURCE} fields must be exactly {list(FRENCH_SOURCE_FIELDS)}"]
    errors: list[str] = []
    if not (isinstance(source["url"], str) and source["url"].startswith("https://")):
        errors.append(f"{where}: {FRENCH_SOURCE}.url must be an https URL")
    sha = source["content_sha256"]
    if not (isinstance(sha, str) and _SHA256_RE.match(sha)):
        errors.append(f"{where}: {FRENCH_SOURCE}.content_sha256 must be 64 lowercase hex")
    return errors


_MISSING = object()


def _param_at(params: Any, path: str) -> Any:
    """The value at a dotted ``params`` path such as ``rates[0].rate``; ``_MISSING`` if none."""
    node = params
    for part in path.split("."):
        match = _PARAM_PATH_RE.fullmatch(part)
        if match is None or not isinstance(node, dict) or match[1] not in node:
            return _MISSING
        node = node[match[1]]
        for index in re.findall(r"\d+", match[2]):
            if not isinstance(node, list) or int(index) >= len(node):
                return _MISSING
            node = node[int(index)]
    return node


def _year_source_errors(entry: Mapping[str, Any], params: Any, where: str) -> list[str]:
    """Check a table entry's ``year_sources`` against the table's own values.

    ``params`` is a rate table's ``params``, or a whole federal table document.
    """
    sources = entry[YEAR_SOURCES]
    if entry["kind"] not in TABLE_KINDS:
        return [f"{where}: {YEAR_SOURCES} belongs only on a {' or '.join(TABLE_KINDS)} entry"]
    if not isinstance(sources, list) or not sources:
        return [f"{where}: {YEAR_SOURCES} must be a non-empty list"]
    errors: list[str] = []
    previous = 0
    for i, source in enumerate(sources):
        at = f"{where} {YEAR_SOURCES}[{i}]"
        if not isinstance(source, dict) or list(source) != list(YEAR_SOURCE_FIELDS):
            errors.append(f"{at}: fields must be exactly {list(YEAR_SOURCE_FIELDS)}")
            continue
        year = source["tax_year"]
        if type(year) is not int or year < previous:
            errors.append(f"{at}: tax_year must be an integer, in ascending order")
        else:
            previous = year
        if not (isinstance(source["url"], str) and source["url"].startswith("https://")):
            errors.append(f"{at}: url must be an https URL")
        if source["official"] is not True:
            errors.append(f"{at}: official must be true; cite only official sources")
        if not _is_calendar_date(source["retrieved"]):
            errors.append(f"{at}: retrieved must be YYYY-MM-DD")
        sha = source["content_sha256"]
        if sha != NOT_RECORDED and not (isinstance(sha, str) and _SHA256_RE.match(sha)):
            errors.append(f"{at}: content_sha256 must be 64 lowercase hex or {NOT_RECORDED!r}")
        values = source["values"]
        if not isinstance(values, dict) or not values:
            errors.append(f"{at}: values must be a non-empty object")
            continue
        for path, value in values.items():
            held = _param_at(params, path)
            if held is _MISSING:
                errors.append(f"{at}: values: {path!r} is not in the rate table")
            elif held != value or type(held) is not type(value):
                errors.append(f"{at}: values: {path!r} is {value!r} here but {held!r} in the table")
    return errors


def _unsourced_manifest_errors(
    entry: Mapping[str, Any], params: Mapping[str, Any], where: str
) -> list[str]:
    """The manifest list and the rate table's list are the same object, or both absent."""
    if entry["kind"] != "rate_table":
        if UNSOURCED_YEARS in entry:
            return [f"{where}: {UNSOURCED_YEARS} belongs only on a rate_table entry"]
        return []
    table_has = UNSOURCED_YEARS in params
    entry_has = UNSOURCED_YEARS in entry
    if not table_has and not entry_has:
        return []
    if not table_has or not entry_has or entry[UNSOURCED_YEARS] != params[UNSOURCED_YEARS]:
        return [f"{where}: {UNSOURCED_YEARS} must match the rate table"]
    return []


def manifest_errors(catalog: Catalog) -> list[str]:
    """Check every manifest entry against its row's source record, and the reverse.

    Each statute and publication row needs exactly one entry, in the manifest file of
    its jurisdiction (statutes) or id prefix (publications), whose ``url`` equals the
    row's ``source_url``. So does each rate table (kind ``rate_table``, id
    ``<code>-rates``, in its state's manifest file, ``url`` equal to the table's
    ``source_url``) and the federal table (kind ``federal_table``, in ``us.json``). Every
    field must be present; an unknown value is the explicit string ``"not_recorded"``, never
    a missing key.
    """
    rows: dict[tuple[str, str], tuple[str, str]] = {}
    french = {("statute", s["id"]) for s in catalog.statutes if s.get(TEXT_FR)}
    for s in catalog.statutes:
        rows[("statute", s["id"])] = (file_stem(s["jurisdiction"]), s["source_url"])
    for p in catalog.publications:
        rows[("publication", p["id"])] = (publication_stem(p), p["source_url"])
    for code, table in catalog.rate_tables.items():
        rows[("rate_table", rate_table_id(code))] = (file_stem(code), table["source_url"])
    for table_id, table in catalog.federal_tables.items():
        rows[("federal_table", table_id)] = (file_stem(table["jurisdiction"]), table["source_url"])
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for entry in catalog.manifest:
        errors.extend(_entry_errors(entry))
        key = (str(entry["kind"]), str(entry["id"]))
        where = f"manifest/{file_stem(entry['_jurisdiction'])}.json entry {entry['id']!r}"
        if key in seen:
            errors.append(f"{where}: duplicate entry")
            continue
        seen.add(key)
        if key not in rows:
            errors.append(f"{where}: no {key[0]} row with this id")
            continue
        stem, url = rows[key]
        if file_stem(entry["_jurisdiction"]) != stem:
            errors.append(f"{where}: belongs in manifest/{stem}.json")
        if entry["url"] != url:
            errors.append(f"{where}: url {entry['url']!r} != row source_url {url!r}")
        if key in french and FRENCH_SOURCE not in entry:
            errors.append(f"{where}: a row with text_fr needs {FRENCH_SOURCE}")
        if YEAR_SOURCES in entry:
            params: Any
            if key[0] == "federal_table":
                params = catalog.federal_tables[key[1]]
            else:
                params = catalog.rate_tables.get(str(entry["_jurisdiction"]), {}).get("params")
            errors.extend(_year_source_errors(entry, params, where))
        marker_params: Mapping[str, Any] = {}
        if key[0] == "rate_table":
            jur = str(entry["_jurisdiction"])
            held = catalog.rate_tables.get(jur)
            if held is None:
                errors.append(f"{where}: rate_table entry has no rate table for jurisdiction {jur}")
            else:
                marker_params = held["params"]
        errors.extend(_unsourced_manifest_errors(entry, marker_params, where))
    for kind, row_id in sorted(set(rows) - seen):
        errors.append(f"{kind} {row_id!r}: no manifest entry")
    return errors


def statute_doc_path(statute: Mapping[str, Any]) -> str:
    """Docs path of a statute page, relative to ``docs/``."""
    sec_slug = str(statute["section"]).lower()
    for old, new in ((".", "_"), ("-", "_"), ("/", "_"), (":", "_"), ("(", "_")):
        sec_slug = sec_slug.replace(old, new)
    sec_slug = sec_slug.replace(")", "").replace(" ", "_")
    base = f"{str(statute['corpus']).lower()}_{sec_slug}"
    if statute["superseded_year"]:
        base = f"{base}_{statute['effective_year']}"
    return f"{str(statute['jurisdiction']).lower()}/{base}.md"


# The note a Canada statute page carries above its texts (see disclaimers.CANADA_TEXT_LABEL).
FRENCH_DOCS_NOTE = (
    "English and French, equally authoritative, reproduced from the Justice Canada "
    "consolidation under the Reproduction of Federal Law Order (SI/97-5) with due diligence "
    "as to accuracy. This is not an official version and is not made in affiliation with, "
    "or with the endorsement of, the Government of Canada. The Spanish text is an "
    "unofficial summary."
)


def render_docs(
    statutes: Sequence[Mapping[str, Any]], publications: Sequence[Mapping[str, Any]]
) -> dict[str, str]:
    """Every generated docs file, keyed by its path relative to ``docs/``.

    That is one page per statute, ``llms.txt``, one ``<code>/llms-full.txt`` per
    jurisdiction with its full text, and ``llms-full.txt`` as the index of those files.
    """
    files: dict[str, str] = {}
    for s in statutes:
        is_summary = s["jurisdiction"] in {"NY", "NJ", "CA", "IL", "MA"}
        en_heading = (
            "Unofficial Reference Summary (English)"
            if is_summary
            else "Operative Statutory Language (English)"
        )
        es_heading = (
            "Resumen de referencia no oficial (Español)"
            if is_summary
            else "Texto Estatutario Oficial (Español)"
        )
        note = f" — {s['source_note']}" if s["source_note"] else ""
        refs = ", ".join(f"`{ref}`" for ref in s["cross_references"])
        french = s.get(TEXT_FR) or ""
        if french:
            official = (
                f"- **Official Text:** {FRENCH_DOCS_NOTE}\n\n"
                "## Operative Statutory Language (English)\n\n"
                f"{s['text_en']}\n\n"
                "## Texte législatif officiel (Français)\n\n"
                f"{french}\n\n"
                "## Resumen no oficial (Español)\n\n"
            )
        else:
            official = f"\n## {en_heading}\n\n{s['text_en']}\n\n## {es_heading}\n\n"
        files[statute_doc_path(s)] = (
            f"# {s['citation']} — {s['title']}\n\n"
            f"- **Jurisdiction:** {s['jurisdiction']}\n"
            f"- **Corpus:** {s['corpus']}\n"
            f"- **Effective Year:** {s['effective_year']}\n"
            f"- **Superseded Year:** {s['superseded_year'] or 'Current / In Force'}\n"
            f"- **Source:** [{s['source_url']}]({s['source_url']}){note}\n"
            f"- **Hierarchy:** `{s['breadcrumbs']}`\n"
            f"- **Cross References:** {refs}\n"
            f"{official}"
            f"{s['text_es']}\n"
        )
    summary = [
        "# TaxCode MCP Intelligence Corpus",
        "",
        "> Authoritative statutory and regulatory references for United States Federal Tax Law,",
        "> Puerto Rico Territorial Tax Law (Act 60-2019 and Ley 1-2011), and all 50 US States "
        "+ District of Columbia.",
        "> Canada (CAN): federal Income Tax Act and Income Tax Regulations sections in their "
        "official English and French text, with unofficial Spanish summaries.",
        "",
        "## Statutory Catalog",
        "",
    ]
    for s in statutes:
        summary.append(
            f"- [{s['citation']} ({s['effective_year']}) — {s['title']}]"
            f"(./{statute_doc_path(s)}): {s['breadcrumbs']}"
        )
    summary.extend(["", "## Administrative Publications & Guidance", ""])
    for p in publications:
        summary.append(f"- **{p['doc_number']}** ({p['doc_date']}) — {p['title']}: {p['summary']}")
    summary.extend(
        [
            "",
            "## Full Statutory Text",
            "",
            "- [Full-text index](./llms-full.txt): one `<code>/llms-full.txt` file per "
            "jurisdiction with the full English and Spanish text of every section "
            "(and the official French text of the Canada sections).",
        ]
    )
    files["llms.txt"] = "\n".join(summary)
    by_code: dict[str, list[Mapping[str, Any]]] = {}
    for s in statutes:
        by_code.setdefault(str(s["jurisdiction"]), []).append(s)
    index = [
        "# Full Statutory Corpus: US Federal, Puerto Rico & Multi-State Tax Intelligence",
        "",
        "Full statutory text is split by jurisdiction. Each file holds every section of one "
        "jurisdiction, in catalog order.",
        "",
    ]
    for code, rows in by_code.items():
        stem = file_stem(code)
        sections = "section" if len(rows) == 1 else "sections"
        index.append(f"- [{code}](./{stem}/llms-full.txt): {len(rows)} {sections}")
        full = [f"# Full Statutory Corpus: {code}\n"]
        if code in {"NY", "NJ", "CA", "IL", "MA"}:
            full.append(
                "Unofficial bilingual reference summaries; not official statutory text. / "
                "Resúmenes bilingües de referencia no oficiales; "
                "no son texto estatutario oficial.\n"
            )
        for s in rows:
            french = s.get(TEXT_FR) or ""
            spanish = (
                f"### Français\n{french}\n\n### Español (resumen no oficial)\n"
                if french
                else "### Español\n"
            )
            full.append(
                f"## {s['citation']}: {s['title']}\n`{s['breadcrumbs']}`\n\n"
                f"### English\n{s['text_en']}\n\n{spanish}{s['text_es']}\n"
            )
        files[f"{stem}/llms-full.txt"] = "\n".join(full)
    files["llms-full.txt"] = "\n".join(index) + "\n"
    return files
