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

Separately, a not-found lookup by a row's own id for a tax year outside that row's window may
end its message with one sentence naming the row that answers that year (``POINTER``). That
sentence is dropped before an answer is compared with its pin, so the rules above still hold
for every other byte, and the number of answers carrying it is fixed below.

Every answer that rules (e) and (f) let change (``is_corrected``) is also pinned in full, its
pointer sentence included, in ``fixtures/prior_years/corrected_answers.json`` (built by
``scripts/build_corrected_pins.py``), so a change to any byte of a corrected answer fails.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastmcp import Client

from scripts.build_corrected_pins import full_sha, read_corrected
from scripts.build_prior_years_pins import BASE_COMMIT, answer, pin, plan, read_fixture
from taxcode_mcp.catalog import load_catalog
from taxcode_mcp.db import db
from taxcode_mcp.server import mcp
from taxcode_mcp.tools.compliance import SUPPORTED_STATE_JURISDICTIONS

# The sentence a not-found lookup by id ends with when another row answers that tax year.
POINTER = re.compile(
    r" That record id names another version; tax year \d+ is answered by '[^']+'\.\Z"
)


def _without_pointer(out: Any) -> tuple[Any, bool]:
    """``out`` with a not-found message's pointer sentence dropped, and whether it had one."""
    if not (isinstance(out, dict) and out.get("status") == "not_found"):
        return out, False
    message = out.get("message")
    found = POINTER.search(message) if isinstance(message, str) else None
    if found is None:
        return out, False
    return {**out, "message": message[: found.start()]}, True


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
# Rows added after BASE_COMMIT (including the three Wave 1 dated rows). Their lookup/resource
# keys are not in the base fixture; exclude them from the plan equality check. Search answers
# that newly list them are allowed via NEW_SINCE_BASE_SEARCHES.
WAVE1_FIXTURES = Path(__file__).parent / "fixtures" / "wave1"
WAVE1_IDS = frozenset(json.loads((WAVE1_FIXTURES / "sources.json").read_text()))
WAVE1_SEARCHES = frozenset(json.loads((WAVE1_FIXTURES / "searches.json").read_text()))
# The Canada (CAN) rows and CRA publications, added after BASE_COMMIT (see CANADA_COUNT).
CANADA_IDS = frozenset(s["id"] for s in load_catalog().statutes if s["jurisdiction"] == "CAN")
CANADA_PUBS = frozenset(p["id"] for p in load_catalog().publications if p["id"].startswith("can-"))
NEW_SINCE_BASE_IDS = frozenset({"pr-ley60-6060-05"}) | WAVE1_IDS | CANADA_IDS
# Searches (outside the 2019-2025 window already allowed) whose result lists newly include
# pr-ley60-6060-05 after Ley 180-2025: "exempt"/"exenta" wording, "income"/"ingreso", and
# "Puerto Rico" at offset 5 for tax years 2026-2028 (and no-year income/ingreso at offset 5).
NEW_SINCE_BASE_SEARCHES = frozenset(
    {
        "search|Puerto Rico|None|2026|5",
        "search|Puerto Rico|None|2027|5",
        "search|Puerto Rico|None|2028|5",
        "search|Puerto Rico|PR|2026|5",
        "search|Puerto Rico|PR|2027|5",
        "search|Puerto Rico|PR|2028|5",
        "search|exempt|None|2026|0",
        "search|exempt|None|2026|5",
        "search|exempt|None|2027|0",
        "search|exempt|None|2027|5",
        "search|exempt|None|2028|0",
        "search|exempt|None|2028|5",
        "search|exempt|PR|2026|0",
        "search|exempt|PR|2026|5",
        "search|exempt|PR|2027|0",
        "search|exempt|PR|2027|5",
        "search|exempt|PR|2028|0",
        "search|exempt|PR|2028|5",
        "search|income|None|2026|5",
        "search|income|None|2027|5",
        "search|income|None|2028|5",
        "search|income|None|None|5",
        "search|income|PR|2026|0",
        "search|income|PR|2026|5",
        "search|income|PR|2027|0",
        "search|income|PR|2027|5",
        "search|income|PR|2028|0",
        "search|income|PR|2028|5",
        "search|ingreso|None|2026|5",
        "search|ingreso|None|2027|5",
        "search|ingreso|None|2028|5",
        "search|ingreso|None|None|5",
        "search|ingreso|PR|2026|0",
        "search|ingreso|PR|2026|5",
        "search|ingreso|PR|2027|0",
        "search|ingreso|PR|2027|5",
        "search|ingreso|PR|2028|0",
        "search|ingreso|PR|2028|5",
    }
)
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
    "ca-rtc-17041": (
        "CA",
        "Cal. Rev. & Tax Code § 17041",
        "17041",
        (None, 2024, 2025, 2026, 2027, 2028),
    ),
}
# The resource read of a section answers with its current row.
CORRECTED_RESOURCES = frozenset({"resource|PR|6020.10", "resource|CA|17041"})
# Resource reads that newly resolve after the {section*} template fix (issue #41).
# Base pins recorded Unknown resource / not found; content is unchanged statute text.
SLASH_RESOURCES = frozenset({"resource|IL|5/201", "resource|IL|5/301", "resource|IL|5/1501(a)(20)"})
# Answers pinned in full in fixtures/prior_years/corrected_answers.json: 240 lookups by a
# corrected row's id, 112 by its citation or bare section, 9 searches and 2 resource reads.
CORRECTED_COUNT = 363


