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
  versions (by citation, bare section, current id or an older dated version's id);
* (c) ``out``: a search for a tax year from 2019 through 2025;
* (d) ``out``: the health check, whose statute count grew.
* (e) ``out``: a lookup that answers with a corrected row (see ``CORRECTED``): by its own id
  with any tax year or none, and by citation or bare section for a tax year that row
  answers; and the searches with no tax year whose results list that row, which now shows
  its corrected text, source, 2020 start and 2022 end, and ranks by that start
  (``CORRECTED_SEARCHES``).
* (f) ``out``: a lookup that answers with a row whose (a)(4) was corrected (see
  ``CORRECTED_A4``): by its own id with any tax year or none, and by citation or bare section
  for a tax year that row answers (or with no tax year, for the current row); and the
  resource read of that section, which shows the current row (``CORRECTED_RESOURCES``).
"""

from __future__ import annotations

from typing import Any

from fastmcp import Client

from scripts.build_prior_years_pins import BASE_COMMIT, answer, pin, plan, read_fixture
from taxcode_mcp.catalog import load_catalog
from taxcode_mcp.server import mcp
from taxcode_mcp.tools.compliance import SUPPORTED_STATE_JURISDICTIONS

# Section that gained versions -> (jurisdiction, citation, bare section, current id, tax
# years answered by a prior-year version).
VERSIONED: dict[str, tuple[str, str, str, str, range]] = {
    "1": ("US", "26 U.S.C. § 1", "1", "us-irc-1", range(2019, 2026)),
    "24": ("US", "26 U.S.C. § 24", "24", "us-irc-24", range(2019, 2025)),
    "32": ("US", "26 U.S.C. § 32", "32", "us-irc-32", range(2019, 2021)),
    "62": ("US", "26 U.S.C. § 62", "62", "us-irc-62", range(2019, 2026)),
    "63": ("US", "26 U.S.C. § 63", "63", "us-irc-63", range(2019, 2025)),
    "1.937-3": ("US", "26 CFR § 1.937-3", "1.937-3", "us-cfr-1-937-3", range(2019, 2026)),
    "1031.02": ("PR", "PR CRI Sec. 1031.02", "1031.02", "pr-cri-1031-02", range(2019, 2026)),
    "1062.01": ("PR", "PR CRI Sec. 1062.01", "1062.01", "pr-cri-1062-01", range(2019, 2020)),
    "1062.03": ("PR", "PR CRI Sec. 1062.03", "1062.03", "pr-cri-1062-03", range(2019, 2020)),
    "1020.02": (
        "PR",
        "Ley 60-2019 Sec. 1020.02",
        "1020.02",
        "pr-ley60-1020-02",
        range(2019, 2026),
    ),
    "2022.02": (
        "PR",
        "Ley 60-2019 Sec. 2022.02",
        "2022.02",
        "pr-ley60-2022-02",
        range(2019, 2026),
    ),
    "6020.03": (
        "PR",
        "Ley 60-2019 Sec. 6020.03",
        "6020.03",
        "pr-ley60-6020-03",
        range(2019, 2026),
    ),
    "6020.10": (
        "PR",
        "Ley 60-2019 Sec. 6020.10",
        "6020.10",
        "pr-ley60-6020-10",
        range(2022, 2025),
    ),
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
    "pr-cri-1031-02-2019",
    "pr-cri-1062-01-2019",
    "pr-cri-1062-03-2019",
    "pr-ley60-1020-02-2019",
    "pr-ley60-1020-02-2020",
    "pr-ley60-2022-02-2019",
    "pr-ley60-6020-03-2019",
    "pr-ley60-6020-10-2019-as-enacted",
    "pr-ley60-6020-10-2022",
    "pr-ley60-6020-10-2024",
}
# Older dated versions of a section that gained versions: their version lists grew too.
DATED_IDS: dict[str, frozenset[str]] = {
    "6020.10": frozenset({"pr-ley60-6020-10-2019", "pr-ley60-6020-10-2025"}),
}
# Row whose text was corrected after the base commit -> (jurisdiction, citation, bare
# section, tax years it answers). Ley 60-2019 Sec. 6020.10(b) wrongly carried Ley 32-2024's
# Fondo Especial para la Igualdad Social split, which first applies in 2024, into 2019-2023,
# and Ley 40-2020's text into 2019. It now answers 2020-2021; 2019 answers with Ley 60-2019 as
# approved (pr-ley60-6020-10-2019-as-enacted), so citation lookups for 2019-2021 may change;
# 2022-2024 now answer with their own versions (VERSIONED).
CORRECTED: dict[str, tuple[str, str, str, range]] = {
    "pr-ley60-6020-10-2019": ("PR", "Ley 60-2019 Sec. 6020.10", "6020.10", range(2019, 2022)),
}
CORRECTED_SEARCHES = frozenset(
    {
        "search|Puerto Rico|None|None|5",
        "search|Puerto Rico|PR|None|5",
        "search|residente|PR|None|5",
        "search|credit|None|None|0",
        "search|credit|PR|None|0",
        "search|exempt|None|None|5",
        "search|exempt|PR|None|5",
        "search|income|PR|None|5",
        "search|ingreso|PR|None|5",
    }
)
# Rows whose (a)(4) was corrected after the base commit -> (jurisdiction, citation, bare
# section, tax years it answers, None for no tax year). The compilation of Ley 60-2019 prints
# Ley 188-2024's (a)(4)(ii) as (i) and omits Ley 52-2022's (i), and the 2025 and current
# rows of Sec. 6020.10 carried that error; both now read as the 2024 row does.
CORRECTED_A4: dict[str, tuple[str, str, str, tuple[int | None, ...]]] = {
    "pr-ley60-6020-10-2025": ("PR", "Ley 60-2019 Sec. 6020.10", "6020.10", (2025,)),
    "pr-ley60-6020-10": ("PR", "Ley 60-2019 Sec. 6020.10", "6020.10", (None, 2026, 2027, 2028)),
}
# The resource read of a section answers with its current row.
CORRECTED_RESOURCES = frozenset({"resource|PR|6020.10"})


def _allowed(key: str) -> tuple[bool, bool]:
    """Whether a key's (out, versions) hash may change."""
    if key == "tools|health":
        return True, False
    kind, *rest = key.split("|")
    if kind == "search":
        year = rest[2]
        return (year != "None" and 2019 <= int(year) <= 2025) or key in CORRECTED_SEARCHES, False
    if kind == "resource":
        return key in CORRECTED_RESOURCES, False
    if kind != "lookup":
        return False, False
    query, jurisdiction, year = rest[0], rest[1], rest[2]
    out = versions = False
    for j, citation, section, current, years in VERSIONED.values():
        if jurisdiction in (j, "None"):
            if query in (citation, section):
                versions = True
                out = out or (year != "None" and int(year) in years)
            versions = versions or query == current or query in DATED_IDS.get(section, ())
    for row, (j, citation, section, years) in CORRECTED.items():
        if jurisdiction in (j, "None"):
            out = out or query == row
            if query in (citation, section):
                out = out or (year != "None" and int(year) in years)
    for row, (j, citation, section, a4_years) in CORRECTED_A4.items():
        if jurisdiction in (j, "None"):
            out = out or query == row
            if query in (citation, section):
                out = out or (None if year == "None" else int(year)) in a4_years
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
    # 280 US and 264 PR in-window lookups (240, plus 24 for 6020.10 in 2022-2024), 52 lookups
    # of the corrected row, 277 window-year searches, 9 searches listing the corrected row, the
    # health check, 64 lookups of the two rows whose (a)(4) was corrected (24 by id, 40 by
    # citation or bare section), and the resource read of 6020.10.
    assert (changed_out, changed_versions) == (948, 2072)
