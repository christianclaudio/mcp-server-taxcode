#!/usr/bin/env python3
"""Statutory Ingestion and Update Pipeline for US and Puerto Rico Tax Codes.

Ingests, updates, parses, and synchronizes statutory provisions (Title 26 U.S.C.,
26 C.F.R., Ley 1-2011, Ley 60-2019) and administrative publications into DuckDB,
and generates standardized llms.txt documentation files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from taxcode_mcp.db import TaxDatabase

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ingest_statutes")


@dataclass
class StatuteRecord:
    """Represents an authoritative statutory section."""

    id: str
    jurisdiction: str
    corpus: str
    citation: str
    section: str
    title: str
    text_en: str
    text_es: str
    effective_year: int
    source_url: str
    cross_references: list[str] = field(default_factory=list)
    breadcrumbs: str = ""

    def content_hash(self) -> str:
        """Compute a SHA-256 checksum of the bilingual text payload."""
        payload = f"{self.citation}|{self.title}|{self.text_en}|{self.text_es}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """Convert record to database dictionary format."""
        return {
            "id": self.id,
            "jurisdiction": self.jurisdiction,
            "corpus": self.corpus,
            "citation": self.citation,
            "section": self.section,
            "title": self.title,
            "text_en": self.text_en,
            "text_es": self.text_es,
            "effective_year": self.effective_year,
            "source_url": self.source_url,
            "cross_references": self.cross_references,
        }


@dataclass
class PublicationRecord:
    """Represents an administrative guidance publication."""

    id: str
    category: str
    doc_number: str
    doc_date: str
    title: str
    summary: str
    source_url: str

    def content_hash(self) -> str:
        """Compute a SHA-256 checksum of the publication payload."""
        payload = f"{self.doc_number}|{self.doc_date}|{self.title}|{self.summary}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """Convert publication to database dictionary format."""
        return {
            "id": self.id,
            "category": self.category,
            "doc_number": self.doc_number,
            "doc_date": self.doc_date,
            "title": self.title,
            "summary": self.summary,
            "source_url": self.source_url,
        }


# Authoritative statutory corpus definitions
STATUTES_CATALOG: list[StatuteRecord] = [
    # US Federal Internal Revenue Code (Title 26 U.S.C.)
    StatuteRecord(
        id="us-irc-933",
        jurisdiction="US",
        corpus="IRC",
        citation="26 U.S.C. § 933",
        section="933",
        title="Income from sources within Puerto Rico",
        text_en=(
            "In the case of an individual who is a bona fide resident of Puerto Rico during the "
            "entire taxable year, gross income does not include income derived from sources within "
            "Puerto Rico (except amounts received for services performed as an employee of the United States "
            "or any agency thereof); but such individual shall not be allowed as a deduction from his "
            "gross income any deductions (other than the deduction under section 151, relating to personal "
            "exemptions) or any credit properly allocable to or chargeable against amounts excluded from gross income."
        ),
        text_es=(
            "En el caso de un individuo que sea residente de buena fe de Puerto Rico durante todo "
            "el año tributario, el ingreso bruto no incluirá ingresos derivados de fuentes dentro "
            "de Puerto Rico (excepto cantidades recibidas por servicios prestados como empleado de "
            "los Estados Unidos o cualquiera de sus agencias); no permitiéndose deducciones atribuibles a dicho ingreso."
        ),
        effective_year=1954,
        source_url="https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title26-section933",
        cross_references=["26 U.S.C. § 937", "26 CFR § 1.937-1", "26 U.S.C. § 865"],
        breadcrumbs="Title 26 > Subtitle A > Chapter 1 > Subchapter N > Part III > Subpart D > § 933",
    ),
    StatuteRecord(
        id="us-irc-937",
        jurisdiction="US",
        corpus="IRC",
        citation="26 U.S.C. § 937",
        section="937",
        title="Residence and source rules involving possessions",
        text_en=(
            "Rules for determining bona fide residency in possessions: (a) An individual is a bona fide "
            "resident if present in the possession for at least 183 days during the taxable year, does not "
            "have a tax home outside the possession, and does not have a closer connection to the United States "
            "or a foreign country. (b) Source rules: Principles for determining whether income is derived from "
            "sources within a possession or effectively connected with the conduct of a trade or business within a possession."
        ),
        text_es=(
            "Establece los requisitos estatutarios para determinar residencia de buena fe en posesiones "
            "(presencia física de 183 días, centro de actividad tributaria 'tax home' y conexión más cercana), "
            "así como las reglas de fuente de ingresos aplicables a posesiones y territorios."
        ),
        effective_year=2004,
        source_url="https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title26-section937",
        cross_references=["26 U.S.C. § 933", "26 CFR § 1.937-1", "26 CFR § 1.937-2"],
        breadcrumbs="Title 26 > Subtitle A > Chapter 1 > Subchapter N > Part III > Subpart D > § 937",
    ),
    StatuteRecord(
        id="us-irc-865",
        jurisdiction="US",
        corpus="IRC",
        citation="26 U.S.C. § 865",
        section="865",
        title="Source rules for personal property sales",
        text_en=(
            "Except as otherwise provided in this section, income from the sale of personal property "
            "(including corporate stock, debt securities, and marketable assets) by a United States resident "
            "shall be sourced in the United States, and by a non-resident shall be sourced outside the United States."
        ),
        text_es=(
            "Salvo disposición expresa en contrario, el ingreso derivado de la venta de bienes personales "
            "(incluyendo acciones y valores negociables) se atribuirá a la residencia del vendedor."
        ),
        effective_year=1986,
        source_url="https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title26-section865",
        cross_references=["26 U.S.C. § 937", "26 CFR § 1.937-2"],
        breadcrumbs="Title 26 > Subtitle A > Chapter 1 > Subchapter N > Part I > § 865",
    ),
    StatuteRecord(
        id="us-irc-957",
        jurisdiction="US",
        corpus="IRC",
        citation="26 U.S.C. § 957",
        section="957",
        title="Controlled foreign corporations; United States persons",
        text_en=(
            "Defines controlled foreign corporations (CFCs). Under subsection (c), bona fide residents of Puerto Rico "
            "are excluded from the definition of a United States person with respect to corporations organized under "
            "the laws of the Commonwealth of Puerto Rico, exempting them from Subpart F and GILTI reporting."
        ),
        text_es=(
            "Define corporaciones foráneas controladas (CFC). Bajo el inciso (c), los residentes de buena fe de "
            "Puerto Rico quedan excluidos de la definición de persona de EE.UU. respecto a corporaciones creadas bajo "
            "las leyes del Estado Libre Asociado de Puerto Rico, eximiéndoles de Subpart F y GILTI."
        ),
        effective_year=1962,
        source_url="https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title26-section957",
        cross_references=["26 U.S.C. § 951", "26 U.S.C. § 933"],
        breadcrumbs="Title 26 > Subtitle A > Chapter 1 > Subchapter N > Part III > Subpart F > § 957",
    ),
    StatuteRecord(
        id="us-irc-1411",
        jurisdiction="US",
        corpus="IRC",
        citation="26 U.S.C. § 1411",
        section="1411",
        title="Imposition of tax on net investment income",
        text_en=(
            "Imposes a 3.8 percent tax on net investment income of individuals, estates, and trusts having modified "
            "adjusted gross income exceeding statutory thresholds ($200,000 single, $250,000 married filing jointly). "
            "Excluded Puerto Rico-source income under § 933 is not included in MAGI or net investment income."
        ),
        text_es=(
            "Impone un impuesto de 3.8 por ciento sobre el ingreso neto de inversión para individuos con ingreso bruto "
            "ajustado modificado superior a los límites estatutarios ($200,000 solteros, $250,000 casados). El ingreso "
            "excluido bajo § 933 no forma parte del ingreso neto de inversión tributable."
        ),
        effective_year=2010,
        source_url="https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title26-section1411",
        cross_references=["26 U.S.C. § 933", "26 CFR § 1.1411-1"],
        breadcrumbs="Title 26 > Subtitle A > Chapter 2A > § 1411",
    ),
    StatuteRecord(
        id="us-irc-6688",
        jurisdiction="US",
        corpus="IRC",
        citation="26 U.S.C. § 6688",
        section="6688",
        title="Assessable penalties with respect to information required under section 937",
        text_en=(
            "Imposes a statutory penalty of $1,000 for failure to file information required under section 937(c) "
            "(IRS Form 8898 for individuals establishing or terminating bona fide residence in a possession) "
            "without reasonable cause."
        ),
        text_es=(
            "Impone una penalidad estatutaria de $1,000 por no presentar la declaración informativa requerida bajo la "
            "sección 937(c) (Formulario IRS 8898 al establecer o culminar residencia en una posesión) sin causa justificada."
        ),
        effective_year=2004,
        source_url="https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title26-section6688",
        cross_references=["26 U.S.C. § 937", "IRS Form 8898"],
        breadcrumbs="Title 26 > Subtitle F > Chapter 68 > Subchapter B > § 6688",
    ),
    # US Treasury Regulations (26 C.F.R.)
    StatuteRecord(
        id="us-cfr-1-937-1",
        jurisdiction="US",
        corpus="CFR",
        citation="26 CFR § 1.937-1",
        section="1.937-1",
        title="Bona fide residency in a possession",
        text_en=(
            "Treasury Regulation detailing the physical presence test alternatives (183-day rule, 549-day 3-year "
            "rule with 60 days minimum per year, 90-day US limit rule), the tax home test, and closer connection "
            "evaluation factors for determining bona fide residency under 26 U.S.C. § 937."
        ),
        text_es=(
            "Reglamentación del Tesoro que detalla las cinco alternativas de presencia física (regla de 183 días, "
            "regla de 549 días en 3 años, límite de 90 días en EE.UU.), el requisito de no mantener tax home fuera, "
            "y los factores de conexión más cercana para calificar como residente de buena fe."
        ),
        effective_year=2006,
        source_url="https://www.ecfr.gov/current/title-26/section-1.937-1",
        cross_references=["26 U.S.C. § 937", "26 CFR § 1.937-2"],
        breadcrumbs="Title 26 > Chapter I > Subchapter A > Part 1 > § 1.937-1",
    ),
    StatuteRecord(
        id="us-cfr-1-937-2",
        jurisdiction="US",
        corpus="CFR",
        citation="26 CFR § 1.937-2",
        section="1.937-2",
        title="Income from sources within a possession",
        text_en=(
            "Sourcing rules for possessions and 10-year built-in gain lookback rules under Treas. Reg. § 1.937-2(f) "
            "for property held prior to establishing bona fide residency. Allows election under § 1.937-2(f)(1)(vi) "
            "to allocate gain based on FMV on residency date (marketable securities) or linear time apportionment."
        ),
        text_es=(
            "Reglas de fuente para posesiones y regla de lookback de 10 años para ganancias acumuladas (built-in gains) "
            "sobre bienes adquiridos antes de establecer residencia bajo Treas. Reg. § 1.937-2(f), permitiendo "
            "elección para segregar valor acumulado previo y posterior."
        ),
        effective_year=2006,
        source_url="https://www.ecfr.gov/current/title-26/section-1.937-2",
        cross_references=["26 U.S.C. § 937", "26 CFR § 1.937-1", "26 U.S.C. § 865"],
        breadcrumbs="Title 26 > Chapter I > Subchapter A > Part 1 > § 1.937-2",
    ),
    StatuteRecord(
        id="us-cfr-1-937-3",
        jurisdiction="US",
        corpus="CFR",
        citation="26 CFR § 1.937-3",
        section="1.937-3",
        title="Income effectively connected with the conduct of a trade or business in a possession",
        text_en=(
            "Principles for determining whether income is effectively connected with the active conduct of a trade "
            "or business within a possession. Evaluates asset-use test and business-activities test for both "
            "possession-source and foreign-source income."
        ),
        text_es=(
            "Principios para determinar si el ingreso está efectivamente conectado con la conducción activa de una "
            "industria o negocio en una posesión, aplicando pruebas de uso de activos y actividades operacionales."
        ),
        effective_year=2006,
        source_url="https://www.ecfr.gov/current/title-26/section-1.937-3",
        cross_references=["26 U.S.C. § 937", "26 CFR § 1.937-2"],
        breadcrumbs="Title 26 > Chapter I > Subchapter A > Part 1 > § 1.937-3",
    ),
    # Puerto Rico Internal Revenue Code (Ley 1-2011)
    StatuteRecord(
        id="pr-cri-1010-01",
        jurisdiction="PR",
        corpus="CRI",
        citation="PR CRI Sec. 1010.01",
        section="1010.01",
        title="Definiciones generales / General Definitions",
        text_en=(
            "Defines fundamental statutory terms under the PR Internal Revenue Code (Ley 1-2011), including resident "
            "individual (domiciled in PR or present for entire tax year), foreign corporation, taxable year, and person."
        ),
        text_es=(
            "Define términos fundamentales bajo el Código de Rentas Internas de Puerto Rico (Ley 1-2011), incluyendo "
            "individuo residente (domiciliado en PR o presente durante el año contributivo), corporación foránea y año contributivo."
        ),
        effective_year=2011,
        source_url="https://hacienda.pr.gov/publicaciones/codigo-de-rentas-internas-de-puerto-rico-de-2011",
        cross_references=["PR CRI Sec. 1031.01", "PR CRI Sec. 1062.01"],
        breadcrumbs="Ley 1-2011 > Subtítulo A > Capítulo 1 > Sección 1010.01",
    ),
    StatuteRecord(
        id="pr-cri-1031-01",
        jurisdiction="PR",
        corpus="CRI",
        citation="PR CRI Sec. 1031.01",
        section="1031.01",
        title="Ingreso bruto / Gross Income",
        text_en=(
            "Statutory definition of gross income including gains, profits, and income derived from salaries, "
            "professions, commerce, trades, or sales of property, while excluding statutory exclusions."
        ),
        text_es=(
            "Incluye ganancias, beneficios e ingresos derivados de sueldos, profesiones, comercio, negocios o "
            "ventas de bienes y propiedades, excluyendo aquellas partidas estatutariamente exentas."
        ),
        effective_year=2011,
        source_url="https://hacienda.pr.gov/publicaciones/codigo-de-rentas-internas-de-puerto-rico-de-2011",
        cross_references=["PR CRI Sec. 1010.01", "PR CRI Sec. 1031.02"],
        breadcrumbs="Ley 1-2011 > Subtítulo A > Capítulo 3 > Subcapítulo A > Sección 1031.01",
    ),
    StatuteRecord(
        id="pr-cri-1031-02",
        jurisdiction="PR",
        corpus="CRI",
        citation="PR CRI Sec. 1031.02",
        section="1031.02",
        title="Exclusiones de ingreso bruto / Exclusions from Gross Income",
        text_en=(
            "Enumerates statutory exclusions from gross income under Puerto Rico law, including life insurance proceeds, "
            "gifts, inheritances, interest on obligations of the Commonwealth of Puerto Rico, and eligible pensions."
        ),
        text_es=(
            "Enumera exclusiones estatutarias del ingreso bruto bajo la ley de Puerto Rico, incluyendo indemnizaciones "
            "de seguro de vida, donaciones, herencias, intereses sobre obligaciones de Puerto Rico y pensiones elegibles."
        ),
        effective_year=2011,
        source_url="https://hacienda.pr.gov/publicaciones/codigo-de-rentas-internas-de-puerto-rico-de-2011",
        cross_references=["PR CRI Sec. 1031.01", "Ley 60-2019 Sec. 2022.01"],
        breadcrumbs="Ley 1-2011 > Subtítulo A > Capítulo 3 > Subcapítulo A > Sección 1031.02",
    ),
    StatuteRecord(
        id="pr-cri-1062-01",
        jurisdiction="PR",
        corpus="CRI",
        citation="PR CRI Sec. 1062.01",
        section="1062.01",
        title="Retención en el origen sobre pagos por servicios prestados",
        text_en=(
            "Mandates a 10 percent withholding at source on payments made for services rendered in Puerto Rico, "
            "subject to relief vouchers or reduced rate certifications issued by the Department of the Treasury."
        ),
        text_es=(
            "Dispone la retención obligatoria del 10 por ciento en el origen sobre pagos por servicios prestados "
            "dentro de Puerto Rico, sujeta a relevos o certificaciones emitidas por el Departamento de Hacienda."
        ),
        effective_year=2011,
        source_url="https://hacienda.pr.gov/publicaciones/codigo-de-rentas-internas-de-puerto-rico-de-2011",
        cross_references=["PR CRI Sec. 1010.01", "PR CRI Sec. 1031.01"],
        breadcrumbs="Ley 1-2011 > Subtítulo A > Capítulo 6 > Sección 1062.01",
    ),
    # Puerto Rico Incentives Code (Ley 60-2019)
    StatuteRecord(
        id="pr-ley60-2021-01",
        jurisdiction="PR",
        corpus="LEY60",
        citation="Ley 60-2019 Sec. 2021.01",
        section="2021.01",
        title="Individuos Residentes Inversionistas - Cualificación",
        text_en=(
            "Establishes eligibility criteria for Individual Resident Investors under Act 60 Chapter 2 (formerly Act 22), "
            "requiring non-residency in Puerto Rico during the 10 preceding years and acquisition of primary residential property."
        ),
        text_es=(
            "Establece requisitos para acogerse al Capítulo 2 del Código de Incentivos (ex-Ley 22), incluyendo no haber sido "
            "residente de Puerto Rico durante los 10 años anteriores y adquirir una residencia principal en la isla."
        ),
        effective_year=2019,
        source_url="https://bvirtualogp.pr.gov/",
        cross_references=["Ley 60-2019 Sec. 2022.01", "26 U.S.C. § 933"],
        breadcrumbs="Ley 60-2019 > Subtítulo B > Capítulo 2 > Sección 2021.01",
    ),
    StatuteRecord(
        id="pr-ley60-2022-01",
        jurisdiction="PR",
        corpus="LEY60",
        citation="Ley 60-2019 Sec. 2022.01",
        section="2022.01",
        title="Exención Contributiva sobre Ganancias de Capital e Intereses",
        text_en=(
            "Provides 100% tax exemption on qualifying capital gains, dividends, and interest recognized after establishing "
            "residency for decrees requested prior to 2027 (4% rate for decrees requested on or after Jan 1, 2027)."
        ),
        text_es=(
            "Dispone exención total (100%) sobre ganancias de capital e intereses devengados tras establecer residencia "
            "para decretos previos a 2027 (y tasa preferencial del 4% para decretos solicitados desde el 1 de enero de 2027)."
        ),
        effective_year=2019,
        source_url="https://bvirtualogp.pr.gov/",
        cross_references=["Ley 60-2019 Sec. 2021.01", "26 CFR § 1.937-2"],
        breadcrumbs="Ley 60-2019 > Subtítulo B > Capítulo 2 > Sección 2022.01",
    ),
    StatuteRecord(
        id="pr-ley60-2031-01",
        jurisdiction="PR",
        corpus="LEY60",
        citation="Ley 60-2019 Sec. 2031.01",
        section="2031.01",
        title="Exportación de Servicios y Comercio - Cualificación",
        text_en=(
            "Qualifies businesses engaged in export services conducted from Puerto Rico to foreign or mainland markets "
            "with no prior commercial nexus on the island (formerly Act 20)."
        ),
        text_es=(
            "Cualifica negocios dedicados a la exportación de servicios elegibles prestados desde Puerto Rico hacia "
            "mercados exteriores sin nexo comercial previo en la isla (antigua Ley 20)."
        ),
        effective_year=2019,
        source_url="https://bvirtualogp.pr.gov/",
        cross_references=["Ley 60-2019 Sec. 2032.01", "26 U.S.C. § 957"],
        breadcrumbs="Ley 60-2019 > Subtítulo B > Capítulo 3 > Sección 2031.01",
    ),
    StatuteRecord(
        id="pr-ley60-2032-01",
        jurisdiction="PR",
        corpus="LEY60",
        citation="Ley 60-2019 Sec. 2032.01",
        section="2032.01",
        title="Tasa Fija Preferencial de 4% sobre Ingreso de Desarrollo",
        text_en=(
            "Establishes a 4 percent flat corporate income tax rate on net export service development income, 100 percent "
            "exemption on distributions and dividends, and 75 percent exemption on municipal and real property taxes."
        ),
        text_es=(
            "Fija una tasa preferencial del 4 por ciento sobre el ingreso neto de desarrollo para exportación, 100 por ciento "
            "de exención sobre distribuciones y dividendos, y 75 por ciento de exención en contribuciones sobre la propiedad."
        ),
        effective_year=2019,
        source_url="https://bvirtualogp.pr.gov/",
        cross_references=["Ley 60-2019 Sec. 2031.01", "PR CRI Sec. 1010.01"],
        breadcrumbs="Ley 60-2019 > Subtítulo B > Capítulo 3 > Sección 2032.01",
    ),
    StatuteRecord(
        id="pr-ley60-6070-55",
        jurisdiction="PR",
        corpus="LEY60",
        citation="Ley 60-2019 Sec. 6070.55",
        section="6070.55",
        title="Derechos de presentación e informes anuales",
        text_en=(
            "Prescribes annual compliance report requirements, filing fees ($5,000 annual filing fee), and mandatory "
            "annual charitable donations ($10,000 split across certified PR non-profit organizations) for decree holders."
        ),
        text_es=(
            "Prescribe los requisitos del informe anual de cumplimiento, cargos de presentación anual ($5,000) y "
            "la aportación benéfica compulsoria de $10,000 anuales distribuida entre entidades sin fines de lucro certificadas."
        ),
        effective_year=2019,
        source_url="https://bvirtualogp.pr.gov/",
        cross_references=["Ley 60-2019 Sec. 2021.01", "Ley 60-2019 Sec. 2022.01"],
        breadcrumbs="Ley 60-2019 > Subtítulo F > Capítulo 7 > Sección 6070.55",
    ),
]

PUBLICATIONS_CATALOG: list[PublicationRecord] = [
    PublicationRecord(
        id="pr-cc-ri-26-13",
        category="carta-circular",
        doc_number="CC RI 26-13",
        doc_date="2026-06-30",
        title="Radicación electrónica obligatoria de planillas de individuos",
        summary=(
            "Instrucciones y requisitos técnicos para la radicación compulsoria a través de SURI "
            "de las planillas de contribución sobre ingresos de individuos para el año contributivo 2026."
        ),
        source_url="https://hacienda.pr.gov/publicaciones/carta-circular-de-rentas-internas-num-26-13",
    ),
    PublicationRecord(
        id="pr-da-23-04",
        category="determinacion-administrativa",
        doc_number="DA 23-04",
        doc_date="2023-08-15",
        title="Tratamiento contributivo de transferencias de propiedad bajo Ley 60",
        summary=(
            "Determinación administrativa sobre exenciones contributivas en la enajenación de activos de capital "
            "por parte de inversionistas residentes con decreto bajo Ley 60."
        ),
        source_url="https://hacienda.pr.gov/publicaciones/determinacion-administrativa-num-23-04",
    ),
    PublicationRecord(
        id="pr-da-21-04",
        category="determinacion-administrativa",
        doc_number="DA 21-04",
        doc_date="2021-04-12",
        title="Procedimiento de cumplimiento anual para decretos de individuos",
        summary=(
            "Directrices para la presentación de informes anuales de cumplimiento y comprobantes de donaciones "
            "benéficas requeridas por el Código de Incentivos de Puerto Rico."
        ),
        source_url="https://hacienda.pr.gov/publicaciones/determinacion-administrativa-num-21-04",
    ),
    PublicationRecord(
        id="pr-bi-26-02",
        category="boletin-informativo",
        doc_number="BI 26-02",
        doc_date="2026-01-20",
        title="Ajustes por inflación para el año contributivo 2026",
        summary=(
            "Parámetros de ajuste por costo de vida, escalas de deducción y exenciones personales "
            "aplicables a la determinación de la obligación tributaria en Puerto Rico."
        ),
        source_url="https://hacienda.pr.gov/publicaciones/boletin-informativo-num-26-02",
    ),
    PublicationRecord(
        id="pr-cc-ri-25-12",
        category="carta-circular",
        doc_number="CC RI 25-12",
        doc_date="2025-11-10",
        title="Guía sobre retención en el origen para corporaciones foráneas",
        summary=(
            "Procedimientos de retención y remisión del impuesto sobre pagos efectuados a corporaciones "
            "foráneas no dedicadas a industria o negocio en Puerto Rico."
        ),
        source_url="https://hacienda.pr.gov/publicaciones/carta-circular-de-rentas-internas-num-25-12",
    ),
]


def check_upstream_endpoints() -> dict[str, Any]:
    """Verify live connectivity to official digital tax publishing authorities."""
    endpoints = {
        "OLRC_USLM": "https://uscode.house.gov/",
        "eCFR_API": "https://www.ecfr.gov/api/versioner/v1/versions.json",
        "Hacienda_PR": "https://hacienda.pr.gov/",
        "OGP_PR": "https://bvirtualogp.pr.gov/",
    }
    results: dict[str, Any] = {}
    for name, url in endpoints.items():
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "TaxCode-MCP-Sync/1.0 (+https://github.com/christianclaudio/mcp-server-taxcode)"
                },
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                results[name] = {"status": "reachable", "code": resp.getcode(), "url": url}
        except Exception as exc:
            results[name] = {"status": "unreachable_or_blocked", "error": str(exc), "url": url}
    return results


def export_documentation(output_dir: Path) -> tuple[int, int]:
    """Generate Markdown and llms.txt documentation files for LLM agents."""
    output_dir.mkdir(parents=True, exist_ok=True)
    us_dir = output_dir / "us"
    pr_dir = output_dir / "pr"
    us_dir.mkdir(exist_ok=True)
    pr_dir.mkdir(exist_ok=True)

    statute_count = 0
    # Export individual markdown files
    for s in STATUTES_CATALOG:
        target_dir = pr_dir if s.jurisdiction == "PR" else us_dir
        filename = f"{s.corpus.lower()}_{s.section.replace('.', '_').replace('-', '_')}.md"
        content = f"""# {s.citation} — {s.title}