def is_corrected(key: str) -> bool:
    """Whether a key answers with a corrected row, by rule (e) or (f); pinned in full."""
    kind, *rest = key.split("|")
    if kind == "search":
        return key in CORRECTED_SEARCHES
    if kind == "resource":
        return key in CORRECTED_RESOURCES
    if kind != "lookup":
        return False
    query, jurisdiction, year = rest[0], rest[1], rest[2]
    tax_year = None if year == "None" else int(year)
    corrected: list[tuple[str, tuple[str, str, str, Collection[int | None]]]] = [
        *CORRECTED.items(),
        *CORRECTED_A4.items(),
    ]
    for row, (j, citation, section, years) in corrected:
        if jurisdiction in (j, "None"):
            if query == row or (query in (citation, section) and tax_year in years):
                return True
    return False


# Prior #29 rate-model states: any year may change.
RATE_MODEL_CHANGED = frozenset({"AR", "CA", "CT", "MD", "NJ", "NY"})
# Batch 4: shared federal standard-deduction readers. 2024 already held 14,600, so a
# 2024 out change is excluded from this general allow; UT 2024 may still differ in
# rate_description only (see _ut_2024_rate_desc_only_matches_pin).
FEDERAL_TABLE_STATES = frozenset(
    {
        "AZ",
        "DC",
        "IA",
        "ID",
        "ME",
        "MO",
        "MT",
        "ND",
        "NM",
        "SC",
        "UT",
    }
)
# Batch 4: MS exemptions and the Ohio MAGI-tier exemption are year-unsuffixed, so
# they apply for every year in the pin grid. Kansas allowances are year-specific
# from 2018; those years are in the same window.
EXEMPTION_STATES = frozenset({"AL", "KS", "MS", "OH"})
EXEMPTION_YEARS = range(2018, 2029)
GA_SCHEDULE_YEARS = range(2018, 2024)
# 2587 previously, plus 232 answers whose tax or rate prose moved onto a schedule
# sourced here: Hawaii's 2024 standard deduction and 2025 brackets, North Carolina's
# 2022-2023 rates and the pre-2022 estimate label, Virginia's deduction by year,
# Oklahoma's 2026 brackets, and West Virginia's 2025 rates. Plus 72 statute lookups
# of Okla. Stat. tit. 68, § 2355 after its source cite was corrected. Plus 206 answers
# for Kentucky's flat rate by year, Wisconsin's 2025 brackets, Ohio's 2025 exemption,
# and the estimate labels on years those tables do not publish. Plus 6 answers for
# Georgia's 2018 6 percent top rate and South Carolina's 2026 schedule. Plus 88
# answers whose New Hampshire description now states the prior-law interest and
# dividends tax and the 2025 repeal. Plus 88 Alabama answers after the personal
# exemption was added beside the maximum standard deduction.
# Plus 42 answers after Ley 180-2025 added pr-ley60-6060-05 (38 post-2025/no-year
# searches newly listing that row; 4 already-allowlisted keys whose out hash also moved).
# Plus 75 answers after ca-rtc-17041 named Mental Health Services Tax through 2024 and
# Behavioral Health Services Tax from 2025 (72 lookups, 1 resource read, and 2 2024 searches;
# the 2025-2028 searches were already in the changed set).
# November 2026 source sweep moves 358 answers: per-year figures, and refusals
# where the official file does not print a complete schedule. The follow-up
# records the printed partial-schedule amounts, which brings two of those
# answers back to the pin. 3836 is the number of answers that still differ.
# Wave 1 changes 90 searches: 32 newly differ from base, 58 already differed.
# Plus the answers that now list a Canada (CAN) row or CRA publication: the CANADA_COUNT ones
# outside the windows, checked byte for byte without those rows, and searches the windows
# above already allow to change. An answer that changed for both reasons is counted once.
CHANGED_OUT = 3836
# Answers that newly list a Canada row or publication and sit outside the allowlists above.
# A search in every jurisdiction ranks the Canada rows among the others, and the newest-first
# publication pages list the CRA guidance. Each is the pinned base answer, byte for byte,
# once the Canada rows are dropped. Main counted 68; Wave 1's search allowlist covers the
# rest, so the pin test's counter on the merged catalog is lower.
CANADA_COUNT = 52
# Schedules added from official publications in this sourcing pass. A year in the
# range may change because that year's figures no longer borrow a neighboring schedule,
# or because the rate prose now names the statute. 2024 amounts that were already
# the held schedule stay on it.
SCHEDULE_UPDATES: dict[str, Collection[int]] = {
    "HI": range(2024, 2029),
    "NC": range(2018, 2029),
    "NH": range(2015, 2029),
    "VA": range(2018, 2029),
    "OK": range(2026, 2029),
    "WV": range(2025, 2029),
    # Kentucky's flat rate by year (KRS 141.020) and the pre-2024 deduction label.
    "KY": range(2015, 2029),
    # Wisconsin's 2025 statutory brackets, and the estimate label outside 2024-2025.
    "WI": range(2015, 2029),
    # Ohio's 2025 exemption (R.C. 5747.025) and the pre-2024 bracket label.
    "OH": range(2015, 2029),
    # Nebraska's pre-2023 indexed brackets are the 2023 schedule, labeled.
    "NE": range(2015, 2023),
    # November 2026 source sweep, limited to the state-years whose answers differ
    # from the pin. Years the inventory does not move are not opened.
    "MI": {2018, 2019, 2020, 2021, 2022, 2023, 2025, 2026, 2027, 2028},
    "MN": {2018, 2019, 2020, 2021, 2022, 2023, 2025, 2026, 2027, 2028},
    "LA": {2018, 2019, 2020, 2021},
    "MS": set(range(2018, 2029)),
    "VT": set(range(2018, 2029)),
    "WA": {2022, 2026, 2027, 2028},
}


