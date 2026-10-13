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


@compliance_server.tool(
    name="evaluate_georgia_residency",
    description=(
        "Evaluate Georgia statutory residency criteria under O.C.G.A. § 48-7-1(10) "
        "and Ga. Comp. R. & Regs. r. 560-7-3-.01, and compute state income tax."
    ),
    annotations=ANNOTATION_READ_ONLY,
    tags={"compliance", "georgia", "residency", "read_only"},
)
def evaluate_georgia_residency(
    days_present_ga: int,
    is_domiciled_in_ga: bool = False,
    maintains_permanent_abode_ga: bool = False,
    federal_agi: float = 0.0,
    filing_status: str = "single",
    age: int = 40,
    retirement_income: float = 0.0,
    tax_year: int = 2024,
    ga_homestead_exemption_claimed: bool = False,
    ga_driver_license_active: bool = False,
    ga_voter_registration_active: bool = False,
) -> dict[str, Any]:
    """Evaluate Georgia domicile, 183-day presence, and state income tax liability."""
    if days_present_ga < 0 or days_present_ga > 366:
        raise SafetyViolationError("Day count must be between 0 and 366.")
    if federal_agi < 0:
        raise SafetyViolationError("Federal AGI cannot be negative.")
    if retirement_income < 0:
        raise SafetyViolationError("Retirement income cannot be negative.")

    # 1. Statutory Residency Tests under O.C.G.A. § 48-7-1(10)
    domicile_test = is_domiciled_in_ga
    presence_test = days_present_ga >= 183
    abode_test = maintains_permanent_abode_ga and days_present_ga > 0

    is_georgia_resident = domicile_test or presence_test or abode_test

    # 2. Domicile Abandonment Audit Risk Analysis (Ga. Comp. R. & Regs. r. 560-7-3-.01)
    audit_flags: list[str] = []
    if ga_homestead_exemption_claimed:
        audit_flags.append(
            "CRITICAL: Active Georgia homestead exemption under O.C.G.A. § 48-5-40 "
            "legally presumes Georgia domicile."
        )
    if ga_driver_license_active:
        audit_flags.append("Active Georgia driver's license indicates continuing residency ties.")
    if ga_voter_registration_active:
        audit_flags.append(
            "Active Georgia voter registration is probative evidence of continuing domicile."
        )

    # 3. Tax Calculation under HB 1023 / HB 1015 (O.C.G.A. § 48-7-20 & § 48-7-27)
    is_mfj = filing_status.strip().lower() in ("married_joint", "mfj", "married filing jointly")
    standard_deduction = 24000.0 if is_mfj else 12000.0

    # Retirement Income Exclusion (O.C.G.A. § 48-7-27)
    max_exclusion = 0.0
    if age >= 65:
        max_exclusion = 65000.0
    elif age >= 62:
        max_exclusion = 35000.0
    retirement_exclusion_applied = min(retirement_income, max_exclusion)

    # Tax rate selection
    if tax_year >= 2026:
        tax_rate = 0.0499
    elif tax_year == 2025:
        tax_rate = 0.0539
    else:
        tax_rate = 0.0549

    taxable_income = max(0.0, federal_agi - standard_deduction - retirement_exclusion_applied)
    estimated_tax = round(taxable_income * tax_rate, 2) if is_georgia_resident else 0.0

    return {
        "status": "success",
        "tax_year": tax_year,
        "is_georgia_resident": is_georgia_resident,
        "residency_tests": {
            "domicile_test_satisfied": domicile_test,
            "183_day_presence_test_satisfied": presence_test,
            "permanent_abode_test_satisfied": abode_test,
            "days_present_ga": days_present_ga,
            "statutory_basis": "O.C.G.A. § 48-7-1(10); Ga. Comp. R. & Regs. r. 560-7-3-.01",
        },
        "domicile_audit_risk": {
            "risk_level": "high" if audit_flags else "standard",
            "flags": audit_flags,
            "abandonment_burden_of_proof": (
                "Under Ga. Comp. R. & Regs. r. 560-7-3-.01, a Georgia domicile continues "
                "until the taxpayer establishes permanent abandonment and relocation elsewhere."
            ),
        },
        "tax_computation": {
            "applicable_rate": tax_rate,
            "standard_deduction": standard_deduction,
            "retirement_exclusion_applied": retirement_exclusion_applied,
            "georgia_taxable_net_income": taxable_income,
            "estimated_georgia_tax": estimated_tax,
            "statutory_rate_authority": "O.C.G.A. § 48-7-20 (HB 1023 / HB 1015 flat tax)",
        },
    }


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
