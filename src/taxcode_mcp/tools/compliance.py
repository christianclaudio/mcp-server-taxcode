"""Tax compliance, residency evaluation, and capital gains sourcing tools."""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from taxcode_mcp.catalog import RATE_TABLE_MODELS, load_rate_tables
from taxcode_mcp.disclaimers import BILINGUAL_DISCLAIMER, CONTENT_NOTICE, ESTIMATE_NOTE
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
        "IRC § 937 and 26 CFR § 1.937-1. Its reporting_requirements say when IRS "
        "Form 8898 must be filed: in the tax year you became or stopped being a bona fide "
        "PR resident, if your worldwide gross income is more than $75,000 "
        "(IRS Instructions for Form 8898)."
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
    tax_year: int | None = None,
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

    # Regulatory guidance and reporting triggers. Form 8898 threshold (worldwide
    # gross income more than $75,000 in the year residence begins or ends):
    # https://www.irs.gov/forms-pubs/about-form-8898 (Instructions, Who Must File).
    reporting_triggers = [
        "Conditional: IRS Form 8898 (Beginning/Ending Bona Fide Residence) is required only "
        "if you became or stopped being a bona fide PR resident in this tax year and your "
        "worldwide gross income > $75k",
        "PR Form 480.20 (Individual Income Tax Return / Planilla) required for PR income",
    ]
    if not is_bona_fide:
        reporting_triggers.append(
            "Caution: Worldwide income remains subject to full US taxation "
            "under IRC § 1 without § 933 exclusion"
        )

    return {
        "status": "success",
        "tax_year": tax_year,
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
                "applicable_pr_treatment": (
                    "Net long-term capital gain attributable to pre-residency appreciation and "
                    "recognized after 10 years of residency is taxed at 5% (Section 2022.02) "
                    "if recognized before Jan 1, 2036 (applications filed through 2026) or "
                    "before Jan 1, 2056 (applications filed from 2027)"
                ),
                "applicable_pr_treatment_es": (
                    "La ganancia neta de capital a largo plazo atribuible a apreciación previa a "
                    "la residencia y reconocida luego de 10 años de residencia tributa al 5% "
                    "(Sección 2022.02) si se reconoce antes del 1 de enero de 2036 (solicitudes "
                    "radicadas hasta 2026) o antes del 1 de enero de 2056 (solicitudes radicadas "
                    "desde 2027)"
                ),
                "disclaimer": BILINGUAL_DISCLAIMER,
                "content_notice": CONTENT_NOTICE,
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


# Georgia's flat rate applies from tax year 2024 (O.C.G.A. § 48-7-20(a.1), as amended by
# HB 1015 (2024): "for taxable years beginning on or after January 1, 2024"). Earlier years
# were taxed on graduated brackets that the rate table does not hold, so an earlier tax year
# gets the table's oldest step, labeled as an estimate.
GA_FLAT_RATE_FIRST_YEAR = 2024
GA_PRE_FLAT_ESTIMATE_NOTE = (
    " [ESTIMATE: Georgia's flat rate starts with tax year 2024; earlier years were taxed on "
    "graduated brackets this table does not hold, so the 2024 rate is used]"
)


def _ga_estimate_note(tax_year: int) -> str:
    """The estimate label for a tax year before Georgia's flat rate; empty from 2024 on."""
    return GA_PRE_FLAT_ESTIMATE_NOTE if tax_year < GA_FLAT_RATE_FIRST_YEAR else ""


def _ga_retirement_cap(ga: dict[str, Any], age: int) -> float:
    """Georgia's retirement income exclusion cap for an age; 0.0 below every listed age."""
    return float(
        next((s["amount"] for s in ga["retirement_exclusion"] if age >= s["min_age"]), 0.0)
    )


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

    # 3. Tax Calculation (O.C.G.A. § 48-7-20 & § 48-7-27), citing the law that set the year's rate
    is_mfj = filing_status.strip().lower() in ("married_joint", "mfj", "married filing jointly")
    ga = RATE_TABLES["GA"]
    standard_deduction = float(ga["standard_deduction"][_status(is_mfj)])

    # Retirement Income Exclusion (O.C.G.A. § 48-7-27)
    max_exclusion = _ga_retirement_cap(ga, age)
    retirement_exclusion_applied = min(retirement_income, max_exclusion)

    # Tax rate selection
    ga_step = _year_step(ga["rates"], tax_year)
    tax_rate = float(ga_step["rate"])

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
            "is_estimate": True,
            "estimate_note": ESTIMATE_NOTE,
            "statutory_rate_authority": (
                f"O.C.G.A. § 48-7-20 (flat tax, {ga_step['law']}){_ga_estimate_note(tax_year)}"
            ),
        },
        "disclaimer": BILINGUAL_DISCLAIMER,
        "content_notice": CONTENT_NOTICE,
    }


