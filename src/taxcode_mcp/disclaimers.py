"""Shared bilingual (English / Spanish) disclaimers and content labels for every tool output."""

from __future__ import annotations

from typing import Any

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

# Kept a plain string (English, then Spanish) so the existing field type does not change.
STATUTE_TRANSLATION_DISCLAIMER = (
    "Unofficial translation/summary for reference purposes only. Official "
    "statutory text in the jurisdiction's authoritative language controls. / "
    "Traducción/resumen no oficial solo para fines de referencia. Prevalece el texto "
    "estatutario oficial en el idioma oficial de la jurisdicción."
)

CONTENT_TYPE_REFERENCE_SUMMARY = "reference_summary"

REFERENCE_SUMMARY_LABEL: dict[str, str] = {
    "en": "Unofficial reference summary; not the official statutory text.",
    "es": "Resumen de referencia no oficial; no es el texto estatutario oficial.",
}

ESTIMATE_NOTE: dict[str, str] = {
    "en": (
        "Estimate only, from a simplified heuristic model. Not a final tax liability; actual "
        "liability depends on the full return, credits, and current law."
    ),
    "es": (
        "Solo un estimado, basado en un modelo heurístico simplificado. No es una obligación "
        "contributiva final; la obligación real depende de la planilla completa, los créditos "
        "y la ley vigente."
    ),
}


def reference_labels() -> dict[str, Any]:
    """Per-item labels for statute and publication summaries."""
    return {
        "content_type": CONTENT_TYPE_REFERENCE_SUMMARY,
        "content_label": dict(REFERENCE_SUMMARY_LABEL),
        "unofficial_summary": True,
        "translation_disclaimer": STATUTE_TRANSLATION_DISCLAIMER,
    }


def output_notice() -> dict[str, Any]:
    """Top-level disclaimer and notice attached to every tool output."""
    return {"disclaimer": dict(BILINGUAL_DISCLAIMER), "content_notice": CONTENT_NOTICE}
