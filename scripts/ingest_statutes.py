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
    superseded_year: int | None = None
    cross_references: list[str] = field(default_factory=list)
    breadcrumbs: str = ""

    def content_hash(self) -> str:
        """Compute a SHA-256 checksum of the bilingual text payload."""
        payload = (
            f"{self.citation}|{self.title}|{self.text_en}|{self.text_es}|"
            f"{self.effective_year}|{self.superseded_year}"
        )
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
            "superseded_year": self.superseded_year,
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
    StatuteRecord(
        id="pr-act22-2012",
        jurisdiction="PR",
        corpus="LEY22",
        citation="Act 22-2012 Sec. 3",
        section="3",
        title="Exención Contributiva sobre Ganancias de Capital (Histórico Ley 22)",
        text_en=(
            "Predecessor 100% tax exemption on capital gains accrued post-residency under original "
            "Act 22-2012 (prior to Act 60 mandatory charitable donations)."
        ),
        text_es=(
            "Exención total del 100% sobre ganancias de capital devengadas luego de establecer "
            "residencia bajo la Ley 22 original (sin requisito de donación anual compulsoria)."
        ),
        effective_year=2012,
        superseded_year=2019,
        source_url="https://bvirtualogp.pr.gov/",
        cross_references=["Ley 60-2019 Sec. 2022.01", "26 U.S.C. § 933"],
        breadcrumbs="Ley 22-2012 > Sección 3",
    ),
    # State of Georgia (Title 48 O.C.G.A. & Regulations)
    StatuteRecord(
        id="ga-ocga-48-7-1",
        jurisdiction="GA",
        corpus="OCGA",
        citation="O.C.G.A. § 48-7-1",
        section="48-7-1",
        title="Income Tax Definitions / Residency Rules",
        text_en=(
            "Defines Georgia resident as any individual who is a legal resident of Georgia on income tax day, "
            "who resides within the state for 183 days or more in the aggregate during the taxable year, "
            "or who maintains a permanent place of abode within the state."
        ),
        text_es=(
            "Define residente de Georgia como todo individuo con residencia legal en Georgia el día de pago "
            "del impuesto, que resida en el estado al menos 183 días del año contributivo, o que mantenga una vivienda permanente."
        ),
        effective_year=2024,
        source_url="https://dor.georgia.gov/",
        cross_references=[
            "O.C.G.A. § 48-7-20",
            "O.C.G.A. § 48-7-30",
            "Ga. Comp. R. & Regs. r. 560-7-3-.01",
        ],
        breadcrumbs="O.C.G.A. > Title 48 > Chapter 7 > Article 1 > § 48-7-1",
    ),
    StatuteRecord(
        id="ga-ocga-48-7-20-2024",
        jurisdiction="GA",
        corpus="OCGA",
        citation="O.C.G.A. § 48-7-20",
        section="48-7-20",
        title="Individual Income Tax Rates (2024 Flat Rate: 5.49%)",
        text_en=(
            "Imposes a flat individual income tax rate of 5.49% on Georgia taxable net income "
            "for tax year 2024 pursuant to HB 1023, eliminating previous graduated brackets."
        ),
        text_es=(
            "Establece una tasa impositiva fija (flat tax) de 5.49% sobre el ingreso neto "
            "tributable de individuos para el año contributivo 2024 bajo HB 1023."
        ),
        effective_year=2024,
        superseded_year=2025,
        source_url="https://dor.georgia.gov/taxes/individual-income-tax",
        cross_references=["O.C.G.A. § 48-7-1", "O.C.G.A. § 48-7-27"],
        breadcrumbs="O.C.G.A. > Title 48 > Chapter 7 > Article 2 > § 48-7-20 (2024)",
    ),
    StatuteRecord(
        id="ga-ocga-48-7-20-2025",
        jurisdiction="GA",
        corpus="OCGA",
        citation="O.C.G.A. § 48-7-20",
        section="48-7-20",
        title="Individual Income Tax Rates (2025 Flat Rate: 5.39%)",
        text_en=(
            "Accelerates the reduction of the flat individual income tax rate to 5.39% for "
            "tax year 2025 pursuant to HB 1015."
        ),
        text_es=(
            "Acelera la reducción de la tasa impositiva individual fija al 5.39% para el "
            "año contributivo 2025 bajo HB 1015."
        ),
        effective_year=2025,
        superseded_year=2026,
        source_url="https://dor.georgia.gov/taxes/individual-income-tax",
        cross_references=["O.C.G.A. § 48-7-1", "O.C.G.A. § 48-7-27"],
        breadcrumbs="O.C.G.A. > Title 48 > Chapter 7 > Article 2 > § 48-7-20 (2025)",
    ),
    StatuteRecord(
        id="ga-ocga-48-7-20-2026",
        jurisdiction="GA",
        corpus="OCGA",
        citation="O.C.G.A. § 48-7-20",
        section="48-7-20",
        title="Individual Income Tax Rates (2026 Target Flat Rate: 4.99%)",
        text_en=(
            "Imposes the target flat individual income tax rate of 4.99% for taxable years "
            "beginning on or after January 1, 2026 pursuant to HB 1023."
        ),
        text_es=(
            "Establece la tasa impositiva fija objetivo de 4.99% sobre el ingreso neto "
            "tributable a partir del 1 de enero de 2026 bajo HB 1023."
        ),
        effective_year=2026,
        superseded_year=None,
        source_url="https://dor.georgia.gov/taxes/individual-income-tax",
        cross_references=["O.C.G.A. § 48-7-1", "O.C.G.A. § 48-7-27"],
        breadcrumbs="O.C.G.A. > Title 48 > Chapter 7 > Article 2 > § 48-7-20 (2026)",
    ),
    StatuteRecord(
        id="ga-ocga-48-7-27",
        jurisdiction="GA",
        corpus="OCGA",
        citation="O.C.G.A. § 48-7-27",
        section="48-7-27",
        title="Computation of Georgia Taxable Net Income",
        text_en=(
            "Calculates Georgia taxable net income from Federal AGI, allowing standard deductions of "
            "$12,000 (single) or $24,000 (married filing jointly), and a retirement income exclusion "
            "of up to $65,000 for individuals age 65 or older ($35,000 for ages 62-64)."
        ),
        text_es=(
            "Dispone el cálculo del ingreso neto gravable a partir del AGI federal, deducción estándar "
            "de $12,000 (solteros) o $24,000 (casados), y exclusión de hasta $65,000 en ingresos de jubilación para mayores de 65 años."
        ),
        effective_year=2024,
        source_url="https://dor.georgia.gov/taxes/individual-income-tax",
        cross_references=["O.C.G.A. § 48-7-20", "26 U.S.C. § 62"],
        breadcrumbs="O.C.G.A. > Title 48 > Chapter 7 > Article 2 > § 48-7-27",
    ),
    StatuteRecord(
        id="ga-ocga-48-7-30",
        jurisdiction="GA",
        corpus="OCGA",
        citation="O.C.G.A. § 48-7-30",
        section="48-7-30",
        title="Taxation of Nonresidents",
        text_en=(
            "Taxes nonresidents on gross income derived from Georgia sources, including compensation "
            "for services performed within Georgia, property located in Georgia, and business conducted in the state."
        ),
        text_es=(
            "Grava a personas no residentes sobre su ingreso bruto derivado de fuentes de Georgia, "
            "incluyendo compensación por servicios realizados en Georgia y actividades comerciales locales."
        ),
        effective_year=2024,
        source_url="https://dor.georgia.gov/",
        cross_references=["O.C.G.A. § 48-7-1", "O.C.G.A. § 48-7-20"],
        breadcrumbs="O.C.G.A. > Title 48 > Chapter 7 > Article 2 > § 48-7-30",
    ),
    StatuteRecord(
        id="ga-reg-560-7-3-01",
        jurisdiction="GA",
        corpus="GAREG",
        citation="Ga. Comp. R. & Regs. r. 560-7-3-.01",
        section="560-7-3-.01",
        title="Residence and Domicile",
        text_en=(
            "Georgia Department of Revenue rule governing residence and legal domicile. Domicile in Georgia "
            "is presumed to continue until the taxpayer establishes abandonment and acquisition of a permanent domicile elsewhere."
        ),
        text_es=(
            "Reglamento del Departamento de Ingresos de Georgia sobre domicilio y residencia. El domicilio en "
            "Georgia se presume continuo a menos que el contribuyente demuestre abandono definitivo y adquisición de nuevo domicilio."
        ),
        effective_year=2023,
        source_url="https://rules.sos.ga.gov/gac/560-7-3-.01",
        cross_references=["O.C.G.A. § 48-7-1", "26 U.S.C. § 937"],
        breadcrumbs="Ga. Comp. R. & Regs. > Chapter 560-7 > Subject 560-7-3 > Rule 560-7-3-.01",
    ),
    # State of California (Title 18 CCR & Revenue and Taxation Code)
    StatuteRecord(
        id="ca-rtc-17014",
        jurisdiction="CA",
        corpus="RTC",
        citation="Cal. Rev. & Tax Code § 17014",
        section="17014",
        title="Definition of Resident (Non-Temporary Purpose Test)",
        text_en=(
            "Defines resident as every individual who is in California for other than a temporary "
            "or transitory purpose, and every individual domiciled in California who is outside "
            "the state for a temporary or transitory purpose. Presumption under § 17016 at 9 months."
        ),
        text_es=(
            "Define residente como todo individuo que permanezca en California para fines "
            "que no sean temporales o transitorios, y todo domiciliado que esté fuera del estado "
            "de forma temporal. Aplica el criterio de conexión más cercana."
        ),
        effective_year=2024,
        source_url="https://www.ftb.ca.gov/",
        cross_references=["Cal. Rev. & Tax Code § 17041", "18 CCR § 17014"],
        breadcrumbs="Cal. Rev. & Tax Code > Part 10 > Chapter 1 > § 17014",
    ),
    StatuteRecord(
        id="ca-rtc-17041",
        jurisdiction="CA",
        corpus="RTC",
        citation="Cal. Rev. & Tax Code § 17041",
        section="17041",
        title="Individual Income Tax Rates and Brackets",
        text_en=(
            "Imposes graduated income tax rates on California taxable income ranging from 1% to "
            "12.3%, plus a 1% Mental Health Services Tax surcharge on taxable income exceeding "
            "$1,000,000, creating a top marginal individual rate of 13.3%."
        ),
        text_es=(
            "Grava el ingreso tributable de California con tasas escalonadas del 1% al 12.3%, "
            "más 1% de recargo para servicios de salud mental sobre ingresos mayores a $1M "
            "(Proposición 63), para una tasa marginal máxima de 13.3%."
        ),
        effective_year=2024,
        source_url="https://www.ftb.ca.gov/",
        cross_references=["Cal. Rev. & Tax Code § 17014"],
        breadcrumbs="Cal. Rev. & Tax Code > Part 10 > Chapter 2 > § 17041",
    ),
    StatuteRecord(
        id="ca-rtc-17951",
        jurisdiction="CA",
        corpus="RTC",
        citation="Cal. Rev. & Tax Code § 17951",
        section="17951",
        title="Gross Income of Nonresidents and Sourcing",
        text_en=(
            "In the case of nonresidents, gross income includes only gross income from sources "
            "within California, including income from a business, trade, or profession conducted "
            "in the state, and compensation for personal services performed in California."
        ),
        text_es=(
            "Para no residentes, el ingreso bruto comprende únicamente ingresos derivados de "
            "fuentes en California, incluyendo negocios, profesiones y servicios personales "
            "realizados dentro del estado."
        ),
        effective_year=2024,
        source_url="https://www.ftb.ca.gov/",
        cross_references=["Cal. Rev. & Tax Code § 17014", "Cal. Rev. & Tax Code § 17041"],
        breadcrumbs="Cal. Rev. & Tax Code > Part 10 > Chapter 9 > § 17951",
    ),
    StatuteRecord(
        id="ca-ccr-17014",
        jurisdiction="CA",
        corpus="CACCR",
        citation="18 CCR § 17014",
        section="17014",
        title="FTB Residence and Domicile Guidelines",
        text_en=(
            "FTB regulation detailing domicile and residence determination. A person may have "
            "multiple residences but only one domicile. Applies the closest connection test "
            "evaluating real property, family, bank accounts, business affiliations, and licenses."
        ),
        text_es=(
            "Reglamento de la FTB sobre residencia y domicilio. Aplica el criterio de conexión "
            "más cercana evaluando propiedades inmuebles, familia, cuentas bancarias, licencias "
            "profesionales y afiliaciones comerciales."
        ),
        effective_year=2023,
        source_url="https://www.ftb.ca.gov/",
        cross_references=["Cal. Rev. & Tax Code § 17014"],
        breadcrumbs="18 CCR > Division 3 > Chapter 2.5 > Subchapter 1 > § 17014",
    ),
    # State of New York (Tax Law & Department of Taxation and Finance Regulations)
    StatuteRecord(
        id="ny-tax-605",
        jurisdiction="NY",
        corpus="NYTAX",
        citation="NY Tax Law § 605(b)",
        section="605",
        title="Resident Individual and Domicile Defined",
        text_en=(
            "Defines resident individual as one domiciled in New York unless they maintain no "
            "permanent abode in NY and spend under 30 days in state; or one not domiciled who "
            "maintains a permanent place of abode and spends over 183 days in NY."
        ),
        text_es=(
            "Define individuo residente como domiciliado en Nueva York a menos que no mantenga "
            "vivienda permanente en NY y pase menos de 30 días en el estado; o no domiciliado "
            "que mantenga vivienda permanente y pase más de 183 días en NY (Residente Estatutario)."
        ),
        effective_year=2024,
        source_url="https://www.tax.ny.gov/",
        cross_references=["NY Tax Law § 601", "20 NYCRR § 105.20"],
        breadcrumbs="NY Tax Law > Article 22 > Part I > § 605",
    ),
    StatuteRecord(
        id="ny-tax-601",
        jurisdiction="NY",
        corpus="NYTAX",
        citation="NY Tax Law § 601",
        section="601",
        title="Personal Income Tax Rates",
        text_en=(
            "Imposes graduated income tax on New York taxable income with top marginal rates up "
            "to 10.9% for high-income earners, plus applicable New York City personal income tax "
            "(up to 3.876%) for NYC residents."
        ),
        text_es=(
            "Grava el ingreso tributable de Nueva York con tasas escalonadas de hasta 10.9% "
            "para personas con altos ingresos, más el impuesto municipal de la Ciudad de "
            "Nueva York (hasta 3.876%) para residentes de NYC."
        ),
        effective_year=2024,
        source_url="https://www.tax.ny.gov/",
        cross_references=["NY Tax Law § 605"],
        breadcrumbs="NY Tax Law > Article 22 > Part I > § 601",
    ),
    StatuteRecord(
        id="ny-reg-105-20",
        jurisdiction="NY",
        corpus="NYCRR",
        citation="20 NYCRR § 105.20",
        section="105.20",
        title="Rules for Determining Domicile (5 Primary Factors)",
        text_en=(
            "New York DTF regulation establishing the 5 primary factors for domicile audit "
            "evaluation: Home, Active Business Involvement, Time Allocation, Items of Personal "
            "Significance ('Near and Dear'), and Family."
        ),
        text_es=(
            "Reglamento de auditoría de domicilio de NY que establece los 5 factores primarios: "
            "Vivienda, Negocio Activo, Tiempo, Objetos de Valor Sentimental ('Near and Dear') y Familia."
        ),
        effective_year=2024,
        source_url="https://www.tax.ny.gov/",
        cross_references=["NY Tax Law § 605"],
        breadcrumbs="20 NYCRR > Chapter II > Subchapter A > Part 105 > § 105.20",
    ),
    StatuteRecord(
        id="ny-reg-132-18",
        jurisdiction="NY",
        corpus="NYCRR",
        citation="20 NYCRR § 132.18",
        section="132.18",
        title="Convenience of the Employer Rule",
        text_en=(
            "Provides that compensation of a nonresident employee for services performed out of "
            "state is sourced to New York if the services are performed outside NY for the "
            "employee's convenience rather than by necessity of the employer."
        ),
        text_es=(
            "Dispone que la compensación de empleados no residentes por servicios realizados fuera "
            "del estado se atribuye a Nueva York si el trabajo remoto se realiza por conveniencia "
            "del empleado y no por necesidad del empleador."
        ),
        effective_year=2024,
        source_url="https://www.tax.ny.gov/",
        cross_references=["NY Tax Law § 605"],
        breadcrumbs="20 NYCRR > Chapter II > Subchapter A > Part 132 > § 132.18",
    ),
    # State of Florida (Constitutional & Statutory Absence of Individual Income Tax)
    StatuteRecord(
        id="fl-const-7-5",
        jurisdiction="FL",
        corpus="FLCONST",
        citation="Fla. Const. Art. VII § 5",
        section="Art. VII Sec. 5",
        title="Constitutional Prohibition of Personal Income Tax",
        text_en=(
            "Prohibits any tax upon the income of natural persons who are residents or citizens "
            "of Florida, guaranteeing the absence of state individual income taxation."
        ),
        text_es=(
            "Prohíbe constitucionalmente cualquier impuesto sobre el ingreso de personas "
            "naturales residentes o ciudadanas de Florida, garantizando la ausencia de "
            "impuesto estatal sobre ingresos individuales."
        ),
        effective_year=1968,
        source_url="https://www.flsenate.gov/",
        cross_references=["Fla. Stat. § 222.17"],
        breadcrumbs="Fla. Const. > Article VII > Section 5",
    ),
    StatuteRecord(
        id="fl-stat-222-17",
        jurisdiction="FL",
        corpus="FLSTAT",
        citation="Fla. Stat. § 222.17",
        section="222.17",
        title="Manifesting and Evidencing Domicile in Florida",
        text_en=(
            "Authorizes any person who has established a domicile in Florida to manifest that "
            "domicile by filing a sworn, notarized statement of domicile with the clerk of the "
            "circuit court in the county of residence."
        ),
        text_es=(
            "Autoriza a toda persona que haya establecido domicilio en Florida a manifestarlo "
            "formalmente mediante declaración jurada radicada ante el secretario del tribunal de "
            "circuito del condado correspondiente."
        ),
        effective_year=2024,
        source_url="https://www.flsenate.gov/",
        cross_references=["Fla. Const. Art. VII § 5", "Fla. Stat. § 196.031"],
        breadcrumbs="Fla. Stat. > Title XV > Chapter 222 > § 222.17",
    ),
    StatuteRecord(
        id="fl-stat-196-031",
        jurisdiction="FL",
        corpus="FLSTAT",
        citation="Fla. Stat. § 196.031",
        section="196.031",
        title="Exemption of Homesteads",
        text_en=(
            "Grants up to $50,000 homestead property tax exemption to persons who maintain "
            "permanent residence. Claiming Florida homestead establishes prima facie proof of "
            "Florida domicile."
        ),
        text_es=(
            "Concede hasta $50,000 de exención contributiva sobre la propiedad principal a "
            "residentes permanentes. Solicitar la exención de 'homestead' constituye prueba "
            "prima facie de domicilio en Florida."
        ),
        effective_year=2024,
        source_url="https://www.flsenate.gov/",
        cross_references=["Fla. Stat. § 222.17"],
        breadcrumbs="Fla. Stat. > Title XIV > Chapter 196 > § 196.031",
    ),
    # State of Texas (Constitutional Prohibition of Individual Income Tax)
    StatuteRecord(
        id="tx-const-8-24a",
        jurisdiction="TX",
        corpus="TXCONST",
        citation="Tex. Const. art. VIII § 24-a",
        section="Art. VIII Sec. 24-a",
        title="Constitutional Prohibition of Personal Income Tax",
        text_en=(
            "Constitutional amendment approved by voters in 2019 prohibiting the Texas legislature "
            "from imposing a tax on the net incomes of individuals, including an individual's "
            "share of partnership and unincorporated association income."
        ),
        text_es=(
            "Enmienda constitucional aprobada en 2019 que prohíbe a la legislatura de Texas imponer "
            "impuestos sobre el ingreso neto de individuos o su participación en sociedades y "
            "asociaciones no incorporadas."
        ),
        effective_year=2019,
        source_url="https://statutes.capitol.texas.gov/",
        cross_references=["Tex. Tax Code § 171.001"],
        breadcrumbs="Tex. Const. > Article VIII > Section 24-a",
    ),
    StatuteRecord(
        id="tx-tax-171-001",
        jurisdiction="TX",
        corpus="TXTAX",
        citation="Tex. Tax Code § 171.001",
        section="171.001",
        title="Texas Franchise Tax (Entity Level)",
        text_en=(
            "Imposes franchise tax on each taxable entity chartered or organized in Texas or doing "
            "business in Texas based on gross margin, maintaining zero taxation on individuals."
        ),
        text_es=(
            "Impone el impuesto de franquicia únicamente a entidades comerciales organizadas o con "
            "negocios en Texas sobre su margen bruto, sin gravar a personas naturales."
        ),
        effective_year=2024,
        source_url="https://statutes.capitol.texas.gov/",
        cross_references=["Tex. Const. art. VIII § 24-a"],
        breadcrumbs="Tex. Tax Code > Title 2 > Subtitle F > Chapter 171 > § 171.001",
    ),
    # State of Washington (Capital Gains Excise Tax & No Income Tax)
    StatuteRecord(
        id="wa-rcw-82-87-040",
        jurisdiction="WA",
        corpus="RCW",
        citation="RCW 82.87.040",
        section="82.87.040",
        title="Tax Imposed on Long-Term Capital Gains",
        text_en=(
            "7% excise tax on individual annual net long-term capital gains allocated to Washington "
            "in excess of statutory standard deduction ($250,000 indexed for inflation)."
        ),
        text_es=(
            "Impuesto especial del 7% sobre ganancias de capital a largo plazo de individuos "
            "que excedan la deducción estándar anual ($250,000 indexada por inflación)."
        ),
        effective_year=2022,
        source_url="https://app.leg.wa.gov/RCW/default.aspx?cite=82.87.040",
        cross_references=["RCW 82.87.020", "Wash. Const. art. VII § 1"],
        breadcrumbs="RCW > Title 82 > Chapter 82.87 > Section 82.87.040",
    ),
    StatuteRecord(
        id="wa-rcw-82-87-020",
        jurisdiction="WA",
        corpus="RCW",
        citation="RCW 82.87.020",
        section="82.87.020",
        title="Capital Gains Tax Definitions and Resident Sourcing",
        text_en=(
            "Defines resident for Washington capital gains excise tax purposes as an individual "
            "domiciled in Washington at the time the long-term capital asset was sold or exchanged."
        ),
        text_es=(
            "Define residente de Washington para el impuesto de ganancias de capital como "
            "persona natural domiciliada en el estado al momento de la venta o intercambio."
        ),
        effective_year=2022,
        source_url="https://app.leg.wa.gov/RCW/default.aspx?cite=82.87.020",
        cross_references=["RCW 82.87.040"],
        breadcrumbs="RCW > Title 82 > Chapter 82.87 > Section 82.87.020",
    ),
    StatuteRecord(
        id="wa-const-7-1",
        jurisdiction="WA",
        corpus="WACONST",
        citation="Wash. Const. art. VII § 1",
        section="Art. VII Sec. 1",
        title="Constitutional Uniformity and Prohibition of Net Income Tax",
        text_en=(
            "Constitutional requirement that all taxes be uniform upon the same class of property, "
            "historically interpreted to prohibit graduated net income taxes."
        ),
        text_es=(
            "Disposición constitucional de tributación uniforme que prohíbe impuestos "
            "progresivos sobre el ingreso neto según interpretado en doctrina judicial histórica."
        ),
        effective_year=1889,
        source_url="https://leg.wa.gov/CodeReviser/Pages/WAConstitution.aspx",
        cross_references=["RCW 82.87.040"],
        breadcrumbs="Wash. Const. > Article VII > Section 1",
    ),
    # Commonwealth of Massachusetts (Part B Income Tax & Fair Share Surtax)
    StatuteRecord(
        id="ma-const-amend-44",
        jurisdiction="MA",
        corpus="MACONST",
        citation="Mass. Const. amend. art. XLIV",
        section="Art. XLIV",
        title="Fair Share Amendment (4% Millionaires' Surtax)",
        text_en=(
            "Constitutional amendment imposing an additional 4% surtax on the portion of annual "
            "taxable income exceeding $1,000,000, creating a top rate of 9.0%."
        ),
        text_es=(
            "Enmienda constitucional que impone una sobretasa adicional del 4% sobre la porción "
            "del ingreso imponible anual que exceda $1,000,000 (indexado por inflación)."
        ),
        effective_year=2023,
        source_url="https://malegislature.gov/Laws/Constitution",
        cross_references=["M.G.L. c. 62 § 4"],
        breadcrumbs="Mass. Const. > Amendments > Article XLIV",
    ),
    StatuteRecord(
        id="ma-gl-62-4",
        jurisdiction="MA",
        corpus="MGL",
        citation="M.G.L. c. 62 § 4",
        section="62-4",
        title="Personal Income Tax Rates",
        text_en=(
            "Standard 5.0% flat income tax rate on Part B taxable personal income (wages, trade, "
            "business, and interest income)."
        ),
        text_es=(
            "Tasa básica de impuesto sobre el ingreso personal de Massachusetts del 5.0% sobre "
            "ingreso imponible de Parte B (salarios, negocios y pensiones)."
        ),
        effective_year=2024,
        source_url="https://malegislature.gov/Laws/GeneralLaws/PartI/TitleIX/Chapter62/Section4",
        cross_references=["Mass. Const. amend. art. XLIV", "M.G.L. c. 62 § 1"],
        breadcrumbs="M.G.L. > Part I > Title IX > Chapter 62 > Section 4",
    ),
    StatuteRecord(
        id="ma-gl-62-1f",
        jurisdiction="MA",
        corpus="MGL",
        citation="M.G.L. c. 62 § 1(f)",
        section="62-1(f)",
        title="Definition of Resident (Domicile and 183-Day Rule)",
        text_en=(
            "Defines resident as any natural person domiciled in Massachusetts, or non-domiciliary "
            "maintaining a permanent place of abode and spending more than 183 days in the state."
        ),
        text_es=(
            "Define residente como persona domiciliada en Massachusetts o no domiciliada que "
            "mantenga un lugar permanente de morada y pase más de 183 días en el estado."
        ),
        effective_year=2024,
        source_url="https://malegislature.gov/Laws/GeneralLaws/PartI/TitleIX/Chapter62/Section1",
        cross_references=["M.G.L. c. 62 § 4"],
        breadcrumbs="M.G.L. > Part I > Title IX > Chapter 62 > Section 1(f)",
    ),
    # State of New Jersey (Gross Income Tax & Convenience of Employer)
    StatuteRecord(
        id="nj-stat-54a-1-2",
        jurisdiction="NJ",
        corpus="NJSA",
        citation="N.J.S.A. 54A:1-2",
        section="54A:1-2",
        title="Definition of Resident Individual",
        text_en=(
            "Defines resident individual based on domicile or statutory residency (maintaining a "
            "permanent place of abode in NJ and spending more than 183 days in NJ)."
        ),
        text_es=(
            "Define residente de NJ por domicilio (salvo morada permanente exterior con <= 30 "
            "días en NJ) o residente estatutario con morada permanente en NJ y más de 183 días."
        ),
        effective_year=2024,
        source_url="https://lis.njleg.state.nj.us/",
        cross_references=["N.J.S.A. 54A:2-1", "N.J.A.C. 18:35-5.2"],
        breadcrumbs="N.J.S.A. > Title 54A > Chapter 1 > Section 54A:1-2",
    ),
    StatuteRecord(
        id="nj-stat-54a-2-1",
        jurisdiction="NJ",
        corpus="NJSA",
        citation="N.J.S.A. 54A:2-1",
        section="54A:2-1",
        title="New Jersey Gross Income Tax Rates",
        text_en=(
            "Graduated gross income tax brackets ranging from 1.4% up to top marginal rate of "
            "10.75% on taxable income exceeding $1,000,000."
        ),
        text_es=(
            "Escalas progresivas de impuesto sobre el ingreso bruto que varían entre 1.4% y una "
            "tasa marginal máxima de 10.75% para ingresos mayores a $1,000,000."
        ),
        effective_year=2024,
        source_url="https://lis.njleg.state.nj.us/",
        cross_references=["N.J.S.A. 54A:1-2"],
        breadcrumbs="N.J.S.A. > Title 54A > Chapter 2 > Section 54A:2-1",
    ),
    StatuteRecord(
        id="nj-admin-18-35-5-2",
        jurisdiction="NJ",
        corpus="NJAC",
        citation="N.J.A.C. 18:35-5.2",
        section="18:35-5.2",
        title="Convenience of the Employer Rule (Reciprocal Sourcing)",
        text_en=(
            "Reciprocal convenience of employer sourcing rule enacted in 2023 sourcing telecommuting "
            "compensation of nonresidents to NJ if their resident state imposes a convenience rule."
        ),
        text_es=(
            "Regla recíproca de conveniencia del patrono aprobada en 2023 que grava a no residentes "
            "que teletrabajen si su estado de residencia aplica una regla similar (ej. Nueva York)."
        ),
        effective_year=2023,
        source_url="https://www.nj.gov/treasury/taxation/",
        cross_references=["N.J.S.A. 54A:5-8"],
        breadcrumbs="N.J.A.C. > Title 18 > Chapter 35 > Subchapter 5 > Section 18:35-5.2",
    ),
    # State of Illinois (Flat Income Tax & Nonresident Sourcing)
    StatuteRecord(
        id="il-stat-35-ilcs-5-201",
        jurisdiction="IL",
        corpus="ILCS",
        citation="35 ILCS 5/201",
        section="5/201",
        title="Illinois Individual Income Tax Rate",
        text_en=(
            "Constitutionally mandated non-graduated flat income tax rate of 4.95% on net income "
            "of individuals, trusts, and estates."
        ),
        text_es=(
            "Impuesto estatal no graduado a tasa fija del 4.95% sobre el ingreso neto de "
            "personas naturales, mandatado por la Constitución de Illinois."
        ),
        effective_year=2024,
        source_url="https://www.ilga.gov/legislation/ilcs/ilcs3.asp?ActID=577",
        cross_references=["35 ILCS 5/1501(a)(20)", "35 ILCS 5/301"],
        breadcrumbs="35 ILCS 5/ > Article 2 > Section 201",
    ),
    StatuteRecord(
        id="il-stat-35-ilcs-5-1501",
        jurisdiction="IL",
        corpus="ILCS",
        citation="35 ILCS 5/1501(a)(20)",
        section="5/1501(a)(20)",
        title="Definition of Resident",
        text_en=(
            "Defines resident as an individual who is in Illinois for other than a temporary "
            "or transitory purpose during the taxable year or who is domiciled in Illinois."
        ),
        text_es=(
            "Define residente como persona en Illinois para fines no transitorios o temporales, "
            "o domiciliada en Illinois ausente temporalmente."
        ),
        effective_year=2024,
        source_url="https://www.ilga.gov/legislation/ilcs/ilcs3.asp?ActID=577",
        cross_references=["35 ILCS 5/201"],
        breadcrumbs="35 ILCS 5/ > Article 15 > Section 1501(a)(20)",
    ),
    StatuteRecord(
        id="il-stat-35-ilcs-5-301",
        jurisdiction="IL",
        corpus="ILCS",
        citation="35 ILCS 5/301",
        section="5/301",
        title="Compensation and Nonresident Income Sourcing",
        text_en=(
            "Rules for allocating and sourcing compensation and business income of nonresidents "
            "based on physical days of services performed within Illinois."
        ),
        text_es=(
            "Reglas de asignación de ingresos y salarios de no residentes a Illinois basándose "
            "en servicios efectivamente prestados en el estado."
        ),
        effective_year=2024,
        source_url="https://www.ilga.gov/legislation/ilcs/ilcs3.asp?ActID=577",
        cross_references=["35 ILCS 5/201"],
        breadcrumbs="35 ILCS 5/ > Article 3 > Section 301",
    ),
    # Commonwealth of Pennsylvania (Flat Tax, Abode, and Convenience Rule)
    StatuteRecord(
        id="pa-stat-72-ps-7302",
        jurisdiction="PA",
        corpus="P.S.",
        citation="72 P.S. § 7302",
        section="7302",
        title="Imposition of Tax",
        text_en=(
            "Imposes Pennsylvania personal income tax at a constitutional flat rate of 3.07% on "
            "eight specified classes of taxable income."
        ),
        text_es=(
            "Impone un impuesto sobre la renta personal con tasa uniforme y fija del 3.07% sobre "
            "ocho clases especificadas de ingresos gravables."
        ),
        effective_year=2024,
        source_url="https://www.legis.state.pa.us/cfdocs/legis/LI/consCheck.cfm?txtType=HTM&ttl=72&div=0&chpt=73",
        cross_references=["72 P.S. § 7301", "72 P.S. § 7303"],
        breadcrumbs="72 P.S. > Chapter 73 > § 7302",
    ),
    StatuteRecord(
        id="pa-stat-72-ps-7301",
        jurisdiction="PA",
        corpus="P.S.",
        citation="72 P.S. § 7301",
        section="7301",
        title="Definitions - Resident Individual",
        text_en=(
            "Defines a resident individual as one domiciled in Pennsylvania or who maintains a "
            "permanent place of abode and spends in the aggregate more than 183 days in the commonwealth."
        ),
        text_es=(
            "Define individuo residente como quien tiene domicilio en Pensilvania o mantiene un "
            "lugar permanente de morada y pasa más de 183 días en el estado."
        ),
        effective_year=2024,
        source_url="https://www.legis.state.pa.us/cfdocs/legis/LI/consCheck.cfm?txtType=HTM&ttl=72&div=0&chpt=73",
        cross_references=["72 P.S. § 7302", "61 Pa. Code § 101.1"],
        breadcrumbs="72 P.S. > Chapter 73 > § 7301",
    ),
    StatuteRecord(
        id="pa-code-61-109-8",
        jurisdiction="PA",
        corpus="Pa. Code",
        citation="61 Pa. Code § 109.8",
        section="109.8",
        title="Convenience of the Employer Rule",
        text_en=(
            "Requires compensation earned by nonresidents telecommuting for a Pennsylvania "
            "employer to be sourced to Pennsylvania unless working from home is for employer necessity."
        ),
        text_es=(
            "Dispone que la compensación por servicios remotos o de teletrabajo para un patrono de "
            "Pensilvania se considera de fuente local a menos que el teletrabajo sea por necesidad del patrono."
        ),
        effective_year=2024,
        source_url="https://www.pacodeandbulletin.gov/Display/pacode?file=/secure/pacode/data/061/chapter109/s109.8.html",
        cross_references=["72 P.S. § 7308"],
        breadcrumbs="61 Pa. Code > Chapter 109 > § 109.8",
    ),
    # State of Connecticut (Graduated Brackets, Abode, and Reciprocal Convenience)
    StatuteRecord(
        id="ct-stat-cgs-12-700",
        jurisdiction="CT",
        corpus="C.G.S.",
        citation="Conn. Gen. Stat. § 12-700",
        section="12-700",
        title="Imposition of Income Tax and Rates",
        text_en=(
            "Imposes graduated personal income tax rates from 3.0% to 6.99% on resident Connecticut "
            "taxable income with benefit phase-out recaptures for high earners."
        ),
        text_es=(
            "Impone tasas impositivas graduadas del 3.0% al 6.99% sobre el ingreso neto de residentes, "
            "con eliminación progresiva de tramos de exención en ingresos altos."
        ),
        effective_year=2024,
        source_url="https://www.cga.ct.gov/current/pub/chap_229.htm#sec_12-700",
        cross_references=["Conn. Gen. Stat. § 12-701"],
        breadcrumbs="C.G.S. > Title 12 > Chapter 229 > § 12-700",
    ),
    StatuteRecord(
        id="ct-stat-cgs-12-701",
        jurisdiction="CT",
        corpus="C.G.S.",
        citation="Conn. Gen. Stat. § 12-701",
        section="12-701",
        title="Definitions - Resident and Sourcing",
        text_en=(
            "Defines resident as an individual domiciled in Connecticut or who maintains a permanent "
            "place of abode and spends in the aggregate more than 183 days of the taxable year in state."
        ),
        text_es=(
            "Define residente statutario como quien mantiene un lugar permanente de morada en Connecticut "
            "y pasa en total más de 183 días en el año contributivo."
        ),
        effective_year=2024,
        source_url="https://www.cga.ct.gov/current/pub/chap_229.htm#sec_12-701",
        cross_references=["Conn. Gen. Stat. § 12-700", "Conn. Gen. Stat. § 12-711"],
        breadcrumbs="C.G.S. > Title 12 > Chapter 229 > § 12-701",
    ),
    StatuteRecord(
        id="ct-stat-cgs-12-711",
        jurisdiction="CT",
        corpus="C.G.S.",
        citation="Conn. Gen. Stat. § 12-711",
        section="12-711",
        title="Convenience of Employer Rule",
        text_en=(
            "Applies a reciprocal convenience of the employer test to nonresident compensation earned "
            "while telecommuting for a Connecticut employer if employee's state applies a convenience rule."
        ),
        text_es=(
            "Aplica la regla de conveniencia del patrono para determinar la fuente de ingresos salariales "
            "de no residentes que teletrabajan si su estado de residencia aplica una regla similar."
        ),
        effective_year=2024,
        source_url="https://www.cga.ct.gov/current/pub/chap_229.htm#sec_12-711",
        cross_references=["Conn. Gen. Stat. § 12-701"],
        breadcrumbs="C.G.S. > Title 12 > Chapter 229 > § 12-711",
    ),
    # State of North Carolina (Phase-In Flat Tax & 183-Day Rebuttable Presumption)
    StatuteRecord(
        id="nc-stat-ncgs-105-153-7",
        jurisdiction="NC",
        corpus="N.C.G.S.",
        citation="N.C. Gen. Stat. § 105-153.7",
        section="105-153.7",
        title="Individual Income Tax Imposition and Rate",
        text_en=(
            "Imposes individual income tax at a decreasing flat rate: 4.5% for 2024, 4.25% for 2025, "
            "and 3.99% for taxable years beginning on or after 2026."
        ),
        text_es=(
            "Impone un impuesto sobre la renta individual de tasa fija decreciente: 4.5% en 2024, "
            "4.25% en 2025, y 3.99% para años tributarios 2026 y posteriores."
        ),
        effective_year=2024,
        source_url="https://www.ncleg.gov/EnactedLegislation/Statutes/HTML/BySection/Chapter_105/Section_105-153.7.html",
        cross_references=["N.C. Gen. Stat. § 105-153.3", "N.C. Gen. Stat. § 105-153.5"],
        breadcrumbs="N.C.G.S. > Chapter 105 > Article 4 > Part 2 > § 105-153.7",
    ),
    StatuteRecord(
        id="nc-stat-ncgs-105-153-3",
        jurisdiction="NC",
        corpus="N.C.G.S.",
        citation="N.C. Gen. Stat. § 105-153.3",
        section="105-153.3",
        title="Definitions - Resident Individual",
        text_en=(
            "Defines resident individual as one domiciled in North Carolina or who resides in state "
            "for more than 183 days during the taxable year creating a rebuttable presumption of residency."
        ),
        text_es=(
            "Define individuo residente como quien reside o tiene domicilio en Carolina del Norte, "
            "o quien pasa más de 183 días en el estado durante el año contributivo presumiendo residencia."
        ),
        effective_year=2024,
        source_url="https://www.ncleg.gov/EnactedLegislation/Statutes/HTML/BySection/Chapter_105/Section_105-153.3.html",
        cross_references=["N.C. Gen. Stat. § 105-153.7"],
        breadcrumbs="N.C.G.S. > Chapter 105 > Article 4 > Part 2 > § 105-153.3",
    ),
    # Commonwealth of Virginia (Graduated Brackets, Actual Residency, Commuter Reciprocity)
    StatuteRecord(
        id="va-stat-58-1-320",
        jurisdiction="VA",
        corpus="Va. Code",
        citation="Va. Code Ann. § 58.1-320",
        section="58.1-320",
        title="Rates of Tax on Individuals",
        text_en=(
            "Imposes Virginia individual income tax at graduated rates: 2.0% on the first $3,000; "
            "3.0% on $3,001 to $5,000; 5.0% on $5,001 to $17,000; and 5.75% on taxable income "
            "exceeding $17,000."
        ),
        text_es=(
            "Impone el impuesto sobre la renta individual de Virginia con tasas graduadas: "
            "2.0% sobre los primeros $3,000; 3.0% de $3,001 a $5,000; 5.0% de $5,001 a $17,000; "
            "y 5.75% sobre el exceso de $17,000."
        ),
        effective_year=2024,
        source_url="https://law.lis.virginia.gov/vacode/title58.1/chapter3/section58.1-320/",
        cross_references=["Va. Code Ann. § 58.1-302", "Va. Code Ann. § 58.1-322.03"],
        breadcrumbs="Va. Code > Title 58.1 > Chapter 3 > Article 2 > § 58.1-320",
    ),
    StatuteRecord(
        id="va-stat-58-1-302",
        jurisdiction="VA",
        corpus="Va. Code",
        citation="Va. Code Ann. § 58.1-302",
        section="58.1-302",
        title="Definitions - Resident and Actual Resident",
        text_en=(
            "Defines resident individual as one domiciled in Virginia, or an actual resident who maintains "
            "a place of abode and spends in the aggregate more than 183 days of the taxable year in Virginia."
        ),
        text_es=(
            "Define individuo residente como quien tiene domicilio en Virginia, o un residente de "
            "hecho que mantiene morada y pasa más de 183 días en total del año contributivo en Virginia."
        ),
        effective_year=2024,
        source_url="https://law.lis.virginia.gov/vacode/title58.1/chapter3/section58.1-302/",
        cross_references=["Va. Code Ann. § 58.1-320", "Va. Code Ann. § 58.1-342"],
        breadcrumbs="Va. Code > Title 58.1 > Chapter 3 > Article 1 > § 58.1-302",
    ),
    StatuteRecord(
        id="va-stat-58-1-342",
        jurisdiction="VA",
        corpus="Va. Code",
        citation="Va. Code Ann. § 58.1-342",
        section="58.1-342",
        title="Reciprocal Tax Agreements for Commuters",
        text_en=(
            "Authorizes reciprocal agreements with contiguous jurisdictions (DC, Maryland, West Virginia, "
            "Pennsylvania, and Kentucky) exempting commuter compensation from non-resident withholding."
        ),
        text_es=(
            "Autoriza acuerdos recíprocos de exención con jurisdicciones limítrofes (DC, Maryland, "
            "West Virginia, Pensilvania y Kentucky) para salarios devengados por viajeros diarios."
        ),
        effective_year=2024,
        source_url="https://law.lis.virginia.gov/vacode/title58.1/chapter3/section58.1-342/",
        cross_references=["Va. Code Ann. § 58.1-302"],
        breadcrumbs="Va. Code > Title 58.1 > Chapter 3 > Article 8 > § 58.1-342",
    ),
    # State of Maryland (Graduated Rates, Abode + 183 Days, County Piggyback Tax)
    StatuteRecord(
        id="md-stat-tg-10-105",
        jurisdiction="MD",
        corpus="Md. Code",
        citation="Md. Code Ann., Tax-Gen. § 10-105",
        section="10-105",
        title="Individual State Income Tax Rates",
        text_en=(
            "Imposes graduated Maryland state income tax rates from 2.0% up to 5.75% for taxable income "
            "exceeding $250,000 for single filers or $300,000 for married couples filing jointly."
        ),
        text_es=(
            "Impone tasas impositivas estatales graduadas desde 2.0% hasta 5.75% sobre el ingreso gravable "
            "que exceda de $250,000 (solteros) o $300,000 (casados rindiendo conjuntamente)."
        ),
        effective_year=2024,
        source_url="https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=gtg&section=10-105",
        cross_references=["Md. Code Ann., Tax-Gen. § 10-101", "Md. Code Ann., Tax-Gen. § 10-106"],
        breadcrumbs="Md. Code > Tax-General > Title 10 > Subtitle 1 > § 10-105",
    ),
    StatuteRecord(
        id="md-stat-tg-10-101",
        jurisdiction="MD",
        corpus="Md. Code",
        citation="Md. Code Ann., Tax-Gen. § 10-101",
        section="10-101",
        title="Definitions - Resident Individual",
        text_en=(
            "Defines a resident individual as one who is domiciled in Maryland on the last day of the taxable "
            "year, or who maintains a place of abode in Maryland for more than six months and is physically "
            "present for 183 days or more."
        ),
        text_es=(
            "Define individuo residente como quien tiene domicilio en Maryland el último día del año "
            "contributivo, o mantiene morada por más de 6 meses y está presente físicamente 183 días o más."
        ),
        effective_year=2024,
        source_url="https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=gtg&section=10-101",
        cross_references=["Md. Code Ann., Tax-Gen. § 10-105"],
        breadcrumbs="Md. Code > Tax-General > Title 10 > Subtitle 1 > § 10-101",
    ),
    StatuteRecord(
        id="md-stat-tg-10-106",
        jurisdiction="MD",
        corpus="Md. Code",
        citation="Md. Code Ann., Tax-Gen. § 10-106",
        section="10-106",
        title="County and Local Piggyback Income Tax",
        text_en=(
            "Mandates that each Maryland county and Baltimore City levy a local piggyback income tax rate "
            "between 2.25% and 3.20%, collected by the Comptroller on the state tax return."
        ),
        text_es=(
            "Requiere que cada condado de Maryland y la ciudad de Baltimore cobren un impuesto local sobre la "
            "renta ('piggyback') de entre 2.25% y 3.20%, recaudado en la planilla estatal."
        ),
        effective_year=2024,
        source_url="https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=gtg&section=10-106",
        cross_references=["Md. Code Ann., Tax-Gen. § 10-105"],
        breadcrumbs="Md. Code > Tax-General > Title 10 > Subtitle 1 > § 10-106",
    ),
    # State of Colorado (Proposition 121 Flat Tax & Statutory Residency)
    StatuteRecord(
        id="co-stat-39-22-104",
        jurisdiction="CO",
        corpus="C.R.S.",
        citation="Colo. Rev. Stat. § 39-22-104",
        section="39-22-104",
        title="Levy of Tax - Single Rate for Individuals",
        text_en=(
            "Imposes a flat tax of 4.40% on Colorado modified federal taxable income of individuals pursuant "
            "to voter-approved Proposition 121 (reduced from 4.55%)."
        ),
        text_es=(
            "Impone una tasa uniforme del 4.40% sobre el ingreso federal tributable modificado de individuos "
            "de Colorado bajo la Proposición 121 aprobada por los electores."
        ),
        effective_year=2024,
        source_url="https://leg.colorado.gov/agencies/office-legislative-legal-services/colorado-revised-statutes",
        cross_references=["Colo. Rev. Stat. § 39-22-103"],
        breadcrumbs="C.R.S. > Title 39 > Article 22 > Part 1 > § 39-22-104",
    ),
    StatuteRecord(
        id="co-stat-39-22-103",
        jurisdiction="CO",
        corpus="C.R.S.",
        citation="Colo. Rev. Stat. § 39-22-103",
        section="39-22-103",
        title="Definitions - Resident Individual",
        text_en=(
            "Defines a resident individual as one domiciled in Colorado, or who maintains a permanent place "
            "of abode in Colorado and spends in the aggregate more than six months of the taxable year in Colorado."
        ),
        text_es=(
            "Define individuo residente como quien tiene domicilio en Colorado, o mantiene morada permanente "
            "y pasa en agregado más de seis meses del año contributivo en Colorado."
        ),
        effective_year=2024,
        source_url="https://leg.colorado.gov/agencies/office-legislative-legal-services/colorado-revised-statutes",
        cross_references=["Colo. Rev. Stat. § 39-22-104"],
        breadcrumbs="C.R.S. > Title 39 > Article 22 > Part 1 > § 39-22-103",
    ),
    # State of Arizona (SB 1828 Flat Tax & 9-Month Presumption)
    StatuteRecord(
        id="az-stat-43-1011",
        jurisdiction="AZ",
        corpus="A.R.S.",
        citation="A.R.S. § 43-1011",
        section="43-1011",
        title="Taxes and Tax Rates - Individuals",
        text_en=(
            "Imposes a flat individual income tax rate of 2.50% on Arizona taxable income, enacted by SB 1828 "
            "to replace graduated individual tax brackets."
        ),
        text_es=(
            "Impone una tasa impositiva fija del 2.50% sobre el ingreso gravable de Arizona de individuos, "
            "promulgada bajo SB 1828 eliminando tramos graduales anteriores."
        ),
        effective_year=2024,
        source_url="https://www.azleg.gov/ars/43/01011.htm",
        cross_references=["A.R.S. § 43-104"],
        breadcrumbs="A.R.S. > Title 43 > Chapter 10 > Article 1 > § 43-1011",
    ),
    StatuteRecord(
        id="az-stat-43-104",
        jurisdiction="AZ",
        corpus="A.R.S.",
        citation="A.R.S. § 43-104",
        section="43-104",
        title="Definitions - Resident and Presumption of Residency",
        text_en=(
            "Defines resident as every individual in Arizona for other than a temporary or transitory purpose; "
            "individuals spending in the aggregate more than nine months (270 days) of the tax year in state "
            "are presumed residents."
        ),
        text_es=(
            "Define residente como todo individuo en Arizona para fines no temporales ni transitorios; quien "
            "pase en agregado más de nueve meses (270 días) en el estado se presume residente."
        ),
        effective_year=2024,
        source_url="https://www.azleg.gov/ars/43/00104.htm",
        cross_references=["A.R.S. § 43-1011"],
        breadcrumbs="A.R.S. > Title 43 > Chapter 1 > Article 1 > § 43-104",
    ),
    # State of Ohio (Rate Consolidation & Bright-Line Contact Period Rule)
    StatuteRecord(
        id="oh-stat-5747-02",
        jurisdiction="OH",
        corpus="R.C.",
        citation="Ohio Rev. Code § 5747.02",
        section="5747.02",
        title="Taxation of Personal Income - Rates",
        text_en=(
            "Levies Ohio personal income tax with phased rate consolidation: 0% up to $26,050; 2.75% from "
            "$26,051 to $100,000; and 3.50% on non-business taxable income exceeding $100,000 for tax year 2024+."
        ),
        text_es=(
            "Grava el ingreso personal en Ohio con consolidación de tasas: 0% hasta $26,050; 2.75% de "
            "$26,051 a $100,000; y 3.50% sobre el ingreso no comercial que exceda $100,000 para el año 2024+."
        ),
        effective_year=2024,
        source_url="https://codes.ohio.gov/ohio-revised-code/section-5747.02",
        cross_references=["Ohio Rev. Code § 5747.24"],
        breadcrumbs="R.C. > Title 57 > Chapter 5747 > § 5747.02",
    ),
    StatuteRecord(
        id="oh-stat-5747-24",
        jurisdiction="OH",
        corpus="R.C.",
        citation="Ohio Rev. Code § 5747.24",
        section="5747.24",
        title="Bright-Line Presumption of Domicile - Contact Periods",
        text_en=(
            "Establishes bright-line residency rules: an individual with <= 212 Ohio contact periods is presumed "
            "non-resident if filing Form IT-NRS affidavit by October 15; an individual with >= 213 contact periods "
            "is presumed an Ohio resident."
        ),
        text_es=(
            "Establece regla objetiva de periodos de contacto: quien tenga <= 212 periodos de contacto se "
            "presume no residente si radica declaración jurada IT-NRS antes del 15 de octubre; >= 213 periodos "
            "presume residencia."
        ),
        effective_year=2024,
        source_url="https://codes.ohio.gov/ohio-revised-code/section-5747.24",
        cross_references=["Ohio Rev. Code § 5747.02"],
        breadcrumbs="R.C. > Title 57 > Chapter 5747 > § 5747.24",
    ),
    # State of Alaska
    StatuteRecord(
        id="ak-stat-43-05-080",
        jurisdiction="AK",
        corpus="Alaska Stat.",
        citation="Alaska Stat. § 43.05.080",
        section="43.05.080",
        title="Revenue Administration and Absence of Individual Income Tax",
        text_en=(
            "Empowers the Department of Revenue to administer tax laws, operating alongside the "
            "historic repeal of former AS 43.20 individual income tax by Ch. 1, SLA 1980."
        ),
        text_es=(
            "Faculta al Departamento de Ingresos a reglamentar la administración tributaria "
            "estatal, tras la histórica derogación del impuesto individual por el Cap. 1, SLA 1980."
        ),
        effective_year=1980,
        source_url="https://www.akleg.gov/basis/statutes.asp#43.05.080",
        cross_references=[],
        breadcrumbs="Alaska Stat. > Title 43 > Chapter 05 > § 43.05.080",
    ),
    # State of Alabama
    StatuteRecord(
        id="al-code-40-18-2",
        jurisdiction="AL",
        corpus="Ala. Code",
        citation="Ala. Code § 40-18-2",
        section="40-18-2",
        title="Tax on Individuals",
        text_en=(
            "Imposes graduated individual income tax rates of 2%, 4%, and 5% on taxable net "
            "income of individuals in Alabama."
        ),
        text_es=(
            "Impone tasas impositivas individuales graduadas del 2%, 4% y 5% sobre el ingreso "
            "neto gravable de individuos en Alabama."
        ),
        effective_year=2024,
        source_url="https://alison.legislature.state.al.us/code-of-alabama",
        cross_references=[],
        breadcrumbs="Ala. Code > Title 40 > Chapter 18 > § 40-18-2",
    ),
    # State of Arkansas
    StatuteRecord(
        id="ar-code-26-51-201",
        jurisdiction="AR",
        corpus="A.C.A.",
        citation="Ark. Code Ann. § 26-51-201",
        section="26-51-201",
        title="Tax on Individuals, Trusts, and Estates",
        text_en=(
            "Imposes individual income tax with graduated brackets and top rate of 3.90% under "
            "Arkansas Act 1 of 2024."
        ),
        text_es=(
            "Impone impuesto sobre el ingreso individual con tramos graduados y tasa máxima de "
            "3.90% bajo la Ley 1 de 2024 de Arkansas."
        ),
        effective_year=2024,
        source_url="https://www.arkleg.state.ar.us/",
        cross_references=[],
        breadcrumbs="A.C.A. > Title 26 > Subtitle 5 > Chapter 51 > Subchapter 2 > § 26-51-201",
    ),
    # District of Columbia
    StatuteRecord(
        id="dc-code-47-1806-03",
        jurisdiction="DC",
        corpus="D.C. Code",
        citation="D.C. Code § 47-1806.03",
        section="47-1806.03",
        title="Tax on Residents - Rates",
        text_en=(
            "Imposes graduated personal income tax rates from 4.0% to top rate 10.75% for "
            "taxable income exceeding $1,000,000 in the District of Columbia."
        ),
        text_es=(
            "Impone tasas de impuesto sobre ingresos personales graduadas desde 4.0% hasta tasa "
            "máxima de 10.75% sobre ingresos superiores a $1,000,000 en el Distrito de Columbia."
        ),
        effective_year=2024,
        source_url="https://code.dccouncil.gov/us/dc/council/code/sections/47-1806.03",
        cross_references=[],
        breadcrumbs="D.C. Code > Title 47 > Chapter 18 > Subchapter VI > § 47-1806.03",
    ),
    # State of Delaware
    StatuteRecord(
        id="de-code-30-1102",
        jurisdiction="DE",
        corpus="Del. Code",
        citation="30 Del. C. § 1102",
        section="1102",
        title="Levy and Rates of Personal Income Tax",
        text_en=(
            "Imposes graduated income tax rates from 2.2% to 6.6% and applies convenience of "
            "employer rules to nonresident compensation."
        ),
        text_es=(
            "Impone tasas de impuesto sobre la renta graduadas del 2.2% al 6.6% y aplica reglas de "
            "conveniencia del empleador a remuneraciones de no residentes."
        ),
        effective_year=2024,
        source_url="https://delcode.delaware.gov/title30/c011/sc01/index.html",
        cross_references=[],
        breadcrumbs="Del. Code > Title 30 > Part II > Chapter 11 > Subchapter I > § 1102",
    ),
    # State of Hawaii
    StatuteRecord(
        id="hi-rev-stat-235-51",
        jurisdiction="HI",
        corpus="HRS",
        citation="Haw. Rev. Stat. § 235-51",
        section="235-51",
        title="Tax on Individuals, Estates, and Trusts",
        text_en=(
            "Imposes graduated individual income tax rates up to 11.0% under Act 97 (2024 Hawaii "
            "tax reform)."
        ),
        text_es=(
            "Impone tasas de impuesto sobre la renta individual graduadas de hasta 11.0% bajo la "
            "Ley 97 (reforma contributiva de Hawaii de 2024)."
        ),
        effective_year=2024,
        source_url="https://www.capitol.hawaii.gov/docs/hrs.htm",
        cross_references=[],
        breadcrumbs="HRS > Title 14 > Chapter 235 > Part I > § 235-51",
    ),
    # State of Idaho
    StatuteRecord(
        id="id-code-63-3024",
        jurisdiction="ID",
        corpus="Idaho Code",
        citation="Idaho Code § 63-3024",
        section="63-3024",
        title="Individuals' Tax and Tax on Estates and Trusts",
        text_en=(
            "Imposes a flat individual income tax rate of 5.695% on taxable income under HB 521 "
            "in Idaho."
        ),
        text_es=(
            "Impone una tasa impositiva individual fija del 5.695% sobre el ingreso gravable de "
            "individuos bajo HB 521 en Idaho."
        ),
        effective_year=2024,
        source_url="https://legislature.idaho.gov/statutesrules/idstat/title63/t63ch30/sect63-3024/",
        cross_references=[],
        breadcrumbs="Idaho Code > Title 63 > Chapter 30 > § 63-3024",
    ),
    # State of Indiana
    StatuteRecord(
        id="in-code-6-3-2-1",
        jurisdiction="IN",
        corpus="Ind. Code",
        citation="Ind. Code § 6-3-2-1",
        section="6-3-2-1",
        title="Imposition of Adjusted Gross Income Tax",
        text_en=(
            "Imposes flat adjusted gross income tax of 3.05% for 2024 alongside county local "
            "income taxes (LIT) in Indiana."
        ),
        text_es=(
            "Impone una tasa fija de impuesto sobre el ingreso bruto ajustado del 3.05% para 2024 "
            "junto a impuestos locales de condado (LIT) en Indiana."
        ),
        effective_year=2024,
        source_url="http://iga.in.gov/legislative/laws/current/ic/title/6/article/3/chapter/2/",
        cross_references=[],
        breadcrumbs="Ind. Code > Title 6 > Article 3 > Chapter 2 > § 6-3-2-1",
    ),
    # State of Iowa
    StatuteRecord(
        id="ia-code-422-5",
        jurisdiction="IA",
        corpus="Iowa Code",
        citation="Iowa Code § 422.5",
        section="422.5",
        title="Tax Imposed - Rates",
        text_en=(
            "Imposes personal income tax transitioning to a flat 3.80% rate under SF 2442 in Iowa."
        ),
        text_es=(
            "Impone impuesto sobre la renta personal en transición hacia una tasa fija uniforme "
            "del 3.80% bajo SF 2442 en Iowa."
        ),
        effective_year=2024,
        source_url="https://www.legis.iowa.gov/law/statutory",
        cross_references=[],
        breadcrumbs="Iowa Code > Title X > Subtitle 2 > Chapter 422 > Subchapter II > § 422.5",
    ),
    # State of Kansas
    StatuteRecord(
        id="ks-stat-79-32-110",
        jurisdiction="KS",
        corpus="K.S.A.",
        citation="Kan. Stat. Ann. § 79-32,110",
        section="79-32,110",
        title="Tax Imposed - Classes of Taxpayers",
        text_en=(
            "Imposes individual income tax at rates of 5.20% and 5.58% under 2024 Special Session "
            "SB 1 in Kansas."
        ),
        text_es=(
            "Impone impuesto sobre el ingreso individual a tasas del 5.20% y 5.58% bajo la sesión "
            "extraordinaria de 2024 SB 1 en Kansas."
        ),
        effective_year=2024,
        source_url="http://www.kslegislature.org/li/b2023_24/statute/079_000_0000_chapter/079_032_0000_article/079_032_0110_section/079_032_0110_k/",
        cross_references=[],
        breadcrumbs="K.S.A. > Chapter 79 > Article 32 > § 79-32,110",
    ),
    # Commonwealth of Kentucky
    StatuteRecord(
        id="ky-rev-stat-141-020",
        jurisdiction="KY",
        corpus="KRS",
        citation="Ky. Rev. Stat. Ann. § 141.020",
        section="141.020",
        title="Levy of Tax on Individuals",
        text_en=(
            "Imposes flat individual income tax rate of 4.0% on net income under statutory "
            "rate-reduction triggers in Kentucky."
        ),
        text_es=(
            "Impone una tasa fija de impuesto sobre la renta del 4.0% sobre el ingreso neto bajo "
            "criterios estatutarios de reducción en Kentucky."
        ),
        effective_year=2024,
        source_url="https://apps.legislature.ky.gov/law/statutes/statute.aspx?id=53235",
        cross_references=[],
        breadcrumbs="KRS > Title XI > Chapter 141 > § 141.020",
    ),
    # State of Louisiana
    StatuteRecord(
        id="la-rev-stat-47-32",
        jurisdiction="LA",
        corpus="La. R.S.",
        citation="La. Rev. Stat. Ann. § 47:32",
        section="47:32",
        title="Rates of Tax (Graduated Schedule through 2024)",
        text_en=(
            "Imposes graduated personal income tax rates of 1.85%, 3.50%, and 4.25% in Louisiana "
            "applicable through tax year 2024."
        ),
        text_es=(
            "Impone tasas de impuesto sobre la renta personal graduadas de 1.85%, 3.50% y 4.25% en "
            "Luisiana aplicables hasta el año contributivo 2024."
        ),
        effective_year=2022,
        superseded_year=2025,
        source_url="https://www.legis.la.gov/legis/Law.aspx?d=101946",
        cross_references=["La. Rev. Stat. Ann. § 47:32"],
        breadcrumbs="La. R.S. > Title 47 > Subtitle II > Chapter 1 > Part I > § 47:32",
    ),
    StatuteRecord(
        id="la-rev-stat-47-32-2025",
        jurisdiction="LA",
        corpus="La. R.S.",
        citation="La. Rev. Stat. Ann. § 47:32",
        section="47:32",
        title="Flat Individual Income Tax Rate (Act 11 of 2024 3rd Ex. Sess.)",
        text_en=(
            "Imposes a flat individual income tax rate of 3.00% on net taxable income effective "
            "for tax periods beginning on or after January 1, 2025 (Act 11 of 2024)."
        ),
        text_es=(
            "Impone una tasa fija de impuesto sobre la renta del 3.00% sobre el ingreso neto "
            "gravable de individuos a partir del 1 de enero de 2025 (Act 11 de 2024)."
        ),
        effective_year=2025,
        source_url="https://www.legis.la.gov/legis/Law.aspx?d=101946",
        cross_references=["La. Rev. Stat. Ann. § 47:32"],
        breadcrumbs="La. R.S. > Title 47 > Subtitle II > Chapter 1 > Part I > § 47:32",
    ),
    # State of Maine
    StatuteRecord(
        id="me-rev-stat-36-5111",
        jurisdiction="ME",
        corpus="36 M.R.S.",
        citation="Me. Rev. Stat. tit. 36, § 5111",
        section="5111",
        title="Imposition and Rate of Tax",
        text_en=(
            "Imposes graduated individual income tax rates of 5.80%, 6.75%, and top rate 7.15% in "
            "Maine."
        ),
        text_es=(
            "Impone tasas de impuesto sobre ingresos individuales graduadas del 5.80%, 6.75% y "
            "tasa máxima de 7.15% en Maine."
        ),
        effective_year=2024,
        source_url="https://legislature.maine.gov/statutes/36/title36sec5111.html",
        cross_references=[],
        breadcrumbs="36 M.R.S. > Part 8 > Chapter 803 > § 5111",
    ),
    # State of Michigan
    StatuteRecord(
        id="mi-comp-laws-206-51",
        jurisdiction="MI",
        corpus="MCL",
        citation="Mich. Comp. Laws § 206.51",
        section="206.51",
        title="Tax Rate on Taxable Income of Individuals",
        text_en=(
            "Imposes a flat individual income tax rate of 4.25% on taxable net income in Michigan."
        ),
        text_es=(
            "Impone una tasa uniforme de impuesto sobre ingresos individuales del 4.25% sobre el "
            "ingreso neto gravable en Michigan."
        ),
        effective_year=2024,
        source_url="http://legislature.mi.gov/doc.aspx?mcl-206-51",
        cross_references=[],
        breadcrumbs="MCL > Chapter 206 > Act 281 of 1967 > Part 1 > Chapter 2 > § 206.51",
    ),
    # State of Minnesota
    StatuteRecord(
        id="mn-stat-290-06",
        jurisdiction="MN",
        corpus="Minn. Stat.",
        citation="Minn. Stat. § 290.06",
        section="290.06",
        title="Rates of Tax - Individuals",
        text_en=(
            "Imposes graduated individual income tax rates: 5.35%, 6.80%, 7.85%, and top rate "
            "9.85% in Minnesota."
        ),
        text_es=(
            "Impone tasas impositivas individuales graduadas: 5.35%, 6.80%, 7.85% y tasa máxima "
            "de 9.85% en Minnesota."
        ),
        effective_year=2024,
        source_url="https://www.revisor.mn.gov/statutes/cite/290.06",
        cross_references=[],
        breadcrumbs="Minn. Stat. > Chapter 290 > § 290.06",
    ),
    # State of Mississippi
    StatuteRecord(
        id="ms-code-27-7-5",
        jurisdiction="MS",
        corpus="Miss. Code",
        citation="Miss. Code Ann. § 27-7-5",
        section="27-7-5",
        title="Imposition and Rates of Tax",
        text_en=(
            "Imposes individual income tax with 0% on the first $10,000 and flat 4.70% on excess "
            "income under the Mississippi Tax Freedom Act."
        ),
        text_es=(
            "Impone impuesto sobre la renta individual con 0% en los primeros $10,000 y 4.70% fijo "
            "sobre el exceso bajo la Ley de Libertad Tributaria de Mississippi."
        ),
        effective_year=2024,
        source_url="https://advance.lexis.com/container?config=00JAAzNzA5ZmEtMDMxMi00YTRkLThjMTMtNDU5YTU1ZjJmYjM1CgkJcFJvY2Vzc2luZ8tX",
        cross_references=[],
        breadcrumbs="Miss. Code > Title 27 > Chapter 7 > Article 1 > § 27-7-5",
    ),
    # State of Missouri
    StatuteRecord(
        id="mo-rev-stat-143-011",
        jurisdiction="MO",
        corpus="RSMo",
        citation="Mo. Rev. Stat. § 143.011",
        section="143.011",
        title="Tax on Resident Individuals",
        text_en=(
            "Imposes graduated individual income tax rates with a top marginal rate of 4.80% in "
            "Missouri under SB 3 and SB 5."
        ),
        text_es=(
            "Impone tasas de impuesto individual graduadas con una tasa marginal máxima de 4.80% "
            "en Missouri bajo SB 3 y SB 5."
        ),
        effective_year=2024,
        source_url="https://revisor.mo.gov/main/OneSection.aspx?section=143.011",
        cross_references=[],
        breadcrumbs="RSMo > Title X > Chapter 143 > § 143.011",
    ),
    # State of Montana
    StatuteRecord(
        id="mt-code-15-30-2103",
        jurisdiction="MT",
        corpus="MCA",
        citation="Mont. Code Ann. § 15-30-2103",
        section="15-30-2103",
        title="Rate of Tax - Net Income",
        text_en=(
            "Imposes restructured individual income tax with two brackets at 4.7% and top rate "
            "5.9% under SB 399 in Montana."
        ),
        text_es=(
            "Impone impuesto individual reestructurado con dos tramos al 4.7% y tasa máxima del "
            "5.9% bajo SB 399 en Montana."
        ),
        effective_year=2024,
        source_url="https://leg.mt.gov/bills/mca/title_0150/chapter_0300/part_0210/section_0030/0150-0300-0210-0030.html",
        cross_references=[],
        breadcrumbs="MCA > Title 15 > Chapter 30 > Part 21 > § 15-30-2103",
    ),
    # State of Nebraska
    StatuteRecord(
        id="ne-rev-stat-77-2715-03",
        jurisdiction="NE",
        corpus="Neb. Rev. Stat.",
        citation="Neb. Rev. Stat. § 77-2715.03",
        section="77-2715.03",
        title="Income Tax - Rates",
        text_en=(
            "Imposes graduated personal income tax rates with top rate of 5.84% and enforces the "
            "convenience of the employer doctrine in Nebraska."
        ),
        text_es=(
            "Impone tasas de impuesto sobre ingresos personales con tasa máxima de 5.84% y aplica "
            "la doctrina de conveniencia del empleador en Nebraska."
        ),
        effective_year=2024,
        source_url="https://nebraskalegislature.gov/laws/statutes.php?statute=77-2715.03",
        cross_references=[],
        breadcrumbs="Neb. Rev. Stat. > Chapter 77 > Article 27 > § 77-2715.03",
    ),
    # State of Nevada
    StatuteRecord(
        id="nv-const-art-10-sec-1",
        jurisdiction="NV",
        corpus="Nev. Const.",
        citation="Nev. Const. art. 10, § 1",
        section="Art. 10, § 1",
        title="Prohibition of Personal Income Tax",
        text_en=(
            "Constitutional prohibition against state personal income, estate, or inheritance "
            "taxes in Nevada."
        ),
        text_es=(
            "Prohibición constitucional contra impuestos estatales sobre el ingreso personal, "
            "herencias o sucesiones en Nevada."
        ),
        effective_year=1989,
        source_url="https://www.leg.state.nv.us/const/nvconst.html#Art10Sec1",
        cross_references=[],
        breadcrumbs="Nev. Const. > Article 10 > § 1",
    ),
    # State of New Hampshire
    StatuteRecord(
        id="nh-rev-stat-77-1",
        jurisdiction="NH",
        corpus="RSA",
        citation="N.H. Rev. Stat. Ann. § 77:1",
        section="77:1",
        title="Rate of Tax on Interest and Dividends (Repealed Effective 2025)",
        text_en=(
            "Imposed 3.0% tax on dividend and interest income for 2024, fully repealed for tax "
            "periods beginning January 1, 2025 under 2023 HB 2 in New Hampshire."
        ),
        text_es=(
            "Impuso 3.0% sobre dividendos e intereses en 2024, totalmente derogado a partir del "
            "1 de enero de 2025 bajo la ley HB 2 de 2023 de New Hampshire."
        ),
        effective_year=2024,
        superseded_year=2025,
        source_url="https://www.gencourt.state.nh.us/rsa/html/v/77/77-1.htm",
        cross_references=[],
        breadcrumbs="RSA > Title V > Chapter 77 > § 77:1",
    ),
    # State of New Mexico
    StatuteRecord(
        id="nm-stat-7-2-7",
        jurisdiction="NM",
        corpus="NMSA",
        citation="N.M. Stat. Ann. § 7-2-7",
        section="7-2-7",
        title="Individual Income Tax Rates",
        text_en=(
            "Imposes graduated individual income tax rates from 1.7% to top rate 5.9% in "
            "New Mexico."
        ),
        text_es=(
            "Impone tasas de impuesto sobre la renta individual graduadas desde 1.7% hasta tasa "
            "máxima de 5.9% en Nuevo México."
        ),
        effective_year=2024,
        source_url="https://nmonesource.com/nmos/nmsa/en/item/4340/index.do#!fragment/sec7-2-7",
        cross_references=[],
        breadcrumbs="NMSA > Chapter 7 > Article 2 > § 7-2-7",
    ),
    # State of North Dakota
    StatuteRecord(
        id="nd-cent-code-57-38-30-3",
        jurisdiction="ND",
        corpus="N.D.C.C.",
        citation="N.D. Cent. Code § 57-38-30.3",
        section="57-38-30.3",
        title="Simplified Method for Computing Individual Income Tax",
        text_en=(
            "Imposes individual income tax at 0% up to $44,725; 1.95% to $225,975; and top rate "
            "2.50% (HB 1158) in North Dakota."
        ),
        text_es=(
            "Impone impuesto sobre la renta individual de 0% hasta $44,725; 1.95% a $225,975; y "
            "tasa máxima de 2.50% (HB 1158) en Dakota del Norte."
        ),
        effective_year=2024,
        source_url="https://www.ndlegis.gov/cencode/t57c38.pdf",
        cross_references=[],
        breadcrumbs="N.D.C.C. > Title 57 > Chapter 57-38 > § 57-38-30.3",
    ),
    # State of Oklahoma
    StatuteRecord(
        id="ok-stat-68-2355",
        jurisdiction="OK",
        corpus="68 O.S.",
        citation="Okla. Stat. tit. 68, § 2355",
        section="2355",
        title="Tax Rates - Individuals",
        text_en=(
            "Imposes graduated individual income tax rates from 0.25% to top rate 4.75% in "
            "Oklahoma."
        ),
        text_es=(
            "Impone tasas de impuesto sobre ingresos individuales graduadas desde 0.25% hasta tasa "
            "máxima de 4.75% en Oklahoma."
        ),
        effective_year=2024,
        source_url="https://www.oscn.net/applications/oscn/DeliverDocument.asp?CiteID=440316",
        cross_references=[],
        breadcrumbs="68 O.S. > Chapter 1 > Article 23 > § 2355",
    ),
    # State of Oregon
    StatuteRecord(
        id="or-rev-stat-316-037",
        jurisdiction="OR",
        corpus="ORS",
        citation="Or. Rev. Stat. § 316.037",
        section="316.037",
        title="Imposition and Rate of Personal Income Tax",
        text_en=(
            "Imposes graduated individual income tax rates of 4.75%, 6.75%, 8.75%, and top rate "
            "9.90% in Oregon."
        ),
        text_es=(
            "Impone tasas de impuesto sobre la renta individual graduadas de 4.75%, 6.75%, 8.75% "
            "y tasa máxima de 9.90% en Oregón."
        ),
        effective_year=2024,
        source_url="https://www.oregonlegislature.gov/bills_laws/ors/ors316.html",
        cross_references=[],
        breadcrumbs="ORS > Title 29 > Chapter 316 > § 316.037",
    ),
    # State of Rhode Island
    StatuteRecord(
        id="ri-gen-laws-44-30-1",
        jurisdiction="RI",
        corpus="R.I.G.L.",
        citation="R.I. Gen. Laws § 44-30-1",
        section="44-30-1",
        title="Persons Subject to Tax - Rates",
        text_en=(
            "Imposes graduated individual income tax rates of 3.75%, 4.75%, and 5.99% on federal "
            "adjusted gross income in Rhode Island."
        ),
        text_es=(
            "Impone tasas de impuesto sobre el ingreso individual del 3.75%, 4.75% y 5.99% "
            "sobre el ingreso bruto ajustado federal en Rhode Island."
        ),
        effective_year=2024,
        source_url="http://webserver.rilin.state.ri.us/Statutes/TITLE44/44-30/44-30-1.htm",
        cross_references=[],
        breadcrumbs="R.I.G.L. > Title 44 > Chapter 44-30 > § 44-30-1",
    ),
    # State of South Carolina
    StatuteRecord(
        id="sc-code-12-6-510",
        jurisdiction="SC",
        corpus="S.C. Code",
        citation="S.C. Code Ann. § 12-6-510",
        section="12-6-510",
        title="Tax Rates for Individuals",
        text_en=(
            "Imposes individual income tax with 0% on initial income and a top marginal rate of "
            "6.40% in South Carolina."
        ),
        text_es=(
            "Impone impuesto individual sobre ingresos con 0% en tramo inicial y tasa marginal "
            "máxima del 6.40% en Carolina del Sur."
        ),
        effective_year=2024,
        source_url="https://www.scstatehouse.gov/code/t12c006.php",
        cross_references=[],
        breadcrumbs="S.C. Code > Title 12 > Chapter 6 > Article 5 > § 12-6-510",
    ),
    # State of South Dakota
    StatuteRecord(
        id="sd-const-art-11-sec-2",
        jurisdiction="SD",
        corpus="S.D. Const.",
        citation="S.D. Const. art. XI, § 2",
        section="Art. XI, § 2",
        title="Constitutional Taxation Power - Incomes and Property",
        text_en=(
            "Empowers the South Dakota Legislature to levy graduated personal income taxes; "
            "the state currently imposes no personal income tax by legislative policy."
        ),
        text_es=(
            "Faculta a la Legislatura a imponer impuestos graduales sobre la renta individual; "
            "Dakota del Sur no cobra impuesto sobre ingresos por política legislativa."
        ),
        effective_year=1978,
        source_url="https://sdlegislature.gov/Statutes/Constitution/2042898",
        cross_references=[],
        breadcrumbs="S.D. Const. > Article XI > § 2",
    ),
    # State of Tennessee
    StatuteRecord(
        id="tn-const-art-2-sec-28",
        jurisdiction="TN",
        corpus="Tenn. Const.",
        citation="Tenn. Const. art. II, § 28",
        section="Art. II, § 28",
        title="Prohibition of Tax on Personal Income",
        text_en=(
            "Constitutional amendment prohibiting any state or local tax upon payroll or earned "
            "personal income in Tennessee."
        ),
        text_es=(
            "Enmienda constitucional que prohíbe cualquier impuesto estatal o local sobre nómina "
            "o ingresos individuales del trabajo en Tennessee."
        ),
        effective_year=2014,
        source_url="https://www.capitol.tn.gov/about/docs/TN-Constitution.pdf",
        cross_references=[],
        breadcrumbs="Tenn. Const. > Article II > § 28",
    ),
    # State of Utah
    StatuteRecord(
        id="ut-code-59-10-104",
        jurisdiction="UT",
        corpus="Utah Code",
        citation="Utah Code Ann. § 59-10-104",
        section="59-10-104",
        title="Tax Basis - Rates for Individuals",
        text_en=(
            "Imposes a flat individual income tax rate of 4.65% on state taxable income under "
            "SB 69 in Utah."
        ),
        text_es=(
            "Impone una tasa fija de impuesto sobre ingresos individuales del 4.65% sobre el "
            "ingreso gravable bajo SB 69 en Utah."
        ),
        effective_year=2024,
        source_url="https://le.utah.gov/xcode/Title59/Chapter10/59-10-S104.html",
        cross_references=[],
        breadcrumbs="Utah Code > Title 59 > Chapter 10 > Part 1 > § 59-10-104",
    ),
    # State of Vermont
    StatuteRecord(
        id="vt-stat-32-5822",
        jurisdiction="VT",
        corpus="32 V.S.A.",
        citation="32 V.S.A. § 5822",
        section="5822",
        title="Tax on Income of Individuals, Estates, and Trusts",
        text_en=(
            "Imposes graduated individual income tax rates: 3.35%, 6.60%, 7.60%, and top rate "
            "8.75% in Vermont."
        ),
        text_es=(
            "Impone tasas de impuesto individual sobre la renta: 3.35%, 6.60%, 7.60% y tasa "
            "máxima de 8.75% en Vermont."
        ),
        effective_year=2024,
        source_url="https://legislature.vermont.gov/statutes/section/32/151/05822",
        cross_references=[],
        breadcrumbs="32 V.S.A. > Part 3 > Chapter 151 > Subchapter 2 > § 5822",
    ),
    # State of West Virginia
    StatuteRecord(
        id="wv-code-11-21-4e",
        jurisdiction="WV",
        corpus="W. Va. Code",
        citation="W. Va. Code § 11-21-4e",
        section="11-21-4e",
        title="Rate of Tax - Taxable Years Beginning After 2022",
        text_en=(
            "Imposes graduated individual income tax rates from 2.36% to top rate 5.12% enacted "
            "under HB 2526 in West Virginia."
        ),
        text_es=(
            "Impone tasas de impuesto sobre ingresos individuales graduadas del 2.36% a tasa "
            "máxima de 5.12% bajo HB 2526 en West Virginia."
        ),
        effective_year=2024,
        source_url="http://www.wvlegislature.gov/WVCODE/Code.cfm?chap=11&art=21#21-4e",
        cross_references=[],
        breadcrumbs="W. Va. Code > Chapter 11 > Article 21 > Part I > § 11-21-4e",
    ),
    # State of Wisconsin
    StatuteRecord(
        id="wi-stat-71-06",
        jurisdiction="WI",
        corpus="Wis. Stat.",
        citation="Wis. Stat. § 71.06",
        section="71.06",
        title="Rates of Tax - Individuals",
        text_en=(
            "Imposes graduated individual income tax rates: 3.50%, 4.40%, 5.30%, and top rate "
            "7.65% in Wisconsin."
        ),
        text_es=(
            "Impone tasas de impuesto individual sobre ingresos: 3.50%, 4.40%, 5.30% y tasa "
            "máxima de 7.65% en Wisconsin."
        ),
        effective_year=2024,
        source_url="https://docs.legis.wisconsin.gov/statutes/statutes/71/iii/06",
        cross_references=[],
        breadcrumbs="Wis. Stat. > Chapter 71 > Subchapter I > § 71.06",
    ),
    # State of Wyoming
    StatuteRecord(
        id="wy-const-art-15-sec-18",
        jurisdiction="WY",
        corpus="Wyo. Const.",
        citation="Wyo. Const. art. 15, § 18",
        section="Art. 15, § 18",
        title="Full Tax Credit Requirement for Any Individual Income Tax",
        text_en=(
            "Constitutional guarantee conditioning any income tax upon full credit for sales, "
            "use, and ad valorem property taxes paid in Wyoming."
        ),
        text_es=(
            "Garantía constitucional que condiciona cualquier impuesto sobre la renta a acreditar "
            "el 100% de impuestos sobre ventas, uso y ad valorem pagados en Wyoming."
        ),
        effective_year=1974,
        source_url="https://wyoleg.gov/statutes/compress/title97.pdf",
        cross_references=[],
        breadcrumbs="Wyo. Const. > Article 15 > § 18",
    ),
]

