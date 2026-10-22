#!/usr/bin/env python3
"""Build every derived catalog artifact from the data files, or check that it is current.

The data files under ``src/taxcode_mcp/data`` are the single source of truth for the
statute and publication rows and the compliance rate tables (see
``taxcode_mcp.catalog``). This script:

- rewrites each data file in its canonical JSON form, so diffs stay small and stable;
- regenerates ``docs/<jurisdiction>/*.md``, ``docs/<jurisdiction>/llms-full.txt`` (that
  jurisdiction's full text), ``docs/llms.txt`` and ``docs/llms-full.txt`` (the index of the
  per-jurisdiction files), and deletes generated files whose rows are gone;
- then reports every manifest error (exit 1), since a manifest entry can't be rewritten
  into shape.

``--check`` writes nothing. It fails (exit 1) when a data file breaks the schema or is
not in canonical form, a rate table is missing or malformed, a manifest entry is
missing, incomplete or disagrees with its row's or rate table's source record, or a
committed docs file differs from what the data generates.
CI runs it, and ``tests/test_catalog.py`` runs the same check.

Usage::

    uv run python scripts/build_catalog.py          # write
    uv run python scripts/build_catalog.py --check  # verify only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from taxcode_mcp.catalog import (
    DATA_DIR,
    CatalogError,
    dump_json,
    load_catalog,
    manifest_errors,
    render_docs,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"


def expected_files(data_dir: Path) -> tuple[dict[Path, str], dict[str, str]]:
    """Canonical data files (absolute path -> text) and generated docs (docs path -> text)."""
    catalog = load_catalog(data_dir, with_manifest=True)
    data = {data_dir / "jurisdictions.json": dump_json(catalog.jurisdictions)}
    for name, document in catalog.documents.items():
        data[data_dir / f"{name}.json"] = dump_json(document)
    return data, render_docs(catalog.statutes, catalog.publications)


def _generated_docs(docs_dir: Path) -> set[str]:
    found = {
        p.relative_to(docs_dir).as_posix()
        for pattern in ("*/*.md", "*/llms-full.txt")
        for p in docs_dir.glob(pattern)
    }
    found |= {name for name in ("llms.txt", "llms-full.txt") if (docs_dir / name).is_file()}
    return found


def check(data_dir: Path = DATA_DIR, docs_dir: Path = DOCS_DIR) -> list[str]:
    """Return every problem found; an empty list means the catalog is current."""
    try:
        data, docs = expected_files(data_dir)
    except CatalogError as exc:
        return [f"data: {exc}"]
    problems = manifest_errors(load_catalog(data_dir, with_manifest=True))
    for path, text in data.items():
        if path.read_text(encoding="utf-8") != text:
            problems.append(
                f"data: {path.relative_to(data_dir).as_posix()} is not in canonical form"
            )
    for rel, text in docs.items():
        target = docs_dir / rel
        if not target.is_file():
            problems.append(f"docs: {rel} is missing")
        elif target.read_text(encoding="utf-8") != text:
            problems.append(f"docs: {rel} differs from the generated output")
    for rel in sorted(_generated_docs(docs_dir) - set(docs)):
        problems.append(f"docs: {rel} has no row in the data files")
    return problems


def manifest_problems(data_dir: Path = DATA_DIR) -> list[str]:
    """Manifest errors after a write: missing, incomplete or mismatched entries."""
    return manifest_errors(load_catalog(data_dir, with_manifest=True))


def write(data_dir: Path = DATA_DIR, docs_dir: Path = DOCS_DIR) -> list[str]:
    """Rewrite data files canonically and regenerate docs; return the paths changed."""
    data, docs = expected_files(data_dir)
    changed: list[str] = []
    for path, text in data.items():
        if path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
            changed.append(f"data/{path.relative_to(data_dir).as_posix()}")
    for rel, text in docs.items():
        target = docs_dir / rel
        if not target.is_file() or target.read_text(encoding="utf-8") != text:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            changed.append(f"docs/{rel}")
    for rel in sorted(_generated_docs(docs_dir) - set(docs)):
        (docs_dir / rel).unlink()
        changed.append(f"docs/{rel} (deleted)")
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Write nothing; exit 1 if data files, the manifest or docs are out of date.",
    )
    args = parser.parse_args(argv)
    if args.check:
        problems = check()
        for problem in problems:
            print(f"[x] {problem}")
        if problems:
            print(
                f"[x] {len(problems)} problem(s). Run `uv run python scripts/build_catalog.py` "
                "and fix any data or manifest error it reports."
            )
            return 1
        print("[✓] Data files, manifest and generated docs are current.")
        return 0
    changed = write()
    for path in changed:
        print(f"[*] wrote {path}")
    print(f"[✓] {len(changed)} file(s) changed.")
    problems = manifest_problems()
    for problem in problems:
        print(f"[x] {problem}")
    if problems:
        print(f"[x] {len(problems)} manifest problem(s) to fix by hand in data/manifest/.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
