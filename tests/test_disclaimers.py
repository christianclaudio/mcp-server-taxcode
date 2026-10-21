"""Every tool output carries the bilingual disclaimer and reference labels."""

from typing import Any

import httpx
import pytest

from taxcode_mcp import server
from taxcode_mcp.db import INITIAL_STATUTES, USLM_TITLE26_SOURCE_URL
from taxcode_mcp.disclaimers import (
    BILINGUAL_DISCLAIMER,
    CONTENT_NOTICE,
    ESTIMATE_NOTE,
    OFFICIAL_TEXT_LABEL,
    OFFICIAL_TEXT_TRANSLATION_DISCLAIMER,
    REFERENCE_SUMMARY_LABEL,
    STATUTE_TRANSLATION_DISCLAIMER,
)


def _assert_notice(out: dict[str, Any]) -> None:
    assert out["disclaimer"] == BILINGUAL_DISCLAIMER
    assert set(out["disclaimer"]) == {"en", "es"}
    assert out["content_notice"] == CONTENT_NOTICE


def _assert_labels(item: dict[str, Any]) -> None:
    assert item["content_type"] == "reference_summary"
    assert item["content_label"] == REFERENCE_SUMMARY_LABEL
    assert item["unofficial_summary"] is True
    assert item["translation_disclaimer"] == STATUTE_TRANSLATION_DISCLAIMER


def _assert_official(item: dict[str, Any]) -> None:
    assert item["content_type"] == "official_text"
    assert item["content_label"] == OFFICIAL_TEXT_LABEL
    assert item["unofficial_summary"] is False
    assert item["translation_disclaimer"] == OFFICIAL_TEXT_TRANSLATION_DISCLAIMER


def _assert_item(item: dict[str, Any]) -> None:
    if item["source_url"] == USLM_TITLE26_SOURCE_URL:
        _assert_official(item)
    else:
        _assert_labels(item)


def test_official_text_label_is_bilingual() -> None:
    assert set(OFFICIAL_TEXT_LABEL) == {"en", "es"}
    assert "OLRC release point 119-121" in OFFICIAL_TEXT_LABEL["en"]
    assert "119-121" in OFFICIAL_TEXT_LABEL["es"]
    assert "Spanish summary is an unofficial" in OFFICIAL_TEXT_TRANSLATION_DISCLAIMER
    assert "English statutory text controls" in OFFICIAL_TEXT_TRANSLATION_DISCLAIMER
    assert "prevalece el texto estatutario oficial en inglés" in (
        OFFICIAL_TEXT_TRANSLATION_DISCLAIMER
    )


def test_statute_translation_disclaimer_is_bilingual() -> None:
    assert "Unofficial translation" in STATUTE_TRANSLATION_DISCLAIMER
    assert "Traducción/resumen no oficial" in STATUTE_TRANSLATION_DISCLAIMER
    assert set(REFERENCE_SUMMARY_LABEL) == {"en", "es"}
    assert set(ESTIMATE_NOTE) == {"en", "es"}


@pytest.mark.asyncio
async def test_health_status_has_disclaimer(mock_transport, monkeypatch) -> None:
    async_client = httpx.AsyncClient(transport=mock_transport, base_url="https://api.example.com")
    monkeypatch.setattr(server, "client", server.TemplateClient(http_client=async_client))
    _assert_notice(await server.get_health_status())


@pytest.mark.asyncio
async def test_statute_outputs_have_disclaimer_and_labels() -> None:
    found = await server.lookup_statute("26 U.S.C. § 933")
    _assert_notice(found)
    _assert_official(found["data"])

    reg = await server.lookup_statute("26 CFR § 1.937-1")
    _assert_notice(reg)
    _assert_labels(reg["data"])

    missing = await server.lookup_statute("Nonexistent 9999")
    assert missing["status"] == "not_found"
    _assert_notice(missing)
    _assert_labels(missing)

    search = await server.search_tax_code("bona fide resident")
    _assert_notice(search)
    kinds = set()
    for item in search["results"]:
        _assert_item(item)
        kinds.add(item["content_type"])
    assert "official_text" in kinds


