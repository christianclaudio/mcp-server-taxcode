"""Tax compliance, residency evaluation, and capital gains sourcing tools."""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from taxcode_mcp.errors import SafetyViolationError, tool_error

logger = logging.getLogger(__name__)

BILINGUAL_DISCLAIMER: dict[str, str] = {
    "en": (
        "NOT TAX OR LEGAL ADVICE. This computational determination is for reference and "
        "informational purposes only based on statutory heuristic models. It does not constitute "
        "formal legal or tax advice, an official opinion, or an audit defense. Any translations "
        "or statutory summaries are unofficial and non-binding; official enacted statutory text "
        "and administrative rulings in their original language control."
    ),
    "es": (
        "NO CONSTITUYE ASESORAMIENTO CONTRIBUTIVO NI LEGAL. Esta determinación computacional es "
        "para fines informativos y de referencia únicamente, basada en modelos heurísticos "
        "estatutarios. No constituye asesoramiento legal o contributivo formal, opinión oficial "
        "ni defensa de auditoría. Todas las traducciones o resúmenes estatutarios son no "
        "oficiales y no vinculantes; el texto estatutario oficial promulgado y las determinaciones "
        "administrativas en su idioma original prevalecen."
    ),
}

CONTENT_NOTICE = "Computational heuristic model with unofficial statutory reference summaries."

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
        "disclaimer": BILINGUAL_DISCLAIMER,
        "content_notice": CONTENT_NOTICE,
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
            "disclaimer": BILINGUAL_DISCLAIMER,
            "content_notice": CONTENT_NOTICE,
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
        "disclaimer": BILINGUAL_DISCLAIMER,
        "content_notice": CONTENT_NOTICE,
    }


SUPPORTED_STATE_JURISDICTIONS = frozenset(
    {"GA", "NY", "CA", "FL", "TX", "WA", "MA", "NJ", "IL", "PA", "CT", "NC"}
)