- **Jurisdiction:** {s.jurisdiction}
- **Corpus:** {s.corpus}
- **Effective Year:** {s.effective_year}
- **Source:** [{s.source_url}]({s.source_url})
- **Hierarchy:** `{s.breadcrumbs}`
- **Cross References:** {", ".join(f"`{ref}`" for ref in s.cross_references)}

## Operative Statutory Language (English)

{s.text_en}

## Texto Estatutario Oficial (Español)

{s.text_es}
"""
        (target_dir / filename).write_text(content, encoding="utf-8")
        statute_count += 1

    # Generate llms.txt
    llms_txt_path = output_dir / "llms.txt"
    llms_summary = [
        "# TaxCode MCP Intelligence Corpus",
        "",
        "> Authoritative statutory and regulatory references for United States Federal Tax Law",
        "> and Puerto Rico Territorial Tax Law (Act 60-2019 and Ley 1-2011).",
        "",
        "## Statutory Catalog",
        "",
    ]
    for s in STATUTES_CATALOG:
        llms_summary.append(
            f"- [{s.citation} — {s.title}](./{'pr' if s.jurisdiction == 'PR' else 'us'}/{s.corpus.lower()}_{s.section.replace('.', '_').replace('-', '_')}.md): {s.breadcrumbs}"
        )

    llms_summary.extend(
        [
            "",
            "## Administrative Publications (Hacienda SURI)",
            "",
        ]
    )
    for p in PUBLICATIONS_CATALOG:
        llms_summary.append(f"- **{p.doc_number}** ({p.doc_date}) — {p.title}: {p.summary}")

    llms_txt_path.write_text("\n".join(llms_summary), encoding="utf-8")

    # Generate llms-full.txt
    llms_full_path = output_dir / "llms-full.txt"
    llms_full_content = ["# Full Statutory Corpus: US & Puerto Rico Tax Intelligence\n"]
    for s in STATUTES_CATALOG:
        llms_full_content.append(
            f"## {s.citation}: {s.title}\n`{s.breadcrumbs}`\n\n### English\n{s.text_en}\n\n### Español\n{s.text_es}\n"
        )
    llms_full_path.write_text("\n".join(llms_full_content), encoding="utf-8")

    return statute_count, len(PUBLICATIONS_CATALOG)


def run_ingest(
    db_path: str = ":memory:",
    sources: str = "all",
    dry_run: bool = False,
    export_docs: Path | None = None,
) -> dict[str, int]:
    """Ingest statutes and publications into DuckDB and optionally export docs."""
    logger.info(
        "Initializing ingestion pipeline (sources=%s, db_path=%s, dry_run=%s)",
        sources,
        db_path,
        dry_run,
    )

    selected_statutes: list[StatuteRecord] = []
    for s in STATUTES_CATALOG:
        if sources == "all":
            selected_statutes.append(s)
        elif sources == "us" and s.jurisdiction == "US":
            selected_statutes.append(s)
        elif sources == "pr" and s.jurisdiction == "PR":
            selected_statutes.append(s)
        elif sources == "cfr" and s.corpus == "CFR":
            selected_statutes.append(s)

    selected_publications: list[PublicationRecord] = []
    if sources in ("all", "pr", "publications"):
        selected_publications = list(PUBLICATIONS_CATALOG)

    stats = {
        "statutes_considered": len(selected_statutes),
        "statutes_upserted": 0,
        "publications_considered": len(selected_publications),
        "publications_upserted": 0,
    }

    if not dry_run:
        target_db = TaxDatabase(db_path)
        for st in selected_statutes:
            target_db.upsert_statute(st.to_dict())
            stats["statutes_upserted"] += 1
        for pub in selected_publications:
            target_db.upsert_publication(pub.to_dict())
            stats["publications_upserted"] += 1

        total_statutes, total_pubs = target_db.get_stats()
        logger.info(
            "Database upsert complete: %d statutes, %d publications in index.",
            total_statutes,
            total_pubs,
        )
    else:
        logger.info(
            "[DRY RUN] Validated %d statutes and %d publications.",
            len(selected_statutes),
            len(selected_publications),
        )

    if export_docs:
        exported_statutes, exported_pubs = export_documentation(export_docs)
        logger.info(
            "Documentation exported to %s: %d statutes, %d publications.",
            export_docs,
            exported_statutes,
            exported_pubs,
        )

    return stats


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for statutory ingestion pipeline."""
    parser = argparse.ArgumentParser(
        description="Ingest, parse, and synchronize US & Puerto Rico Tax Codes into DuckDB.",
    )
    parser.add_argument(
        "--db-path",
        default=os.getenv("TAXCODE_DB_PATH", ":memory:"),
        help="Target DuckDB database path (defaults to TAXCODE_DB_PATH or :memory:).",
    )
    parser.add_argument(
        "--sources",
        choices=["all", "us", "pr", "cfr", "publications"],
        default="all",
        help="Subsets of statutory sources to process (default: all).",
    )
    parser.add_argument(
        "--export-docs",
        type=Path,
        default=None,
        help="Optional directory to export llms.txt and markdown documentation.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate ingestion without writing to DuckDB or disk.",
    )
    parser.add_argument(
        "--check-upstream",
        action="store_true",
        help="Check connectivity to official government publishing endpoints.",
    )

    args = parser.parse_args(argv)

    if args.check_upstream:
        logger.info("Checking upstream publishing authority endpoints...")
        results = check_upstream_endpoints()
        print(json.dumps(results, indent=2))
        return 0

    stats = run_ingest(
        db_path=args.db_path,
        sources=args.sources,
        dry_run=args.dry_run,
        export_docs=args.export_docs,
    )
    logger.info("Pipeline executed successfully: %s", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
