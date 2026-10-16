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

CONTENT_NOTICE = (
    "Computational heuristic model with statutory reference content; each item's "
    "content_type says whether it is official text or an unofficial summary."
)

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

CONTENT_TYPE_OFFICIAL_TEXT = "official_text"

OFFICIAL_TEXT_LABEL: dict[str, str] = {
    "en": "Official statutory text (US Code, OLRC release point 119-121).",
    "es": "Texto estatutario oficial (Código de los EE. UU., punto de publicación OLRC 119-121).",
}

OFFICIAL_TEXT_TRANSLATION_DISCLAIMER = (
    "The Spanish summary is an unofficial translation for reference only; the official "
    "English statutory text controls. / El resumen en español es una traducción no oficial "
    "solo para fines de referencia; prevalece el texto estatutario oficial en inglés."
)

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


def reference_labels(*, official_text: bool = False) -> dict[str, Any]:
    """Per-item labels; official_text marks rows holding the official statutory text."""
    if official_text:
        return {
            "content_type": CONTENT_TYPE_OFFICIAL_TEXT,
            "content_label": dict(OFFICIAL_TEXT_LABEL),
            "unofficial_summary": False,
            "translation_disclaimer": OFFICIAL_TEXT_TRANSLATION_DISCLAIMER,
        }
    return {
        "content_type": CONTENT_TYPE_REFERENCE_SUMMARY,
        "content_label": dict(REFERENCE_SUMMARY_LABEL),
        "unofficial_summary": True,
        "translation_disclaimer": STATUTE_TRANSLATION_DISCLAIMER,
    }


def output_notice() -> dict[str, Any]:
    """Top-level disclaimer and notice attached to every tool output."""
    return {"disclaimer": dict(BILINGUAL_DISCLAIMER), "content_notice": CONTENT_NOTICE}
