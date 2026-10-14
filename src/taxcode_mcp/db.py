"""DuckDB database layer for statutory corpora and regulatory guidance."""

from __future__ import annotations

import logging
import threading
from typing import Any

import duckdb

from taxcode_mcp.config import settings

logger = logging.getLogger(__name__)

STATUTE_TRANSLATION_DISCLAIMER = (
    "Unofficial translation/summary for reference purposes only. Official "
    "statutory text in the jurisdiction's authoritative language controls."
)

# Canonical initial seed data for US Federal and Puerto Rico tax provisions
INITIAL_STATUTES: list[dict[str, Any]] = [
    {
        "id": "us-irc-933",
        "jurisdiction": "US",
        "corpus": "IRC",
        "citation": "26 U.S.C. § 933",
        "section": "933",
        "title": "Income from sources within Puerto Rico",
        "text_es": (
            "En el caso de un individuo que sea residente de buena fe de Puerto Rico durante todo "
            "el año tributario, el ingreso bruto no incluirá ingresos derivados de fuentes dentro "
            "de Puerto Rico (excepto cantidades por servicios prestados a los Estados Unidos)."
        ),
        "text_en": (
            "In the case of an individual who is a bona fide resident of Puerto Rico during the "
            "entire taxable year, gross income does not include income derived from sources within "
            "Puerto Rico (except amounts received for services performed as an employee of the US)."
        ),
        "effective_year": 1954,
        "source_url": "https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title26-section933",
        "cross_references": ["26 U.S.C. § 937", "26 CFR § 1.937-1", "26 U.S.C. § 865"],
    },
    {
        "id": "us-irc-937",
        "jurisdiction": "US",
        "corpus": "IRC",
        "citation": "26 U.S.C. § 937",
        "section": "937",
        "title": "Residence and source rules involving possessions",
        "text_es": (
            "Establece los requisitos estatutarios para determinar residencia de buena fe en "
            "posesiones (presencia física, centro tributario 'tax home' y conexión más cercana), "
            "así como las reglas de fuente de ingresos aplicables a posesiones."
        ),
        "text_en": (
            "Rules for determining bona fide residency in possessions (presence test, tax home "
            "test, and closer connection test) and statutory principles for sourcing income."
        ),
        "effective_year": 2004,
        "source_url": "https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title26-section937",
        "cross_references": ["26 U.S.C. § 933", "26 CFR § 1.937-1", "26 CFR § 1.937-2"],
    },
    {
        "id": "us-irc-865",
        "jurisdiction": "US",
        "corpus": "IRC",
        "citation": "26 U.S.C. § 865",
        "section": "865",
        "title": "Source rules for personal property sales",
        "text_es": (
            "Salvo disposición expresa en contrario, el ingreso derivado de la venta de bienes "
            "personales (incluyendo valores negociables y acciones) se atribuirá a la residencia "
            "del vendedor."
        ),
        "text_en": (
            "Except as otherwise provided in this section, income from the sale of personal "
            "property (including corporate stock and marketable securities) is sourced to the "
            "residence of the seller."
        ),
        "effective_year": 1986,
        "source_url": "https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title26-section865",
        "cross_references": ["26 U.S.C. § 937", "26 CFR § 1.937-2"],
    },
    {
        "id": "us-cfr-1-937-1",
        "jurisdiction": "US",
        "corpus": "CFR",
        "citation": "26 CFR § 1.937-1",
        "section": "1.937-1",
        "title": "Bona fide residency in a possession",
        "text_es": (
            "Reglamentación del Tesoro que articula las cinco alternativas de presencia física "
            "(regla de 183 días, regla de 549 días en 3 años, límite de 90 días en EE.UU.), "
            "el requisito de no mantener tax home fuera, y los factores de conexión más cercana."
        ),
        "text_en": (
            "Treasury Regulation detailing the presence test alternatives (183-day rule, 549-day "
            "3-year rule, 90-day US presence limit), the tax home rule, and closer connection."
        ),
        "effective_year": 2006,
        "source_url": "https://www.ecfr.gov/current/title-26/section-1.937-1",
        "cross_references": ["26 U.S.C. § 937", "26 CFR § 1.937-2"],
    },
    {
        "id": "us-cfr-1-937-2",
        "jurisdiction": "US",
        "corpus": "CFR",
        "citation": "26 CFR § 1.937-2",
        "section": "1.937-2",
        "title": "Income from sources within a possession",
        "text_es": (
            "Reglas de fuente para posesiones y regla de lookback de 10 años para ganancias "
            "acumuladas (built-in gains) sobre bienes adquiridos antes de establecer residencia."
        ),
        "text_en": (
            "Sourcing rules for possessions and 10-year built-in gain lookback rules under "
            "Treas. Reg. § 1.937-2(f) for property held prior to establishing bona fide residency."
        ),
        "effective_year": 2006,
        "source_url": "https://www.ecfr.gov/current/title-26/section-1.937-2",
        "cross_references": ["26 U.S.C. § 937", "26 CFR § 1.937-1"],
    },
    {
        "id": "pr-cri-1010-01",
        "jurisdiction": "PR",
        "corpus": "CRI",
        "citation": "PR CRI Sec. 1010.01",
        "section": "1010.01",
        "title": "Definiciones generales / General Definitions",
        "text_es": (
            "Define términos fundamentales bajo el Código de Rentas Internas de Puerto Rico "
            "(Ley 1-2011), incluyendo individuo residente, corporación foránea y año contributivo."
        ),
        "text_en": (
            "Defines fundamental statutory terms under the PR Internal Revenue Code (Ley 1-2011), "
            "including resident individual, foreign corporation, taxable year, and person."
        ),
        "effective_year": 2011,
        "source_url": "https://hacienda.pr.gov/publicaciones/codigo-de-rentas-internas-de-puerto-rico-de-2011",
        "cross_references": ["PR CRI Sec. 1031.01", "PR CRI Sec. 1062.01"],
    },
    {
        "id": "pr-cri-1031-01",
        "jurisdiction": "PR",
        "corpus": "CRI",
        "citation": "PR CRI Sec. 1031.01",
        "section": "1031.01",
        "title": "Ingreso bruto / Gross Income",
        "text_es": (
            "Incluye ganancias, beneficios e ingresos derivados de sueldos, profesiones, "
            "comercio o ventas en propiedad, y excluye partidas estatutariamente exentas."
        ),
        "text_en": (
            "Includes gains, profits, and income derived from salaries, professions, commerce, "
            "or sales of property, while excluding statutory exclusions."
        ),
        "effective_year": 2011,
        "source_url": "https://hacienda.pr.gov/publicaciones/codigo-de-rentas-internas-de-puerto-rico-de-2011",
        "cross_references": ["PR CRI Sec. 1010.01", "PR CRI Sec. 1031.02"],
    },
    {
        "id": "pr-ley60-2021-01",
        "jurisdiction": "PR",
        "corpus": "LEY60",
        "citation": "Ley 60-2019 Sec. 2021.01",
        "section": "2021.01",
        "title": "Individuos Residentes Inversionistas - Cualificación",
        "text_es": (
            "Establece requisitos para acogerse al Capítulo 2 del Código de Incentivos, "
            "incluyendo no haber sido residente de PR en los 10 años anteriores y "
            "adquirir una residencia principal."
        ),
        "text_en": (
            "Establishes eligibility criteria for Individual Resident Investors under Act 60 "
            "Chapter 2 (formerly Act 22), including 10-year non-residency and home purchase."
        ),
        "effective_year": 2019,
        "source_url": "https://bvirtualogp.pr.gov/",
        "cross_references": ["Ley 60-2019 Sec. 2022.01", "26 U.S.C. § 933"],
    },
    {
        "id": "pr-ley60-2022-01",
        "jurisdiction": "PR",
        "corpus": "LEY60",
        "citation": "Ley 60-2019 Sec. 2022.01",
        "section": "2022.01",
        "title": "Exención Contributiva sobre Ganancias de Capital e Intereses",
        "text_es": (
            "Dispone exención total (100%) sobre ganancias de capital e intereses para decretos "
            "previos a 2027 (y tasa del 4% para decretos solicitados desde 2027)."
        ),
        "text_en": (
            "Provides 100% tax exemption on capital gains, dividends, and interest post-residency "
            "(4% capital gains rate for decrees requested on or after Jan 1, 2027)."
        ),
        "effective_year": 2019,
        "source_url": "https://bvirtualogp.pr.gov/",
        "cross_references": ["Ley 60-2019 Sec. 2021.01", "26 CFR § 1.937-2"],
    },
    {
        "id": "pr-ley60-2031-01",
        "jurisdiction": "PR",
        "corpus": "LEY60",
        "citation": "Ley 60-2019 Sec. 2031.01",
        "section": "2031.01",
        "title": "Exportación de Servicios y Comercio - Cualificación",
        "text_es": (
            "Cualifica negocios dedicados a la exportación de servicios elegibles prestados "
            "desde Puerto Rico hacia mercados exteriores sin nexo previo en la isla (ex-Ley 20)."
        ),
        "text_en": (
            "Qualifies businesses engaged in export services conducted from Puerto Rico to "
            "foreign or mainland markets with no prior nexus in the island (formerly Act 20)."
        ),
        "effective_year": 2019,
        "source_url": "https://bvirtualogp.pr.gov/",
        "cross_references": ["Ley 60-2019 Sec. 2032.01", "26 U.S.C. § 957"],
    },
    {
        "id": "pr-ley60-2032-01",
        "jurisdiction": "PR",
        "corpus": "LEY60",
        "citation": "Ley 60-2019 Sec. 2032.01",
        "section": "2032.01",
        "title": "Tasa Fija Preferencial de 4% sobre Ingreso de Desarrollo",
        "text_es": (
            "Fija una tasa del 4% sobre el ingreso neto de desarrollo para exportación, "
            "100% de exención sobre dividendos, y 75% de exención en contribución "
            "sobre la propiedad."
        ),
        "text_en": (
            "Establishes a 4% corporate rate on export net income, 100% exemption on dividends, "
            "and 75% exemption on property taxes."
        ),
        "effective_year": 2019,
        "source_url": "https://bvirtualogp.pr.gov/",
        "cross_references": ["Ley 60-2019 Sec. 2031.01", "PR CRI Sec. 1010.01"],
    },
    {
        "id": "pr-act22-2012",
        "jurisdiction": "PR",
        "corpus": "LEY22",
        "citation": "Act 22-2012 Sec. 3",
        "section": "3",
        "title": "Exención Contributiva sobre Ganancias de Capital (Histórico Ley 22)",
        "text_es": (
            "Exención total del 100% sobre ganancias de capital devengadas luego de establecer "
            "residencia bajo la Ley 22 original (sin requisito de donación anual compulsoria)."
        ),
        "text_en": (
            "Predecessor 100% tax exemption on capital gains accrued post-residency under original "
            "Act 22-2012 (prior to Act 60 mandatory charitable donations)."
        ),
        "effective_year": 2012,
        "superseded_year": 2019,
        "source_url": "https://bvirtualogp.pr.gov/",
        "cross_references": ["Ley 60-2019 Sec. 2022.01", "26 U.S.C. § 933"],
    },
    {
        "id": "ga-ocga-48-7-1",
        "jurisdiction": "GA",
        "corpus": "OCGA",
        "citation": "O.C.G.A. § 48-7-1",
        "section": "48-7-1",
        "title": "Income Tax Definitions / Residency Rules",
        "text_es": (
            "Define residente de Georgia como todo individuo con residencia legal en "
            "Georgia el día de pago del impuesto, que resida en el estado al menos "
            "183 días del año contributivo, o que mantenga una vivienda permanente en Georgia."
        ),
        "text_en": (
            "Defines Georgia resident as any individual who is a legal resident of Georgia on "
            "income tax day, who resides within the state for 183 days or more in the aggregate "
            "during the taxable year, or who maintains a permanent place of abode within the state."
        ),
        "effective_year": 2024,
        "source_url": "https://dor.georgia.gov/",
        "cross_references": [
            "O.C.G.A. § 48-7-20",
            "O.C.G.A. § 48-7-30",
            "Ga. Comp. R. & Regs. r. 560-7-3-.01",
        ],
    },
    {
        "id": "ga-ocga-48-7-20-2024",
        "jurisdiction": "GA",
        "corpus": "OCGA",
        "citation": "O.C.G.A. § 48-7-20",
        "section": "48-7-20",
        "title": "Individual Income Tax Rates (2024 Flat Rate: 5.49%)",
        "text_es": (
            "Establece una tasa impositiva fija (flat tax) de 5.49% sobre el ingreso neto "
            "tributable de individuos para el año contributivo 2024 bajo HB 1023."
        ),
        "text_en": (
            "Imposes a flat individual income tax rate of 5.49% on Georgia taxable net income "
            "for tax year 2024 pursuant to HB 1023, eliminating previous graduated brackets."
        ),
        "effective_year": 2024,
        "superseded_year": 2025,
        "source_url": "https://dor.georgia.gov/taxes/individual-income-tax",
        "cross_references": ["O.C.G.A. § 48-7-1", "O.C.G.A. § 48-7-27"],
    },
    {
        "id": "ga-ocga-48-7-20-2025",
        "jurisdiction": "GA",
        "corpus": "OCGA",
        "citation": "O.C.G.A. § 48-7-20",
        "section": "48-7-20",
        "title": "Individual Income Tax Rates (2025 Flat Rate: 5.39%)",
        "text_es": (
            "Acelera la reducción de la tasa impositiva individual fija al 5.39% para el "
            "año contributivo 2025 bajo HB 1015."
        ),
        "text_en": (
            "Accelerates the reduction of the flat individual income tax rate to 5.39% for "
            "tax year 2025 pursuant to HB 1015."
        ),
        "effective_year": 2025,
        "superseded_year": 2026,
        "source_url": "https://dor.georgia.gov/taxes/individual-income-tax",
        "cross_references": ["O.C.G.A. § 48-7-1", "O.C.G.A. § 48-7-27"],
    },
    {
        "id": "ga-ocga-48-7-20-2026",
        "jurisdiction": "GA",
        "corpus": "OCGA",
        "citation": "O.C.G.A. § 48-7-20",
        "section": "48-7-20",
        "title": "Individual Income Tax Rates (2026 Target Flat Rate: 4.99%)",
        "text_es": (
            "Establece la tasa impositiva fija objetivo de 4.99% sobre el ingreso neto "
            "tributable a partir del 1 de enero de 2026 bajo HB 1023."
        ),
        "text_en": (
            "Imposes the target flat individual income tax rate of 4.99% for taxable years "
            "beginning on or after January 1, 2026 pursuant to HB 1023."
        ),
        "effective_year": 2026,
        "superseded_year": None,
        "source_url": "https://dor.georgia.gov/taxes/individual-income-tax",
        "cross_references": ["O.C.G.A. § 48-7-1", "O.C.G.A. § 48-7-27"],
    },
    {
        "id": "ga-ocga-48-7-27",
        "jurisdiction": "GA",
        "corpus": "OCGA",
        "citation": "O.C.G.A. § 48-7-27",
        "section": "48-7-27",
        "title": "Computation of Georgia Taxable Net Income",
        "text_es": (
            "Dispone el cálculo del ingreso neto gravable a partir del AGI federal, deducción "
            "estándar de $12,000 (solteros) o $24,000 (casados), y exclusión de hasta $65,000 "
            "en ingresos de jubilación para mayores de 65 años."
        ),
        "text_en": (
            "Calculates Georgia taxable net income from Federal AGI, allowing standard "
            "deductions of $12,000 (single) or $24,000 (married filing jointly), and a "
            "retirement income exclusion of up to $65,000 for age 65+ ($35,000 for ages 62-64)."
        ),
        "effective_year": 2024,
        "source_url": "https://dor.georgia.gov/taxes/individual-income-tax",
        "cross_references": ["O.C.G.A. § 48-7-20", "26 U.S.C. § 62"],
    },
    {
        "id": "ga-ocga-48-7-30",
        "jurisdiction": "GA",
        "corpus": "OCGA",
        "citation": "O.C.G.A. § 48-7-30",
        "section": "48-7-30",
        "title": "Taxation of Nonresidents",
        "text_es": (
            "Grava a personas no residentes sobre su ingreso bruto derivado de fuentes de "
            "Georgia, incluyendo compensación por servicios realizados en Georgia y "
            "actividades comerciales locales."
        ),
        "text_en": (
            "Taxes nonresidents on gross income derived from Georgia sources, including "
            "compensation for services performed within Georgia, property located in Georgia, "
            "and business conducted in the state."
        ),
        "effective_year": 2024,
        "source_url": "https://dor.georgia.gov/",
        "cross_references": ["O.C.G.A. § 48-7-1", "O.C.G.A. § 48-7-20"],
    },
    {
        "id": "ga-reg-560-7-3-01",
        "jurisdiction": "GA",
        "corpus": "GAREG",
        "citation": "Ga. Comp. R. & Regs. r. 560-7-3-.01",
        "section": "560-7-3-.01",
        "title": "Residence and Domicile",
        "text_es": (
            "Reglamento del Departamento de Ingresos de Georgia sobre domicilio y residencia. "
            "El domicilio en Georgia se presume continuo a menos que el contribuyente demuestre "
            "abandono definitivo y adquisición de nuevo domicilio."
        ),
        "text_en": (
            "Georgia Department of Revenue rule governing residence and legal domicile. "
            "Domicile in Georgia is presumed to continue until the taxpayer establishes "
            "abandonment and acquisition of a permanent domicile elsewhere."
        ),
        "effective_year": 2023,
        "source_url": "https://rules.sos.ga.gov/gac/560-7-3-.01",
        "cross_references": ["O.C.G.A. § 48-7-1", "26 U.S.C. § 937"],
    },
    # State of California (Revenue and Taxation Code & FTB Regulations)
    {
        "id": "ca-rtc-17014",
        "jurisdiction": "CA",
        "corpus": "RTC",
        "citation": "Cal. Rev. & Tax Code § 17014",
        "section": "17014",
        "title": "Definition of Resident (Non-Temporary Purpose Test)",
        "text_es": (
            "Define residente como todo individuo que permanezca en California para fines "
            "que no sean temporales o transitorios, y todo domiciliado que esté fuera del estado "
            "de forma temporal. Aplica el criterio de conexión más cercana."
        ),
        "text_en": (
            "Defines resident as every individual who is in California for other than a temporary "
            "or transitory purpose, and every individual domiciled in California who is outside "
            "the state for a temporary or transitory purpose. Presumption under § 17016 "
            "at 9 months."
        ),
        "effective_year": 2024,
        "source_url": "https://www.ftb.ca.gov/",
        "cross_references": [
            "Cal. Rev. & Tax Code § 17041",
            "18 CCR § 17014",
        ],
    },
    {
        "id": "ca-rtc-17041",
        "jurisdiction": "CA",
        "corpus": "RTC",
        "citation": "Cal. Rev. & Tax Code § 17041",
        "section": "17041",
        "title": "Individual Income Tax Rates and Brackets",
        "text_es": (
            "Grava el ingreso tributable de California con tasas escalonadas del 1% al 12.3%, "
            "más 1% de recargo para servicios de salud mental sobre ingresos mayores a $1M "
            "(Proposición 63), para una tasa marginal máxima de 13.3%."
        ),
        "text_en": (
            "Imposes graduated income tax rates on California taxable income ranging from 1% to "
            "12.3%, plus a 1% Mental Health Services Tax surcharge on taxable income exceeding "
            "$1,000,000, creating a top marginal individual rate of 13.3%."
        ),
        "effective_year": 2024,
        "source_url": "https://www.ftb.ca.gov/",
        "cross_references": ["Cal. Rev. & Tax Code § 17014"],
    },
    {
        "id": "ca-rtc-17951",
        "jurisdiction": "CA",
        "corpus": "RTC",
        "citation": "Cal. Rev. & Tax Code § 17951",
        "section": "17951",
        "title": "Gross Income of Nonresidents and Sourcing",
        "text_es": (
            "Para no residentes, el ingreso bruto comprende únicamente ingresos derivados de "
            "fuentes en California, incluyendo negocios, profesiones y servicios personales "
            "realizados dentro del estado."
        ),
        "text_en": (
            "In the case of nonresidents, gross income includes only gross income from sources "
            "within California, including income from a business, trade, or profession conducted "
            "in the state, and compensation for personal services performed in California."
        ),
        "effective_year": 2024,
        "source_url": "https://www.ftb.ca.gov/",
        "cross_references": [
            "Cal. Rev. & Tax Code § 17014",
            "Cal. Rev. & Tax Code § 17041",
        ],
    },
    {
        "id": "ca-ccr-17014",
        "jurisdiction": "CA",
        "corpus": "CACCR",
        "citation": "18 CCR § 17014",
        "section": "17014",
        "title": "FTB Residence and Domicile Guidelines",
        "text_es": (
            "Reglamento de la FTB sobre residencia y domicilio. Aplica el criterio de conexión "
            "más cercana evaluando propiedades inmuebles, familia, cuentas bancarias, licencias "
            "profesionales y afiliaciones comerciales."
        ),
        "text_en": (
            "FTB regulation detailing domicile and residence determination. A person may have "
            "multiple residences but only one domicile. Applies the closest connection test "
            "evaluating real property, family, bank accounts, business affiliations, and licenses."
        ),
        "effective_year": 2023,
        "source_url": "https://www.ftb.ca.gov/",
        "cross_references": ["Cal. Rev. & Tax Code § 17014"],
    },
    # State of New York (Tax Law & Department of Taxation and Finance Regulations)
    {
        "id": "ny-tax-605",
        "jurisdiction": "NY",
        "corpus": "NYTAX",
        "citation": "NY Tax Law § 605(b)",
        "section": "605",
        "title": "Resident Individual and Domicile Defined",
        "text_es": (
            "Define individuo residente como domiciliado en Nueva York a menos que no mantenga "
            "vivienda permanente en NY y pase menos de 30 días en el estado; o no domiciliado "
            "que mantenga vivienda permanente y pase más de 183 días en NY (Residente Estatutario)."
        ),
        "text_en": (
            "Defines resident individual as one domiciled in New York unless they maintain no "
            "permanent abode in NY and spend under 30 days in state; or one not domiciled who "
            "maintains a permanent place of abode and spends over 183 days in NY."
        ),
        "effective_year": 2024,
        "source_url": "https://www.tax.ny.gov/",
        "cross_references": ["NY Tax Law § 601", "20 NYCRR § 105.20"],
    },
    {
        "id": "ny-tax-601",
        "jurisdiction": "NY",
        "corpus": "NYTAX",
        "citation": "NY Tax Law § 601",
        "section": "601",
        "title": "Personal Income Tax Rates",
        "text_es": (
            "Grava el ingreso tributable de Nueva York con tasas escalonadas de hasta 10.9% "
            "para personas con altos ingresos, más el impuesto municipal de la Ciudad de "
            "Nueva York (hasta 3.876%) para residentes de NYC."
        ),
        "text_en": (
            "Imposes graduated income tax on New York taxable income with top marginal rates up "
            "to 10.9% for high-income earners, plus applicable New York City personal income tax "
            "(up to 3.876%) for NYC residents."
        ),
        "effective_year": 2024,
        "source_url": "https://www.tax.ny.gov/",
        "cross_references": ["NY Tax Law § 605"],
    },
    {
        "id": "ny-reg-105-20",
        "jurisdiction": "NY",
        "corpus": "NYCRR",
        "citation": "20 NYCRR § 105.20",
        "section": "105.20",
        "title": "Rules for Determining Domicile (5 Primary Factors)",
        "text_es": (
            "Reglamento de auditoría de domicilio de NY que establece los 5 factores primarios: "
            "Vivienda, Negocio Activo, Tiempo, Objetos de Valor Sentimental ('Near and Dear') "
            "y Familia."
        ),
        "text_en": (
            "New York DTF regulation establishing the 5 primary factors for domicile audit "
            "evaluation: Home, Active Business Involvement, Time Allocation, Items of Personal "
            "Significance ('Near and Dear'), and Family."
        ),
        "effective_year": 2024,
        "source_url": "https://www.tax.ny.gov/",
        "cross_references": ["NY Tax Law § 605"],
    },
    {
        "id": "ny-reg-132-18",
        "jurisdiction": "NY",
        "corpus": "NYCRR",
        "citation": "20 NYCRR § 132.18",
        "section": "132.18",
        "title": "Convenience of the Employer Rule",
        "text_es": (
            "Dispone que la compensación de empleados no residentes por servicios realizados fuera "
            "del estado se atribuye a Nueva York si el trabajo remoto se realiza por conveniencia "
            "del empleado y no por necesidad del empleador."
        ),
        "text_en": (
            "Provides that compensation of a nonresident employee for services performed out of "
            "state is sourced to New York if the services are performed outside NY for the "
            "employee's convenience rather than by necessity of the employer."
        ),
        "effective_year": 2024,
        "source_url": "https://www.tax.ny.gov/",
        "cross_references": ["NY Tax Law § 605"],
    },
    # State of Florida (Constitutional & Statutory Absence of Individual Income Tax)
    {
        "id": "fl-const-7-5",
        "jurisdiction": "FL",
        "corpus": "FLCONST",
        "citation": "Fla. Const. Art. VII § 5",
        "section": "Art. VII Sec. 5",
        "title": "Constitutional Prohibition of Personal Income Tax",
        "text_es": (
            "Prohíbe constitucionalmente cualquier impuesto sobre el ingreso de personas "
            "naturales residentes o ciudadanas de Florida, garantizando la ausencia de "
            "impuesto estatal sobre ingresos individuales."
        ),
        "text_en": (
            "Prohibits any tax upon the income of natural persons who are residents or citizens "
            "of Florida, guaranteeing the absence of state individual income taxation."
        ),
        "effective_year": 1968,
        "source_url": "https://www.flsenate.gov/",
        "cross_references": ["Fla. Stat. § 222.17"],
    },
    {
        "id": "fl-stat-222-17",
        "jurisdiction": "FL",
        "corpus": "FLSTAT",
        "citation": "Fla. Stat. § 222.17",
        "section": "222.17",
        "title": "Manifesting and Evidencing Domicile in Florida",
        "text_es": (
            "Autoriza a toda persona que haya establecido domicilio en Florida a manifestarlo "
            "formalmente mediante declaración jurada radicada ante el secretario del tribunal de "
            "circuito del condado correspondiente."
        ),
        "text_en": (
            "Authorizes any person who has established a domicile in Florida to manifest that "
            "domicile by filing a sworn, notarized statement of domicile with the clerk of the "
            "circuit court in the county of residence."
        ),
        "effective_year": 2024,
        "source_url": "https://www.flsenate.gov/",
        "cross_references": ["Fla. Const. Art. VII § 5", "Fla. Stat. § 196.031"],
    },
    {
        "id": "fl-stat-196-031",
        "jurisdiction": "FL",
        "corpus": "FLSTAT",
        "citation": "Fla. Stat. § 196.031",
        "section": "196.031",
        "title": "Exemption of Homesteads",
        "text_es": (
            "Concede hasta $50,000 de exención contributiva sobre la propiedad principal a "
            "residentes permanentes. Solicitar la exención de 'homestead' constituye prueba "
            "prima facie de domicilio en Florida."
        ),
        "text_en": (
            "Grants up to $50,000 homestead property tax exemption to persons who maintain "
            "permanent residence. Claiming Florida homestead establishes prima facie proof of "
            "Florida domicile."
        ),
        "effective_year": 2024,
        "source_url": "https://www.flsenate.gov/",
        "cross_references": ["Fla. Stat. § 222.17"],
    },
    # State of Texas (Constitutional Prohibition of Individual Income Tax)
    {
        "id": "tx-const-8-24a",
        "jurisdiction": "TX",
        "corpus": "TXCONST",
        "citation": "Tex. Const. art. VIII § 24-a",
        "section": "Art. VIII Sec. 24-a",
        "title": "Constitutional Prohibition of Personal Income Tax",
        "text_es": (
            "Enmienda constitucional aprobada en 2019 que prohíbe a la legislatura de Texas "
            "imponer impuestos sobre el ingreso neto de individuos o su participación en "
            "sociedades y asociaciones no incorporadas."
        ),
        "text_en": (
            "Constitutional amendment approved by voters in 2019 prohibiting the Texas legislature "
            "from imposing a tax on the net incomes of individuals, including an individual's "
            "share of partnership and unincorporated association income."
        ),
        "effective_year": 2019,
        "source_url": "https://statutes.capitol.texas.gov/",
        "cross_references": ["Tex. Tax Code § 171.001"],
    },
    {
        "id": "tx-tax-171-001",
        "jurisdiction": "TX",
        "corpus": "TXTAX",
        "citation": "Tex. Tax Code § 171.001",
        "section": "171.001",
        "title": "Texas Franchise Tax (Entity Level)",
        "text_es": (
            "Impone el impuesto de franquicia únicamente a entidades comerciales organizadas o con "
            "negocios en Texas sobre su margen bruto, sin gravar a personas naturales."
        ),
        "text_en": (
            "Imposes franchise tax on each taxable entity chartered or organized in Texas or doing "
            "business in Texas based on gross margin, maintaining zero taxation on individuals."
        ),
        "effective_year": 2024,
        "source_url": "https://statutes.capitol.texas.gov/",
        "cross_references": ["Tex. Const. art. VIII § 24-a"],
    },
    # State of Washington (Capital Gains Excise Tax & No Income Tax)
    {
        "id": "wa-rcw-82-87-040",
        "jurisdiction": "WA",
        "corpus": "RCW",
        "citation": "RCW 82.87.040",
        "section": "82.87.040",
        "title": "Tax Imposed on Long-Term Capital Gains",
        "text_es": (
            "Impuesto especial del 7% sobre ganancias de capital a largo plazo de individuos "
            "que excedan la deducción estándar anual ($250,000 indexada por inflación)."
        ),
        "text_en": (
            "7% excise tax on individual annual net long-term capital gains allocated to "
            "Washington in excess of statutory standard deduction ($250,000 indexed for inflation)."
        ),
        "effective_year": 2022,
        "source_url": "https://app.leg.wa.gov/RCW/default.aspx?cite=82.87.040",
        "cross_references": ["RCW 82.87.020", "Wash. Const. art. VII § 1"],
    },
    {
        "id": "wa-rcw-82-87-020",
        "jurisdiction": "WA",
        "corpus": "RCW",
        "citation": "RCW 82.87.020",
        "section": "82.87.020",
        "title": "Capital Gains Tax Definitions and Resident Sourcing",
        "text_es": (
            "Define residente de Washington para el impuesto de ganancias de capital como "
            "persona natural domiciliada en el estado al momento de la venta o intercambio."
        ),
        "text_en": (
            "Defines resident for Washington capital gains excise tax purposes as an individual "
            "domiciled in Washington at the time the long-term capital asset was sold or exchanged."
        ),
        "effective_year": 2022,
        "source_url": "https://app.leg.wa.gov/RCW/default.aspx?cite=82.87.020",
        "cross_references": ["RCW 82.87.040"],
    },
    {
        "id": "wa-const-7-1",
        "jurisdiction": "WA",
        "corpus": "WACONST",
        "citation": "Wash. Const. art. VII § 1",
        "section": "Art. VII Sec. 1",
        "title": "Constitutional Uniformity and Prohibition of Net Income Tax",
        "text_es": (
            "Disposición constitucional de tributación uniforme que prohíbe impuestos "
            "progresivos sobre el ingreso neto según interpretado en doctrina judicial histórica."
        ),
        "text_en": (
            "Constitutional requirement that all taxes be uniform upon the same class of property, "
            "historically interpreted to prohibit graduated net income taxes."
        ),
        "effective_year": 1889,
        "source_url": "https://leg.wa.gov/CodeReviser/Pages/WAConstitution.aspx",
        "cross_references": ["RCW 82.87.040"],
    },
    # Commonwealth of Massachusetts (Part B Income Tax & Fair Share Surtax)
    {
        "id": "ma-const-amend-44",
        "jurisdiction": "MA",
        "corpus": "MACONST",
        "citation": "Mass. Const. amend. art. XLIV",
        "section": "Art. XLIV",
        "title": "Fair Share Amendment (4% Millionaires' Surtax)",
        "text_es": (
            "Enmienda constitucional que impone una sobretasa adicional del 4% sobre la porción "
            "del ingreso imponible anual que exceda $1,000,000 (indexado por inflación)."
        ),
        "text_en": (
            "Constitutional amendment imposing an additional 4% surtax on the portion of annual "
            "taxable income exceeding $1,000,000, creating a top rate of 9.0%."
        ),
        "effective_year": 2023,
        "source_url": "https://malegislature.gov/Laws/Constitution",
        "cross_references": ["M.G.L. c. 62 § 4"],
    },
    {
        "id": "ma-gl-62-4",
        "jurisdiction": "MA",
        "corpus": "MGL",
        "citation": "M.G.L. c. 62 § 4",
        "section": "62-4",
        "title": "Personal Income Tax Rates",
        "text_es": (
            "Tasa básica de impuesto sobre el ingreso personal de Massachusetts del 5.0% sobre "
            "ingreso imponible de Parte B (salarios, negocios y pensiones)."
        ),
        "text_en": (
            "Standard 5.0% flat income tax rate on Part B taxable personal income (wages, trade, "
            "business, and interest income)."
        ),
        "effective_year": 2024,
        "source_url": "https://malegislature.gov/Laws/GeneralLaws/PartI/TitleIX/Chapter62/Section4",
        "cross_references": ["Mass. Const. amend. art. XLIV", "M.G.L. c. 62 § 1"],
    },
    {
        "id": "ma-gl-62-1f",
        "jurisdiction": "MA",
        "corpus": "MGL",
        "citation": "M.G.L. c. 62 § 1(f)",
        "section": "62-1(f)",
        "title": "Definition of Resident (Domicile and 183-Day Rule)",
        "text_es": (
            "Define residente como persona domiciliada en Massachusetts o no domiciliada que "
            "mantenga un lugar permanente de morada y pase más de 183 días en el estado."
        ),
        "text_en": (
            "Defines resident as any natural person domiciled in Massachusetts, or non-domiciliary "
            "maintaining a permanent place of abode and spending more than 183 days in the state."
        ),
        "effective_year": 2024,
        "source_url": "https://malegislature.gov/Laws/GeneralLaws/PartI/TitleIX/Chapter62/Section1",
        "cross_references": ["M.G.L. c. 62 § 4"],
    },
    # State of New Jersey (Gross Income Tax & Convenience of Employer)
    {
        "id": "nj-stat-54a-1-2",
        "jurisdiction": "NJ",
        "corpus": "NJSA",
        "citation": "N.J.S.A. 54A:1-2",
        "section": "54A:1-2",
        "title": "Definition of Resident Individual",
        "text_es": (
            "Define residente de NJ por domicilio (salvo morada permanente exterior con <= 30 "
            "días en NJ) o residente estatutario con morada permanente en NJ y más de 183 días."
        ),
        "text_en": (
            "Defines resident individual based on domicile or statutory residency (maintaining a "
            "permanent place of abode in NJ and spending more than 183 days in NJ)."
        ),
        "effective_year": 2024,
        "source_url": "https://lis.njleg.state.nj.us/",
        "cross_references": ["N.J.S.A. 54A:2-1", "N.J.A.C. 18:35-5.2"],
    },
    {
        "id": "nj-stat-54a-2-1",
        "jurisdiction": "NJ",
        "corpus": "NJSA",
        "citation": "N.J.S.A. 54A:2-1",
        "section": "54A:2-1",
        "title": "New Jersey Gross Income Tax Rates",
        "text_es": (
            "Escalas progresivas de impuesto sobre el ingreso bruto que varían entre 1.4% y una "
            "tasa marginal máxima de 10.75% para ingresos mayores a $1,000,000."
        ),
        "text_en": (
            "Graduated gross income tax brackets ranging from 1.4% up to top marginal rate of "
            "10.75% on taxable income exceeding $1,000,000."
        ),
        "effective_year": 2024,
        "source_url": "https://lis.njleg.state.nj.us/",
        "cross_references": ["N.J.S.A. 54A:1-2"],
    },
    {
        "id": "nj-admin-18-35-5-2",
        "jurisdiction": "NJ",
        "corpus": "NJAC",
        "citation": "N.J.A.C. 18:35-5.2",
        "section": "18:35-5.2",
        "title": "Convenience of the Employer Rule (Reciprocal Sourcing)",
        "text_es": (
            "Regla recíproca de conveniencia del patrono aprobada en 2023 que grava a no "
            "residentes que teletrabajen si su estado aplica regla similar (ej. Nueva York)."
        ),
        "text_en": (
            "Reciprocal convenience of employer sourcing rule enacted in 2023 sourcing "
            "telecommuting compensation of nonresidents to NJ if their resident state "
            "imposes a convenience rule."
        ),
        "effective_year": 2023,
        "source_url": "https://www.nj.gov/treasury/taxation/",
        "cross_references": ["N.J.S.A. 54A:5-8"],
    },
    # State of Illinois (Flat Income Tax & Nonresident Sourcing)
    {
        "id": "il-stat-35-ilcs-5-201",
        "jurisdiction": "IL",
        "corpus": "ILCS",
        "citation": "35 ILCS 5/201",
        "section": "5/201",
        "title": "Illinois Individual Income Tax Rate",
        "text_es": (
            "Impuesto estatal no graduado a tasa fija del 4.95% sobre el ingreso neto de "
            "personas naturales, mandatado por la Constitución de Illinois."
        ),
        "text_en": (
            "Constitutionally mandated non-graduated flat income tax rate of 4.95% on net income "
            "of individuals, trusts, and estates."
        ),
        "effective_year": 2024,
        "source_url": "https://www.ilga.gov/legislation/ilcs/ilcs3.asp?ActID=577",
        "cross_references": ["35 ILCS 5/1501(a)(20)", "35 ILCS 5/301"],
    },
    {
        "id": "il-stat-35-ilcs-5-1501",
        "jurisdiction": "IL",
        "corpus": "ILCS",
        "citation": "35 ILCS 5/1501(a)(20)",
        "section": "5/1501(a)(20)",
        "title": "Definition of Resident",
        "text_es": (
            "Define residente como persona en Illinois para fines no transitorios o temporales, "
            "o domiciliada en Illinois ausente temporalmente."
        ),
        "text_en": (
            "Defines resident as an individual who is in Illinois for other than a temporary "
            "or transitory purpose during the taxable year or who is domiciled in Illinois."
        ),
        "effective_year": 2024,
        "source_url": "https://www.ilga.gov/legislation/ilcs/ilcs3.asp?ActID=577",
        "cross_references": ["35 ILCS 5/201"],
    },
    {
        "id": "il-stat-35-ilcs-5-301",
        "jurisdiction": "IL",
        "corpus": "ILCS",
        "citation": "35 ILCS 5/301",
        "section": "5/301",
        "title": "Compensation and Nonresident Income Sourcing",
        "text_es": (
            "Reglas de asignación de ingresos y salarios de no residentes a Illinois basándose "
            "en servicios efectivamente prestados en el estado."
        ),
        "text_en": (
            "Rules for allocating and sourcing compensation and business income of nonresidents "
            "based on physical days of services performed within Illinois."
        ),
        "effective_year": 2024,
        "source_url": "https://www.ilga.gov/legislation/ilcs/ilcs3.asp?ActID=577",
        "cross_references": ["35 ILCS 5/201"],
    },
    {
        "id": "pa-stat-72-ps-7302",
        "jurisdiction": "PA",
        "corpus": "P.S.",
        "citation": "72 P.S. § 7302",
        "section": "7302",
        "title": "Imposition of Tax",
        "text_es": (
            "Impone un impuesto sobre la renta personal con tasa uniforme y fija del 3.07% sobre "
            "ocho clases especificadas de ingresos gravables."
        ),
        "text_en": (
            "Imposes Pennsylvania personal income tax at a constitutional flat rate of 3.07% on "
            "eight specified classes of taxable income."
        ),
        "effective_year": 2024,
        "source_url": "https://www.legis.state.pa.us/cfdocs/legis/LI/consCheck.cfm?txtType=HTM&ttl=72&div=0&chpt=73",
        "cross_references": ["72 P.S. § 7301", "72 P.S. § 7303"],
    },
    {
        "id": "pa-stat-72-ps-7301",
        "jurisdiction": "PA",
        "corpus": "P.S.",
        "citation": "72 P.S. § 7301",
        "section": "7301",
        "title": "Definitions - Resident Individual",
        "text_es": (
            "Define individuo residente como quien tiene domicilio en Pensilvania o mantiene un "
            "lugar permanente de morada y pasa más de 183 días en el estado."
        ),
        "text_en": (
            "Defines a resident individual as one domiciled in Pennsylvania or who maintains a "
            "permanent place of abode and spends in the aggregate more than 183 days in the "
            "commonwealth."
        ),
        "effective_year": 2024,
        "source_url": "https://www.legis.state.pa.us/cfdocs/legis/LI/consCheck.cfm?txtType=HTM&ttl=72&div=0&chpt=73",
        "cross_references": ["72 P.S. § 7302", "61 Pa. Code § 101.1"],
    },
    {
        "id": "pa-code-61-109-8",
        "jurisdiction": "PA",
        "corpus": "Pa. Code",
        "citation": "61 Pa. Code § 109.8",
        "section": "109.8",
        "title": "Convenience of the Employer Rule",
        "text_es": (
            "Dispone que la compensación por servicios remotos o de teletrabajo para un patrono "
            "de Pensilvania se considera de fuente local a menos que el teletrabajo sea por "
            "necesidad del patrono."
        ),
        "text_en": (
            "Requires compensation earned by nonresidents telecommuting for a Pennsylvania "
            "employer to be sourced to Pennsylvania unless working from home is for employer "
            "necessity."
        ),
        "effective_year": 2024,
        "source_url": "https://www.pacodeandbulletin.gov/Display/pacode?file=/secure/pacode/data/061/chapter109/s109.8.html",
        "cross_references": ["72 P.S. § 7308"],
    },
    {
        "id": "ct-stat-cgs-12-700",
        "jurisdiction": "CT",
        "corpus": "C.G.S.",
        "citation": "Conn. Gen. Stat. § 12-700",
        "section": "12-700",
        "title": "Imposition of Income Tax and Rates",
        "text_es": (
            "Impone tasas impositivas graduadas del 3.0% al 6.99% sobre el ingreso neto de "
            "residentes, con eliminación progresiva de tramos de exención en ingresos altos."
        ),
        "text_en": (
            "Imposes graduated personal income tax rates from 3.0% to 6.99% on resident "
            "Connecticut taxable income with benefit phase-out recaptures for high earners."
        ),
        "effective_year": 2024,
        "source_url": "https://www.cga.ct.gov/current/pub/chap_229.htm#sec_12-700",
        "cross_references": ["Conn. Gen. Stat. § 12-701"],
    },
    {
        "id": "ct-stat-cgs-12-701",
        "jurisdiction": "CT",
        "corpus": "C.G.S.",
        "citation": "Conn. Gen. Stat. § 12-701",
        "section": "12-701",
        "title": "Definitions - Resident and Sourcing",
        "text_es": (
            "Define residente statutario como quien mantiene un lugar permanente de morada en "
            "Connecticut y pasa en total más de 183 días en el año contributivo."
        ),
        "text_en": (
            "Defines resident as an individual domiciled in Connecticut or who maintains a "
            "permanent place of abode and spends in the aggregate more than 183 days of the "
            "taxable year in state."
        ),
        "effective_year": 2024,
        "source_url": "https://www.cga.ct.gov/current/pub/chap_229.htm#sec_12-701",
        "cross_references": ["Conn. Gen. Stat. § 12-700", "Conn. Gen. Stat. § 12-711"],
    },
    {
        "id": "ct-stat-cgs-12-711",
        "jurisdiction": "CT",
        "corpus": "C.G.S.",
        "citation": "Conn. Gen. Stat. § 12-711",
        "section": "12-711",
        "title": "Convenience of Employer Rule",
        "text_es": (
            "Aplica la regla de conveniencia del patrono para determinar la fuente de ingresos "
            "salariales de no residentes que teletrabajan si su estado aplica una regla similar."
        ),
        "text_en": (
            "Applies a reciprocal convenience of the employer test to nonresident compensation "
            "earned while telecommuting for a Connecticut employer if employee's state applies "
            "a convenience rule."
        ),
        "effective_year": 2024,
        "source_url": "https://www.cga.ct.gov/current/pub/chap_229.htm#sec_12-711",
        "cross_references": ["Conn. Gen. Stat. § 12-701"],
    },
    {
        "id": "nc-stat-ncgs-105-153-7",
        "jurisdiction": "NC",
        "corpus": "N.C.G.S.",
        "citation": "N.C. Gen. Stat. § 105-153.7",
        "section": "105-153.7",
        "title": "Individual Income Tax Imposition and Rate",
        "text_es": (
            "Impone un impuesto sobre la renta individual de tasa fija decreciente: 4.5% en 2024, "
            "4.25% en 2025, y 3.99% para años tributarios 2026 y posteriores."
        ),
        "text_en": (
            "Imposes individual income tax at a decreasing flat rate: 4.5% for 2024, 4.25% "
            "for 2025, and 3.99% for taxable years beginning on or after 2026."
        ),
        "effective_year": 2024,
        "source_url": "https://www.ncleg.gov/EnactedLegislation/Statutes/HTML/BySection/Chapter_105/Section_105-153.7.html",
        "cross_references": ["N.C. Gen. Stat. § 105-153.3", "N.C. Gen. Stat. § 105-153.5"],
    },
    {
        "id": "nc-stat-ncgs-105-153-3",
        "jurisdiction": "NC",
        "corpus": "N.C.G.S.",
        "citation": "N.C. Gen. Stat. § 105-153.3",
        "section": "105-153.3",
        "title": "Definitions - Resident Individual",
        "text_es": (
            "Define individuo residente como quien reside o tiene domicilio en Carolina del "
            "Norte, o quien pasa más de 183 días en el estado presumiendo residencia."
        ),
        "text_en": (
            "Defines resident individual as one domiciled in North Carolina or who resides in "
            "state for more than 183 days during the taxable year creating a rebuttable "
            "presumption."
        ),
        "effective_year": 2024,
        "source_url": "https://www.ncleg.gov/EnactedLegislation/Statutes/HTML/BySection/Chapter_105/Section_105-153.3.html",
        "cross_references": ["N.C. Gen. Stat. § 105-153.7"],
    },
    {
        "id": "va-stat-58-1-320",
        "jurisdiction": "VA",
        "corpus": "Va. Code",
        "citation": "Va. Code Ann. § 58.1-320",
        "section": "58.1-320",
        "title": "Rates of Tax on Individuals",
        "text_es": (
            "Impone el impuesto sobre la renta individual de Virginia con tasas graduadas: "
            "2.0% sobre los primeros $3,000; 3.0% de $3,001 a $5,000; 5.0% de $5,001 a $17,000; "
            "y 5.75% sobre el exceso de $17,000."
        ),
        "text_en": (
            "Imposes Virginia individual income tax at graduated rates: 2.0% on the first $3,000; "
            "3.0% on $3,001 to $5,000; 5.0% on $5,001 to $17,000; and 5.75% on taxable income "
            "exceeding $17,000."
        ),
        "effective_year": 2024,
        "source_url": "https://law.lis.virginia.gov/vacode/title58.1/chapter3/section58.1-320/",
        "cross_references": ["Va. Code Ann. § 58.1-302", "Va. Code Ann. § 58.1-322.03"],
    },
    {
        "id": "va-stat-58-1-302",
        "jurisdiction": "VA",
        "corpus": "Va. Code",
        "citation": "Va. Code Ann. § 58.1-302",
        "section": "58.1-302",
        "title": "Definitions - Resident and Actual Resident",
        "text_es": (
            "Define individuo residente como quien tiene domicilio en Virginia, o un residente "
            "de hecho que mantiene morada y pasa más de 183 días en total del año contributivo "
            "en Virginia."
        ),
        "text_en": (
            "Defines resident individual as one domiciled in Virginia, or an actual resident "
            "who maintains a place of abode and spends in the aggregate more than 183 days of "
            "the taxable year in Virginia."
        ),
        "effective_year": 2024,
        "source_url": "https://law.lis.virginia.gov/vacode/title58.1/chapter3/section58.1-302/",
        "cross_references": ["Va. Code Ann. § 58.1-320", "Va. Code Ann. § 58.1-342"],
    },
    {
        "id": "va-stat-58-1-342",
        "jurisdiction": "VA",
        "corpus": "Va. Code",
        "citation": "Va. Code Ann. § 58.1-342",
        "section": "58.1-342",
        "title": "Reciprocal Tax Agreements for Commuters",
        "text_es": (
            "Autoriza acuerdos recíprocos de exención con jurisdicciones limítrofes (DC, Maryland, "
            "West Virginia, Pensilvania y Kentucky) para salarios devengados por viajeros diarios."
        ),
        "text_en": (
            "Authorizes reciprocal agreements with contiguous jurisdictions (DC, Maryland, "
            "West Virginia, Pennsylvania, and Kentucky) exempting commuter compensation from "
            "non-resident withholding."
        ),
        "effective_year": 2024,
        "source_url": "https://law.lis.virginia.gov/vacode/title58.1/chapter3/section58.1-342/",
        "cross_references": ["Va. Code Ann. § 58.1-302"],
    },
    {
        "id": "md-stat-tg-10-105",
        "jurisdiction": "MD",
        "corpus": "Md. Code",
        "citation": "Md. Code Ann., Tax-Gen. § 10-105",
        "section": "10-105",
        "title": "Individual State Income Tax Rates",
        "text_es": (
            "Impone tasas impositivas estatales graduadas desde 2.0% hasta 5.75% sobre el "
            "ingreso gravable que exceda de $250,000 (solteros) o $300,000 (casados rindiendo "
            "conjuntamente)."
        ),
        "text_en": (
            "Imposes graduated Maryland state income tax rates from 2.0% up to 5.75% for "
            "taxable income exceeding $250,000 for single filers or $300,000 for married couples "
            "filing jointly."
        ),
        "effective_year": 2024,
        "source_url": "https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=gtg&section=10-105",
        "cross_references": [
            "Md. Code Ann., Tax-Gen. § 10-101",
            "Md. Code Ann., Tax-Gen. § 10-106",
        ],
    },
    {
        "id": "md-stat-tg-10-101",
        "jurisdiction": "MD",
        "corpus": "Md. Code",
        "citation": "Md. Code Ann., Tax-Gen. § 10-101",
        "section": "10-101",
        "title": "Definitions - Resident Individual",
        "text_es": (
            "Define individuo residente como quien tiene domicilio en Maryland el último día del "
            "año contributivo, o mantiene morada por más de 6 meses y está presente físicamente "
            "183 días o más."
        ),
        "text_en": (
            "Defines a resident individual as one who is domiciled in Maryland on the last day "
            "of the taxable year, or who maintains a place of abode in Maryland for more than "
            "six months and is physically present for 183 days or more."
        ),
        "effective_year": 2024,
        "source_url": "https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=gtg&section=10-101",
        "cross_references": ["Md. Code Ann., Tax-Gen. § 10-105"],
    },
    {
        "id": "md-stat-tg-10-106",
        "jurisdiction": "MD",
        "corpus": "Md. Code",
        "citation": "Md. Code Ann., Tax-Gen. § 10-106",
        "section": "10-106",
        "title": "County and Local Piggyback Income Tax",
        "text_es": (
            "Requiere que cada condado de Maryland y la ciudad de Baltimore cobren un impuesto "
            "local sobre la renta ('piggyback') de entre 2.25% y 3.20%, recaudado en la "
            "planilla estatal."
        ),
        "text_en": (
            "Mandates that each Maryland county and Baltimore City levy a local piggyback "
            "income tax rate between 2.25% and 3.20%, collected by the Comptroller on the "
            "state tax return."
        ),
        "effective_year": 2024,
        "source_url": "https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=gtg&section=10-106",
        "cross_references": ["Md. Code Ann., Tax-Gen. § 10-105"],
    },
    {
        "id": "co-stat-39-22-104",
        "jurisdiction": "CO",
        "corpus": "C.R.S.",
        "citation": "Colo. Rev. Stat. § 39-22-104",
        "section": "39-22-104",
        "title": "Levy of Tax - Single Rate for Individuals",
        "text_es": (
            "Impone una tasa uniforme del 4.40% sobre el ingreso federal tributable modificado "
            "de individuos de Colorado bajo la Proposición 121 aprobada por los electores."
        ),
        "text_en": (
            "Imposes a flat tax of 4.40% on Colorado modified federal taxable income of "
            "individuals pursuant to voter-approved Proposition 121 (reduced from 4.55%)."
        ),
        "effective_year": 2024,
        "source_url": "https://leg.colorado.gov/agencies/office-legislative-legal-services/colorado-revised-statutes",
        "cross_references": ["Colo. Rev. Stat. § 39-22-103"],
    },
    {
        "id": "co-stat-39-22-103",
        "jurisdiction": "CO",
        "corpus": "C.R.S.",
        "citation": "Colo. Rev. Stat. § 39-22-103",
        "section": "39-22-103",
        "title": "Definitions - Resident Individual",
        "text_es": (
            "Define individuo residente como quien tiene domicilio en Colorado, o mantiene "
            "morada permanente y pasa en agregado más de seis meses del año contributivo "
            "en Colorado."
        ),
        "text_en": (
            "Defines a resident individual as one domiciled in Colorado, or who maintains a "
            "permanent place of abode in Colorado and spends in the aggregate more than six "
            "months of the taxable year in Colorado."
        ),
        "effective_year": 2024,
        "source_url": "https://leg.colorado.gov/agencies/office-legislative-legal-services/colorado-revised-statutes",
        "cross_references": ["Colo. Rev. Stat. § 39-22-104"],
    },
    {
        "id": "az-stat-43-1011",
        "jurisdiction": "AZ",
        "corpus": "A.R.S.",
        "citation": "A.R.S. § 43-1011",
        "section": "43-1011",
        "title": "Taxes and Tax Rates - Individuals",
        "text_es": (
            "Impone una tasa impositiva fija del 2.50% sobre el ingreso gravable de Arizona de "
            "individuos, promulgada bajo SB 1828 eliminando tramos graduales anteriores."
        ),
        "text_en": (
            "Imposes a flat individual income tax rate of 2.50% on Arizona taxable income, "
            "enacted by SB 1828 to replace graduated individual tax brackets."
        ),
        "effective_year": 2024,
        "source_url": "https://www.azleg.gov/ars/43/01011.htm",
        "cross_references": ["A.R.S. § 43-104"],
    },
    {
        "id": "az-stat-43-104",
        "jurisdiction": "AZ",
        "corpus": "A.R.S.",
        "citation": "A.R.S. § 43-104",
        "section": "43-104",
        "title": "Definitions - Resident and Presumption of Residency",
        "text_es": (
            "Define residente como todo individuo en Arizona para fines no temporales ni "
            "transitorios; quien pase en agregado más de nueve meses (270 días) en el estado "
            "se presume residente."
        ),
        "text_en": (
            "Defines resident as every individual in Arizona for other than a temporary or "
            "transitory purpose; individuals spending in the aggregate more than nine months "
            "(270 days) of the tax year in state are presumed residents."
        ),
        "effective_year": 2024,
        "source_url": "https://www.azleg.gov/ars/43/00104.htm",
        "cross_references": ["A.R.S. § 43-1011"],
    },
    {
        "id": "oh-stat-5747-02",
        "jurisdiction": "OH",
        "corpus": "R.C.",
        "citation": "Ohio Rev. Code § 5747.02",
        "section": "5747.02",
        "title": "Taxation of Personal Income - Rates",
        "text_es": (
            "Grava el ingreso personal en Ohio con consolidación de tasas: 0% hasta $26,050; "
            "2.75% de $26,051 a $100,000; y 3.50% sobre el ingreso no comercial que exceda "
            "$100,000 para el año 2024+."
        ),
        "text_en": (
            "Levies Ohio personal income tax with phased rate consolidation: 0% up to $26,050; "
            "2.75% from $26,051 to $100,000; and 3.50% on non-business taxable income exceeding "
            "$100,000 for tax year 2024+."
        ),
        "effective_year": 2024,
        "source_url": "https://codes.ohio.gov/ohio-revised-code/section-5747.02",
        "cross_references": ["Ohio Rev. Code § 5747.24"],
    },
    {
        "id": "oh-stat-5747-24",
        "jurisdiction": "OH",
        "corpus": "R.C.",
        "citation": "Ohio Rev. Code § 5747.24",
        "section": "5747.24",
        "title": "Bright-Line Presumption of Domicile - Contact Periods",
        "text_es": (
            "Establece regla objetiva de periodos de contacto: quien tenga <= 212 periodos de "
            "contacto se presume no residente si radica declaración jurada IT-NRS antes del "
            "15 de octubre; >= 213 periodos presume residencia."
        ),
        "text_en": (
            "Establishes bright-line residency rules: an individual with <= 212 Ohio contact "
            "periods is presumed non-resident if filing Form IT-NRS affidavit by October 15; "
            "an individual with >= 213 contact periods is presumed an Ohio resident."
        ),
        "effective_year": 2024,
        "source_url": "https://codes.ohio.gov/ohio-revised-code/section-5747.24",
        "cross_references": ["Ohio Rev. Code § 5747.02"],
    },
]