def _state_year(key: str) -> tuple[str, str, int] | None:
    """`(kind, state_or_empty, tax_year)` for a state-residency or georgia key."""
    kind, *rest = key.split("|")
    if kind == "state":
        return kind, rest[0], int(rest[1])
    if kind == "ga":
        return kind, "GA", int(rest[0])
    return None


def _strip_dependent_note(out: Any) -> Any:
    """Answer with dependent-exemption notes removed from every string field."""
    from taxcode_mcp.tools.compliance import DEPENDENT_EXEMPTION_NOTE, GA_TOOL_DEPENDENT_NOTE

    notes = (DEPENDENT_EXEMPTION_NOTE, GA_TOOL_DEPENDENT_NOTE)

    def _strip(value: str) -> str:
        for note in notes:
            value = value.replace(note, "")
        return value

    if isinstance(out, dict):
        return {
            k: (_strip(v) if isinstance(v, str) else _strip_dependent_note(v))
            for k, v in out.items()
        }
    if isinstance(out, list):
        return [_strip_dependent_note(v) for v in out]
    return out


def _ga_note_only_matches_pin(out: Any, base_out_hash: str) -> bool:
    """Whether `out` equals the pin once `DEPENDENT_EXEMPTION_NOTE` is removed."""
    return pin(_strip_dependent_note(out))[0] == base_out_hash


# rate_description at BASE_COMMIT for UT years that use the unsuffixed rate_desc.
UT_PIN_RATE_DESCRIPTION = (
    "Flat 4.55% rate (SB 69) less the taxpayer tax credit: 6% of the $14,600 "
    "($29,200 joint) federal standard deduction, reduced by 1.3% of income over "
    "$17,652 ($35,304 joint)"
)


def _with_rate_description(out: Any, text: str) -> Any:
    """Answer with every `rate_description` string replaced by `text`."""
    if isinstance(out, dict):
        return {
            k: (text if k == "rate_description" else _with_rate_description(v, text))
            for k, v in out.items()
        }
    if isinstance(out, list):
        return [_with_rate_description(v, text) for v in out]
    return out


