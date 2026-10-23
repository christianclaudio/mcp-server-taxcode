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
MANIFEST_KINDS = frozenset({"statute", "publication", "rate_table"})
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
    "NY": "brackets_whole_income",
    "CA": "brackets_whole_income",
    "WA": "capital_gains_excise",
    "MA": "flat_with_surtax",
    "NJ": "brackets_whole_income",
    "IL": "flat",
    "PA": "flat_no_deduction",
    "CT": "brackets_whole_income",
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
}
RATE_TABLE_FIELDS: tuple[str, ...] = ("schema", "jurisdiction", "model", "source_url", "params")
# Field order of ``params`` per model. ``allowance`` is the filing-status amount taken off
# income first, stored under its statutory name: ``deduction`` or ``exemption``.
RATE_MODEL_PARAMS: dict[str, tuple[str, ...]] = {
    "georgia": ("standard_deduction", "retirement_exclusion", "rates"),
    "brackets_whole_income": ("allowance", "brackets"),
    "brackets_cumulative": ("allowance", "brackets"),
    "maryland": ("standard_deduction", "brackets"),
    "flat": ("allowance", "rate"),
    "flat_no_deduction": ("rate",),
    "flat_by_year": ("allowance", "rates"),
    "flat_with_surtax": ("allowance", "rate", "surtax"),
    "capital_gains_excise": ("capital_gains_deduction", "rate"),
}
ALLOWANCE_NAMES = ("deduction", "exemption")
FILING_STATUSES = ("single", "mfj")
REGIMES = frozenset({"none", "flat", "graduated"})
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
    **dict.fromkeys(
        (
            f"{name}_{status}{year}"
            for name in ("deduction", "top_bracket", "zero_bracket")
            for status in FILING_STATUSES
            for year in ("", "_2025", "_2026")
            if not (year == "_2026" and name != "deduction")
        ),
        float,
    ),
    **dict.fromkeys(
        ("top_rate", "middle_rate", "rate", "rate_2025", "rate_2026", "rate_2027"), float
    ),
    "middle_rate_estimate": bool,
    "brackets": dict,
}
# How a graduated registry table taxes income below its top bracket. ``brackets`` is the
# state's official marginal schedule; ``middle_rate`` is one statutory rate; and
# ``middle_rate_estimate`` (always ``true``) marks the old stand-in of 65% of the top rate,
# kept only where no official schedule maps onto this model. Exactly one must be present.
_REGISTRY_GRADUATED = ("brackets", "middle_rate", "middle_rate_estimate")
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
                _check_keys(row, STATUTE_FIELDS, where)
                _check_types(row, where)
                _check(row["jurisdiction"] == code, f"{where}: jurisdiction must be {code!r}")
                _check(row["id"] not in statute_ids, f"{where}: duplicate statute id")
                statute_ids.add(row["id"])
                loaded = dict(row)
                loaded["text_en"] = "\n".join(row["text_en"])
                loaded["text_es"] = "\n".join(row["text_es"])
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
                _check_keys(entry, MANIFEST_FIELDS, where)
                catalog.manifest.append({**entry, "_jurisdiction": code})
    if with_manifest:
        stray = _stray_files(root, "rates", {file_stem(c) for c in RATE_TABLE_MODELS})
        _check(not stray, f"files for no rate table: {stray}")
        for code in RATE_TABLE_MODELS:
            _check(code in codes, f"jurisdictions.json: rate table state {code!r} is missing")
            stem = file_stem(code)
            doc = rate_table_document(root / "rates" / f"{stem}.json", code)
            catalog.rate_tables[code] = catalog.documents[f"rates/{stem}"] = doc
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
        if "rate_desc" in step:
            _check(
                isinstance(step["rate_desc"], str) and bool(step["rate_desc"]),
                f"{at}.rate_desc: must be a non-empty string",
            )

    _check_steps(value, fields, "from_year", where, row)


def _check_registry(params: Mapping[str, Any], where: str) -> None:
    unknown = sorted(set(params) - set(_REGISTRY_TYPES))
    _check(not unknown, f"{where}: unknown keys {unknown}")
    missing = [k for k in _REGISTRY_REQUIRED if k not in params]
    _check(not missing, f"{where}: missing keys {missing}")
    for name, value in params.items():
        expected = _REGISTRY_TYPES[name]
        _check(type(value) is expected, f"{where}.{name}: must be {expected.__name__}")
        if name == "brackets":
            _check_brackets(value, ("over", "rate"), f"{where}.{name}")
        elif name == "middle_rate_estimate":
            _check(value is True, f"{where}.{name}: must be true when present")
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
    if "brackets" in params:
        unused = sorted(_REGISTRY_FORMULA_KEYS & set(params))
        _check(not unused, f"{where}: {unused} are unused next to brackets")