INITIAL_PUBLICATIONS: list[dict[str, Any]] = [
    {
        "id": "pr-cc-ri-21-10",
        "category": "carta-circular",
        "doc_number": "CC RI 21-10",
        "doc_date": "2021-03-31",
        "title": "Tratamiento contributivo del teletrabajo y servicios prestados por no residentes",
        "summary": (
            "Directrices oficiales del Departamento de Hacienda sobre la fuente de ingresos "
            "por servicios laborales remotos, retención y establecimiento permanente en PR."
        ),
        "source_url": "https://hacienda.pr.gov/publicaciones/carta-circular-de-rentas-internas-num-21-10",
    },
    {
        "id": "pr-cc-ri-26-13",
        "category": "carta-circular",
        "doc_number": "CC RI 26-13",
        "doc_date": "2026-06-30",
        "title": "Radicación electrónica obligatoria de planillas de individuos",
        "summary": (
            "Instrucciones y requisitos técnicos para la radicación compulsoria a través de SURI "
            "de las planillas de contribución sobre ingresos de individuos."
        ),
        "source_url": "https://hacienda.pr.gov/publicaciones/carta-circular-de-rentas-internas-num-26-13",
    },
    {
        "id": "pr-da-23-04",
        "category": "determinacion-administrativa",
        "doc_number": "DA 23-04",
        "doc_date": "2023-08-15",
        "title": "Tratamiento contributivo de transferencias de propiedad bajo Ley 60",
        "summary": (
            "Determinación administrativa sobre exenciones contributivas en la enajenación de "
            "activos de capital por parte de inversionistas residentes con decreto bajo Ley 60."
        ),
        "source_url": "https://hacienda.pr.gov/publicaciones/determinacion-administrativa-num-23-04",
    },
    {
        "id": "pr-da-21-04",
        "category": "determinacion-administrativa",
        "doc_number": "DA 21-04",
        "doc_date": "2021-04-12",
        "title": "Procedimiento de cumplimiento anual para decretos de individuos",
        "summary": (
            "Directrices para la presentación de informes anuales de cumplimiento y comprobantes "
            "de donaciones benéficas requeridas por el Código de Incentivos de Puerto Rico."
        ),
        "source_url": "https://hacienda.pr.gov/publicaciones/determinacion-administrativa-num-21-04",
    },
    {
        "id": "pa-dor-it-bulletin-2021-01",
        "category": "policy-bulletin",
        "doc_number": "PA REV-BULL 2021-01",
        "doc_date": "2021-06-15",
        "title": "Pennsylvania Personal Income Tax Telework and Sourcing Guidance",
        "summary": (
            "Guidance from the Pennsylvania Department of Revenue concerning the convenience of "
            "the employer doctrine and withholding obligations for telecommuting employees."
        ),
        "source_url": "https://www.revenue.pa.gov/FormsandPublications/FormsPublications/Pages/default.aspx",
    },
    {
        "id": "ga-dor-pb-it-2024-01",
        "category": "policy-bulletin",
        "doc_number": "DOR PB IT-2024-01",
        "doc_date": "2024-04-20",
        "title": "Georgia Flat Tax Rate and Standard Deduction Adjustments under HB 1023",
        "summary": (
            "Guidance from the Georgia Department of Revenue explaining the reduction in the "
            "individual income tax rate to 5.49% for tax year 2024 and elimination of "
            "graduated brackets."
        ),
        "source_url": "https://dor.georgia.gov/document/document/policy-bulletin-it-2024-01/download",
    },
    {
        "id": "ga-dor-pb-it-2022-02",
        "category": "policy-bulletin",
        "doc_number": "DOR PB IT-2022-02",
        "doc_date": "2022-05-18",
        "title": "Georgia Pass-Through Entity Tax (PTET) Guidance under HB 149",
        "summary": (
            "Technical guidelines for eligible S corporations and partnerships electing to pay "
            "tax at the entity level to mitigate the federal limitation on individual state and "
            "local tax (SALT) deductions."
        ),
        "source_url": "https://dor.georgia.gov/document/document/policy-bulletin-it-2022-02/download",
    },
    {
        "id": "va-tax-bulletin-05-7",
        "category": "policy-bulletin",
        "doc_number": "VA TB 05-7",
        "doc_date": "2005-08-25",
        "title": "Virginia Guidelines for Determining Residency",
        "summary": (
            "Official guidance from the Virginia Department of Taxation regarding domiciliary and "
            "actual residency tests, temporary absences, and reciprocal commuter provisions."
        ),
        "source_url": "https://www.tax.virginia.gov/laws-rules-decisions/tax-bulletins/05-7",
    },
    {
        "id": "md-admin-release-34",
        "category": "policy-bulletin",
        "doc_number": "MD AR-34",
        "doc_date": "2023-01-10",
        "title": "Maryland Domicile and Statutory Residency Physical Presence Guidelines",
        "summary": (
            "Administrative guidance issued by the Comptroller of Maryland detailing the "
            "183-day rule, permanent place of abode factors, and county local income tax "
            "calculation."
        ),
        "source_url": "https://www.marylandtaxes.gov/forms/tax_publications/administrative_releases.php",
    },
    {
        "id": "oh-tax-info-it-2007-08",
        "category": "policy-bulletin",
        "doc_number": "OH IT 2007-08",
        "doc_date": "2007-09-01",
        "title": "Ohio Residency Guidelines and Bright-Line Contact Period Test",
        "summary": (
            "Comprehensive information release from the Ohio Department of Taxation explaining "
            "contact periods, the bright-line test, and instructions for filing Form IT-NRS "
            "non-domicile affidavit."
        ),
        "source_url": "https://tax.ohio.gov/individual/resources/information-releases",
    },
]