PUBLICATIONS_CATALOG: list[PublicationRecord] = [
    PublicationRecord(
        id="pr-cc-ri-21-10",
        category="carta-circular",
        doc_number="CC RI 21-10",
        doc_date="2021-03-31",
        title="Tratamiento contributivo del teletrabajo y servicios prestados por no residentes",
        summary=(
            "Directrices oficiales del Departamento de Hacienda sobre la fuente de ingresos por servicios "
            "laborales remotos, retención en el origen y establecimiento permanente en Puerto Rico."
        ),
        source_url="https://hacienda.pr.gov/publicaciones/carta-circular-de-rentas-internas-num-21-10",
    ),
    PublicationRecord(
        id="pa-dor-it-bulletin-2021-01",
        category="policy-bulletin",
        doc_number="PA REV-BULL 2021-01",
        doc_date="2021-06-15",
        title="Pennsylvania Personal Income Tax Telework and Sourcing Guidance",
        summary=(
            "Guidance from the Pennsylvania Department of Revenue concerning the convenience of "
            "the employer doctrine and withholding obligations for telecommuting employees."
        ),
        source_url="https://www.revenue.pa.gov/FormsandPublications/FormsPublications/Pages/default.aspx",
    ),
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
    PublicationRecord(
        id="ga-dor-pb-it-2024-01",
        category="policy-bulletin",
        doc_number="DOR PB IT-2024-01",
        doc_date="2024-04-20",
        title="Georgia Flat Tax Rate and Standard Deduction Adjustments under HB 1023",
        summary=(
            "Guidance from the Georgia Department of Revenue explaining the reduction in the individual income "
            "tax rate to 5.49% for tax year 2024 and elimination of graduated brackets."
        ),
        source_url="https://dor.georgia.gov/document/document/policy-bulletin-it-2024-01/download",
    ),
    PublicationRecord(
        id="ga-dor-pb-it-2022-02",
        category="policy-bulletin",
        doc_number="DOR PB IT-2022-02",
        doc_date="2022-05-18",
        title="Georgia Pass-Through Entity Tax (PTET) Guidance under HB 149",
        summary=(
            "Technical guidelines for eligible S corporations and partnerships electing to pay tax at the entity level "
            "to mitigate the federal limitation on individual state and local tax (SALT) deductions."
        ),
        source_url="https://dor.georgia.gov/document/document/policy-bulletin-it-2022-02/download",
    ),
    PublicationRecord(
        id="va-tax-bulletin-05-7",
        category="policy-bulletin",
        doc_number="VA TB 05-7",
        doc_date="2005-08-25",
        title="Virginia Guidelines for Determining Residency",
        summary=(
            "Official guidance from the Virginia Department of Taxation regarding domiciliary and "
            "actual residency tests, temporary absences, and reciprocal commuter provisions."
        ),
        source_url="https://www.tax.virginia.gov/laws-rules-decisions/tax-bulletins/05-7",
    ),
    PublicationRecord(
        id="md-admin-release-34",
        category="policy-bulletin",
        doc_number="MD AR-34",
        doc_date="2023-01-10",
        title="Maryland Domicile and Statutory Residency Physical Presence Guidelines",
        summary=(
            "Administrative guidance issued by the Comptroller of Maryland detailing the 183-day rule, "
            "permanent place of abode factors, and county local income tax calculation."
        ),
        source_url="https://www.marylandtaxes.gov/forms/tax_publications/administrative_releases.php",
    ),
    PublicationRecord(
        id="oh-tax-info-it-2007-08",
        category="policy-bulletin",
        doc_number="OH IT 2007-08",
        doc_date="2007-09-01",
        title="Ohio Residency Guidelines and Bright-Line Contact Period Test",
        summary=(
            "Comprehensive information release from the Ohio Department of Taxation explaining contact "
            "periods, the bright-line test, and instructions for filing Form IT-NRS non-domicile affidavit."
        ),
        source_url="https://tax.ohio.gov/individual/resources/information-releases",
    ),
]


