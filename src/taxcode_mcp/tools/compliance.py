"""Tax compliance, residency evaluation, and capital gains sourcing tools."""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from taxcode_mcp.errors import SafetyViolationError, tool_error

logger = logging.getLogger(__name__)

ANNOTATION_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

compliance_server = FastMCP(
    "taxcode-compliance",
    instructions="Deterministic cross-border tax calculations and residency determinations.",
)


@compliance_server.tool(
    name="evaluate_pr_bona_fide_residency",
    description=(
        "Evaluate statutory criteria for PR bona fide residency under "
        "IRC § 937 and 26 CFR § 1.937-1."
    ),
    annotations=ANNOTATION_READ_ONLY,
    tags={"compliance", "residency", "read_only"},
)
def evaluate_pr_bona_fide_residency(
    days_present_pr: int,
    days_present_us: int,
    prior_year_1_pr_days: int = 0,
    prior_year_2_pr_days: int = 0,
    has_us_tax_home: bool = False,
    closer_connection_jurisdiction: str = "PR",
) -> dict[str, Any]:
    """Evaluate IRC § 937 presence, tax home, and closer connection tests."""
    if days_present_pr < 0 or days_present_us < 0:
        raise SafetyViolationError("Day counts cannot be negative.")

    # 1. Presence Test alternatives under 26 CFR § 1.937-1(c)
    pass_183_days = days_present_pr >= 183
    three_year_sum = days_present_pr + prior_year_1_pr_days + prior_year_2_pr_days
    pass_549_days = (
        three_year_sum >= 549
        and days_present_pr >= 60
        and prior_year_1_pr_days >= 60
        and prior_year_2_pr_days >= 60
    )
    pass_90_day_us = days_present_us <= 90
    presence_satisfied = pass_183_days or pass_549_days or pass_90_day_us

    # 2. Tax Home Test (cannot maintain a US tax home during the year)
    tax_home_satisfied = not has_us_tax_home

    # 3. Closer Connection Test (must have closer economic/social ties to PR than US)
    closer_connection_satisfied = closer_connection_jurisdiction.strip().upper() == "PR"

    # Overall statutory bona fide residency determination
    is_bona_fide = presence_satisfied and tax_home_satisfied and closer_connection_satisfied

    # Regulatory guidance and reporting triggers
    reporting_triggers = [
        "IRS Form 8898 (Beginning/Ending Bona Fide Residence) required if worldwide income > $75k",
        "PR Form 480.20 (Individual Income Tax Return / Planilla) required for PR income",
    ]
    if not is_bona_fide:
        reporting_triggers.append(
            "Caution: Worldwide income remains subject to full US taxation "
            "under IRC § 1 without § 933 exclusion"
        )

    return {
        "status": "success",
        "is_bona_fide_resident": is_bona_fide,
        "presence_test": {
            "satisfied": presence_satisfied,
            "rule_183_days_satisfied": pass_183_days,
            "rule_549_days_3_year_satisfied": pass_549_days,
            "rule_90_days_us_limit_satisfied": pass_90_day_us,
            "days_present_pr": days_present_pr,
            "days_present_us": days_present_us,
            "three_year_cumulative_pr_days": three_year_sum,
        },
        "tax_home_test": {
            "satisfied": tax_home_satisfied,
            "has_us_tax_home": has_us_tax_home,
            "statutory_basis": "26 U.S.C. § 937(a)(2); 26 CFR § 1.937-1(d)",
        },
        "closer_connection_test": {
            "satisfied": closer_connection_satisfied,
            "jurisdiction": closer_connection_jurisdiction.strip().upper(),
            "statutory_basis": "26 U.S.C. § 937(a)(2); 26 CFR § 1.937-1(e)",
        },
        "authoritative_basis": "26 U.S.C. § 937; 26 CFR § 1.937-1",
        "reporting_requirements": reporting_triggers,
    }


