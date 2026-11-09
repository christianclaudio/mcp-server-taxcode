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

# Official text quoted from an annual US Code edition (prior-year rows); {edition} is its year.
USCODE_EDITION_TEXT_LABEL: dict[str, str] = {
    "en": "Official statutory text (US Code, {edition} edition, GPO).",
    "es": "Texto estatutario oficial (Código de los EE. UU., edición de {edition}, GPO).",
}

# Canada rows quote the Justice Canada consolidated XML at one laws-lois-xml commit. The
# Reproduction of Federal Law Order (SI/97-5) lets anyone reproduce federal law without charge
# or permission, provided due diligence is exercised in ensuring accuracy and the reproduction
# is not represented as an official version, or as made in affiliation with or with the
# endorsement of the Government of Canada. {commit} is the short commit id.
CANADA_TEXT_LABEL: dict[str, str] = {
    "en": (
        "Official statutory text of Canada (Justice Canada consolidation, laws-lois-xml "
        "commit {commit}). The English and French versions are equally authoritative "
        "(Charter s. 18(1); Official Languages Act s. 13). Reproduced under the Reproduction "
        "of Federal Law Order (SI/97-5), with due diligence as to accuracy; this is not an "
        "official version (Legislation Revision and Consolidation Act s. 31) and is not made "
        "in affiliation with, or with the endorsement of, the Government of Canada. The "
        "Spanish text is an unofficial summary."
    ),
    "es": (
        "Texto legal oficial de Canadá (codificación del Ministerio de Justicia de Canadá, "
        "laws-lois-xml commit {commit}). Las versiones en inglés y en francés son igualmente "
        "auténticas (Carta, art. 18(1); Ley de Lenguas Oficiales, art. 13). Reproducido "
        "conforme al Decreto sobre la reproducción de la legislación federal (TR/97-5), con "
        "la debida diligencia en cuanto a su exactitud; no es una versión oficial (Ley de "
        "revisión y codificación de textos legislativos, art. 31) ni se hace en "
        "asociación con el Gobierno de Canadá ni con su aval. El texto en español es un "
        "resumen no oficial."
    ),
    "fr": (
        "Texte législatif officiel du Canada (codification du ministère de la Justice, "
        "laws-lois-xml commit {commit}). Les versions française et anglaise ont également "
        "force de loi (Charte, par. 18(1); Loi sur les langues officielles, art. 13). "
        "Reproduit en vertu du Décret sur la reproduction de la législation fédérale "
        "(TR/97-5), avec une diligence raisonnable quant à son exactitude; il ne s’agit pas "
        "d’une version officielle (Loi sur la révision et la codification des textes "
        "législatifs, art. 31) et la reproduction n’est faite ni en association avec le "
        "gouvernement du Canada ni avec son appui. Le texte espagnol est un résumé non "
        "officiel."
    ),
}

CANADA_TRANSLATION_DISCLAIMER = (
    "The English and French texts are both official and equally authoritative; the Spanish "
    "summary is unofficial and for reference only. / Los textos en inglés y en francés son "
    "oficiales e igualmente auténticos; el resumen en español no es oficial y es solo de "
    "referencia. / Les textes anglais et français sont officiels et ont également force de "
    "loi; le résumé espagnol n’est pas officiel et n’est fourni qu’à titre de référence."
)

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


def reference_labels(
    *,
    official_text: bool = False,
    code_edition: int | None = None,
    canada_commit: str | None = None,
) -> dict[str, Any]:
    """Per-item labels; official_text marks rows holding the official statutory text.

    ``code_edition`` names the annual US Code edition an official text is quoted from;
    without it the label names the pinned OLRC release point. ``canada_commit`` (the
    laws-lois-xml commit of a Justice Canada text) gives the Canada label instead.
    """
    if official_text and canada_commit is not None:
        return {
            "content_type": CONTENT_TYPE_OFFICIAL_TEXT,
            "content_label": {
                k: v.format(commit=canada_commit[:7]) for k, v in CANADA_TEXT_LABEL.items()
            },
            "unofficial_summary": False,
            "translation_disclaimer": CANADA_TRANSLATION_DISCLAIMER,
        }
    if official_text:
        label = (
            dict(OFFICIAL_TEXT_LABEL)
            if code_edition is None
            else {k: v.format(edition=code_edition) for k, v in USCODE_EDITION_TEXT_LABEL.items()}
        )
        return {
            "content_type": CONTENT_TYPE_OFFICIAL_TEXT,
            "content_label": label,
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