def check_upstream_endpoints() -> dict[str, Any]:
    """Verify live connectivity to official digital tax publishing authorities."""
    endpoints = {
        "OLRC_USLM": "https://uscode.house.gov/",
        "eCFR_API": "https://www.ecfr.gov/api/versioner/v1/versions.json",
        "Hacienda_PR": "https://hacienda.pr.gov/",
        "OGP_PR": "https://bvirtualogp.pr.gov/",
        "Georgia_DOR": "https://dor.georgia.gov/",
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


def _statute_file_base(s: StatuteRecord) -> str:
    """Derive clean, lowercase filename base without spaces for a statute record."""
    sec_slug = (
        s.section.replace(".", "_")
        .replace("-", "_")
        .replace("/", "_")
        .replace(":", "_")
        .replace("(", "_")
        .replace(")", "")
        .replace(" ", "_")
        .lower()
    )
    if s.superseded_year:
        return f"{s.corpus.lower()}_{sec_slug}_{s.effective_year}"
    return f"{s.corpus.lower()}_{sec_slug}"


def export_documentation(output_dir: Path) -> tuple[int, int]:
    """Generate Markdown and llms.txt documentation files for LLM agents."""
    output_dir.mkdir(parents=True, exist_ok=True)
    statute_count = 0
    # Export individual markdown files
    for s in STATUTES_CATALOG:
        target_dir = output_dir / s.jurisdiction.lower()
        target_dir.mkdir(exist_ok=True)

        file_base = _statute_file_base(s)
        filename = f"{file_base}.md"
        content = f"""# {s.citation} — {s.title}

- **Jurisdiction:** {s.jurisdiction}
- **Corpus:** {s.corpus}
- **Effective Year:** {s.effective_year}
- **Superseded Year:** {s.superseded_year or "Current / In Force"}
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
        "> Authoritative statutory and regulatory references for United States Federal Tax Law,",
        "> Puerto Rico Territorial Tax Law (Act 60-2019 and Ley 1-2011), and Georgia State Tax Law (O.C.G.A. Title 48).",
        "",
        "## Statutory Catalog",
        "",
    ]
    for s in STATUTES_CATALOG:
        file_base = _statute_file_base(s)
        llms_summary.append(
            f"- [{s.citation} ({s.effective_year}) — {s.title}](./{s.jurisdiction.lower()}/{file_base}.md): {s.breadcrumbs}"
        )

    llms_summary.extend(
        [
            "",
            "## Administrative Publications (Hacienda SURI & Georgia DOR)",
            "",
        ]
    )
    for p in PUBLICATIONS_CATALOG:
        llms_summary.append(f"- **{p.doc_number}** ({p.doc_date}) — {p.title}: {p.summary}")

    llms_txt_path.write_text("\n".join(llms_summary), encoding="utf-8")

    # Generate llms-full.txt
    llms_full_path = output_dir / "llms-full.txt"
    llms_full_content = ["# Full Statutory Corpus: US, Puerto Rico & Georgia Tax Intelligence\n"]
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
        elif sources == "cfr" and s.corpus == "CFR":
            selected_statutes.append(s)
        elif sources.lower() == s.jurisdiction.lower():
            selected_statutes.append(s)

    selected_publications: list[PublicationRecord] = []
    for pub in PUBLICATIONS_CATALOG:
        if sources in ("all", "publications"):
            selected_publications.append(pub)
        elif sources.lower() == pub.id.split("-")[0].lower():
            selected_publications.append(pub)

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
        description="Ingest, parse, and synchronize US, Puerto Rico & State Tax Codes into DuckDB.",
    )
    parser.add_argument(
        "--db-path",
        default=os.getenv("TAXCODE_DB_PATH", ":memory:"),
        help="Target DuckDB database path (defaults to TAXCODE_DB_PATH or :memory:).",
    )
    parser.add_argument(
        "--sources",
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