@compliance_server.tool(
    name="calculate_built_in_gain_sourcing",
    description=(
        "Calculate US vs PR capital gain sourcing under Treas. Reg. § 1.937-2(f) (10-year rule)."
    ),
    annotations=ANNOTATION_READ_ONLY,
    tags={"compliance", "sourcing", "read_only"},
)
def calculate_built_in_gain_sourcing(
    is_marketable: bool,
    total_gain: float,
    fmv_on_residency_date: float | None = None,
    adjusted_basis: float | None = None,
    days_held_as_resident: int | None = None,
    total_days_held: int | None = None,
    years_since_residency: float = 0.0,
) -> dict[str, Any]:
    """Calculate pre-move built-in gain vs post-move appreciation allocations."""
    try:
        if total_gain < 0:
            raise SafetyViolationError("Total gain cannot be negative.")

        # If resident for >= 10 continuous years, the built-in gain lookback rule expires
        if years_since_residency >= 10.0:
            return {
                "status": "success",
                "lookback_expired": True,
                "years_since_residency": years_since_residency,
                "us_source_gain": 0.0,
                "pr_source_gain": total_gain,
                "allocation_method": "10-year lookback expired under 26 CFR § 1.937-2(f)",
                "applicable_federal_treatment": (
                    "100% PR-source; excluded from gross income under IRC § 933"
                ),
                "applicable_pr_treatment": "Subject to Act 60 capital gains rate (0% or 4%)",
            }

        if is_marketable:
            # Marketable securities rule under 26 CFR § 1.937-2(f)(1)(vi)
            if fmv_on_residency_date is None or adjusted_basis is None:
                raise SafetyViolationError(
                    "Marketable securities allocation requires "
                    "fmv_on_residency_date and adjusted_basis."
                )
            if fmv_on_residency_date < adjusted_basis:
                us_gain = 0.0
            else:
                us_gain = min(total_gain, round(fmv_on_residency_date - adjusted_basis, 2))
            pr_gain = round(max(0.0, total_gain - us_gain), 2)
            method = "Marketable securities election: FMV on residency date allocation"
        else:
            # Non-marketable property linear apportionment rule
            if days_held_as_resident is None or total_days_held is None or total_days_held <= 0:
                raise SafetyViolationError(
                    "Non-marketable property allocation requires positive "
                    "days_held_as_resident and total_days_held."
                )
            ratio = min(1.0, max(0.0, days_held_as_resident / total_days_held))
            pr_gain = round(total_gain * ratio, 2)
            us_gain = round(max(0.0, total_gain - pr_gain), 2)
            method = "Non-marketable property: linear holding period time apportionment"

        return {
            "status": "success",
            "lookback_expired": False,
            "years_since_residency": years_since_residency,
            "total_gain": total_gain,
            "us_source_gain": us_gain,
            "pr_source_gain": pr_gain,
            "allocation_method": method,
            "federal_tax_treatment": (
                f"US-source gain (${us_gain:,.2f}) taxed at federal rates (up to 20% + 3.8% NIIT). "
                f"PR-source gain (${pr_gain:,.2f}) excluded under IRC § 933."
            ),
            "puerto_rico_tax_treatment": (
                f"PR-source gain (${pr_gain:,.2f}) eligible for Act 60 preferential rate "
                "(0% grandfathered, or 4% for post-2026 decrees)."
            ),
            "statutory_basis": "26 CFR § 1.937-2(f); 26 U.S.C. § 865; 26 U.S.C. § 933",
        }
    except Exception as exc:
        failure = tool_error(exc)
    raise failure from None


@compliance_server.prompt("cross_border_tax_analysis")
def cross_border_tax_analysis(taxpayer_scenario: str) -> str:
    """Generate structured analytical prompt for dual-jurisdiction US and PR tax analysis."""
    return f"""You are an elite cross-border tax attorney and computational legal analyst.
Analyze the following taxpayer scenario involving US federal tax law and Puerto Rico statutes:

SCENARIO:
{taxpayer_scenario}

ANALYTICAL FRAMEWORK:
1. Determine bona fide residency under 26 U.S.C. § 937 and 26 CFR § 1.937-1.
2. Evaluate applicability of the federal income exclusion under 26 U.S.C. § 933.
3. Sourcing determination: Apply 26 U.S.C. § 937(b) and 26 CFR § 1.937-2 (10-year rule).
4. Local PR tax incentives: Apply Puerto Rico Incentives Code (Ley 60-2019, Chapter 2/3).
5. Federal filing obligations: Assess required IRS reporting (Form 8898, Form 1040, Form 8960).

Cite all operative code sections and regulations precisely.
"""
