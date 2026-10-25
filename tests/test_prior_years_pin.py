"""Existing-years pin: adding prior-year versions changes no answer outside their windows.

``fixtures/prior_years/f4f0d5d_pins.json.gz`` holds, for every call in
``scripts/build_prior_years_pins.py``'s plan as answered at the base commit (the last commit
before prior-year versions), hashes of the answer with every ``available_versions`` removed
(``out``) and of those version lists alone (``versions``). That script's docstring gives the
command that rebuilds the fixture from a checkout of the base commit.

Only these answers may change, and only in these ways:

* (a) ``out``: a lookup by citation or bare section of a section that gained versions, for a
  tax year inside those versions (a lookup by a current row's own id names that version, so
  it may not change);
* (b) ``versions``: any lookup of such a section, every year, since it now lists more
  versions;
* (c) ``out``: a search for a tax year from 2019 through 2025;
* (d) ``out``: the health check, whose statute count grew.
"""

from __future__ import annotations

from typing import Any

from fastmcp import Client

from scripts.build_prior_years_pins import BASE_COMMIT, answer, pin, plan, read_fixture
from taxcode_mcp.catalog import load_catalog
from taxcode_mcp.server import mcp
from taxcode_mcp.tools.compliance import SUPPORTED_STATE_JURISDICTIONS

# Section that gained versions -> (citation, bare section, current id, tax years answered by
# a prior-year version).
VERSIONED: dict[str, tuple[str, str, str, range]] = {
    "1": ("26 U.S.C. § 1", "1", "us-irc-1", range(2019, 2026)),
    "24": ("26 U.S.C. § 24", "24", "us-irc-24", range(2019, 2025)),
    "32": ("26 U.S.C. § 32", "32", "us-irc-32", range(2019, 2021)),
    "62": ("26 U.S.C. § 62", "62", "us-irc-62", range(2019, 2026)),
    "63": ("26 U.S.C. § 63", "63", "us-irc-63", range(2019, 2025)),
    "1.937-3": ("26 CFR § 1.937-3", "1.937-3", "us-cfr-1-937-3", range(2019, 2026)),
}
PRIOR_YEAR_IDS = {
    "us-irc-1-2019",
    "us-irc-1-2020",
    "us-irc-24-2019",
    "us-irc-24-2021",
    "us-irc-32-2019",
    "us-irc-62-2019",
    "us-irc-62-2020",
    "us-irc-62-2021",
    "us-irc-63-2019",
    "us-irc-63-2021",
    "us-cfr-1-937-3-2019",
}


def _allowed(key: str) -> tuple[bool, bool]:
    """Whether a key's (out, versions) hash may change."""
    if key == "tools|health":
        return True, False
    kind, *rest = key.split("|")
    if kind == "search":
        year = rest[2]
        return year != "None" and 2019 <= int(year) <= 2025, False
    if kind != "lookup":
        return False, False
    query, jurisdiction, year = rest[0], rest[1], rest[2]
    out = versions = False
    if jurisdiction in ("US", "None"):
        for citation, section, current, years in VERSIONED.values():
            if query in (citation, section):
                versions = True
                out = out or (year != "None" and int(year) in years)
            versions = versions or query == current
    return out, versions


def test_the_fixture_pins_exactly_the_base_commits_calls() -> None:
    fixture = read_fixture()
    assert fixture["head"] == BASE_COMMIT
    base_rows = [s for s in load_catalog().statutes if s["id"] not in PRIOR_YEAR_IDS]
    assert set(fixture["pins"]) == set(plan(base_rows, SUPPORTED_STATE_JURISDICTIONS))
    assert len(fixture["pins"]) == 26_709


async def test_no_answer_changes_outside_the_new_windows() -> None:
    pins: dict[str, list[str]] = read_fixture()["pins"]
    calls = plan(load_catalog().statutes, SUPPORTED_STATE_JURISDICTIONS)
    unexpected: list[str] = []
    changed_out = changed_versions = 0
    async with Client(mcp) as client:
        for key in sorted(pins):
            out: Any = await answer(client, calls[key])
            now = pin(out)
            out_ok, versions_ok = _allowed(key)
            if now[0] != pins[key][0]:
                changed_out += 1
                if not out_ok:
                    unexpected.append(f"out {key}")
            if now[1] != pins[key][1]:
                changed_versions += 1
                if not versions_ok:
                    unexpected.append(f"versions {key}")
    assert unexpected == []
    # 280 in-window lookups + 167 window-year searches + the health check.
    assert (changed_out, changed_versions) == (448, 1080)
