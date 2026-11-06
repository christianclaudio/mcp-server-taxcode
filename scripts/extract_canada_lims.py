#!/usr/bin/env python3
"""Render sections of a Justice Canada consolidated XML file as plain statutory text.

The ``CAN`` statute rows (``src/taxcode_mcp/data/statutes/can.json``) hold the English and
French text of Income Tax Act and Income Tax Regulations sections as published in
``justicecanada/laws-lois-xml`` at the commit their ``source_url`` names. This script turns
one section of a locally saved copy of that file into the same lines, so anyone can check a
row against the file whose SHA-256 the manifest records (``<sha>`` is the commit)::

    URL=https://raw.githubusercontent.com/justicecanada/laws-lois-xml/<sha>/eng/acts/I-3.3.xml
    curl -fsSLO "$URL"
    sha256sum I-3.3.xml
    uv run python scripts/extract_canada_lims.py I-3.3.xml 128.1

Each provision is one line: its label (a section label is carried into its first subsection,
as in ``128.1 (1)``) followed by its text. A marginal note is a line of its own before the
provision it heads. Formulas keep their formula, connector (``where``) and term lines.
Amendment history notes are left out; they are not part of the operative text.
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET

# Children rendered as part of their parent, never as a provision of their own.
_INLINE_ONLY = frozenset({"HistoricalNote", "Label", "MarginalNote", "Text", "FormulaTerm"})
# Inline elements whose text is dropped: amendment history and footnote markers.
_DROPPED = frozenset({"HistoricalNote", "FootnoteRef"})


def flat(elem: ET.Element | None) -> str:
    """The text of ``elem`` and its inline children on one line, whitespace collapsed."""
    if elem is None:
        return ""
    parts: list[str] = []

    def walk(node: ET.Element) -> None:
        if node.tag in _DROPPED:
            if node.tail:
                parts.append(node.tail)
            return
        if node.tag == "Repealed":
            parts.append(" ")
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
        if node is not elem and node.tail:
            parts.append(node.tail)

    walk(elem)
    return " ".join("".join(parts).split())


def render(elem: ET.Element, carry: str = "") -> list[str]:
    """The lines of one provision and everything nested in it, in document order."""
    lines: list[str] = []
    note = elem.find("MarginalNote")
    if note is not None:
        lines.append(flat(note))
    if elem.tag == "Formula":
        return [*lines, flat(elem.find("FormulaText"))]
    if elem.tag == "FormulaConnector":
        return [*lines, flat(elem)]
    head = " ".join(p for p in (carry, flat(elem.find("Label"))) if p)
    if elem.tag == "FormulaDefinition":
        head = " ".join(p for p in (head, flat(elem.find("FormulaTerm"))) if p)
    pending = head
    text = elem.find("Text")
    if text is not None:
        lines.append(" ".join(p for p in (pending, flat(text)) if p))
        pending = ""
    for child in elem:
        if child.tag in _INLINE_ONLY or child.tag == "FormulaText":
            continue
        nested = render(child, pending)
        if nested:
            pending = ""
        lines.extend(nested)
    if pending and not lines:
        lines.append(pending)
    return [line for line in lines if line]


def find_section(root: ET.Element, label: str) -> ET.Element | None:
    """The first ``Section`` of the document body whose label is ``label``."""
    body = root.find("Body")
    for section in (root if body is None else body).iter("Section"):
        if flat(section.find("Label")) == label:
            return section
    return None


def section_lines(path: str, label: str) -> list[str]:
    """The rendered lines of section ``label`` of the XML file at ``path``."""
    section = find_section(ET.parse(path).getroot(), label)
    if section is None:
        raise SystemExit(f"[!] no section {label!r} in {path}")
    return render(section)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: extract_canada_lims.py <file.xml> <section label>", file=sys.stderr)
        return 2
    print(json.dumps(section_lines(argv[0], argv[1]), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