# Rate tables (deductions, brackets, rates, per-year values) live in
# ``taxcode_mcp/data/rates/<code>.json`` and are validated on load; see
# ``taxcode_mcp.catalog.load_rate_tables``. The formulas below read every number from them.
RATE_TABLES: dict[str, dict[str, Any]] = load_rate_tables()
# Graduated/flat/none states computed by the shared registry formula, in table order.
STATE_REGISTRY: dict[str, dict[str, Any]] = {
    code: params for code, params in RATE_TABLES.items() if RATE_TABLE_MODELS[code] == "registry"
}
# FL and TX levy no personal income tax, so they have no rate table.
SUPPORTED_STATE_JURISDICTIONS = frozenset({"FL", "TX", *RATE_TABLE_MODELS})


def _status(is_mfj: bool) -> str:
    return "mfj" if is_mfj else "single"


def _allowance(params: dict[str, Any], is_mfj: bool) -> float:
    """The filing-status deduction or exemption of a rate table."""
    by_status = params["deduction"] if "deduction" in params else params["exemption"]
    return float(by_status[_status(is_mfj)])


def _year_step(steps: list[dict[str, Any]], tax_year: int) -> dict[str, Any]:
    """The first step whose ``from_year`` is reached; the last step (``null``) otherwise."""
    return next(s for s in steps if s["from_year"] is None or tax_year >= s["from_year"])


def _bracket(brackets: list[dict[str, Any]], taxable_income: float) -> dict[str, Any]:
    """The first bracket whose ``over`` the income exceeds; the last (``null``) otherwise."""
    return next(b for b in brackets if b["over"] is None or taxable_income > b["over"])


def _whole_income_tax(brackets: list[dict[str, Any]], taxable_income: float) -> float:
    """The bracket's rate applied to the whole taxable income (this model's simplification)."""
    return float(round(taxable_income * _bracket(brackets, taxable_income)["rate"], 2))


def _cumulative_tax(brackets: list[dict[str, Any]], taxable_income: float) -> float:
    """The bracket's ``base`` plus its rate on the income over its threshold."""
    b = _bracket(brackets, taxable_income)
    over = 0.0 if b["over"] is None else b["over"]
    return float(round(b["base"] + b["rate"] * (taxable_income - over), 2))


def _marginal_tax(brackets: list[dict[str, Any]], taxable_income: float) -> float:
    """Each bracket's rate on the slice of income between its ``over`` and the next one up."""
    tax = 0.0
    ceiling = taxable_income
    for b in brackets:
        floor = 0.0 if b["over"] is None else float(b["over"])
        if ceiling > floor:
            tax += b["rate"] * (ceiling - floor)
            ceiling = floor
    return float(round(tax, 2))


def _pct(rate: float) -> str:
    """A rate as prose: ``0.0575`` -> ``5.75%``."""
    return f"{rate * 100:.2f}%"


def _money(amount: float) -> str:
    """A whole-dollar amount as prose: ``1000000.0`` -> ``$1,000,000``."""
    return f"${amount:,.0f}"