def _check_params(model: str, params: Any, where: str) -> None:
    _check(isinstance(params, dict), f"{where}: params must be an object")
    where = f"{where} params"
    if model == "registry":
        _check_registry(params, where)
        return
    fields = list(RATE_MODEL_PARAMS[model])
    if fields[0] == "allowance":
        first = next(iter(params), None)
        _check(first in ALLOWANCE_NAMES, f"{where}: first key must be one of {ALLOWANCE_NAMES}")
        fields[0] = str(first)
    _check_keys(params, fields, where)
    for name in fields:
        value, at = params[name], f"{where}.{name}"
        if name in (*ALLOWANCE_NAMES, "standard_deduction") and model != "maryland":
            _check_by_status(value, at)
        elif name == "standard_deduction":  # maryland: a share of AGI between two limits
            _check(isinstance(value, dict), f"{at}: must be an object")
            _check_keys(value, ("percent_of_agi", "minimum", "maximum"), at)
            _check_rate(value["percent_of_agi"], f"{at}.percent_of_agi")
            _check_by_status(value["minimum"], f"{at}.minimum")
            _check_by_status(value["maximum"], f"{at}.maximum")
        elif name == "brackets":
            cumulative = model == "brackets_cumulative"
            _check_brackets(value, ("over", "base", "rate") if cumulative else ("over", "rate"), at)
        elif name == "rates":
            by_year = model == "flat_by_year"
            _check_year_steps(
                value, ("from_year", "rate", "rate_desc") if by_year else ("from_year", "rate"), at
            )
        elif name == "retirement_exclusion":
            _check(isinstance(value, list) and len(value) > 0, f"{at}: must be a non-empty list")
            for i, step in enumerate(value):
                _check(isinstance(step, dict), f"{at}[{i}]: must be an object")
                _check_keys(step, ("min_age", "amount"), f"{at}[{i}]")
                _check(type(step["min_age"]) is int, f"{at}[{i}].min_age: must be an integer")
                _check_amount(step["amount"], f"{at}[{i}].amount")
            ages = [step["min_age"] for step in value]
            _check(ages == sorted(set(ages), reverse=True), f"{at}: min_age must decrease")
        elif name == "surtax":
            _check(isinstance(value, dict), f"{at}: must be an object")
            _check_keys(value, ("over", "rate"), at)
            _check_amount(value["over"], f"{at}.over")
            _check_rate(value["rate"], f"{at}.rate")
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
    _check_params(model, doc["params"], where)
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


def statute_db_row(statute: Mapping[str, Any]) -> dict[str, Any]:
    """The ``statutes`` table columns of a loaded statute row."""
    return {name: statute[name] for name in STATUTE_DB_FIELDS}


def statute_document_row(statute: Mapping[str, Any]) -> dict[str, Any]:
    """A loaded statute row in data-file form (texts as lists of lines)."""
    row = {name: statute[name] for name in STATUTE_FIELDS}
    row["text_en"] = split_lines(statute["text_en"])
    row["text_es"] = split_lines(statute["text_es"])
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
    return errors


def manifest_errors(catalog: Catalog) -> list[str]:
    """Check every manifest entry against its row's source record, and the reverse.

    Each statute and publication row needs exactly one entry, in the manifest file of
    its jurisdiction (statutes) or id prefix (publications), whose ``url`` equals the
    row's ``source_url``. So does each rate table (kind ``rate_table``, id
    ``<code>-rates``, in its state's manifest file, ``url`` equal to the table's
    ``source_url``). Every field must be present; an unknown value is the explicit
    string ``"not_recorded"``, never a missing key.
    """
    rows: dict[tuple[str, str], tuple[str, str]] = {}
    for s in catalog.statutes:
        rows[("statute", s["id"])] = (file_stem(s["jurisdiction"]), s["source_url"])
    for p in catalog.publications:
        rows[("publication", p["id"])] = (publication_stem(p), p["source_url"])
    for code, table in catalog.rate_tables.items():
        rows[("rate_table", rate_table_id(code))] = (file_stem(code), table["source_url"])
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


def render_docs(
    statutes: Sequence[Mapping[str, Any]], publications: Sequence[Mapping[str, Any]]
) -> dict[str, str]:
    """Every generated docs file, keyed by its path relative to ``docs/``.

    That is one page per statute, ``llms.txt``, one ``<code>/llms-full.txt`` per
    jurisdiction with its full text, and ``llms-full.txt`` as the index of those files.
    """
    files: dict[str, str] = {}
    for s in statutes:
        note = f" — {s['source_note']}" if s["source_note"] else ""
        refs = ", ".join(f"`{ref}`" for ref in s["cross_references"])
        files[statute_doc_path(s)] = (
            f"# {s['citation']} — {s['title']}\n\n"
            f"- **Jurisdiction:** {s['jurisdiction']}\n"
            f"- **Corpus:** {s['corpus']}\n"
            f"- **Effective Year:** {s['effective_year']}\n"
            f"- **Superseded Year:** {s['superseded_year'] or 'Current / In Force'}\n"
            f"- **Source:** [{s['source_url']}]({s['source_url']}){note}\n"
            f"- **Hierarchy:** `{s['breadcrumbs']}`\n"
            f"- **Cross References:** {refs}\n\n"
            "## Operative Statutory Language (English)\n\n"
            f"{s['text_en']}\n\n"
            "## Texto Estatutario Oficial (Español)\n\n"
            f"{s['text_es']}\n"
        )
    summary = [
        "# TaxCode MCP Intelligence Corpus",
        "",
        "> Authoritative statutory and regulatory references for United States Federal Tax Law,",
        "> Puerto Rico Territorial Tax Law (Act 60-2019 and Ley 1-2011), and all 50 US States "
        "+ District of Columbia.",
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
            "jurisdiction with the full English and Spanish text of every section.",
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
        for s in rows:
            full.append(
                f"## {s['citation']}: {s['title']}\n`{s['breadcrumbs']}`\n\n"
                f"### English\n{s['text_en']}\n\n### Español\n{s['text_es']}\n"
            )
        files[f"{stem}/llms-full.txt"] = "\n".join(full)
    files["llms-full.txt"] = "\n".join(index) + "\n"
    return files