class TaxDatabase:
    """Thread-safe DuckDB manager for tax statutes and administrative guidance."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._mem_conn: duckdb.DuckDBPyConnection | None = None
        if self.db_path == ":memory:":
            self._mem_conn = duckdb.connect(":memory:")
        self._init_db()

    def _get_write_conn(self) -> duckdb.DuckDBPyConnection:
        if self._mem_conn is not None:
            return self._mem_conn
        return duckdb.connect(self.db_path)

    def _get_read_conn(self) -> duckdb.DuckDBPyConnection:
        if self._mem_conn is not None:
            return self._mem_conn
        return duckdb.connect(self.db_path, read_only=True)

    def _init_db(self) -> None:
        """Create tables and seed initial data if required."""
        with self._lock:
            conn = self._get_write_conn()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS statutes (
                        id VARCHAR PRIMARY KEY,
                        jurisdiction VARCHAR,
                        corpus VARCHAR,
                        citation VARCHAR,
                        section VARCHAR,
                        title VARCHAR,
                        text_es VARCHAR,
                        text_en VARCHAR,
                        effective_year INTEGER,
                        superseded_year INTEGER,
                        source_url VARCHAR,
                        cross_references VARCHAR[]
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS publications (
                        id VARCHAR PRIMARY KEY,
                        category VARCHAR,
                        doc_number VARCHAR UNIQUE,
                        doc_date VARCHAR,
                        title VARCHAR,
                        summary VARCHAR,
                        source_url VARCHAR
                    );
                    """
                )

                # Seed initial statutes if table is empty
                statute_count = conn.execute("SELECT COUNT(*) FROM statutes;").fetchone()
                if statute_count and statute_count[0] == 0:
                    for s in INITIAL_STATUTES:
                        conn.execute(
                            """
                            INSERT INTO statutes (
                                id, jurisdiction, corpus, citation, section,
                                title, text_es, text_en, effective_year,
                                superseded_year, source_url, cross_references
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                            """,
                            [
                                s["id"],
                                s["jurisdiction"],
                                s["corpus"],
                                s["citation"],
                                s["section"],
                                s["title"],
                                s["text_es"],
                                s["text_en"],
                                s["effective_year"],
                                s.get("superseded_year"),
                                s["source_url"],
                                s["cross_references"],
                            ],
                        )

                # Seed initial publications if table is empty
                pub_count = conn.execute("SELECT COUNT(*) FROM publications;").fetchone()
                if pub_count and pub_count[0] == 0:
                    for p in INITIAL_PUBLICATIONS:
                        conn.execute(
                            """
                            INSERT INTO publications (
                                id, category, doc_number, doc_date, title, summary, source_url
                            ) VALUES (?, ?, ?, ?, ?, ?, ?);
                            """,
                            [
                                p["id"],
                                p["category"],
                                p["doc_number"],
                                p["doc_date"],
                                p["title"],
                                p["summary"],
                                p["source_url"],
                            ],
                        )
            finally:
                if self._mem_conn is None:
                    conn.close()

    def get_connection(self) -> duckdb.DuckDBPyConnection:
        """Open a DuckDB connection or in-memory cursor.

        Uses ``read_only=True`` for non-memory files to allow multiple concurrent readers.
        """
        if self._mem_conn is not None:
            return self._mem_conn.cursor()
        return duckdb.connect(self.db_path, read_only=True)

    def get_stats(self) -> tuple[int, int]:
        """Return the count of indexed (statutes, publications)."""
        with self._lock:
            conn = self._get_read_conn()
            try:
                s_count = conn.execute("SELECT COUNT(*) FROM statutes;").fetchone()
                p_count = conn.execute("SELECT COUNT(*) FROM publications;").fetchone()
                return (
                    s_count[0] if s_count else 0,
                    p_count[0] if p_count else 0,
                )
            finally:
                if self._mem_conn is None:
                    conn.close()

    def lookup_statute(
        self,
        citation_or_section: str,
        jurisdiction: str | None = None,
        tax_year: int | None = None,
        lang: str = "en",
    ) -> dict[str, Any] | None:
        """Find a statute by exact citation or section code and optional tax year."""
        query_val = citation_or_section.strip()
        with self._lock:
            conn = self._get_read_conn()
            try:
                sql = """
                    SELECT id, jurisdiction, corpus, citation, section, title,
                           text_es, text_en, effective_year, superseded_year,
                           source_url, cross_references
                    FROM statutes
                    WHERE (
                        LOWER(citation) = LOWER(?)
                        OR LOWER(section) = LOWER(?)
                        OR LOWER(REPLACE(citation, ' ', '')) = LOWER(REPLACE(?, ' ', ''))
                    )
                """
                params: list[Any] = [query_val, query_val, query_val]
                if jurisdiction:
                    sql += " AND UPPER(jurisdiction) = UPPER(?)"
                    params.append(jurisdiction.strip())

                if tax_year is not None:
                    sql += (
                        " AND effective_year <= ? "
                        "AND (superseded_year IS NULL OR ? < superseded_year)"
                    )
                    params.extend([tax_year, tax_year])

                sql += " ORDER BY effective_year DESC LIMIT 1;"
                result = conn.execute(sql, params).fetchone()
                if not result:
                    return None

                text = result[7] if lang.lower() == "en" else result[6]
                matched_citation = result[3]

                history_rows = conn.execute(
                    """
                    SELECT effective_year, superseded_year, id
                    FROM statutes
                    WHERE LOWER(citation) = LOWER(?)
                    ORDER BY effective_year DESC;
                    """,
                    [matched_citation],
                ).fetchall()
                available_versions = [
                    {
                        "effective_year": h[0],
                        "superseded_year": h[1],
                        "is_current": h[1] is None,
                        "id": h[2],
                    }
                    for h in history_rows
                ]

                return {
                    "id": result[0],
                    "jurisdiction": result[1],
                    "corpus": result[2],
                    "citation": result[3],
                    "section": result[4],
                    "title": result[5],
                    "operative_text": text,
                    "text_es": result[6],
                    "text_en": result[7],
                    "effective_year": result[8],
                    "superseded_year": result[9],
                    "is_current": result[9] is None,
                    "available_versions": available_versions,
                    "source_url": result[10],
                    "cross_references": result[11] or [],
                    "language": lang.lower(),
                    "official_language": "es" if result[1] == "PR" else "en",
                    "content_type": "reference_summary",
                    "unofficial_summary": True,
                    "translation_disclaimer": STATUTE_TRANSLATION_DISCLAIMER,
                }
            finally:
                if self._mem_conn is None:
                    conn.close()

    def search_statutes(
        self,
        query: str,
        jurisdiction: str | None = None,
        corpus: str | None = None,
        tax_year: int | None = None,
        lang: str = "en",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Search statutes across title, citation, and multilingual operative text."""
        q = f"%{query.strip().lower()}%"
        with self._lock:
            conn = self._get_read_conn()
            try:
                sql = """
                    SELECT id, jurisdiction, corpus, citation, section, title,
                           text_es, text_en, effective_year, superseded_year,
                           source_url, cross_references
                    FROM statutes
                    WHERE (
                        LOWER(title) LIKE ?
                        OR LOWER(citation) LIKE ?
                        OR LOWER(text_en) LIKE ?
                        OR LOWER(text_es) LIKE ?
                    )
                """
                params: list[Any] = [q, q, q, q]
                if jurisdiction:
                    sql += " AND UPPER(jurisdiction) = UPPER(?)"
                    params.append(jurisdiction.strip())
                if corpus:
                    sql += " AND UPPER(corpus) = UPPER(?)"
                    params.append(corpus.strip())
                if tax_year is not None:
                    sql += (
                        " AND effective_year <= ? "
                        "AND (superseded_year IS NULL OR ? < superseded_year)"
                    )
                    params.extend([tax_year, tax_year])

                sql += " ORDER BY effective_year DESC, id LIMIT ?;"
                params.append(limit)

                rows = conn.execute(sql, params).fetchall()
                output: list[dict[str, Any]] = []
                for r in rows:
                    text = r[7] if lang.lower() == "en" else r[6]
                    output.append(
                        {
                            "id": r[0],
                            "jurisdiction": r[1],
                            "corpus": r[2],
                            "citation": r[3],
                            "section": r[4],
                            "title": r[5],
                            "operative_text": text,
                            "effective_year": r[8],
                            "superseded_year": r[9],
                            "is_current": r[9] is None,
                            "source_url": r[10],
                            "cross_references": r[11] or [],
                            "content_type": "reference_summary",
                            "unofficial_summary": True,
                            "translation_disclaimer": STATUTE_TRANSLATION_DISCLAIMER,
                        }
                    )
                return output
            finally:
                if self._mem_conn is None:
                    conn.close()

    def list_publications(
        self,
        category: str | None = None,
        since_date: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """List administrative circulars and determinations."""
        with self._lock:
            conn = self._get_read_conn()
            try:
                sql = """
                    SELECT id, category, doc_number, doc_date, title, summary, source_url
                    FROM publications
                    WHERE 1=1
                """
                params: list[Any] = []
                if category:
                    sql += " AND LOWER(category) = LOWER(?)"
                    params.append(category.strip())
                if since_date:
                    sql += " AND doc_date >= ?"
                    params.append(since_date.strip())

                sql += " ORDER BY doc_date DESC LIMIT ?;"
                params.append(limit)

                rows = conn.execute(sql, params).fetchall()
                return [
                    {
                        "id": r[0],
                        "category": r[1],
                        "doc_number": r[2],
                        "doc_date": r[3],
                        "title": r[4],
                        "summary": r[5],
                        "source_url": r[6],
                    }
                    for r in rows
                ]
            finally:
                if self._mem_conn is None:
                    conn.close()

    def insert_statute(self, statute: dict[str, Any]) -> None:
        """Insert a statute into the database."""
        with self._lock:
            conn = self._get_write_conn()
            try:
                conn.execute(
                    """
                    INSERT INTO statutes (
                        id, jurisdiction, corpus, citation, section,
                        title, text_es, text_en, effective_year,
                        superseded_year, source_url, cross_references
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    [
                        statute["id"],
                        statute["jurisdiction"],
                        statute["corpus"],
                        statute["citation"],
                        statute["section"],
                        statute["title"],
                        statute.get("text_es", ""),
                        statute.get("text_en", ""),
                        statute.get("effective_year", 2026),
                        statute.get("superseded_year"),
                        statute.get("source_url", ""),
                        statute.get("cross_references", []),
                    ],
                )
            finally:
                if self._mem_conn is None:
                    conn.close()

    def upsert_statute(self, statute: dict[str, Any]) -> None:
        """Insert or update a statute in the database."""
        with self._lock:
            conn = self._get_write_conn()
            try:
                conn.execute(
                    """
                    INSERT INTO statutes (
                        id, jurisdiction, corpus, citation, section,
                        title, text_es, text_en, effective_year,
                        superseded_year, source_url, cross_references
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO UPDATE SET
                        jurisdiction = EXCLUDED.jurisdiction,
                        corpus = EXCLUDED.corpus,
                        citation = EXCLUDED.citation,
                        section = EXCLUDED.section,
                        title = EXCLUDED.title,
                        text_es = EXCLUDED.text_es,
                        text_en = EXCLUDED.text_en,
                        effective_year = EXCLUDED.effective_year,
                        superseded_year = EXCLUDED.superseded_year,
                        source_url = EXCLUDED.source_url,
                        cross_references = EXCLUDED.cross_references;
                    """,
                    [
                        statute["id"],
                        statute["jurisdiction"],
                        statute["corpus"],
                        statute["citation"],
                        statute["section"],
                        statute["title"],
                        statute.get("text_es", ""),
                        statute.get("text_en", ""),
                        statute.get("effective_year", 2026),
                        statute.get("superseded_year"),
                        statute.get("source_url", ""),
                        statute.get("cross_references", []),
                    ],
                )
            finally:
                if self._mem_conn is None:
                    conn.close()

    def upsert_publication(self, pub: dict[str, Any]) -> None:
        """Insert or update an administrative publication in the database."""
        with self._lock:
            conn = self._get_write_conn()
            try:
                conn.execute(
                    """
                    INSERT INTO publications (
                        id, category, doc_number, doc_date, title, summary, source_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO UPDATE SET
                        category = EXCLUDED.category,
                        doc_number = EXCLUDED.doc_number,
                        doc_date = EXCLUDED.doc_date,
                        title = EXCLUDED.title,
                        summary = EXCLUDED.summary,
                        source_url = EXCLUDED.source_url;
                    """,
                    [
                        pub["id"],
                        pub["category"],
                        pub["doc_number"],
                        pub["doc_date"],
                        pub["title"],
                        pub.get("summary", ""),
                        pub.get("source_url", ""),
                    ],
                )
            finally:
                if self._mem_conn is None:
                    conn.close()

    def delete_statute(self, statute_id: str) -> bool:
        """Delete a statute by ID."""
        with self._lock:
            conn = self._get_write_conn()
            try:
                res = conn.execute(
                    "DELETE FROM statutes WHERE id = ? RETURNING id;", [statute_id]
                ).fetchone()
                return res is not None
            finally:
                if self._mem_conn is None:
                    conn.close()

    def delete_publication(self, pub_id: str) -> bool:
        """Delete a publication by ID."""
        with self._lock:
            conn = self._get_write_conn()
            try:
                res = conn.execute(
                    "DELETE FROM publications WHERE id = ? RETURNING id;", [pub_id]
                ).fetchone()
                return res is not None
            finally:
                if self._mem_conn is None:
                    conn.close()


# Global default database instance configured via settings.DB_PATH
db = TaxDatabase(settings.DB_PATH)
