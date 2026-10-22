"""Statute and publication rows, loaded from the versioned data files.

The data files under ``taxcode_mcp/data`` are the single source of truth for every
row the server serves and every generated docs page:

- ``jurisdictions.json``: the jurisdiction codes in catalog order. Rows load in this
  order, then in file order, so the database and the docs keep a stable order.
- ``statutes/<code>.json``: one jurisdiction's statute rows. ``text_en`` and
  ``text_es`` are stored as lists of lines (joined with ``"\\n"`` on load), so a
  change to one line of a long section shows as a one-line diff.
- ``publications/<code>.json``: publications whose id starts with ``<code>-``.
- ``manifest/<code>.json``: one source record per statute and publication row of
  that jurisdiction (see :data:`MANIFEST_FIELDS`).

Every file is written in one canonical form (:func:`dump_json`), and
``scripts/build_catalog.py --check`` fails when a file, a generated docs page or a
manifest entry is out of step.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
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
    "official",
    "licence",
    "retrieved",
    "content_sha256",
    "enacting_law",
    "effective_date",
    "verification",
)
MANIFEST_KINDS = frozenset({"statute", "publication"})
VERIFICATION_STATUSES = frozenset({"verified_verbatim", "summary", NOT_RECORDED})

_STR_FIELDS = frozenset(
    {"id", "jurisdiction", "corpus", "citation", "section", "title", "source_url"}
    | {"breadcrumbs", "source_note", "category", "doc_number", "doc_date", "summary"}
)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CatalogError(ValueError):
    """A data file breaks the catalog schema."""


@dataclass
class Catalog:
    """Rows loaded from the data files, in catalog order."""

    jurisdictions: list[str]
    statutes: list[dict[str, Any]] = field(default_factory=list)
    publications: list[dict[str, Any]] = field(default_factory=list)
    manifest: list[dict[str, Any]] = field(default_factory=list)
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
    :func:`manifest_errors` for the cross-check against the rows).
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
    return catalog


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


def _entry_errors(entry: Mapping[str, Any]) -> list[str]:
    where = f"manifest/{file_stem(entry['_jurisdiction'])}.json entry {entry['id']!r}"
    errors: list[str] = []
    if entry["kind"] not in MANIFEST_KINDS:
        errors.append(f"{where}: kind must be one of {sorted(MANIFEST_KINDS)}")
    for name in ("id", "url", "licence", "enacting_law"):
        if not isinstance(entry[name], str) or not entry[name]:
            errors.append(f"{where}: {name} must be a non-empty string or {NOT_RECORDED!r}")
    if not (isinstance(entry["official"], bool) or entry["official"] == NOT_RECORDED):
        errors.append(f"{where}: official must be true, false or {NOT_RECORDED!r}")
    for name in ("retrieved", "effective_date"):
        value = entry[name]
        if value != NOT_RECORDED and not (isinstance(value, str) and _DATE_RE.match(value)):
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
    row's ``source_url``. Every field must be present; an unknown value is the explicit
    string ``"not_recorded"``, never a missing key.
    """
    rows: dict[tuple[str, str], tuple[str, str]] = {}
    for s in catalog.statutes:
        rows[("statute", s["id"])] = (file_stem(s["jurisdiction"]), s["source_url"])
    for p in catalog.publications:
        rows[("publication", p["id"])] = (publication_stem(p), p["source_url"])
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
        index.append(f"- [{code}](./{stem}/llms-full.txt): {len(rows)} sections")
        full = [f"# Full Statutory Corpus: {code}\n"]
        for s in rows:
            full.append(
                f"## {s['citation']}: {s['title']}\n`{s['breadcrumbs']}`\n\n"
                f"### English\n{s['text_en']}\n\n### Español\n{s['text_es']}\n"
            )
        files[f"{stem}/llms-full.txt"] = "\n".join(full)
    files["llms-full.txt"] = "\n".join(index) + "\n"
    return files