def _range_desc(table: dict[str, Any], lead: str = "Graduated brackets") -> str:
    """The lowest and highest bracket rate across both filing statuses."""
    rates = [b["rate"] for status in ("single", "mfj") for b in table["brackets"][status]]
    return f"{lead} from {_pct(min(rates))} to {_pct(max(rates))}"


def _steps_desc(brackets: list[dict[str, Any]]) -> str:
    """Every bracket of one schedule, lowest first: ``0.00% up to $X, R to $Y, R over $Y``."""
    steps = brackets[::-1]
    parts = [f"{_pct(steps[0]['rate'])} up to {_money(steps[1]['over'])}"]
    for step, above in zip(steps[1:-1], steps[2:], strict=True):
        parts.append(f"{_pct(step['rate'])} to {_money(above['over'])}")
    parts.append(f"{_pct(steps[-1]['rate'])} over {_money(steps[-1]['over'])}")
    return "Graduated brackets: " + ", ".join(parts)


_FLAT_BY_CONSTITUTION = "(the state constitution requires a flat rate)"

MIDDLE_RATE_ESTIMATE_NOTE = (
    " [ESTIMATE: no official bracket schedule is modeled for this year; income below the "
    "top bracket is taxed at 65% of the top rate, which is not a statutory rate]"
)


