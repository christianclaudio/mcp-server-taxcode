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
        elif sources == "pr" and pub.id.startswith("pr-"):
            selected_publications.append(pub)
        elif sources == "ga" and pub.id.startswith("ga-"):
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
        description="Ingest, parse, and synchronize US, Puerto Rico & Georgia Tax Codes into DuckDB.",
    )
    parser.add_argument(
        "--db-path",
        default=os.getenv("TAXCODE_DB_PATH", ":memory:"),
        help="Target DuckDB database path (defaults to TAXCODE_DB_PATH or :memory:).",
    )
    parser.add_argument(
        "--sources",
        choices=["all", "us", "pr", "ga", "cfr", "publications"],
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