def _ut_2024_rate_desc_only_matches_pin(out: Any, base_out_hash: str) -> bool:
    """Whether `out` equals the pin once `rate_description` is the pin-era UT text.

    Amounts and every other field must match; only the rate-description prose may differ.
    """
    return pin(_with_rate_description(out, UT_PIN_RATE_DESCRIPTION))[0] == base_out_hash


def _allowed(key: str) -> tuple[bool, bool]:
    """Whether a key's (out, versions) hash may change for any reason."""
    if key == "tools|health":
        return True, False
    parsed = _state_year(key)
    if parsed is not None:
        kind, state, year = parsed
        if state in RATE_MODEL_CHANGED:
            return True, False
        if state in FEDERAL_TABLE_STATES and year != 2024:
            return True, False
        if state in EXEMPTION_STATES and year in EXEMPTION_YEARS:
            return True, False
        if state == "GA" and year in GA_SCHEDULE_YEARS:
            return True, False
        if year in SCHEDULE_UPDATES.get(state, range(0)):
            return True, False
        # Other GA years: note-only, decided in `_compare` after stripping the note.
        return False, False
    kind, *rest = key.split("|")
    # The Oklahoma § 2355 row's source cite was corrected. Lookups of that row may
    # change; the stored summary text does not.
    if kind == "lookup" and rest[0] in (
        "2355",
        "Okla. Stat. tit. 68, § 2355",
        "ok-stat-68-2355",
    ):
        return True, False
    if kind == "search":
        year = rest[2]
        return (
            (year != "None" and 2019 <= int(year) <= 2025)
            or is_corrected(key)
            or key in NEW_SINCE_BASE_SEARCHES
            or key in WAVE1_SEARCHES
        ), False
    if kind == "resource":
        return (
            key in CORRECTED_RESOURCES or key in SLASH_RESOURCES or key == "resource|OK|2355"
        ), False
    if kind != "lookup":
        return is_corrected(key), False
    query, jurisdiction, year = rest[0], rest[1], rest[2]
    out = versions = False
    for j, citation, section, current, years in VERSIONED.values():
        if jurisdiction in (j, "None"):
            if query in (citation, section):
                versions = True
                out = out or (year != "None" and int(year) in years)
            versions = versions or query == current or query in DATED_IDS.get(section, ())
    return out or is_corrected(key), versions


def _lists_canada(key: str) -> bool:
    """Whether a key's answer may list a Canada row or publication."""
    kind, *rest = key.split("|")
    return kind == "pubs" or (kind == "search" and rest[1] == "None")


@contextmanager
def _without_canada() -> Iterator[None]:
    """Answers as at BASE_COMMIT: searches and publication pages without the Canada rows."""
    search, listed = db.search_statutes, db.list_publications

    def search_base(*, limit: int = 5, offset: int = 0, **kw: Any) -> list[dict[str, Any]]:
        rows = search(**kw, limit=offset + limit + len(CANADA_IDS), offset=0)
        return [r for r in rows if r["id"] not in CANADA_IDS][offset : offset + limit]

    def list_base(*, limit: int = 10, offset: int = 0, **kw: Any) -> list[dict[str, Any]]:
        rows = listed(**kw, limit=offset + limit + len(CANADA_PUBS), offset=0)
        return [r for r in rows if r["id"] not in CANADA_PUBS][offset : offset + limit]

    with (
        patch.object(db, "search_statutes", search_base),
        patch.object(db, "list_publications", list_base),
    ):
        yield


def _compare(
    key: str, raw: Any, base: list[str], full: str | None
) -> tuple[list[str], bool, bool, bool]:
    """One answer against its pins: problems, whether out / versions changed, pointer seen."""
    out, has_pointer = _without_pointer(raw)
    now = pin(out)
    out_ok, versions_ok = _allowed(key)
    out_changed, versions_changed = now[0] != base[0], now[1] != base[1]
    problems: list[str] = []
    parsed = _state_year(key)
    ga_note_only = (
        parsed is not None
        and parsed[1] == "GA"
        and parsed[2] not in GA_SCHEDULE_YEARS
        and out_changed
    )
    ut_2024_desc_only = (
        parsed is not None and parsed[1] == "UT" and parsed[2] == 2024 and out_changed
    )
    if out_changed and not out_ok:
        if ga_note_only and _ga_note_only_matches_pin(out, base[0]):
            pass  # DEPENDENT_EXEMPTION_NOTE text only; amounts match the pin
        elif ut_2024_desc_only and _ut_2024_rate_desc_only_matches_pin(out, base[0]):
            pass  # rate_description prose only; amounts match the pin
        else:
            problems.append(f"out {key}")
    if versions_changed and not versions_ok:
        problems.append(f"versions {key}")
    if full is not None and full_sha(raw) != full:
        problems.append(f"corrected {key}")
    return problems, out_changed, versions_changed, has_pointer