@compliance_server.tool(
    name="evaluate_state_residency",
    description=(
        "Evaluate individual statutory residency, domicile nexus, and state tax liability "
        "across all 50 US states and District of Columbia."
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
    middle_rate_estimated = False

    if state == "GA":
        statutory_basis = "O.C.G.A. § 48-7-1(10); Ga. Comp. R. & Regs. r. 560-7-3-.01"
        is_resident = (
            is_domiciled
            or days_in_state >= 183
            or (maintains_permanent_abode and days_in_state > 0)
        )
        ga = RATE_TABLES["GA"]
        standard_ded = float(ga["standard_deduction"][_status(is_mfj)])
        max_ret = _ga_retirement_cap(ga, age)
        ret_excl = min(retirement_income, max_ret)
        ga_step = _year_step(ga["rates"], tax_year)
        rate = float(ga_step["rate"])
        rate_desc = f"Flat {_pct(rate)} rate ({ga_step['law']}){_ga_estimate_note(tax_year)}"
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
        rate_desc = _range_desc(RATE_TABLES["NY"])
        std_ded = _allowance(RATE_TABLES["NY"], is_mfj)
        taxable_income = max(0.0, federal_agi - std_ded)
        if is_resident:
            brackets = RATE_TABLES["NY"]["brackets"][_status(is_mfj)]
            estimated_tax = _whole_income_tax(brackets, taxable_income)

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
        rate_desc = (
            f"{_range_desc(RATE_TABLES['CA'])} "
            "(the top rate includes the 1% Mental Health Services Tax)"
        )
        std_ded = _allowance(RATE_TABLES["CA"], is_mfj)
        taxable_income = max(0.0, federal_agi - std_ded)
        if is_resident:
            brackets = RATE_TABLES["CA"]["brackets"][_status(is_mfj)]
            estimated_tax = _whole_income_tax(brackets, taxable_income)

    elif state == "FL":
        statutory_basis = "Fla. Const. Art. VII § 5; Fla. Stat. § 222.17"
        is_resident = is_domiciled or (maintains_permanent_abode and days_in_state >= 183)
        rate_authority = "Fla. Const. Art. VII § 5"
        rate_desc = "No personal income tax (constitutional prohibition)"
        taxable_income = 0.0
        estimated_tax = 0.0

    elif state == "TX":
        statutory_basis = "Tex. Const. art. VIII § 24-a; Tex. Tax Code § 171.001"
        is_resident = is_domiciled or (maintains_permanent_abode and days_in_state >= 183)
        rate_authority = "Tex. Const. art. VIII § 24-a"
        rate_desc = "No personal income tax (constitutional prohibition)"
        taxable_income = 0.0
        estimated_tax = 0.0

    elif state == "WA":
        statutory_basis = "Wash. Const. art. VII § 1; RCW 82.87.040"
        is_resident = is_domiciled or (maintains_permanent_abode and days_in_state >= 183)
        rate_authority = "RCW 82.87.040"
        wa = RATE_TABLES["WA"]
        cap_gain_ded = float(wa["capital_gains_deduction"])
        rate_desc = (
            f"No ordinary income tax; {_pct(wa['rate'])} excise tax on capital gains over "
            f"the {_money(cap_gain_ded)} deduction"
        )
        taxable_income = max(0.0, capital_gains - cap_gain_ded)
        estimated_tax = round(taxable_income * wa["rate"], 2) if is_resident else 0.0

    elif state == "MA":
        statutory_basis = "M.G.L. c. 62 § 1(f), § 4; Mass. Const. amend. art. XLIV"
        is_resident = is_domiciled or (maintains_permanent_abode and days_in_state > 183)
        rate_authority = "M.G.L. c. 62 § 4 & Fair Share Amendment"
        ma = RATE_TABLES["MA"]
        rate_desc = (
            f"Flat {_pct(ma['rate'])} rate plus {_pct(ma['surtax']['rate'])} Fair Share surtax "
            f"on income over {_money(ma['surtax']['over'])}"
        )
        std_ded = _allowance(ma, is_mfj)
        taxable_income = max(0.0, federal_agi - std_ded)
        if is_resident:
            base_tax = taxable_income * ma["rate"]
            surtax = max(0.0, taxable_income - ma["surtax"]["over"]) * ma["surtax"]["rate"]
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
        rate_desc = _range_desc(RATE_TABLES["NJ"])
        exemption = _allowance(RATE_TABLES["NJ"], is_mfj)
        taxable_income = max(0.0, federal_agi - exemption)
        if is_resident:
            brackets = RATE_TABLES["NJ"]["brackets"][_status(is_mfj)]
            estimated_tax = _whole_income_tax(brackets, taxable_income)

    elif state == "IL":
        statutory_basis = "35 ILCS 5/201, 5/1501(a)(20), 5/301"
        is_resident = is_domiciled or (days_in_state >= 183 and maintains_permanent_abode)
        rate_authority = "35 ILCS 5/201"
        rate_desc = f"Flat {_pct(RATE_TABLES['IL']['rate'])} rate {_FLAT_BY_CONSTITUTION}"
        exemption = _allowance(RATE_TABLES["IL"], is_mfj)
        taxable_income = max(0.0, federal_agi - exemption)
        estimated_tax = round(taxable_income * RATE_TABLES["IL"]["rate"], 2) if is_resident else 0.0

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
        rate_desc = f"Flat {_pct(RATE_TABLES['PA']['rate'])} rate {_FLAT_BY_CONSTITUTION}"
        taxable_income = max(0.0, federal_agi)
        estimated_tax = round(taxable_income * RATE_TABLES["PA"]["rate"], 2) if is_resident else 0.0

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
        rate_desc = _range_desc(RATE_TABLES["CT"])
        # Standard exemption phase-out model
        exemption = _allowance(RATE_TABLES["CT"], is_mfj)
        taxable_income = max(0.0, federal_agi - exemption)
        if is_resident:
            brackets = RATE_TABLES["CT"]["brackets"][_status(is_mfj)]
            estimated_tax = _whole_income_tax(brackets, taxable_income)

    elif state == "NC":
        statutory_basis = "N.C. Gen. Stat. §§ 105-153.3, 105-153.7"
        is_resident = is_domiciled or (days_in_state > 183)
        rate_authority = "N.C. Gen. Stat. § 105-153.7"
        nc_step = _year_step(RATE_TABLES["NC"]["rates"], tax_year)
        nc_rate = float(nc_step["rate"])
        rate_desc = str(nc_step["rate_desc"])
        exemption = _allowance(RATE_TABLES["NC"], is_mfj)
        taxable_income = max(0.0, federal_agi - exemption)
        estimated_tax = round(taxable_income * nc_rate, 2) if is_resident else 0.0

    elif state == "VA":
        statutory_basis = "Va. Code Ann. §§ 58.1-302, 58.1-320, 58.1-342"
        is_resident = is_domiciled or days_in_state > 183
        rate_authority = "Va. Code Ann. § 58.1-320"
        rate_desc = _range_desc(RATE_TABLES["VA"])
        standard_ded = _allowance(RATE_TABLES["VA"], is_mfj)
        taxable_income = max(0.0, federal_agi - standard_ded)
        if is_resident:
            brackets = RATE_TABLES["VA"]["brackets"][_status(is_mfj)]
            estimated_tax = _cumulative_tax(brackets, taxable_income)

    elif state == "MD":
        statutory_basis = "Md. Code Ann., Tax-Gen. §§ 10-101, 10-105, 10-106"
        is_resident = is_domiciled or (maintains_permanent_abode and days_in_state >= 183)
        if is_resident:
            audit_flags.append(
                "MD County Piggyback Tax (Tax-Gen. § 10-106): Maryland residents are subject "
                "to an additional mandatory local income tax between 2.25% and 3.20%."
            )
        rate_authority = "Md. Code Ann., Tax-Gen. § 10-105"
        rate_desc = (
            f"{_range_desc(RATE_TABLES['MD'], 'Graduated state brackets')} "
            "plus county piggyback tax (2.25%-3.20%)"
        )
        md_ded = RATE_TABLES["MD"]["standard_deduction"]
        max_ded = float(md_ded["maximum"][_status(is_mfj)])
        min_ded = float(md_ded["minimum"][_status(is_mfj)])
        standard_ded = min(max_ded, max(min_ded, federal_agi * md_ded["percent_of_agi"]))
        taxable_income = max(0.0, federal_agi - standard_ded)
        if is_resident:
            brackets = RATE_TABLES["MD"]["brackets"][_status(is_mfj)]
            estimated_tax = _whole_income_tax(brackets, taxable_income)

    elif state == "CO":
        statutory_basis = "Colo. Rev. Stat. §§ 39-22-103, 39-22-104"
        is_resident = is_domiciled or (maintains_permanent_abode and days_in_state > 183)
        rate_authority = "Colo. Rev. Stat. § 39-22-104"
        co_step = _year_step(RATE_TABLES["CO"]["rates"], tax_year)
        co_rate = float(co_step["rate"])
        rate_desc = str(co_step["rate_desc"])
        standard_ded = _allowance(RATE_TABLES["CO"], is_mfj)
        taxable_income = max(0.0, federal_agi - standard_ded)
        estimated_tax = round(taxable_income * co_rate, 2) if is_resident else 0.0

    elif state == "AZ":
        statutory_basis = "A.R.S. §§ 43-104, 43-1011"
        is_resident = (
            is_domiciled
            or days_in_state > 270
            or (maintains_permanent_abode and days_in_state > 183)
        )
        if not is_resident and 183 < days_in_state <= 270:
            audit_flags.append(
                "AZ Rebuttable Presumption (A.R.S. § 43-104): Aggregate presence over 9 months "
                "creates a statutory presumption of residency; 183+ days with local ties invites "
                "audit scrutiny."
            )
        rate_authority = "A.R.S. § 43-1011"
        rate_desc = f"Flat {_pct(RATE_TABLES['AZ']['rate'])} rate (SB 1828)"
        standard_ded = _allowance(RATE_TABLES["AZ"], is_mfj)
        taxable_income = max(0.0, federal_agi - standard_ded)
        estimated_tax = round(taxable_income * RATE_TABLES["AZ"]["rate"], 2) if is_resident else 0.0

    elif state == "OH":
        statutory_basis = "Ohio Rev. Code §§ 5747.02, 5747.24"
        is_resident = is_domiciled or days_in_state >= 213
        if not is_domiciled and 183 <= days_in_state <= 212:
            audit_flags.append(
                "OH Bright-Line Contact Period Rule (R.C. § 5747.24): Taxpayer with <= 212 contact "
                "periods must file Form IT-NRS Non-Ohio Domicile Affidavit by October 15 to secure "
                "non-residency presumption."
            )
        rate_authority = "Ohio Rev. Code § 5747.02"
        rate_desc = _steps_desc(RATE_TABLES["OH"]["brackets"][_status(is_mfj)])
        personal_exemption = _allowance(RATE_TABLES["OH"], is_mfj)
        taxable_income = max(0.0, federal_agi - personal_exemption)
        if is_resident:
            brackets = RATE_TABLES["OH"]["brackets"][_status(is_mfj)]
            estimated_tax = _cumulative_tax(brackets, taxable_income)

    else:
        info = STATE_REGISTRY[state]
        statutory_basis = str(info["statutory_basis"])
        rate_authority = str(info["rate_authority"])
        rate_desc = str(info["rate_desc"])
        day_threshold = int(info.get("day_threshold", 183))
        requires_abode = bool(info.get("requires_abode", True))
        inclusive_threshold = bool(info.get("inclusive_threshold", False))

        presence_met = (
            days_in_state >= day_threshold if inclusive_threshold else days_in_state > day_threshold
        )
        if requires_abode:
            is_resident = is_domiciled or (presence_met and maintains_permanent_abode)
        else:
            is_resident = is_domiciled or presence_met

        if state in ("DE", "NE") and telecommuting_days_out_of_state > 0:
            basis_prefix = statutory_basis.split(";")[0]
            audit_flags.append(
                f"{state} Convenience of the Employer Doctrine ({basis_prefix}): "
                f"Wage income allocated to {state} unless remote work is for employer necessity."
            )
        if state == "IN":
            audit_flags.append(
                "IN Local Income Tax (LIT, Ind. Code § 6-3.6): County income taxes range "
                "from 0.5% to 3.0% in addition to state base rate."
            )
        if state == "LA":
            if tax_year == 2026:
                audit_flags.append(
                    "LA Standard Deduction (Act 11 of 2024 / RIB 26-019): Adjusted for CPI-U "
                    "inflation to $12,838 single / $25,676 joint for 2026."
                )
            elif tax_year >= 2027:
                rate_desc = (
                    f"{rate_desc} [PROVISIONAL: 2027+ standard deduction pending statutory CPI-U]"
                )
                audit_flags.append(
                    "LA Standard Deduction (Act 11 of 2024): 2027+ estimates are provisional "
                    "and use the 2026 baseline ($12,838 / $25,676) pending annual CPI-U release."
                )

        # Year-aware regime selection
        if tax_year >= 2025 and "regime_2025" in info:
            regime = str(info["regime_2025"])
        else:
            regime = str(info["regime"])

        # Year-aware standard deduction selection
        if tax_year >= 2026 and (
            "deduction_mfj_2026" in info if is_mfj else "deduction_single_2026" in info
        ):
            ded_key = "deduction_mfj_2026" if is_mfj else "deduction_single_2026"
        elif tax_year >= 2025 and (
            "deduction_mfj_2025" in info if is_mfj else "deduction_single_2025" in info
        ):
            ded_key = "deduction_mfj_2025" if is_mfj else "deduction_single_2025"
        else:
            ded_key = "deduction_mfj" if is_mfj else "deduction_single"
        ded = float(info.get(ded_key, 0.0))
        taxable_income = max(0.0, federal_agi - ded)

        if regime == "none":
            taxable_income = 0.0
            estimated_tax = 0.0
        elif regime == "graduated" and "brackets" in info:
            brackets = info["brackets"][_status(is_mfj)]
            estimated_tax = _marginal_tax(brackets, taxable_income) if is_resident else 0.0
        elif regime == "flat":
            if tax_year >= 2027 and "rate_2027" in info:
                flat_rate = float(info["rate_2027"])
            elif tax_year >= 2026 and "rate_2026" in info:
                flat_rate = float(info["rate_2026"])
            elif tax_year >= 2025 and "rate_2025" in info:
                flat_rate = float(info["rate_2025"])
            else:
                flat_rate = float(info.get("rate", 0.0))
            estimated_tax = round(taxable_income * flat_rate, 2) if is_resident else 0.0
        else:  # graduated
            if tax_year >= 2025 and (
                "top_bracket_mfj_2025" in info if is_mfj else "top_bracket_single_2025" in info
            ):
                tb_key = "top_bracket_mfj_2025" if is_mfj else "top_bracket_single_2025"
            else:
                tb_key = "top_bracket_mfj" if is_mfj else "top_bracket_single"
            top_bracket = float(info.get(tb_key, 0.0))
            top_rate = float(info.get("top_rate", 0.0))
            if "middle_rate" in info:
                mid_rate = float(info["middle_rate"])
            else:  # middle_rate_estimate: no official schedule maps onto this formula
                mid_rate = top_rate * 0.65
                middle_rate_estimated = True
                rate_desc = f"{rate_desc}{MIDDLE_RATE_ESTIMATE_NOTE}"
            if tax_year >= 2025 and (
                "zero_bracket_mfj_2025" in info if is_mfj else "zero_bracket_single_2025" in info
            ):
                zb_key = "zero_bracket_mfj_2025" if is_mfj else "zero_bracket_single_2025"
            else:
                zb_key = "zero_bracket_mfj" if is_mfj else "zero_bracket_single"
            zero_bracket = float(info.get(zb_key, 0.0))
            if is_resident:
                if taxable_income <= zero_bracket:
                    estimated_tax = 0.0
                elif taxable_income > top_bracket:
                    base_est = (top_bracket - zero_bracket) * mid_rate
                    estimated_tax = round(base_est + top_rate * (taxable_income - top_bracket), 2)
                else:
                    estimated_tax = round((taxable_income - zero_bracket) * mid_rate, 2)
            else:
                estimated_tax = 0.0

    tax_computation: dict[str, Any] = {
        "taxable_net_income": taxable_income,
        "estimated_state_tax": estimated_tax,
        "is_estimate": True,
        "estimate_note": ESTIMATE_NOTE,
        "rate_description": rate_desc,
        "statutory_rate_authority": rate_authority,
        "middle_rate_is_estimate": middle_rate_estimated,
    }
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
        "tax_computation": tax_computation,
        "disclaimer": BILINGUAL_DISCLAIMER,
        "content_notice": CONTENT_NOTICE,
    }


@compliance_server.prompt("cross_border_tax_analysis")
def cross_border_tax_analysis(taxpayer_scenario: str) -> str:
    """Generate structured analytical prompt for dual-jurisdiction US and PR tax analysis."""
    return f"""You are a research assistant for cross-border tax law. You help organize
statutory research; you do not give legal or tax advice. Outline the research questions for
the following taxpayer scenario involving US federal tax law and Puerto Rico statutes:

SCENARIO:
{taxpayer_scenario}

ANALYTICAL FRAMEWORK:
1. Determine bona fide residency under 26 U.S.C. § 937 and 26 CFR § 1.937-1.
2. Evaluate applicability of the federal income exclusion under 26 U.S.C. § 933.
3. Sourcing determination: Apply 26 U.S.C. § 937(b) and 26 CFR § 1.937-2 (10-year rule).
4. Local PR tax incentives: Apply Puerto Rico Incentives Code (Ley 60-2019, Chapter 2/3).
5. Federal filing obligations: Assess required IRS reporting (Form 8898, Form 1040, Form 8960).

Cite all operative code sections and regulations precisely. State that the result is
research for reference only, not legal or tax advice, and that a licensed professional
should review it. / Indique que el resultado es investigación de referencia únicamente, no
asesoramiento legal ni contributivo, y que un profesional licenciado debe revisarlo.
"""
