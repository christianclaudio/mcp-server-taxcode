#!/usr/bin/env python3
"""Build ``tests/fixtures/prior_years/corrected_answers.json``, full pins of corrected answers.

``tests/test_prior_years_pin.py`` lets an answer that comes from a corrected row (a key for
which its ``is_corrected`` is true) differ from the base commit pins. This file pins each of
those answers in full, as this checkout answers it: the sha256 of the whole answer, with its
``available_versions`` and any not-found message included, so a change to any byte of a
corrected answer fails that test. Rebuild it only when a corrected row is meant to change::

    uv run python scripts/build_corrected_pins.py

It runs the calls of ``scripts/build_prior_years_pins.py`` that the base fixture pins through
an in-memory client, uses no network, and writes the same bytes on every run.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CORRECTED_FIXTURE = ROOT / "tests" / "fixtures" / "prior_years" / "corrected_answers.json"


def full_sha(out: Any) -> str:
    """The sha256 of a whole answer."""
    text = json.dumps(out, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def read_corrected(path: Path = CORRECTED_FIXTURE) -> dict[str, str]:
    """Key -> full sha256 of each corrected answer."""
    pins: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))["pins"]
    return pins


async def _pins() -> dict[str, str]:
    from fastmcp import Client

    import taxcode_mcp
    from scripts.build_prior_years_pins import answer, plan, read_fixture
    from taxcode_mcp.catalog import load_catalog
    from taxcode_mcp.server import mcp
    from taxcode_mcp.tools.compliance import SUPPORTED_STATE_JURISDICTIONS
    from tests.test_prior_years_pin import is_corrected

    module = Path(taxcode_mcp.__file__).resolve()
    if not module.is_relative_to(ROOT / "src"):
        raise SystemExit(f"[!] imported {module}, not this checkout")
    calls = plan(load_catalog().statutes, SUPPORTED_STATE_JURISDICTIONS)
    pins: dict[str, str] = {}
    async with Client(mcp) as client:
        for key in sorted(read_fixture()["pins"]):
            if is_corrected(key):
                pins[key] = full_sha(await answer(client, calls[key]))
    return pins


def main() -> int:
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    pins = asyncio.run(_pins())
    body = json.dumps({"pins": pins}, sort_keys=True, indent=1) + "\n"
    CORRECTED_FIXTURE.write_text(body, encoding="utf-8")
    print(f"[*] wrote {len(pins)} corrected-answer pins to {CORRECTED_FIXTURE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
