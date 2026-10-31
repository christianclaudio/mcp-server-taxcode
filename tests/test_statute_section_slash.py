"""Statute resource reads for section ids that contain a slash (#41)."""

from __future__ import annotations

import pytest
from fastmcp import Client

from taxcode_mcp.server import mcp


async def _read(client: Client, uri: str) -> str:
    contents = await client.read_resource(uri)
    text = getattr(contents[0], "text", None)
    assert isinstance(text, str)
    return text


@pytest.mark.parametrize(
    ("uri", "citation"),
    [
        ("taxcode://statutes/IL/5/201", "35 ILCS 5/201"),
        ("taxcode://statutes/IL/5%2F201", "35 ILCS 5/201"),
        ("taxcode://statutes/IL/5/301", "35 ILCS 5/301"),
        ("taxcode://statutes/IL/5/1501(a)(20)", "35 ILCS 5/1501(a)(20)"),
    ],
)
async def test_statute_resource_reads_illinois_sections_with_slashes(uri: str, citation: str) -> None:
    async with Client(mcp) as client:
        text = await _read(client, uri)
    assert "not found" not in text.lower()
    assert "Jurisdiction: IL" in text
    # The header opens with the matched row's citation, so each URI must resolve to its own section.
    assert text.startswith(f"{citation} - ")


async def test_statute_resource_still_reads_a_single_segment_section() -> None:
    async with Client(mcp) as client:
        text = await _read(client, "taxcode://statutes/US/933")
    assert "Jurisdiction: US" in text
    assert "933" in text


async def test_statute_resource_template_advertises_section_wildcard() -> None:
    async with Client(mcp) as client:
        templates = [t.uri_template for t in await client.list_resource_templates()]
    assert templates == ["taxcode://statutes/{jurisdiction}/{section*}{?page_cursor}"]


async def test_unknown_slash_section_reports_not_found() -> None:
    async with Client(mcp) as client:
        text = await _read(client, "taxcode://statutes/IL/9/999")
    assert "not found" in text.lower()
