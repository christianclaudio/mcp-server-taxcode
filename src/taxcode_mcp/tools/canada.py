"""Canada (``CAN``) federal residency, Part I tax and departure tax estimates.

``compliance_evaluate_state_residency`` answers ``jurisdiction="CAN"`` (or ``"Canada"``; ``"CA"``
stays California) with :func:`evaluate_canada`. Every figure comes from the ``canada_federal``
rate table (``taxcode_mcp/data/rates/can.json``); amounts are Canadian dollars.

- Residency (ITA s. 2(1), s. 250(1)(a); CRA Income Tax Folio S5-F1-C1): a factual resident
  keeps residential ties in Canada (``is_domiciled`` or ``maintains_permanent_abode``); someone
  without them who sojourns in Canada 183 days or more in the year is deemed resident for the
  whole year; ``event="emigration"`` makes a factual resident a part-year resident who leaves
  (s. 114, s. 128.1(4)); anyone else is a non-resident.
- Estimated federal Part I tax: the year's graduated brackets (s. 117(2), indexed by s. 117.1)
  on the taxable income, less the basic personal amount credit at the lowest rate (s. 118(1)(c),
  (1.1)), never below zero. Provincial tax, other credits and surtaxes are not modeled.
- Departure tax (``event="emigration"``): each property is deemed disposed of at fair market
  value unless s. 128.1(4)(b) excludes it; half of the net gain is taxable (s. 38(a)); the tax
  is the increase in the estimated Part I tax. Reportable property (s. 128.1(10)) worth more than
  $25,000 in total must be listed on Form T1161 (s. 128.1(9)).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from taxcode_mcp.disclaimers import BILINGUAL_DISCLAIMER, CONTENT_NOTICE, ESTIMATE_NOTE
from taxcode_mcp.errors import SafetyViolationError

# Names that select this model; "CA" is California.
CANADA_NAMES = frozenset({"can", "canada"})
SUPPORTED_EVENTS = ("emigration",)

PropertyCategory = Literal[
    "capital_property",
    "canadian_real_or_resource",
    "canadian_business_pe",
    "excluded_right_or_interest",
    "excluded_right_reportable",
    "canadian_cash",
    "personal_use_property",
]

# Category -> (the s. 128.1(4)(b) exclusion, or "" when the property is deemed disposed of;
# whether it is reportable property under s. 128.1(10)).
CATEGORY_RULES: dict[str, tuple[str, bool]] = {
    "capital_property": ("", True),
    "canadian_real_or_resource": ("ITA s. 128.1(4)(b)(i)", True),
    "canadian_business_pe": ("ITA s. 128.1(4)(b)(ii)", True),
    "excluded_right_or_interest": ("ITA s. 128.1(4)(b)(iii)", False),
    "excluded_right_reportable": ("ITA s. 128.1(4)(b)(iii)", True),
    "canadian_cash": ("", False),
    "personal_use_property": ("", True),
}
# The categories s. 128.1(4)(b)(iv) (a short-term resident's property) can exclude: the
# ones (i) to (iii) do not already exclude.
_SHORT_TERM_CATEGORIES = frozenset({"capital_property", "personal_use_property"})


class EmigrationProperty(BaseModel):
    """One property owned immediately before leaving Canada (amounts in CAD)."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="A label for the property.")
    category: PropertyCategory = Field(
        default="capital_property",
        description=(
            "capital_property (default: shares, funds, foreign real estate, foreign currency); "
            "canadian_real_or_resource (s. 128.1(4)(b)(i)); canadian_business_pe (property of a "
            "business carried on through a permanent establishment in Canada, (4)(b)(ii)); "
            "excluded_right_or_interest (RRSP, RRIF, TFSA, FHSA, pension, CPP/OAS and other "
            "rights in the s. 128.1(10) definition, (4)(b)(iii)); excluded_right_reportable "
            "(an excluded right that is still reportable: paragraphs (c), (j) and (l) of that "
            "definition, such as employee stock option rights); canadian_cash (Canadian money "
            "and deposits); personal_use_property."
        ),
    )
    fair_market_value: float = Field(ge=0.0, description="Fair market value when leaving.")
    adjusted_cost_base: float = Field(default=0.0, ge=0.0, description="Adjusted cost base.")
    owned_at_last_immigration_or_inherited: bool = Field(
        default=False,
        description=(
            "Owned when the individual last became resident in Canada, or inherited since: "
            "the property s. 128.1(4)(b)(iv) excludes for a short-term resident."
        ),
    )