def test_the_fixture_pins_exactly_the_base_commits_calls() -> None:
    fixture = read_fixture()
    assert fixture["head"] == BASE_COMMIT
    base_rows = [
        s for s in load_catalog().statutes if s["id"] not in PRIOR_YEAR_IDS | NEW_SINCE_BASE_IDS
    ]
    assert set(fixture["pins"]) == set(plan(base_rows, SUPPORTED_STATE_JURISDICTIONS))
    assert len(fixture["pins"]) == 26_709


def test_every_corrected_answer_is_pinned_in_full() -> None:
    corrected = read_corrected()
    assert set(corrected) == {key for key in read_fixture()["pins"] if is_corrected(key)}
    assert len(corrected) == CORRECTED_COUNT
    assert all(len(sha) == 64 for sha in corrected.values())


async def test_no_answer_changes_outside_the_new_windows() -> None:
    pins: dict[str, list[str]] = read_fixture()["pins"]
    corrected = read_corrected()
    calls = plan(load_catalog().statutes, SUPPORTED_STATE_JURISDICTIONS)
    unexpected: list[str] = []
    changed_out = changed_versions = pointed = canada = 0
    ga_pre2019_amount_mismatches: list[str] = []
    ut_2024_amount_mismatches: list[str] = []
    async with Client(mcp) as client:
        for key in sorted(pins):
            raw = await answer(client, calls[key])
            problems, out_changed, versions_changed, has_pointer = _compare(
                key, raw, pins[key], corrected.get(key)
            )
            if problems and _lists_canada(key):
                with _without_canada():
                    base_raw = await answer(client, calls[key])
                problems = _compare(key, base_raw, pins[key], corrected.get(key))[0]
                canada += not problems
            unexpected += problems
            changed_out += out_changed
            changed_versions += versions_changed
            pointed += has_pointer
            parsed = _state_year(key)
            if parsed is not None and parsed[1] == "GA" and parsed[2] < 2018:
                out, _ = _without_pointer(raw)
                if not _ga_note_only_matches_pin(out, pins[key][0]):
                    ga_pre2019_amount_mismatches.append(key)
            if parsed is not None and parsed[1] == "UT" and parsed[2] == 2024:
                out, _ = _without_pointer(raw)
                if not _ut_2024_rate_desc_only_matches_pin(out, pins[key][0]):
                    ut_2024_amount_mismatches.append(key)
    assert unexpected == []
    # Pre-2018 GA answers may gain DEPENDENT_EXEMPTION_NOTE text only; amounts match pin.
    assert ga_pre2019_amount_mismatches == []
    # UT 2024 may change only in rate_description; amounts and other fields match pin.
    assert ut_2024_amount_mismatches == []
    # See CHANGED_OUT. Versions are unchanged at 2072.
    assert (changed_out, changed_versions) == (CHANGED_OUT, 2072)
    assert canada == CANADA_COUNT
    # Not-found lookups by id whose message names the row for that tax year.
    assert pointed == 288


# A corrected answer that finds a row, and a corrected not-found answer naming the 2024 row.
CORRECTED_FOUND = "lookup|pr-ley60-6020-10|PR|2026|en"
CORRECTED_POINTED = "lookup|pr-ley60-6020-10-2019|PR|2024|en"
# A not-found lookup by a current id, not corrected, whose message names the 2019 row.
POINTED = "lookup|pr-ley60-6020-03|PR|2019|en"


async def _answers(*keys: str) -> list[Any]:
    calls = plan(load_catalog().statutes, SUPPORTED_STATE_JURISDICTIONS)
    async with Client(mcp) as client:
        return [await answer(client, calls[key]) for key in keys]


def _flip(text: str, at: int) -> str:
    """``text`` with the character at ``at`` changed."""
    return text[:at] + ("y" if text[at] == "x" else "x") + text[at + 1 :]


def _problems(key: str, raw: Any) -> list[str]:
    return _compare(key, raw, read_fixture()["pins"][key], read_corrected().get(key))[0]


