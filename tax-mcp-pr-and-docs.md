# Tax MCP follow-up: Puerto Rico reuse, sources, translation, docs site
Prepared Sun Oct 11, 2026, ~9:00 PM ET by Grok Bot. Builds on `tax-mcp-plan.md`. **[verified]** = checked this session; **[unverified]** = needs confirming. **Not legal advice.**

## TL;DR
- **Reuse:** statutes (CRI 2011) and binding regulations are almost certainly free to republish and translate (government edicts doctrine, Georgia v. PRO). Hacienda's own guidance (CC, DA, BI, Opiniones) and forms are *probably* fine, but that's the open legal question. Hacienda's site footer says "2014 © Departamento de Hacienda", and its terms page returns "Página no encontrada", so there are no published reuse terms. **Do not ingest LexJuris** (commercial, and it adds its own editorial work). A PR lawyer should confirm two points: (a) whether the edicts doctrine covers *executive-agency* guidance, and (b) how to treat Hacienda's © footer.
- **Sources:** everything needed is on hacienda.pr.gov. It's a Drupal site with filterable listings and **1,261 "Publicaciones Oficiales"** (410 Cartas Circulares, 158 DAs). The CRI is one born-digital PDF: 1,104 pages, ~3.67M characters, no OCR needed.
- **Translation:** Gemini 2.5 Flash on Vertex is the cheapest by far, roughly **$15–40 one-time** for an estimated ~35M-character corpus. Cloud Translation NMT costs $20/M characters (≈$700) but has native glossaries. Recommended: Gemini batch, with the glossary included in the prompt, Spanish kept authoritative, and every translation labeled "Traducción no oficial / Unofficial translation".
- **Docs site:** **Astro Starlight on Firebase Hosting (or GitHub Pages), $0.** It has built-in i18n, Pagefind search, and the `starlight-llms-txt` plugin, plus our own FastMCP server for the doc-MCP. Mintlify's free Starter tier includes a custom domain and an MCP server, but its **AI translations are Pro-only ($450/mo)**, and it's a hosted SaaS that's a poor fit for an auto-generated corpus of 10k+ pages.