@compliance_server.tool(
    name="evaluate_state_residency",
    description=(
        "Evaluate individual statutory residency, domicile nexus, and state tax liability "
        "across key migration corridors: GA, NY, CA, FL, TX, WA, MA, NJ, IL, PA, CT, and NC."
    ),
    annotations=ANNOTATION_READ_ONLY,
    tags={"compliance", "states", "residency", "read_only"},
)
def evaluate_state_residency(
    jurisdiction: str,
    days_in_state: int,
    is_domiciled: bool = False,
    maintains_permanent_abode: bool = False,
    federal_agi: float = 0.0,
    filing_status: str = "single",
    age: int = 40,
    retirement_income: float = 0.0,
    capital_gains: float = 0.0,
    tax_year: int = 2024,
    has_active_homestead_exemption: bool = False,
    has_active_driver_license: bool = False,
    has_active_voter_registration: bool = False,
    telecommuting_days_out_of_state: int = 0,
) -> dict[str, Any]:
    """Evaluate state domicile, presence day counts, and statutory state tax liability."""
    state = jurisdiction.strip().upper()
    if state not in SUPPORTED_STATE_JURISDICTIONS:
        raise SafetyViolationError(
            f"Unsupported jurisdiction '{jurisdiction}'. Supported states: "
            f"{', '.join(sorted(SUPPORTED_STATE_JURISDICTIONS))}."
        )
    if days_in_state < 0 or days_in_state > 366:
        raise SafetyViolationError("Day count must be between 0 and 366.")
    if federal_agi < 0:
        raise SafetyViolationError("Federal AGI cannot be negative.")
    if retirement_income < 0:
        raise SafetyViolationError("Retirement income cannot be negative.")
    if capital_gains < 0:
        raise SafetyViolationError("Capital gains cannot be negative.")
    if telecommuting_days_out_of_state < 0:
        raise SafetyViolationError("Telecommuting day count cannot be negative.")

    is_mfj = filing_status.strip().lower() in ("married_joint", "mfj", "married filing jointly")

    # Common domicile audit risk factors
    audit_flags: list[str] = []
    if has_active_homestead_exemption:
        audit_flags.append(
            f"CRITICAL: Active homestead exemption in {state} legally presumes local domicile."
        )
    if has_active_driver_license:
        audit_flags.append(f"Active {state} driver's license indicates continuing residency ties.")
    if has_active_voter_registration:
        audit_flags.append(
            f"Active {state} voter registration is probative evidence of continuing domicile."
        )

    # State-specific statutory determination
    is_resident = False
    statutory_basis = ""
    rate_authority = ""
    rate_desc = ""
    taxable_income = 0.0
    estimated_tax = 0.0

    if state == "GA":
        statutory_basis = "O.C.G.A. § 48-7-1(10); Ga. Comp. R. & Regs. r. 560-7-3-.01"
        is_resident = (
            is_domiciled
            or days_in_state >= 183
            or (maintains_permanent_abode and days_in_state > 0)
        )
        standard_ded = 24000.0 if is_mfj else 12000.0
        max_ret = 65000.0 if age >= 65 else (35000.0 if age >= 62 else 0.0)
        ret_excl = min(retirement_income, max_ret)
        rate = 0.0499 if tax_year >= 2026 else (0.0539 if tax_year == 2025 else 0.0549)
        rate_desc = f"Flat {rate * 100:.2f}% income tax (HB 1023 / HB 1015)"
        rate_authority = "O.C.G.A. § 48-7-20"
        taxable_income = max(0.0, federal_agi - standard_ded - ret_excl)
        estimated_tax = round(taxable_income * rate, 2) if is_resident else 0.0

    elif state == "NY":
        statutory_basis = "NY Tax Law § 605(b); 20 NYCRR § 105.20"
        statutory_res = maintains_permanent_abode and days_in_state > 183
        is_resident = is_domiciled or statutory_res
        if not is_resident and telecommuting_days_out_of_state > 0:
            audit_flags.append(
                "NY Convenience of the Employer Rule (20 NYCRR § 132.18): Telecommuting days "
                "outside NY for a NY employer are sourced to NY unless for employer necessity."
            )
        rate_authority = "NY Tax Law § 601"
        rate_desc = "Graduated brackets from 4.0% to 10.9%"
        std_ded = 16050.0 if is_mfj else 8000.0
        taxable_income = max(0.0, federal_agi - std_ded)
        if is_resident:
            if taxable_income > 5000000:
                estimated_tax = round(taxable_income * 0.109, 2)
            elif taxable_income > 1077550:
                estimated_tax = round(taxable_income * 0.0965, 2)
            elif taxable_income > 215400:
                estimated_tax = round(taxable_income * 0.0685, 2)
            elif taxable_income > 80650:
                estimated_tax = round(taxable_income * 0.060, 2)
            else:
                estimated_tax = round(taxable_income * 0.055, 2)

    elif state == "CA":
        statutory_basis = "Cal. Rev. & Tax Code § 17014, § 17016; 18 CCR § 17014"
        presumption_9mo = days_in_state > 270
        if presumption_9mo:
            audit_flags.append(
                "CA RTC § 17016: Presence exceeding 9 months establishes statutory presumption "
                "of California residence."
            )
        is_resident = (
            is_domiciled or presumption_9mo or (days_in_state > 183 and maintains_permanent_abode)
        )
        rate_authority = "Cal. Rev. & Tax Code § 17041"
        rate_desc = "Graduated brackets up to 12.3% plus 1% Mental Health Services Tax (13.3%)"
        std_ded = 10726.0 if is_mfj else 5363.0
        taxable_income = max(0.0, federal_agi - std_ded)
        if is_resident:
            if taxable_income > 1000000:
                estimated_tax = round(taxable_income * 0.133, 2)
            elif taxable_income > 349137:
                estimated_tax = round(taxable_income * 0.113, 2)
            elif taxable_income > 68350:
                estimated_tax = round(taxable_income * 0.093, 2)
            else:
                estimated_tax = round(taxable_income * 0.060, 2)

    elif state == "FL":
        statutory_basis = "Fla. Const. Art. VII § 5; Fla. Stat. § 222.17"
        is_resident = is_domiciled or (maintains_permanent_abode and days_in_state >= 183)
        rate_authority = "Fla. Const. Art. VII § 5"
        rate_desc = "0.0% (Constitutional prohibition of personal income tax)"
        taxable_income = 0.0
        estimated_tax = 0.0

    elif state == "TX":
        statutory_basis = "Tex. Const. art. VIII § 24-a; Tex. Tax Code § 171.001"
        is_resident = is_domiciled or (maintains_permanent_abode and days_in_state >= 183)
        rate_authority = "Tex. Const. art. VIII § 24-a"
        rate_desc = "0.0% (Constitutional prohibition of personal income tax)"
        taxable_income = 0.0
        estimated_tax = 0.0

    elif state == "WA":
        statutory_basis = "Wash. Const. art. VII § 1; RCW 82.87.040"
        is_resident = is_domiciled or (maintains_permanent_abode and days_in_state >= 183)
        rate_authority = "RCW 82.87.040"
        rate_desc = "0.0% on ordinary income; 7.0% excise tax on capital gains over deduction"
        cap_gain_ded = 262000.0
        taxable_income = max(0.0, capital_gains - cap_gain_ded)
        estimated_tax = round(taxable_income * 0.07, 2) if is_resident else 0.0

    elif state == "MA":
        statutory_basis = "M.G.L. c. 62 § 1(f), § 4; Mass. Const. amend. art. XLIV"
        is_resident = is_domiciled or (maintains_permanent_abode and days_in_state > 183)
        rate_authority = "M.G.L. c. 62 § 4 & Fair Share Amendment"
        rate_desc = "5.0% Part B income tax plus 4.0% Fair Share surtax on income > $1,000,000"
        std_ded = 8800.0 if is_mfj else 4400.0
        taxable_income = max(0.0, federal_agi - std_ded)
        if is_resident:
            base_tax = taxable_income * 0.05
            surtax = max(0.0, taxable_income - 1000000.0) * 0.04
            estimated_tax = round(base_tax + surtax, 2)

    elif state == "NJ":
        statutory_basis = "N.J.S.A. 54A:1-2, 54A:2-1; N.J.A.C. 18:35-5.2"
        is_resident = is_domiciled or (maintains_permanent_abode and days_in_state > 183)
        if not is_resident and telecommuting_days_out_of_state > 0:
            audit_flags.append(
                "NJ Reciprocal Convenience Rule (P.L. 2023 c. 125): Telecommuting days outside "
                "NJ for a NJ employer are sourced to NJ if employee home state applies a "
                "convenience rule."
            )
        rate_authority = "N.J.S.A. 54A:2-1"
        rate_desc = "Graduated brackets from 1.4% to 10.75%"
        exemption = 2000.0 if is_mfj else 1000.0
        taxable_income = max(0.0, federal_agi - exemption)
        if is_resident:
            if taxable_income > 1000000:
                estimated_tax = round(taxable_income * 0.1075, 2)
            elif taxable_income > 500000:
                estimated_tax = round(taxable_income * 0.0897, 2)
            elif taxable_income > 75000:
                estimated_tax = round(taxable_income * 0.0637, 2)
            else:
                estimated_tax = round(taxable_income * 0.035, 2)

    elif state == "IL":
        statutory_basis = "35 ILCS 5/201, 5/1501(a)(20), 5/301"
        is_resident = is_domiciled or (days_in_state >= 183 and maintains_permanent_abode)
        rate_authority = "35 ILCS 5/201"
        rate_desc = "Constitutional flat 4.95% rate"
        exemption = 5550.0 if is_mfj else 2775.0
        taxable_income = max(0.0, federal_agi - exemption)
        estimated_tax = round(taxable_income * 0.0495, 2) if is_resident else 0.0

    elif state == "PA":
        statutory_basis = "72 P.S. § 7301, 72 P.S. § 7302; 61 Pa. Code § 109.8"
        is_resident = is_domiciled or (maintains_permanent_abode and days_in_state > 183)
        if not is_resident and telecommuting_days_out_of_state > 0:
            audit_flags.append(
                "PA Convenience of the Employer Rule (61 Pa. Code § 109.8): Wages earned while "
                "telecommuting for a Pennsylvania employer are sourced to PA unless remote work "
                "is for the employer's necessity."
            )
        rate_authority = "72 P.S. § 7302"
        rate_desc = "Constitutional flat 3.07% rate"
        taxable_income = max(0.0, federal_agi)
        estimated_tax = round(taxable_income * 0.0307, 2) if is_resident else 0.0

    elif state == "CT":
        statutory_basis = "Conn. Gen. Stat. §§ 12-700, 12-701, 12-711"
        is_resident = is_domiciled or (maintains_permanent_abode and days_in_state > 183)
        if not is_resident and telecommuting_days_out_of_state > 0:
            audit_flags.append(
                "CT Reciprocal Convenience Rule (Conn. Gen. Stat. § 12-711): Nonresident "
                "telecommuting wages are sourced to CT if employee's resident state applies "
                "a convenience rule."
            )
        rate_authority = "Conn. Gen. Stat. § 12-700"
        rate_desc = "Graduated brackets from 3.0% to 6.99%"
        # Standard exemption phase-out model
        exemption = 24000.0 if is_mfj else 15000.0
        taxable_income = max(0.0, federal_agi - exemption)
        if is_resident:
            if taxable_income > 500000:
                estimated_tax = round(taxable_income * 0.0699, 2)
            elif taxable_income > 100000:
                estimated_tax = round(taxable_income * 0.06, 2)
            elif taxable_income > 50000:
                estimated_tax = round(taxable_income * 0.05, 2)
            else:
                estimated_tax = round(taxable_income * 0.03, 2)

    else:  # NC
        statutory_basis = "N.C. Gen. Stat. §§ 105-153.3, 105-153.7"
        is_resident = is_domiciled or (days_in_state > 183)
        rate_authority = "N.C. Gen. Stat. § 105-153.7"
        if tax_year >= 2026:
            nc_rate = 0.0399
            rate_desc = "Flat 3.99% rate (2026+ statutory phase-in)"
        elif tax_year == 2025:
            nc_rate = 0.0425
            rate_desc = "Flat 4.25% rate (2025 statutory rate)"
        else:
            nc_rate = 0.045
            rate_desc = "Flat 4.5% rate (2024 statutory rate)"
        exemption = 25500.0 if is_mfj else 12750.0
        taxable_income = max(0.0, federal_agi - exemption)
        estimated_tax = round(taxable_income * nc_rate, 2) if is_resident else 0.0

    return {
        "status": "success",
        "jurisdiction": state,
        "tax_year": tax_year,
        "is_resident": is_resident,
        "residency_tests": {
            "domicile_satisfied": is_domiciled,
            "days_in_state": days_in_state,
            "maintains_permanent_abode": maintains_permanent_abode,
            "statutory_basis": statutory_basis,
        },
        "domicile_audit_risk": {
            "risk_level": "high" if audit_flags else "standard",
            "flags": audit_flags,
        },
        "tax_computation": {
            "taxable_net_income": taxable_income,
            "estimated_state_tax": estimated_tax,
            "rate_description": rate_desc,
            "statutory_rate_authority": rate_authority,
        },
        "disclaimer": BILINGUAL_DISCLAIMER,
        "content_notice": CONTENT_NOTICE,
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