async def test_one_changed_byte_of_a_corrected_answer_fails_its_full_pin() -> None:
    found, pointed = await _answers(CORRECTED_FOUND, CORRECTED_POINTED)
    assert found["status"] == "success" and _problems(CORRECTED_FOUND, found) == []
    for change in (
        lambda d: d.update(title=_flip(d["title"], 0)),
        lambda d: d["cross_references"].append("x"),
        lambda d: d["available_versions"][-1].update(
            id=_flip(d["available_versions"][-1]["id"], 0)
        ),
    ):
        mutated = copy.deepcopy(found)
        change(mutated["data"])
        assert f"corrected {CORRECTED_FOUND}" in _problems(CORRECTED_FOUND, mutated)
    # The full pin covers the pointer sentence too: naming another row still fails.
    assert pointed["message"].endswith(" is answered by 'pr-ley60-6020-10-2024'.")
    assert _problems(CORRECTED_POINTED, pointed) == []
    mutated = {**pointed, "message": pointed["message"].replace("-2024'.", "-2025'.")}
    assert _problems(CORRECTED_POINTED, mutated) == [f"corrected {CORRECTED_POINTED}"]


async def test_only_the_pointer_sentence_at_the_end_of_a_not_found_message_is_dropped() -> None:
    (res,) = await _answers(POINTED)
    msg = res["message"]
    found = POINTER.search(msg)
    assert res["status"] == "not_found" and found is not None
    assert POINTED not in read_corrected() and _problems(POINTED, res) == []
    head, sentence = msg[: found.start()], msg[found.start() :]
    for changed in (
        {**res, "message": _flip(msg, 0)},  # other text changed
        {**res, "message": sentence.lstrip() + " " + head},  # sentence not at the end
        {**res, "message": msg + " "},  # text after the sentence
        {**res, "message": msg + sentence},  # the sentence twice
        {**res, "message": head + sentence.replace("That", "This")},  # another sentence
        {**res, "status": "error"},  # not a not-found answer
        {**res, "query": _flip(res["query"], 0)},  # another field
    ):
        assert _problems(POINTED, changed) == [f"out {POINTED}"], changed


BASE_CA_RTC_17041_SNIPPETS = {
    "en": (
        "Imposes graduated income tax rates on California taxable income ranging from 1% "
        "to 12.3%, plus a 1% Mental Health Services Tax surcharge on taxable income "
        "exceeding $1,000,000, creating a top marginal individual rate of 13.3%."
    ),
    "es": (
        "Grava el ingreso tributable de California con tasas escalonadas del 1% al 12.3%, "
        "más 1% de recargo para servicios de salud mental sobre ingresos mayores a $1M "
        "(Proposición 63), para una tasa marginal máxima de 13.3%."
    ),
}


async def test_new_since_base_searches_differ_only_by_the_inserted_row() -> None:
    """Each NEW_SINCE_BASE_SEARCHES answer is the base answer plus the new row, nothing else.

    Ranking is per row, so dropping NEW_SINCE_BASE_IDS from the ranked rows (and restoring
    the base snippet on ca-rtc-17041 where it enters the page) must give back the exact
    pinned base answer: same IDs, same order, same paging fields.
    """
    pins: dict[str, list[str]] = read_fixture()["pins"]
    calls = plan(load_catalog().statutes, SUPPORTED_STATE_JURISDICTIONS)
    keys = sorted(NEW_SINCE_BASE_SEARCHES)
    ranked = db.search_statutes

    def without_new_rows(*, limit: int = 5, offset: int = 0, **kw: Any) -> list[dict[str, Any]]:
        rows = ranked(**kw, limit=offset + limit + len(NEW_SINCE_BASE_IDS), offset=0)
        kept = [
            (
                {**r, "snippet": BASE_CA_RTC_17041_SNIPPETS[r["snippet_language"]]}
                if r["id"] == "ca-rtc-17041"
                else r
            )
            for r in rows
            if r["id"] not in NEW_SINCE_BASE_IDS
        ]
        return kept[offset : offset + limit]

    async with Client(mcp) as client:
        now = [_without_pointer(await answer(client, calls[k]))[0] for k in keys]
        with patch.object(db, "search_statutes", without_new_rows):
            base = [_without_pointer(await answer(client, calls[k]))[0] for k in keys]
    for key, current, rebuilt in zip(keys, now, base, strict=True):
        assert pin(current)[0] != pins[key][0], key
        assert pin(rebuilt) == pins[key], key