@pytest.mark.asyncio
async def test_publications_have_disclaimer_and_labels() -> None:
    pubs = await server.list_publications()
    _assert_notice(pubs)
    assert pubs["publications"]
    for item in pubs["publications"]:
        _assert_labels(item)


def test_built_in_gain_early_return_has_disclaimer() -> None:
    out = server.calculate_built_in_gain_sourcing(
        is_marketable=True, total_gain=1000.0, years_since_residency=10.0
    )
    assert out["lookback_expired"] is True
    _assert_notice(out)


def test_built_in_gain_early_return_states_5_percent_rate() -> None:
    out = server.calculate_built_in_gain_sourcing(
        is_marketable=True, total_gain=1000.0, years_since_residency=10.0
    )
    treatment = out["applicable_pr_treatment"]
    assert "5%" in treatment
    assert "Section 2022.02" in treatment
    assert "0% or 4%" not in treatment
    assert "before Jan 1, 2036 (applications filed through 2026)" in treatment
    assert "before Jan 1, 2056 (applications filed from 2027)" in treatment
    treatment_es = out["applicable_pr_treatment_es"]
    assert "5%" in treatment_es
    assert "Sección 2022.02" in treatment_es
    assert "antes del 1 de enero de 2036 (solicitudes radicadas hasta 2026)" in treatment_es
    assert "antes del 1 de enero de 2056 (solicitudes radicadas desde 2027)" in treatment_es


def test_pr_residency_reports_tax_year_and_gross_income() -> None:
    out = server.evaluate_pr_bona_fide_residency(200, 30, tax_year=2025)
    assert out["tax_year"] == 2025
    assert any("worldwide gross income > $75k" in r for r in out["reporting_requirements"])
    assert server.evaluate_pr_bona_fide_residency(200, 30)["tax_year"] is None


def test_form_8898_is_a_conditional_check_with_both_conditions() -> None:
    out = server.evaluate_pr_bona_fide_residency(200, 30, tax_year=2025)
    form = [r for r in out["reporting_requirements"] if "Form 8898" in r]
    assert len(form) == 1
    assert form[0].startswith("Conditional:")
    assert "became or stopped being a bona fide PR resident in this tax year" in form[0]
    assert "worldwide gross income > $75k" in form[0]


def test_state_estimates_are_labeled() -> None:
    ga = server.evaluate_georgia_residency(days_present_ga=200, federal_agi=60000.0)
    assert ga["tax_computation"]["is_estimate"] is True
    assert ga["tax_computation"]["estimate_note"] == ESTIMATE_NOTE
    st = server.evaluate_state_residency(jurisdiction="NY", days_in_state=200, federal_agi=90000.0)
    assert st["tax_computation"]["is_estimate"] is True
    assert st["tax_computation"]["estimate_note"] == ESTIMATE_NOTE


def test_prompt_is_research_assistance_not_attorney() -> None:
    text = server.cross_border_tax_analysis("Moved to PR in 2024.")
    assert "attorney" not in text
    assert "research assistant" in text
    assert "not legal or tax advice" in text


def test_act60_seeds_cite_primary_sources() -> None:
    seeds = {s["id"]: s for s in INITIAL_STATUTES}
    for sid in ("pr-ley60-2021-01", "pr-ley60-2031-01", "pr-ley60-2032-01"):
        assert seeds[sid]["source_url"].endswith("/leyesreferencia/PDF/60-2019.pdf")
    iri = seeds["pr-ley60-2022-01"]
    assert iri["source_url"].endswith("/PDF/2020/0038-2026.pdf")
    assert "interest, dividends, and capital gains" in iri["text_en"]
    assert "5%" in iri["text_en"] and "cuatro por ciento (4%)" in iri["text_es"]
    # The 5% capital gain rate is in Section 2022.02, which Act 38-2026 also restates.
    gains = seeds["pr-ley60-2022-02"]
    assert gains["source_url"] == iri["source_url"]
    assert "cinco por ciento (5%)" in gains["text_es"]
    assert "before Jan 1, 2036" in iri["text_en"] and "before Jan 1, 2056" in iri["text_en"]
    assert "antes del 1 de enero de 2036" in iri["text_es"]
    assert "antes del 1 de enero de 2056" in iri["text_es"]
