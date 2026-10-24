"""Every rate or amount a ``rate_description`` states comes from that state's rate table.

The description is prose a client shows next to the computed tax, so a number in it that
the formula does not use is a drift between what the tool says and what it computes.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import pytest

from taxcode_mcp.tools import compliance

_PERCENT = re.compile(r"(\d+(?:\.\d+)?)%")
_DOLLARS = re.compile(r"\$(\d[\d,]*(?:\.\d+)?)(k?)")

# Figures the prose states that no formula reads, with the reason each may stay.
NOT_IN_TABLE: dict[str, set[float]] = {
    "CA": {1.0},  # the 1% Mental Health Services Tax, already inside the 13.3% top rate
    "MD": {2.25, 3.2},  # the county piggyback range; county tax is not computed
    "NH": {3.0},  # the 2024 interest and dividends tax, which this tool does not compute
}
# The registry's labeled stand-in states its own factor.
ESTIMATE_FACTOR = 65.0


def _leaves(node: Any) -> Iterator[float]:
    if isinstance(node, dict):
        for value in node.values():
            yield from _leaves(value)
    elif isinstance(node, list):
        for value in node:
            yield from _leaves(value)
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        yield float(node)


def _stated(text: str) -> list[tuple[str, float]]:
    found = [("rate", float(m.group(1))) for m in _PERCENT.finditer(text)]
    for m in _DOLLARS.finditer(text):
        amount = float(m.group(1).replace(",", ""))
        found.append(("amount", amount * 1000 if m.group(2) else amount))
    return found


def _descriptions(code: str) -> Iterator[tuple[int, str, str]]:
    for tax_year in range(2018, 2028):
        for filing_status in ("single", "married_joint"):
            result = compliance.evaluate_state_residency(
                jurisdiction=code,
                days_in_state=300,
                is_domiciled=True,
                maintains_permanent_abode=True,
                federal_agi=150000.0,
                capital_gains=400000.0,
                filing_status=filing_status,
                tax_year=tax_year,
            )
            yield tax_year, filing_status, result["tax_computation"]["rate_description"]


@pytest.mark.parametrize("code", sorted(compliance.SUPPORTED_STATE_JURISDICTIONS))
def test_rate_description_matches_the_rate_table(code: str) -> None:
    table = compliance.RATE_TABLES.get(code, {})
    leaves = list(_leaves(table))
    rates = {round(v * 100, 6) for v in leaves if 0 < v < 1} | {0.0}
    amounts = {round(v, 2) for v in leaves}
    allowed = NOT_IN_TABLE.get(code, set())
    drift = []
    for tax_year, status, text in _descriptions(code):
        for kind, value in _stated(text):
            if value in allowed:
                continue
            if kind == "rate" and value == ESTIMATE_FACTOR and "ESTIMATE" in text:
                continue
            if value not in (rates if kind == "rate" else amounts):
                drift.append(f"{tax_year} {status}: {kind} {value:g} in {text!r}")
    assert not drift, f"{code} prose states figures its rate table does not hold:\n" + "\n".join(
        sorted(set(drift))
    )