def is_canada(jurisdiction: str) -> bool:
    """True for ``"CAN"`` and ``"Canada"`` in any case."""
    return jurisdiction.strip().lower() in CANADA_NAMES


def _year_row(params: dict[str, Any], tax_year: int) -> dict[str, Any]:
    rows = {int(row["tax_year"]): row for row in params["years"]}
    if tax_year not in rows:
        raise SafetyViolationError(
            f"Canada federal rates are held for tax years {min(rows)}-{max(rows)}; got {tax_year}."
        )
    row: dict[str, Any] = rows[tax_year]
    return row


def _graduated_tax(brackets: list[dict[str, Any]], income: float) -> float:
    tax, ceiling = 0.0, income
    for step in brackets:
        floor = 0.0 if step["over"] is None else float(step["over"])
        if ceiling > floor:
            tax += float(step["rate"]) * (ceiling - floor)
            ceiling = floor
    return round(tax, 2)


def basic_personal_amount(bpa: dict[str, Any], income: float) -> float:
    """The s. 118(1.1) amount: the maximum, reduced to the minimum across the phase-out."""
    top, bottom = float(bpa["maximum"]), float(bpa["minimum"])
    start, end = float(bpa["phaseout_start"]), float(bpa["phaseout_end"])
    if income <= start:
        return top
    share = min(1.0, (income - start) / (end - start))
    return round(top - (top - bottom) * share, 2)


def part_i_tax(row: dict[str, Any], income: float) -> dict[str, float]:
    """Estimated federal Part I tax on ``income``: brackets less the basic personal credit."""
    graduated = _graduated_tax(row["brackets"], income)
    amount = basic_personal_amount(row["basic_personal_amount"], income)
    credit = round(amount * float(row["credit_rate"]), 2)
    return {
        "graduated_tax": graduated,
        "basic_personal_amount": amount,
        "basic_personal_amount_credit": credit,
        "estimated_federal_tax": max(0.0, round(graduated - credit, 2)),
    }


def _property(item: EmigrationProperty, params: dict[str, Any], short_term: bool) -> dict[str, Any]:
    exclusion, reportable = CATEGORY_RULES[item.category]
    reason = "ITA s. 128.1(10) reportable property"
    if not reportable:
        reason = "Not reportable: ITA s. 128.1(10) " + (
            "paragraph (a)" if item.category == "canadian_cash" else "paragraph (b)"
        )
    if short_term and item.owned_at_last_immigration_or_inherited:
        if item.category in _SHORT_TERM_CATEGORIES:
            exclusion = "ITA s. 128.1(4)(b)(iv)"
            reportable = False
            reason = "Not reportable: ITA s. 128.1(10) paragraph (c), unless it is taxable Canadian"
            reason += " property"
    fmv, acb = item.fair_market_value, item.adjusted_cost_base
    if item.category == "personal_use_property" and reportable:
        floor = float(params["personal_use_reporting_threshold"])
        if fmv < floor:
            reportable = False
            reason = "Not reportable: ITA s. 128.1(10) paragraph (d), worth less than $10,000"
    gain = 0.0
    if not exclusion and item.category != "canadian_cash":
        if item.category == "personal_use_property":
            # s. 46(1): cost and proceeds are each at least $1,000; s. 40(2)(g)(iii): no loss.
            minimum = float(params["personal_use_minimum"])
            gain = max(0.0, max(fmv, minimum) - max(acb, minimum))
        else:
            gain = fmv - acb
    return {
        "description": item.description,
        "category": item.category,
        "fair_market_value": fmv,
        "adjusted_cost_base": acb,
        "deemed_disposed": not exclusion,
        "exclusion": exclusion or None,
        "deemed_gain": round(gain, 2),
        "reportable": reportable,
        "reportable_basis": reason,
    }