## 1) Puerto Rico reuse: verdicts
Legal background:
- **17 U.S.C. §105** covers only works of the *U.S. Government* (federal officers and employees), not states or territories. It does not reach PR. The Court in *Georgia v. PRO* notes the Act "contains no similar prohibition against works of state governments" [verified, https://www.law.cornell.edu/supremecourt/text/18-1150].
- **Federal copyright law applies in PR** (PR Ley 55-2012's own exposición de motivos says "En Puerto Rico aplica el 'Copyright Act' de 1976") [verified, https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF/Propiedad%20Intelectual/55-2012.pdf]. So PR's edicts stand on the federal government edicts doctrine.
- **Georgia v. Public.Resource.Org, 590 U.S. 255 (2020):** officials "empowered to speak with the force of law cannot be the authors of ... the works they create in the course of their official duties". This covers judges and legislators, including non-binding annotations they write [verified, https://www.supremecourt.gov/opinions/19pdf/18-1150_7m58.pdf]. The Court did **not** decide whether executive-agency works are covered. That's the gap.
- **PR moral rights (Ley 55-2012):** these are attribution and integrity rights for authors. They're a low risk for government texts, but we should attribute the source and never alter the original. [unverified as to applicability to government works; lawyer]
- **Ley 122-2019 (Datos Abiertos):** "Open Data" means data that can be "usados, reutilizados y redistribuidos libre y gratuitamente por cualquier persona", and reuse "con fines comerciales o no comerciales" is expressly allowed. Its list of data to publish includes "Reglamentos, cartas, circulares, normativas ... y determinaciones" [verified via the text at https://bvirtualogp.pr.gov/ogp/bvirtual/leyesreferencia/pdf/122-2019.pdf ; English: https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF/2-ingles/122-2019.pdf]. This is strong *policy* support for reusing Hacienda circulars, even though the law sets no formal license.
- **Ley 141-2019 (Transparencia):** this is an access-to-information law. Hacienda links to it from its menu [verified link present]. It supports access, not a copyright waiver. [unverified details]
- **hacienda.pr.gov terms:** the footer reads "2014 © Departamento de Hacienda". `/terminos-y-condiciones` is a 404 ("Página no encontrada"). No reuse license was found. [verified]
- **LexJuris (lexjuris.com):** a private, commercial compilation with its own formatting and notes. Treat it as copyrighted and ToS-restricted. Link to it only; don't ingest. [unverified ToS]
- **OGP Biblioteca Virtual:** the correct host is `bvirtualogp.pr.gov` (`bvirtual.ogp.pr.gov` returned 404). It's a government compilation of laws in Spanish, with some in English. [verified host]

| Document type | Verdict | Basis / caveat |
|---|---|---|
| CRI 2011 (statute text, as amended) | **Republish + translate: yes (high confidence)** | The law itself; edicts doctrine (Banks, Georgia v. PRO). Use Hacienda's or OGP's compilation as the source. A compiler's *headings/notes* could be a thin layer: keep them attributed |
| Reglamentos (8049 and others, binding regs) | **Yes (high–medium)** | Have the force of law. But the Reglamento 8049 *compilation* says it's reference-only; label it that way |
| Cartas Circulares, Determinaciones Administrativas | **Probably yes (medium)** | Official guidance issued by the Secretary (statutory basis to confirm); Ley 122-2019 lists "cartas, circulares ... determinaciones" as open data. **Lawyer:** do executive edicts qualify? |
| Opiniones Administrativas / rulings to taxpayers | **Probably yes, redacted only (medium)** | Publish only what Hacienda itself has published (already redacted). Never pull private rulings |
| Boletines Informativos, Folletos, Tablas de Retención | **Likely yes (medium)** | Informational government works. The © footer is the main risk |
| Forms and instructions (Planillas) | **Yes to link and quote; host copies cautiously (medium)** | Low risk, but forms change yearly: link to the official PDF and index the instruction text |
| LexJuris, Microjuris, Westlaw PR | **No** | Private editorial work and ToS. Link only |
| Translations we make | **Ours, so we can license them CC0/CC BY** | Mark them "unofficial"; the Spanish text controls |

**Open legal questions (for a PR lawyer):** (1) Does the edicts doctrine reach Hacienda (executive) guidance, or do we rely on Ley 122-2019 plus fair use? (2) Is the "© Departamento de Hacienda" footer an actual copyright claim over its publications? (3) Should we send Hacienda a courtesy notice, or ask for written permission or open-data status (cheap insurance)? (4) Disclaimer wording: not tax advice, and the translation is not official, in both languages. (5) Moral rights (Ley 55-2012) and how to attribute government texts. (6) Do we need registration if we use the "Hacienda" name or seal? Don't use the seal.

## 2) PR source inventory
| Source | URL | Format / lang | Count / update | Status |
|---|---|---|---|---|
| CRI 2011 landing | https://hacienda.pr.gov/publicaciones/codigo-de-rentas-internas-de-puerto-rico-de-2011 | HTML | Page says "según enmendado al 28 de mayo de 2026" | [verified] |
| CRI PDF | https://hacienda.pr.gov/sites/default/files/codigo_de_rentas_internas_2011_segun_enmendado_28.08.2026.pdf | PDF, born-digital (Word 365), Spanish (has an «english» link marker, OGP style) | 1,104 pages, 9.2 MB, ~3.67M chars. One file replaced in full on each update. **The filename says 28.08.2026 but the page says May 28.** Diff on SHA, don't trust labels | [verified] |
| Code index (2011 + 1994) | https://hacienda.pr.gov/sobre-hacienda/publicaciones/area-de-rentas-internas-y-area-de-politica-contributiva/codigo-de-rentas-internas | HTML | | [verified earlier] |
| Reglamentos listing | https://hacienda.pr.gov/sobre-hacienda/publicaciones/area-de-rentas-internas-y-area-de-politica-contributiva/reglamentos | HTML → PDFs | Page loads (200). Count not parsed (JS/list markup) | [partly verified] |
| Reglamento 8049 compilation | https://hacienda.pr.gov/publicaciones/compilacion-del-reglamento-del-codigo-de-rentas-internas-de-puerto-rico-de-2011-segun-enmendado | PDF, Spanish | rev. Jul 13, 2026; "reference only" | [verified earlier] |
| Publicaciones Oficiales (all) | https://hacienda.pr.gov/sobre-hacienda/publicaciones/area-de-rentas-internas-y-area-de-politica-contributiva/publicaciones-oficiales | HTML listing → PDFs (/sites/default/files/publicaciones/YYYY/MM/) | **1,261** items; newest CC RI 26-13 (Jun 30, 2026) | [verified] |
| Filter `?field_pri_categorizacion_value=` | values: `carta-circular` (**410**), `determinacion-administrativa` (**158**), `boletin-informativo`, `boletin-informativo-politica-contributiva`, `carta-circular-politica-contributiva`, `folletos-informativos-de-rentas-internas`, `listas-oficiales`, `publicacion`, `tablas-de-retencion` | Mostly Spanish; some English (e.g. "Administrative Determination No. 23-04") | Date filters available (field_pri_fecha_antes_de/despues_de) → incremental crawl | [verified] |
| Opiniones Administrativas | Not a separate filter value; may fall under `publicacion` or not be published | | **unknown**: confirm | [unverified] |
| Planillas, Formularios y Anejos | https://hacienda.pr.gov/formularios (200) and "Planillas, Formularios y Anejos" menu | PDFs (fillable), Spanish, some English | Yearly per tax year; hundreds of files [unverified count] | [partly verified] |
| Contabilidad Central CCs (not tax) | .../contabilidad-central/cartas-circulares-de-contabilidad-central | | 595; **exclude** (gov accounting) | [verified] |
| OGP Biblioteca Virtual | https://bvirtualogp.pr.gov/ (laws in force, some English) | PDF | Cross-check the CRI and source laws like 60-2019 (Incentives Code) | [verified host] |
| Legislature SUTRA | https://sutra.oslpr.org/ (200) | HTML/PDF | Bills and enacted laws as they pass: an early signal that a CRI amendment is coming | [verified reachable] |
| Departamento de Estado | Leyes aprobadas/Reglamentos registry (Ley 38-2017 LPAU filings) | PDF | Official registry of filed regulations. URL to confirm | [unverified] |
| Ley 60-2019 Incentives Code (DDEC) | via bvirtualogp | PDF | Phase 1 add-on (Act 60 decrees are core PR tax) | [unverified] |

Size estimate (Spanish): CRI ~3.7M + regs ~3–5M + 1,261 publications at ~10–20k chars each ≈ 13–25M + form instructions ~5–10M ≈ **~25–45M characters (call it ~35M)**. This is an estimate.

## 3) Translation
Official prices [verified, https://cloud.google.com/translate/pricing ; https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing]:
- **Cloud Translation NMT:** $20 per 1M chars (first 500k chars/mo free). Glossary creation is free. Document translation is $0.08/page.
- **Translation LLM (Advanced):** $10 per 1M input chars + $10 per 1M output chars.
- **Gemini 2.5 Flash (Vertex):** $0.30 per 1M input tokens and $2.50 per 1M output tokens; Batch/Flex is $0.15 / $1.25.

Estimate for ~35M chars (≈9M tokens in, ~10M out at ~4 chars/token, assumed):
| Option | One-time cost | Notes |
|---|---|---|
| Gemini 2.5 Flash batch | **≈ $14** (+ glossary/prompt overhead → ~$20–40) | Best quality-per-$; glossary goes in the prompt; legal-register control |
| Gemini 2.5 Flash standard | ≈ $28 (~$40–60 with overhead) | |
| Translation LLM | ≈ $700 | |
| NMT + glossary | ≈ $700 | Deterministic, native glossary |
Ongoing diffs (a few CCs per week plus CRI amendments): **under $1/mo** on Gemini.

Approach: translate per section (CRI "Sección xxxx.xx", CC paragraphs) so the EN and ES pages align 1:1. Spanish is authoritative. Every EN page gets a banner "Unofficial translation. The Spanish original controls", plus a link to the source PDF. Keep a versioned glossary (`glossary.csv`: planilla→return, contribución sobre ingresos→income tax, IVU→sales and use tax (IVU), arbitrios→excise taxes, Secretario→Secretary of the Treasury, decreto→decree, etc.), and seed it from Hacienda's own English documents and OGP's English laws. Only re-translate sections whose SHA changed. QA: back-translate a sample, check the glossary terms, and get a human spot-check on the top 50 CRI sections. Translate the federal content to Spanish later, on the same pipeline.

## 4) Docs site comparison
| | Mintlify Starter | Starlight (Astro) | Docusaurus | MkDocs Material | Fumadocs |
|---|---|---|---|---|---|
| Price | $0; Pro $450/mo [verified https://www.mintlify.com/pricing] | $0 OSS | $0 OSS | $0 OSS | $0 OSS |
| Custom domain | Yes on Starter | Yes (Firebase/GH Pages) | Yes | Yes | Yes (needs a server/Vercel for full features) |
| MCP server | Built in on Starter | DIY: FastMCP over the markdown | DIY | DIY | DIY |
| llms.txt | Built in | `starlight-llms-txt` (llms/-full/-small, customSets per jurisdiction) | `docusaurus-plugin-llms` | `mkdocs-llmstxt` (no .md per page) | Built in (`npx @fumadocs/cli feature llms`, lang-aware `index(lang)`) |
| EN/ES i18n | Supported; **AI translations Pro-only** | Built in (locales, fallback banner) | Built in (mature) | Plugin (mkdocs-static-i18n) | Built in |
| Search | Built in | Pagefind (static, scales) | Algolia DocSearch or local plugin | lunr built in (heavy at scale) | Orama |
| 10k+ pages | Hosted; repo-sync limits [unverified] | Good; static builds; Pagefind chunked | OK but slow builds and memory-hungry [unverified] | Slow search index at scale | Next.js; fine |
| OSS program | Mintlify has had an OSS program [unverified, check] | n/a | n/a | n/a | n/a |

**Recommendation: Starlight + `starlight-llms-txt` + Pagefind on Firebase Hosting** (GitHub Pages works as a fallback). It costs $0, handles i18n with a fallback note out of the box, and generates per-jurisdiction llms sets. The doc MCP is the tax MCP itself: FastMCP serving the same markdown/SQLite, so the docs aren't duplicated. Fumadocs is runner-up if a lang-aware `llms.txt` per locale matters a lot. Revisit Mintlify only if their OSS program grants Pro features for free.

## 5) Recommended stack
Cloud Run Jobs + Scheduler crawlers (Hacienda filter + date window, SHA diffs) → GCS raw → pdftotext/PyMuPDF section splitter → Markdown ES (authoritative) → Gemini 2.5 Flash batch EN with glossary → git repo `content/{es,en}/pr/...` → Starlight build → Firebase Hosting with `/llms.txt`, `/es/llms.txt` and per-jurisdiction `llms-full` → SQLite FTS5 index baked into the FastMCP server (from Christian's template) on Cloud Run. Tools: `search`, `get_section`, `cite("PR CRI Sec. 1031.01")`, `list_publications(type, since)`, `changes_since(date)`, all returning `lang`, `official: true/false` and source URL.

## 6) Updated Phase 1: Puerto Rico first (~4–5 weeks, estimate)
1. **Week 0:** legal review (questions above); choose the domain and repo name; optional courtesy email to Hacienda (needs Christian's go).
2. **Week 1:** Hacienda crawler (listing + filters + date windows), CRI and Reglamento 8049 download, SHA change detection, raw archive.
3. **Week 2:** CRI section splitter (Sección xxxx.xx, Subtítulo/Capítulo), CC/DA/BI metadata (number, date, asunto), ES markdown with provenance.
4. **Week 3:** glossary v1 plus Gemini batch EN, with the banner and QA sample; Starlight site with ES default and EN locale; llms.txt sets.
5. **Week 4:** FastMCP server from the template (100% tests), SQLite FTS5 bilingual index, cite parser for CRI/CC/DA/BI numbers.
6. **Week 5:** Scheduler (daily listing crawl, weekly CRI hash check), stale flags, SUTRA watch for new laws, deploy; registry listing only on Christian's go.
Phase 1b: Ley 60-2019 Incentives Code and its Reglamento. Then IRS (Phase 2), then states (Phase 3).
Phase 1 cost: translation ~$20–60 one-time, then under $1/mo. Hosting stays as in the plan (~$15–50/mo, mostly Cloud Run/DB, estimated).
