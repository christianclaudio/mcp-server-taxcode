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
import re
from collections.abc import Collection
from typing import Any

from fastmcp import Client

from scripts.build_corrected_pins import full_sha, read_corrected
from scripts.build_prior_years_pins import BASE_COMMIT, answer, pin, plan, read_fixture
from taxcode_mcp.catalog import load_catalog
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
# Resource reads that newly resolve after the {section*} template fix (issue #41).
# Base pins recorded Unknown resource / not found; content is unchanged statute text.
SLASH_RESOURCES = frozenset({"resource|IL|5/201", "resource|IL|5/301", "resource|IL|5/1501(a)(20)"})
# Answers pinned in full in fixtures/prior_years/corrected_answers.json: 180 lookups by a
# corrected row's id, 64 by its citation or bare section, 9 searches and 1 resource read.
CORRECTED_COUNT = 254


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


# State residency answers that intentionally change with this PR's rate-model work
# (AR DFA brackets replacing the 65% middle-rate stand-in; CA/CT/MD/NJ/NY whole-income
# estimate labels). Their out hashes may differ from the f4f0d5d pin.
RATE_MODEL_CHANGED = frozenset({"AR", "CA", "CT", "MD", "NJ", "NY"})


def _allowed(key: str) -> tuple[bool, bool]:
    """Whether a key's (out, versions) hash may change."""
    if key == "tools|health":
        return True, False
    kind, *rest = key.split("|")
    if kind == "state" and rest[0] in RATE_MODEL_CHANGED:
        return True, False
    if kind == "search":
        year = rest[2]
        return (year != "None" and 2019 <= int(year) <= 2025) or is_corrected(key), False
    if kind == "resource":
        return key in CORRECTED_RESOURCES or key in SLASH_RESOURCES, False
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


def _compare(
    key: str, raw: Any, base: list[str], full: str | None
) -> tuple[list[str], bool, bool, bool]:
    """One answer against its pins: problems, whether out / versions changed, pointer seen."""
    out, has_pointer = _without_pointer(raw)
    now = pin(out)
    out_ok, versions_ok = _allowed(key)
    out_changed, versions_changed = now[0] != base[0], now[1] != base[1]
    problems: list[str] = []
    if out_changed and not out_ok:
        problems.append(f"out {key}")
    if versions_changed and not versions_ok:
        problems.append(f"versions {key}")
    if full is not None and full_sha(raw) != full:
        problems.append(f"corrected {key}")
    return problems, out_changed, versions_changed, has_pointer


def test_the_fixture_pins_exactly_the_base_commits_calls() -> None:
    fixture = read_fixture()
    assert fixture["head"] == BASE_COMMIT
    base_rows = [s for s in load_catalog().statutes if s["id"] not in PRIOR_YEAR_IDS]
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
    changed_out = changed_versions = pointed = 0
    async with Client(mcp) as client:
        for key in sorted(pins):
            raw = await answer(client, calls[key])
            problems, out_changed, versions_changed, has_pointer = _compare(
                key, raw, pins[key], corrected.get(key)
            )
            unexpected += problems
            changed_out += out_changed
            changed_versions += versions_changed
            pointed += has_pointer
    assert unexpected == []
    # 280 US and 264 PR in-window lookups (240, plus 24 for 6020.10 in 2022-2024), 52 lookups
    # of the corrected row, 277 window-year searches, 9 searches listing the corrected row, the
    # health check, 64 lookups of the two rows whose (a)(4) was corrected (24 by id, 40 by
    # citation or bare section), the resource read of 6020.10, 3 Illinois resource reads that
    # newly resolve once {section*} matches slash section ids (#41), and 528 state-residency
    # answers for AR/CA/CT/MD/NJ/NY whose rate model or ESTIMATE label changed (#29).
    assert (changed_out, changed_versions) == (1479, 2072)
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