def _properties(raw: list[Any] | None) -> list[EmigrationProperty]:
    items: list[EmigrationProperty] = []
    for i, value in enumerate(raw or []):
        try:
            items.append(EmigrationProperty.model_validate(value))
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first["loc"])
            raise SafetyViolationError(
                f"emigration_properties[{i}]: {where}: {first['msg']}"
            ) from None
    return items


def _departure(
    params: dict[str, Any],
    row: dict[str, Any],
    income: float,
    capital_gains: float,
    items: list[EmigrationProperty],
    months: int | None,
) -> dict[str, Any]:
    threshold_months = int(params["short_term_resident_months"])
    short_term = months is not None and months <= threshold_months
    rows = [_property(item, params, short_term) for item in items]
    deemed = round(sum(r["deemed_gain"] for r in rows), 2)
    inclusion = float(params["capital_gains_inclusion_rate"])
    before = income + inclusion * capital_gains
    after = income + inclusion * max(0.0, capital_gains + deemed)
    taxable_gain = round(after - before, 2)
    departure_tax = round(
        part_i_tax(row, after)["estimated_federal_tax"]
        - part_i_tax(row, before)["estimated_federal_tax"],
        2,
    )
    reportable = round(sum(r["fair_market_value"] for r in rows if r["reportable"]), 2)
    t1161_threshold = float(params["t1161_threshold"])
    t1161 = reportable > t1161_threshold
    return {
        "event": "emigration",
        "statutory_basis": (
            "ITA s. 128.1(4)(b) (deemed disposition), s. 128.1(9) and (10) (reportable "
            "property), s. 38(a) (inclusion rate), s. 40(2)(g)(iii) and s. 46(1) "
            "(personal-use property)"
        ),
        "properties": rows,
        "deemed_capital_gain": deemed,
        "capital_gains_inclusion_rate": inclusion,
        "taxable_capital_gain": taxable_gain,
        "estimated_departure_tax": departure_tax,
        "short_term_resident_rule": {
            "months_resident_in_last_120": months,
            "threshold_months": threshold_months,
            "applies": None if months is None else short_term,
            "basis": "ITA s. 128.1(4)(b)(iv)",
        },
        "reportable_fair_market_value": reportable,
        "t1161_threshold": t1161_threshold,
        "t1161_required": t1161,
        "forms": [
            {
                "form": "T1243",
                "publication_id": "can-cra-t1243",
                "required": any(r["deemed_disposed"] for r in rows),
                "purpose": "Deemed Disposition of Property by an Emigrant of Canada",
            },
            {
                "form": "T1161",
                "publication_id": "can-cra-t1161",
                "required": t1161,
                "purpose": "List of Properties by an Emigrant of Canada (ITA s. 128.1(9))",
            },
            {
                "form": "T1244",
                "publication_id": "can-cra-t1244",
                "required": False,
                "purpose": (
                    "Optional election to defer paying the departure tax until the property "
                    "is sold (ITA s. 220(4.5)); security may be required"
                ),
            },
        ],
        "is_estimate": True,
        "estimate_note": ESTIMATE_NOTE,
        "not_modeled": [
            "Elections under ITA s. 128.1(4)(d) and (6) and s. 220(4.5) security",
            "Taxable Canadian property status of each property (assumed not, for (4)(b)(iv))",
            "Foreign tax credits and tax treaty relief on the deemed disposition",
        ],
    }


_UNUSED_INPUTS: dict[str, Any] = {
    "filing_status": "single",
    "age": 40,
    "retirement_income": 0.0,
    "has_active_homestead_exemption": False,
    "telecommuting_days_out_of_state": 0,
    "dependents": 0,
    "spouse_agi": 0.0,
    "itemized_deductions": 0.0,
}


def evaluate_canada(
    params: dict[str, Any],
    *,
    days_in_canada: int,
    is_domiciled: bool,
    maintains_permanent_abode: bool,
    taxable_income: float,
    capital_gains: float,
    tax_year: int,
    has_active_driver_license: bool,
    has_active_voter_registration: bool,
    event: str | None,
    emigration_properties: list[Any] | None,
    months_resident_in_last_120: int | None,
    other_inputs: dict[str, Any],
) -> dict[str, Any]:
    """The ``evaluate_state_residency`` answer for Canada (see the module docstring)."""
    if days_in_canada < 0 or days_in_canada > 366:
        raise SafetyViolationError("Day count must be between 0 and 366.")
    if taxable_income < 0:
        raise SafetyViolationError("Taxable income (federal_agi) cannot be negative.")
    if capital_gains < 0:
        raise SafetyViolationError("Capital gains cannot be negative.")
    if event is not None and event.strip().lower() not in SUPPORTED_EVENTS:
        raise SafetyViolationError(
            f"Unsupported event {event!r}. Supported events: {', '.join(SUPPORTED_EVENTS)}."
        )
    emigrating = event is not None
    if not emigrating and (
        emigration_properties is not None or months_resident_in_last_120 is not None
    ):
        raise SafetyViolationError(
            'emigration_properties and months_resident_in_last_120 need event="emigration".'
        )
    if months_resident_in_last_120 is not None and not 0 <= months_resident_in_last_120 <= 120:
        raise SafetyViolationError("months_resident_in_last_120 must be between 0 and 120.")
    factual = is_domiciled or maintains_permanent_abode
    if emigrating and not factual:
        raise SafetyViolationError(
            'event="emigration" needs a factual resident of Canada before leaving '
            "(is_domiciled or maintains_permanent_abode)."
        )
    items = _properties(emigration_properties)
    row = _year_row(params, tax_year)
    sojourner_days = int(params["sojourner_days"])
    if emigrating:
        status = "part_year_emigrant"
    elif factual:
        status = "factual_resident"
    elif days_in_canada >= sojourner_days:
        status = "deemed_resident_sojourner"
    else:
        status = "non_resident"
    is_resident = status != "non_resident"
    inclusion = float(params["capital_gains_inclusion_rate"])
    income = round(taxable_income + inclusion * capital_gains, 2)
    tax = part_i_tax(row, income)
    estimated = tax["estimated_federal_tax"] if is_resident else 0.0
    flags: list[str] = []
    if has_active_driver_license:
        flags.append(
            "A Canadian driver’s licence is a secondary residential tie (Income Tax Folio "
            "S5-F1-C1)."
        )
    if has_active_voter_registration:
        flags.append(
            "Ongoing social and civic ties in Canada are secondary residential ties (Income "
            "Tax Folio S5-F1-C1)."
        )
    if emigrating and maintains_permanent_abode:
        flags.append(
            "CRITICAL: keeping a dwelling available in Canada is a significant residential tie; "
            "residence may not end on departure (Income Tax Folio S5-F1-C1)."
        )
    notes = {
        "factual_resident": (
            "Resident all year: Part I tax on worldwide taxable income (ITA s. 2(1))."
        ),
        "deemed_resident_sojourner": (
            f"Sojourned in Canada {days_in_canada} days (183 or more): deemed resident "
            "throughout the year (ITA s. 250(1)(a))."
        ),
        "part_year_emigrant": (
            "Resident until departure: taxable income is the resident-period income plus "
            "Canadian-source income after it (ITA s. 114); enter that total as federal_agi. "
            "Credits are prorated for a part-year resident (ITA s. 118.91, not modeled)."
        ),
        "non_resident": (
            "Non-resident: Part I tax applies only to taxable income earned in Canada (ITA "
            "s. 2(3), s. 115), and Part XIII withholding of 25% to passive Canadian income (ITA "
            "s. 212); neither is estimated here. A treaty tie-breaker can also make a resident "
            "non-resident (ITA s. 250(5))."
        ),
    }
    lowest = float(row["brackets"][-1]["rate"])
    top = float(row["brackets"][0]["rate"])
    bpa = row["basic_personal_amount"]
    bpa_max = float(bpa["maximum"])
    bpa_min = float(bpa["minimum"])
    if bpa_max > bpa_min:
        bpa_desc = (
            f"the basic personal amount credit at {lowest * 100:g}% "
            f"(${bpa_max:,.0f} phased down to ${bpa_min:,.0f} under s. 118(1.1) "
            "between the 4th and 5th brackets, "
            f"${float(bpa['phaseout_start']):,.0f} to ${float(bpa['phaseout_end']):,.0f})"
        )
    else:
        bpa_desc = (
            f"the ${bpa_max:,.0f} basic personal amount credit at {lowest * 100:g}% "
            "(before the s. 118(1.1) phaseout took effect in 2020)"
        )
    tax_computation: dict[str, Any] = {
        "currency": "CAD",
        "taxable_net_income": income,
        "capital_gains_inclusion_rate": inclusion,
        "graduated_tax": tax["graduated_tax"] if is_resident else 0.0,
        "basic_personal_amount": tax["basic_personal_amount"],
        "basic_personal_amount_credit": tax["basic_personal_amount_credit"] if is_resident else 0.0,
        "credit_rate": float(row["credit_rate"]),
        "estimated_federal_tax": estimated,
        "estimated_state_tax": estimated,
        "is_estimate": True,
        "estimate_note": ESTIMATE_NOTE,
        "rate_description": (
            f"Federal graduated brackets from {lowest * 100:g}% to {top * 100:g}% for "
            f"{tax_year}, less {bpa_desc}; "
            "estimated_state_tax repeats the federal estimate. Provincial or territorial "
            "tax, other credits and deductions are not modeled."
        ),
        "statutory_rate_authority": "ITA s. 117(2), s. 117.1, s. 118(1)(c) and (1.1)",
        "middle_rate_is_estimate": False,
        "residency_note": notes[status],
    }
    ignored = sorted(k for k, default in _UNUSED_INPUTS.items() if other_inputs[k] != default)
    result: dict[str, Any] = {
        "status": "success",
        "jurisdiction": "CAN",
        "tax_year": tax_year,
        "is_resident": is_resident,
        "residency_tests": {
            "residency_status": status,
            "factual_residence_ties": factual,
            "domicile_satisfied": is_domiciled,
            "days_in_state": days_in_canada,
            "maintains_permanent_abode": maintains_permanent_abode,
            "sojourner_days_threshold": sojourner_days,
            "statutory_basis": (
                "ITA s. 2(1), s. 250(1)(a), s. 114 and s. 128.1(4); CRA Income Tax Folio S5-F1-C1"
            ),
        },
        "domicile_audit_risk": {"risk_level": "high" if flags else "standard", "flags": flags},
        "tax_computation": tax_computation,
    }
    if emigrating:
        result["departure_tax"] = _departure(
            params, row, taxable_income, capital_gains, items, months_resident_in_last_120
        )
    result["references"] = {
        "statutes": ["can-ita-2", "can-ita-114", "can-ita-117", "can-ita-118", "can-ita-250"]
        + (["can-ita-38", "can-ita-128-1"] if emigrating else []),
        "publications": ["can-cra-folio-s5-f1-c1"]
        + (
            ["can-cra-leaving-canada", "can-cra-t1243", "can-cra-t1161", "can-cra-t1244"]
            if emigrating
            else []
        ),
    }
    result["ignored_inputs"] = ignored
    result["disclaimer"] = BILINGUAL_DISCLAIMER
    result["content_notice"] = CONTENT_NOTICE
    return result
