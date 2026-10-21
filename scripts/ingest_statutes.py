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
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from taxcode_mcp.db import (
    ECFR_TITLE26_DATE,
    USLM_TITLE26_RELEASE_POINT,
    USLM_TITLE26_SOURCE_URL,
    TaxDatabase,
)

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
        title="Income from sources within Puerto Rico / Ingresos de fuentes dentro de Puerto Rico",
        text_en=(
            "The following items shall not be included in gross income and shall be exempt from "
            "taxation under this subtitle:\n"
            "(1) Resident of Puerto Rico for entire taxable year\n"
            "In the case of an individual who is a bona fide resident of Puerto Rico during the entire "
            "taxable year, income derived from sources within Puerto Rico (except amounts received for "
            "services performed as an employee of the United States or any agency thereof); but such "
            "individual shall not be allowed as a deduction from his gross income any deductions (other "
            "than the deduction under section 151, relating to personal exemptions), or any credit, "
            "properly allocable to or chargeable against amounts excluded from gross income under this "
            "paragraph.\n"
            "(2) Taxable year of change of residence from Puerto Rico\n"
            "In the case of an individual citizen of the United States who has been a bona fide "
            "resident of Puerto Rico for a period of at least 2 years before the date on which he "
            "changes his residence from Puerto Rico, income derived from sources therein (except "
            "amounts received for services performed as an employee of the United States or any agency "
            "thereof) which is attributable to that part of such period of Puerto Rican residence "
            "before such date; but such individual shall not be allowed as a deduction from his gross "
            "income any deductions (other than the deduction for personal exemptions under section "
            "151), or any credit, properly allocable to or chargeable against amounts excluded from "
            "gross income under this paragraph."
        ),
        text_es=(
            "En el caso de un individuo que sea residente de buena fe de Puerto Rico durante todo el "
            "año tributario, el ingreso bruto no incluirá ingresos derivados de fuentes dentro de "
            "Puerto Rico (excepto cantidades recibidas por servicios prestados como empleado de los "
            "Estados Unidos o cualquiera de sus agencias); no permitiéndose deducciones atribuibles a "
            "dicho ingreso."
        ),
        effective_year=1954,
        source_url=USLM_TITLE26_SOURCE_URL,
        cross_references=["26 U.S.C. § 937", "26 CFR § 1.937-1", "26 U.S.C. § 865"],
        breadcrumbs="Title 26 > Subtitle A > Chapter 1 > Subchapter N > Part III > Subpart D > § 933 (OLRC release point 119-121)",
    ),
    StatuteRecord(
        id="us-irc-937",
        jurisdiction="US",
        corpus="IRC",
        citation="26 U.S.C. § 937",
        section="937",
        title="Residence and source rules involving possessions / Reglas de residencia y fuente relativas a las posesiones",
        text_en=(
            "(a) Bona fide resident\n"
            "For purposes of this subpart, section 865(g)(3), section 876, section 881(b), paragraphs "
            "(2) and (3) of section 901(b), section 957(c), section 3401(a)(8)(C), and section 7654(a), "
            "except as provided in regulations, the term “bona fide resident” means a person—\n"
            "(1) who is present for at least 183 days during the taxable year in Guam, American Samoa, "
            "the Northern Mariana Islands, Puerto Rico, or the Virgin Islands, as the case may be, and\n"
            "(2) who does not have a tax home (determined under the principles of section 911(d)(3) "
            "without regard to the second sentence thereof) outside such specified possession during "
            "the taxable year and does not have a closer connection (determined under the principles of "
            "section 7701(b)(3)(B)(ii)) to the United States or a foreign country than to such "
            "specified possession.\n"
            "For purposes of paragraph (1), the determination as to whether a person is present for any "
            "day shall be made under the principles of section 7701(b).\n"
            "(b) Source rules\n"
            "Except as provided in regulations, for purposes of this title—\n"
            "(1) except as provided in paragraph (2), rules similar to the rules for determining "
            "whether income is income from sources within the United States or is effectively connected "
            "with the conduct of a trade or business within the United States shall apply for purposes "
            "of determining whether income is from sources within a possession specified in subsection "
            "(a)(1) or effectively connected with the conduct of a trade or business within any such "
            "possession, and\n"
            "(2) any income treated as income from sources within the United States or as effectively "
            "connected with the conduct of a trade or business within the United States shall not be "
            "treated as income from sources within any such possession or as effectively connected with "
            "the conduct of a trade or business within any such possession.\n"
            "(c) Reporting requirement\n"
            "(1) In general\n"
            "If, for any taxable year, an individual takes the position for United States income tax "
            "reporting purposes that the individual became, or ceases to be, a bona fide resident of a "
            "possession specified in subsection (a)(1), such individual shall file with the Secretary, "
            "at such time and in such manner as the Secretary may prescribe, notice of such position.\n"
            "(2) Transition rule\n"
            "If, for any of an individual’s 3 taxable years ending before the individual’s first "
            "taxable year ending after the date of the enactment of this subsection, the individual "
            "took a position described in paragraph (1), the individual shall file with the Secretary, "
            "at such time and in such manner as the Secretary may prescribe, notice of such position."
        ),
        text_es=(
            "Establece los requisitos estatutarios para determinar residencia de buena fe en posesiones "
            "(presencia física de 183 días, centro de actividad tributaria 'tax home' y conexión más "
            "cercana), así como las reglas de fuente de ingresos aplicables a posesiones y territorios."
        ),
        effective_year=2004,
        source_url=USLM_TITLE26_SOURCE_URL,
        cross_references=["26 U.S.C. § 933", "26 CFR § 1.937-1", "26 CFR § 1.937-2"],
        breadcrumbs="Title 26 > Subtitle A > Chapter 1 > Subchapter N > Part III > Subpart D > § 937 (OLRC release point 119-121)",
    ),
    StatuteRecord(
        id="us-irc-865",
        jurisdiction="US",
        corpus="IRC",
        citation="26 U.S.C. § 865",
        section="865",
        title="Source rules for personal property sales / Reglas de fuente para ventas de propiedad mueble",
        text_en=(
            "(a) General rule\n"
            "Except as otherwise provided in this section, income from the sale of personal property—\n"
            "(1) by a United States resident shall be sourced in the United States, or\n"
            "(2) by a nonresident shall be sourced outside the United States.\n"
            "(b) Exception for inventory property\n"
            "In the case of income derived from the sale of inventory property—\n"
            "(1) this section shall not apply, and\n"
            "(2) such income shall be sourced under the rules of sections 861(a)(6), 862(a)(6), and "
            "863.\n"
            "Notwithstanding the preceding sentence, any income from the sale of any unprocessed timber "
            "which is a softwood and was cut from an area in the United States shall be sourced in the "
            "United States and the rules of sections 862(a)(6) and 863(b) shall not apply to any such "
            "income. For purposes of the preceding sentence, the term “unprocessed timber” means any "
            "log, cant, or similar form of timber.\n"
            "(c) Exception for depreciable personal property\n"
            "(1) In general\n"
            "Gain (not in excess of the depreciation adjustments) from the sale of depreciable personal "
            "property shall be allocated between sources in the United States and sources outside the "
            "United States—\n"
            "(A) by treating the same proportion of such gain as sourced in the United States as the "
            "United States depreciation adjustments with respect to such property bear to the total "
            "depreciation adjustments, and\n"
            "(B) by treating the remaining portion of such gain as sourced outside the United States.\n"
            "(2) Gain in excess of depreciation\n"
            "Gain (in excess of the depreciation adjustments) from the sale of depreciable personal "
            "property shall be sourced as if such property were inventory property.\n"
            "(3) United States depreciation adjustments\n"
            "For purposes of this subsection—\n"
            "(A) In general\n"
            "The term “United States depreciation adjustments” means the portion of the depreciation "
            "adjustments to the adjusted basis of the property which are attributable to the "
            "depreciation deductions allowable in computing taxable income from sources in the United "
            "States.\n"
            "(B) Special rule for certain property\n"
            "Except in the case of property of a kind described in section 168(g)(4), if, for any "
            "taxable year—\n"
            "(i) such property is used predominantly in the United States, or\n"
            "(ii) such property is used predominantly outside the United States,\n"
            "all of the depreciation deductions allowable for such year shall be treated as having been "
            "allocated to income from sources in the United States (or, where clause (ii) applies, from "
            "sources outside the United States).\n"
            "(4) Other definitions\n"
            "For purposes of this subsection—\n"
            "(A) Depreciable personal property\n"
            "The term “depreciable personal property” means any personal property if the adjusted basis "
            "of such property includes depreciation adjustments.\n"
            "(B) Depreciation adjustments\n"
            "The term “depreciation adjustments” means adjustments reflected in the adjusted basis of "
            "any property on account of depreciation deductions (whether allowed with respect to such "
            "property or other property and whether allowed to the taxpayer or to any other person).\n"
            "(C) Depreciation deductions\n"
            "The term “depreciation deductions” means any deductions for depreciation or amortization "
            "or any other deduction allowable under any provision of this chapter which treats an "
            "otherwise capital expenditure as a deductible expense.\n"
            "(d) Exception for intangibles\n"
            "(1) In general\n"
            "In the case of any sale of an intangible—\n"
            "(A) this section shall apply only to the extent the payments in consideration of such sale "
            "are not contingent on the productivity, use, or disposition of the intangible, and\n"
            "(B) to the extent such payments are so contingent, the source of such payments shall be "
            "determined under this part in the same manner as if such payments were royalties.\n"
            "(2) Intangible\n"
            "For purposes of paragraph (1), the term “intangible” means any patent, copyright, secret "
            "process or formula, goodwill, trademark, trade brand, franchise, or other like property.\n"
            "(3) Special rule in the case of goodwill\n"
            "To the extent this section applies to the sale of goodwill, payments in consideration of "
            "such sale shall be treated as from sources in the country in which such goodwill was "
            "generated.\n"
            "(4) Coordination with subsection (c)\n"
            "(A) Gain not in excess of depreciation adjustments sourced under subsection (c)\n"
            "Notwithstanding paragraph (1), any gain from the sale of an intangible shall be sourced "
            "under subsection (c) to the extent such gain does not exceed the depreciation adjustments "
            "with respect to such intangible.\n"
            "(B) Subsection (c)(2) not to apply to intangibles\n"
            "Paragraph (2) of subsection (c) shall not apply to any gain from the sale of an "
            "intangible.\n"
            "(e) Special rules for sales through offices or fixed places of business\n"
            "(1) Sales by residents\n"
            "(A) In general\n"
            "In the case of income not sourced under subsection (b), (c), (d)(1)(B) or (3), or (f), if "
            "a United States resident maintains an office or other fixed place of business in a foreign "
            "country, income from sales of personal property attributable to such office or other fixed "
            "place of business shall be sourced outside the United States.\n"
            "(B) Tax must be imposed\n"
            "Subparagraph (A) shall not apply unless an income tax equal to at least 10 percent of the "
            "income from the sale is actually paid to a foreign country with respect to such income.\n"
            "(2) Sales by nonresidents\n"
            "(A) In general\n"
            "Notwithstanding any other provisions of this part, if a nonresident maintains an office or "
            "other fixed place of business in the United States, income from any sale of personal "
            "property (including inventory property) attributable to such office or other fixed place "
            "of business shall be sourced in the United States. The preceding sentence shall not apply "
            "for purposes of section 971 (defining export trade corporation).\n"
            "(B) Exception\n"
            "Subparagraph (A) shall not apply to any sale of inventory property which is sold for use, "
            "disposition, or consumption outside the United States if an office or other fixed place of "
            "business of the taxpayer in a foreign country materially participated in the sale.\n"
            "(3) Sales attributable to an office or other fixed place of business\n"
            "The principles of section 864(c)(5) shall apply in determining whether a taxpayer has an "
            "office or other fixed place of business and whether a sale is attributable to such an "
            "office or other fixed place of business.\n"
            "(f) Stock of affiliates\n"
            "If—\n"
            "(1) a United States resident sells stock in an affiliate which is a foreign corporation,\n"
            "(2) such sale occurs in a foreign country in which such affiliate is engaged in the active "
            "conduct of a trade or business, and\n"
            "(3) more than 50 percent of the gross income of such affiliate for the 3-year period "
            "ending with the close of such affiliate’s taxable year immediately preceding the year in "
            "which the sale occurred was derived from the active conduct of a trade or business in such "
            "foreign country,\n"
            "any gain from such sale shall be sourced outside the United States. For purposes of "
            "paragraphs (2) and (3), the United States resident may elect to treat an affiliate and all "
            "other corporations which are wholly owned (directly or indirectly) by the affiliate as one "
            "corporation.\n"
            "(g) United States resident; nonresident\n"
            "For purposes of this section—\n"
            "(1) In general\n"
            "Except as otherwise provided in this subsection—\n"
            "(A) United States resident\n"
            "The term “United States resident” means—\n"
            "(i) any individual who—\n"
            "(I) is a United States citizen or a resident alien and does not have a tax home (as "
            "defined in section 911(d)(3)) in a foreign country, or\n"
            "(II) is a nonresident alien and has a tax home (as so defined) in the United States, and\n"
            "(ii) any corporation, trust, or estate which is a United States person (as defined in "
            "section 7701(a)(30)).\n"
            "(B) Nonresident\n"
            "The term “nonresident” means any person other than a United States resident.\n"
            "(2) Special rules for United States citizens and resident aliens\n"
            "For purposes of this section, a United States citizen or resident alien shall not be "
            "treated as a nonresident with respect to any sale of personal property unless an income "
            "tax equal to at least 10 percent of the gain derived from such sale is actually paid to a "
            "foreign country with respect to that gain.\n"
            "(3) Special rule for certain stock sales by residents of Puerto Rico\n"
            "Paragraph (2) shall not apply to the sale by an individual who was a bona fide resident of "
            "Puerto Rico during the entire taxable year of stock in a corporation if—\n"
            "(A) such corporation is engaged in the active conduct of a trade or business in Puerto "
            "Rico, and\n"
            "(B) more than 50 percent of its gross income for the 3-year period ending with the close "
            "of such corporation’s taxable year immediately preceding the year in which such sale "
            "occurred was derived from the active conduct of a trade or business in Puerto Rico.\n"
            "For purposes of the preceding sentence, the taxpayer may elect to treat a corporation and "
            "all other corporations which are wholly owned (directly or indirectly) by such corporation "
            "as one corporation.\n"
            "(h) Treatment of gains from sale of certain stock or intangibles and from certain "
            "liquidations\n"
            "(1) In general\n"
            "In the case of gain to which this subsection applies—\n"
            "(A) such gain shall be sourced outside the United States, but\n"
            "(B) subsections (a), (b), and (c) of section 904 and sections 907 and 960 shall be applied "
            "separately with respect to such gain.\n"
            "(2) Gain to which subsection applies\n"
            "This subsection shall apply to—\n"
            "(A) Gain from sale of certain stock or intangibles\n"
            "Any gain—\n"
            "(i) which is from the sale of stock in a foreign corporation or an intangible (as defined "
            "in subsection (d)(2)) and which would otherwise be sourced in the United States under this "
            "section,\n"
            "(ii) which, under a treaty obligation of the United States (applied without regard to this "
            "section), would be sourced outside the United States, and\n"
            "(iii) with respect to which the taxpayer chooses the benefits of this subsection.\n"
            "(B) Gain from liquidation in possession\n"
            "Any gain which is derived from the receipt of any distribution in liquidation of a "
            "corporation—\n"
            "(i) which is organized in a possession of the United States, and\n"
            "(ii) more than 50 percent of the gross income of which during the 3-taxable year period "
            "ending with the close of the taxable year immediately preceding the taxable year in which "
            "the distribution is received is from the active conduct of a trade or business in such "
            "possession.\n"
            "(i) Other definitions\n"
            "For purposes of this section—\n"
            "(1) Inventory property\n"
            "The term “inventory property” means personal property described in paragraph (1) of "
            "section 1221(a).\n"
            "(2) Sale includes exchange\n"
            "The term “sale” includes an exchange or any other disposition.\n"
            "(3) Treatment of possessions\n"
            "Any possession of the United States shall be treated as a foreign country.\n"
            "(4) Affiliate\n"
            "The term “affiliate” means a member of the same affiliated group (within the meaning of "
            "section 1504(a) without regard to section 1504(b)).\n"
            "(5) Treatment of partnerships\n"
            "In the case of a partnership, except as provided in regulations, this section shall be "
            "applied at the partner level.\n"
            "(j) Regulations\n"
            "The Secretary shall prescribe such regulations as may be necessary or appropriate to carry "
            "out the purpose of this section, including regulations—\n"
            "(1) relating to the treatment of losses from sales of personal property,\n"
            "(2) applying the rules of this section to income derived from trading in futures "
            "contracts, forward contracts, options contracts, and other instruments, and\n"
            "(3) providing that, subject to such conditions (which may include provisions comparable to "
            "section 877) as may be provided in such regulations, subsections (e)(1)(B) and (g)(2) "
            "shall not apply for purposes of sections 931 and 933.\n"
            "(k) Cross references\n"
            "(1) For provisions relating to the characterization as dividends for source purposes of "
            "gains from the sale of stock in certain foreign corporations, see section 1248.\n"
            "(2) For sourcing of income from certain foreign currency transactions, see section 988."
        ),
        text_es=(
            "Salvo disposición expresa en contrario, el ingreso derivado de la venta de bienes "
            "personales (incluyendo acciones y valores negociables) se atribuirá a la residencia del "
            "vendedor."
        ),
        effective_year=1986,
        source_url=USLM_TITLE26_SOURCE_URL,
        cross_references=["26 U.S.C. § 937", "26 CFR § 1.937-2"],
        breadcrumbs="Title 26 > Subtitle A > Chapter 1 > Subchapter N > Part I > § 865 (OLRC release point 119-121)",
    ),
    StatuteRecord(
        id="us-irc-1",
        jurisdiction="US",
        corpus="IRC",
        citation="26 U.S.C. § 1",
        section="1",
        title="Tax imposed / Imposición del impuesto",
        text_en=(
            "(a) Married individuals filing joint returns and surviving spouses\n"
            "There is hereby imposed on the taxable income of—\n"
            "(1) every married individual (as defined in section 7703) who makes a single return "
            "jointly with his spouse under section 6013, and\n"
            "(2) every surviving spouse (as defined in section 2(a)),\n"
            "a tax determined in accordance with the following table:\n"
            "If taxable income is: | The tax is:\n"
            "Not over $36,900 | 15% of taxable income.\n"
            "Over $36,900 but not over $89,150 | $5,535, plus 28% of the excess over $36,900.\n"
            "Over $89,150 but not over $140,000 | $20,165, plus 31% of the excess over $89,150.\n"
            "Over $140,000 but not over $250,000 | $35,928.50, plus 36% of the excess over $140,000.\n"
            "Over $250,000 | $75,528.50, plus 39.6% of the excess over $250,000.\n"
            "(b) Heads of households\n"
            "There is hereby imposed on the taxable income of every head of a household (as defined in "
            "section 2(b)) a tax determined in accordance with the following table:\n"
            "If taxable income is: | The tax is:\n"
            "Not over $29,600 | 15% of taxable income.\n"
            "Over $29,600 but not over $76,400 | $4,440, plus 28% of the excess over $29,600.\n"
            "Over $76,400 but not over $127,500 | $17,544, plus 31% of the excess over $76,400.\n"
            "Over $127,500 but not over $250,000 | $33,385, plus 36% of the excess over $127,500.\n"
            "Over $250,000 | $77,485, plus 39.6% of the excess over $250,000.\n"
            "(c) Unmarried individuals (other than surviving spouses and heads of households)\n"
            "There is hereby imposed on the taxable income of every individual (other than a surviving "
            "spouse as defined in section 2(a) or the head of a household as defined in section 2(b)) "
            "who is not a married individual (as defined in section 7703) a tax determined in "
            "accordance with the following table:\n"
            "If taxable income is: | The tax is:\n"
            "Not over $22,100 | 15% of taxable income.\n"
            "Over $22,100 but not over $53,500 | $3,315, plus 28% of the excess over $22,100.\n"
            "Over $53,500 but not over $115,000 | $12,107, plus 31% of the excess over $53,500.\n"
            "Over $115,000 but not over $250,000 | $31,172, plus 36% of the excess over $115,000.\n"
            "Over $250,000 | $79,772, plus 39.6% of the excess over $250,000.\n"
            "(d) Married individuals filing separate returns\n"
            "There is hereby imposed on the taxable income of every married individual (as defined in "
            "section 7703) who does not make a single return jointly with his spouse under section "
            "6013, a tax determined in accordance with the following table:\n"
            "If taxable income is: | The tax is:\n"
            "Not over $18,450 | 15% of taxable income.\n"
            "Over $18,450 but not over $44,575 | $2,767.50, plus 28% of the excess over $18,450.\n"
            "Over $44,575 but not over $70,000 | $10,082.50, plus 31% of the excess over $44,575.\n"
            "Over $70,000 but not over $125,000 | $17,964.25, plus 36% of the excess over $70,000.\n"
            "Over $125,000 | $37,764.25, plus 39.6% of the excess over $125,000.\n"
            "(e) Estates and trusts\n"
            "There is hereby imposed on the taxable income of—\n"
            "(1) every estate, and\n"
            "(2) every trust,\n"
            "taxable under this subsection a tax determined in accordance with the following table:\n"
            "If taxable income is: | The tax is:\n"
            "Not over $1,500 | 15% of taxable income.\n"
            "Over $1,500 but not over $3,500 | $225, plus 28% of the excess over $1,500.\n"
            "Over $3,500 but not over $5,500 | $785, plus 31% of the excess over $3,500.\n"
            "Over $5,500 but not over $7,500 | $1,405, plus 36% of the excess over $5,500.\n"
            "Over $7,500 | $2,125, plus 39.6% of the excess over $7,500.\n"
            "(f) Phaseout of marriage penalty in 15-percent bracket; adjustments in tax tables so that "
            "inflation will not result in tax increases\n"
            "(1) In general\n"
            "Not later than December 15 of 1993, and each subsequent calendar year, the Secretary shall "
            "prescribe tables which shall apply in lieu of the tables contained in subsections (a), "
            "(b), (c), (d), and (e) with respect to taxable years beginning in the succeeding calendar "
            "year.\n"
            "(2) Method of prescribing tables\n"
            "The table which under paragraph (1) is to apply in lieu of the table contained in "
            "subsection (a), (b), (c), (d), or (e), as the case may be, with respect to taxable years "
            "beginning in any calendar year shall be prescribed—\n"
            "(A) except as provided in paragraph (8), by increasing the minimum and maximum dollar "
            "amounts for each bracket for which a tax is imposed under such table by the cost-of-living "
            "adjustment for such calendar year, determined—\n"
            "(i) except as provided in clause (ii), by substituting “1992” for “2016” in paragraph "
            "(3)(A)(ii), and\n"
            "(ii) in the case of adjustments to the dollar amounts at which the 36 percent rate bracket "
            "begins or at which the 39.6 percent rate bracket begins, by substituting “1993” for “2016” "
            "in paragraph (3)(A)(ii),\n"
            "(B) by not changing the rate applicable to any rate bracket as adjusted under subparagraph "
            "(A), and\n"
            "(C) by adjusting the amounts setting forth the tax to the extent necessary to reflect the "
            "adjustments in the rate brackets.\n"
            "(3) Cost-of-living adjustment\n"
            "For purposes of this subsection—\n"
            "(A) In general\n"
            "The cost-of-living adjustment for any calendar year is the percentage (if any) by which—\n"
            "(i) the C-CPI-U for the preceding calendar year, exceeds\n"
            "(ii) the CPI for calendar year 2016, multiplied by the amount determined under "
            "subparagraph (B).\n"
            "(B) Amount determined\n"
            "The amount determined under this clause is the amount obtained by dividing—\n"
            "(i) the C-CPI-U for calendar year 2016, by\n"
            "(ii) the CPI for calendar year 2016.\n"
            "(C) Special rule for adjustments with a base year after 2016\n"
            "For purposes of any provision of this title which provides for the substitution of a year "
            "after 2016 for “2016” in subparagraph (A)(ii), subparagraph (A) shall be applied by "
            "substituting “the C-CPI-U for calendar year 2016” for “the CPI for calendar year 2016” and "
            "all that follows in clause (ii) thereof.\n"
            "(4) CPI for any calendar year\n"
            "For purposes of paragraph (3), the CPI for any calendar year is the average of the "
            "Consumer Price Index as of the close of the 12-month period ending on August 31 of such "
            "calendar year.\n"
            "(5) Consumer Price Index\n"
            "For purposes of paragraph (4), the term “Consumer Price Index” means the last Consumer "
            "Price Index for all-urban consumers published by the Department of Labor. For purposes of "
            "the preceding sentence, the revision of the Consumer Price Index which is most consistent "
            "with the Consumer Price Index for calendar year 1986 shall be used.\n"
            "(6) C-CPI-U\n"
            "For purposes of this subsection—\n"
            "(A) In general\n"
            "The term “C-CPI-U” means the Chained Consumer Price Index for All Urban Consumers (as "
            "published by the Bureau of Labor Statistics of the Department of Labor). The values of the "
            "Chained Consumer Price Index for All Urban Consumers taken into account for purposes of "
            "determining the cost-of-living adjustment for any calendar year under this subsection "
            "shall be the latest values so published as of the date on which such Bureau publishes the "
            "initial value of the Chained Consumer Price Index for All Urban Consumers for the month of "
            "August for the preceding calendar year.\n"
            "(B) Determination for calendar year\n"
            "The C-CPI-U for any calendar year is the average of the C-CPI-U as of the close of the "
            "12-month period ending on August 31 of such calendar year.\n"
            "(7) Rounding\n"
            "(A) In general\n"
            "If any increase determined under paragraph (2)(A), section 63(c)(4), section 68(b)(2) 1 or "
            "section 151(d)(4) is not a multiple of $50, such increase shall be rounded to the next "
            "lowest multiple of $50.\n"
            "(B) Table for married individuals filing separately\n"
            "In the case of a married individual filing a separate return, subparagraph (A) (other than "
            "with respect to sections 63(c)(4) and 151(d)(4)(A)) shall be applied by substituting “$25” "
            "for “$50” each place it appears.\n"
            "(8) Elimination of marriage penalty in 15-percent bracket\n"
            "With respect to taxable years beginning after December 31, 2003, in prescribing the tables "
            "under paragraph (1)—\n"
            "(A) the maximum taxable income in the 15-percent rate bracket in the table contained in "
            "subsection (a) (and the minimum taxable income in the next higher taxable income bracket "
            "in such table) shall be 200 percent of the maximum taxable income in the 15-percent rate "
            "bracket in the table contained in subsection (c) (after any other adjustment under this "
            "subsection), and\n"
            "(B) the comparable taxable income amounts in the table contained in subsection (d) shall "
            "be ½ of the amounts determined under subparagraph (A).\n"
            "(g) Certain unearned income of children taxed as if parent’s income\n"
            "(1) In general\n"
            "In the case of any child to whom this subsection applies, the tax imposed by this section "
            "shall be equal to the greater of—\n"
            "(A) the tax imposed by this section without regard to this subsection, or\n"
            "(B) the sum of—\n"
            "(i) the tax which would be imposed by this section if the taxable income of such child for "
            "the taxable year were reduced by the net unearned income of such child, plus\n"
            "(ii) such child’s share of the allocable parental tax.\n"
            "(2) Child to whom subsection applies\n"
            "This subsection shall apply to any child for any taxable year if—\n"
            "(A) such child—\n"
            "(i) has not attained age 18 before the close of the taxable year, or\n"
            "(ii)(I) has attained age 18 before the close of the taxable year and meets the age "
            "requirements of section 152(c)(3) (determined without regard to subparagraph (B) thereof), "
            "and\n"
            "(II) whose earned income (as defined in section 911(d)(2)) for such taxable year does not "
            "exceed one-half of the amount of the individual’s support (within the meaning of section "
            "152(c)(1)(D) after the application of section 152(f)(5) (without regard to subparagraph "
            "(A) thereof)) for such taxable year,\n"
            "(B) either parent of such child is alive at the close of the taxable year, and\n"
            "(C) such child does not file a joint return for the taxable year.\n"
            "(3) Allocable parental tax\n"
            "For purposes of this subsection—\n"
            "(A) In general\n"
            "The term “allocable parental tax” means the excess of—\n"
            "(i) the tax which would be imposed by this section on the parent’s taxable income if such "
            "income included the net unearned income of all children of the parent to whom this "
            "subsection applies, over\n"
            "(ii) the tax imposed by this section on the parent without regard to this subsection.\n"
            "For purposes of clause (i), net unearned income of all children of the parent shall not be "
            "taken into account in computing any exclusion, deduction, or credit of the parent.\n"
            "(B) Child’s share\n"
            "A child’s share of any allocable parental tax of a parent shall be equal to an amount "
            "which bears the same ratio to the total allocable parental tax as the child’s net unearned "
            "income bears to the aggregate net unearned income of all children of such parent to whom "
            "this subsection applies.\n"
            "(C) Special rule where parent has different taxable year\n"
            "Except as provided in regulations, if the parent does not have the same taxable year as "
            "the child, the allocable parental tax shall be determined on the basis of the taxable year "
            "of the parent ending in the child’s taxable year.\n"
            "(4) Net unearned income\n"
            "For purposes of this subsection—\n"
            "(A) In general\n"
            "The term “net unearned income” means the excess of—\n"
            "(i) the portion of the adjusted gross income for the taxable year which is not "
            "attributable to earned income (as defined in section 911(d)(2)), over\n"
            "(ii) the sum of—\n"
            "(I) the amount in effect for the taxable year under section 63(c)(5)(A) (relating to "
            "limitation on standard deduction in the case of certain dependents), plus\n"
            "(II) the greater of the amount described in subclause (I) or, if the child itemizes his "
            "deductions for the taxable year, the amount of the itemized deductions allowed by this "
            "chapter for the taxable year which are directly connected with the production of the "
            "portion of adjusted gross income referred to in clause (i).\n"
            "(B) Limitation based on taxable income\n"
            "The amount of the net unearned income for any taxable year shall not exceed the "
            "individual’s taxable income for such taxable year.\n"
            "(C) Treatment of distributions from qualified disability trusts\n"
            "For purposes of this subsection, in the case of any child who is a beneficiary of a "
            "qualified disability trust (as defined in section 642(b)(2)(C)(ii)), any amount included "
            "in the income of such child under sections 652 and 662 during a taxable year shall be "
            "considered earned income of such child for such taxable year.\n"
            "(5) Special rules for determining parent to whom subsection applies\n"
            "For purposes of this subsection, the parent whose taxable income shall be taken into "
            "account shall be—\n"
            "(A) in the case of parents who are not married (within the meaning of section 7703), the "
            "custodial parent (within the meaning of section 152(e)) of the child, and\n"
            "(B) in the case of married individuals filing separately, the individual with the greater "
            "taxable income.\n"
            "(6) Providing of parent’s TIN\n"
            "The parent of any child to whom this subsection applies for any taxable year shall provide "
            "the TIN of such parent to such child and such child shall include such TIN on the child’s "
            "return of tax imposed by this section for such taxable year.\n"
            "(7) Election to claim certain unearned income of child on parent’s return\n"
            "(A) In general\n"
            "If—\n"
            "(i) any child to whom this subsection applies has gross income for the taxable year only "
            "from interest and dividends (including Alaska Permanent Fund dividends),\n"
            "(ii) such gross income is more than the amount described in paragraph (4)(A)(ii)(I) and "
            "less than 10 times the amount so described,\n"
            "(iii) no estimated tax payments for such year are made in the name and TIN of such child, "
            "and no amount has been deducted and withheld under section 3406, and\n"
            "(iv) the parent of such child (as determined under paragraph (5)) elects the application "
            "of subparagraph (B),\n"
            "such child shall be treated (other than for purposes of this paragraph) as having no gross "
            "income for such year and shall not be required to file a return under section 6012.\n"
            "(B) Income included on parent’s return\n"
            "In the case of a parent making the election under this paragraph—\n"
            "(i) the gross income of each child to whom such election applies (to the extent the gross "
            "income of such child exceeds twice the amount described in paragraph (4)(A)(ii)(I)) shall "
            "be included in such parent’s gross income for the taxable year,\n"
            "(ii) the tax imposed by this section for such year with respect to such parent shall be "
            "the amount equal to the sum of—\n"
            "(I) the amount determined under this section after the application of clause (i), plus\n"
            "(II) for each such child, 10 percent of the lesser of the amount described in paragraph "
            "(4)(A)(ii)(I) or the excess of the gross income of such child over the amount so "
            "described, and\n"
            "(iii) any interest which is an item of tax preference under section 57(a)(5) of the child "
            "shall be treated as an item of tax preference of such parent (and not of such child).\n"
            "(C) Regulations\n"
            "The Secretary shall prescribe such regulations as may be necessary or appropriate to carry "
            "out the purposes of this paragraph.\n"
            "(h) Maximum capital gains rate\n"
            "(1) In general\n"
            "If a taxpayer has a net capital gain for any taxable year, the tax imposed by this section "
            "for such taxable year shall not exceed the sum of—\n"
            "(A) a tax computed at the rates and in the same manner as if this subsection had not been "
            "enacted on the greater of—\n"
            "(i) taxable income reduced by the net capital gain; or\n"
            "(ii) the lesser of—\n"
            "(I) the amount of taxable income taxed at a rate below 25 percent; or\n"
            "(II) taxable income reduced by the adjusted net capital gain;\n"
            "(B) 0 percent of so much of the adjusted net capital gain (or, if less, taxable income) as "
            "does not exceed the excess (if any) of—\n"
            "(i) the amount of taxable income which would (without regard to this paragraph) be taxed "
            "at a rate below 25 percent, over\n"
            "(ii) the taxable income reduced by the adjusted net capital gain;\n"
            "(C) 15 percent of the lesser of—\n"
            "(i) so much of the adjusted net capital gain (or, if less, taxable income) as exceeds the "
            "amount on which a tax is determined under subparagraph (B), or\n"
            "(ii) the excess of—\n"
            "(I) the amount of taxable income which would (without regard to this paragraph) be taxed "
            "at a rate below 39.6 percent, over\n"
            "(II) the sum of the amounts on which a tax is determined under subparagraphs (A) and (B),\n"
            "(D) 20 percent of the adjusted net capital gain (or, if less, taxable income) in excess of "
            "the sum of the amounts on which tax is determined under subparagraphs (B) and (C),\n"
            "(E) 25 percent of the excess (if any) of—\n"
            "(i) the unrecaptured section 1250 gain (or, if less, the net capital gain (determined "
            "without regard to paragraph (11))), over\n"
            "(ii) the excess (if any) of—\n"
            "(I) the sum of the amount on which tax is determined under subparagraph (A) plus the net "
            "capital gain, over\n"
            "(II) taxable income; and\n"
            "(F) 28 percent of the amount of taxable income in excess of the sum of the amounts on "
            "which tax is determined under the preceding subparagraphs of this paragraph.\n"
            "(2) Net capital gain taken into account as investment income\n"
            "For purposes of this subsection, the net capital gain for any taxable year shall be "
            "reduced (but not below zero) by the amount which the taxpayer takes into account as "
            "investment income under section 163(d)(4)(B)(iii).\n"
            "(3) Adjusted net capital gain\n"
            "For purposes of this subsection, the term “adjusted net capital gain” means the sum of—\n"
            "(A) net capital gain (determined without regard to paragraph (11)) reduced (but not below "
            "zero) by the sum of—\n"
            "(i) unrecaptured section 1250 gain, and\n"
            "(ii) 28-percent rate gain, plus\n"
            "(B) qualified dividend income (as defined in paragraph (11)).\n"
            "(4) 28-percent rate gain\n"
            "For purposes of this subsection, the term “28-percent rate gain” means the excess (if any) "
            "of—\n"
            "(A) the sum of—\n"
            "(i) collectibles gain; and\n"
            "(ii) section 1202 gain, over\n"
            "(B) the sum of—\n"
            "(i) collectibles loss;\n"
            "(ii) the net short-term capital loss; and\n"
            "(iii) the amount of long-term capital loss carried under section 1212(b)(1)(B) to the "
            "taxable year.\n"
            "(5) Collectibles gain and loss\n"
            "For purposes of this subsection—\n"
            "(A) In general\n"
            "The terms “collectibles gain” and “collectibles loss” mean gain or loss (respectively) "
            "from the sale or exchange of a collectible (as defined in section 408(m) without regard to "
            "paragraph (3) thereof) which is a capital asset held for more than 1 year but only to the "
            "extent such gain is taken into account in computing gross income and such loss is taken "
            "into account in computing taxable income.\n"
            "(B) Partnerships, etc.\n"
            "For purposes of subparagraph (A), any gain from the sale of an interest in a partnership, "
            "S corporation, or trust which is attributable to unrealized appreciation in the value of "
            "collectibles shall be treated as gain from the sale or exchange of a collectible. Rules "
            "similar to the rules of section 751 shall apply for purposes of the preceding sentence.\n"
            "(6) Unrecaptured section 1250 gain\n"
            "For purposes of this subsection—\n"
            "(A) In general\n"
            "The term “unrecaptured section 1250 gain” means the excess (if any) of—\n"
            "(i) the amount of long-term capital gain (not otherwise treated as ordinary income) which "
            "would be treated as ordinary income if section 1250(b)(1) included all depreciation and "
            "the applicable percentage under section 1250(a) were 100 percent, over\n"
            "(ii) the excess (if any) of—\n"
            "(I) the amount described in paragraph (4)(B); over\n"
            "(II) the amount described in paragraph (4)(A).\n"
            "(B) Limitation with respect to section 1231 property\n"
            "The amount described in subparagraph (A)(i) from sales, exchanges, and conversions "
            "described in section 1231(a)(3)(A) for any taxable year shall not exceed the net section "
            "1231 gain (as defined in section 1231(c)(3)) for such year.\n"
            "(7) Section 1202 gain\n"
            "For purposes of this subsection, the term “section 1202 gain” means the excess of—\n"
            "(A) the gain which would be excluded from gross income under section 1202 but for the "
            "percentage limitation in section 1202(a), over\n"
            "(B) the gain excluded from gross income under section 1202.\n"
            "(8) Coordination with recapture of net ordinary losses under section 1231\n"
            "If any amount is treated as ordinary income under section 1231(c), such amount shall be "
            "allocated among the separate categories of net section 1231 gain (as defined in section "
            "1231(c)(3)) in such manner as the Secretary may by forms or regulations prescribe.\n"
            "(9) Regulations\n"
            "The Secretary may prescribe such regulations as are appropriate (including regulations "
            "requiring reporting) to apply this subsection in the case of sales and exchanges by "
            "pass-thru entities and of interests in such entities.\n"
            "(10) Pass-thru entity defined\n"
            "For purposes of this subsection, the term “pass-thru entity” means—\n"
            "(A) a regulated investment company;\n"
            "(B) a real estate investment trust;\n"
            "(C) an S corporation;\n"
            "(D) a partnership;\n"
            "(E) an estate or trust;\n"
            "(F) a common trust fund; and\n"
            "(G) a qualified electing fund (as defined in section 1295).\n"
            "(11) Dividends taxed as net capital gain\n"
            "(A) In general\n"
            "For purposes of this subsection, the term “net capital gain” means net capital gain "
            "(determined without regard to this paragraph) increased by qualified dividend income.\n"
            "(B) Qualified dividend income\n"
            "For purposes of this paragraph—\n"
            "(i) In general\n"
            "The term “qualified dividend income” means dividends received during the taxable year "
            "from—\n"
            "(I) domestic corporations, and\n"
            "(II) qualified foreign corporations.\n"
            "(ii) Certain dividends excluded\n"
            "Such term shall not include—\n"
            "(I) any dividend from a corporation which for the taxable year of the corporation in which "
            "the distribution is made, or the preceding taxable year, is a corporation exempt from tax "
            "under section 501 or 521,\n"
            "(II) any amount allowed as a deduction under section 591 (relating to deduction for "
            "dividends paid by mutual savings banks, etc.), and\n"
            "(III) any dividend described in section 404(k).\n"
            "(iii) Coordination with section 246(c)\n"
            "Such term shall not include any dividend on any share of stock—\n"
            "(I) with respect to which the holding period requirements of section 246(c) are not met "
            "(determined by substituting in section 246(c) “60 days” for “45 days” each place it "
            "appears and by substituting “121-day period” for “91-day period”), or\n"
            "(II) to the extent that the taxpayer is under an obligation (whether pursuant to a short "
            "sale or otherwise) to make related payments with respect to positions in substantially "
            "similar or related property.\n"
            "(C) Qualified foreign corporations\n"
            "(i) In general\n"
            "Except as otherwise provided in this paragraph, the term “qualified foreign corporation” "
            "means any foreign corporation if—\n"
            "(I) such corporation is incorporated in a possession of the United States, or\n"
            "(II) such corporation is eligible for benefits of a comprehensive income tax treaty with "
            "the United States which the Secretary determines is satisfactory for purposes of this "
            "paragraph and which includes an exchange of information program.\n"
            "(ii) Dividends on stock readily tradable on United States securities market\n"
            "A foreign corporation not otherwise treated as a qualified foreign corporation under "
            "clause (i) shall be so treated with respect to any dividend paid by such corporation if "
            "the stock with respect to which such dividend is paid is readily tradable on an "
            "established securities market in the United States.\n"
            "(iii) Exclusion of dividends of certain foreign corporations\n"
            "Such term shall not include—\n"
            "(I) any foreign corporation which for the taxable year of the corporation in which the "
            "dividend was paid, or the preceding taxable year, is a passive foreign investment company "
            "(as defined in section 1297), and\n"
            "(II) any corporation which first becomes a surrogate foreign corporation (as defined in "
            "section 7874(a)(2)(B)) after the date of the enactment of this subclause, other than a "
            "foreign corporation which is treated as a domestic corporation under section 7874(b).\n"
            "(iv) Coordination with foreign tax credit limitation\n"
            "Rules similar to the rules of section 904(b)(2)(B) shall apply with respect to the "
            "dividend rate differential under this paragraph.\n"
            "(D) Special rules\n"
            "(i) Amounts taken into account as investment income\n"
            "Qualified dividend income shall not include any amount which the taxpayer takes into "
            "account as investment income under section 163(d)(4)(B).\n"
            "(ii) Extraordinary dividends\n"
            "If a taxpayer to whom this section applies receives, with respect to any share of stock, "
            "qualified dividend income from 1 or more dividends which are extraordinary dividends "
            "(within the meaning of section 1059(c)), any loss on the sale or exchange of such share "
            "shall, to the extent of such dividends, be treated as long-term capital loss.\n"
            "(iii) Treatment of dividends from regulated investment companies and real estate "
            "investment trusts\n"
            "A dividend received from a regulated investment company or a real estate investment trust "
            "shall be subject to the limitations prescribed in sections 854 and 857.\n"
            "(i) Rate reductions after 2000\n"
            "(1) 10-percent rate bracket\n"
            "(A) In general\n"
            "In the case of taxable years beginning after December 31, 2000—\n"
            "(i) the rate of tax under subsections (a), (b), (c), and (d) on taxable income not over "
            "the initial bracket amount shall be 10 percent, and\n"
            "(ii) the 15 percent rate of tax shall apply only to taxable income over the initial "
            "bracket amount but not over the maximum dollar amount for the 15-percent rate bracket.\n"
            "(B) Initial bracket amount\n"
            "For purposes of this paragraph, the initial bracket amount is—\n"
            "(i) $14,000 in the case of subsection (a),\n"
            "(ii) $10,000 in the case of subsection (b), and\n"
            "(iii) ½ the amount applicable under clause (i) (after adjustment, if any, under "
            "subparagraph (C)) in the case of subsections (c) and (d).\n"
            "(C) Inflation adjustment\n"
            "In prescribing the tables under subsection (f) which apply with respect to taxable years "
            "beginning in calendar years after 2003—\n"
            "(i) the cost-of-living adjustment shall be determined under subsection (f)(3) by "
            "substituting “2002” for “2016” in subparagraph (A)(ii) thereof, and\n"
            "(ii) the adjustments under clause (i) shall not apply to the amount referred to in "
            "subparagraph (B)(iii).\n"
            "If any amount after adjustment under the preceding sentence is not a multiple of $50, such "
            "amount shall be rounded to the next lowest multiple of $50.\n"
            "(2) 25-, 28-, and 33-percent rate brackets\n"
            "The tables under subsections (a), (b), (c), (d), and (e) shall be applied—\n"
            "(A) by substituting “25%” for “28%” each place it appears (before the application of "
            "subparagraph (B)),\n"
            "(B) by substituting “28%” for “31%” each place it appears, and\n"
            "(C) by substituting “33%” for “36%” each place it appears.\n"
            "(3) Modifications to income tax brackets for high-income taxpayers\n"
            "(A) 35-percent rate bracket\n"
            "In the case of taxable years beginning after December 31, 2012—\n"
            "(i) the rate of tax under subsections (a), (b), (c), and (d) on a taxpayer’s taxable "
            "income in the highest rate bracket shall be 35 percent to the extent such income does not "
            "exceed an amount equal to the excess of—\n"
            "(I) the applicable threshold, over\n"
            "(II) the dollar amount at which such bracket begins, and\n"
            "(ii) the 39.6 percent rate of tax under such subsections shall apply only to the "
            "taxpayer’s taxable income in such bracket in excess of the amount to which clause (i) "
            "applies.\n"
            "(B) Applicable threshold\n"
            "For purposes of this paragraph, the term “applicable threshold” means—\n"
            "(i) $450,000 in the case of subsection (a),\n"
            "(ii) $425,000 in the case of subsection (b),\n"
            "(iii) $400,000 in the case of subsection (c), and\n"
            "(iv) ½ the amount applicable under clause (i) (after adjustment, if any, under "
            "subparagraph (C)) in the case of subsection (d).\n"
            "(C) Inflation adjustment\n"
            "For purposes of this paragraph, with respect to taxable years beginning in calendar years "
            "after 2013, each of the dollar amounts under clauses (i), (ii), and (iii) of subparagraph "
            "(B) shall be adjusted in the same manner as under paragraph (1)(C)(i), except that "
            "subsection (f)(3)(A)(ii) shall be applied by substituting “2012” for “2016”.\n"
            "(4) Adjustment of tables\n"
            "The Secretary shall adjust the tables prescribed under subsection (f) to carry out this "
            "subsection.\n"
            "(j) Modifications for taxable years beginning after 2017\n"
            "(1) In general\n"
            "In the case of a taxable year beginning after December 31, 2017—\n"
            "(A) subsection (i) shall not apply, and\n"
            "(B) this section (other than subsection (i)) shall be applied as provided in paragraphs "
            "(2) through (6).\n"
            "(2) Rate tables\n"
            "(A) Married individuals filing joint returns and surviving spouses\n"
            "The following table shall be applied in lieu of the table contained in subsection (a):\n"
            "If taxable income is: | The tax is:\n"
            "Not over $19,050 | 10% of taxable income.\n"
            "Over $19,050 but not over $77,400 | $1,905, plus 12% of the excess over $19,050.\n"
            "Over $77,400 but not over $165,000 | $8,907, plus 22% of the excess over $77,400.\n"
            "Over $165,000 but not over $315,000 | $28,179, plus 24% of the excess over $165,000.\n"
            "Over $315,000 but not over $400,000 | $64,179, plus 32% of the excess over $315,000.\n"
            "Over $400,000 but not over $600,000 | $91,379, plus 35% of the excess over $400,000.\n"
            "Over $600,000 | $161,379, plus 37% of the excess over $600,000.\n"
            "(B) Heads of households\n"
            "The following table shall be applied in lieu of the table contained in subsection (b):\n"
            "If taxable income is: | The tax is:\n"
            "Not over $13,600 | 10% of taxable income.\n"
            "Over $13,600 but not over $51,800 | $1,360, plus 12% of the excess over $13,600.\n"
            "Over $51,800 but not over $82,500 | $5,944, plus 22% of the excess over $51,800.\n"
            "Over $82,500 but not over $157,500 | $12,698, plus 24% of the excess over $82,500.\n"
            "Over $157,500 but not over $200,000 | $30,698, plus 32% of the excess over $157,500.\n"
            "Over $200,000 but not over $500,000 | $44,298, plus 35% of the excess over $200,000.\n"
            "Over $500,000 | $149,298, plus 37% of the excess over $500,000.\n"
            "(C) Unmarried individuals other than surviving spouses and heads of households\n"
            "The following table shall be applied in lieu of the table contained in subsection (c):\n"
            "If taxable income is: | The tax is:\n"
            "Not over $9,525 | 10% of taxable income.\n"
            "Over $9,525 but not over $38,700 | $952.50, plus 12% of the excess over $9,525.\n"
            "Over $38,700 but not over $82,500 | $4,453.50, plus 22% of the excess over $38,700.\n"
            "Over $82,500 but not over $157,500 | $14,089.50, plus 24% of the excess over $82,500.\n"
            "Over $157,500 but not over $200,000 | $32,089.50, plus 32% of the excess over $157,500.\n"
            "Over $200,000 but not over $500,000 | $45,689.50, plus 35% of the excess over $200,000.\n"
            "Over $500,000 | $150,689.50, plus 37% of the excess over $500,000.\n"
            "(D) Married individuals filing separate returns\n"
            "The following table shall be applied in lieu of the table contained in subsection (d):\n"
            "If taxable income is: | The tax is:\n"
            "Not over $9,525 | 10% of taxable income.\n"
            "Over $9,525 but not over $38,700 | $952.50, plus 12% of the excess over $9,525.\n"
            "Over $38,700 but not over $82,500 | $4,453.50, plus 22% of the excess over $38,700.\n"
            "Over $82,500 but not over $157,500 | $14,089.50, plus 24% of the excess over $82,500.\n"
            "Over $157,500 but not over $200,000 | $32,089.50, plus 32% of the excess over $157,500.\n"
            "Over $200,000 but not over $300,000 | $45,689.50, plus 35% of the excess over $200,000.\n"
            "Over $300,000 | $80,689.50, plus 37% of the excess over $300,000.\n"
            "(E) Estates and trusts\n"
            "The following table shall be applied in lieu of the table contained in subsection (e):\n"
            "If taxable income is: | The tax is:\n"
            "Not over $2,550 | 10% of taxable income.\n"
            "Over $2,550 but not over $9,150 | $255, plus 24% of the excess over $2,550.\n"
            "Over $9,150 but not over $12,500 | $1,839, plus 35% of the excess over $9,150.\n"
            "Over $12,500 | $3,011.50, plus 37% of the excess over $12,500.\n"
            "(F) References to rate tables\n"
            "Any reference in this title to a rate of tax under subsection (c) shall be treated as a "
            "reference to the corresponding rate bracket under subparagraph (C) of this paragraph, "
            "except that the reference in section 3402(q)(1) to the third lowest rate of tax applicable "
            "under subsection (c) shall be treated as a reference to the fourth lowest rate of tax "
            "under subparagraph (C).\n"
            "(3) Adjustments\n"
            "(A) No adjustment in 2018\n"
            "The tables contained in paragraph (2) shall apply without adjustment for taxable years "
            "beginning after December 31, 2017, and before January 1, 2019.\n"
            "(B) Subsequent years\n"
            "For taxable years beginning after December 31, 2018, the Secretary shall prescribe tables "
            "which shall apply in lieu of the tables contained in paragraph (2) in the same manner as "
            "under paragraphs (1) and (2) of subsection (f) (applied without regard to clauses (i) and "
            "(ii) of subsection (f)(2)(A)), except that in prescribing such tables—\n"
            "(i) solely for purposes of determining the dollar amounts at which any rate bracket higher "
            "than 12 percent ends and at which any rate bracket higher than 22 percent begins, "
            "subsection (f)(3) shall be applied by substituting “calendar year 2017” for “calendar year "
            "2016” in subparagraph (A)(ii) thereof,\n"
            "(ii) subsection (f)(7)(B) shall apply to any unmarried individual other than a surviving "
            "spouse or head of household, and\n"
            "(iii) subsection (f)(8) shall not apply.\n"
            "[(4) Repealed. Pub. L. 116–94, div. O, title V, § 501(a), Dec. 20, 2019, 133 Stat. 3180]\n"
            "(5) Application of current income tax brackets to capital gains brackets\n"
            "(A) In general\n"
            "Section 1(h)(1) shall be applied—\n"
            "(i) by substituting “below the maximum zero rate amount” for “which would (without regard "
            "to this paragraph) be taxed at a rate below 25 percent” in subparagraph (B)(i), and\n"
            "(ii) by substituting “below the maximum 15-percent rate amount” for “which would (without "
            "regard to this paragraph) be taxed at a rate below 39.6 percent” in subparagraph "
            "(C)(ii)(I).\n"
            "(B) Maximum amounts defined\n"
            "For purposes of applying section 1(h) with the modifications described in subparagraph "
            "(A)—\n"
            "(i) Maximum zero rate amount\n"
            "The maximum zero rate amount shall be—\n"
            "(I) in the case of a joint return or surviving spouse, $77,200,\n"
            "(II) in the case of an individual who is a head of household (as defined in section 2(b)), "
            "$51,700,\n"
            "(III) in the case of any other individual (other than an estate or trust), an amount equal "
            "to ½ of the amount in effect for the taxable year under subclause (I), and\n"
            "(IV) in the case of an estate or trust, $2,600.\n"
            "(ii) Maximum 15-percent rate amount\n"
            "The maximum 15-percent rate amount shall be—\n"
            "(I) in the case of a joint return or surviving spouse, $479,000 (½ such amount in the case "
            "of a married individual filing a separate return),\n"
            "(II) in the case of an individual who is the head of a household (as defined in section "
            "2(b)), $452,400,\n"
            "(III) in the case of any other individual (other than an estate or trust), $425,800, and\n"
            "(IV) in the case of an estate or trust, $12,700.\n"
            "(C) Inflation adjustment\n"
            "In the case of any taxable year beginning after 2018, each of the dollar amounts in "
            "clauses (i) and (ii) of subparagraph (B) shall be increased by an amount equal to—\n"
            "(i) such dollar amount, multiplied by\n"
            "(ii) the cost-of-living adjustment determined under subsection (f)(3) for the calendar "
            "year in which the taxable year begins, determined by substituting “calendar year 2017” for "
            "“calendar year 2016” in subparagraph (A)(ii) thereof.\n"
            "If any increase under this subparagraph is not a multiple of $50, such increase shall be "
            "rounded to the next lowest multiple of $50.\n"
            "(6) Section 15 not to apply\n"
            "Section 15 shall not apply to any change in a rate of tax by reason of this subsection."
        ),
        text_es=(
            "Resumen en español; el texto oficial está en inglés. Impone el impuesto sobre el ingreso "
            "tributable de individuos, caudales y fideicomisos mediante tablas según el estado civil: "
            "casados que rinden conjuntamente y cónyuges supérstites (a), jefes de familia (b), "
            "solteros (c), casados que rinden por separado (d) y caudales y fideicomisos (e). Incluye "
            "los ajustes por inflación (f), el impuesto sobre ingreso no devengado de ciertos menores "
            "(g), las tasas máximas para ganancias de capital y dividendos calificados (h) y las "
            "modificaciones para años contributivos que comienzan después de 2017 (j)."
        ),
        effective_year=1954,
        source_url=USLM_TITLE26_SOURCE_URL,
        cross_references=["26 U.S.C. § 63", "26 U.S.C. § 2"],
        breadcrumbs="Title 26 > Subtitle A > Chapter 1 > Subchapter A > Part I > § 1 (OLRC release point 119-121)",
    ),
    StatuteRecord(
        id="us-irc-24",
        jurisdiction="US",
        corpus="IRC",
        citation="26 U.S.C. § 24",
        section="24",
        title="Child tax credit / Crédito por hijos",
        text_en=(
            "(a) Allowance of credit\n"
            "There shall be allowed as a credit against the tax imposed by this chapter for the taxable "
            "year with respect to each qualifying child of the taxpayer for which the taxpayer is "
            "allowed a deduction under section 151 an amount equal to $1,000.\n"
            "(b) Limitations\n"
            "(1) Limitation based on adjusted gross income\n"
            "The amount of the credit allowable under subsection (a) shall be reduced (but not below "
            "zero) by $50 for each $1,000 (or fraction thereof) by which the taxpayer’s modified "
            "adjusted gross income exceeds the threshold amount. For purposes of the preceding "
            "sentence, the term “modified adjusted gross income” means adjusted gross income increased "
            "by any amount excluded from gross income under section 911, 931, or 933.\n"
            "(2) Threshold amount\n"
            "For purposes of paragraph (1), the term “threshold amount” means—\n"
            "(A) $110,000 in the case of a joint return,\n"
            "(B) $75,000 in the case of an individual who is not married, and\n"
            "(C) $55,000 in the case of a married individual filing a separate return.\n"
            "For purposes of this paragraph, marital status shall be determined under section 7703.\n"
            "(c) Qualifying child\n"
            "For purposes of this section—\n"
            "(1) In general\n"
            "The term “qualifying child” means a qualifying child of the taxpayer (as defined in "
            "section 152(c)) who has not attained age 17.\n"
            "(2) Exception for certain noncitizens\n"
            "The term “qualifying child” shall not include any individual who would not be a dependent "
            "if subparagraph (A) of section 152(b)(3) were applied without regard to all that follows "
            "“resident of the United States”.\n"
            "(d) Portion of credit refundable\n"
            "(1) In general\n"
            "The aggregate credits allowed to a taxpayer under subpart C shall be increased by the "
            "lesser of—\n"
            "(A) the credit which would be allowed under this section without regard to this subsection "
            "and the limitation under section 26(a) or\n"
            "(B) the amount by which the aggregate amount of credits allowed by this subpart "
            "(determined without regard to this subsection) would increase if the limitation imposed by "
            "section 26(a) were increased by the greater of—\n"
            "(i) 15 percent of so much of the taxpayer’s earned income (within the meaning of section "
            "32) which is taken into account in computing taxable income for the taxable year as "
            "exceeds $3,000, or\n"
            "(ii) in the case of a taxpayer with 3 or more qualifying children, the excess (if any) of—\n"
            "(I) the taxpayer’s social security taxes for the taxable year, over\n"
            "(II) the credit allowed under section 32 for the taxable year.\n"
            "The amount of the credit allowed under this subsection shall not be treated as a credit "
            "allowed under this subpart and shall reduce the amount of credit otherwise allowable under "
            "subsection (a) without regard to section 26(a). For purposes of subparagraph (B), any "
            "amount excluded from gross income by reason of section 112 shall be treated as earned "
            "income which is taken into account in computing taxable income for the taxable year.\n"
            "(2) Social security taxes\n"
            "For purposes of paragraph (1)—\n"
            "(A) In general\n"
            "The term “social security taxes” means, with respect to any taxpayer for any taxable year—\n"
            "(i) the amount of the taxes imposed by sections 3101 and 3201(a) on amounts received by "
            "the taxpayer during the calendar year in which the taxable year begins,\n"
            "(ii) 50 percent of the taxes imposed by section 1401 on the self-employment income of the "
            "taxpayer for the taxable year, and\n"
            "(iii) 50 percent of the taxes imposed by section 3211(a) on amounts received by the "
            "taxpayer during the calendar year in which the taxable year begins.\n"
            "(B) Coordination with special refund of social security taxes\n"
            "The term “social security taxes” shall not include any taxes to the extent the taxpayer is "
            "entitled to a special refund of such taxes under section 6413(c).\n"
            "(C) Special rule\n"
            "Any amounts paid pursuant to an agreement under section 3121(l) (relating to agreements "
            "entered into by American employers with respect to foreign affiliates) which are "
            "equivalent to the taxes referred to in subparagraph (A)(i) shall be treated as taxes "
            "referred to in such subparagraph.\n"
            "(3) Exception for taxpayers excluding foreign earned income\n"
            "Paragraph (1) shall not apply to any taxpayer for any taxable year if such taxpayer elects "
            "to exclude any amount from gross income under section 911 for such taxable year.\n"
            "(e) Identification requirements\n"
            "(1) Qualifying child identification requirement\n"
            "No credit shall be allowed under this section to a taxpayer with respect to any qualifying "
            "child unless the taxpayer includes the name and taxpayer identification number of such "
            "qualifying child on the return of tax for the taxable year and such taxpayer "
            "identification number was issued on or before the due date for filing such return.\n"
            "(2) Taxpayer identification requirement\n"
            "No credit shall be allowed under this section if the taxpayer identification number of the "
            "taxpayer was issued after the due date for filing the return for the taxable year.\n"
            "(f) Taxable year must be full taxable year\n"
            "Except in the case of a taxable year closed by reason of the death of the taxpayer, no "
            "credit shall be allowable under this section in the case of a taxable year covering a "
            "period of less than 12 months.\n"
            "(g) Restrictions on taxpayers who improperly claimed credit in prior year\n"
            "(1) Taxpayers making prior fraudulent or reckless claims\n"
            "(A) In general\n"
            "No credit shall be allowed under this section for any taxable year in the disallowance "
            "period.\n"
            "(B) Disallowance period\n"
            "For purposes of subparagraph (A), the disallowance period is—\n"
            "(i) the period of 10 taxable years after the most recent taxable year for which there was "
            "a final determination that the taxpayer’s claim of credit under this section was due to "
            "fraud, and\n"
            "(ii) the period of 2 taxable years after the most recent taxable year for which there was "
            "a final determination that the taxpayer’s claim of credit under this section was due to "
            "reckless or intentional disregard of rules and regulations (but not due to fraud).\n"
            "(2) Taxpayers making improper prior claims\n"
            "In the case of a taxpayer who is denied credit under this section for any taxable year as "
            "a result of the deficiency procedures under subchapter B of chapter 63, no credit shall be "
            "allowed under this section for any subsequent taxable year unless the taxpayer provides "
            "such information as the Secretary may require to demonstrate eligibility for such credit.\n"
            "(h) Special rules for taxable years beginning after 2017\n"
            "(1) In general\n"
            "In the case of a taxable year beginning after December 31, 2017, this section shall be "
            "applied as provided in paragraphs (2) through (7).\n"
            "(2) Credit amount\n"
            "Subsection (a) shall be applied by substituting “$2,200” for “$1,000”.\n"
            "(3) Limitation\n"
            "In lieu of the amount determined under subsection (b)(2), the threshold amount shall be "
            "$400,000 in the case of a joint return ($200,000 in any other case).\n"
            "(4) Partial credit allowed for certain other dependents\n"
            "(A) In general\n"
            "The credit determined under subsection (a) (after the application of paragraph (2)) shall "
            "be increased by $500 for each dependent of the taxpayer (as defined in section 152) other "
            "than a qualifying child described in subsection (c).\n"
            "(B) Exception for certain noncitizens\n"
            "Subparagraph (A) shall not apply with respect to any individual who would not be a "
            "dependent if subparagraph (A) of section 152(b)(3) were applied without regard to all that "
            "follows “resident of the United States”.\n"
            "(C) Certain qualifying children\n"
            "In the case of any qualifying child with respect to whom a credit is not allowed under "
            "this section by reason of paragraph (7), such child shall be treated as a dependent to "
            "whom subparagraph (A) applies.\n"
            "(5) Maximum amount of refundable credit\n"
            "The amount determined under subsection (d)(1)(A) with respect to any qualifying child "
            "shall not exceed $1,400, and such subsection shall be applied without regard to paragraph "
            "(4) of this subsection.\n"
            "(6) Earned income threshold for refundable credit\n"
            "Subsection (d)(1)(B)(i) shall be applied by substituting “$2,500” for “$3,000”.\n"
            "(7) Social security number required\n"
            "(A) In general\n"
            "No credit shall be allowed under this section to a taxpayer with respect to any qualifying "
            "child unless the taxpayer includes on the return of tax for the taxable year—\n"
            "(i) the taxpayer’s social security number (or, in the case of a joint return, the social "
            "security number of at least 1 spouse), and\n"
            "(ii) the social security number of such qualifying child.\n"
            "(B) Social security number\n"
            "For purposes of this paragraph, the term “social security number” means a social security "
            "number issued to an individual by the Social Security Administration, but only if the "
            "social security number is issued—\n"
            "(i) to a citizen of the United States or pursuant to subclause (I) (or that portion of "
            "subclause (III) that relates to subclause (I)) of section 205(c)(2)(B)(i) of the Social "
            "Security Act, and\n"
            "(ii) before the due date for such return.\n"
            "(i) Inflation adjustments\n"
            "(1) Maximum amount of refundable credit\n"
            "In the case of a taxable year beginning after 2024, the $1,400 amount in subsection (h)(5) "
            "shall be increased by an amount equal to—\n"
            "(A) such dollar amount, multiplied by\n"
            "(B) the cost-of-living adjustment determined under section 1(f)(3) for the calendar year "
            "in which the taxable year begins, determined by substituting “2017” for “2016” in "
            "subparagraph (A)(ii) thereof.\n"
            "(2) Special rule for adjustment of credit amount\n"
            "In the case of a taxable year beginning after 2025, the $2,200 amount in subsection (h)(2) "
            "shall be increased by an amount equal to—\n"
            "(A) such dollar amount, multiplied by\n"
            "(B) the cost-of-living adjustment determined under section 1(f)(3) for the calendar year "
            "in which the taxable year begins, determined by substituting “2024” for “2016” in "
            "subparagraph (A)(ii) thereof.\n"
            "(3) Rounding\n"
            "If any increase under this subsection is not a multiple of $100, such increase shall be "
            "rounded to the next lowest multiple of $100.\n"
            "(j) Reconciliation of credit and advance credit\n"
            "(1) In general\n"
            "The amount of the credit allowed under this section to any taxpayer for any taxable year "
            "shall be reduced (but not below zero) by the aggregate amount of payments made under "
            "section 7527A to such taxpayer during such taxable year. Any failure to so reduce the "
            "credit shall be treated as arising out of a mathematical or clerical error and assessed "
            "according to section 6213(b)(1).\n"
            "(2) Excess advance payments\n"
            "(A) In general\n"
            "If the aggregate amount of payments under section 7527A to the taxpayer during the taxable "
            "year exceeds the amount of the credit allowed under this section to such taxpayer for such "
            "taxable year (determined without regard to paragraph (1)), the tax imposed by this chapter "
            "for such taxable year shall be increased by the amount of such excess. Any failure to so "
            "increase the tax shall be treated as arising out of a mathematical or clerical error and "
            "assessed according to section 6213(b)(1).\n"
            "(B) Safe harbor based on modified adjusted gross income\n"
            "(i) In general\n"
            "In the case of a taxpayer whose modified adjusted gross income (as defined in subsection "
            "(b)) for the taxable year does not exceed 200 percent of the applicable income threshold, "
            "the amount of the increase determined under subparagraph (A) with respect to such taxpayer "
            "for such taxable year shall be reduced (but not below zero) by the safe harbor amount.\n"
            "(ii) Phase out of safe harbor amount\n"
            "In the case of a taxpayer whose modified adjusted gross income (as defined in subsection "
            "(b)) for the taxable year exceeds the applicable income threshold, the safe harbor amount "
            "otherwise in effect under clause (i) shall be reduced by the amount which bears the same "
            "ratio to such amount as such excess bears to the applicable income threshold.\n"
            "(iii) Applicable income threshold\n"
            "For purposes of this subparagraph, the term “applicable income threshold” means—\n"
            "(I) $60,000 in the case of a joint return or surviving spouse (as defined in section "
            "2(a)),\n"
            "(II) $50,000 in the case of a head of household, and\n"
            "(III) $40,000 in any other case.\n"
            "(iv) Safe harbor amount\n"
            "For purposes of this subparagraph, the term “safe harbor amount” means, with respect to "
            "any taxable year, the product of—\n"
            "(I) $2,000, multiplied by\n"
            "(II) the excess (if any) of the number of qualified children taken into account in "
            "determining the annual advance amount with respect to the taxpayer under section 7527A "
            "with respect to months beginning in such taxable year, over the number of qualified "
            "children taken into account in determining the credit allowed under this section for such "
            "taxable year.\n"
            "(k) Application of credit in possessions\n"
            "(1) Mirror code possessions\n"
            "(A) In general\n"
            "The Secretary shall pay to each possession of the United States with a mirror code tax "
            "system amounts equal to the loss (if any) to that possession by reason of the application "
            "of this section (determined without regard to this subsection) with respect to taxable "
            "years beginning after 2020. Such amounts shall be determined by the Secretary based on "
            "information provided by the government of the respective possession.\n"
            "(B) Coordination with credit allowed against United States income taxes\n"
            "No credit shall be allowed under this section for any taxable year to any individual to "
            "whom a credit is allowable against taxes imposed by a possession of the United States with "
            "a mirror code tax system by reason of the application of this section in such possession "
            "for such taxable year.\n"
            "(C) Mirror code tax system\n"
            "For purposes of this paragraph, the term “mirror code tax system” means, with respect to "
            "any possession of the United States, the income tax system of such possession if the "
            "income tax liability of the residents of such possession under such system is determined "
            "by reference to the income tax laws of the United States as if such possession were the "
            "United States.\n"
            "(2) Puerto Rico\n"
            "(A) Application to taxable years in 2021\n"
            "(i) For application of refundable credit to residents of Puerto Rico, see subsection "
            "(i)(1).\n"
            "(ii) For nonapplication of advance payment to residents of Puerto Rico, see section "
            "7527A(e)(4)(A).\n"
            "(B) Application to taxable years after 2021\n"
            "In the case of any bona fide resident of Puerto Rico (within the meaning of section "
            "937(a)) for any taxable year beginning after December 31, 2021—\n"
            "(i) the credit determined under this section shall be allowable to such resident, and\n"
            "(ii) subsection (d)(1)(B)(ii) shall be applied without regard to the phrase “in the case "
            "of a taxpayer with 3 or more qualifying children”.\n"
            "(3) American Samoa\n"
            "(A) In general\n"
            "The Secretary shall pay to American Samoa amounts estimated by the Secretary as being "
            "equal to the aggregate benefits that would have been provided to residents of American "
            "Samoa by reason of the application of this section for taxable years beginning after 2020 "
            "if the provisions of this section had been in effect in American Samoa (applied as if "
            "American Samoa were the United States and without regard to the application of this "
            "section to bona fide residents of Puerto Rico under subsection (i)(1)).\n"
            "(B) Distribution requirement\n"
            "Subparagraph (A) shall not apply unless American Samoa has a plan, which has been approved "
            "by the Secretary, under which American Samoa will promptly distribute such payments to its "
            "residents.\n"
            "(C) Coordination with credit allowed against United States income taxes\n"
            "(i) In general\n"
            "In the case of a taxable year with respect to which a plan is approved under subparagraph "
            "(B), this section (other than this subsection) shall not apply to any individual eligible "
            "for a distribution under such plan.\n"
            "(ii) Application of section in event of absence of approved plan\n"
            "In the case of a taxable year with respect to which a plan is not approved under "
            "subparagraph (B)—\n"
            "(I) if such taxable year begins in 2021, subsection (i)(1) shall be applied by "
            "substituting “bona fide resident of Puerto Rico or American Samoa” for “bona fide resident "
            "of Puerto Rico”, and\n"
            "(II) if such taxable year begins after December 31, 2021, rules similar to the rules of "
            "paragraph (2)(B) shall apply with respect to bona fide residents of American Samoa (within "
            "the meaning of section 937(a)).\n"
            "(4) Treatment of payments\n"
            "For purposes of section 1324 of title 31, United States Code, the payments under this "
            "subsection shall be treated in the same manner as a refund due from a credit provision "
            "referred to in subsection (b)(2) of such section."
        ),
        text_es=(
            "Resumen en español; el texto oficial está en inglés. Concede un crédito contra el impuesto "
            "por cada hijo calificado (a), sujeto a límites según el ingreso bruto ajustado modificado "
            "(b), con una porción reembolsable (d) y requisitos de número de identificación (e). La "
            "subsección (h) contiene las reglas especiales para años contributivos que comienzan "
            "después de 2017, la (i) los ajustes por inflación, la (j) la reconciliación con pagos por "
            "adelantado y la (k) la aplicación del crédito en las posesiones, incluido Puerto Rico."
        ),
        effective_year=1997,
        source_url=USLM_TITLE26_SOURCE_URL,
        cross_references=["26 U.S.C. § 152", "26 U.S.C. § 32"],
        breadcrumbs="Title 26 > Subtitle A > Chapter 1 > Subchapter A > Part IV > Subpart A > § 24 (OLRC release point 119-121)",
    ),
    StatuteRecord(
        id="us-irc-32",
        jurisdiction="US",
        corpus="IRC",
        citation="26 U.S.C. § 32",
        section="32",
        title="Earned income / Crédito por ingreso devengado",
        text_en=(
            "(a) Allowance of credit\n"
            "(1) In general\n"
            "In the case of an eligible individual, there shall be allowed as a credit against the tax "
            "imposed by this subtitle for the taxable year an amount equal to the credit percentage of "
            "so much of the taxpayer’s earned income for the taxable year as does not exceed the earned "
            "income amount.\n"
            "(2) Limitation\n"
            "The amount of the credit allowable to a taxpayer under paragraph (1) for any taxable year "
            "shall not exceed the excess (if any) of—\n"
            "(A) the credit percentage of the earned income amount, over\n"
            "(B) the phaseout percentage of so much of the adjusted gross income (or, if greater, the "
            "earned income) of the taxpayer for the taxable year as exceeds the phaseout amount.\n"
            "(b) Percentages and amounts\n"
            "For purposes of subsection (a)—\n"
            "(1) Percentages\n"
            "The credit percentage and the phaseout percentage shall be determined as follows:\n"
            "In the case of an eligible individual with: | The credit percentage is: | The phaseout "
            "percentage is:\n"
            "1 qualifying child | 34 | 15.98\n"
            "2 qualifying children | 40 | 21.06\n"
            "3 or more qualifying children | 45 | 21.06\n"
            "No qualifying children | 7.65 | 7.65\n"
            "(2) Amounts\n"
            "(A) In general\n"
            "Subject to subparagraph (B), the earned income amount and the phaseout amount shall be "
            "determined as follows:\n"
            "In the case of an eligible individual with: | The earned income amount is: | The phaseout "
            "amount is:\n"
            "1 qualifying child | $6,330 | $11,610\n"
            "2 or more qualifying children | $8,890 | $11,610\n"
            "No qualifying children | $4,220 | $5,280\n"
            "(B) Joint returns\n"
            "In the case of a joint return filed by an eligible individual and such individual’s "
            "spouse, the phaseout amount determined under subparagraph (A) shall be increased by "
            "$5,000.\n"
            "(c) Definitions and special rules\n"
            "For purposes of this section—\n"
            "(1) Eligible individual\n"
            "(A) In general\n"
            "The term “eligible individual” means—\n"
            "(i) any individual who has a qualifying child for the taxable year, or\n"
            "(ii) any other individual who does not have a qualifying child for the taxable year, if—\n"
            "(I) such individual’s principal place of abode is in the United States for more than "
            "one-half of such taxable year,\n"
            "(II) such individual (or, if the individual is married, either the individual or the "
            "individual’s spouse) has attained age 25 but not attained age 65 before the close of the "
            "taxable year, and\n"
            "(III) such individual is not a dependent for whom a deduction is allowable under section "
            "151 to another taxpayer for any taxable year beginning in the same calendar year as such "
            "taxable year.\n"
            "(B) Qualifying child ineligible\n"
            "If an individual is the qualifying child of a taxpayer for any taxable year of such "
            "taxpayer beginning in a calendar year, such individual shall not be treated as an eligible "
            "individual for any taxable year of such individual beginning in such calendar year.\n"
            "(C) Exception for individual claiming benefits under section 911\n"
            "The term “eligible individual” does not include any individual who claims the benefits of "
            "section 911 (relating to citizens or residents living abroad) for the taxable year.\n"
            "(D) Limitation on eligibility of nonresident aliens\n"
            "The term “eligible individual” shall not include any individual who is a nonresident alien "
            "individual for any portion of the taxable year unless such individual is treated for such "
            "taxable year as a resident of the United States for purposes of this chapter by reason of "
            "an election under subsection (g) or (h) of section 6013.\n"
            "(E) Identification number requirement\n"
            "No credit shall be allowed under this section to an eligible individual who does not "
            "include on the return of tax for the taxable year—\n"
            "(i) such individual’s taxpayer identification number, and\n"
            "(ii) if the individual is married, the taxpayer identification number of such individual’s "
            "spouse.\n"
            "(2) Earned income\n"
            "(A) The term “earned income” means—\n"
            "(i) wages, salaries, tips, and other employee compensation, but only if such amounts are "
            "includible in gross income for the taxable year, plus\n"
            "(ii) the amount of the taxpayer’s net earnings from self-employment for the taxable year "
            "(within the meaning of section 1402(a)), but such net earnings shall be determined with "
            "regard to the deduction allowed to the taxpayer by section 164(f).\n"
            "(B) For purposes of subparagraph (A)—\n"
            "(i) the earned income of an individual shall be computed without regard to any community "
            "property laws,\n"
            "(ii) no amount received as a pension or annuity shall be taken into account,\n"
            "(iii) no amount to which section 871(a) applies (relating to income of nonresident alien "
            "individuals not connected with United States business) shall be taken into account,\n"
            "(iv) no amount received for services provided by an individual while the individual is an "
            "inmate at a penal institution shall be taken into account,\n"
            "(v) no amount described in subparagraph (A) received for service performed in work "
            "activities as defined in paragraph (4) or (7) of section 407(d) of the Social Security Act "
            "to which the taxpayer is assigned under any State program under part A of title IV of such "
            "Act shall be taken into account, but only to the extent such amount is subsidized under "
            "such State program, and\n"
            "(vi) a taxpayer may elect to treat amounts excluded from gross income by reason of section "
            "112 as earned income.\n"
            "(3) Qualifying child\n"
            "(A) In general\n"
            "The term “qualifying child” means a qualifying child of the taxpayer (as defined in "
            "section 152(c), determined without regard to paragraph (1)(D) thereof and section 152(e)).\n"
            "(B) Married individual\n"
            "The term “qualifying child” shall not include an individual who is married as of the close "
            "of the taxpayer’s taxable year unless the taxpayer is entitled to a deduction under "
            "section 151 for such taxable year with respect to such individual (or would be so entitled "
            "but for section 152(e)).\n"
            "(C) Place of abode\n"
            "For purposes of subparagraph (A), the requirements of section 152(c)(1)(B) shall be met "
            "only if the principal place of abode is in the United States.\n"
            "(D) Identification requirements\n"
            "(i) In general\n"
            "A qualifying child shall not be taken into account under subsection (b) unless the "
            "taxpayer includes the name, age, and TIN of the qualifying child on the return of tax for "
            "the taxable year.\n"
            "(ii) Other methods\n"
            "The Secretary may prescribe other methods for providing the information described in "
            "clause (i).\n"
            "(4) Treatment of military personnel stationed outside the United States\n"
            "For purposes of paragraphs (1)(A)(ii)(I) and (3)(C), the principal place of abode of a "
            "member of the Armed Forces of the United States shall be treated as in the United States "
            "during any period during which such member is stationed outside the United States while "
            "serving on extended active duty with the Armed Forces of the United States. For purposes "
            "of the preceding sentence, the term “extended active duty” means any period of active duty "
            "pursuant to a call or order to such duty for a period in excess of 90 days or for an "
            "indefinite period.\n"
            "(d) Married individuals\n"
            "(1) In general\n"
            "In the case of an individual who is married, this section shall apply only if a joint "
            "return is filed for the taxable year under section 6013.\n"
            "(2) Determination of marital status\n"
            "For purposes of this section—\n"
            "(A) In general\n"
            "Except as provided in subparagraph (B), marital status shall be determined under section "
            "7703(a).\n"
            "(B) Special rule for separated spouse\n"
            "An individual shall not be treated as married if such individual—\n"
            "(i) is married (as determined under section 7703(a)) and does not file a joint return for "
            "the taxable year,\n"
            "(ii) resides with a qualifying child of the individual for more than one-half of such "
            "taxable year, and\n"
            "(iii)(I) during the last 6 months of such taxable year, does not have the same principal "
            "place of abode as the individual’s spouse, or\n"
            "(II) has a decree, instrument, or agreement (other than a decree of divorce) described in "
            "section 121(d)(3)(C) with respect to the individual’s spouse and is not a member of the "
            "same household with the individual’s spouse by the end of the taxable year.\n"
            "(e) Taxable year must be full taxable year\n"
            "Except in the case of a taxable year closed by reason of the death of the taxpayer, no "
            "credit shall be allowable under this section in the case of a taxable year covering a "
            "period of less than 12 months.\n"
            "(f) Amount of credit to be determined under tables\n"
            "(1) In general\n"
            "The amount of the credit allowed by this section shall be determined under tables "
            "prescribed by the Secretary.\n"
            "(2) Requirements for tables\n"
            "The tables prescribed under paragraph (1) shall reflect the provisions of subsections (a) "
            "and (b) and shall have income brackets of not greater than $50 each—\n"
            "(A) for earned income between $0 and the amount of earned income at which the credit is "
            "phased out under subsection (b), and\n"
            "(B) for adjusted gross income between the dollar amount at which the phaseout begins under "
            "subsection (b) and the amount of adjusted gross income at which the credit is phased out "
            "under subsection (b).\n"
            "[(g) Repealed. Pub. L. 111–226, title II, § 219(a)(2), Aug. 10, 2010, 124 Stat. 2403]\n"
            "[(h) Repealed. Pub. L. 107–16, title III, § 303(c), June 7, 2001, 115 Stat. 55]\n"
            "(i) Denial of credit for individuals having excessive investment income\n"
            "(1) In general\n"
            "No credit shall be allowed under subsection (a) for the taxable year if the aggregate "
            "amount of disqualified income of the taxpayer for the taxable year exceeds $10,000.\n"
            "(2) Disqualified income\n"
            "For purposes of paragraph (1), the term “disqualified income” means—\n"
            "(A) interest or dividends to the extent includible in gross income for the taxable year,\n"
            "(B) interest received or accrued during the taxable year which is exempt from tax imposed "
            "by this chapter,\n"
            "(C) the excess (if any) of—\n"
            "(i) gross income from rents or royalties not derived in the ordinary course of a trade or "
            "business, over\n"
            "(ii) the sum of—\n"
            "(I) the deductions (other than interest) which are clearly and directly allocable to such "
            "gross income, plus\n"
            "(II) interest deductions properly allocable to such gross income,\n"
            "(D) the capital gain net income (as defined in section 1222) of the taxpayer for such "
            "taxable year, and\n"
            "(E) the excess (if any) of—\n"
            "(i) the aggregate income from all passive activities for the taxable year (determined "
            "without regard to any amount included in earned income under subsection (c)(2) or "
            "described in a preceding subparagraph), over\n"
            "(ii) the aggregate losses from all passive activities for the taxable year (as so "
            "determined).\n"
            "For purposes of subparagraph (E), the term “passive activity” has the meaning given such "
            "term by section 469.\n"
            "(j) Inflation adjustments\n"
            "(1) In general\n"
            "In the case of any taxable year beginning after 2015 (2021 in the case of the dollar "
            "amount in subsection (i)(1)), each of the dollar amounts in subsections (b)(2) and (i)(1) "
            "shall be increased by an amount equal to—\n"
            "(A) such dollar amount, multiplied by\n"
            "(B) the cost-of-living adjustment determined under section 1(f)(3) for the calendar year "
            "in which the taxable year begins, determined by substituting in subparagraph (A)(ii) "
            "thereof—\n"
            "(i) in the case of amounts in subsection (b)(2)(A), “calendar year 1995” for “calendar "
            "year 2016”,\n"
            "(ii) in the case of the $5,000 amount in subsection (b)(2)(B), “calendar year 2008” for "
            "“calendar year 2016”, and\n"
            "(iii) in the case of the $10,000 amount in subsection (i)(1), “calendar year 2020” for "
            "“calendar year 2016”.\n"
            "(2) Rounding\n"
            "(A) In general\n"
            "If any dollar amount in subsection (b)(2)(A) (after being increased under subparagraph (B) "
            "thereof), after being increased under paragraph (1), is not a multiple of $10, such dollar "
            "amount shall be rounded to the nearest multiple of $10.\n"
            "(B) Disqualified income threshold amount\n"
            "If the dollar amount in subsection (i)(1), after being increased under paragraph (1), is "
            "not a multiple of $50, such amount shall be rounded to the next lowest multiple of $50.\n"
            "(k) Restrictions on taxpayers who improperly claimed credit in prior year\n"
            "(1) Taxpayers making prior fraudulent or reckless claims\n"
            "(A) In general\n"
            "No credit shall be allowed under this section for any taxable year in the disallowance "
            "period.\n"
            "(B) Disallowance period\n"
            "For purposes of paragraph (1), the disallowance period is—\n"
            "(i) the period of 10 taxable years after the most recent taxable year for which there was "
            "a final determination that the taxpayer’s claim of credit under this section was due to "
            "fraud, and\n"
            "(ii) the period of 2 taxable years after the most recent taxable year for which there was "
            "a final determination that the taxpayer’s claim of credit under this section was due to "
            "reckless or intentional disregard of rules and regulations (but not due to fraud).\n"
            "(2) Taxpayers making improper prior claims\n"
            "In the case of a taxpayer who is denied credit under this section for any taxable year as "
            "a result of the deficiency procedures under subchapter B of chapter 63, no credit shall be "
            "allowed under this section for any subsequent taxable year unless the taxpayer provides "
            "such information as the Secretary may require to demonstrate eligibility for such credit.\n"
            "(l) Coordination with certain means-tested programs\n"
            "For purposes of—\n"
            "(1) the United States Housing Act of 1937,\n"
            "(2) title V of the Housing Act of 1949,\n"
            "(3) section 101 of the Housing and Urban Development Act of 1965,\n"
            "(4) sections 221(d)(3), 235, and 236 of the National Housing Act, and\n"
            "(5) the Food and Nutrition Act of 2008,\n"
            "any refund made to an individual (or the spouse of an individual) by reason of this "
            "section shall not be treated as income (and shall not be taken into account in determining "
            "resources for the month of its receipt and the following month).\n"
            "(m) Identification numbers\n"
            "Solely for purposes of subsections (c)(1)(E) and (c)(3)(D), a taxpayer identification "
            "number means a social security number issued to an individual by the Social Security "
            "Administration (other than a social security number issued pursuant to clause (II) (or "
            "that portion of clause (III) that relates to clause (II)) of section 205(c)(2)(B)(i) of "
            "the Social Security Act) on or before the due date for filing the return for the taxable "
            "year.\n"
            "(n) Special rules for individuals without qualifying children\n"
            "In the case of any taxable year beginning after December 31, 2020, and before January 1, "
            "2022—\n"
            "(1) Decrease in minimum age for credit\n"
            "(A) In general\n"
            "Subsection (c)(1)(A)(ii)(II) shall be applied by substituting “the applicable minimum age” "
            "for “age 25”.\n"
            "(B) Applicable minimum age\n"
            "For purposes of this paragraph, the term “applicable minimum age” means—\n"
            "(i) except as otherwise provided in this subparagraph, age 19,\n"
            "(ii) in the case of a specified student (other than a qualified former foster youth or a "
            "qualified homeless youth), age 24, and\n"
            "(iii) in the case of a qualified former foster youth or a qualified homeless youth, age "
            "18.\n"
            "(C) Specified student\n"
            "For purposes of this paragraph, the term “specified student” means, with respect to any "
            "taxable year, an individual who is an eligible student (as defined in section 25A(b)(3)) "
            "during at least 5 calendar months during the taxable year.\n"
            "(D) Qualified former foster youth\n"
            "For purposes of this paragraph, the term “qualified former foster youth” means an "
            "individual who—\n"
            "(i) on or after the date that such individual attained age 14, was in foster care provided "
            "under the supervision or administration of an entity administering (or eligible to "
            "administer) a plan under part B or part E of title IV of the Social Security Act (without "
            "regard to whether Federal assistance was provided with respect to such child under such "
            "part E), and\n"
            "(ii) provides (in such manner as the Secretary may provide) consent for entities which "
            "administer a plan under part B or part E of title IV of the Social Security Act to "
            "disclose to the Secretary information related to the status of such individual as a "
            "qualified former foster youth.\n"
            "(E) Qualified homeless youth\n"
            "For purposes of this paragraph, the term “qualified homeless youth” means, with respect to "
            "any taxable year, an individual who certifies, in a manner as provided by the Secretary, "
            "that such individual is either an unaccompanied youth who is a homeless child or youth, or "
            "is unaccompanied, at risk of homelessness, and self-supporting.\n"
            "(2) Elimination of maximum age for credit\n"
            "Subsection (c)(1)(A)(ii)(II) shall be applied without regard to the phrase “but not "
            "attained age 65”.\n"
            "(3) Increase in credit and phaseout percentages\n"
            "The table contained in subsection (b)(1) shall be applied by substituting “15.3” for "
            "“7.65” each place it appears therein.\n"
            "(4) Increase in earned income and phaseout amounts\n"
            "(A) In general\n"
            "The table contained in subsection (b)(2)(A) shall be applied—\n"
            "(i) by substituting “$9,820” for “$4,220”, and\n"
            "(ii) by substituting “$11,610” for “$5,280”.\n"
            "(B) Coordination with inflation adjustment\n"
            "Subsection (j) shall not apply to any dollar amount specified in this paragraph."
        ),
        text_es=(
            "Resumen en español; el texto oficial está en inglés. Concede a individuos elegibles un "
            "crédito reembolsable igual a un porcentaje del ingreso devengado hasta un tope, que se "
            "reduce gradualmente a partir de un nivel de ingreso (a). Fija los porcentajes y cantidades "
            "según el número de hijos calificados (b), define individuo elegible, hijo calificado e "
            "ingreso devengado (c) y deniega el crédito cuando el ingreso de inversión es excesivo (i). "
            "Las cantidades se ajustan por inflación (j)."
        ),
        effective_year=1975,
        source_url=USLM_TITLE26_SOURCE_URL,
        cross_references=["26 U.S.C. § 24", "26 U.S.C. § 152"],
        breadcrumbs="Title 26 > Subtitle A > Chapter 1 > Subchapter A > Part IV > Subpart C > § 32 (OLRC release point 119-121)",
    ),
    StatuteRecord(
        id="us-irc-61",
        jurisdiction="US",
        corpus="IRC",
        citation="26 U.S.C. § 61",
        section="61",
        title="Gross income defined / Definición de ingreso bruto",
        text_en=(
            "(a) General definition\n"
            "Except as otherwise provided in this subtitle, gross income means all income from whatever "
            "source derived, including (but not limited to) the following items:\n"
            "(1) Compensation for services, including fees, commissions, fringe benefits, and similar "
            "items;\n"
            "(2) Gross income derived from business;\n"
            "(3) Gains derived from dealings in property;\n"
            "(4) Interest;\n"
            "(5) Rents;\n"
            "(6) Royalties;\n"
            "(7) Dividends;\n"
            "(8) Annuities;\n"
            "(9) Income from life insurance and endowment contracts;\n"
            "(10) Pensions;\n"
            "(11) Income from discharge of indebtedness;\n"
            "(12) Distributive share of partnership gross income;\n"
            "(13) Income in respect of a decedent; and\n"
            "(14) Income from an interest in an estate or trust.\n"
            "(b) Cross references\n"
            "For items specifically included in gross income, see part II (sec. 71 and following). For "
            "items specifically excluded from gross income, see part III (sec. 101 and following)."
        ),
        text_es=(
            "Resumen en español; el texto oficial está en inglés. Salvo disposición en contrario, el "
            "ingreso bruto comprende todo ingreso de cualquier fuente, incluidos, entre otros, la "
            "compensación por servicios, el ingreso de negocios, las ganancias en la venta de "
            "propiedad, intereses, rentas, regalías, dividendos, anualidades, pensiones, la condonación "
            "de deudas y la participación distributiva en sociedades."
        ),
        effective_year=1954,
        source_url=USLM_TITLE26_SOURCE_URL,
        cross_references=["26 U.S.C. § 62", "26 U.S.C. § 933"],
        breadcrumbs="Title 26 > Subtitle A > Chapter 1 > Subchapter B > Part I > § 61 (OLRC release point 119-121)",
    ),
    StatuteRecord(
        id="us-irc-62",
        jurisdiction="US",
        corpus="IRC",
        citation="26 U.S.C. § 62",
        section="62",
        title="Adjusted gross income defined / Definición de ingreso bruto ajustado",
        text_en=(
            "(a) General rule\n"
            "For purposes of this subtitle, the term “adjusted gross income” means, in the case of an "
            "individual, gross income minus the following deductions:\n"
            "(1) Trade and business deductions\n"
            "The deductions allowed by this chapter (other than by part VII of this subchapter) which "
            "are attributable to a trade or business carried on by the taxpayer, if such trade or "
            "business does not consist of the performance of services by the taxpayer as an employee.\n"
            "(2) Certain trade and business deductions of employees\n"
            "(A) Reimbursed expenses of employees\n"
            "The deductions allowed by part VI (section 161 and following) which consist of expenses "
            "paid or incurred by the taxpayer, in connection with the performance by him of services as "
            "an employee, under a reimbursement or other expense allowance arrangement with his "
            "employer. The fact that the reimbursement may be provided by a third party shall not be "
            "determinative of whether or not the preceding sentence applies.\n"
            "(B) Certain expenses of performing artists\n"
            "The deductions allowed by section 162 which consist of expenses paid or incurred by a "
            "qualified performing artist in connection with the performances by him of services in the "
            "performing arts as an employee.\n"
            "(C) Certain expenses of officials\n"
            "The deductions allowed by section 162 which consist of expenses paid or incurred with "
            "respect to services performed by an official as an employee of a State or a political "
            "subdivision thereof in a position compensated in whole or in part on a fee basis.\n"
            "(D) Certain expenses of early childhood, elementary, and secondary school teachers\n"
            "The deductions allowed by section 162 which consist of expenses, not in excess of $250, "
            "paid or incurred by an eligible educator—\n"
            "(i) by reason of the participation of the educator in professional development courses "
            "related to the curriculum in which the educator provides instruction or to the students "
            "for which the educator provides instruction, and\n"
            "(ii) in connection with books, supplies (other than nonathletic supplies for courses of "
            "instruction in health or physical education), computer equipment (including related "
            "software and services) and other equipment, and supplementary materials used by the "
            "eligible educator in the classroom.\n"
            "(E) Certain expenses of members of reserve components of the Armed Forces of the United "
            "States\n"
            "The deductions allowed by section 162 which consist of expenses, determined at a rate not "
            "in excess of the rates for travel expenses (including per diem in lieu of subsistence) "
            "authorized for employees of agencies under subchapter I of chapter 57 of title 5, United "
            "States Code, paid or incurred by the taxpayer in connection with the performance of "
            "services by such taxpayer as a member of a reserve component of the Armed Forces of the "
            "United States for any period during which such individual is more than 100 miles away from "
            "home in connection with such services.\n"
            "(3) Losses from sale or exchange of property\n"
            "The deductions allowed by part VI (sec. 161 and following) as losses from the sale or "
            "exchange of property.\n"
            "(4) Deductions attributable to rents and royalties\n"
            "The deductions allowed by part VI (sec. 161 and following), by section 212 (relating to "
            "expenses for production of income), and by section 611 (relating to depletion) which are "
            "attributable to property held for the production of rents or royalties.\n"
            "(5) Certain deductions of life tenants and income beneficiaries of property\n"
            "In the case of a life tenant of property, or an income beneficiary of property held in "
            "trust, or an heir, legatee, or devisee of an estate, the deduction for depreciation "
            "allowed by section 167 and the deduction allowed by section 611.\n"
            "(6) Pension, profit-sharing, and annuity plans of self-employed individuals\n"
            "In the case of an individual who is an employee within the meaning of section 401(c)(1), "
            "the deduction allowed by section 404.\n"
            "(7) Retirement savings\n"
            "The deduction allowed by section 219 (relating to deduction of certain retirement "
            "savings).\n"
            "[(8) Repealed. Pub. L. 104–188, title I, § 1401(b)(4), Aug. 20, 1996, 110 Stat. 1788]\n"
            "(9) Penalties forfeited because of premature withdrawal of funds from time savings "
            "accounts or deposits\n"
            "The deductions allowed by section 165 for losses incurred in any transaction entered into "
            "for profit, though not connected with a trade or business, to the extent that such losses "
            "include amounts forfeited to a bank, mutual savings bank, savings and loan association, "
            "building and loan association, cooperative bank or homestead association as a penalty for "
            "premature withdrawal of funds from a time savings account, certificate of deposit, or "
            "similar class of deposit.\n"
            "[(10) Repealed. Pub. L. 115–97, title I, § 11051(b)(2)(A), Dec. 22, 2017, 131 Stat. 2089]\n"
            "(11) Reforestation expenses\n"
            "The deduction allowed by section 194.\n"
            "(12) Certain required repayments of supplemental unemployment compensation benefits\n"
            "The deduction allowed by section 165 for the repayment to a trust described in paragraph "
            "(9) or (17) of section 501(c) of supplemental unemployment compensation benefits received "
            "from such trust if such repayment is required because of the receipt of trade readjustment "
            "allowances under section 231 or 232 of the Trade Act of 1974 (19 U.S.C. 2291 and 2292).\n"
            "(13) Jury duty pay remitted to employer\n"
            "Any deduction allowable under this chapter by reason of an individual remitting any "
            "portion of any jury pay to such individual’s employer in exchange for payment by the "
            "employer of compensation for the period such individual was performing jury duty. For "
            "purposes of the preceding sentence, the term “jury pay” means any payment received by the "
            "individual for the discharge of jury duty.\n"
            "[(14) Repealed. Pub. L. 113–295, div. A, title II, § 221(a)(34)(C), Dec. 19, 2014, 128 "
            "Stat. 4042]\n"
            "(15) Moving expenses\n"
            "The deduction allowed by section 217.\n"
            "(16) Archer MSAs\n"
            "The deduction allowed by section 220.\n"
            "(17) Interest on education loans\n"
            "The deduction allowed by section 221.\n"
            "[(18) Repealed. Pub. L. 116–260, div. EE, title I, § 104(b)(2)(A), Dec. 27, 2020, 134 "
            "Stat. 3041]\n"
            "(19) Health savings accounts\n"
            "The deduction allowed by section 223.\n"
            "(20) Costs involving discrimination suits, etc.\n"
            "Any deduction allowable under this chapter for attorney fees and court costs paid by, or "
            "on behalf of, the taxpayer in connection with any action involving a claim of unlawful "
            "discrimination (as defined in subsection (e)) or a claim of a violation of subchapter III "
            "of chapter 37 of title 31, United States Code, or a claim made under section 1862(b)(3)(A) "
            "of the Social Security Act (42 U.S.C. 1395y(b)(3)(A)). The preceding sentence shall not "
            "apply to any deduction in excess of the amount includible in the taxpayer’s gross income "
            "for the taxable year on account of a judgment or settlement (whether by suit or agreement "
            "and whether as lump sum or periodic payments) resulting from such claim.\n"
            "(21) Attorneys’ fees relating to awards to whistleblowers\n"
            "(A) In general\n"
            "Any deduction allowable under this chapter for attorney fees and court costs paid by, or "
            "on behalf of, the taxpayer in connection with any award under—\n"
            "(i) section 7623(b), or\n"
            "(ii) in the case of taxable years beginning after December 31, 2017, any action brought "
            "under—\n"
            "(I) section 21F of the Securities Exchange Act of 1934 (15 U.S.C. 78u–6),\n"
            "(II) a State false claims act, including a State false claims act with qui tam provisions, "
            "or\n"
            "(III) section 23 of the Commodity Exchange Act (7 U.S.C. 26).\n"
            "(B) May not exceed award\n"
            "Subparagraph (A) shall not apply to any deduction in excess of the amount includible in "
            "the taxpayer’s gross income for the taxable year on account of such award.\n"
            "Nothing in this section shall permit the same item to be deducted more than once. Any "
            "deduction allowed by section 199A shall not be treated as a deduction described in any of "
            "the preceding paragraphs of this subsection.\n"
            "(b) Qualified performing artist\n"
            "(1) In general\n"
            "For purposes of subsection (a)(2)(B), the term “qualified performing artist” means, with "
            "respect to any taxable year, any individual if—\n"
            "(A) such individual performed services in the performing arts as an employee during the "
            "taxable year for at least 2 employers,\n"
            "(B) the aggregate amount allowable as a deduction under section 162 in connection with the "
            "performance of such services exceeds 10 percent of such individual’s gross income "
            "attributable to the performance of such services, and\n"
            "(C) the adjusted gross income of such individual for the taxable year (determined without "
            "regard to subsection (a)(2)(B)) does not exceed $16,000.\n"
            "(2) Nominal employer not taken into account\n"
            "An individual shall not be treated as performing services in the performing arts as an "
            "employee for any employer during any taxable year unless the amount received by such "
            "individual from such employer for the performance of such services during the taxable year "
            "equals or exceeds $200.\n"
            "(3) Special rules for married couples\n"
            "(A) In general\n"
            "Except in the case of a husband and wife who lived apart at all times during the taxable "
            "year, if the taxpayer is married at the close of the taxable year, subsection (a)(2)(B) "
            "shall apply only if the taxpayer and his spouse file a joint return for the taxable year.\n"
            "(B) Application of paragraph (1)\n"
            "In the case of a joint return—\n"
            "(i) paragraph (1) (other than subparagraph (C) thereof) shall be applied separately with "
            "respect to each spouse, but\n"
            "(ii) paragraph (1)(C) shall be applied with respect to their combined adjusted gross "
            "income.\n"
            "(C) Determination of marital status\n"
            "For purposes of this subsection, marital status shall be determined under section 7703(a).\n"
            "(D) Joint return\n"
            "For purposes of this subsection, the term “joint return” means the joint return of a "
            "husband and wife made under section 6013.\n"
            "(c) Certain arrangements not treated as reimbursement arrangements\n"
            "For purposes of subsection (a)(2)(A), an arrangement shall in no event be treated as a "
            "reimbursement or other expense allowance arrangement if—\n"
            "(1) such arrangement does not require the employee to substantiate the expenses covered by "
            "the arrangement to the person providing the reimbursement, or\n"
            "(2) such arrangement provides the employee the right to retain any amount in excess of the "
            "substantiated expenses covered under the arrangement.\n"
            "The substantiation requirements of the preceding sentence shall not apply to any expense "
            "to the extent that substantiation is not required under section 274(d) for such expense by "
            "reason of the regulations prescribed under the 2nd sentence thereof.\n"
            "(d) Definition; special rules\n"
            "(1) Eligible educator\n"
            "(A) In general\n"
            "For purposes of subsection (a)(2)(D), the term “eligible educator” means, with respect to "
            "any taxable year, an individual who is an early childhood or kindergarten through grade 12 "
            "teacher, instructor, counselor, principal, or aide in a school for at least 900 hours "
            "during a school year.\n"
            "(B) School\n"
            "The term “school” means—\n"
            "(i) in the case of early childhood education, any school or childcare facility which—\n"
            "(I) provides educational or childcare services for more than 2 individuals (other than "
            "individuals who reside at the school or facility) who have not attained age 6, and\n"
            "(II) operates at the public expense or receives a fee, payment, or grant for providing "
            "such services for any of the individuals (regardless of whether such school or facility is "
            "operated for profit), and\n"
            "(ii) in the case of elementary education or secondary education (kindergarten through "
            "grade 12), any school which provides such education, as determined under State law.\n"
            "(2) Coordination with exclusions\n"
            "A deduction shall be allowed under subsection (a)(2)(D) for expenses only to the extent "
            "the amount of such expenses exceeds the amount excludable under section 135, 529(c)(1), or "
            "530(d)(2) for the taxable year.\n"
            "(3) Inflation adjustment\n"
            "In the case of any taxable year beginning after 2015, the $250 amount in subsection "
            "(a)(2)(D) shall be increased by an amount equal to—\n"
            "(A) such dollar amount, multiplied by\n"
            "(B) the cost-of-living adjustment determined under section 1(f)(3) for the calendar year "
            "in which the taxable year begins, determined by substituting “calendar year 2014” for "
            "“calendar year 2016” in subparagraph (A)(ii) thereof.\n"
            "Any increase determined under the preceding sentence shall be rounded to the nearest "
            "multiple of $50.\n"
            "(e) Unlawful discrimination defined\n"
            "For purposes of subsection (a)(20), the term “unlawful discrimination” means an act that "
            "is unlawful under any of the following:\n"
            "(1) Section 302 of the Civil Rights Act of 1991 (42 U.S.C. 2000e–16b).\n"
            "(2) Section 201, 202, 203, 204, 205, 206, 207, or 208 of the Congressional Accountability "
            "Act of 1995 (2 U.S.C. 1311, 1312, 1313, 1314, 1315, 1316, or 1317).1\n"
            "(3) The National Labor Relations Act (29 U.S.C. 151 et seq.).\n"
            "(4) The Fair Labor Standards Act of 1938 (29 U.S.C. 201 et seq.).\n"
            "(5) Section 4 or 15 of the Age Discrimination in Employment Act of 1967 (29 U.S.C. 623 or "
            "633a).\n"
            "(6) Section 501 or 504 of the Rehabilitation Act of 1973 (29 U.S.C. 791 or 794).\n"
            "(7) Section 510 of the Employee Retirement Income Security Act of 1974 (29 U.S.C. 1140).\n"
            "(8) Title IX of the Education Amendments of 1972 (20 U.S.C. 1681 et seq.).\n"
            "(9) The Employee Polygraph Protection Act of 1988 (29 U.S.C. 2001 et seq.).\n"
            "(10) The Worker Adjustment and Retraining Notification Act (29 U.S.C. 2102 et seq.).\n"
            "(11) Section 105 of the Family and Medical Leave Act of 1993 (29 U.S.C. 2615).\n"
            "(12) Chapter 43 of title 38, United States Code (relating to employment and reemployment "
            "rights of members of the uniformed services).\n"
            "(13) Section 1977, 1979, or 1980 of the Revised Statutes (42 U.S.C. 1981, 1983, or 1985).\n"
            "(14) Section 703, 704, or 717 of the Civil Rights Act of 1964 (42 U.S.C. 2000e–2, 2000e–3, "
            "or 2000e–16).\n"
            "(15) Section 804, 805, 806, 808, or 818 of the Fair Housing Act (42 U.S.C. 3604, 3605, "
            "3606, 3608, or 3617).\n"
            "(16) Section 102, 202, 302, or 503 of the Americans with Disabilities Act of 1990 (42 "
            "U.S.C. 12112, 12132, 12182, or 12203).\n"
            "(17) Any provision of Federal law (popularly known as whistleblower protection provisions) "
            "prohibiting the discharge of an employee, the discrimination against an employee, or any "
            "other form of retaliation or reprisal against an employee for asserting rights or taking "
            "other actions permitted under Federal law.\n"
            "(18) Any provision of Federal, State, or local law, or common law claims permitted under "
            "Federal, State, or local law—\n"
            "(i) providing for the enforcement of civil rights, or\n"
            "(ii) regulating any aspect of the employment relationship, including claims for wages, "
            "compensation, or benefits, or prohibiting the discharge of an employee, the discrimination "
            "against an employee, or any other form of retaliation or reprisal against an employee for "
            "asserting rights or taking other actions permitted by law."
        ),
        text_es=(
            "Resumen en español; el texto oficial está en inglés. Define el ingreso bruto ajustado de "
            "un individuo como el ingreso bruto menos las deducciones enumeradas en la subsección (a), "
            "entre ellas ciertos gastos de negocio, pérdidas en la venta de propiedad, deducciones "
            "atribuibles a rentas y regalías, y aportaciones a ciertos planes de retiro. Incluye reglas "
            "para artistas calificados (b) y para acuerdos de reembolso (c)."
        ),
        effective_year=1954,
        source_url=USLM_TITLE26_SOURCE_URL,
        cross_references=["26 U.S.C. § 61", "26 U.S.C. § 63"],
        breadcrumbs="Title 26 > Subtitle A > Chapter 1 > Subchapter B > Part I > § 62 (OLRC release point 119-121)",
    ),
    StatuteRecord(
        id="us-irc-63",
        jurisdiction="US",
        corpus="IRC",
        citation="26 U.S.C. § 63",
        section="63",
        title="Taxable income defined / Definición de ingreso tributable",
        text_en=(
            "(a) In general\n"
            "Except as provided in subsection (b), for purposes of this subtitle, the term “taxable "
            "income” means gross income minus the deductions allowed by this chapter (other than the "
            "standard deduction).\n"
            "(b) Individuals who do not itemize their deductions\n"
            "In the case of an individual who does not elect to itemize his deductions for the taxable "
            "year, for purposes of this subtitle, the term “taxable income” means adjusted gross "
            "income, minus—\n"
            "(1) the standard deduction,\n"
            "(2) the deduction for personal exemptions provided in section 151,\n"
            "(3) any deduction provided in section 199A,\n"
            "(4) the deduction provided in section 170(p),\n"
            "(5) the deduction provided in section 224,\n"
            "(6) the deduction provided in section 225,\n"
            "(7) so much of the deduction allowed by section 163(a) as is attributable to the exception "
            "under section 163(h)(4)(A), and\n"
            "(8) so much of the deduction allowed by section 165(a) as is attributable to the qualified "
            "net disaster loss (as defined in section 165(h)(6)(B)).\n"
            "(c) Standard deduction\n"
            "For purposes of this subtitle—\n"
            "(1) In general\n"
            "Except as otherwise provided in this subsection, the term “standard deduction” means the "
            "sum of—\n"
            "(A) the basic standard deduction, and\n"
            "(B) the additional standard deduction.\n"
            "(2) Basic standard deduction\n"
            "For purposes of paragraph (1), the basic standard deduction is—\n"
            "(A) 200 percent of the dollar amount in effect under subparagraph (C) for the taxable year "
            "in the case of—\n"
            "(i) a joint return, or\n"
            "(ii) a surviving spouse (as defined in section 2(a)),\n"
            "(B) $4,400 in the case of a head of household (as defined in section 2(b)), or\n"
            "(C) $3,000 in any other case.\n"
            "(3) Additional standard deduction for aged and blind\n"
            "For purposes of paragraph (1), the additional standard deduction is the sum of each "
            "additional amount to which the taxpayer is entitled under subsection (f).\n"
            "(4) Adjustments for inflation\n"
            "In the case of any taxable year beginning in a calendar year after 1988, each dollar "
            "amount contained in paragraph (2)(B), (2)(C), or (5) or subsection (f) shall be increased "
            "by an amount equal to—\n"
            "(A) such dollar amount, multiplied by\n"
            "(B) the cost-of-living adjustment determined under section 1(f)(3) for the calendar year "
            "in which the taxable year begins, by substituting for “calendar year 2016” in subparagraph "
            "(A)(ii) thereof—\n"
            "(i) “calendar year 1987” in the case of the dollar amounts contained in paragraph (2)(B), "
            "(2)(C), or (5)(A) or subsection (f), and\n"
            "(ii) “calendar year 1997” in the case of the dollar amount contained in paragraph (5)(B).\n"
            "(5) Limitation on basic standard deduction in the case of certain dependents\n"
            "In the case of an individual with respect to whom a deduction under section 151 is "
            "allowable to another taxpayer for a taxable year beginning in the calendar year in which "
            "the individual’s taxable year begins, the basic standard deduction applicable to such "
            "individual for such individual’s taxable year shall not exceed the greater of—\n"
            "(A) $500, or\n"
            "(B) the sum of $250 and such individual’s earned income.\n"
            "(6) Certain individuals, etc., not eligible for standard deduction\n"
            "In the case of—\n"
            "(A) a married individual filing a separate return where either spouse itemizes deductions,\n"
            "(B) a nonresident alien individual,\n"
            "(C) an individual making a return under section 443(a)(1) for a period of less than 12 "
            "months on account of a change in his annual accounting period, or\n"
            "(D) an estate or trust, common trust fund, or partnership,\n"
            "the standard deduction shall be zero.\n"
            "(7) Special rules for taxable years beginning after 2017\n"
            "In the case of a taxable year beginning after December 31, 2017—\n"
            "(A) Increase in standard deduction\n"
            "Paragraph (2) shall be applied—\n"
            "(i) by substituting “$23,625” for “$4,400” in subparagraph (B), and\n"
            "(ii) by substituting “$15,750” for “$3,000” in subparagraph (C).\n"
            "(B) Adjustment for inflation\n"
            "(i) In general\n"
            "Paragraph (4) shall not apply to the dollar amounts contained in paragraphs (2)(B) and "
            "(2)(C).\n"
            "(ii) Adjustment of increased amounts\n"
            "In the case of a taxable year beginning after 2025, the $23,625 and $15,750 amounts in "
            "subparagraph (A) shall each be increased by an amount equal to—\n"
            "(I) such dollar amount, multiplied by\n"
            "(II) the cost-of-living adjustment determined under section 1(f)(3) for the calendar year "
            "in which the taxable year begins, determined by substituting “2024” for “2016” in "
            "subparagraph (A)(ii) thereof.\n"
            "If any increase under this clause is not a multiple of $50, such increase shall be rounded "
            "to the next lowest multiple of $50.\n"
            "(d) Itemized deductions\n"
            "For purposes of this subtitle, the term “itemized deductions” means the deductions "
            "allowable under this chapter other than—\n"
            "(1) the deductions allowable in arriving at adjusted gross income, and\n"
            "(2) any deduction referred to in any paragraph of subsection (b).\n"
            "(e) Election to itemize\n"
            "(1) In general\n"
            "Unless an individual makes an election under this subsection for the taxable year, no "
            "itemized deduction shall be allowed for the taxable year. For purposes of this subtitle, "
            "the determination of whether a deduction is allowable under this chapter shall be made "
            "without regard to the preceding sentence.\n"
            "(2) Time and manner of election\n"
            "Any election under this subsection shall be made on the taxpayer’s return, and the "
            "Secretary shall prescribe the manner of signifying such election on the return.\n"
            "(3) Change of election\n"
            "Under regulations prescribed by the Secretary, a change of election with respect to "
            "itemized deductions for any taxable year may be made after the filing of the return for "
            "such year. If the spouse of the taxpayer filed a separate return for any taxable year "
            "corresponding to the taxable year of the taxpayer, the change shall not be allowed unless, "
            "in accordance with such regulations—\n"
            "(A) the spouse makes a change of election with respect to itemized deductions, for the "
            "taxable year covered in such separate return, consistent with the change of treatment "
            "sought by the taxpayer, and\n"
            "(B) the taxpayer and his spouse consent in writing to the assessment (within such period "
            "as may be agreed on with the Secretary) of any deficiency, to the extent attributable to "
            "such change of election, even though at the time of the filing of such consent the "
            "assessment of such deficiency would otherwise be prevented by the operation of any law or "
            "rule of law.\n"
            "This paragraph shall not apply if the tax liability of the taxpayer’s spouse for the "
            "taxable year corresponding to the taxable year of the taxpayer has been compromised under "
            "section 7122.\n"
            "(f) Aged or blind additional amounts\n"
            "(1) Additional amounts for the aged\n"
            "The taxpayer shall be entitled to an additional amount of $600—\n"
            "(A) for himself if he has attained age 65 before the close of his taxable year, and\n"
            "(B) for the spouse of the taxpayer if the spouse has attained age 65 before the close of "
            "the taxable year and an additional exemption is allowable to the taxpayer for such spouse "
            "under section 151(b).\n"
            "(2) Additional amount for blind\n"
            "The taxpayer shall be entitled to an additional amount of $600—\n"
            "(A) for himself if he is blind at the close of the taxable year, and\n"
            "(B) for the spouse of the taxpayer if the spouse is blind as of the close of the taxable "
            "year and an additional exemption is allowable to the taxpayer for such spouse under "
            "section 151(b).\n"
            "For purposes of subparagraph (B), if the spouse dies during the taxable year the "
            "determination of whether such spouse is blind shall be made as of the time of such death.\n"
            "(3) Higher amount for certain unmarried individuals\n"
            "In the case of an individual who is not married and is not a surviving spouse, paragraphs "
            "(1) and (2) shall be applied by substituting “$750” for “$600”.\n"
            "(4) Blindness defined\n"
            "For purposes of this subsection, an individual is blind only if his central visual acuity "
            "does not exceed 20/200 in the better eye with correcting lenses, or if his visual acuity "
            "is greater than 20/200 but is accompanied by a limitation in the fields of vision such "
            "that the widest diameter of the visual field subtends an angle no greater than 20 degrees.\n"
            "(g) Marital status\n"
            "For purposes of this section, marital status shall be determined under section 7703."
        ),
        text_es=(
            "Resumen en español; el texto oficial está en inglés. El ingreso tributable es el ingreso "
            "bruto menos las deducciones permitidas (a); para quien no detalla sus deducciones, es el "
            "ingreso bruto ajustado menos la deducción estándar y otras deducciones indicadas en (b). "
            "Define la deducción estándar y sus ajustes (c), las deducciones detalladas (d), la "
            "elección de detallar (e), las cantidades adicionales por edad o ceguera (f) y el estado "
            "civil (g)."
        ),
        effective_year=1954,
        source_url=USLM_TITLE26_SOURCE_URL,
        cross_references=["26 U.S.C. § 62", "26 U.S.C. § 1"],
        breadcrumbs="Title 26 > Subtitle A > Chapter 1 > Subchapter B > Part I > § 63 (OLRC release point 119-121)",
    ),
    StatuteRecord(
        id="us-irc-911",
        jurisdiction="US",
        corpus="IRC",
        citation="26 U.S.C. § 911",
        section="911",
        title="Citizens or residents of the United States living abroad / Ciudadanos o residentes de los Estados Unidos que viven en el extranjero",
        text_en=(
            "(a) Exclusion from gross income\n"
            "At the election of a qualified individual (made separately with respect to paragraphs (1) "
            "and (2)), there shall be excluded from the gross income of such individual, and exempt "
            "from taxation under this subtitle, for any taxable year—\n"
            "(1) the foreign earned income of such individual, and\n"
            "(2) the housing cost amount of such individual.\n"
            "(b) Foreign earned income\n"
            "(1) Definition\n"
            "For purposes of this section—\n"
            "(A) In general\n"
            "The term “foreign earned income” with respect to any individual means the amount received "
            "by such individual from sources within a foreign country or countries which constitute "
            "earned income attributable to services performed by such individual during the period "
            "described in subparagraph (A) or (B) of subsection (d)(1), whichever is applicable.\n"
            "(B) Certain amounts not included in foreign earned income\n"
            "The foreign earned income for an individual shall not include amounts—\n"
            "(i) received as a pension or annuity,\n"
            "(ii) paid by the United States or an agency thereof to an employee of the United States or "
            "an agency thereof,\n"
            "(iii) included in gross income by reason of section 402(b) (relating to taxability of "
            "beneficiary of nonexempt trust) or section 403(c) (relating to taxability of beneficiary "
            "under a nonqualified annuity), or\n"
            "(iv) received after the close of the taxable year following the taxable year in which the "
            "services to which the amounts are attributable are performed.\n"
            "(2) Limitation on foreign earned income\n"
            "(A) In general\n"
            "The foreign earned income of an individual which may be excluded under subsection (a)(1) "
            "for any taxable year shall not exceed the amount of foreign earned income computed on a "
            "daily basis at an annual rate equal to the exclusion amount for the calendar year in which "
            "such taxable year begins.\n"
            "(B) Attribution to year in which services are performed\n"
            "For purposes of applying subparagraph (A), amounts received shall be considered received "
            "in the taxable year in which the services to which the amounts are attributable are "
            "performed.\n"
            "(C) Treatment of community income\n"
            "In applying subparagraph (A) with respect to amounts received from services performed by a "
            "husband or wife which are community income under community property laws applicable to "
            "such income, the aggregate amount which may be excludable from the gross income of such "
            "husband and wife under subsection (a)(1) for any taxable year shall equal the amount which "
            "would be so excludable if such amounts did not constitute community income.\n"
            "(D) Exclusion amount\n"
            "(i) In general\n"
            "The exclusion amount for any calendar year is $80,000.\n"
            "(ii) Inflation adjustment\n"
            "In the case of any taxable year beginning in a calendar year after 2005, the $80,000 "
            "amount in clause (i) shall be increased by an amount equal to the product of—\n"
            "(I) such dollar amount, and\n"
            "(II) the cost-of-living adjustment determined under section 1(f)(3) for the calendar year "
            "in which the taxable year begins, determined by substituting “2004” for “2016” in "
            "subparagraph (A)(ii) thereof.\n"
            "If any increase determined under the preceding sentence is not a multiple of $100, such "
            "increase shall be rounded to the next lowest multiple of $100.\n"
            "(c) Housing cost amount\n"
            "For purposes of this section—\n"
            "(1) In general\n"
            "The term “housing cost amount” means an amount equal to the excess of—\n"
            "(A) the housing expenses of an individual for the taxable year to the extent such expenses "
            "do not exceed the amount determined under paragraph (2), over\n"
            "(B) an amount equal to the product of—\n"
            "(i) 16 percent of the amount (computed on a daily basis) in effect under subsection "
            "(b)(2)(D) for the calendar year in which such taxable year begins, multiplied by\n"
            "(ii) the number of days of such taxable year within the applicable period described in "
            "subparagraph (A) or (B) of subsection (d)(1).\n"
            "(2) Limitation\n"
            "(A) In general\n"
            "The amount determined under this paragraph is an amount equal to the product of—\n"
            "(i) 30 percent (adjusted as may be provided under subparagraph (B)) of the amount "
            "(computed on a daily basis) in effect under subsection (b)(2)(D) for the calendar year in "
            "which the taxable year of the individual begins, multiplied by\n"
            "(ii) the number of days of such taxable year within the applicable period described in "
            "subparagraph (A) or (B) of subsection (d)(1).\n"
            "(B) Regulations\n"
            "The Secretary may issue regulations or other guidance providing for the adjustment of the "
            "percentage under subparagraph (A)(i) on the basis of geographic differences in housing "
            "costs relative to housing costs in the United States.\n"
            "(3) Housing expenses\n"
            "(A) In general\n"
            "The term “housing expenses” means the reasonable expenses paid or incurred during the "
            "taxable year by or on behalf of an individual for housing for the individual (and, if they "
            "reside with him, for his spouse and dependents) in a foreign country. The term—\n"
            "(i) includes expenses attributable to the housing (such as utilities and insurance), but\n"
            "(ii) does not include interest and taxes of the kind deductible under section 163 or 164 "
            "or any amount allowable as a deduction under section 216(a).\n"
            "Housing expenses shall not be treated as reasonable to the extent such expenses are lavish "
            "or extravagant under the circumstances.\n"
            "(B) Second foreign household\n"
            "(i) In general\n"
            "Except as provided in clause (ii), only housing expenses incurred with respect to that "
            "abode which bears the closest relationship to the tax home of the individual shall be "
            "taken into account under paragraph (1).\n"
            "(ii) Separate household for spouse and dependents\n"
            "If an individual maintains a separate abode outside the United States for his spouse and "
            "dependents and they do not reside with him because of living conditions which are "
            "dangerous, unhealthful, or otherwise adverse, then—\n"
            "(I) the words “if they reside with him” in subparagraph (A) shall be disregarded, and\n"
            "(II) the housing expenses incurred with respect to such abode shall be taken into account "
            "under paragraph (1).\n"
            "(4) Special rules where housing expenses not provided by employer\n"
            "(A) In general\n"
            "To the extent the housing cost amount of any individual for any taxable year is not "
            "attributable to employer provided amounts, such amount shall be treated as a deduction "
            "allowable in computing adjusted gross income to the extent of the limitation of "
            "subparagraph (B).\n"
            "(B) Limitation\n"
            "For purposes of subparagraph (A), the limitation of this subparagraph is the excess of—\n"
            "(i) the foreign earned income of the individual for the taxable year, over\n"
            "(ii) the amount of such income excluded from gross income under subsection (a) for the "
            "taxable year.\n"
            "(C) 1-year carryover of housing amounts not allowed by reason of subparagraph (B)\n"
            "(i) In general\n"
            "The amount not allowable as a deduction for any taxable year under subparagraph (A) by "
            "reason of the limitation of subparagraph (B) shall be treated as a deduction allowable in "
            "computing adjusted gross income for the succeeding taxable year (and only for the "
            "succeeding taxable year) to the extent of the limitation of clause (ii) for such "
            "succeeding taxable year.\n"
            "(ii) Limitation\n"
            "For purposes of clause (i), the limitation of this clause for any taxable year is the "
            "excess of—\n"
            "(I) the limitation of subparagraph (B) for such taxable year, over\n"
            "(II) amounts treated as a deduction under subparagraph (A) for such taxable year.\n"
            "(D) Employer provided amounts\n"
            "For purposes of this paragraph, the term “employer provided amounts” means any amount paid "
            "or incurred on behalf of the individual by the individual’s employer which is foreign "
            "earned income included in the individual’s gross income for the taxable year (without "
            "regard to this section).\n"
            "(E) Foreign earned income\n"
            "For purposes of this paragraph, an individual’s foreign earned income for any taxable year "
            "shall be determined without regard to the limitation of subparagraph (A) of subsection "
            "(b)(2).\n"
            "(d) Definitions and special rules\n"
            "For purposes of this section—\n"
            "(1) Qualified individual\n"
            "The term “qualified individual” means an individual whose tax home is in a foreign country "
            "and who is—\n"
            "(A) a citizen of the United States and establishes to the satisfaction of the Secretary "
            "that he has been a bona fide resident of a foreign country or countries for an "
            "uninterrupted period which includes an entire taxable year, or\n"
            "(B) a citizen or resident of the United States and who, during any period of 12 "
            "consecutive months, is present in a foreign country or countries during at least 330 full "
            "days in such period.\n"
            "(2) Earned income\n"
            "(A) In general\n"
            "The term “earned income” means wages, salaries, or professional fees, and other amounts "
            "received as compensation for personal services actually rendered, but does not include "
            "that part of the compensation derived by the taxpayer for personal services rendered by "
            "him to a corporation which represents a distribution of earnings or profits rather than a "
            "reasonable allowance as compensation for the personal services actually rendered.\n"
            "(B) Taxpayer engaged in trade or business\n"
            "In the case of a taxpayer engaged in a trade or business in which both personal services "
            "and capital are material income-producing factors, under regulations prescribed by the "
            "Secretary, a reasonable allowance as compensation for the personal services rendered by "
            "the taxpayer, not in excess of 30 percent of his share of the net profits of such trade or "
            "business, shall be considered as earned income.\n"
            "(3) Tax home\n"
            "The term “tax home” means, with respect to any individual, such individual’s home for "
            "purposes of section 162(a)(2) (relating to traveling expenses while away from home). An "
            "individual shall not be treated as having a tax home in a foreign country for any period "
            "for which his abode is within the United States, unless such individual is serving in an "
            "area designated by the President of the United States by Executive order as a combat zone "
            "for purposes of section 112 in support of the Armed Forces of the United States.\n"
            "(4) Waiver of period of stay in foreign country\n"
            "Notwithstanding paragraph (1), an individual who—\n"
            "(A) is a bona fide resident of, or is present in, a foreign country for any period,\n"
            "(B) leaves such foreign country after August 31, 1978—\n"
            "(i) during any period during which the Secretary determines, after consultation with the "
            "Secretary of State or his delegate, that individuals were required to leave such foreign "
            "country because of war, civil unrest, or similar adverse conditions in such foreign "
            "country which precluded the normal conduct of business by such individuals, and\n"
            "(ii) before meeting the requirements of such paragraph (1), and\n"
            "(C) establishes to the satisfaction of the Secretary that such individual could reasonably "
            "have been expected to have met such requirements but for the conditions referred to in "
            "clause (i) of subparagraph (B),\n"
            "shall be treated as a qualified individual with respect to the period described in "
            "subparagraph (A) during which he was a bona fide resident of, or was present in, the "
            "foreign country, and in applying subsections (b)(2)(A), (c)(1)(B)(ii), and (c)(2)(A)(ii) "
            "with respect to such individual, only the days within such period shall be taken into "
            "account.\n"
            "(5) Test of bona fide residence\n"
            "If—\n"
            "(A) an individual who has earned income from sources within a foreign country submits a "
            "statement to the authorities of that country that he is not a resident of that country, "
            "and\n"
            "(B) such individual is held not subject as a resident of that country to the income tax of "
            "that country by its authorities with respect to such earnings,\n"
            "then such individual shall not be considered a bona fide resident of that country for "
            "purposes of paragraph (1)(A).\n"
            "(6) Denial of double benefits\n"
            "No deduction or exclusion from gross income under this subtitle or credit against the tax "
            "imposed by this chapter (including any credit or deduction for the amount of taxes paid or "
            "accrued to a foreign country or possession of the United States) shall be allowed to the "
            "extent such deduction, exclusion, or credit is properly allocable to or chargeable against "
            "amounts excluded from gross income under subsection (a).\n"
            "(7) Aggregate benefit cannot exceed foreign earned income\n"
            "The sum of the amount excluded under subsection (a) and the amount deducted under "
            "subsection (c)(4)(A) for the taxable year shall not exceed the individual’s foreign earned "
            "income for such year.\n"
            "(8) Limitation on income earned in restricted country\n"
            "(A) In general\n"
            "If travel (or any transaction in connection with such travel) with respect to any foreign "
            "country is subject to the regulations described in subparagraph (B) during any period—\n"
            "(i) the term “foreign earned income” shall not include any income from sources within such "
            "country attributable to services performed during such period,\n"
            "(ii) the term “housing expenses” shall not include any expenses allocable to such period "
            "for housing in such country or for housing of the spouse or dependents of the taxpayer in "
            "another country while the taxpayer is present in such country, and\n"
            "(iii) an individual shall not be treated as a bona fide resident of, or as present in, a "
            "foreign country for any day during which such individual was present in such country "
            "during such period.\n"
            "(B) Regulations\n"
            "For purposes of this paragraph, regulations are described in this subparagraph if such "
            "regulations—\n"
            "(i) have been adopted pursuant to the Trading With the Enemy Act (50 U.S.C. 4301 et seq.) "
            "or the International Emergency Economic Powers Act (50 U.S.C. 1701 et seq.), and\n"
            "(ii) include provisions generally prohibiting citizens and residents of the United States "
            "from engaging in transactions related to travel to, from, or within a foreign country.\n"
            "(C) Exception\n"
            "Subparagraph (A) shall not apply to any individual during any period in which such "
            "individual’s activities are not in violation of the regulations described in subparagraph "
            "(B).\n"
            "(9) Regulations\n"
            "The Secretary shall prescribe such regulations as may be necessary or appropriate to carry "
            "out the purposes of this section, including regulations providing rules—\n"
            "(A) for cases where a husband and wife each have earned income from sources outside the "
            "United States, and\n"
            "(B) for married individuals filing separate returns.\n"
            "(e) Election\n"
            "(1) In general\n"
            "An election under subsection (a) shall apply to the taxable year for which made and to all "
            "subsequent taxable years unless revoked under paragraph (2).\n"
            "(2) Revocation\n"
            "A taxpayer may revoke an election made under paragraph (1) for any taxable year after the "
            "taxable year for which such election was made. Except with the consent of the Secretary, "
            "any taxpayer who makes such a revocation for any taxable year may not make another "
            "election under this section for any subsequent taxable year before the 6th taxable year "
            "after the taxable year for which such revocation was made.\n"
            "(f) Determination of tax liability\n"
            "(1) In general\n"
            "If, for any taxable year, any amount is excluded from gross income of a taxpayer under "
            "subsection (a), then, notwithstanding sections 1 and 55—\n"
            "(A) if such taxpayer has taxable income for such taxable year, the tax imposed by section "
            "1 for such taxable year shall be equal to the excess (if any) of—\n"
            "(i) the tax which would be imposed by section 1 for such taxable year if the taxpayer’s "
            "taxable income were increased by the amount excluded under subsection (a) for such taxable "
            "year, over\n"
            "(ii) the tax which would be imposed by section 1 for such taxable year if the taxpayer’s "
            "taxable income were equal to the amount excluded under subsection (a) for such taxable "
            "year, and\n"
            "(B) if such taxpayer has a taxable excess (as defined in section 55(b)(1)(B)) for such "
            "taxable year, the amount determined under the first sentence of section 55(b)(1)(A) for "
            "such taxable year shall be equal to the excess (if any) of—\n"
            "(i) the amount which would be determined under such sentence for such taxable year "
            "(subject to the limitation of section 55(b)(3)) if the taxpayer’s taxable excess (as so "
            "defined) were increased by the amount excluded under subsection (a) for such taxable year, "
            "over\n"
            "(ii) the amount which would be determined under such sentence for such taxable year if the "
            "taxpayer’s taxable excess (as so defined) were equal to the amount excluded under "
            "subsection (a) for such taxable year.\n"
            "For purposes of this paragraph, the amount excluded under subsection (a) shall be reduced "
            "by the aggregate amount of any deductions or exclusions disallowed under subsection (d)(6) "
            "with respect to such excluded amount.\n"
            "(2) Special rules\n"
            "(A) Regular tax\n"
            "In applying section 1(h) for purposes of determining the tax under paragraph (1)(A)(i) for "
            "any taxable year in which, without regard to this subsection, the taxpayer’s net capital "
            "gain exceeds taxable income (hereafter in this subparagraph referred to as the capital "
            "gain excess)—\n"
            "(i) the taxpayer’s net capital gain (determined without regard to section 1(h)(11)) shall "
            "be reduced (but not below zero) by such capital gain excess,\n"
            "(ii) the taxpayer’s qualified dividend income shall be reduced by so much of such capital "
            "gain excess as exceeds the taxpayer’s net capital gain (determined without regard to "
            "section 1(h)(11) and the reduction under clause (i)), and\n"
            "(iii) adjusted net capital gain, unrecaptured section 1250 gain, and 28-percent rate gain "
            "shall each be determined after increasing the amount described in section 1(h)(4)(B) by "
            "such capital gain excess.\n"
            "(B) Alternative minimum tax\n"
            "In applying section 55(b)(3) for purposes of determining the tax under paragraph (1)(B)(i) "
            "for any taxable year in which, without regard to this subsection, the taxpayer’s net "
            "capital gain exceeds the taxable excess (as defined in section 55(b)(1)(B))—\n"
            "(i) the rules of subparagraph (A) shall apply, except that such subparagraph shall be "
            "applied by substituting “the taxable excess (as defined in section 55(b)(1)(B))” for "
            "“taxable income”, and\n"
            "(ii) the reference in section 55(b)(3)(B) to the excess described in section 1(h)(1)(B), "
            "and the reference in section 55(b)(3)(C)(ii) to the excess described in section "
            "1(h)(1)(C)(ii), shall each be treated as a reference to each such excess as determined "
            "under the rules of subparagraph (A) for purposes of determining the tax under paragraph "
            "(1)(A)(i).\n"
            "(C) Definitions\n"
            "Terms used in this paragraph which are also used in section 1(h) shall have the respective "
            "meanings given such terms by section 1(h), except that in applying subparagraph (B) the "
            "adjustments under part VI of subchapter A shall be taken into account.\n"
            "(g) Cross references\n"
            "For administrative and penal provisions relating to the exclusions provided for in this "
            "section, see sections 6001, 6011, 6012(c), and the other provisions of subtitle F."
        ),
        text_es=(
            "Resumen en español; el texto oficial está en inglés. Permite a un individuo calificado "
            "elegir excluir de su ingreso bruto el ingreso devengado en el extranjero y la cantidad de "
            "vivienda (a). Define el ingreso devengado en el extranjero y su límite ajustado por "
            "inflación (b), la cantidad de costo de vivienda (c) y al individuo calificado con "
            "domicilio tributario (tax home) en un país extranjero (d). Regula la elección (e) y el "
            "cómputo del impuesto (f)."
        ),
        effective_year=1954,
        source_url=USLM_TITLE26_SOURCE_URL,
        cross_references=["26 U.S.C. § 937", "26 U.S.C. § 6012"],
        breadcrumbs="Title 26 > Subtitle A > Chapter 1 > Subchapter N > Part III > Subpart B > § 911 (OLRC release point 119-121)",
    ),
    StatuteRecord(
        id="us-irc-1411",
        jurisdiction="US",
        corpus="IRC",
        citation="26 U.S.C. § 1411",
        section="1411",
        title="Imposition of tax on net investment income / Impuesto sobre el ingreso neto de inversión",
        text_en=(
            "(a) In general\n"
            "Except as provided in subsection (e)—\n"
            "(1) Application to individuals\n"
            "In the case of an individual, there is hereby imposed (in addition to any other tax "
            "imposed by this subtitle) for each taxable year a tax equal to 3.8 percent of the lesser "
            "of—\n"
            "(A) net investment income for such taxable year, or\n"
            "(B) the excess (if any) of—\n"
            "(i) the modified adjusted gross income for such taxable year, over\n"
            "(ii) the threshold amount.\n"
            "(2) Application to estates and trusts\n"
            "In the case of an estate or trust, there is hereby imposed (in addition to any other tax "
            "imposed by this subtitle) for each taxable year a tax of 3.8 percent of the lesser of—\n"
            "(A) the undistributed net investment income for such taxable year, or\n"
            "(B) the excess (if any) of—\n"
            "(i) the adjusted gross income (as defined in section 67(e)) for such taxable year, over\n"
            "(ii) the dollar amount at which the highest tax bracket in section 1(e) begins for such "
            "taxable year.\n"
            "(b) Threshold amount\n"
            "For purposes of this chapter, the term “threshold amount” means—\n"
            "(1) in the case of a taxpayer making a joint return under section 6013 or a surviving "
            "spouse (as defined in section 2(a)), $250,000,\n"
            "(2) in the case of a married taxpayer (as defined in section 7703) filing a separate "
            "return, ½ of the dollar amount determined under paragraph (1), and\n"
            "(3) in any other case, $200,000.\n"
            "(c) Net investment income\n"
            "For purposes of this chapter—\n"
            "(1) In general\n"
            "The term “net investment income” means the excess (if any) of—\n"
            "(A) the sum of—\n"
            "(i) gross income from interest, dividends, annuities, royalties, and rents, other than "
            "such income which is derived in the ordinary course of a trade or business not described "
            "in paragraph (2),\n"
            "(ii) other gross income derived from a trade or business described in paragraph (2), and\n"
            "(iii) net gain (to the extent taken into account in computing taxable income) attributable "
            "to the disposition of property other than property held in a trade or business not "
            "described in paragraph (2), over\n"
            "(B) the deductions allowed by this subtitle which are properly allocable to such gross "
            "income or net gain.\n"
            "(2) Trades and businesses to which tax applies\n"
            "A trade or business is described in this paragraph if such trade or business is—\n"
            "(A) a passive activity (within the meaning of section 469) with respect to the taxpayer, "
            "or\n"
            "(B) a trade or business of trading in financial instruments or commodities (as defined in "
            "section 475(e)(2)).\n"
            "(3) Income on investment of working capital subject to tax\n"
            "A rule similar to the rule of section 469(e)(1)(B) shall apply for purposes of this "
            "subsection.\n"
            "(4) Exception for certain active interests in partnerships and S corporations\n"
            "In the case of a disposition of an interest in a partnership or S corporation—\n"
            "(A) gain from such disposition shall be taken into account under clause (iii) of paragraph "
            "(1)(A) only to the extent of the net gain which would be so taken into account by the "
            "transferor if all property of the partnership or S corporation were sold for fair market "
            "value immediately before the disposition of such interest, and\n"
            "(B) a rule similar to the rule of subparagraph (A) shall apply to a loss from such "
            "disposition.\n"
            "(5) Exception for distributions from qualified plans\n"
            "The term “net investment income” shall not include any distribution from a plan or "
            "arrangement described in section 401(a), 403(a), 403(b), 408, 408A, or 457(b).\n"
            "(6) Special rule\n"
            "Net investment income shall not include any item taken into account in determining "
            "self-employment income for such taxable year on which a tax is imposed by section 1401(b).\n"
            "(d) Modified adjusted gross income\n"
            "For purposes of this chapter, the term “modified adjusted gross income” means adjusted "
            "gross income increased by the excess of—\n"
            "(1) the amount excluded from gross income under section 911(a)(1), over\n"
            "(2) the amount of any deductions (taken into account in computing adjusted gross income) "
            "or exclusions disallowed under section 911(d)(6) with respect to the amounts described in "
            "paragraph (1).\n"
            "(e) Nonapplication of section\n"
            "This section shall not apply to—\n"
            "(1) a nonresident alien, or\n"
            "(2) a trust all of the unexpired interests in which are devoted to one or more of the "
            "purposes described in section 170(c)(2)(B)."
        ),
        text_es=(
            "Resumen en español; el texto oficial está en inglés. Impone un impuesto de 3.8 por ciento "
            "a individuos sobre el menor entre el ingreso neto de inversión y el exceso del ingreso "
            "bruto ajustado modificado sobre la cantidad umbral, y una regla análoga para caudales y "
            "fideicomisos (a). Fija las cantidades umbral (b), define el ingreso neto de inversión (c) "
            "y el ingreso bruto ajustado modificado (d), y excluye a extranjeros no residentes y a "
            "ciertos fideicomisos (e)."
        ),
        effective_year=2010,
        source_url=USLM_TITLE26_SOURCE_URL,
        cross_references=["26 U.S.C. § 933", "26 CFR § 1.1411-1"],
        breadcrumbs="Title 26 > Subtitle A > Chapter 2A > § 1411 (OLRC release point 119-121)",
    ),
    StatuteRecord(
        id="us-irc-6012",
        jurisdiction="US",
        corpus="IRC",
        citation="26 U.S.C. § 6012",
        section="6012",
        title="Persons required to make returns of income / Personas obligadas a rendir planillas de ingreso",
        text_en=(
            "(a) General rule\n"
            "Returns with respect to income taxes under subtitle A shall be made by the following:\n"
            "(1)(A) Every individual having for the taxable year gross income which equals or exceeds "
            "the exemption amount, except that a return shall not be required of an individual—\n"
            "(i) who is not married (determined by applying section 7703), is not a surviving spouse "
            "(as defined in section 2(a)), is not a head of a household (as defined in section 2(b)), "
            "and for the taxable year has gross income of less than the sum of the exemption amount "
            "plus the basic standard deduction applicable to such an individual,\n"
            "(ii) who is a head of a household (as so defined) and for the taxable year has gross "
            "income of less than the sum of the exemption amount plus the basic standard deduction "
            "applicable to such an individual,\n"
            "(iii) who is a surviving spouse (as so defined) and for the taxable year has gross income "
            "of less than the sum of the exemption amount plus the basic standard deduction applicable "
            "to such an individual, or\n"
            "(iv) who is entitled to make a joint return and whose gross income, when combined with the "
            "gross income of his spouse, is, for the taxable year, less than the sum of twice the "
            "exemption amount plus the basic standard deduction applicable to a joint return, but only "
            "if such individual and his spouse, at the close of the taxable year, had the same "
            "household as their home.\n"
            "Clause (iv) shall not apply if for the taxable year such spouse makes a separate return or "
            "any other taxpayer is entitled to an exemption for such spouse under section 151(c).\n"
            "(B) The amount specified in clause (i), (ii), or (iii) of subparagraph (A) shall be "
            "increased by the amount of 1 additional standard deduction (within the meaning of section "
            "63(c)(3)) in the case of an individual entitled to such deduction by reason of section "
            "63(f)(1)(A) (relating to individuals age 65 or more), and the amount specified in clause "
            "(iv) of subparagraph (A) shall be increased by the amount of the additional standard "
            "deduction for each additional standard deduction to which the individual or his spouse is "
            "entitled by reason of section 63(f)(1).\n"
            "(C) The exception under subparagraph (A) shall not apply to any individual—\n"
            "(i) who is described in section 63(c)(5) and who has—\n"
            "(I) income (other than earned income) in excess of the sum of the amount in effect under "
            "section 63(c)(5)(A) plus the additional standard deduction (if any) to which the "
            "individual is entitled, or\n"
            "(II) total gross income in excess of the standard deduction, or\n"
            "(ii) for whom the standard deduction is zero under section 63(c)(6).\n"
            "(D) For purposes of this subsection—\n"
            "(i) The terms “standard deduction”, “basic standard deduction” and “additional standard "
            "deduction” have the respective meanings given such terms by section 63(c).\n"
            "(ii) The term “exemption amount” has the meaning given such term by section 151(d). In the "
            "case of an individual described in section 151(d)(2), the exemption amount shall be zero.\n"
            "(2) Every corporation subject to taxation under subtitle A;\n"
            "(3) Every estate the gross income of which for the taxable year is $600 or more;\n"
            "(4) Every trust having for the taxable year any taxable income, or having gross income of "
            "$600 or over, regardless of the amount of taxable income;\n"
            "(5) Every estate or trust of which any beneficiary is a nonresident alien;\n"
            "(6) Every political organization (within the meaning of section 527(e)(1)), and every fund "
            "treated under section 527(g) as if it constituted a political organization, which has "
            "political organization taxable income (within the meaning of section 527(c)(1)) for the "
            "taxable year;\n"
            "(7) Every homeowners association (within the meaning of section 528(c)(1)) which has "
            "homeowners association taxable income (within the meaning of section 528(d)) for the "
            "taxable year; and\n"
            "(8) Every estate of an individual under chapter 7 or 11 of title 11 of the United States "
            "Code (relating to bankruptcy) the gross income of which for the taxable year is not less "
            "than the sum of the exemption amount plus the basic standard deduction under section "
            "63(c)(2)(C);\n"
            "except that subject to such conditions, limitations, and exceptions and under such "
            "regulations as may be prescribed by the Secretary, nonresident alien individuals subject "
            "to the tax imposed by section 871 and foreign corporations subject to the tax imposed by "
            "section 881 may be exempted from the requirement of making returns under this section.\n"
            "(b) Returns made by fiduciaries and receivers\n"
            "(1) Returns of decedents\n"
            "If an individual is deceased, the return of such individual required under subsection (a) "
            "shall be made by his executor, administrator, or other person charged with the property of "
            "such decedent.\n"
            "(2) Persons under a disability\n"
            "If an individual is unable to make a return required under subsection (a), the return of "
            "such individual shall be made by a duly authorized agent, his committee, guardian, "
            "fiduciary or other person charged with the care of the person or property of such "
            "individual. The preceding sentence shall not apply in the case of a receiver appointed by "
            "authority of law in possession of only a part of the property of an individual.\n"
            "(3) Receivers, trustees and assignees for corporations\n"
            "In a case where a receiver, trustee in a case under title 11 of the United States Code, or "
            "assignee, by order of a court of competent jurisdiction, by operation of law or otherwise, "
            "has possession of or holds title to all or substantially all the property or business of a "
            "corporation, whether or not such property or business is being operated, such receiver, "
            "trustee, or assignee shall make the return of income for such corporation in the same "
            "manner and form as corporations are required to make such returns.\n"
            "(4) Returns of estates and trusts\n"
            "Returns of an estate, a trust, or an estate of an individual under chapter 7 or 11 of "
            "title 11 of the United States Code shall be made by the fiduciary thereof.\n"
            "(5) Joint fiduciaries\n"
            "Under such regulations as the Secretary may prescribe, a return made by one of two or more "
            "joint fiduciaries shall be sufficient compliance with the requirements of this section. A "
            "return made pursuant to this paragraph shall contain a statement that the fiduciary has "
            "sufficient knowledge of the affairs of the person for whom the return is made to enable "
            "him to make the return, and that the return is, to the best of his knowledge and belief, "
            "true and correct.\n"
            "(6) IRA share of partnership income\n"
            "In the case of a trust which is exempt from taxation under section 408(e), for purposes of "
            "this section, the trust’s distributive share of items of gross income and gain of any "
            "partnership to which subchapter C or D of chapter 63 applies shall be treated as equal to "
            "the trust’s distributive share of the taxable income of such partnership.\n"
            "(c) Certain income earned abroad or from sale of residence\n"
            "For purposes of this section, gross income shall be computed without regard to the "
            "exclusion provided for in section 121 (relating to gain from sale of principal residence) "
            "and without regard to the exclusion provided for in section 911 (relating to citizens or "
            "residents of the United States living abroad).\n"
            "(d) Tax-exempt interest required to be shown on return\n"
            "Every person required to file a return under this section for the taxable year shall "
            "include on such return the amount of interest received or accrued during the taxable year "
            "which is exempt from the tax imposed by chapter 1.\n"
            "(e) Consolidated returns\n"
            "For provisions relating to consolidated returns by affiliated corporations, see chapter 6.\n"
            "(f) Special rule for taxable years 2018 through 2025\n"
            "In the case of a taxable year beginning after December 31, 2017, and before January 1, "
            "2026, subsection (a)(1) shall not apply, and every individual who has gross income for the "
            "taxable year shall be required to make returns with respect to income taxes under subtitle "
            "A, except that a return shall not be required of—\n"
            "(1) an individual who is not married (determined by applying section 7703) and who has "
            "gross income for the taxable year which does not exceed the standard deduction applicable "
            "to such individual for such taxable year under section 63, or\n"
            "(2) an individual entitled to make a joint return if—\n"
            "(A) the gross income of such individual, when combined with the gross income of such "
            "individual’s spouse, for the taxable year does not exceed the standard deduction which "
            "would be applicable to the taxpayer for such taxable year under section 63 if such "
            "individual and such individual’s spouse made a joint return,\n"
            "(B) such individual and such individual’s spouse have the same household as their home at "
            "the close of the taxable year,\n"
            "(C) such individual’s spouse does not make a separate return, and\n"
            "(D) neither such individual nor such individual’s spouse is an individual described in "
            "section 63(c)(5) who has income (other than earned income) in excess of the amount in "
            "effect under section 63(c)(5)(A)."
        ),
        text_es=(
            "Resumen en español; el texto oficial está en inglés. Establece quiénes deben rendir "
            "planilla de contribución sobre ingresos (a), incluidos los individuos cuyo ingreso bruto "
            "iguala o excede la cantidad aplicable, corporaciones, caudales, fideicomisos y sociedades, "
            "y las planillas de fiduciarios y síndicos (b). Contiene reglas para ingreso devengado en "
            "el extranjero (c), la divulgación de intereses exentos (d), las planillas consolidadas (e) "
            "y una regla especial para los años 2018 a 2025 (f)."
        ),
        effective_year=1954,
        source_url=USLM_TITLE26_SOURCE_URL,
        cross_references=["26 U.S.C. § 63", "26 U.S.C. § 911"],
        breadcrumbs="Title 26 > Subtitle F > Chapter 61 > Subchapter A > Part II > Subpart B > § 6012 (OLRC release point 119-121)",
    ),
    StatuteRecord(
        id="us-irc-957",
        jurisdiction="US",
        corpus="IRC",
        citation="26 U.S.C. § 957",
        section="957",
        title=(
            "Controlled foreign corporations; United States persons / Corporaciones foráneas controladas; "
            "personas de los Estados Unidos"
        ),
        text_en=(
            "(a) General rule\n"
            "For purposes of this title, the term “controlled foreign corporation” means any foreign corporation "
            "if more than 50 percent of—\n"
            "(1) the total combined voting power of all classes of stock of such corporation entitled to vote, or\n"
            "(2) the total value of the stock of such corporation,\n"
            "is owned (within the meaning of section 958(a)), or is considered as owned by applying the rules of "
            "ownership of section 958(b), by United States shareholders on any day during the taxable year of "
            "such foreign corporation.\n"
            "(b) Special rule for insurance\n"
            "For purposes only of taking into account income described in section 953(a) (relating to insurance "
            "income), the term “controlled foreign corporation” includes not only a foreign corporation as "
            "defined by subsection (a) but also one of which more than 25 percent of the total combined voting "
            "power of all classes of stock (or more than 25 percent of the total value of stock) is owned (within "
            "the meaning of section 958(a)), or is considered as owned by applying the rules of ownership of "
            "section 958(b), by United States shareholders on any day during the taxable year of such "
            "corporation, if the gross amount of premiums or other consideration in respect of the reinsurance or "
            "the issuing of insurance or annuity contracts not described in section 953(e)(2) exceeds 75 percent "
            "of the gross amount of all premiums or other consideration in respect of all risks.\n"
            "(c) United States person\n"
            "For purposes of this subpart, the term “United States person” has the meaning assigned to it by "
            "section 7701(a)(30) except that—\n"
            "(1) with respect to a corporation organized under the laws of the Commonwealth of Puerto Rico, such "
            "term does not include an individual who is a bona fide resident of Puerto Rico, if a dividend "
            "received by such individual during the taxable year from such corporation would, for purposes of "
            "section 933(1), be treated as income derived from sources within Puerto Rico, and\n"
            "(2) with respect to a corporation organized under the laws of Guam, American Samoa, or the Northern "
            "Mariana Islands—\n"
            "(A) 80 percent or more of the gross income of which for the 3-year period ending at the close of the "
            "taxable year (or for such part of such period as such corporation or any predecessor has been in "
            "existence) was derived from sources within such a possession or was effectively connected with the "
            "conduct of a trade or business in such a possession, and\n"
            "(B) 50 percent or more of the gross income of which for such period (or part) was derived from the "
            "active conduct of a trade or business within such a possession,\n"
            "such term does not include an individual who is a bona fide resident of Guam, American Samoa, or the "
            "Northern Mariana Islands.\n"
            "For purposes of subparagraphs (A) and (B) of paragraph (2), the determination as to whether income "
            "was derived from the active conduct of a trade or business within a possession shall be made under "
            "regulations prescribed by the Secretary."
        ),
        text_es=(
            "Resumen en español; el texto oficial está en inglés. Define “corporación foránea controlada” como "
            "toda corporación foránea en la que accionistas de los Estados Unidos poseen más del 50 por ciento "
            "del poder combinado de voto o del valor de las acciones en cualquier día del año contributivo (a), "
            "con un umbral de 25 por ciento para el ingreso de seguros (b). Para fines de esta subparte, “persona "
            "de los Estados Unidos” no incluye, respecto a una corporación organizada bajo las leyes de Puerto "
            "Rico, al individuo residente de buena fe de Puerto Rico si un dividendo que reciba de dicha "
            "corporación se trataría como ingreso de fuentes dentro de Puerto Rico para fines de la sección "
            "933(1) (c)(1), y contiene una regla análoga para Guam, Samoa Americana y las Islas Marianas del "
            "Norte (c)(2)."
        ),
        effective_year=1962,
        source_url=USLM_TITLE26_SOURCE_URL,
        cross_references=["26 U.S.C. § 933", "26 CFR § 1.937-3"],
        breadcrumbs="Title 26 > Subtitle A > Chapter 1 > Subchapter N > Part III > Subpart F > § 957 (OLRC release point 119-121)",
    ),
    StatuteRecord(
        id="us-irc-6688",
        jurisdiction="US",
        corpus="IRC",
        citation="26 U.S.C. § 6688",
        section="6688",
        title=(
            "Assessable penalties with respect to information required to be furnished under section 7654 / "
            "Penalidades tasables respecto a información que debe suministrarse bajo la sección 7654"
        ),
        text_en=(
            "In addition to any criminal penalty provided by law, any person described in section 7654(a) who is "
            "required under section 937(c) or by regulations prescribed under section 7654 to furnish information "
            "and who fails to comply with such requirement at the time prescribed by such regulations unless it "
            "is shown that such failure is due to reasonable cause and not to willful neglect, shall pay (upon "
            "notice and demand by the Secretary and in the same manner as tax) a penalty of $1,000 for each such "
            "failure."
        ),
        text_es=(
            "Resumen en español; el texto oficial está en inglés. Además de cualquier penalidad criminal, toda "
            "persona descrita en la sección 7654(a) que deba suministrar información bajo la sección 937(c) o "
            "bajo los reglamentos de la sección 7654 y no cumpla a tiempo, salvo que demuestre causa razonable y "
            "no negligencia voluntaria, pagará una penalidad de $1,000 por cada incumplimiento."
        ),
        effective_year=1972,
        source_url=USLM_TITLE26_SOURCE_URL,
        cross_references=["26 U.S.C. § 937"],
        breadcrumbs="Title 26 > Subtitle F > Chapter 68 > Subchapter B > Part I > § 6688 (OLRC release point 119-121)",
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
        source_url=f"https://www.ecfr.gov/on/{ECFR_TITLE26_DATE}/title-26/section-1.937-1",
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
        source_url=f"https://www.ecfr.gov/on/{ECFR_TITLE26_DATE}/title-26/section-1.937-2",
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
            "(a) Scope. Section 937(b) and this section set forth the rules for determining whether income is "
            "effectively connected with the conduct of a trade or business within a particular possession (the "
            "relevant possession) for purposes of the Internal Revenue Code, including sections 881(b) and 957(c) "
            "and Subpart D, Part III, Subchapter N, Chapter 1 of the Internal Revenue Code. Paragraph (c) of this "
            "section does not apply, however, for purposes of section 881(b). In the case of a possession or "
            "territory that administers income tax laws that are identical (except for the substitution of the "
            "name of the possession or territory for the term “United States” where appropriate) to those in "
            "force in the United States, these rules do not apply for purposes of the application of such laws.\n"
            "(b) In general. Except as provided in paragraphs (c) and (d) of this section, the principles of "
            "section 864(c) and the regulations under that section (relating to the determination of income, gain "
            "or loss that is effectively connected with the conduct of a trade or business within the United "
            "States) generally will be applied in determining whether income is effectively connected with the "
            "conduct of a trade or business within the relevant possession, without regard to whether the "
            "taxpayer qualifies as a nonresident alien individual or a foreign corporation with respect to such "
            "possession. Subject to the rules of this section, the principles of section 864(c)(4) will apply for "
            "purposes of determining whether income from sources without the relevant possession is effectively "
            "connected with the conduct of a trade or business in the relevant possession. For purposes of the "
            "preceding sentence, all income other than income from sources within the relevant possession (as "
            "determined under the rules of § 1.937-2) will be considered income from sources without the relevant "
            "possession in the application of the principles of section 864(c) under this paragraph (b), it "
            "generally will be sufficient to substitute the name of the relevant possession for the term “United "
            "States” where appropriate, but additional substitutions may be necessary to accomplish the intent of "
            "section 937(b) and this section. In no case, however, will a bona fide resident or other person "
            "have, as a result of the application of these principles, more income effectively connected with the "
            "conduct of a trade or business in the relevant possession than the amount of U.S. effectively "
            "connected income that a similarly situated U.S. person who is not a bona fide resident would have "
            "under section 864(c).\n"
            "(c) U.S. income—(1) In general. Except as provided in paragraph (d) of this section, income "
            "considered to be effectively connected with the conduct of a trade or business within the relevant "
            "possession will not include any item of income determined under the rules of sections 861 through "
            "865 and the regulations under those provisions to be—\n"
            "(i) From sources within the United States; or\n"
            "(ii) Effectively connected with the conduct of a trade or business within the United States.\n"
            "(2) Conduit arrangements. Income will be considered to be from sources within the United States for "
            "purposes of paragraph (c)(1) of this section if, pursuant to a plan or arrangement—\n"
            "(i) The income is received in exchange for consideration provided to another person; and\n"
            "(ii) Such person (or another person) provides the same consideration (or consideration of a like "
            "kind) to a third person in exchange for one or more payments constituting income from sources within "
            "the United States.\n"
            "(d) Income from certain sales of inventory property. Paragraph (c) of this section will not apply to "
            "income from sales of inventory property described in § 1.863-3(e).\n"
            "(e) Examples. The provisions of this section may be illustrated by the following examples:\n"
            "Example 1. X is a bona fide resident of Possession I, a section 931 possession (as defined in § "
            "1.931-1(c)(1)). X has an office in Possession I from which X conducts a business consisting of the "
            "development and sale of specialized computer software. A purchaser of software will frequently pay X "
            "an additional amount to install the software on the purchaser's operating system and to ensure that "
            "the software is functioning properly. X performs the installation services at the purchaser's place "
            "of business, which may be in Possession I, in the United States, or in another country. The "
            "provision of such services is not de minimis and constitutes a separate transaction under the rules "
            "of § 1.861-18. Under the principles of section 864(c)(4) as applied pursuant to paragraph (b) of "
            "this section, the compensation that X receives for personal services performed outside of Possession "
            "I is not considered to be effectively connected with the conduct of a trade or business in "
            "Possession I for purposes of section 931(a)(2).\n"
            "Example 2. (i) F Bank is organized under the laws of Country FC and operates an active banking "
            "business from offices in the U.S. Virgin Islands (USVI). In connection with this banking business, F "
            "Bank makes loans to and receives interest payments from borrowers who reside in the USVI, in the "
            "United States, and in Country FC.\n"
            "(ii) Under the principles of section 861(a)(1) as applied pursuant to § 1.937-2(b), interest "
            "payments received by F Bank from borrowers who reside in the United States or in Country FC "
            "constitute income from sources outside of the USVI. Under the principles of section 864(c)(4) as "
            "applied pursuant to paragraph (b) of this section, interest income from sources outside of the USVI "
            "generally may constitute income that is effectively connected with the conduct of a trade or "
            "business within the USVI for purposes of the Internal Revenue Code. However, interest payments "
            "received by F Bank from borrowers who reside in the United States constitute income from sources "
            "within the United States under section 861(a)(1). Accordingly, under paragraph (c)(1) of this "
            "section, such interest income will not be treated as effectively connected with the conduct of a "
            "trade or business in the USVI for purposes of the Internal Revenue Code (for example, for purposes "
            "of section 934(b)). Interest payments received by F Bank from borrowers who reside in Country FC, "
            "however, may be treated as effectively connected with the conduct of a trade or business in the USVI "
            "for purposes of the Internal Revenue Code (including section 934(b)).\n"
            "(iii) To the extent that, as described in section 934(a), the USVI administers income tax laws that "
            "are identical (except for the substitution of the name of the USVI for the term “United States” "
            "where appropriate) to those in force in the United States, interest payments received by F Bank from "
            "borrowers who reside in the United States or in Country FC may be treated as income that is "
            "effectively connected with the conduct of a trade or business in the USVI for purposes of F Bank's "
            "income tax liability to the USVI under mirrored section 882.\n"
            "Example 3. (i) G is a partnership that is organized under the laws of, and that operates an active "
            "financing business from offices in, Possession I. Interests in G are owned by D, a bona fide "
            "resident of Possession I, and N, an alien individual who resides in Country FC. Pursuant to a "
            "pre-arrangement, G loans $x to T, a business entity organized under the laws of Country FC, and T in "
            "turn loans $y to E, a U.S. resident. In accordance with the arrangement, E pays interest to T, which "
            "in turn pays interest to G.\n"
            "(ii) The arrangement constitutes a conduit arrangement under paragraph (c)(2) of this section, and "
            "the interest payments received by G are treated as income from sources within the United States for "
            "purposes of paragraph (c)(1) of this section. Accordingly, the interest received by G will not be "
            "treated as effectively connected with the conduct of a trade or business in Possession I for "
            "purposes of the Internal Revenue Code (including sections 931(a)(2) and 934(b), if applicable with "
            "respect to D). Whether such interest constitutes income from sources within the United States for "
            "other purposes of the Internal Revenue Code under generally applicable conduit principles will "
            "depend on the facts and circumstances. See, for example, Aiken Indus., Inc. v. Commissioner, 56 T.C. "
            "925 (1971).\n"
            "(iii) If Possession I administers income tax laws that are identical (except for the substitution of "
            "the name of the possession for the term “United States” where appropriate) to those in force in the "
            "United States, the interest received by G may be treated as income effectively connected with the "
            "conduct of a trade or business in Possession I under mirrored section 864(c)(4) for purposes of "
            "determining the Possession I territorial income tax liability of N under mirrored section 871.\n"
            "(f) Effective/applicability date. Except as otherwise provided in this paragraph (f), this section "
            "applies to income earned in taxable years ending after April 9, 2008. Taxpayers may choose to apply "
            "paragraph (b) of this section to income earned in open taxable years ending after October 22, 2004.\n"
            "Amendment history (eCFR source note): adopted by T.D. 9391, 73 FR 19374, Apr. 9, 2008; amended by "
            "T.D. 9391, 73 FR 27728, May 14, 2008 (correction), T.D. 9921, 85 FR 79853, Dec. 11, 2020, and "
            "T.D. 10022, 90 FR 3003, Jan. 14, 2025."
        ),
        text_es=(
            "Resumen en español; el texto oficial está en inglés. Establece las reglas para determinar si un "
            "ingreso está efectivamente conectado con la conducción de una industria o negocio en una posesión "
            "(a), aplicando los principios de la sección 864(c) (b). Ese ingreso no incluye partidas de fuentes "
            "dentro de los Estados Unidos ni efectivamente conectadas con una industria o negocio en los Estados "
            "Unidos, incluidos los arreglos de conducto (c), salvo ciertas ventas de inventario (d). Incluye "
            "ejemplos (e) y aplica a ingresos de años contributivos que terminen después del 9 de abril de 2008 "
            "(f). "
            "Historial de enmiendas (nota de fuente del eCFR): adoptada por T.D. 9391, 73 FR 19374, 9 de abril "
            "de 2008; enmendada por T.D. 9391, 73 FR 27728, 14 de mayo de 2008 (corrección), T.D. 9921, 85 FR "
            "79853, 11 de diciembre de 2020, y T.D. 10022, 90 FR 3003, 14 de enero de 2025."
        ),
        effective_year=2008,
        source_url=f"https://www.ecfr.gov/on/{ECFR_TITLE26_DATE}/title-26/section-1.937-3",
        cross_references=["26 U.S.C. § 937", "26 CFR § 1.937-2", "26 U.S.C. § 957"],
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
            "individual (domiciled in Puerto Rico; presumed resident if present in Puerto Rico 183 days during the "
            "calendar year), foreign corporation, taxable year, and person."
        ),
        text_es=(
            "Define términos fundamentales bajo el Código de Rentas Internas de Puerto Rico (Ley 1-2011), incluyendo "
            "individuo residente (domiciliado en Puerto Rico; se presume residente si estuvo presente en Puerto Rico "
            "183 días durante el año natural), corporación extranjera y año contributivo."
        ),
        effective_year=2011,
        source_url="https://hacienda.pr.gov/sites/default/files/codigo_de_rentas_internas_2011_segun_enmendado_28.08.2026.pdf",
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
        source_url="https://hacienda.pr.gov/sites/default/files/codigo_de_rentas_internas_2011_segun_enmendado_28.08.2026.pdf",
        cross_references=["PR CRI Sec. 1010.01", "PR CRI Sec. 1031.02"],
        breadcrumbs="Ley 1-2011 > Subtítulo A > Capítulo 3 > Subcapítulo A > Sección 1031.01",
    ),
    StatuteRecord(
        id="pr-cri-1031-02",
        jurisdiction="PR",
        corpus="CRI",
        citation="PR CRI Sec. 1031.02",
        section="1031.02",
        title="Exenciones del Ingreso Bruto / Exemptions from Gross Income",
        text_en=(
            "English summary; the official text is in Spanish. Lists, in 38 paragraphs, the items of income "
            "exempt from tax under Subtitle A (a), including annuities (1), certain fringe benefits paid by an "
            "employer (2), interest on specified obligations, including those of the United States and of the "
            "Government of Puerto Rico (3), certain dividends (4), pensions from government retirement systems "
            "and qualified plans up to $15,000 a year for pensioners aged 60 or over on the last day of the "
            "taxable year and $11,000 a year in other cases (13), unemployment compensation (19), and amounts "
            "received from sources outside Puerto Rico by a United States citizen who establishes to the "
            "Secretary's satisfaction that they were not a resident of Puerto Rico during the entire taxable "
            "year, and, for an individual who changes residence to Puerto Rico, amounts received from sources "
            "outside Puerto Rico attributable to the period of residence outside Puerto Rico, in both cases "
            "with no deduction allowed for items allocable to the excluded amounts (36)(A)."
        ),
        text_es=(
            "(a) Las siguientes partidas de ingreso estarán exentas de tributación bajo este Subtítulo:\n"
            "(1) Anualidades. —\n"
            "(A) Anualidades de empleados. —\n"
            "(i) Si un contrato de anualidades fuere comprado por un patrono para un empleado bajo un plan con "
            "respecto al cual la aportación del patrono fuere deducible bajo la Sección 1033.09(a)(1)(B) o si un "
            "contrato de anualidades fuere comprado para un empleado por un patrono exento bajo la Sección "
            "1101.01 el empleado incluirá en su ingreso las cantidades recibidas bajo tal contrato para el año en "
            "que sean recibidas, excepto que si el empleado hubiera pagado parte del precio de la anualidad, la "
            "anualidad será incluida en su ingreso según se dispone en la Sección 1031.01(b)(11)(A), "
            "considerándose como el precio de la anualidad la cantidad aportada por el empleado.\n"
            "(ii) En todos los demás casos, si los derechos del empleado bajo el contrato fueren irrevocables "
            "excepto por falta del pago de primas futuras, la cantidad aportada por el patrono para dicho "
            "contrato de anualidades en o después que tales derechos se conviertan en irrevocables será incluida "
            "en el ingreso del empleado en el año en que la cantidad se aporte. Dicha cantidad, junto a "
            "cualesquiera cantidades aportadas por el empleado, constituirá el precio pagado por el contrato de "
            "anualidades para determinar el monto de la anualidad que debe incluirse en el ingreso del empleado "
            "bajo la Sección 1031.01(b)(11)(A).\n"
            "(B) Pólizas educacionales. — El monto recibido de las pólizas educacionales por el asegurado a la "
            "terminación o liquidación del seguro, ya sea el total de la póliza educacional o ya sea la póliza "
            "pagada en anualidades o mensualidades, estará exento de tributación por este Subtítulo hasta la "
            "cantidad de diez mil (10,000) dólares.\n"
            "(C) Anualidades de Aseguradores Internacionales. — Cantidades recibidas por un individuo no "
            "residente o por una corporación o sociedad extranjera no dedicada a industria o negocio en Puerto "
            "Rico como beneficios o intereses de cualquier clase con arreglo a un contrato de anualidad emitido "
            "por un asegurador internacional.\n"
            "(2) Ciertos beneficios marginales pagados por un patrono para sus empleados. — Las siguientes "
            "cantidades pagadas o acumuladas por un patrono para beneficio de un empleado:\n"
            "(A) Primas de seguros de vida. — Las primas pagadas por un patrono sobre pólizas de seguros de vida "
            "grupales o colectivas cubriendo la vida de sus empleados que cumplan con los requisitos del Artículo "
            "14.010 del Código de Seguros de Puerto Rico, según enmendado, hasta la cantidad de cincuenta mil "
            "(50,000) dólares de protección. Las primas atribuibles al costo de protección del seguro en exceso "
            "de este límite son tributables para el empleado en el año contributivo en que las mismas sean "
            "pagadas.\n"
            "(B) Plan flexible de beneficios. — Aquellas cantidades pagadas o acumuladas por un patrono para "
            "beneficio de un empleado bajo un plan flexible de beneficios serán exentas de contribución a tenor "
            "con la Sección 1032.06.\n"
            "(C) Pagos para el cuido de dependientes. — Las cantidades pagadas o acumuladas por un patrono para "
            "beneficio de un empleado de conformidad con lo dispuesto en la Sección 1032.07, siempre que dichos "
            "beneficios formen parte de un plan flexible de beneficios establecido de acuerdo con las "
            "disposiciones de la Sección 1032.06.\n"
            "(D) Aportaciones a planes de salud o de accidente. —Las aportaciones de un patrono a planes de salud "
            "o accidente de sus empleados para cubrir lesiones personales o enfermedad, ya sea mediante seguro, o "
            "en cualquier otra forma que cumpla con lo dispuesto en la Sección 1032.08, incluyendo aportaciones a "
            "una cuenta de ahorro de salud de un empleado conforme a la Sección 1081.04.\n"
            "(3) Intereses exentos de contribución. — Intereses sobre:\n"
            "(A) las obligaciones de los Estados Unidos, de cualquier estado o territorio de los Estados Unidos, "
            "o subdivisión política de los mismos y del Distrito de Columbia;\n"
            "(B) las obligaciones del Gobierno de Puerto Rico o cualesquiera de sus instrumentalidades o "
            "subdivisiones políticas;\n"
            "(C) valores emitidos en virtud de la Ley de Préstamos Agrícolas de 1971, o en virtud de las "
            "disposiciones de dicha ley, según sea enmendada, incluyendo obligaciones emitidas por cualquier "
            "subsidiaria de los “Farm Credit Banks of Baltimore” dedicada a financiar directa o indirectamente "
            "préstamos agrícolas y a agricultores en Puerto Rico con dichos fondos; incluyendo, préstamos a "
            "residentes rurales para financiar vivienda rural, préstamos a cooperativas poseídas y controladas "
            "por agricultores y dedicadas al mercadeo o distribución de productos agrícolas, la compra de "
            "materiales, o a proveer servicios a negocios agrícolas, y la adquisición de préstamos o descuento de "
            "notas ya concedidas;\n"
            "(D) hipotecas aseguradas en virtud de las disposiciones de la Ley Nacional de Hogares (“National "
            "Housing Act”), aprobada en junio 27 de 1934, o en virtud de las disposiciones de dicha ley según sea "
            "enmendada, que sean:\n"
            "(i) otorgadas no más tarde del 15 de febrero de 1973, y poseídas el 5 de mayo de 1973, por personas "
            "residentes de Puerto Rico; y\n"
            "(ii) otorgadas dentro de los ciento ochenta (180) días siguientes al 15 de febrero de 1973, y para "
            "adquirir las cuales tenga contraída en dicha fecha una obligación contractual de adquirirlas una "
            "persona residente de Puerto Rico;\n"
            "(E) hipotecas sobre propiedad residencial localizada en Puerto Rico otorgadas después del 30 de "
            "junio de 1983 y antes del 1 de agosto de 1997, y aseguradas o garantizadas en virtud de las "
            "disposiciones de la Ley Nacional de Hogares (“National Housing Act”), aprobada el 27 de junio de "
            "1934, según enmendada, o en virtud de las disposiciones de la Ley de Reajuste de los Miembros del "
            "Servicio de 1944 (“Servicemen's Readjustment Act of 1944”), según enmendada.\n"
            "(F) hipotecas sobre propiedad residencial localizada en Puerto Rico otorgadas después del 31 de "
            "julio de 1997 y antes del 1 de enero de 2014 que sean de construcción nueva y hayan sido otorgadas "
            "coetáneamente con la primera transmisión de la propiedad hipotecada a un nuevo titular. Para fines "
            "de ésta sección el término “construcción nueva” significará propiedad residencial recién edificada y "
            "que ésta sea asegurada o garantizada en virtud de las disposiciones de la Ley Nacional de Hogares "
            "“National Housing Act” aprobada el 27 de junio de 1934, según enmendada, o en virtud de las "
            "disposiciones de la Ley de Reajuste de los Miembros del Servicio de 1944 “Servicemen's Readjustment "
            "Act of 1944”, según enmendada.\n"
            "(G) hipotecas originadas para proveer financiamiento permanente para la construcción o adquisición "
            "de vivienda de interés social, tales como la Ley Núm. 124 de 10 de diciembre de 1993, administradas "
            "por el Departamento de la Vivienda y los programas de viviendas de interés social auspiciados por el "
            "Gobierno Federal;\n"
            "(H) hipotecas aseguradas por el Secretario de Agricultura de los Estados Unidos de América en virtud "
            "de las disposiciones de la Ley del Congreso conocida por “Bankhead-Jones Farm Tenant Act”, aprobada "
            "el 22 de julio de 1937, o en virtud de las disposiciones de dicha ley según sea enmendada;\n"
            "(I) obligaciones aseguradas o garantizadas en virtud de las disposiciones de la Ley de Reajuste de "
            "los Miembros del Servicio de 1944 (“Servicemen's Readjustment Act of 1944”), o en virtud de las "
            "disposiciones de dicha ley, según sea enmendada, que sean:\n"
            "(i) otorgadas no más tarde del 15 de febrero de 1973, y poseídas al 5 de mayo de 1973, por personas "
            "residentes de Puerto Rico.\n"
            "(ii) otorgadas dentro de los ciento ochenta (180) días siguientes al 15 de febrero de 1973, y para "
            "adquirir las cuales tenga contraída en dicha fecha una obligación contractual de adquirirlas una "
            "persona residente de Puerto Rico;\n"
            "(J) valores emitidos por asociaciones cooperativas organizadas y operadas bajo las disposiciones de "
            "la Ley Núm. 239 de 1 de septiembre de 2004, conocida como “Ley General de Asociaciones Cooperativas "
            "de 2004”, o bajo las disposiciones de la Ley Núm. 225 de 28 de octubre de 2002, conocida como Ley de "
            "Sociedades Cooperativas de Ahorro y Crédito de 2002, hasta un máximo de cinco mil (5,000) dólares;\n"
            "(K) depósitos en cuentas que devenguen intereses, en cooperativas, asociaciones de ahorro "
            "autorizadas por el Gobierno Federal, o por el Gobierno de Puerto Rico, bancos comerciales y "
            "mutualistas o en cualquier otra organización de carácter bancario radicada en Puerto Rico, hasta la "
            "cantidad total de dos mil (2,000) dólares por cada contribuyente que sea individuo. En el caso de un "
            "contribuyente que rinda planilla conjunta con su cónyuge, la exclusión no excederá de cuatro mil "
            "(4,000) dólares. Si los cónyuges que viven juntos optan por rendir planillas separadas, la exclusión "
            "para cada uno no excederá de dos mil (2,000) dólares. Esta disposición es aplicable a la porción de "
            "los intereses pagados o acreditados sobre depósitos en cuentas que devenguen intereses que le "
            "pertenezcan a uno (1) o más individuos, sucesiones o fideicomisos y estén registrados a nombre de "
            "una casa de corretaje como nominatario. También será de aplicación a aquella parte de cualquier "
            "cantidad pagada o distribuida de una cuenta de retiro individual que consista de intereses de los "
            "descritos en la Sección 1023.04. Disponiéndose que, para años contributivos comenzados después del "
            "31 de diciembre de 2018, el monto de la exención dispuesta en este inciso será de cien (100) dólares "
            "por cada contribuyente que sea individuo. En el caso de un contribuyente que rinda planilla conjunta "
            "con su cónyuge, la exclusión no excederá de doscientos (200) dólares. Si los cónyuges que viven "
            "juntos optan por rendir planillas separadas, la exención para cado uno no excederá de cien (100) "
            "dólares.\n"
            "(L) obligaciones emitidas por —\n"
            "(i) el Fideicomiso de Conservación de Puerto Rico, según el mismo ha sido creado y es operado bajo "
            "la Escritura Núm. 5 de 23 de enero de 1970, otorgada ante el Notario Luis F. Sánchez Vilella;\n"
            "(ii) el Fideicomiso de Vivienda y Desarrollo Humano de Puerto Rico, según el mismo ha sido creado y "
            "es operado bajo la Escritura Núm. 135 de 15 de mayo de 2004, otorgada ante el Notario José Orlando "
            "Mercado Gelys;\n"
            "(iii) el Patronato de Monumentos de San Juan, siempre y cuando el mismo obtenga y mantenga una "
            "exención bajo la Sección 1101.01 del Código, y cuyo propósito sea recaudar fondos para la "
            "restauración y mantenimiento de la Iglesia San José y la Catedral de San Juan Bautista localizadas "
            "en el Viejo San Juan; y\n"
            "(iv) Sociedad de Educación y Rehabilitación de Puerto Rico (SER de Puerto Rico), siempre y cuando el "
            "mismo obtenga y mantenga una exención bajo la Sección 1101.01 del Código. La exclusión del ingreso "
            "bruto y la exención de tributación de los intereses que generan las obligaciones mencionadas en este "
            "inciso (L) no se verán afectadas por el hecho de que la fuente de fondos para el pago de dichos "
            "intereses provengan directa o indirectamente de otras obligaciones o instrumentos financieros que no "
            "disfruten de un tratamiento contributivo similar al de las obligaciones mencionadas en este inciso "
            "(L).\n"
            "(M) préstamos otorgados por un banco comercial o cualquier otro organismo de carácter bancario o "
            "financiero radicado en Puerto Rico a una corporación especial propiedad de trabajadores, cuyo "
            "producto sea utilizado por dicha corporación especial para uno de los siguientes propósitos:\n"
            "(i) financiar la adquisición, desarrollo, construcción, expansión, rehabilitación o mejoras de "
            "propiedad inmueble localizada en Puerto Rico a ser utilizada para propósitos comerciales por parte "
            "de la corporación especial, siempre y cuando el préstamo no exceda del costo de la propiedad o "
            "mejoras a realizarse respecto a ésta;\n"
            "(ii) financiar la adquisición de maquinaria u otra propiedad mueble tangible a ser utilizada para "
            "propósitos comerciales por parte de la corporación especial respecto a sus operaciones de negocio en "
            "Puerto Rico, siempre y cuando el préstamo no exceda del precio de compra de dicha propiedad; o\n"
            "(iii) financiar la adquisición de acciones de capital en una corporación, que sea tratada para "
            "propósitos de contribución sobre ingresos de Puerto Rico como una compra de activos de dicha "
            "corporación;\n"
            "(N) préstamos otorgados por un banco comercial o cualquier otro organismo de carácter bancario o "
            "financiero radicado en Puerto Rico a una persona para la adquisición y pago de certificados de "
            "membresía como miembro ordinario, extraordinario o corporativo, según corresponda, en una "
            "corporación especial propiedad de trabajadores;\n"
            "(O) préstamos otorgados por un banco comercial o cualquier otro organismo de carácter bancario o "
            "financiero radicado en Puerto Rico a una persona para la compra o inversión por sí y para sí de "
            "acciones preferidas de una corporación especial propiedad de trabajadores.\n"
            "(P) préstamos a pequeñas y medianas empresas. — Los intereses sobre préstamos de hasta doscientos "
            "cincuenta mil (250,000) dólares en el agregado por negocio exento a pequeñas o medianas empresas "
            "según se define dicho término en la Ley Núm. 73 de 28 de mayo de 2008, conocida como “Ley de "
            "Incentivos Económicos para el Desarrollo de Puerto Rico” para su establecimiento o expansión, "
            "siempre que el préstamo cumpla con los requisitos establecidos en la Ley conocida como “Community "
            "Reinvestment Act of 1977”, “Pub. Law 95-128, 91 Stat. 1147” según enmendada, y aquellos requisitos "
            "que por reglamento establezca el Comisionado de Instituciones Financieras.\n"
            "(Q) préstamos para capitalización de pequeñas y medianas empresas. — Los intereses sobre préstamos "
            "hasta doscientos cincuenta mil (250,000) dólares en el agregado por negocio exento otorgados a "
            "accionistas de negocios exentos para ser utilizados en la capitalización inicial o el subsiguiente "
            "requerimiento de capital de negocios exentos de una pequeña o mediana empresa, según dicho término "
            "se define en la Sección 2 (i) de la Ley Núm. 73 de 28 de mayo de 2008, conocida como la “Ley de "
            "Incentivos Económicos para el Desarrollo de Puerto Rico”. Cualquier persona que posea obligaciones "
            "de las enumeradas en este párrafo, deberá someter con la planilla requerida por este Subtítulo, un "
            "estado demostrativo del número o cantidad de dichas obligaciones que posea y de los ingresos "
            "recibidos de las mismas, en la forma y suministrando la información que el Secretario requiera.\n"
            "(4) Dividendos. —\n"
            "(A) Los dividendos recibidos por corporaciones distribuidos de ingreso de desarrollo industrial "
            "provenientes de intereses sobre:\n"
            "(i) obligaciones del Gobierno de Puerto Rico o de cualesquiera de sus instrumentalidades o "
            "subdivisiones políticas;\n"
            "(ii) hipotecas aseguradas por la Autoridad de Financiamiento de la Vivienda de Puerto Rico "
            "adquiridas después del 31 de marzo de 1977; o\n"
            "(iii) préstamos u otros valores con garantía hipotecaria otorgados por cualquier sistema de "
            "pensiones o de retiro de carácter general establecido por la Asamblea Legislativa de Puerto Rico, "
            "los municipios y las agencias, instrumentalidades y corporaciones del Gobierno de Puerto Rico, "
            "adquiridos después del 31 de marzo de 1977.\n"
            "(B) Distribuciones de corporaciones de dividendos limitados. — En el caso de un individuo, los "
            "dividendos que reciba de una corporación de dividendos limitados que cualifique bajo la Sección "
            "1101.01(a)(6)(A). El contribuyente deberá someter con la planilla un estado demostrativo del número "
            "de acciones de la corporación de dividendos limitados que posea y de los dividendos recibidos sobre "
            "dichas acciones durante el año contributivo.\n"
            "(C) Dividendos de asociaciones cooperativas. — El ingreso recibido por residentes de Puerto Rico por "
            "concepto de distribución de dividendos efectuados por asociaciones cooperativas domésticas.\n"
            "(D) Dividendos de un Asegurador Internacional o de una Compañía Tenedora del Asegurador "
            "Internacional. — Distribuciones de dividendos efectuados por un Asegurador Internacional o una "
            "Compañía Tenedora del Asegurador Internacional a tenor con lo establecido en el Artículo 61.240 del "
            "Código de Seguros de Puerto Rico.\n"
            "(5) Sacerdotes o ministros. — El valor razonable de alquiler de una casa de vivienda y sus "
            "pertenencias, así como el pago del agua, luz, gas y teléfono concedidos a un sacerdote o ministro de "
            "cualquier religión debidamente ordenado, como parte de su compensación. Para ser elegible para la "
            "exención dispuesta en este párrafo, los gastos por este concepto no pueden ser suntuosos ni "
            "extravagantes según determine el Secretario.\n"
            "(6) Incentivos recibidos por agricultores. —\n"
            "(A) En el caso de cualquier agricultor, los incentivos que reciba del Gobierno de Puerto Rico para:\n"
            "(i) la compra de maquinaria agrícola utilizada para la producción de caña de azúcar, café, "
            "vegetales, arroz y frutos alimenticios;\n"
            "(ii) la compra de abono, yerbicida, fungicida y materiales para el mejoramiento del terreno, para la "
            "apicultura, maricultura, acuacultura y otros que determine o adicione el Secretario de Agricultura;\n"
            "(iii) la compra de alambre, postes, materiales para los cercados de ganado y otros que determine o "
            "adicione el Secretario de Agricultura; y\n"
            "(iv) para la construcción de facilidades ganaderas, porcinas, avícolas y embarcaciones pesqueras.\n"
            "(B) Estos incentivos se considerarán una reducción de los costos o gastos, según sea el caso, "
            "incurridos por el agricultor. El Secretario de Agricultura y/o el Secretario del Departamento de "
            "Desarrollo Económico y Comercio, según aplique, deberá someter al Secretario anualmente, en o antes "
            "del 31 de enero del año siguiente al año natural en que se otorgaron los referidos incentivos, una "
            "relación conteniendo la siguiente información:\n"
            "(i) nombre, número de cuenta y dirección de cada agricultor a quien se han concedido los incentivos, "
            "y\n"
            "(ii) concepto y monto de los incentivos.\n"
            "(7) Mejoras efectuadas por el arrendatario en la propiedad del arrendador. — El ingreso, que no sea "
            "rentas, derivado por el arrendador de propiedad inmueble a la terminación del arrendamiento que "
            "represente el valor atribuible a edificaciones o a otras mejoras efectuadas por el arrendatario en "
            "dicha propiedad.\n"
            "(8) Recobro de deudas incobrables, contribuciones anteriores, recargos y otras partidas. — El "
            "ingreso atribuible al recobro, durante el año contributivo, de una deuda incobrable, contribución "
            "anterior, recargo u otra partida, hasta el monto del recobro excluible con respecto a dicha deuda, "
            "contribución, recargo u otra partida. Para los fines de este párrafo:\n"
            "(A) Definición de deuda incobrable. — El término “deuda incobrable” significa una deuda por cuya "
            "pérdida parcial o total de valor se concedió una deducción bajo este Subtítulo o bajo cualquier ley "
            "de contribuciones sobre ingresos anterior para cualquier año contributivo anterior.\n"
            "(B) Definición de contribución anterior. — El término “contribución anterior” significa una "
            "contribución con respecto a la cual se concedió una deducción o crédito para un año contributivo "
            "anterior.\n"
            "(C) Definición de recargo. — El término “recargo” significa una cantidad pagada o acumulada con "
            "respecto a la cual se concedió una deducción o crédito para un año contributivo anterior y que es "
            "atribuible a no haberse rendido planilla con respecto a una contribución o a no haberse pagado una "
            "contribución, dentro del término dispuesto por este Subtítulo o bajo la ley de contribuciones sobre "
            "ingresos bajo la cual se impone la contribución, o a no haberse rendido planilla en relación con una "
            "contribución o a no haberse pagado una contribución.\n"
            "(D) Definición de otra partida. — El término “otra partida” significa una cantidad pagada o "
            "acumulada con respecto a la cual se concedió una deducción para un año contributivo anterior.\n"
            "(E) Definición de recobro excluible. — El término “recobro excluible” respecto a una deuda "
            "incobrable, contribución anterior o recargo significa el monto, determinado de acuerdo con los "
            "reglamentos promulgados por el Secretario, de las deducciones o créditos concedidos por concepto de "
            "dicha deuda incobrable, contribución anterior o recargo, que no resultó en una reducción de la "
            "contribución del contribuyente bajo este Subtítulo, sin incluir la contribución impuesta por la "
            "Sección 1022.05, o bajo disposiciones correspondientes de leyes anteriores de contribuciones sobre "
            "ingresos, reducido por el monto excluible bajo este párrafo en años contributivos anteriores con "
            "respecto a dicha deuda, contribución o recargo.\n"
            "(F) Reglas especiales aplicables en el caso de la contribución impuesta por la Sección 1022.05. — En "
            "la aplicación de los incisos (A), (B), (C), (D) y (E) de este párrafo, al determinarse la "
            "contribución impuesta por la Sección 1022.05, un recobro excluible será concedido para los fines de "
            "dicha sección, haya o no la deuda incobrable, contribución anterior o recargo resultado en una "
            "reducción de la contribución impuesta por la Sección 1022.05 para el año contributivo anterior; y en "
            "el caso de una deuda incobrable, contribución anterior o recargo no admisible como deducción o "
            "crédito para el año contributivo anterior bajo este Subtítulo, excluyendo la Sección 1022.05, pero "
            "admisible para el mismo año contributivo bajo dicha sección, un recobro excluible será admisible "
            "para los fines de dicha sección, si dicha deuda incobrable, contribución anterior o recargo no "
            "resultó en una reducción de la contribución bajo dicha Sección 1022.05. Según se utilizan en este "
            "inciso, las referencias en este Subtítulo, y a la Sección 1022.05, en caso de años contributivos no "
            "sujetos a este Subtítulo, se entenderán hechas a las disposiciones correspondientes de las leyes "
            "anteriores de contribuciones sobre ingresos.\n"
            "(9) Estipendios recibidos por ciertos médicos durante el período de internado. —El estipendio "
            "recibido por un médico durante su período de internado bajo un contrato suscrito con el Departamento "
            "de Salud de Puerto Rico o con cualquier municipio o subdivisión política del mismo para recibir "
            "entrenamiento médico mediante la práctica en un hospital. Esta exclusión aplicará tanto al "
            "estipendio por concepto de subsidio mensual como al subsidio adicional para el pago de vivienda y "
            "comidas, y se concederá por un período máximo de setenta y dos (72) meses.\n"
            "(10) Pagos por licenciamiento al personal militar y naval. — Cantidades recibidas durante el año "
            "contributivo como pagos por licenciamiento en relación con servicio en las fuerzas militares o "
            "navales de los Estados Unidos.\n"
            "(11) Ingreso de agencias o sindicatos de noticias. — El ingreso de agencias o sindicatos de noticias "
            "o de otros servicios de prensa, recibido de empresas periodísticas o de radiodifusión, por concepto "
            "de rentas o cánones (“royalties”) por el uso o publicación de o por el derecho de usar o publicar en "
            "Puerto Rico, propiedad literaria o artística de dichas agencias o sindicatos.\n"
            "(12) Premios de la Lotería de Puerto Rico y de la Lotería Adicional. — Las cantidades recibidas por "
            "concepto de premios de la Lotería de Puerto Rico y los premios de la Lotería Adicional.\n"
            "(13) Las cantidades recibidas por concepto de pensiones concedidas o a concederse por los sistemas o "
            "fondos de retiro subvencionados por el Gobierno de Puerto Rico, de anualidades o pensiones "
            "concedidas por el Gobierno de los Estados Unidos de América, y por las instrumentalidades o "
            "subdivisiones políticas de ambos gobiernos, y de planes de pensiones, retiro o anualidades "
            "cualificadas bajo las disposiciones de la Sección 1081.01, concedidas por patronos de la empresa "
            "privada, hasta el límite que se dispone a continuación:\n"
            "(A) En el caso de pensionados que al último día del año contributivo tengan sesenta (60) años o más, "
            "la exención será de quince mil (15,000) dólares anuales.\n"
            "(B) En los demás casos, la exención será de once mil (11,000) dólares anuales.\n"
            "(C) La exención concedida en este párrafo aplicará solamente a cantidades recibidas por concepto de "
            "separación de empleo en forma de anualidad o de pagos periódicos. Disponiéndose que, para años "
            "comenzados luego del 31 de diciembre de 2018, la exención concedida en este párrafo aplicará "
            "solamente a cantidades pagadas al participante o, luego de su muerte, a su beneficiario, mediante "
            "una pensión o anualidad vitalicia o pagos periódicos completados luego de la fecha en que el "
            "participante ha terminado su empleo con el patrono gubernamental o privado que mantiene o participa "
            "en el sistema, fondo o plan que realiza el pago.\n"
            "(D) Para propósitos de esta Sección 1031.02(a)(13) y la Sección 1081.01, “pagos periódicos” "
            "significa:\n"
            "(i) pagos realizados durante un periodo fijo de al menos cinco (5) años en cantidades "
            "substancialmente similares; o\n"
            "(ii) pagos mínimos requeridos bajo el Código de Rentas Internas de los Estados Unidos de 1986, según "
            "enmendado, o cualquier disposición legal sucesora (Minimum Required Distributions).\n"
            "(14) Las cantidades recibidas por los pensionados del Sistema de Retiro de los Empleados del "
            "Gobierno de Puerto Rico y sus Instrumentalidades, los del Sistema de Retiro de la Judicatura, los "
            "del Sistema de Retiro de la Universidad de Puerto Rico y los Pensionados del Sistema de Pensiones y "
            "Anualidades para los Maestros por concepto del Aguinaldo de Navidad y del Bono de Verano concedidos "
            "por la Ley Núm. 37 y la Ley Núm. 38 de 13 de junio de 2001, y del Bono de Medicamentos concedido por "
            "la Ley Núm. 155 de 27 de junio de 2003 y la Ley Núm. 162 de 15 de julio de 2003.\n"
            "(15) Premios de hipódromos. — El monto de los premios obtenidos en “pools”, bancas, dupletas, "
            "quinielas (“daily double”), exacta, “subscription funds” y cualquier otra jugada en hipódromos de "
            "Puerto Rico.\n"
            "(16) Ganancia en la venta o permuta de residencia principal por ciertos individuos. —\n"
            "(A) Regla general. — En el caso de un individuo, la ganancia realizada en la venta o permuta de su "
            "residencia principal, hasta la cantidad de ciento cincuenta mil (150,000) dólares por contribuyente. "
            "Esta exclusión aplicará si —\n"
            "(i) el individuo opta por esta exclusión;\n"
            "(ii) el individuo tiene sesenta (60) años de edad o más en la fecha de dicha venta o permuta; y\n"
            "(iii) durante el período de cinco (5) años terminado en la fecha de dicha venta o permuta dicha "
            "propiedad ha sido poseída y usada por el individuo como su residencia principal por períodos totales "
            "de tres (3) años o más.\n"
            "(B) Limitación. — El inciso (A) no aplicará a cualquier ganancia en una venta o permuta si el "
            "contribuyente ha ejercido una opción anteriormente con respecto a la exclusión de ganancia en una "
            "venta o permuta de su residencia principal bajo este párrafo, ni a cualquier cantidad distribuida de "
            "una cuenta de retiro individual conforme a las disposiciones de la Sección 1081.02(d)(6).\n"
            "(C) Elección. — Una elección bajo las disposiciones del inciso (A) puede ser hecha en cualquier "
            "momento antes de la expiración del período para establecer una reclamación de crédito o reintegro de "
            "las contribuciones impuestas por este Subtítulo para el año contributivo en que se realiza la venta "
            "o permuta, y deberá ser hecha por escrito y, una vez se emita reglamentación al efecto, en aquella "
            "forma que el Secretario disponga por reglamento. En el caso de un contribuyente que sea casado, la "
            "elección podrá ser hecha únicamente si el cónyuge se une a éste en dicha elección.\n"
            "(D) Reglas especiales. —\n"
            "(i) Para fines de este párrafo, si el contribuyente tiene acciones como socio-partícipe en una "
            "asociación cooperativa de viviendas (según dichos términos son definidos en la Sección "
            "1033.15(a)(2)(B)), el requisito del período de posesión especificado en el inciso (A)(iii) de este "
            "párrafo y los requisitos de dicho inciso se aplicarán a la casa o apartamento que el contribuyente "
            "tenía derecho a ocupar como tal socio-partícipe.\n"
            "(ii) Para fines de este párrafo, la destrucción en todo o en parte, robo, incautación o ejercicio "
            "del poder de requisición o expropiación forzosa o la amenaza o inminencia de ello, se tratará como "
            "una venta o permuta de la residencia.\n"
            "(iii) En el caso de propiedad una parte de la cual ha sido poseída y usada por el individuo como su "
            "residencia principal por períodos totales de tres (3) años o más durante el período de cinco (5) "
            "años terminado en la fecha de la venta o permuta, este párrafo se aplicará con respecto a aquella "
            "parte de la ganancia en la venta o permuta de dicha propiedad, según se determine bajo reglamentos "
            "promulgados por el Secretario, que sea atribuible a la parte de la propiedad así poseída y usada por "
            "el contribuyente.\n"
            "(iv) Al aplicar la Sección 1034.04(m) (relacionada a la venta o permuta de residencia) la cantidad "
            "realizada en la venta o permuta de propiedad será tratada como la cantidad determinada, sin "
            "considerar este párrafo, reducida por la cantidad de ganancias excluida del ingreso bruto conforme a "
            "una opción bajo este párrafo.\n"
            "(v) En el caso de propiedad poseída conjuntamente por cónyuges, si ambos son propietarios comunes de "
            "la propiedad, y uno de los cónyuges satisface los requisitos de edad, posesión y uso establecidos en "
            "el inciso (A) respecto a dicha propiedad, entonces ambos cónyuges serán considerados como que "
            "satisfacen dichos requisitos de edad, posesión y uso.\n"
            "(vi) En el caso de una venta o permuta de propiedad adquirida por el cónyuge supérstite mediante "
            "legado del cónyuge fallecido, si —\n"
            "(I) el cónyuge fallecido (durante el período de cinco (5) años terminado en la fecha de la venta o "
            "permuta) hubiere satisfecho los requisitos de posesión y uso establecidos en el inciso (A) con "
            "respecto a dicha propiedad, y\n"
            "(II) no está en vigor ninguna elección hecha por el cónyuge fallecido bajo las disposiciones del "
            "inciso (A) respecto a una venta o permuta efectuada con anterioridad, entonces dicho cónyuge "
            "supérstite será considerado como que satisface los requisitos de posesión y uso establecidos en "
            "dicho inciso respecto a dicha propiedad.\n"
            "(vii) Para fines de este párrafo —\n"
            "(I) la determinación de si un individuo es casado se hará a la fecha de la venta o permuta; y\n"
            "(II) un individuo casado que no vive con su cónyuge o un individuo legalmente separado de su cónyuge "
            "bajo un decreto de divorcio o separación no será considerado como casado.\n"
            "(17) Ciertos ingresos relacionados con la operación de corporaciones especiales propiedad de "
            "trabajadores — Se eximen de contribución los siguientes ingresos:\n"
            "(A) Los ingresos de las corporaciones especiales propiedad de trabajadores que sean acreditados a la "
            "cuenta colectiva de reserva y al fondo social. Para que las aportaciones al fondo social cualifiquen "
            "para la exención aquí concedida será necesario que los beneficios del fondo social estén accesibles "
            "a todos los residentes del municipio donde la corporación especial propiedad de trabajadores tiene "
            "establecida su oficina principal, excepto para los miembros ordinarios y corporativos de la "
            "corporación; además, se le deberá demostrar al Secretario que el fondo social se utiliza para los "
            "fines señalados en el Artículo 1507 de la Ley Núm. 144 de 10 de agosto de 1995, según enmendada.\n"
            "(B) La proporción de los avisos de crédito por productividad y por patrocinio que las corporaciones "
            "especiales propiedad de trabajadores capitalicen en las cuentas internas de capital de los miembros "
            "ordinarios y miembros extraordinarios y que no puedan distribuirse o pagarse hasta que el miembro "
            "ordinario o extraordinario cese toda relación con la corporación especial propiedad de trabajadores. "
            "Cualquier cantidad de los avisos de crédito por productividad y por patrocinio que se acoja a la "
            "exención provista en esta sección y que posteriormente se distribuya al miembro ordinario o "
            "extraordinario con anterioridad a que éste cese toda relación con la corporación especial propiedad "
            "de trabajadores estará sujeta a una penalidad por una cantidad igual al diez (10) por ciento de la "
            "cantidad distribuida y se deberá incluir como ingreso bruto tributable en dicho año. La anterior "
            "penalidad de diez (10) por ciento será retenida por la corporación especial propiedad de "
            "trabajadores y remitida de inmediato al Secretario.\n"
            "(C) El noventa (90) por ciento del ingreso recibido por una persona por concepto de renta de "
            "propiedad inmueble y mueble de cualquier naturaleza, utilizada por una corporación especial "
            "propiedad de trabajadores en su desarrollo, organización, construcción, establecimiento u operación.\n"
            "(18) Cuota de Ajuste por Costo de Vida. — La cuota de ajuste por costo de vida recibida por "
            "empleados del Gobierno de los Estados Unidos de América que trabajan en Puerto Rico hasta el monto "
            "que esté exenta de tributación para fines de la contribución sobre ingresos impuesta por el Código "
            "de Rentas Internas Federal. El contribuyente deberá incluir con la planilla evidencia que demuestre "
            "el monto de la cuota de ajustes por concepto de costo de vida recibida durante el año. Recaerá en el "
            "Departamento la responsabilidad de verificar que los contribuyentes hayan cumplido con su deber "
            "contributivo en los cuatro (4) años anteriores al año de radicación. En el caso que no hayan "
            "cumplido con su deber contributivo, el Departamento podrá revocarle el privilegio concedido en este "
            "párrafo y el contribuyente tendrá que pagar el monto adeudado con penalidades y recargos.\n"
            "(19) Compensación por desempleo. — Las cantidades recibidas por concepto de compensación por "
            "desempleo bajo una ley de los Estados Unidos, de un estado de la Unión, o del Gobierno de Puerto "
            "Rico.\n"
            "(20) Compensación recibida por servicio militar activo prestado por personal militar en una “zona de "
            "combate”. Esta exclusión no aplica al personal militar movilizado fuera de Puerto Rico para relevar "
            "personal militar enviado a la zona de combate.\n"
            "(A) Personal alistado. — La exención aplica a la paga básica máxima recibida por el personal militar "
            "alistado, por servicio militar activo mientras esté en la zona de combate.\n"
            "(B) Oficiales comisionados. — En el caso de oficiales comisionados la exención dispuesta en el "
            "inciso (A) estará limitada a la paga básica máxima recibida por el personal militar alistado\n"
            "(C) Definiciones. — Para fines de este párrafo —.\n"
            "(i) el término “oficial comisionado” no incluye a los oficiales técnicos administrativos (“warrant "
            "officers”);\n"
            "(ii) el término “personal militar” incluye tanto a los miembros de las Fuerzas Armadas de los "
            "Estados Unidos como a los miembros de la Guardia Nacional de Puerto Rico que sean activados durante "
            "el período de conflicto y sirvan en la zona de combate;\n"
            "(iii) el término “zona de combate” significa el área designada mediante Orden Ejecutiva del "
            "Presidente de los Estados Unidos como el área en que combatirán las Fuerzas Armadas de los Estados "
            "Unidos durante el período de conflicto.\n"
            "(21) Ingreso recibido o devengado en relación con la celebración de juegos deportivos, organizados "
            "por asociaciones o federaciones internacionales. — Ingresos de cualquier naturaleza recibidos o "
            "devengados por equipos de miembros de asociaciones o federaciones internacionales o asociaciones o "
            "entidades afiliadas a dichos equipos y el ingreso de cualquier naturaleza devengado por entidades "
            "contratadas por dichos equipos o por dichas asociaciones o entidades afiliadas a éstos para "
            "organizar y encargarse de la operación, promoción o administración de dichos juegos en Puerto Rico. "
            "Esta disposición aplica a las siguientes asociaciones o federaciones internacionales:\n"
            "(A) Equipos de béisbol de Grandes Ligas, mejor conocida por su término en inglés “Major League "
            "Baseball”, incluyendo, pero sin que se entienda una limitación, la Oficina del Comisionado de "
            "Béisbol de Grandes Ligas, “Major League Baseball Properties, Inc.”, “Major League Baseball "
            "Enterprises, Inc.”, “Baseball Television, Inc.”, “Major League Baseball Advanced Media, L.P.” y "
            "entidades sucesoras de éstas, en relación con la celebración de juegos de béisbol de Grandes Ligas "
            "en Puerto Rico.\n"
            "(B) Asociación Nacional de Baloncesto de los Estados Unidos, mejor conocida como por sus siglas en "
            "inglés como “NBA”, incluyendo, pero sin que se entienda una limitación a la Oficina del Comisionado "
            "de la Asociación Nacional de Baloncesto, “NBA TV”, “NBA Latin America, Inc.”, la Asociación de "
            "Nacional de Baloncesto de Mujeres mejor conocida como “WNBA” o la Liga Nacional de Desarrollo del "
            "Baloncesto mejor conocida como “NBDL” y entidades sucesoras de éstas, en relación con la celebración "
            "de juegos de baloncesto de la “NBA” en Puerto Rico.\n"
            "(22) Ingreso devengado por participantes en la Serie del Caribe. — Los ingresos devengados por los "
            "jugadores, dirigentes, personal técnico y equipos extranjeros, no residentes, que participen en las "
            "Series del Caribe del Béisbol Profesional que se celebren en Puerto Rico.\n"
            "(23) El ingreso derivado por el Asegurador Internacional o por una Compañía Tenedora del Asegurador "
            "Internacional, sujeto a las disposiciones del Artículo 61.240 del Código de Seguros de Puerto Rico, "
            "incluyendo el ingreso derivado de la liquidación o disolución de las operaciones en Puerto Rico.\n"
            "(24) Subsidio Federal para Planes de Medicamentos Recetados. — Los pagos por concepto del subsidio "
            "recibido bajo las disposiciones de la Sección 1860D-22 de la Ley del Seguro Social, según enmendada "
            ", o sea posteriormente enmendada. Esta exención de ingreso bruto no afectará la determinación de "
            "cualquier deducción admisible bajo la Sección 1033.01 de este Subtítulo. Por consiguiente, un "
            "contribuyente podrá reclamar una deducción bajo la Sección 1033.01 de este Subtítulo, aun cuando "
            "dicho contribuyente también reciba un subsidio exento relacionado con la deducción admisible bajo la "
            "Sección 1033.01 de este Subtítulo.\n"
            "(25) Las cantidades recibidas por un empleado, de conformidad a la Ley Núm. 84 de 1 de marzo de "
            "1999, conocida como “Ley para la Creación de los Centros de Cuidado Diurno para Niños en el "
            "Gobierno”, siempre que los gastos estén relacionados con un dependiente, sobre el cual el empleado "
            "tiene derecho a reclamar una exención bajo la Sección 1033.18(b). No se admitirá deducción bajo "
            "ninguna otra disposición de este Subtítulo, por cualquier cantidad exenta de contribución por razón "
            "de este párrafo.\n"
            "(26) Para años contributivos comenzados antes del 1 de enero de 2020, la compensación recibida por "
            "un investigador o científico elegible por servicios prestados a la Universidad de Puerto Rico y "
            "todas aquellas otras instituciones de educación superior acreditadas en Puerto Rico, por concepto de "
            "investigaciones científicas hasta una cantidad igual al máximo establecido por los Institutos "
            "Nacionales de Salud para concesiones (“grants”) como salario para investigadores que reciben "
            "concesiones de cualquiera de las organizaciones que componen los Institutos Nacionales de Salud para "
            "el período aplicable conforme los avisos publicados por los Institutos; disponiéndose que para el "
            "año natural que comienza el 1ro de enero de 2008, la cantidad a excluirse será de ciento noventa y "
            "cinco mil (195,000) dólares. Se excluye de este beneficio cualquier ingreso que un investigador o "
            "científico pueda devengar por servicios prestados a otras personas, naturales o jurídicas, que no "
            "sean la Universidad de Puerto Rico u otra institución de educación superior.\n"
            "(A) Institución de educación superior. — Significa una institución educativa, pública o privada, "
            "debidamente acreditada por el Consejo de Educación de Puerto Rico, conforme la Ley Núm. 17 de 16 de "
            "junio de 1993, según enmendada, o por la Middle States Commission on Higher Education de la Middle "
            "States Association of Colleges and Schools.\n"
            "(B) Investigador o científico elegible. — Significa un individuo residente de Puerto Rico durante el "
            "año contributivo, contratado por la Universidad de Puerto Rico u otra institución de educación "
            "superior en Puerto Rico, que se dedique principalmente a llevar a cabo investigaciones científicas "
            "elegibles y que haya sometido una propuesta de investigación científica a los Institutos Nacionales "
            "de Salud o a otra organización del Gobierno Federal de los Estados Unidos o del Gobierno de Puerto "
            "Rico, y que, por la aprobación de dicha propuesta, la institución académica reciba una concesión "
            "(“grant”) para investigación bajo el Proyecto de Investigación R01 o su equivalente, cuya cuantía "
            "cubra los costos de investigación, incluyendo la compensación de dicho investigador y del personal "
            "clave, compra de equipos y suministros, publicaciones y otros gastos relacionados; disponiéndose, "
            "que salvo en el caso de investigadores principales múltiples “Multiple Principal Investigators "
            "(PI’s)”, no habrá más de un individuo elegible para esta deducción por concesión (“grant”) aprobada.\n"
            "(C) Investigaciones científicas elegibles. — Significa cualquier investigación que se lleve a cabo "
            "por la Universidad de Puerto Rico u otra institución de educación superior que reciba una concesión "
            "(“grant”) bajo el Proyecto de Investigación R01 u otro proyecto similar de cualquiera de las "
            "organizaciones que componen los Institutos Nacionales de Salud o bajo programas o mecanismos "
            "similares auspiciados por cualquier otra organización que promueva la investigación científica "
            "competitiva, incluyendo pero sin limitarse a, la Fundación Nacional de Ciencia (“National Science "
            "Foundation”).\n"
            "(27) Para años contributivos comenzados antes del 1 de enero de 2020, la compensación recibida por "
            "un investigador o científico elegible por servicios prestados por concepto de actividades de "
            "investigación y desarrollo de ciencia y tecnología llevadas a cabo dentro del Distrito establecido "
            "en el Artículo 7 de la Ley Núm. 214 de 18 de agosto de 2004, según enmendada, hasta la cantidad de "
            "doscientos cincuenta mil (250,000) dólares. Para ropósitos de este párrafo, el término “investigador "
            "o científico elegible” significa un individuo residente de Puerto Rico durante el año contributivo, "
            "contratado por una institución ubicada en el Distrito establecido en el Artículo 7 de la Ley Núm. "
            "214-2004, según enmendada, que se dedique principalmente a llevar a cabo actividades de "
            "investigación y desarrollo de ciencia y tecnología. La recomendación inicial de si una persona es un "
            "“investigador o científico elegible” para propósitos de este párrafo y el número de investigadores o "
            "científicos elegibles que podrán disfrutar de la exención concedida en este párrafo se hará por el "
            "consejo de fiduciarios, según se define dicho término en dicha Ley Núm. 214-2004. Dicha "
            "recomendación inicial será sometida para aprobación final ante el Secretario y el Secretario del "
            "Departamento de Desarrollo Económico y Comercio. El Secretario podrá delegar en el Secretario del "
            "Departamento de Desarrollo Económico y Comercio la determinación final si así el Secretario lo "
            "dispone mediante Carta Circular, determinación administrativa o cualquier otro documento "
            "informativo. Si el Secretario no se expresa dentro de los veinte (20) días de sometida para su "
            "aprobación la recomendación del Consejo de Fiduciarios, el Secretario del Departamento de Desarrollo "
            "Económico y Comercio tomará la decisión final sobre si acoger las recomendaciones del Consejo de "
            "Fiduciarios.\n"
            "(28) Rentas de la Zona Histórica. — Para años contributivos comenzados antes del 1 de enero de 2020, "
            "sujeto a los requisitos de la Ley Número 7 del 4 de marzo de 1955, según enmendada, y de cualquier "
            "otra ley que la sustituya o complemente, hasta el límite dispuesto en dichas leyes, las rentas "
            "percibidas como producto del alquiler de edificios existentes en la Zona Histórica de la ciudad de "
            "San Juan Bautista de Puerto Rico o en cualquier zona histórica establecida en Puerto Rico por el "
            "Instituto de Cultura Puertorriqueña, o la Junta de Planificación que hayan sido mejorados, "
            "restaurados, reestructurados, o reconstruidos sustancialmente o de nueva edificación de acuerdo con "
            "las normas establecidas por el Instituto de Cultura Puertorriqueña para armonizar con las "
            "características de la zona histórica donde enclaven, y habiendo obtenido los correspondientes "
            "permisos de las agencias pertinentes y un certificado del Instituto de Cultura Puertorriqueña "
            "haciendo constar su conformidad con la obra tal y como haya sido terminada.\n"
            "(29) Dietas y gastos de viajes de legisladores. — Las cantidades recibidas por los miembros de la "
            "Asamblea Legislativa del Gobierno de Puerto Rico por concepto de dietas y gastos de viaje, las "
            "cuales representan reembolso de gastos que realmente se incurren.\n"
            "(30) Dietas y gastos de viajes de legisladoras y Legisladores Municipales. — Las cantidades "
            "recibidas por los miembros de las Legislaturas Municipales por concepto de dietas y gastos de viaje, "
            "las cuales representan reembolso de gastos que realmente se incurren.\n"
            "(31) Ingreso de las Entidades Bancarias Internacionales. — El ingreso proveniente de cualquiera de "
            "las actividades descritas en el apartado (a) de la Sección 12 de la Ley Número 52, de 11 de agosto "
            "de 1989, según enmendada, “Ley Reguladora del Centro Bancario Internacional”, llevadas a cabo por "
            "cualquier Entidad Bancaria Internacional debidamente autorizada para llevar a cabo tales actividades "
            "bajo las disposiciones de dicha ley, incluyendo el ingreso derivado de la liquidación o disolución "
            "de las operaciones en Puerto Rico.\n"
            "(32) Compensación a ciudadanos y extranjeros no residentes en Puerto Rico para producir proyectos "
            "fílmicos. — Los sueldos, honorarios o compensaciones pagados por personas naturales o entidades "
            "corporativas procedentes del exterior a ciudadanos y extranjeros no residentes en Puerto Rico, por "
            "concepto de los servicios técnicos brindados por éstos durante la realización de producciones "
            "cinematográficas, con fines de distribución a casas de cine o televisiva.\n"
            "(33) Cantidades pagadas por un patrono a un empleado por concepto de reembolso de gastos de viaje, "
            "comidas, hospedaje, entretenimiento y otros gastos relacionados con el empleo. — Cantidades pagadas "
            "por un patrono por concepto de reembolso de gastos, pagados o incurridos por el contribuyente en "
            "relación con la prestación por él de servicios como empleado, que consistan de gastos de viaje, "
            "comidas, hospedaje, mientras esté ausente de la residencia, entretenimiento (excepto aquellas sumas "
            "consideradas suntuosas o extravagantes ante las circunstancias), y otros gastos relacionados con el "
            "empleo, siempre y cuando dicho reembolso se haga bajo un plan de reembolso de gastos establecido por "
            "el patrono que cumpla con los requisitos que el Secretario establezca mediante reglamento a esos "
            "efectos.\n"
            "(34) El ingreso devengado por concepto de las horas extras trabajadas por un miembro del Negociado "
            "de la Policía de Puerto Rico, según este servidor público es definido en el Artículo 1.02 de la Ley "
            "20-2017, según enmendada, conocida como “Ley del Departamento de Seguridad Pública de Puerto Rico”. "
            "Asimismo, a partir del 1 de enero de 2019 estarán exentos de toda tributación, los salarios que se "
            "les paguen retroactivamente a los miembros del referido Negociado, por concepto de los aumentos en "
            "los tipos básicos de las escalas y por los aumentos de sueldos otorgados en virtud de la Ley "
            "227-2004, según enmendada, y cualesquiera otros ingresos que estos generen por promociones pasadas "
            "de acuerdo a las escalas salariales, y que aún se les adeuden. Para los salarios pagados por los "
            "conceptos antes mencionados durante el año 2018, que no hayan sido incluidos en el Comprobante de "
            "Retención de dicho año, se concederá un crédito, equivalente al cien por ciento (100%) de dicho "
            "ingreso, en el año 2019. A partir del 1 de julio de 2022 se exime de tributación, cualquier "
            "desembolso, aportación y/o contribución que se haga a los miembros activos y retirados del Negociado "
            "de la Policía de Puerto Rico, por concepto de las bonificaciones otorgadas por virtud del fondo de "
            "Retiro de la Policía de la Ley 106-2017 según la Aportación Definida para el retiro mejorado de los "
            "miembros de la Policía. Esta Ley aplicará retroactivamente en caso de que se haya realizado algún "
            "desembolso o contribución previo a su aprobación por concepto de la Ley 106-2017. Estas exclusiones "
            "no les aplican a los empleados civiles del antes mencionado Negociado.\n"
            "(35) El ingreso devengado a partir del 1 de julio de 2022 por concepto de las horas extras "
            "trabajadas por un miembro de la Policía Municipal, según este funcionario es definido en el Artículo "
            "3.026 de la Ley 107-2020, conocida como “Código Municipal de Puerto Rico”. Esta exclusión no aplica "
            "a empleados civiles adscritos a la Policía Municipal.\n"
            "(36) Partidas misceláneas. —\n"
            "(A) Ingreso de fuentes fuera de Puerto Rico. —\n"
            "(i) No residente en Puerto Rico durante todo el año contributivo. — En el caso de un individuo que "
            "sea ciudadano de los Estados Unidos y que establezca a satisfacción del Secretario que no ha sido un "
            "residente de Puerto Rico durante todo el año contributivo, las cantidades recibidas de fuentes fuera "
            "de Puerto Rico, pero no se admitirá a dicho individuo como deducción de su ingreso bruto deducción "
            "alguna que sea propiamente asignable o imputable a cantidades excluidas del ingreso bruto bajo este "
            "párrafo.\n"
            "(ii) Año contributivo en que se cambia la residencia a Puerto Rico. — En el caso de un individuo que "
            "no ha sido un residente de Puerto Rico y que cambie su residencia a Puerto Rico, las cantidades "
            "recibidas de fuentes fuera de Puerto Rico atribuibles al período de residencia fuera de Puerto Rico, "
            "pero no se admitirá a dicho individuo como deducción de su ingreso bruto deducción alguna que sea "
            "propiamente asignable o imputable a cantidades excluidas del ingreso bruto bajo este párrafo.\n"
            "(B) Ingreso del Gobierno de los Estados Unidos, de Gobiernos Extranjeros y de Organizaciones "
            "Internacionales. — El ingreso del Gobierno de los Estados Unidos, de gobiernos extranjeros o de "
            "organizaciones internacionales, recibido de inversiones en Puerto Rico en acciones, bonos u otros "
            "valores domésticos poseídos por dichos gobiernos u organizaciones internacionales, o de intereses "
            "sobre depósitos en bancos en Puerto Rico de dineros pertenecientes a dichos gobiernos u "
            "organizaciones internacionales, o de cualquier otra fuente dentro de Puerto Rico;\n"
            "(C) el ingreso de los estados, municipios y otras subdivisiones políticas. —Ingreso derivado de "
            "cualquier empresa de servicio público o del ejercicio de cualquier función gubernamental esencial, "
            "que se acumule a cualquier estado, territorio, o al Distrito de Columbia, o a cualquier subdivisión "
            "política de un estado o territorio, o ingreso acumulado al gobierno de cualquier posesión de los "
            "Estados Unidos o a cualquier subdivisión política de la misma;\n"
            "(D) las entradas de asociaciones de dueños de embarcaciones y protección e indemnización mutua;\n"
            "(E) remuneración de empleados de gobiernos extranjeros o de organizaciones internacionales. —\n"
            "(i) Regla para exclusión. — Los jornales, honorarios o sueldo de cualquier empleado de un gobierno "
            "extranjero o de una organización internacional, incluyendo un funcionario consular u otro "
            "funcionario o un representante no diplomático, recibidos como compensación por servicios oficiales a "
            "dicho gobierno u organización internacional —\n"
            "(I) Si dicho empleado no es un ciudadano de los Estados Unidos;\n"
            "(II) Si, en el caso de un empleado de un gobierno extranjero, los servicios son de carácter análogo "
            "a aquellos prestados por empleados del Gobierno de los Estados Unidos en países extranjeros; y\n"
            "(III) Si, en el caso de un empleado de un gobierno extranjero, el gobierno extranjero concede una "
            "exención equivalente a empleados del Gobierno de los Estados Unidos que realicen servicios análogos "
            "en dicho país extranjero.\n"
            "(F) Ingresos provenientes de edificios arrendados o rentados al Gobierno de Puerto Rico para "
            "hospitales públicos, casas de salud o de convalecencia y facilidades complementarias a dichos "
            "hospitales y casas de salud o de convalecencia, tales como vivienda para enfermeras, cafetería, "
            "servicios de lavandería, centros de rehabilitación física y vocacional; para escuelas públicas, y "
            "facilidades físicas complementarias a la educación, tales como bibliotecas, librerías, residencias "
            "de estudiantes y profesores y centros de servicios múltiples como los de cafetería, reunión y "
            "esparcimiento; o en la construcción de edificios para ser arrendados a entidades de fines no "
            "pecuniarios que los utilicen como hospitales, casas de salud o de convalecencia y facilidades "
            "físicas complementarias. Esta exención estará disponible única y exclusivamente para aquellos "
            "edificios que tenían un contrato de arrendamiento en vigor al 22 de noviembre de 2010.\n"
            "(G) Ingreso derivado por el contribuyente de la reventa de propiedad mueble o servicios cuya "
            "adquisición por dicho contribuyente estuvo sujeta a tributación bajo la Sección 3070.01 de este "
            "Código o la Sección 2101 de la Ley 120 del 31 de octubre de 1994, según enmendada, conocida como el "
            "Código de Rentas Internas de 1994.\n"
            "(37) Ingreso derivado por jóvenes por concepto de salarios, servicios prestados y/o trabajo por "
            "cuenta propia. — Para años contributivos comenzados luego del 31 de diciembre de 2013, los primeros "
            "cuarenta mil dólares ($40,000) de ingreso bruto generados por un joven por concepto de salarios, "
            "servicios prestados y/o trabajo por cuenta propia, serán exentos de tributación bajo este Subtítulo. "
            "El exceso de cuarenta mil dólares ($40,000) tributará a tasas ordinarias. En este caso el "
            "contribuyente no tendrá derecho a reclamar la Deducción Especial para Ciertos Individuos dispuesta "
            "en la Sección 1033.16. Para propósitos de este inciso, el término joven significa aquel individuo "
            "residente de Puerto Rico, cuya edad fluctúa entre los dieciséis (16) y los veintiséis (26) años al "
            "finalizar el año contributivo.\n"
            "(38) Salarios pagados retroactivamente a los empleados civiles del Negociado de la Policía de Puerto "
            "Rico. — A partir del 1 de enero de 2023 estarán exentos de toda tributación los salarios que se les "
            "paguen retroactivamente a los empleados civiles del referido Negociado por concepto de los aumentos "
            "salariales otorgados en virtud de la Ley 164-2003 y cualesquiera otros ingresos que se generen por "
            "promociones posteriores de acuerdo a las escalas salariales, que aún se adeuden."
        ),
        effective_year=2011,
        source_url="https://hacienda.pr.gov/sites/default/files/codigo_de_rentas_internas_2011_segun_enmendado_28.08.2026.pdf",
        cross_references=["PR CRI Sec. 1031.01", "PR CRI Sec. 1081.01"],
        breadcrumbs="Ley 1-2011 > Subtítulo A > Capítulo 3 > Subcapítulo A > Sección 1031.02",
    ),
    StatuteRecord(
        id="pr-cri-1062-01",
        jurisdiction="PR",
        corpus="CRI",
        citation="PR CRI Sec. 1062.01",
        section="1062.01",
        title=(
            "Retención en el Origen de la Contribución en el Caso de Salarios / Withholding of Tax at Source on "
            "Wages"
        ),
        text_en=(
            "English summary; the official text is in Spanish. Requires every employer that pays wages to "
            "deduct and withhold income tax under the withholding tables approved by the Secretary; this "
            "reporting and withholding obligation does not apply to payments made after December 31, 2021 for "
            "services performed by a remote worker (Trabajador a Distancia, defined at Section 1010.01(a)(42); "
            "the Code's cross-reference reads (a)(43)) (b). Defines wages, "
            "payroll period, employee, employer and related terms (a), and covers the withholding exemption "
            "and allowances (c), exemption certificates (f), the return and payment of withheld tax (j)-(k), "
            "the employer's liability for the tax (l) and annual statements (n). Under (r), for occasional, "
            "temporary or seasonal wages paid daily at an hourly rate, the Secretary may authorize withholding "
            "at a flat 2 percent for payments made before January 1, 2019 and 5 percent for payments made "
            "after December 31, 2018."
        ),
        text_es=(
            "(a) Definiciones. — Según se utiliza en esta sección —\n"
            "(1) Salarios. —El término “salarios” significa toda remuneración por servicios prestados por un "
            "empleado para su patrono, y toda remuneración en concepto de pensión por servicios prestados, "
            "incluyendo el valor en dinero de toda remuneración pagada por cualquier medio que no sea dinero, "
            "excepto que dicho término no incluirá remuneración pagada—\n"
            "(A) para pagos realizados antes del 1 de enero de 2019, por servicios prestados en trabajo agrícola, "
            "según se define en el párrafo (6) de este apartado, excluyendo servicios prestados por empleados "
            "ejecutivos, administrativos, de oficina o de supervisión y por empleados que desempeñen puestos "
            "permanentes. Todo pago realizado a partir del 1 de enero de 2019, será considerado como salario no "
            "sujeto a retención en el origen y se requerirá que dicho pago esté informado en un comprobante de "
            "retención. O\n"
            "(B) para pagos realizados antes del 1 de enero de 2019, por servicios domésticos en el hogar, club "
            "colegial local, o capítulo local de una fraternidad o sororidad colegial, Todo pago realizado a "
            "partir del 1 de enero de 2019, será considerado como salario no sujeto a retención en el origen y se "
            "requerirá que dicho pago esté informado en un comprobante de retención. O\n"
            "(C) por servicios fuera del curso de la industria o negocio del patrono, prestados en cualquier "
            "trimestre natural por un empleado, a menos que la remuneración en dinero pagada por dichos servicios "
            "sea más de ochocientos setenta y cinco (875) y dichos servicios sean prestados por un individuo que "
            "es regularmente empleado por dicho patrono para prestar dichos servicios. Para los fines de este "
            "inciso, un individuo se considerará que es empleado regularmente por un patrono durante un trimestre "
            "natural sólo si (i) en cada uno de cualesquiera veinticuatro (24) días durante dicho trimestre dicho "
            "individuo presta a dicho patrono, por alguna parte del día, servicios fuera del curso de la "
            "industria o negocio del patrono, o (ii) dicho individuo fue empleado regularmente, según se "
            "determine bajo la cláusula (i), por dicho patrono en la prestación de servicios durante el "
            "precedente trimestre natural, o\n"
            "(D) por servicios prestados por un ciudadano o residente de Puerto Rico para un gobierno extranjero "
            "o para una organización internacional, o\n"
            "(E) por servicios prestados por un individuo no residente que no sea la remuneración en concepto de "
            "pensión por servicios prestados, o\n"
            "(F) para pagos realizados antes del 1 de enero de 2019, por servicios prestados por un ministro de "
            "una iglesia debidamente ordenado, comisionado o autorizado, en el ejercicio de su ministerio, o por "
            "un miembro de una orden religiosa en el cumplimiento de deberes requeridos por dicha orden. Todo "
            "pago realizado a partir del 1 de enero de 2019, será considerado como salario no sujeto a retención "
            "en el origen y se requerirá que dicho pago esté informado en un comprobante de retención.\n"
            "(G) para pagos realizados antes del 1 de enero de 2019, compensaciones o indemnizaciones recibidas "
            "por un empleado por razón de despido, sin que sea necesario determinar su justa causa, hasta una "
            "cantidad máxima equivalente a la indemnización que el empleado pudiese recibir al amparo de la Ley "
            "Núm. 80, de 30 mayo de 1976, o bajo un acuerdo de compensación por despido entre el patrono y el "
            "empleado. Todo pago realizado a partir del 1 de enero de 2019, será considerado como salario no "
            "sujeto a retención en el origen y se requerirá que dicho pago esté informado en un comprobante de "
            "retención.\n"
            "(H) por servicios prestados fuera de Puerto Rico por un individuo residente de Puerto Rico si, a la "
            "fecha de pago de la remuneración, se requiere del patrono por una ley de los Estados Unidos, "
            "cualquier posesión de los Estados Unidos o cualquier país extranjero, el retener la contribución en "
            "el origen sobre toda o cualquier parte de dicha remuneración, o\n"
            "(I) por concepto de pensiones sujetas a retención bajo otras disposiciones de este Subtítulo, así "
            "como aquella cantidad recibida de dichas pensiones que sea excluible del ingreso bruto bajo la "
            "Sección 1031.02(a)(13).\n"
            "(J) Todo pagador que únicamente tenga la responsabilidad de informar los salarios no sujetos a "
            "retencion que se incluyen en este apartado, no tendran la obligacion de radicar la planilla que se "
            "requiere en el apartado (j) de esta Sección.\n"
            "(2) Período de nómina. — El término “período de nómina” significa un período por el cual un pago de "
            "salarios fuere ordinariamente hecho al empleado por su patrono, y el término “período de nómina "
            "misceláneo” significa un período de nómina que no sea uno diario, semanal, por cada 2 semanas, "
            "quincenal, mensual, trimestral, semestral o anual.\n"
            "(3) Empleado. — El término “empleado” incluye un funcionario, empleado o funcionario electo del "
            "Gobierno de Puerto Rico o de cualquier subdivisión política del mismo, o de cualquier agencia o "
            "instrumentalidad de cualesquiera de los anteriores. El término “empleado” también incluye un oficial "
            "de una corporación.\n"
            "(4) Patrono. — El término “patrono” significa la persona para quien un individuo presta o ha "
            "prestado cualquier servicio de cualquier naturaleza como empleado de dicha persona, excepto que —\n"
            "(A) si la persona para quien el individuo presta o ha prestado los servicios no tuviere el control "
            "del pago de los salarios por dichos servicios, el término “patrono”, excepto para los fines del "
            "párrafo (1), significa la persona que tenga el control del pago de dichos salarios; y\n"
            "(B) en el caso de una persona que pague salarios en representación de un individuo no residente, "
            "sociedad extranjera o corporación extranjera, que no se dedique a industria o negocio en Puerto "
            "Rico, el término “patrono”, excepto para los fines del párrafo (1), significa dicha persona.\n"
            "(5) Persona soltera. — El término “persona soltera” significa una persona con respecto a la cual "
            "estuviere en vigor bajo el apartado (f) un certificado de exención para la retención en el que "
            "conste que dicha persona es un contribuyente individual según definido en la Sección 1010.03(a)(1).\n"
            "(6) Persona casada. — El término “persona casada” significa una persona con respecto a la cual "
            "estuviere en vigor bajo el apartado (f) un certificado de exención para la retención en el que "
            "conste que dicha persona es casada, según definido en la Sección 1010.03(a)(2).\n"
            "(7) Dependiente. — El término “dependiente” significa una persona incluida en un certificado de "
            "exención para la retención, en vigor bajo el apartado (f), como un dependiente del empleado, según "
            "dicho término “dependiente” se define en la Sección 1033.18(c)(1).\n"
            "(8) Trabajo agrícola. — El término “trabajo agrícola” incluye todo servicio prestado —\n"
            "(A) En una finca, en el empleo de cualquier persona, en relación con el cultivo de la tierra, o en "
            "relación con el cultivo o cosecho de cualquier producto agrícola u hortícola, incluyendo la crianza, "
            "apaciguamiento, cuido, adiestramiento y manejo de ganado, abejas, aves de corral y vida silvestre.\n"
            "(B) En el empleo del dueño o poseedor u otro explotador de una finca, en relación con la "
            "explotación, administración, conservación, mejoramiento o mantenimiento de tal finca y sus aperos y "
            "equipo, o en el salvamento de bosques maderables o en la limpieza del terreno de broza y otros "
            "despojos dejados por un huracán, si la mayor parte de tal servicio se presta en una finca.\n"
            "(C) En relación con la producción o cosecho de cualquier producto definido como un producto agrícola "
            "en la Sección 15(g) de la Ley del Congreso conocida como Agricultural Marketing Act, según ha sido "
            "enmendada, o en relación con la explotación o mantenimiento de zanjas, canales, depósitos o vías de "
            "aguas, no poseídas o explotadas con fines de lucro que se usen exclusivamente para suministrar y "
            "conservar agua para fines de labranza.\n"
            "(D) (i) En el empleo del explotador de una finca en el manejo, siembra, secamiento, empaque, envase, "
            "elaboración, refrigeración, clasificación, depósito, o en la entrega para almacenado o para el "
            "mercado o a un porteador para la transportación al mercado en estado no elaborado, de cualquier "
            "producto agrícola u hortícola; pero sólo si dicho explotador produjo más de la mitad del producto "
            "con respecto al cual se presta tal servicio.\n"
            "(ii) En el empleo de un grupo de explotadores de fincas (que no sea una organización cooperativa) en "
            "la prestación de un servicio descrito en la cláusula (i), pero sólo si tales explotadores produjeron "
            "todo el producto con respecto al cual se presta tal servicio. Para los fines de esta cláusula, "
            "cualquier grupo no incorporado de explotadores se considerará una organización cooperativa si el "
            "número de explotadores comprendidos en dicho grupo es de más de veinte (20) en cualquier momento "
            "durante el trimestre natural en que se presta tal servicio.\n"
            "(iii) Las disposiciones de las cláusulas (i) y (ii) no se entenderán aplicables con respecto a "
            "servicio prestado en relación con el enlatado en escala comercial o con la refrigeración comercial o "
            "en relación con cualquier producto agrícola u hortícola después de su entrega en un mercado terminal "
            "para su distribución para consumo.\n"
            "(E) En una finca explotada con fines de lucro si dicho servicio no es en el curso de la industria o "
            "negocio del patrono o es servicio doméstico en una residencia del patrono. Según se utiliza en este "
            "párrafo, el término “finca” incluye fincas de ganado, de lechería, de aves de corral, de frutas y de "
            "hortalizas, plantaciones, ranchos, criaderos, fincas de pastos, invernáculos u otras estructuras "
            "similares usadas primordialmente para el cultivo de productos agrícolas u hortícolas, y vergeles.\n"
            "(b) Obligación de Retener. — Todo patrono que haga pagos de salarios deducirá y retendrá sobre el "
            "monto de los salarios una contribución determinada de acuerdo a las tablas de retención que, en "
            "armonía con los tipos contributivos fijados en este Subtítulo, aprobará el Secretario, las cuales "
            "formarán parte del reglamento bajo este Subtítulo. Para los fines de aplicar dichas tablas, el "
            "término “el monto de los salarios” significa la cantidad por la cual los salarios exceden la "
            "exención para la retención admisible bajo el apartado (c)(1). Disponiéndose que, la obligación de "
            "reportar y retener establecida en este apartado (b) no aplicará para pagos realizados después del 31 "
            "de diciembre de 2021, por servicios realizados por un trabajador a distancia, según definido en la "
            "Sección 1010.01(a)(43) de este Código.\n"
            "(c) Exención Para la Retención. —\n"
            "(1) Al computarse la contribución que debe ser deducida y retenida conforme a las tablas promulgadas "
            "por el Secretario, según lo dispuesto en el apartado (b), se admitirá como exención para la "
            "retención con respecto a los salarios pagados por cada período de nómina una exención determinada "
            "conforme a las tablas de exención para la retención que en armonía con las disposiciones de este "
            "Subtítulo aprobará el Secretario, las cuales formarán parte del reglamento de este Subtítulo. Dichas "
            "tablas tomarán en consideración el monto de la exención personal y exención por dependientes "
            "admisibles al contribuyente, de acuerdo con la Sección 1033.18, así como la concesión para la "
            "retención basada en las deducciones dispuestas en el párrafo (2) de este apartado.\n"
            "(2) Concesión para la retención basada en deducciones. —\n"
            "(A) Al determinar el monto de la exención para la retención bajo el párrafo (1), se admitirán "
            "concesiones basadas en deducciones en un número igual al resultante al dividir:\n"
            "(i) a opción del empleado, el monto de las deducciones que él estime ha de tener derecho a deducir "
            "bajo la Sección 1033.15 al computar su ingreso neto para el año contributivo correspondiente, entre "
            "quinientos (500) dólares. Para los fines de este párrafo el sistema establecido bajo la Ley de "
            "Seguro Social (“Social Security Act”) no se considerará como un sistema gubernamental de pensiones o "
            "retiro y una fracción no se considerará a menos que la misma exceda de cincuenta (50) por ciento, en "
            "cuyo caso esta fracción será tratada como una concesión adicional.\n"
            "(B) En el caso de esposo y esposa que vivan juntos, que al determinar la concesión para la retención "
            "ejerzan la opción provista en la cláusula (ii) del inciso (A), el número de concesiones a que tienen "
            "derecho bajo este párrafo será determinado tomando como base sus salarios y deducciones combinados. "
            "Ellos podrán dividirse las concesiones totales según lo deseen pero a base de concesiones completas. "
            "Sin embargo, cualquier concesión reclamada por uno de los cónyuges en un certificado de exención "
            "para la retención no podrá ser reclamada por el otro. Disponiéndose que, para salarios pagados "
            "después del 31 de diciembre de 2018, el Secretario establecerá mediante reglamento la forma de "
            "determinar las concesiones por deducciones y la forma de determinar el monto del salario sujeto a "
            "retención y la tasa de retención aplicable.\n"
            "(3) Si se pagaren salarios con respecto a un período que no sea un período de nómina, la exención "
            "para la retención admisible con respecto a cada pago de dichos salarios será la exención admitida "
            "para un período de nómina misceláneo que contenga un número de días, incluyendo domingo y días "
            "feriados, igual al número de días en el período con respecto al cual se pagaren dichos salarios.\n"
            "(4) En cualquier caso en que se pagaren salarios por un patrono sin consideración a período alguno "
            "de nómina u otro período, la exención para la retención admisible con respecto a cada pago de dichos "
            "salarios será la exención admitida para un período de nómina misceláneo que contenga un número de "
            "días igual al número de días, incluyendo domingos y días feriados, que hayan transcurrido desde la "
            "fecha del último pago de dichos salarios por dicho patrono durante el año natural, o desde la fecha "
            "del comienzo del empleo con dicho patrono durante dicho año, o desde el 1 de enero de dicho año, "
            "cualquiera de estas fechas que sea la posterior.\n"
            "(5) En cualquier caso en que el período o el término descrito en el párrafo (4) con respecto a "
            "cualquier salario fuere menor de una semana, el Secretario, bajo reglamentos prescritos por él, "
            "podrá autorizar a un patrono, al computar la contribución que debe ser deducida y retenida, a usar "
            "el exceso de la totalidad de los salarios pagados al empleado durante la semana natural sobre la "
            "exención para la retención admitida por este apartado para un período de nómina semanal.\n"
            "(6) Al determinarse el monto a ser deducido y retenido bajo este apartado, los salarios podrán, a "
            "opción del patrono, ser computados al dólar más cercano.\n"
            "(d) Retención Alternativa. — El Secretario está autorizado para por reglamentos disponer, bajo "
            "aquellas condiciones y hasta el límite que lo crea conveniente, un método de retención en "
            "sustitución de aquel de otro modo requerido bajo esta sección, cuando a su discreción estime "
            "necesario o conveniente disponer tal método de retención alternativa. Dicha retención alternativa "
            "será considerada a todos los fines contribución requerida a ser deducida y retenida bajo esta "
            "sección.\n"
            "(e) Retención Adicional. — El Secretario está autorizado para por reglamentos proveer, bajo aquellas "
            "condiciones y hasta el límite que lo crea conveniente, retención en adición a aquella de otro modo "
            "requerida bajo esta sección, en casos en que el patrono y el empleado acordaren (en aquella forma "
            "que mediante reglamentos prescriba el Secretario) tal retención. Dicha retención adicional será "
            "considerada a todos los fines contribución requerida a ser deducida y retenida bajo esta sección.\n"
            "(f) Certificados de Exención para la Retención. — Todo empleado que reciba salarios suministrará a "
            "su patrono un certificado de exención para la retención bajo su firma a fin de que se compute su "
            "exención para la retención. En caso de un cambio que afecte la exención para la retención, un nuevo "
            "certificado será suministrado no más tarde de diez (10) días después de ocurrir dicho cambio. El "
            "certificado será en aquella forma y contendrá aquella información que el Secretario por reglamentos "
            "prescriba.\n"
            "(1) Dicho certificado —\n"
            "(A) Si fuere suministrado después de la fecha del comienzo del empleo con el patrono debido a un "
            "cambio de estado personal, será efectivo con respecto al primer pago de salarios que se haga en o "
            "después de la primera fecha de determinación de estado personal que acaezca por lo menos treinta "
            "(30) días a partir de la fecha en que dicho certificado sea suministrado al patrono, excepto que a "
            "opción del patrono dicho certificado podrá hacerse efectivo con respecto a cualquier pago anterior "
            "de salarios hecho en o después de la fecha en que se suministre dicho certificado. Para los fines de "
            "este párrafo, el término “fecha de determinación de estado personal” significa el 1 de enero y el 1 "
            "de julio de cada año.\n"
            "(B) Si fuere suministrado por otra razón que no sea un cambio de estado personal, será efectivo a "
            "partir del comienzo del primer período de nómina que termine, o del primer pago de salarios que se "
            "haga sin consideración a un período de nómina, en o después de la fecha en la cual dicho certificado "
            "sea suministrado al patrono, excepto que si la razón para radicar el nuevo certificado es una que ha "
            "de surtir efecto a partir del año natural siguiente, dicho certificado no se tomará en consideración "
            "con respecto a pago alguno que se efectúe en el año natural en que se rinda.\n"
            "(2) Un certificado que entre en vigor bajo este apartado continuará en vigor con respecto al patrono "
            "hasta que otro de tales certificados suministrado por el empleado entre en vigor bajo este apartado. "
            "Si no estuviere en vigor certificado alguno bajo este apartado con respecto a un empleado, dicho "
            "empleado será tratado, a los fines de la exención para la retención, como persona casada que no "
            "reclama exención personal alguna para la retención y que no tiene dependientes ni concesión alguna "
            "basada en deducciones.\n"
            "(3) Todo patrono que reciba de cualquier empleado un certificado de exención en el cual el número de "
            "dependientes reclamados exceda de ocho (8), deberá someter al Secretario, bajo reglamentos "
            "prescritos por él, una copia de dicho certificado, así como una copia de cualquier declaración "
            "escrita recibida del empleado para sustentar la información contenida en el certificado.\n"
            "(g) Salarios Incluidos y Excluidos. — Si la remuneración pagada por un patrono a un empleado por "
            "servicios prestados durante una mitad o más de cualquier período de nómina de no más de treinta y un "
            "(31) días consecutivos constituyere salarios, toda la remuneración pagada por dicho patrono a dicho "
            "empleado para dicho período será considerada como salarios; pero si la remuneración pagada por un "
            "patrono a un empleado por servicios prestados durante más de la mitad de cualquier período tal de "
            "nómina no constituyere salarios, entonces ninguna parte de la remuneración pagada por dicho patrono "
            "a dicho empleado para dicho período será considerada como salarios.\n"
            "(h) Períodos de Pagos que se Extienden a Otros. — Si un pago de salarios fuere hecho a un empleado "
            "por un patrono —\n"
            "(1) con respecto a un período de nómina u otro período, cualquier parte del cual estuviere incluido "
            "en un período de nómina u otro período con respecto al cual también se pagaren salarios a dicho "
            "empleado por dicho patrono, o\n"
            "(2) sin consideración a período alguno de nómina u otro período, pero en o antes del vencimiento de "
            "un período de nómina u otro período con respecto al cual también se pagaren salarios a dicho "
            "empleado por dicho patrono, o\n"
            "(3) con respecto a un período que comenzare en un año natural y terminare en otro, o\n"
            "(4) por conducto de un agente, fiduciario u otra persona que también pagare o tuviere el control, "
            "recibo, custodia o la disposición de los salarios pagaderos por otro patrono a dicho empleado; la "
            "forma de retención y el monto a ser deducido y retenido bajo esta Sección, serán determinados de "
            "acuerdo con reglamentos prescritos por el Secretario, bajo los cuales la exención para la retención "
            "admitida al empleado en cualquier año natural se aproximará a la exención para la retención "
            "admisible con respecto a un período de nómina anual.\n"
            "(i) Retención a Base de Salarios Promedios. — El Secretario podrá, bajo reglamentos prescritos por "
            "él, autorizar a los patronos (1) a hacer un estimado de los salarios que serán pagados a cualquier "
            "empleado en cualquier trimestre del año natural, (2) a determinar el monto a ser deducido y retenido "
            "sobre cada pago de salarios a dicho empleado durante dicho trimestre como si el promedio apropiado "
            "de los salarios así estimados constituyere los salarios realmente pagados, y (3) a deducir y retener "
            "sobre cualquier pago de salarios a dicho empleado durante dicho trimestre, aquella cantidad que "
            "pueda ser necesaria para ajustar el monto realmente deducido y retenido sobre los salarios de dicho "
            "empleado durante dicho trimestre a la cantidad que deba ser deducida y retenida durante dicho "
            "trimestre sin considerar este apartado. Disponiéndose que para pagos efectuados después del 31 de "
            "diciembre de 2018, el Secretario podrá requerir al patrono hacer un estimado de los salarios que "
            "serán pagados durante el año natural, deducir aquella exención o deducción a la que el empleado "
            "tenga derecho y determinar la cantidad de contribución sobre ingresos a retener para cada pago de "
            "nómina basado en el cómputo de la contribución que resulta a base del estimado de salario anual.\n"
            "(j) Planilla y Pago. — Toda persona obligada a deducir y retener cualquier contribución bajo esta "
            "sección deberá, en o antes del último día del mes siguiente al cierre de cada uno de los trimestres "
            "terminados el treinta y uno (31) de marzo, treinta (30) de junio, treinta (30) de septiembre y "
            "treinta y uno (31) de diciembre de cada año, en lugar de en la fecha prescrita en las Secciones "
            "1061.15 y 1061.16, rendir una planilla por la misma y pagar aquella parte de la misma que no haya "
            "sido pagada o depositada conforme se establece en la forma y manera dispuestas en el Subtítulo F. "
            "Dicha planilla será rendida al Secretario y contendrá aquella información, y será hecha en aquella "
            "forma que el Secretario establezca mediante reglamento al efecto.\n"
            "(k) Planilla y Pago por Patrono Gubernamental. — Si el patrono es el Gobierno de Puerto Rico, "
            "cualquier subdivisión política del mismo, o cualquier agencia o instrumentalidad de los mismos, la "
            "planilla por el monto deducido y retenido sobre cualesquier salario podrá ser rendida por cualquier "
            "funcionario o empleado del Gobierno de Puerto Rico, de dicha subdivisión política o de dicha agencia "
            "o instrumentalidad, según sea el caso, que tenga el control del pago de dichos salarios o que esté "
            "debidamente designado para tal fin.\n"
            "(l) Responsabilidad por la contribución. — El patrono será responsable al Secretario del pago de la "
            "contribución que deberá ser deducida y retenida bajo esta Sección, y no responderá a persona otra "
            "alguna por el monto de cualesquiera de estos pagos. Todo patrono que al momento de rendir su "
            "planilla de contribución sobre ingresos no haya remitido al Departamento de Hacienda la totalidad "
            "del monto deducido y retenido sobre los salarios pagados a sus empleados correspondientes al año "
            "contributivo por el cual está rindiendo y no haya informado dichos salarios pagados y contribución "
            "retenida en los comprobantes de retención según lo dispuesto en el apartado (n) de esta Sección, no "
            "podrá reclamar los salarios pagados como gastos de operación.\n"
            "(m)Contribución Pagada por el Receptor. — Si el patrono, en violación de las disposiciones de este "
            "Subtítulo, dejare de deducir y retener la contribución bajo esta sección, y la contribución contra "
            "la cual dicha contribución deba ser acreditada fuere posteriormente pagada, la contribución que así "
            "debió ser deducida y retenida no será cobrada al patrono, pero este apartado no relevará en caso "
            "alguno al patrono de responsabilidad por cualesquiera penalidades o adiciones a la contribución de "
            "otro modo aplicables con respecto a tal omisión de deducir y retener.\n"
            "(n) Declaraciones. —\n"
            "(1) Estado de reconciliación anual. — Todo patrono obligado a deducir y retener cualquier "
            "contribución con respecto a los salarios de sus empleados y a rendir una planilla mensual por dicha "
            "contribución así deducida y retenida, deberá someter, en o antes del treinta y uno (31) de enero del "
            "año siguiente, en adición a cualesquiera otros documentos, un estado de reconciliación anual en el "
            "que conste el monto de la contribución deducida y retenida mensualmente bajo esta sección con "
            "respecto a dichos salarios.\n"
            "(2) Comprobante de retención. — Todo patrono obligado a deducir y retener una contribución con "
            "respecto a los salarios de un empleado, o que hubiere estado así obligado de haber el empleado "
            "reclamado solamente la exención personal admitida a un individuo soltero, suministrará a cada uno de "
            "dichos empleados con respecto a su empleo durante el año natural, en o antes del treinta y uno (31) "
            "de enero del año siguiente o, si su empleo terminare antes del cierre de dicho año natural, entonces "
            "el día en que se efectuare el último pago de salarios, una declaración escrita en la que conste la "
            "siguiente información:\n"
            "(A) nombre, dirección y número de cuenta (seguro social) del patrono,\n"
            "(B) nombre, dirección y número de cuenta (seguro social) del empleado,\n"
            "(C) monto total de los salarios pagados, según dicho término se define en el apartado (a)(1),\n"
            "(D) monto de la contribución deducida y retenida bajo esta sección con respecto a dichos salarios,\n"
            "(E) monto de los gastos reembolsados al empleado, y\n"
            "(F) aportaciones en efectivo o diferidas a planes cualificados bajo la Sección 1081.01(e).\n"
            "(3) Declaraciones constituirán planillas informativas. — Las declaraciones que por este apartado se "
            "requiere sean suministradas con respecto a salarios serán suministradas en aquellas otras ocasiones, "
            "contendrán aquella otra información y serán en aquella forma que el Secretario por reglamentos "
            "prescriba. Un duplicado de dicha declaración, si fuera hecho y radicado de acuerdo con reglamentos "
            "prescritos por el Secretario, constituirá la planilla que ha de rendirse con respecto a dichos "
            "salarios bajo este Subtítulo.\n"
            "(4) Prórroga. — El Secretario, bajo aquellos reglamentos que prescriba, podrá conceder a cualquier "
            "patrono una prórroga razonable que no exceda de 30 días con respecto a las declaraciones que deben "
            "suministrarse bajo este apartado.\n"
            "(5) El Secretario podrá exigir que las declaraciones requeridas bajo este apartado sean radicadas "
            "ante el Departamento de Hacienda a través de medios electrónicos. Asimismo, se autoriza al patrono a "
            "someter los comprobantes de retención a sus empleados a través de medios electrónicos. El Secretario "
            "podrá requerir que el pago o depósito de las contribuciones retenidas bajo esta Sección se realice "
            "únicamente por medios electrónicos.\n"
            "(o) No Deducibilidad de la Contribución al Computarse el Ingreso Neto. — La contribución deducida y "
            "retenida bajo esta sección no será admitida como una deducción ni al patrono ni al receptor del "
            "ingreso al computarse el ingreso neto para los fines de cualquier contribución sobre ingresos "
            "impuesta por ley de la Asamblea Legislativa.\n"
            "(p) Reintegros o Créditos. —\n"
            "(1) Patronos. — Cuando haya habido un pago de contribución en exceso bajo esta sección, el reintegro "
            "o crédito se hará al patrono sólo hasta el límite en que el monto de dicho pago en exceso no fue "
            "deducido y retenido bajo esta sección por el patrono.\n"
            "(2) Empleados. — El reintegro o crédito en casos de retención excesiva se regirá por el Subtítulo F.\n"
            "(q) Penalidades. — Para las penalidades aplicables en el caso de cualquier persona que suministre "
            "información fraudulenta o que deje de suministrar la declaración o información requerida bajo el "
            "apartado (e) o bajo el apartado (n) de esta sección véase el Subtítulo F.\n"
            "(r) En aquellos casos de salarios por servicios prestados en trabajos ocasionales, temporales o "
            "estacionales en que el periodo de nómina con respecto al empleado sea diario y el monto de los "
            "mismos esté basado en un jornal por hora, si el patrono demuestra a satisfacción del Secretario que "
            "determinar el monto de la contribución a deducir y retener sobre dichos salarios bajo las "
            "disposiciones del apartado (b) le ocasionaría serios contratiempos, éste podrá, previa autorización "
            "del Secretario, deducir y retener la contribución sobre ingresos en el origen sobre los referidos "
            "salarios aplicando al monto total de éstos, sin considerar exención para la retención alguna un dos "
            "(2) por ciento, para pagos realizados antes del 1 de enero de 2019 y cinco (5) por ciento para pagos "
            "realizados después del 31 de diciembre de 2018. A fin de determinar si la aplicación de las "
            "disposiciones del apartado (b) ocasionaría serios contratiempos al patrono, se tomarán en "
            "consideración los siguientes factores, entre otros:\n"
            "(1) Número de empleados que tiene el patrono y que reciben salarios bajo tales circunstancias.\n"
            "(2) Regularidad con que se emplea cada uno de dichos empleados.\n"
            "(3) Rotación de empleados en el negocio del patrono.\n"
            "(4) Monto de la remuneración por empleado.\n"
            "(5) Tiempo disponible para preparar y tramitar las nóminas correspondientes a dichos salarios.\n"
            "(6) Dificultades para determinar el monto del salario del empleado por no poder predecir de antemano "
            "la duración de la tarea."
        ),
        effective_year=2011,
        source_url="https://hacienda.pr.gov/sites/default/files/codigo_de_rentas_internas_2011_segun_enmendado_28.08.2026.pdf",
        cross_references=["PR CRI Sec. 1010.01", "PR CRI Sec. 1031.02"],
        breadcrumbs="Ley 1-2011 > Subtítulo A > Capítulo 6 > Subcapítulo B > Sección 1062.01",
    ),
    StatuteRecord(
        id="pr-cri-1062-03",
        jurisdiction="PR",
        corpus="CRI",
        citation="PR CRI Sec. 1062.03",
        section="1062.03",
        title=(
            "Retención en el Origen Sobre Pagos por Servicios Prestados / Withholding at Source on Payments for "
            "Services Rendered"
        ),
        text_en=(
            "English summary; the official text is in Spanish. The Government of Puerto Rico and every person "
            "who, in a trade or business or for the production of income in Puerto Rico, makes payments to "
            "another person for services rendered must deduct and withhold 7 percent of payments made before "
            "January 1, 2019 and 10 percent of payments made after December 31, 2018; at the service provider's "
            "election, 10, 15 or 20 percent may be withheld instead (a). Lists the payments not subject to this "
            "withholding (b), including the first $500 paid during the calendar year to the service provider for "
            "taxable years beginning after December 31, 2018 (the first $1,500 for payments made before January "
            "1, 2019), wages subject to withholding under Section 1062.01, and payments for services rendered "
            "outside Puerto Rico. Also covers the payer's liability (c), the return and payment of the tax "
            "withheld (d), failure to withhold (e)-(f), waivers (g), and quarterly and annual reconciliation and "
            "informative returns (h)-(j)."
        ),
        text_es=(
            "(a) Regla General. — El Gobierno de Puerto Rico y toda persona, natural o jurídica, que en el "
            "ejercicio de una actividad de industria o negocio o para la producción de ingresos en Puerto Rico, "
            "efectúe pagos a otra persona por concepto de servicios prestados y todo pagador que efectúe pagos a "
            "un proveedor de servicios de salud por servicios de salud prestados por dicho proveedor a cualquier "
            "persona, deducirá y retendrá, para pagos realizados antes de 1 de enero de 2019, el siete (7) por "
            "ciento y, para pagos realizados después del 31 de diciembre de 2018, el diez (10) por ciento de "
            "dichos pagos. No obstante, a elección del proveedor de servicios, el pagador podrá deducir y "
            "retener, una cantidad mayor, equivalente al diez (10) por ciento, al quince (15) por ciento, o al "
            "veinte (20) por ciento de dichos pagos. Disponiéndose que, para pagos efectuados después del 31 de "
            "diciembre de 2018, el pagador podrá, a opción del proveedor de servicios, retener sobre la totalidad "
            "del ingreso pagado la tasa máxima aplicable en la Sección 1021.06. El término “Gobierno de Puerto "
            "Rico” incluye al Gobierno de Puerto Rico, sus agencias, instrumentalidades, corporaciones públicas y "
            "subdivisiones políticas. El término “pagador” significa aseguradores, asociaciones con fines no "
            "pecuniarios, cooperativas de seguros de salud, organizaciones de servicios de salud y cualquier otra "
            "persona que realice pagos a nombre de las personas aquí mencionadas. El término servicios y la "
            "retención aquí definida no incluye el pago de primas de seguro, arrendamiento o venta de propiedad "
            "mueble tangible o inmueble, imprenta, venta de periódicos, revistas y otras publicaciones "
            "(incluyendo colocación de anuncios) y contratación de tiempo de radio o televisión. No obstante, el "
            "pago de la comisión sobre la prima de seguro al agente sí estará sujeta a la retención impuesta por "
            "esta Sección. La excepción de retención dispuesta en la oración anterior, no exime al pagador de "
            "informar las cantidades pagadas, por servicios recibidos, en una declaración informativa sujeto a lo "
            "dispuesto en la Sección 1063.01.\n"
            "(b) Reglas Especiales. — La obligación de deducción y retención dispuesta en el apartado (a) de esta "
            "Sección no aplicará a:\n"
            "(1) En el caso de pagos efectuados antes del 1 de enero de 2019, los primeros mil quinientos (1,500) "
            "dólares pagados durante el año natural a la persona que prestó el servicio. En el caso de "
            "corporaciones o sociedades que operen en Puerto Rico por medio de sucursales, el límite de mil "
            "quinientos (1,500) dólares aquí dispuesto aplicará a cada sucursal por separado, a opción del agente "
            "retenedor. Disponiéndose que, para años contributivos comenzados luego del 31 de diciembre de 2018, "
            "la exención sobre la retención dispuesta en esta sección será sobre los primeros quinientos (500) "
            "dólares pagados durante el año natural a la persona que prestó el servicio.\n"
            "(2) Pagos efectuados a hospitales, clínicas, laboratorios clínicos, hogares de pacientes con "
            "enfermedades terminales, hogares de ancianos e instituciones para incapacitados.\n"
            "(3) Pagos efectuados a organizaciones exentas según lo dispuesto en la Sección 1101.01.\n"
            "(4) Ingreso o ganancia generada por vendedores directos por la venta de productos de consumo. El "
            "término “vendedores directos” significa un individuo que:\n"
            "(A) Está dedicado a la venta (o solicitación de venta) de productos de consumo a cualquier comprador "
            "a base de un acuerdo de compraventa, comisión por depósito, o cualquier arreglo similar según el "
            "Secretario determine por reglamento, para la reventa (por el comprador o cualquier otra persona) en "
            "el hogar u otro lugar que no sea un establecimiento de ventas al por menor, o\n"
            "(B) está dedicado a la venta (o solicitación de venta de) productos de consumo en el hogar u otro "
            "lugar que no sea un establecimiento de ventas al por menor. El término “vendedor directo” no incluye "
            "aquella persona que recibe comisiones o cualquier otro tipo de pagos que representen, en todo o en "
            "parte, el ingreso o la ganancia generada por un “vendedor directo” u otro tipo de vendedor que esté "
            "bajo la supervisión, el control, la dirección, o sea miembro de la red de distribución de dicha "
            "persona.\n"
            "(5) Pagos efectuados a contratistas o subcontratistas por la construcción de obras. El término "
            "“construcción de obras” no incluye servicios de arquitectura, ingeniería, diseño, consultoría, "
            "electricistas, plomeros, pintores, personal de mantenimiento, persona que provee servicios de mano "
            "de obra, seguridad y otros servicios de naturaleza similar.\n"
            "(6) Pagos por servicios prestados por individuos no residentes o corporaciones o sociedades "
            "extranjeras no dedicadas a industria o negocios en Puerto Rico que estén sujetos a la retención "
            "dispuesta en las Secciones 1062.08 y 1062.11.\n"
            "(7) Pagos de salarios sujetos a la retención dispuesta en la Sección 1062.01.\n"
            "(8) Pagos por servicios a individuos, corporaciones y sociedades durante los primeros tres (3) años "
            "del comienzo de una actividad de prestación de servicios. Esta exención podrá ser disfrutada por el "
            "contribuyente bajo las disposiciones del párrafo (4) del apartado (g) de esta Sección.\n"
            "(9) Pagos al Gobierno de Puerto Rico, sus agencias, corporaciones públicas y subdivisiones "
            "políticas.\n"
            "(10) Pagos por servicios a un agricultor bona fide que cumpla con los requisitos para la deducción "
            "dispuesta en la Sección 1033.12 o en cualquier otra disposición de ley especial equivalente.\n"
            "(11) Pagos efectuados directamente, o a través de agentes, representantes u otros intermediarios, a "
            "un porteador elegible. El término “porteador elegible” significa una persona cuya industria o "
            "negocio principal es el transporte aéreo, el transporte marítimo de carga o pasajeros o el servicio "
            "de comunicaciones telefónicas entre Puerto Rico y cualquier punto fuera de Puerto Rico. No obstante, "
            "el pago de la comisión al agente sí estará sujeto a la retención impuesta por esta Sección.\n"
            "(12) Pagos efectuados por un porteador elegible (según dicho término se define en el párrafo (11) a "
            "entidades con fines no pecuniarios, con el propósito de llevar a cabo la contabilidad, registro, "
            "informe y cobro de ventas de pasajes de transportación aérea o marítima y otros servicios "
            "relacionados a nombre de o para beneficio de dicho porteador elegible.\n"
            "(13) Pagos por servicios eclesiásticos prestados por sacerdotes u otros ministros del evangelio, "
            "debidamente autorizados u ordenados, incluyendo rabinos hebreos.\n"
            "(14) Pagos por servicios prestados fuera de Puerto Rico.\n"
            "(15) Pagos efectuados por una sociedad, sociedad especial o corporación de individuos dedicadas a la "
            "prestación de servicios sujetos a retención bajo el apartado (a) de esta Sección, a un individuo que "
            "sea socio, accionista o dueño de dicha entidad por concepto de servicios prestados por dicho "
            "individuo a la entidad. Disponiéndose que, esta exención no exime de la obligación de retener sobre "
            "la participación distribuible del socio o dueño bajo las Secciones 1062.04, 1062.05 y 1062.07 de "
            "este Código.\n"
            "(16) Pagos efectuados por una persona dedicada al ejercicio de una actividad de industria o negocio "
            "o para la producción de ingresos en Puerto Rico a otra persona dedicada al ejercicio de una "
            "actividad de industria o negocio o para la producción de ingresos en Puerto Rico y que forma parte "
            "de un grupo controlado de corporaciones o de un grupo de entidades relacionadas, según definido en "
            "las Secciones 1010.04 y 1010.05 de este Código.\n"
            "(17) Los servicios de educación continua descritos en el párrafo (11) del apartado (ll) de la "
            "Sección 4010.01.\n"
            "(c) Responsabilidad del Pagador. — Salvo que se disponga lo contrario, toda persona que venga "
            "obligada a deducir y retener cualquier contribución bajo las disposiciones de esta Sección será "
            "responsable al Secretario del pago de dicha contribución y no será responsable a persona otra alguna "
            "por el monto de cualesquiera de dichos pagos. Toda persona que al momento de rendir su planilla de "
            "contribución sobre ingresos no haya remitido al Departamento de Hacienda la totalidad del monto "
            "retenido sobre pagos descritos en el apartado (a) de esta Sección y no haya radicado ante el "
            "Departamento de Hacienda las declaraciones informativas requeridas en los apartados (h), (i) y (j) "
            "de esta Sección, no podrá reclamar dichos pagos como gastos de operación. Disponiéndose que, en el "
            "caso de personas bajo el método de acumulación o con un año económico, podrán reclamar la deducción, "
            "según reflejada en sus libros de contabilidad, siempre y cuando presenten junto a su planilla una "
            "reconciliación entre el gasto reclamado y la cantidad informada en la declaración. No obstante, no "
            "se requerirá que las personas bajo el método de acumulación o con un año económico sometan junto con "
            "su planilla una reconciliación entre el gasto reclamado y la cantidad informada en las declaraciones "
            "informativas para que puedan reclamar la deducción, cuando dicha persona radique junto con su "
            "planilla de contribución sobre ingresos el estado financiero auditado conforme a lo dispuesto en la "
            "Sección 1061.15(a) y radique la Información Suplementaria requerida en la Sección 1061.15(b).\n"
            "(d) Planilla y Pago de la Contribución Retenida. — Todo pagador que venga obligado a deducir y "
            "retener la contribución dispuesta en el apartado (a) rendirá una planilla y pagará o depositará la "
            "misma no más tarde del decimoquinto (15to) día del mes siguiente al cierre del mes natural en el "
            "cual la contribución fue deducida y retenida. Dicha planilla será rendida al Secretario y contendrá "
            "aquella información y será hecha en la forma que el Secretario establezca mediante reglamento. El "
            "Secretario podrá requerir que dicha planilla y el pago correspondiente se realicen a través de "
            "medios electrónicos únicamente.\n"
            "(e) Si se Dejare de Retener. — Si el agente retenedor dejare de efectuar la retención a que se "
            "refiere el apartado (a), la cantidad que debió ser deducida y retenida, a menos que el contribuyente "
            "pague la contribución al Secretario, le será cobrada directamente a la instrumentalidad "
            "gubernamental o a aquella persona a quien se le haya delegado la obligación de deducir y retener la "
            "contribución sobre ingresos.\n"
            "(f) Cualquier persona que dejare de cumplir con su responsabilidad de deducir y retener la "
            "contribución sobre ingresos descrita en el apartado (a) de esta Sección, estará sujeta a las "
            "penalidades dispuestas en las Secciones 6030.12 y 6041.01 del Subtítulo F, a menos que el "
            "contribuyente pague la contribución al Secretario.\n"
            "(g) Relevos. —\n"
            "(1) En el caso de entidades, según dicho término se define en la Sección 1010.05(c), que estén al "
            "día con sus responsabilidades contributivas, en lugar de la retención dispuesta en el apartado (a), "
            "se deducirá y retendrá el tres (3) por ciento, en pagos realizados antes del 1 de enero de 2019 y "
            "seis (6) por ciento en pagos realizados después del 31 de diciembre de 2018, siempre y cuando haya "
            "sometido con su planilla el Informe de Procedimientos Previamente Acordados requerido o Estados "
            "Financieros Auditados requeridos conforme a lo dispuesto en la Sección 1061.15.\n"
            "(2) En el caso de entidades según dicho término se define en la Sección 1010.05(c), con volumen de "
            "negocios de un millón (1,000,000) de dólares o más que estén al día con sus responsabilidades "
            "contributivas y sometan estados financieros acompañados por un Informe de Auditor, sujeto a lo "
            "dispuesto en la Sección 1061.15, en lugar de la retención dispuesta en el apartado (a), no se hará "
            "retención alguna por concepto de pagos por servicios prestados por estas entidades.\n"
            "(3) En el caso de individuos y entidades no incluidas en el párrafo (2) de este apartado, con "
            "volumen de negocios de un millón (1,000,000) de dólares o más, que estén al día con sus "
            "responsabilidades contributivas y sometan estados financieros acompañados por un Informe de Auditor, "
            "sujeto a lo dispuesto en la Sección 1061.15, el porciento de retención aplicable será seis (6) por "
            "ciento, en lugar de la retención dispuesta en el apartado (a).\n"
            "(4) En los casos de otros sectores o categorías de empresas o negocios en que se demuestre a "
            "satisfacción del Secretario, o en que el propio Secretario determine, que la obligación de esta "
            "sección ocasionará contratiempos indebidos a dichos sectores o categorías de empresas o negocios, "
            "sin conducir a fin práctico alguno, debido a que las cantidades así retenidas tendrán que ser "
            "reintegradas a los contribuyentes, o que dicha retención resultará excesiva, el Secretario podrá, "
            "bajo aquellas reglas y reglamentos que promulgue, relevar al agente retenedor de realizar tal "
            "retención en todo o en parte, a todas las empresas o negocios incluidos en el sector o categoría. El "
            "Secretario podrá utilizar los criterios antes mencionados para relevar, en todo o en parte, al "
            "agente retenedor de realizar la retención dispuesta en el apartado (a) o en este apartado, en los "
            "casos de corporaciones o sociedades que arrastren una cantidad sustancial de pérdida neta en "
            "operaciones, en relación con el volumen anual de negocios de dicha corporación.\n"
            "(A) Disponiéndose que, en las circunstancias descritas a continuación, se entenderá que el "
            "contribuyente ha probado que la retención le causa contratiempos indebidos sin conducir a un fin "
            "práctico:\n"
            "(i) Una persona que se encuentra en los primeros tres (3) años de operaciones de una actividad "
            "económica y no haya obtenido un relevo de dicha actividad económica bajo las disposiciones del "
            "párrafo (8) del apartado (b) de esta sección. Para estos propósitos, el hecho de que un "
            "contribuyente haya llevado a cabo la misma actividad económica en otra jurisdicción o haber "
            "realizado otra actividad económica en o fuera de Puerto Rico no será tomado en consideración;\n"
            "(I) el Secretario podrá denegar el relevo a aquellas personas que lleven a cabo sustancialmente la "
            "misma actividad por la cual ya habían recibido un relevo anteriormente;\n"
            "(ii) Un Médico Cualificado, según definido en la Ley 14-2017, a quien se le haya otorgado y tenga "
            "vigente un decreto de exención bajo dicha ley; y\n"
            "(I) Disponiéndose que en el caso de que la totalidad de los accionistas, socios o dueños de un "
            "Negocio de Servicios Médicos, según definido en la Ley 14-2017, que tribute bajo las disposiciones "
            "del Capítulo 7 o el Subcapítulo E del Capítulo 11 de este Subtítulo, sean Médicos Cualificados a "
            "quienes se les haya otorgado y tengan vigente un decreto de exención bajo dicha ley, se le otorgará "
            "un relevo total de retención.\n"
            "(iii) Una persona que haya terminado su año contributivo anterior con una pérdida neta en sus "
            "operaciones en Puerto Rico y no haya estado sujeto a Contribución Básica Alterna o Contribución "
            "Alternativa Mínima para cualquiera de los últimos tres (3) años.\n"
            "(I) No obstante, el Secretario podrá denegar el relevo a toda persona que haya reflejado pérdidas en "
            "sus operaciones por más de tres (3) años contributivos consecutivos.\n"
            "(5) En el caso de individuos o entidades que, para un año contributivo particular opten por tributar "
            "sus ingresos bajo la contribución opcional establecida en las Sección 1021.06 o 1022.07 y el total "
            "de ingreso bruto basado en la planilla del año contributivo anterior no exceda de cien mil (100,000) "
            "podrán obtener un relevo parcial para que la retención dispuesta en el apartado (a) de esta Sección "
            "sea seis (6) por ciento en lugar de la cantidad dispuesta en el apartado (a) de esta Sección. Todo "
            "individuo que obtenga este relevo parcial de retención se obliga a tributar bajo la contribución "
            "opcional dispuesta en la Sección 1021.06 para el año contributivo para el cual se solicitó dicho "
            "relevo, siempre y cuando durante dicho año contributivo cumpla con los requisitos de la Sección "
            "1021.06 o 1022.07.\n"
            "(h) Planilla Trimestral de reconciliación. — Toda persona obligada a deducir y retener cualquier "
            "contribución bajo esta Sección deberá, en o antes del último día del mes siguiente al cierre de cada "
            "uno de los trimestres terminados el treinta y uno (31) de marzo, treinta (30) de junio, treinta (30) "
            "de septiembre y treinta y uno (31) de diciembre de cada año, rendir una planilla en la que detalle "
            "los pagos efectuados, la contribución retenida y depositada durante el trimestre y pague aquella "
            "parte de la misma que no haya sido pagada o depositada conforme se establece en la forma y manera "
            "dispuesta en el Subtítulo F. Dicha planilla contendrá aquella información, y será hecha en aquella "
            "forma que el Secretario establezca mediante reglamento al efecto. El Secretario podrá requerir que "
            "la planilla trimestral de reconciliación, así como cualquier balance adeudado con esta se sometan "
            "únicamente a través de medios electrónicos. Disponiéndose que la Planilla Trimestral aquí dispuesta "
            "no deberá contener información sobre los pagos a proveedores de servicios de telecomunicaciones, "
            "servicios de acceso a internet, servicios de televisión por cable o satélite, anuncios o primas de "
            "seguro.\n"
            "(i) Estado de Reconciliación Anual. — Todo pagador que venga obligado a efectuar la deducción y "
            "retención dispuesta en el apartado (a) y a rendir una planilla por las cantidades deducidas y "
            "retenidas según se establece en el apartado (d), deberá someter, no más tarde del 28 de febrero del "
            "año siguiente, un estado de reconciliación anual en el que conste el monto de las cantidades "
            "retenidas bajo esta sección con respecto a los pagos por servicios prestados efectuados durante el "
            "año.\n"
            "(j) Declaraciones Informativas. — Todo pagador que venga obligado a efectuar la deducción y "
            "retención dispuesta en el apartado (a) y a rendir una planilla por las cantidades deducidas y "
            "retenidas según se establece en el apartado (d), deberá someter, no más tarde del 28 de febrero del "
            "año siguiente, una declaración informativa en la que conste el monto total pagado y la contribución "
            "retenida durante el año natural anterior y el nombre, dirección y número de cuenta del receptor de "
            "tales pagos. El Secretario establecerá mediante reglamento el modo de informar los pagos descritos "
            "en esta Sección y las cantidades retenidas de acuerdo con lo dispuesto en el apartado (a). El "
            "Secretario podrá exigir que las declaraciones requeridas bajo este apartado sean radicadas ante el "
            "Departamento de Hacienda a través de medios electrónicos. Asimismo, se autoriza al agente retenedor "
            "a someter las declaraciones informativas a sus proveedores de servicios a través de medios "
            "electrónicos."
        ),
        effective_year=2011,
        source_url="https://hacienda.pr.gov/sites/default/files/codigo_de_rentas_internas_2011_segun_enmendado_28.08.2026.pdf",
        cross_references=["PR CRI Sec. 1062.01"],
        breadcrumbs="Ley 1-2011 > Subtítulo A > Capítulo 6 > Subcapítulo B > Sección 1062.03",
    ),
    # Puerto Rico Incentives Code (Ley 60-2019)
    StatuteRecord(
        id="pr-ley60-1020-02",
        jurisdiction="PR",
        corpus="LEY60",
        citation="Ley 60-2019 Sec. 1020.02",
        section="1020.02",
        title="Definiciones Aplicables a Actividades de Individuos",
        text_en=(
            "English summary; the official text is in Spanish. Defines the terms used for the individual activities in "
            "Chapter 2 of Subtitle B of the Incentives Code. Paragraph (4), as amended by Act 38-2026 (approved March "
            "10, 2026, per the approval stamp on page 1 of the Departamento de Estado certified copy, "
            "https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF/2020/0038-2026.pdf; in force on approval under "
            "its Article 8), defines a Resident Individual Investor: an individual eligible for the benefits of "
            "Sections 2022.01 and 2022.02 who is a Resident Individual of Puerto Rico, was not a Resident Individual of "
            "Puerto Rico between January 17, 2006 and January 17, 2012, and becomes one no later than the taxable year "
            "ending December 31, 2055. Students studying outside Puerto Rico who lived in Puerto Rico before leaving, "
            "personnel temporarily working outside Puerto Rico for the Government of Puerto Rico, its agencies and "
            "instrumentalities, and people in similar situations do not qualify, because their domicile stays in Puerto "
            "Rico while they live outside it. For applications submitted after December 31, 2026, the individual must "
            "show that they were not a resident of Puerto Rico for at least six (6) years before moving to Puerto Rico. "
            "The other paragraphs define Special Agreement for the Creation of Businesses (1), Mi Futuro (2), Eligible "
            "Dividends of Qualified Physicians (3), Eligible Income of a Qualified Physician (5), Institution of Higher "
            "Education (6), Eligible Scientific Research (7), Eligible Researcher or Scientist (8), Young Entrepreneur "
            "(9), Qualified Physician (10), Medical Services Business (11), Other Assets (12), Hard-to-Recruit "
            "Professional (13), Professional Medical Services (14) and Full Time (15)."
        ),
        text_es=(
            "(a) Para propósitos de actividades relacionadas con el Capítulo 2 del Subtítulo B de este Código "
            "relacionado con actividades que lleven a cabo individuos, los términos, frases y palabras tendrán el "
            "significado y alcance que se expresan a continuación:\n"
            "(1) Acuerdo Especial para la Creación de Empresas. — Significa el Acuerdo que se lleve a cabo entre un "
            "Joven Empresario (según se define en este apartado) y el Secretario del DDEC. El Joven Empresario deberá "
            "comprometerse al desarrollo de su empresa, a la creación de empleos, y a otras condiciones, según aplique, "
            "a cambio de los beneficios aplicables que se disponen en este Código. Los beneficios aplicables se "
            "enumerarán específicamente en el Acuerdo. El Acuerdo establecerá el término de su vigencia y vencerá "
            "cuando los beneficios que se conceden en él caduquen, según las disposiciones de este Código y las "
            "obligaciones pactadas en el Acuerdo.\n"
            "(2) Mi Futuro. — significa el programa de plan de ahorro para estudiantes establecido en la Sección "
            "2026.01 de este Código.\n"
            "(3) Dividendos Elegibles de Médicos Cualificados. — Significa los dividendos provenientes de Ingresos "
            "Elegibles de Médicos Cualificados, que declara un Negocio de Servicios Médicos a favor de un Médico "
            "Cualificado, computado de conformidad con el Código de Rentas Internas.\n"
            "(4) Individuo Residente Inversionista. — Significa un individuo elegible para obtener los beneficios de "
            "las Secciones 2022.01 y 2022.02 de este Código y que es un Individuo Residente de Puerto Rico, que no haya "
            "sido un Individuo Residente de Puerto Rico entre el 17 de enero de 2006 y el 17 de enero de 2012, y que se "
            "convierta en un Individuo Residente de Puerto Rico no más tarde del Año Contributivo que finaliza el 31 de "
            "diciembre de 2055. Los estudiantes que cursen estudios fuera de Puerto Rico que residían en Puerto Rico "
            "antes de marcharse a estudiar, el personal que trabaje fuera de Puerto Rico temporalmente para el Gobierno "
            "de Puerto Rico, sus agencias e instrumentalidades, y personas en situaciones similares a las antes "
            "descritas, no cualificarán para considerarse como Individuos Residentes Inversionistas, ya que su "
            "domicilio en estos casos continúa siendo Puerto Rico por el período en que residan fuera de nuestra "
            "jurisdicción. Para solicitudes sometidas después del 31 de diciembre de 2026, el individuo deberá "
            "demostrar que no fue residente de Puerto Rico durante un mínimo de seis (6) años previos a la fecha de "
            "mudarse a Puerto Rico.\n"
            "(5) Ingreso Elegible de Médico Cualificado. — Significa el ingreso neto que se deriva de la prestación de "
            "Servicios Médicos Profesionales que se ofrecen en Puerto Rico, computado de conformidad con el Código de "
            "Rentas Internas.\n"
            "(6) Institución de Educación Superior. — Significa una institución educativa, pública o privada, "
            "acreditada conforme a la Ley 212-2018, según enmendada, conocida como “Ley de Registro y Licenciamiento de "
            "Instituciones de Educación” o por la Middle States Commission on Higher Education de la Middle States "
            "Association of Colleges and Schools.\n"
            "(7) Investigaciones Científicas Elegibles. — Significa cualquier investigación que lleve a cabo la "
            "Universidad de Puerto Rico u otra Institución de Educación Superior que reciba una concesión (grant) "
            "obtenida mediante una propuesta revisada por pares (peer reviewed) en una competencia abierta para obtener "
            "dicha concesión, para llevar a cabo un proyecto de investigación u otro proyecto similar incluyendo "
            "concesiones para entrenamiento, desarrollo de capacidades profesionales o desarrollo de la fuerza "
            "trabajadora (training, capacity development, and workforce development), de cualquiera de las "
            "organizaciones que componen los Institutos Nacionales de Salud o bajo programas o mecanismos similares "
            "auspiciados por cualquier otra agencia federal que promueva la investigación científica competitiva, "
            "incluyendo pero sin limitarse a, la Fundación Nacional de Ciencia (National Science Foundation), "
            "Departamento de Energía, Departamento de Defensa, NASA, NOAA, Agencia de Protección Ambiental, entre "
            "otras. Incluirán, además, las concesiones resultado de propuestas competitivas que provengan de "
            "fundaciones privadas, otras organizaciones sin fines de lucro, o empresas privadas que provean concesiones "
            "competitivas para la investigación y desarrollo. Se consideran elegibles aquellas concesiones de "
            "propuestas competitivas donde la competencia esté restringida a poblaciones minoritarias (underserved "
            "minorities) según las definiciones de minorías a nivel federal. Serán elegibles también las concesiones "
            "por subcontrato de propuestas competitivas (research subawards) donde el investigador principal del "
            "subcontrato es una Persona Doméstica.\n"
            "(8) Investigador o Científico Elegible. — Significa un Persona Doméstica, durante el Año Contributivo, "
            "contratado por la Universidad de Puerto Rico u otra Institución de Educación Superior autorizada a operar "
            "en Puerto Rico, que se dedique, entre otras funciones docentes a llevar a cabo Investigaciones Científicas "
            "Elegibles y que haya sometido una propuesta de investigación científica una organización de las descritas "
            "en la definición de Investigaciones Científicas Elegibles y que, con la aprobación de la propuesta, la "
            "institución académica reciba una concesión (grant) para investigación, según la definición de "
            "Investigaciones Científicas Elegibles, cuya cuantía cubra los costos de investigación, incluyendo, entre "
            "otros, la compensación del Investigador y del personal clave, compra de equipos y suministros, "
            "publicaciones y otros gastos relacionados, según sea aplicable. Salvo en el caso de Investigadores "
            "Principales Múltiples (Multiple Principal Investigators or Multi PI’s), no habrá más de un individuo "
            "elegible para esta deducción por concesión aprobada, según la definición de Investigaciones Científicas "
            "Elegibles, incluyendo las concesiones por subcontrato.\n"
            "(9) Joven Empresario. — Significará todo Individuo Residente de Puerto Rico, cuya edad fluctúe entre los "
            "dieciséis (16) y treinta y cinco (35) años de edad, que interese crear y operar a largo plazo una nueva "
            "empresa en Puerto Rico, por un término indefinido, y que haya obtenido su diploma de escuela superior o "
            "una certificación equivalente del Departamento de Educación de Puerto Rico, o que aún se encuentren "
            "cursando estudios y presenten evidencia que certifique que cursan estudios conducentes a obtener un "
            "certificado o diploma de escuela superior conforme a los criterios que se adopten por reglamento.\n"
            "(10) Médico Cualificado. — Significa un individuo admitido a la práctica de la medicina general o de "
            "cualquier especialidad, de la podiatría; de la audiología; de la quiropráctica; de la optometría; sea "
            "un(a) cirujano(a) dentista o practique alguna especialidad de la odontología y que ejerce a Tiempo "
            "Completo su profesión. Esta definición incluye a los médicos que se encuentran cursando sus estudios de "
            "residencia como parte de un programa acreditado.\n"
            "(11) Negocio de Servicios Médicos. — Significa cualquier corporación de servicios profesionales o compañía "
            "de responsabilidad limitada que preste Servicios Médicos Profesionales en Puerto Rico, ya sea una Entidad "
            "doméstica o una Entidad foránea, y que esté autorizada para hacer negocios en Puerto Rico.\n"
            "(12) Otros Activos. — Significa mercancías (commodities), monedas, y cualquier activo digital basado en la "
            "tecnología de cadenas de bloques (blockchain).\n"
            "(13) Profesional de Difícil Reclutamiento. — Significa un individuo elegible para obtener los beneficios "
            "de la Sección 2022.03 de este Código y que es un Individuo Residente de Puerto Rico, con un empleo a "
            "tiempo completo, cuyo talento sea indispensable por su conocimiento especializado para las operaciones de "
            "un Negocio Exento bajo este Código o Leyes de Incentivos Anteriores. El término “difícil reclutamiento” "
            "será definido mediante el Reglamento de Incentivos.\n"
            "(14) Servicios Médicos Profesionales. — Significa servicios de diagnóstico y tratamiento que ofrece un "
            "Médico Cualificado.\n"
            "(15) Tiempo Completo. — Significa que un Médico Cualificado dedica al menos cien (100) horas mensuales a "
            "ofrecer Servicios Médicos Profesionales en un hospital público o privado, en una agencia federal o "
            "estatal, en una oficina privada dedicada a ofrecer Servicios Médicos Profesionales o en una escuela de "
            "medicina acreditada.\n"
            "[Nota: El párrafo (4) del apartado (a) fue enmendado por la Ley 38-2026, aprobada el 10 de marzo de 2026 "
            "según el sello de aprobación en la página 1 de la copia certificada por el Departamento de Estado "
            "(https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF/2020/0038-2026.pdf), que comenzó a regir "
            "inmediatamente después de su aprobación (Art. 8).]"
        ),
        effective_year=2026,
        source_url="https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF/60-2019.pdf",
        cross_references=["Ley 60-2019 Sec. 2022.01", "Ley 60-2019 Sec. 2022.02"],
        breadcrumbs="Ley 60-2019 > Subtítulo A > Capítulo 2 > Sección 1020.02",
    ),
    StatuteRecord(
        id="pr-ley60-2021-01",
        jurisdiction="PR",
        corpus="LEY60",
        citation="Ley 60-2019 Sec. 2021.01",
        section="2021.01",
        title="Individuos Inversionistas que se Trasladen a Puerto Rico",
        text_en=(
            "English summary; the official text is in Spanish. Any Resident Individual Investor, as defined in Section "
            "1020.02(a)(4), may ask the Secretary of the DDEC for the economic benefits of Subchapter B of Chapter 2 "
            "(the interest, dividend and capital gain rules of Sections 2022.01 and 2022.02), subject to the limitation "
            "in Section 2022.03(b): a Hard-to-Recruit Professional who takes the Section 2022.03 benefit cannot also "
            "benefit from Sections 2022.01 and 2022.02."
        ),
        text_es=(
            "Cualquier Individuo Residente Inversionista podrá solicitarle al Secretario del DDEC los beneficios "
            "económicos que se proveen en el Subcapítulo B de este Capítulo, sujeto a la limitación provista en la "
            "Sección 2022.03(b)."
        ),
        effective_year=2019,
        source_url="https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF/60-2019.pdf",
        cross_references=[
            "Ley 60-2019 Sec. 2022.01",
            "26 U.S.C. § 933",
            "Ley 60-2019 Sec. 1020.02",
            "Ley 60-2019 Sec. 6020.10",
        ],
        breadcrumbs="Ley 60-2019 > Subtítulo B > Capítulo 2 > Sección 2021.01",
    ),
    StatuteRecord(
        id="pr-ley60-2022-01-2019",
        jurisdiction="PR",
        corpus="LEY60",
        citation="Ley 60-2019 Sec. 2022.01",
        section="2022.01",
        title=(
            "Exención al Ingreso por Intereses y Dividendos Devengados por Individuo Residente Inversionista"
        ),
        text_en=(
            "English summary; the text of this version is not in the catalog. Interest and dividends from all sources "
            "that a Resident Individual Investor earns after becoming a Puerto Rico resident and before January 1, "
            "2036, including those from a registered investment company described in Section 1112.01 of the Puerto Rico "
            "Internal Revenue Code, are fully exempt from Puerto Rico income tax, including the alternative basic tax. "
            "So are interest, financing charges, dividends and partnership distributive shares received in that period "
            "from International Banking Entities authorized under the International Banking Center Act. Capital gains "
            "are governed by Section 2022.02, not by this section."
        ),
        text_es=(
            "Resumen. Los intereses y dividendos de todas las fuentes que devengue un Individuo Residente Inversionista "
            "luego de advenir residente de Puerto Rico y antes del 1 de enero de 2036, incluidos los de una compañía "
            "inscrita de inversiones descrita en la Sección 1112.01 del Código de Rentas Internas de Puerto Rico, están "
            "totalmente exentos de la contribución sobre ingresos de Puerto Rico, incluida la contribución básica "
            "alterna. También lo están los intereses, cargos por financiamiento, dividendos y participaciones en "
            "beneficios de sociedades recibidos en ese periodo de Entidades Bancarias Internacionales autorizadas "
            "conforme a la “Ley del Centro Bancario”. La ganancia de capital se rige por la Sección 2022.02, no por "
            "esta sección."
        ),
        effective_year=2019,
        superseded_year=2026,
        source_url="https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF/60-2019.pdf",
        cross_references=["Ley 60-2019 Sec. 2022.02", "26 CFR § 1.937-2"],
        breadcrumbs="Ley 60-2019 > Subtítulo B > Capítulo 2 > Sección 2022.01",
    ),
    StatuteRecord(
        id="pr-ley60-2022-01",
        jurisdiction="PR",
        corpus="LEY60",
        citation="Ley 60-2019 Sec. 2022.01",
        section="2022.01",
        title=(
            "Exención al Ingreso por Intereses y Dividendos Devengados por Individuo Residente Inversionista"
        ),
        text_en=(
            "English summary; the official text is in Spanish. Section as amended by Act 38-2026 (approved March 10, "
            "2026, per the approval stamp on page 1 of the Departamento de Estado certified copy, "
            "https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF/2020/0038-2026.pdf; in force on approval under "
            "its Article 8). (a) For a Resident Individual Investor who applied for a decree under Sections 2022.01 and "
            "2022.02 on or before December 31, 2026: interest and dividends from all sources earned after becoming a "
            "Puerto Rico resident but before January 1, 2036, including those from a registered investment company "
            "described in Section 1112.01 of the Puerto Rico Internal Revenue Code, are fully exempt from Puerto Rico "
            "income tax, including the alternative basic tax; so are interest, financing charges, dividends and "
            "partnership distributive shares received from International Banking Entities authorized under the "
            "International Banking Center Act, earned in the same period. (b) For an investor who applied on or after "
            "January 1, 2027: such interest and dividends earned after becoming a resident but before January 1, 2056 "
            "are taxed at a 4% fixed preferential rate instead of any higher income tax, including the alternative "
            "basic tax (1), unless a more favorable rate applies under the Incentives Code, the Puerto Rico Internal "
            "Revenue Code or any other law (2). Either way, the treatment applies only while the decree has not been "
            "revoked under Section 6020.09(a). Section 2022.02 sets the companion capital gain rules, so the decree "
            "covers interest, dividends, and capital gains: for applications filed through 2026, 5% on pre-residency "
            "appreciation recognized after ten years of residency and before Jan 1, 2036 and full exemption on "
            "post-residency appreciation recognized before Jan 1, 2036; for applications filed from 2027, 5% on "
            "pre-residency appreciation recognized after ten years of residency and before Jan 1, 2056 and 4% on "
            "post-residency appreciation recognized before Jan 1, 2056."
        ),
        text_es=(
            "(a) El ingreso de todas las fuentes que devengue un Individuo Residente Inversionista, luego de haber "
            "advenido residente de Puerto Rico, pero antes del 1 de enero de 2036, que conste de intereses y "
            "dividendos, incluyendo, pero sin limitarse a, intereses y dividendos que provengan de una compañía "
            "inscrita de inversiones, según descrita en la Sección 1112.01 del Código de Rentas Internas de Puerto "
            "Rico, estará totalmente exento del pago de contribuciones sobre ingresos de Puerto Rico, incluyendo la "
            "contribución básica alterna provista en el Código de Rentas Internas de Puerto Rico. Además, el ingreso "
            "derivado por un Individuo Residente Inversionista luego de haber advenido residente de Puerto Rico, pero "
            "antes del 1 de enero de 2036, que conste de intereses, cargos por financiamiento, dividendos o "
            "participación en beneficio de sociedades recibidos de Entidades Bancarias Internacionales autorizadas "
            "conforme a la “Ley del Centro Bancario”, estará totalmente exento del pago de contribuciones sobre "
            "ingresos en Puerto Rico, incluyendo la contribución básica alterna provista en el Código de Rentas "
            "Internas de Puerto Rico. Este inciso (a) será aplicable exclusivamente a todo Individuo Residente "
            "Inversionista que presentó su solicitud para obtener un Decreto concediendo los beneficios de las "
            "Secciones 2022.01 y 2022.02 de este Código en o antes del 31 de diciembre de 2026, siempre y cuando el "
            "Decreto no hubiese sido revocado a tenor con la Sección 6020.09(a).\n"
            "(b) Fuentes que devengue un Individuo Residente Inversionista-\n"
            "(1) El ingreso de todas las fuentes que devengue un Individuo Residente Inversionista, luego de haber "
            "advenido residente de Puerto Rico, pero antes del 1 de enero de 2056, que conste de intereses y "
            "dividendos, incluyendo, pero sin limitarse a, intereses y dividendos que provengan de una compañía "
            "inscrita de inversiones, según descrita en la Sección 1112.01 del Código de Rentas Internas de Puerto "
            "Rico, estará sujeto a una tasa fija preferencial de contribución sobre ingresos de un cuatro por ciento "
            "(4%), en lugar de cualquier otra contribución sobre ingresos mayor, si alguna, dispuesta por el Código de "
            "Rentas Internas de Puerto Rico o cualquier otra ley, incluyendo la contribución básica alterna provista "
            "por el Subtítulo A del Código de Rentas Internas de Puerto Rico.\n"
            "(2) No obstante lo generalmente dispuesto en el párrafo (1) de este apartado (b), aquel ingreso de todas "
            "las fuentes que devengue un Individuo Residente Inversionista, luego de haber advenido residente de Puerto "
            "Rico, pero antes del 1 de enero de 2056, que conste de intereses y dividendos, incluyendo, pero sin "
            "limitarse a, intereses y dividendos que provengan de una compañía inscrita de inversiones, según descrita "
            "en la Sección 1112.01 del Código de Rentas Internas de Puerto Rico, que esté sujeto a una tasa "
            "contributiva más favorable por disposición de este Código, del Código de Rentas Internas de Puerto Rico o "
            "cualquier otra ley, estará sujeta al pago de contribuciones sobre ingresos conforme al tratamiento "
            "contributivo más favorable en lugar de aquella contribución sobre ingresos dispuesta en el párrafo (1) de "
            "este apartado (b).\n"
            "(3) Los párrafos (1) y (2) de este apartado (b) serán aplicable exclusivamente a todo Individuo Residente "
            "Inversionista que presentó su solicitud para obtener un Decreto concediendo los beneficios de las "
            "Secciones 2022.01 y 2022.02 de este Código en o luego del 1 de enero de 2027, siempre y cuando el Decreto "
            "no hubiese sido revocado a tenor con la Sección 6020.09(a).\n"
            "[Nota: Sección enmendada por la Ley 38-2026, aprobada el 10 de marzo de 2026 según el sello de aprobación "
            "en la página 1 de la copia certificada por el Departamento de Estado "
            "(https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF/2020/0038-2026.pdf), que comenzó a regir "
            "inmediatamente después de su aprobación (Art. 8).]"
        ),
        effective_year=2026,
        superseded_year=None,
        source_url="https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF/2020/0038-2026.pdf",
        cross_references=[
            "Ley 60-2019 Sec. 2021.01",
            "26 CFR § 1.937-2",
            "Ley 60-2019 Sec. 2022.02",
            "Ley 60-2019 Sec. 1020.02",
        ],
        breadcrumbs="Ley 60-2019 > Subtítulo B > Capítulo 2 > Sección 2022.01",
    ),
    StatuteRecord(
        id="pr-ley60-2022-02",
        jurisdiction="PR",
        corpus="LEY60",
        citation="Ley 60-2019 Sec. 2022.02",
        section="2022.02",
        title=(
            "Contribución Especial a Individuo Residente Inversionista — Ganancia Neta de Capital"
        ),
        text_en=(
            "English summary; the official text is in Spanish. Section as amended by Act 38-2026 (approved March 10, "
            "2026, per the approval stamp on page 1 of the Departamento de Estado certified copy, "
            "https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF/2020/0038-2026.pdf; in force on approval under "
            "its Article 8). (a) Pre-residency appreciation, decree applications filed on or before December 31, 2026: "
            "the part of a Resident Individual Investor's net long-term capital gain attributable to appreciation of "
            "Securities or Other Assets held before becoming a Puerto Rico resident, recognized more than ten (10) "
            "years after becoming a resident and before January 1, 2036, is taxed at 5% instead of any other tax under "
            "the Puerto Rico Internal Revenue Code and is not subject to the alternative basic tax; recognized at any "
            "other time, it is taxed under the regular rules. The gain is limited to the appreciation accrued while the "
            "investor lived outside Puerto Rico and, for taxable years after December 31, 2016, is income from sources "
            "outside Puerto Rico. (b) Post-residency appreciation, applications filed on or before December 31, 2026: "
            "all net capital gain from appreciation after becoming a resident, on assets held at the move or acquired "
            "later, that is recognized before January 1, 2036 is fully exempt from Puerto Rico income tax, including "
            "the alternative basic tax; recognized after December 31, 2035, it is taxed under the regular rules. (c) "
            "Pre-residency appreciation, applications filed on or after January 1, 2027: the same 5% rule as (a), for "
            "gains recognized more than ten years after becoming a resident and before January 1, 2056. (d) "
            "Post-residency appreciation, applications filed on or after January 1, 2027: net capital gain recognized "
            "before January 1, 2056 is taxed at a 4% fixed preferential rate instead of any higher income tax, "
            "including the alternative basic tax, unless a more favorable rate applies under the Incentives Code, the "
            "Puerto Rico Internal Revenue Code or any other law; recognized after December 31, 2055, it is taxed under "
            "the regular rules. Each paragraph applies only while the decree has not been revoked under Section "
            "6020.09(a)."
        ),
        text_es=(
            "(a) Apreciación antes de convertirse en Individuo Residente de Puerto Rico para el Individuo que radique "
            "una solicitud para un decreto de Individuo Residente Inversionista en o antes del 31 de diciembre de 2026. "
            "La parte de la ganancia neta de capital a largo plazo que genere un Individuo Residente Inversionista que "
            "sea atribuible a cualquier apreciación que tuvieran Valores u Otros Activos, que posea éste antes de "
            "convertirse en Individuo Residente de Puerto Rico, que sea reconocida luego de transcurridos diez (10) "
            "años de convertirse en Individuo Residente de Puerto Rico y antes del 1 de enero de 2036, estará sujeta al "
            "pago de una contribución de cinco por ciento (5%), en lugar de cualesquiera otras contribuciones que "
            "impone el Código de Rentas Internas de Puerto Rico, y no estará sujeta a la contribución básica alterna "
            "provista por el Subtítulo A del Código de Rentas Internas de Puerto Rico. Si tal apreciación se reconoce "
            "en cualquier otro momento, la ganancia neta de capital con relación a tales Valores u Otros Activos estará "
            "sujeta al pago de contribuciones sobre ingresos conforme al tratamiento contributivo que provee el Código "
            "de Rentas Internas de Puerto Rico. El monto de esta ganancia neta de capital a largo plazo estará limitado "
            "a la porción de la ganancia que se relacione con la apreciación que tuvieron los Valores u Otros Activos "
            "mientras el Individuo Residente Inversionista vivía fuera de Puerto Rico. Para años contributivos "
            "posteriores al 31 de diciembre de 2016, la ganancia de capital se considerará ingreso de fuentes fuera de "
            "Puerto Rico para propósitos de la contribución sobre ingresos que dispone el Código de Rentas Internas de "
            "Puerto Rico. Este inciso (a) será aplicable exclusivamente a todo Individuo Residente Inversionista que "
            "presentó su solicitud para obtener un Decreto concediendo los beneficios de las Secciones 2022.01 y "
            "2022.02 de este Código en o antes del 31 de diciembre de 2026, siempre y cuando el Decreto no hubiese sido "
            "revocado a tenor con la Sección 6020.09(a).\n"
            "(b) Apreciación después de convertirse en Individuo Residente de Puerto Rico, para el Individuo que "
            "radique una solicitud para un decreto de Individuo Residente Inversionista en o antes del 31 de diciembre "
            "de 2026. La totalidad de la ganancia neta de capital que genere un Individuo Residente Inversionista "
            "relacionada con cualquier apreciación que tuvieran Valores u Otros Activos, luego de éste convertirse en "
            "Individuo Residente de Puerto Rico, que se reconozca antes del 1 de enero de 2036, estará totalmente "
            "exenta del pago de contribuciones sobre ingresos de Puerto Rico, incluyendo la contribución básica alterna "
            "que provee el Código de Rentas Internas de Puerto Rico. Si tal apreciación se reconoce luego del 31 de "
            "diciembre de 2035, la ganancia neta de capital con relación a tales Valores u Otros Activos estará sujeta "
            "al pago de contribuciones sobre ingresos conforme al tratamiento contributivo que provee el Código de "
            "Rentas Internas de Puerto Rico. El monto de esta ganancia neta de capital se refiere a la porción de la "
            "ganancia que se relacione a la apreciación que tuvieron los Valores u Otros Activos que el Individuo "
            "Residente Inversionista poseía al momento de convertirse en Individuo Residente de Puerto Rico y a los que "
            "éste adquiera luego de convertirse en Individuo Residente de Puerto Rico. Este inciso (b) será aplicable "
            "exclusivamente a todo Individuo Residente Inversionista que presentó su solicitud para obtener un Decreto "
            "concediendo los beneficios de las Secciones 2022.01 y 2022.02 de este Código en o antes del 31 de "
            "diciembre de 2026, siempre y cuando el Decreto no hubiese sido revocado a tenor con la Sección 6020.09(a).\n"
            "(c) Apreciación antes de convertirse en Individuo Residente de Puerto Rico, para el Individuo que radicó "
            "una solicitud para un decreto de Individuo Residente Inversionista del 1 de enero de 2027 en adelante. La "
            "parte de la ganancia neta de capital a largo plazo que genere un Individuo Residente Inversionista que sea "
            "atribuible a cualquier apreciación que tuvieran Valores u Otros Activos, que posea éste antes de "
            "convertirse en Individuo Residente de Puerto Rico, que sea reconocida luego de transcurridos diez (10) "
            "años de convertirse en Individuo Residente de Puerto Rico y antes del 1 de enero de 2056, estará sujeta al "
            "pago de una contribución de cinco por ciento (5%), en lugar de cualesquiera otras contribuciones que "
            "impone el Código de Rentas Internas de Puerto Rico, y no estará sujeta a la contribución básica alterna "
            "provista por el Subtítulo A del Código de Rentas Internas de Puerto Rico. Si tal apreciación se reconoce "
            "en cualquier otro momento, la ganancia neta de capital con relación a tales Valores u Otros Activos estará "
            "sujeta al pago de contribuciones sobre ingresos conforme al tratamiento contributivo que provee el Código "
            "de Rentas Internas de Puerto Rico. El monto de esta ganancia neta de capital a largo plazo estará limitado "
            "a la porción de la ganancia que se relacione con la apreciación que tuvieron los Valores u Otros Activos "
            "mientras el Individuo Residente Inversionista vivía fuera de Puerto Rico. Para años contributivos "
            "posteriores al 31 de diciembre de 2016, la ganancia de capital se considerará ingreso de fuentes fuera de "
            "Puerto Rico para propósitos de la contribución sobre ingresos que dispone el Código de Rentas Internas de "
            "Puerto Rico. Este inciso (c) será aplicable exclusivamente a todo Individuo Residente Inversionista que "
            "presentó su solicitud para obtener un Decreto concediendo los beneficios de las Secciones 2022.01 y "
            "2022.02 de este Código en o luego del 1 de enero de 2027, siempre y cuando el Decreto no hubiese sido "
            "revocado a tenor con la Sección 6020.09(a).\n"
            "(d) Apreciación después de convertirse en Individuo Residente de Puerto Rico, para el Individuo que radicó "
            "una solicitud para un decreto de Individuo Residente Inversionista del 1 de enero de 2027 en adelante. La "
            "totalidad de la ganancia neta de capital que genere un Individuo Residente Inversionista relacionada con "
            "cualquier apreciación que tuvieran Valores u Otros Activos, luego de éste convertirse en Individuo "
            "Residente de Puerto Rico, que se reconozca antes del 1 de enero de 2056, estará sujeta a una tasa fija "
            "preferencial de contribución sobre ingresos de un cuatro por ciento (4%), en lugar de cualquier otra "
            "contribución sobre ingresos mayor, si alguna, dispuesta por el Código de Rentas Internas de Puerto Rico o "
            "cualquier otra ley, incluyendo la contribución básica alterna provista por el Subtítulo A del Código de "
            "Rentas Internas de Puerto Rico. No obstante lo anterior, aquella ganancia neta de capital que genere un "
            "Individuo Residente Inversionista relacionada con cualquier apreciación que tuvieran Valores u Otros "
            "Activos, luego de éste convertirse en Individuo Residente de Puerto Rico, que se reconozca antes del 1 de "
            "enero de 2056, que esté sujeto a una tasa contributiva más favorable por disposición de este Código, del "
            "Código de Rentas Internas de Puerto Rico o cualquier otra ley, estará sujeta al pago de contribuciones "
            "sobre ingresos conforme al tratamiento contributivo más favorable en lugar de aquella contribución sobre "
            "ingresos dispuesta en la oración anterior de este apartado (d). Si tal apreciación se reconoce luego del "
            "31 de diciembre de 2055, la ganancia neta de capital con relación a tales Valores u Otros Activos estará "
            "sujeta al pago de contribuciones sobre ingresos conforme al tratamiento contributivo que provee el Código "
            "de Rentas Internas de Puerto Rico. El monto de esta ganancia neta de capital se refiere a la porción de la "
            "ganancia que se relacione a la apreciación que tuvieron los Valores u Otros Activos que el Individuo "
            "Residente Inversionista poseía al momento de convertirse en Individuo Residente de Puerto Rico y a los que "
            "éste adquiera luego de convertirse en Individuo Residente de Puerto Rico. Este inciso (d) será aplicable "
            "exclusivamente a todo Individuo Residente Inversionista que presentó su solicitud para obtener un Decreto "
            "concediendo los beneficios de las Secciones 2022.01 y 2022.02 de este Código en o luego del 1 de enero de "
            "2027, siempre y cuando el Decreto no hubiese sido revocado a tenor con la Sección 6020.09(a).\n"
            "[Nota: Sección enmendada por la Ley 38-2026, aprobada el 10 de marzo de 2026 según el sello de aprobación "
            "en la página 1 de la copia certificada por el Departamento de Estado "
            "(https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF/2020/0038-2026.pdf), que comenzó a regir "
            "inmediatamente después de su aprobación (Art. 8).]"
        ),
        effective_year=2026,
        source_url="https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF/2020/0038-2026.pdf",
        cross_references=["Ley 60-2019 Sec. 2022.01", "Ley 60-2019 Sec. 1020.02"],
        breadcrumbs="Ley 60-2019 > Subtítulo B > Capítulo 2 > Sección 2022.02",
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
        source_url="https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF/60-2019.pdf",
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
        source_url="https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF/60-2019.pdf",
        cross_references=["Ley 60-2019 Sec. 2031.01", "PR CRI Sec. 1010.01"],
        breadcrumbs="Ley 60-2019 > Subtítulo B > Capítulo 3 > Sección 2032.01",
    ),
    StatuteRecord(
        id="pr-ley60-6020-03",
        jurisdiction="PR",
        corpus="LEY60",
        citation="Ley 60-2019 Sec. 6020.03",
        section="6020.03",
        title="Periodo de Exención, Renegociación y Extensión de Decreto",
        text_en=(
            "English summary; the official text is in Spanish. (a) An exempt business holding a decree under the "
            "Incentives Code enjoys its benefits for fifteen (15) years while it meets the decree's requirements and "
            "conditions, unless the Code provides otherwise. (b) An exempt business under the Code or under prior "
            "incentive laws may ask to renegotiate its decree to receive the Code's benefits, under the procedure set "
            "by the Incentives Regulation. (c) An exempt business that met its decree's requirements throughout its "
            "exemption period and shows the Secretary of the DDEC that an extension serves Puerto Rico's best economic "
            "and social interests may ask for a fifteen (15) year extension, for a total of thirty (30) years. (d) "
            "Added by Act 38-2026 (approved March 10, 2026, per the approval stamp on page 1 of the Departamento de "
            "Estado certified copy, https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF/2020/0038-2026.pdf; in "
            "force on approval under its Article 8): decree applications under Section 2021.01 filed on or before "
            "December 31, 2026 and not yet granted or approved may, at the applicant's election, be processed under "
            "Sections 2022.01(b) and 2022.02(c) and (d). A Resident Individual Investor who holds a decree under Act "
            "22-2012 or a Section 2021.01 decree applied for on or before December 31, 2026 may ask to modify the "
            "decree to reflect the terms and conditions of Sections 2022.01(b) and 2022.02(c) and (d)."
        ),
        text_es=(
            "(a) Periodo de Exención. — Todo Negocio Exento que posea un Decreto bajo este Código, disfrutará de los "
            "beneficios por un período de quince (15) años siempre que durante este término cumpla con los requisitos y "
            "las condiciones establecidos en el Decreto, a menos que se disponga de otro modo en este Código.\n"
            "(b) Renegociación. — Todo Negocio Exento bajo este Código, o bajo Leyes de Incentivos Anteriores, podrá "
            "solicitar renegociar su Decreto para el disfrute de los beneficios concedidos bajo este Código. El "
            "Reglamento de Incentivos dispondrá los requisitos y el procedimiento a seguir para la renegociación de un "
            "Decreto bajo este Código.\n"
            "(c) Extensión. — Todo Negocio Exento bajo este Código, o bajo las Leyes de Incentivosanteriores, que, a "
            "través de todo su período de exención, haya cumplido con los requisitos o las condiciones establecidos en "
            "el Decreto, y que demuestre al Secretario del DDEC que la extensión de su Decreto redundará en los mejores "
            "intereses económicos y sociales del pueblo de Puerto Rico, podrá solicitar al Secretario una extensión de "
            "su Decreto por quince (15) años adicionales, para un total de treinta (30) años.\n"
            "(d) Reglas aplicables a decretos de Individuo Residente Inversionista. — Las solicitudes de Decretos bajo "
            "la Sección 2021.01 de este Código que se hayan radicado en o antes del 31 de diciembre de 2026 que no se "
            "hayan concedido o aprobado, podrán tramitarse, a elección del solicitante, bajo las disposiciones de las "
            "Secciones 2022.01(b) y 2022.02(c) y (d) de este Código. Todo Individuo Residente Inversionista que (1) "
            "haya obtenido un Decreto bajo la Ley 22-2012, según enmendada, o (2) haya obtenido un Decreto bajo la "
            "Sección 2021.01 de este Código que fue solicitado en o antes del 31 de diciembre de 2026, podrá solicitar "
            "una modificación a su Decreto para reflejar los términos y condiciones de las Secciones 2022.01(b) y "
            "2022.02(c) y (d) de este Código.\n"
            "[Nota: El apartado (d) fue añadido por la Ley 38-2026, aprobada el 10 de marzo de 2026 según el sello de "
            "aprobación en la página 1 de la copia certificada por el Departamento de Estado "
            "(https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF/2020/0038-2026.pdf), que comenzó a regir "
            "inmediatamente después de su aprobación (Art. 8).]"
        ),
        effective_year=2026,
        source_url="https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF/60-2019.pdf",
        cross_references=[
            "Ley 60-2019 Sec. 2021.01",
            "Ley 60-2019 Sec. 2022.01",
            "Ley 60-2019 Sec. 2022.02",
        ],
        breadcrumbs="Ley 60-2019 > Subtítulo F > Capítulo 2 > Sección 6020.03",
    ),
    StatuteRecord(
        id="pr-ley60-6020-10",
        jurisdiction="PR",
        corpus="LEY60",
        citation="Ley 60-2019 Sec. 6020.10",
        section="6020.10",
        title="Informes",
        text_en=(
            "English summary; the official text is in Spanish. Requires every exempt business holding a decree to file "
            "an annual income tax return and annual compliance reports (a). The annual reports of exempt businesses "
            "under Section 2021.01 (resident individual investors) must be accompanied by evidence of a minimum annual "
            "contribution of $10,000 to nonprofit entities operating in Puerto Rico; $5,000 goes to nonprofits that "
            "address child poverty and appear on the list published by the Comisión Especial Conjunta de Fondos "
            "Legislativos para Impacto Comunitario, $2,500 to another nonprofit not on that list, and $2,500 to the "
            "Fondo Especial para la Igualdad Social, although nothing prevents the entire (100%) annual contribution "
            "from going to nonprofits on that list (b). A resident individual investor must show the purchase of real "
            "property in Puerto Rico as principal residence within two years after obtaining the decree, and keep it as "
            "principal residence for the life of the decree; under Act 38-2026 (approved March 10, 2026, per the "
            "approval stamp on page 1 of the Departamento de Estado certified copy, "
            "https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF/2020/0038-2026.pdf; in force on approval under "
            "its Article 8), for an individual who applied for the decree on or after January 1, 2027, that evidence "
            "must show that full ownership of the principal residence is recorded, or pending recording, in the Puerto "
            "Rico Property Registry in the investor's name, jointly with the spouse, or in the name of a trust "
            "described in Section 2022.07 (c). Filing the annual reports under Section 2021.01 or Act 22-2012 carries "
            "an annual fee of $5,000: $300 to a special fund administered by the DDEC and $4,700 to the General Fund "
            "(d). Failing to file a required report, or filing it late, can bring an administrative fine of up to "
            "$10,000 (e). For taxable years beginning after December 31, 2024, every report required by the section, "
            "including the compliance report, must be filed electronically with the Secretary of the Treasury as part "
            "of the exempt business's income tax return, together with the corresponding fee; the Secretary may "
            "postpone this requirement by one additional year for all or some decree holders through a publication of "
            "general applicability (f). Hacienda and the DDEC postponed it in Determinación Administrativa Núm. 26-01 / "
            "Orden Administrativa Núm. DDEC 2026-01 of February 5, 2026, so electronic filing with the income tax "
            "return applies to taxable years beginning after December 31, 2025."
        ),
        text_es=(
            "(a) Informes Requeridos a Negocios Exentos y a sus Accionistas:\n"
            "(1) Todo Negocio Exento que posea un Decreto bajo este Código, radicará anualmente ante el "
            "Secretario de Hacienda una planilla de contribución sobre ingresos, independientemente de la "
            "cantidad de su ingreso bruto o neto, separada de cualquier otra planilla que por otros motivos esté "
            "obligado a rendir con relación a las operaciones de la industria cubiertas por los beneficios "
            "provistos en este Código, y de acuerdo con el Código de Rentas de Puerto Rico. El Secretario de "
            "Hacienda podrá compartir con el Secretario del DDEC la información así recibida, siempre y cuando se "
            "proteja la confidencialidad de la información.\n"
            "(2) Todo Accionista de un Negocio Exento que posea un Decreto concedido bajo este Código, deberá "
            "rendir anualmente ante el Departamento de Hacienda una planilla de contribución sobre ingresos "
            "conforme a las disposiciones del Código de Rentas Internas, siempre que bajo las disposiciones del "
            "Código de Rentas Internas tuviera la obligación de así hacerlo.\n"
            "(3) Todo Negocio Exento que posea un Decreto bajo este Código o cualquier Ley de Incentivos "
            "Anteriores, anualmente radicará electrónicamente con la Oficina de Incentivos, no más tarde del "
            "quince (15) de noviembre siguiente al cierre del año natural, en el caso de Negocios Exentos con un "
            "año natural o el decimoquinto (15to) día del undécimo (11mo) mes siguiente al cierre del año "
            "contributivo, en el caso de Negocio Exento, un informe de cumplimiento.\n"
            "(4) Todo Negocio Exento que posea un Decreto bajo este Código, anualmente radicará electrónicamente "
            "con la Oficina de Incentivos, no más tarde de treinta (30) días después de la fecha prescrita por "
            "ley para la radicación de la correspondiente planilla de contribución sobre ingresos, incluyendo las "
            "prórrogas concedidas para este propósito, un informe de cumplimiento.\n"
            "(i) Este informe deberá venir acompañado por los derechos que se dispongan por reglamento, y los "
            "mismos serán pagados mediante transferencia electrónica a través del Portal electrónico de la forma "
            "y manera que para estos propósitos establezca la Oficina de Incentivos. La información ofrecida en "
            "este informe anual será utilizada para propósitos de estadísticas y estudios económicos, así como "
            "para la evaluación que deberá realizar el Profesional de Cumplimiento cada dos (2) años para "
            "conceder o denegar el Certificado de Cumplimiento relacionado, excepto en el caso de los Negocios "
            "Exentos exceptuados del cumplimiento con el apartado (c) de la Sección 6011.04.\n"
            "(ii) Este informe deberá venir acompañado por los derechos que se dispongan por reglamento, y los "
            "mismos serán pagados mediante transferencia electrónica a través del Portal electrónico de la forma "
            "y manera que para estos propósitos establezca la Oficina de Incentivos. La información ofrecida en "
            "este informe anual será utilizada para propósitos de estadísticas y estudios económicos, así como "
            "para la evaluación que deberá realizar el Profesional de Cumplimiento cada dos (2) años para "
            "conceder o denegar el Certificado de Cumplimiento relacionado.\n"
            "(iii) Este informe se radicará electrónicamente en el Portal electrónico que para estos propósitos "
            "establecerá la Oficina de Incentivos.\n"
            "(iv) El Secretario del DDEC establecerá mediante reglamento, orden administrativa o cualquier otra "
            "comunicación de naturaleza similar, la información y documentación que se requiera para completar "
            "este informe.\n"
            "(b) Los informes anuales que requiere este Código para Negocios Exentos bajo la Sección 2021.01 "
            "deberán estar acompañados con evidencia de una aportación anual mínima de diez mil dólares (10,000), "
            "que serán destinados a entidades sin fines de lucro operando en Puerto Rico bajo la Sección 1101.01 "
            "del Código de Rentas Internas de Puerto Rico, incluyendo al Fondo Especial para la Igualdad Social, "
            "que no sea controlada por la misma persona, ni por su descendientes o ascendientes, cónyuges o "
            "socios. Independientemente de la cantidad aportada sobre el mínimo dispuesto en este apartado, cinco "
            "mil dólares ($5,000) estarán destinados a entidades sin fines de lucro que tengan servicios "
            "dirigidos a atender la erradicación de la pobreza infantil, que operen en la jurisdicción de Puerto "
            "Rico bajo la Sección 1101.01 del Código de Rentas Internas, que no sea controlada por la misma "
            "persona, ni por su descendientes o ascendientes, cónyuges o socios, y que se encuentre en la lista "
            "que publicará la “Comisión Especial Conjunta de Fondos Legislativos para Impacto Comunitario” en o "
            "antes del 31 de diciembre de cada año de conformidad con las disposiciones de este Código. Nada "
            "impedirá que el cien por ciento (100%) de la aportación anual sea destinada a las entidades sin "
            "fines de lucro que se encuentren en la citada lista. Dos mil quinientos (2,500) dólares deberán ser "
            "destinados a cualquier otra entidad sin fines de lucro operando en Puerto Rico bajo la Sección "
            "1101.01 del Código de Rentas Internas de Puerto Rico, incluyendo al Fondo Especial para la Igualdad "
            "Social, que no sea controlada por la misma persona, ni por su descendientes o ascendientes, cónyuges "
            "o socios y que no se encuentre en la lista publicada por la Comisión Especial de Fondos Legislativos "
            "para Impacto Comunitario. El Negocio Exento tendrá que evidenciarle a la Oficina de Exención que la "
            "entidad sin fines de lucro seleccionada es una entidad que brinda servicios directos a la comunidad. "
            "La aportación se realizará de forma directa a la entidad sin fines de lucro seleccionada por el "
            "Negocio Exento bajo la Sección 2021.01 que realiza la aportación anual. No obstante, la Oficina de "
            "Exención enviará, no más tarde de treinta (30) días a la Comisión Especial de Fondos Legislativos "
            "para Impacto Comunitario un informe detallado de las entidades sin fines de lucro que reciban la "
            "aportación. Los restantes dos mil quinientos (2,500) dólares deberán ser destinados al Fondo "
            "Especial para la Igualdad Social.\n"
            "(c) En lo que concierne al Individuo Residente Inversionista, deberá someter evidencia de haber "
            "adquirido, como único dueño, o en conjunto con su cónyuge, por compra, dentro de los dos (2) años "
            "después de la obtención del Decreto bajo las disposiciones de este Código, la titularidad de "
            "propiedad inmueble en Puerto Rico, adquiriendo de un dueño, sea una persona o empresa totalmente "
            "desvinculada y ajena a la persona con el Decreto bajo este Código, para que constituya su residencia "
            "principal en la jurisdicción de Puerto Rico y acreditar en el Informe anual, que mantiene el "
            "exclusivo y completo dominio de un bien inmueble como residencia principal, sea de forma exclusiva o "
            "junto a su cónyuge, durante toda la vigencia del Decreto. Para el Individuo que radicó una solicitud "
            "para un decreto de Individuo Residente Inversionista del 1 de enero de 2027 en adelante, dicha "
            "evidencia acreditará que la titularidad en pleno dominio del bien inmueble que constituye la "
            "residencia principal del Individuo Residente Inversionista consta inscrita, o pendiente de "
            "inscripción, en el Registro de la Propiedad de Puerto Rico a nombre del Individuo Residente "
            "Inversionista, o en conjunto con su cónyuge, o a nombre de un fideicomiso descrito en la Sección "
            "2022.07.\n"
            "(d) La radicación de los informes anuales requeridos para Negocios Exentos bajo la Sección 2021.01 "
            "de este Código, o bajo la Ley 22-2012, según enmendada, conocida como la “Ley Para Incentivar el "
            "Traslado de Individuos Inversionistas a Puerto Rico”, estará sujeta a un cargo anual de cinco mil "
            "(5,000) dólares, de los cuales trescientos (300) dólares serán pagaderos a favor del Secretario de "
            "Hacienda y nutrirán un Fondo Especial administrado por el DDEC, y cuatro mil setecientos (4,700) "
            "dólares serán pagaderos a favor del Secretario de Hacienda que serán destinados al Fondo General del "
            "Gobierno de Puerto Rico.\n"
            "(e) El Secretario del DDEC, luego de serle informado por la agencia concernida, podrá imponer una "
            "multa administrativa de hasta un máximo de diez mil (10,000) dólares a cualquier Negocio Exento que "
            "posea un Decreto concedido bajo este Código y que deje de radicar alguno de los informes que el "
            "Secretario de Hacienda, el Secretario del DDEC o el Comisionado de Seguros le requiera, o que "
            "radique los mismos después de la fecha de su vencimiento. La radicación de un informe incompleto se "
            "considerará como no radicado, si la agencia concernida notifica al Negocio Exento de alguna omisión "
            "en el informe requerido y dicho Negocio Exento no somete la información que falta dentro de quince "
            "(15) días de haber sido notificada, o no justifica razonablemente la falta de la misma.\n"
            "(f) Todo informe requerido por esta Sección, incluyendo el informe de cumplimiento, para años "
            "contributivos comenzados luego del 31 de diciembre de 2024, deberá ser radicado electrónicamente "
            "ante el Secretario de Hacienda como parte de la planilla de contribución sobre ingresos del Negocio "
            "Exento, junto al pago de derechos correspondiente. El Secretario de Hacienda podrá posponer la "
            "entrada en vigor de las disposiciones de este apartado por un año adicional para todos o algunos "
            "concesionarios mediante publicación de carácter general.\n"
            "[Nota: El apartado (c) fue enmendado por la Ley 38-2026, aprobada el 10 de marzo de 2026 según el sello de "
            "aprobación en la página 1 de la copia certificada por el Departamento de Estado "
            "(https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF/2020/0038-2026.pdf), que comenzó a regir "
            "inmediatamente después de su aprobación (Art. 8).]"
        ),
        effective_year=2026,
        source_url="https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF/60-2019.pdf",
        cross_references=["Ley 60-2019 Sec. 2021.01"],
        breadcrumbs="Ley 60-2019 > Subtítulo F > Capítulo 2 > Sección 6020.10",
    ),
    StatuteRecord(
        id="pr-ley60-6020-10-2019",
        jurisdiction="PR",
        corpus="LEY60",
        citation="Ley 60-2019 Sec. 6020.10",
        section="6020.10",
        title="Informes",
        text_en=(
            "English summary; the official text is in Spanish. Requires every exempt business holding a decree to file "
            "an annual income tax return and annual compliance reports (a). The annual reports of exempt businesses "
            "under Section 2021.01 (resident individual investors) must be accompanied by evidence of a minimum annual "
            "contribution of $10,000 to nonprofit entities operating in Puerto Rico; $5,000 goes to nonprofits that "
            "address child poverty and appear on the list published by the Comisión Especial Conjunta de Fondos "
            "Legislativos para Impacto Comunitario, $2,500 to another nonprofit not on that list, and $2,500 to the "
            "Fondo Especial para la Igualdad Social, although nothing prevents the entire (100%) annual contribution "
            "from going to nonprofits on that list (b). A resident individual investor must show the purchase of real "
            "property in Puerto Rico as principal residence within two years after obtaining the decree (c). Filing the "
            "annual reports under Section 2021.01 or Act 22-2012 carries an annual fee of $5,000: $300 to a special "
            "fund administered by the DDEC and $4,700 to the General Fund (d). Failing to file a required report, or "
            "filing it late, can bring an administrative fine of up to $10,000 (e). This is the version in force before "
            "2026; it was superseded in 2026 by the text as amended by Act 38-2026 (pr-ley60-6020-10)."
        ),
        text_es=(
            "(a) Informes Requeridos a Negocios Exentos y a sus Accionistas:\n"
            "(1) Todo Negocio Exento que posea un Decreto bajo este Código, radicará anualmente ante el "
            "Secretario de Hacienda una planilla de contribución sobre ingresos, independientemente de la "
            "cantidad de su ingreso bruto o neto, separada de cualquier otra planilla que por otros motivos esté "
            "obligado a rendir con relación a las operaciones de la industria cubiertas por los beneficios "
            "provistos en este Código, y de acuerdo con el Código de Rentas de Puerto Rico. El Secretario de "
            "Hacienda podrá compartir con el Secretario del DDEC la información así recibida, siempre y cuando se "
            "proteja la confidencialidad de la información.\n"
            "(2) Todo Accionista de un Negocio Exento que posea un Decreto concedido bajo este Código, deberá "
            "rendir anualmente ante el Departamento de Hacienda una planilla de contribución sobre ingresos "
            "conforme a las disposiciones del Código de Rentas Internas, siempre que bajo las disposiciones del "
            "Código de Rentas Internas tuviera la obligación de así hacerlo.\n"
            "(3) Todo Negocio Exento que posea un Decreto bajo este Código o cualquier Ley de Incentivos "
            "Anteriores, anualmente radicará electrónicamente con la Oficina de Incentivos, no más tarde del "
            "quince (15) de noviembre siguiente al cierre del año natural, en el caso de Negocios Exentos con un "
            "año natural o el decimoquinto (15to) día del undécimo (11mo) mes siguiente al cierre del año "
            "contributivo, en el caso de Negocio Exento, un informe de cumplimiento.\n"
            "(4) Todo Negocio Exento que posea un Decreto bajo este Código, anualmente radicará electrónicamente "
            "con la Oficina de Incentivos, no más tarde de treinta (30) días después de la fecha prescrita por "
            "ley para la radicación de la correspondiente planilla de contribución sobre ingresos, incluyendo las "
            "prórrogas concedidas para este propósito, un informe de cumplimiento.\n"
            "(i) Este informe deberá venir acompañado por los derechos que se dispongan por reglamento, y los "
            "mismos serán pagados mediante transferencia electrónica a través del Portal electrónico de la forma "
            "y manera que para estos propósitos establezca la Oficina de Incentivos. La información ofrecida en "
            "este informe anual será utilizada para propósitos de estadísticas y estudios económicos, así como "
            "para la evaluación que deberá realizar el Profesional de Cumplimiento cada dos (2) años para "
            "conceder o denegar el Certificado de Cumplimiento relacionado, excepto en el caso de los Negocios "
            "Exentos exceptuados del cumplimiento con el apartado (c) de la Sección 6011.04.\n"
            "(ii) Este informe deberá venir acompañado por los derechos que se dispongan por reglamento, y los "
            "mismos serán pagados mediante transferencia electrónica a través del Portal electrónico de la forma "
            "y manera que para estos propósitos establezca la Oficina de Incentivos. La información ofrecida en "
            "este informe anual será utilizada para propósitos de estadísticas y estudios económicos, así como "
            "para la evaluación que deberá realizar el Profesional de Cumplimiento cada dos (2) años para "
            "conceder o denegar el Certificado de Cumplimiento relacionado.\n"
            "(iii) Este informe se radicará electrónicamente en el Portal electrónico que para estos propósitos "
            "establecerá la Oficina de Incentivos.\n"
            "(iv) El Secretario del DDEC establecerá mediante reglamento, orden administrativa o cualquier otra "
            "comunicación de naturaleza similar, la información y documentación que se requiera para completar "
            "este informe.\n"
            "(b) Los informes anuales que requiere este Código para Negocios Exentos bajo la Sección 2021.01 "
            "deberán estar acompañados con evidencia de una aportación anual mínima de diez mil dólares (10,000), "
            "que serán destinados a entidades sin fines de lucro operando en Puerto Rico bajo la Sección 1101.01 "
            "del Código de Rentas Internas de Puerto Rico, incluyendo al Fondo Especial para la Igualdad Social, "
            "que no sea controlada por la misma persona, ni por su descendientes o ascendientes, cónyuges o "
            "socios. Independientemente de la cantidad aportada sobre el mínimo dispuesto en este apartado, cinco "
            "mil dólares ($5,000) estarán destinados a entidades sin fines de lucro que tengan servicios "
            "dirigidos a atender la erradicación de la pobreza infantil, que operen en la jurisdicción de Puerto "
            "Rico bajo la Sección 1101.01 del Código de Rentas Internas, que no sea controlada por la misma "
            "persona, ni por su descendientes o ascendientes, cónyuges o socios, y que se encuentre en la lista "
            "que publicará la “Comisión Especial Conjunta de Fondos Legislativos para Impacto Comunitario” en o "
            "antes del 31 de diciembre de cada año de conformidad con las disposiciones de este Código. Nada "
            "impedirá que el cien por ciento (100%) de la aportación anual sea destinada a las entidades sin "
            "fines de lucro que se encuentren en la citada lista. Dos mil quinientos (2,500) dólares deberán ser "
            "destinados a cualquier otra entidad sin fines de lucro operando en Puerto Rico bajo la Sección "
            "1101.01 del Código de Rentas Internas de Puerto Rico, incluyendo al Fondo Especial para la Igualdad "
            "Social, que no sea controlada por la misma persona, ni por su descendientes o ascendientes, cónyuges "
            "o socios y que no se encuentre en la lista publicada por la Comisión Especial de Fondos Legislativos "
            "para Impacto Comunitario. El Negocio Exento tendrá que evidenciarle a la Oficina de Exención que la "
            "entidad sin fines de lucro seleccionada es una entidad que brinda servicios directos a la comunidad. "
            "La aportación se realizará de forma directa a la entidad sin fines de lucro seleccionada por el "
            "Negocio Exento bajo la Sección 2021.01 que realiza la aportación anual. No obstante, la Oficina de "
            "Exención enviará, no más tarde de treinta (30) días a la Comisión Especial de Fondos Legislativos "
            "para Impacto Comunitario un informe detallado de las entidades sin fines de lucro que reciban la "
            "aportación. Los restantes dos mil quinientos (2,500) dólares deberán ser destinados al Fondo "
            "Especial para la Igualdad Social.\n"
            "(c) En lo que concierne al Individuo Residente Inversionista, deberá someter evidencia de haber adquirido, "
            "como único dueño, o en conjunto con su cónyuge, por compra, dentro de los dos (2) años después de la "
            "obtención del Decreto bajo las disposiciones de este Código, la titularidad de propiedad inmueble en "
            "Puerto Rico, adquiriendo de un dueño, sea una persona o empresa totalmente desvinculada y ajena a la "
            "persona con el Decreto bajo este Código, para que constituya su residencia principal en la jurisdicción de "
            "Puerto Rico y acreditar en el Informe anual, que mantiene el exclusivo y completo dominio de un bien "
            "inmueble como residencia principal, sea de forma exclusiva o junto a su cónyuge, durante toda la vigencia "
            "del Decreto.\n"
            "(d) La radicación de los informes anuales requeridos para Negocios Exentos bajo la Sección 2021.01 "
            "de este Código, o bajo la Ley 22-2012, según enmendada, conocida como la “Ley Para Incentivar el "
            "Traslado de Individuos Inversionistas a Puerto Rico”, estará sujeta a un cargo anual de cinco mil "
            "(5,000) dólares, de los cuales trescientos (300) dólares serán pagaderos a favor del Secretario de "
            "Hacienda y nutrirán un Fondo Especial administrado por el DDEC, y cuatro mil setecientos (4,700) "
            "dólares serán pagaderos a favor del Secretario de Hacienda que serán destinados al Fondo General del "
            "Gobierno de Puerto Rico.\n"
            "(e) El Secretario del DDEC, luego de serle informado por la agencia concernida, podrá imponer una "
            "multa administrativa de hasta un máximo de diez mil (10,000) dólares a cualquier Negocio Exento que "
            "posea un Decreto concedido bajo este Código y que deje de radicar alguno de los informes que el "
            "Secretario de Hacienda, el Secretario del DDEC o el Comisionado de Seguros le requiera, o que "
            "radique los mismos después de la fecha de su vencimiento. La radicación de un informe incompleto se "
            "considerará como no radicado, si la agencia concernida notifica al Negocio Exento de alguna omisión "
            "en el informe requerido y dicho Negocio Exento no somete la información que falta dentro de quince "
            "(15) días de haber sido notificada, o no justifica razonablemente la falta de la misma.\n"
            "[Nota: Versión sustituida en 2026.]"
        ),
        effective_year=2019,
        superseded_year=2026,
        source_url="https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF/60-2019.pdf",
        cross_references=["Ley 60-2019 Sec. 2021.01"],
        breadcrumbs="Ley 60-2019 > Subtítulo F > Capítulo 2 > Sección 6020.10",
    ),
    StatuteRecord(
        id="pr-ley60-6070-55",
        jurisdiction="PR",
        corpus="LEY60",
        citation="Ley 60-2019 Sec. 6070.55",
        section="6070.55",
        title="Definiciones aplicables a las Zonas de Oportunidad",
        text_en=(
            "English summary; the official text is in Spanish. Defines the terms used in the Opportunity Zones "
            "chapter of the Incentives Code (a), including eligible activity, Committee, decree, Fund (an entity "
            "that is an “Opportunity Zone Fund” under Section 1400Z-2(d)(1) of the federal Internal Revenue "
            "Code), eligible investment, eligible business, exempt business and eligible zone (an area of Puerto "
            "Rico designated as an opportunity zone under Section 1400Z-1(b)(3) of the federal Internal Revenue "
            "Code and designated an eligible zone by the Committee). Other terms have the meaning they have in "
            "the Internal Revenue Code and its regulations (b)."
        ),
        text_es=(
            "(a) Para los fines de este Capítulo los siguientes términos y frases tendrán el significado que a "
            "continuación se expresa:\n"
            "(1) “Actividad Elegible” — significa un Proyecto Prioritario en Zonas de Oportunidad.\n"
            "(2) “Chief Financial Officer” — significa el principal oficial de finanzas públicas creado en virtud "
            "de la Orden Ejecutiva OE-2013-007.\n"
            "(3) “Chief Investment Officer” — significa el principal oficial de inversiones creado en virtud de "
            "la Orden Ejecutiva OE-2018-035.\n"
            "(4) “Código de Rentas Internas” — significa la Ley 1-2011, según enmendada, conocida como el “Código "
            "de Rentas Internas para un Nuevo Puerto Rico”, o cualquier ley sucesora.\n"
            "(5) “Código de Rentas Internas Federal” — significa el Código de Rentas Internas Federal de 1986, "
            "Pub. Law 99-514, 68A Stat. 3, según enmendado, o cualquier ley posterior que la sustituya.\n"
            "(6) “Comisionado” — significa el Comisionado de Instituciones Financieras creado por la Ley Núm. 4 "
            "de 11 de octubre de 1985, según enmendada.\n"
            "(7) “Comité” — significa el “Comité de Proyectos Prioritarios en Zonas de Oportunidad”, adscrito a "
            "la Oficina del Gobernador, con las facultades dispuestas en este Capítulo, y compuesto por el "
            "Principal Oficial Financiero (Chief Financial Officer), quien lo presidirá, el Principal Oficial de "
            "Inversiones (Chief Investment Officer), el Director Ejecutivo de la Autoridad de la Asesoría "
            "Financiera y Agencia Fiscal de Puerto Rico, el Director Ejecutivo de la Autoridad para las Alianzas "
            "Público Privadas de Puerto Rico, el Secretario del Departamento de Desarrollo Económico y Comercio, "
            "un miembro nombrado por el Senado de Puerto Rico y un miembro nombrado por la Cámara de "
            "Representantes de Puerto Rico, o sus respectivos designados de tiempo en tiempo quienes tendrán los "
            "mismos derechos y obligaciones de los funcionarios que representan, incluyendo la asistencia a las "
            "reuniones por aquellos medios y/o tecnología que sea autorizada y, por tanto, utilizada por el "
            "Comité para llevar a cabo las mismas. A solicitud del Presidente del Comité, el Gobernador podrá "
            "nombrar otros miembros al Comité para atender solicitudes específicas, conforme a la naturaleza del "
            "negocio solicitante. El Comité adoptará las normas, procedimientos y reglamentos que sean necesarios "
            "para los propósitos de las funciones asignadas en este Capítulo sin sujeción a las disposiciones de "
            "la Ley 38-2017, conocida como la “Ley de Procedimiento Administrativo Uniforme del Gobierno de "
            "Puerto Rico”. Disponiéndose que cinco (5) de los siete (7) o una mayoría de los miembros del Comité "
            "constituirán quorum para las reuniones de dicho Comité. No obstante, solo se reconocerá quorum si un "
            "representante de los Cuerpos Legislativos participa de las reuniones y así se certifica, salvo que "
            "existan incomparecencias inexcusadas a dos o más reuniones consecutivas, en cuyo caso se certificará "
            "el quorum con los demás cinco (5) miembros presentes.\n"
            "(8) “Decreto” — significa el decreto emitido de conformidad con la Sección 6070.60 de este Código, "
            "mediante la cual se notifica la aprobación de una solicitud debidamente radicada y las condiciones "
            "impuestas a la misma.\n"
            "(9) “Crédito por inversión elegible” — significa los créditos según el apartado (i) del 6070.56 de "
            "este Código.\n"
            "(10) “Director” — significa el Director de la Oficina de Exención Contributiva Industrial.\n"
            "(11) “Distribución de ingresos neto de desarrollo de zonas de oportunidad” — significa cualquier "
            "distribución de dividendos o ganancias de un negocio exento o una distribución en liquidación de un "
            "negocio exento de las utilidades y beneficios provenientes de los ingresos netos de zonas de "
            "oportunidad.\n"
            "(12) “Entidad ignorada” — significa una entidad que es tratada como un “disregarded entity” para "
            "propósitos del Código de Rentas Internas Federal.\n"
            "(13) “Fondo” — significa una entidad que cumple con los siguientes requisitos:\n"
            "(A) no más tarde de la fecha de comienzo de operaciones en conformidad con el apartado (e) de la "
            "Sección 6070.59 de este Código y durante el periodo de designación establecido en la Sección "
            "1400Z-1(f) del Código de Rentas Internas Federal, la entidad es un “Opportunity Zone Fund” conforme "
            "a la Sección 1400Z-2(d) (1) del Código de Rentas Internas Federal;\n"
            "(B) durante el periodo que comienza al día siguiente de la expiración de la designación establecida "
            "en la sección 1400Z-1(f) del Código de Rentas Internas Federal y que termina el día de la expiración "
            "del decreto, la entidad de otro modo calificaría como un “Opportunity Zone Fund” conforme a la "
            "Sección 1400Z-2(d)(1) del Código de Rentas Federal, si dicha designación todavía estuviera vigente.\n"
            "(14) “Gobernador” — significa el Gobernador de Puerto Rico.\n"
            "(15) “Ingreso neto de zonas de oportunidad” — significa el ingreso neto de un negocio exento "
            "generado en la operación de una actividad elegible, según determinado bajo el Código de Rentas "
            "Internas.\n"
            "(16) “Inversión elegible” — significa el efectivo que haya sido aportado a:\n"
            "(A) un Fondo que es un negocio exento a cambio de acciones emitidas por el Fondo (si el Fondo es una "
            "corporación) o a cambio de una participación en el Fondo (si el Fondo es una sociedad, compañía de "
            "responsabilidad limitada, sociedad o empresa en común);\n"
            "(B) un Fondo a cambio de acciones emitidas por el Fondo (si el Fondo es una corporación) o a cambio "
            "de una participación en el Fondo (si el Fondo es una sociedad, compañía de responsabilidad limitada, "
            "sociedad o empresa en común) y el Fondo invierte dichas aportaciones al capital de una corporación "
            "que es un negocio exento o una sociedad que es un negocio exento a cambio de acciones emitidas por "
            "la corporación o a cambio de una participación en la sociedad (si la sociedad es una compañía de "
            "responsabilidad limitada, sociedad o empresa en común) y dicha inversión por el Fondo es en "
            "cumplimiento con la Sección 1400Z-2(d)(2) del Código de Rentas Internas Federal; o\n"
            "(C) a una corporación que es un negocio exento a cambio de acciones emitidas por la corporación, o a "
            "una compañía de responsabilidad limitada, sociedad o empresa en común que es un negocio exento a "
            "cambio de una participación en una compañía de responsabilidad limitada, sociedad o empresa en "
            "común, siempre y cuando un Fondo invierta en dicha corporación o compañía de responsabilidad "
            "limitada, sociedad o empresa en común y dicha inversión por el Fondo es en cumplimiento con la "
            "Sección 1400Z-2(d)(2) del Código de Rentas Internas Federal.\n"
            "(17) “Inversionista” — significa cualquier persona natural o jurídica que haga una inversión "
            "elegible, según definida en el párrafo (16) de este apartado.\n"
            "(18) “Ley de Patentes Municipales” — significa la Ley Núm. 113 de 10 de julio de 1974, según "
            "enmendada.\n"
            "(19) “Negocio” — significa una corporación, sociedad, compañía de responsabilidad, sociedad o "
            "empresa en común.\n"
            "(20) “Negocio elegible” — significa un negocio que cumple con los siguientes requisitos:\n"
            "(A) la actividad del negocio es llevada a cabo en su totalidad en una zona elegible;\n"
            "(B) la actividad llevada a cabo por el negocio no es elegible para una concesión de exención "
            "contributiva bajo Leyes de Incentivos Anteriores, tales como: la Ley 20-2012, según enmendada, "
            "conocida como la “Ley para Fomentar la Exportación de Servicios”, la Ley 73-2008, según enmendada, "
            "conocida como la “Ley de Incentivos Económicos para el Desarrollo de Puerto Rico”, la Ley 74-2010, "
            "según enmendada, conocida como “Ley de Desarrollo Turístico de Puerto Rico de 2010”, la Ley 83-2010, "
            "según enmendada, conocida como la “Ley de Incentivos de Energía Verde de Puerto Rico”, la Ley "
            "27-2011, según enmendada, conocida como la “Ley de Incentivos Económicos para la Industria Fílmica "
            "de Puerto Rico” o cualquier ley sucesora o análoga a las anteriormente descritas;\n"
            "(C) el negocio es llevado a cabo por el Fondo o una entidad en la cual invierte el Fondo bajo la "
            "Sección 1400Z-2(d)(2) del Código de Rentas Internas Federal;\n"
            "(D) la actividad llevada a cabo por el negocio es un Proyecto Prioritario en zona de oportunidad.\n"
            "(21) “Negocio exento” — significa un negocio elegible al que se le ha concedido un decreto de "
            "exención contributiva bajo este Capítulo.\n"
            "(22) “Oficina de Exención” — significa la Oficina de Exención Contributiva Industrial.\n"
            "(23) “Proyecto Prioritario en Zonas de Oportunidad” — significa una industria o negocio u otra "
            "actividad de producción de ingresos que aportará a la diversificación, recuperación o transformación "
            "social y económica de la comunidad de la zona elegible.\n"
            "(24) “Proyecto Prioritario Residencial Elegible” — significa un Proyecto Prioritario en zonas de "
            "oportunidad que tenga un componente importante de vivienda.\n"
            "(25) “Secretario” — significa el Secretario o la Secretaria del Departamento de Hacienda de Puerto "
            "Rico.\n"
            "(26) “Secretario de Desarrollo Económico” — significa el Secretario o la Secretaria del Departamento "
            "de Desarrollo Económico y Comercio.\n"
            "(27) “Zona elegible” — significa un área de Puerto Rico que ha sido designada como zona de "
            "oportunidad bajo la Sección 1400Z-1(b) (3) del Código de Rentas Internas Federal, según delineadas "
            "en el mapa mantenido por el Departamento del Tesoro Federal y que ha sido designada como una zona "
            "elegible por el Comité mediante reglamentación, carta circular, determinación administrativa o "
            "boletín informativo de carácter general.\n"
            "(b) Definiciones de otros términos. — Los demás términos aplicables a las Zonas de Oportunidad que "
            "se emplean en este Capítulo, a menos que específicamente se disponga lo contrario, tendrán el mismo "
            "significado que tienen en el Código de Rentas Internas y sus reglamentos."
        ),
        effective_year=2019,
        source_url="https://bvirtualogp.pr.gov/ogp/Bvirtual/leyesreferencia/PDF/60-2019.pdf",
        cross_references=["Ley 60-2019 Sec. 6070.56", "Ley 60-2019 Sec. 6070.59"],
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
        id="us-irs-pub-17",
        category="irs-publication",
        doc_number="IRS Pub. 17 (2025)",
        doc_date="2026-01-13",
        title=(
            "Your Federal Income Tax (For Individuals) / Su impuesto federal sobre los ingresos (para "
            "individuos)"
        ),
        summary=(
            "IRS general guide for preparing 2025 individual Form 1040 returns: filing requirements, "
            "income, adjustments, standard deduction, and credits. / Guía general del IRS para preparar "
            "las planillas individuales del Formulario 1040 de 2025: requisitos de radicación, "
            "ingresos, ajustes, deducción estándar y créditos."
        ),
        source_url="https://www.irs.gov/pub/irs-pdf/p17.pdf",
    ),
    PublicationRecord(
        id="us-irs-pub-501",
        category="irs-publication",
        doc_number="IRS Pub. 501 (2025)",
        doc_date="2026-02-04",
        title=(
            "Dependents, Standard Deduction, and Filing Information / Dependientes, deducción estándar "
            "e información sobre la radicación"
        ),
        summary=(
            "Explains who must file a 2025 return, filing status, who qualifies as a dependent, and the "
            "standard deduction amounts. / Explica quién debe radicar la planilla de 2025, el estado "
            "civil para efectos de la planilla, quién califica como dependiente y las cantidades de la "
            "deducción estándar."
        ),
        source_url="https://www.irs.gov/pub/irs-pdf/p501.pdf",
    ),
    PublicationRecord(
        id="us-irs-pub-505",
        category="irs-publication",
        doc_number="IRS Pub. 505 (2026)",
        doc_date="2026-03-31",
        title=("Tax Withholding and Estimated Tax / Retención de impuestos e impuesto estimado"),
        summary=(
            "Explains federal income tax withholding on wages and other income and how to figure and "
            "pay estimated tax for 2026. / Explica la retención del impuesto federal sobre salarios y "
            "otros ingresos, y cómo calcular y pagar el impuesto estimado de 2026."
        ),
        source_url="https://www.irs.gov/pub/irs-pdf/p505.pdf",
    ),
    PublicationRecord(
        id="us-irs-pub-570",
        category="irs-publication",
        doc_number="IRS Pub. 570 (2025)",
        doc_date="2026-02-26",
        title=(
            "Tax Guide for Individuals With Income From U.S. Territories / Guía tributaria para "
            "individuos con ingresos de territorios de EE.UU."
        ),
        summary=(
            "Federal filing rules for 2025 for individuals with income from Puerto Rico and the other "
            "U.S. territories, including bona fide residence and source rules. / Reglas federales de "
            "radicación para 2025 aplicables a individuos con ingresos de Puerto Rico y los demás "
            "territorios de EE.UU., incluidas la residencia de buena fe y las reglas de fuente."
        ),
        source_url="https://www.irs.gov/pub/irs-pdf/p570.pdf",
    ),
    PublicationRecord(
        id="us-irs-pub-1321",
        category="irs-publication",
        doc_number="IRS Pub. 1321 (Rev. October 2025)",
        doc_date="2025-10-01",
        title=(
            "Special Instructions for Bona Fide Residents of Puerto Rico Who Must File a U.S. "
            "Individual Income Tax Return / Instrucciones especiales para residentes de buena fe de "
            "Puerto Rico que deben radicar una planilla federal individual"
        ),
        summary=(
            "How bona fide residents of Puerto Rico who exclude Puerto Rico-source income adjust Form "
            "1040 or Form 1040-SR. / Cómo los residentes de buena fe de Puerto Rico que excluyen "
            "ingresos de fuentes de Puerto Rico ajustan el Formulario 1040 o 1040-SR."
        ),
        source_url="https://www.irs.gov/pub/irs-pdf/p1321.pdf",
    ),
    PublicationRecord(
        id="us-irs-pub-54",
        category="irs-publication",
        doc_number="IRS Pub. 54 (Rev. December 2025)",
        doc_date="2026-01-08",
        title=(
            "Tax Guide for U.S. Citizens and Resident Aliens Abroad / Guía tributaria para ciudadanos y "
            "extranjeros residentes de EE.UU. en el extranjero"
        ),
        summary=(
            "Federal filing rules for U.S. citizens and resident aliens living abroad, including the "
            "foreign earned income exclusion and housing exclusion or deduction. / Reglas federales de "
            "radicación para ciudadanos y extranjeros residentes de EE.UU. que viven en el extranjero, "
            "incluidas la exclusión de ingreso devengado en el extranjero y la exclusión o deducción "
            "por vivienda."
        ),
        source_url="https://www.irs.gov/pub/irs-pdf/p54.pdf",
    ),
    PublicationRecord(
        id="pr-cc-ri-21-10",
        category="carta-circular",
        doc_number="CC RI 21-10",
        doc_date="2021-03-25",
        title="Centros de Orientación y Preparación de Planillas",
        summary=(
            "A tenor con lo anterior, el Departamento emite esta Carta Circular con el propósito de establecer el "
            "procedimiento para la preparación y radicación electrónica de la Planilla del año contributivo 2020 "
            "en los Centros."
        ),
        source_url="https://hacienda.pr.gov/sites/default/files/publicaciones/2021/03/cc_ri_21-10_centros_de_orientacion_y_preparacion_de_planillas_2020_signed_25-mar-2021.pdf",
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
        title=(
            "Integración del Impuesto sobre Ventas y Uso Municipal en el Sistema Unificado de Rentas Internas y "
            "nueva versión de la Planilla Mensual de Impuesto sobre Ventas y Uso y sobre Importaciones (Modelo SC "
            "2915)"
        ),
        summary=(
            "A tenor con lo anterior, el Departamento emite esta Carta Circular con el propósito de establecer: "
            "(i) el nuevo procedimiento en SURI relacionado a la integración del IVU Municipal, incluyendo la "
            "radicación de la Planilla Mensual de IVU y el pago del IVU Estatal y del IVU Municipal "
            "correspondiente y el cambio en la remisión del IVU Municipal en las transacciones de venta de "
            "partidas tributables no atribuibles a una localidad en Puerto Rico, a negocios multinivel y sobre "
            "partidas importadas para uso, entre otros; (ii) el uso de la nueva versión de la Planilla Mensual de "
            "IVU para incluir las partidas tributables y exentas que deben informarse para propósitos del IVU "
            "Municipal; (iii) el tratamiento aplicable a los municipios que no participan del proceso de "
            "radicación consolidada en SURI, y (iv) la fecha de vigencia de estos nuevos requisitos."
        ),
        source_url="https://hacienda.pr.gov/sites/default/files/publicaciones/2026/06/cc_ri_26-13_-_integracion_cofim_y_nueva_planilla_mensual_ivu_signed.pdf",
    ),
    PublicationRecord(
        id="pr-ddec-cc-2026-001",
        category="carta-circular",
        doc_number="CC DDEC 2026-001",
        doc_date="2026-01-12",
        title=(
            "Donaciones requeridas a Individuos Residentes Inversionistas y evidencia de adquisición de propiedad "
            "residencial"
        ),
        summary=(
            "En consideración a lo anterior, el Director de la OIN, en consulta con la Comisión Especial, con la "
            "Oficina de Gerencia y Presupuesto y la Comisión para Combatir la Pobreza Infantil y la Desigualdad Social "
            "en Puerto Rico adscrita al Departamento de la Familia, emite la presente Carta Circular con el propósito "
            "de establecer las instancias en las que se reconocerán ciertas donaciones y aclarar la interpretación "
            "administrativa con relación a la evidencia de adquisición de propiedad inmueble residencial en Puerto "
            "Rico."
        ),
        source_url="https://docs.pr.gov/files/DDEC/Aviso%20Pu%CC%81blico/Carta%20Circular%20DDEC%20N%C3%9AM.%202026-001%20(OIN)%20Donaciones%20IRI%20-%201.12.26.pdf",
    ),
    PublicationRecord(
        id="pr-cc-ri-26-01",
        category="carta-circular",
        doc_number="CC RI 26-01",
        doc_date="2026-02-05",
        title=(
            "Radicación del Formulario Modelo SC 2644, Solicitud de Prórroga para Rendir la Planilla de Contribución "
            "sobre Ingresos, para el año contributivo 2025"
        ),
        summary=(
            "A tenor con lo anterior, el Departamento de Hacienda (“Departamento”) emite esta Carta Circular con el "
            "propósito de establecer el procedimiento de radicación electrónica de la Solicitud de Prórroga Automática "
            "para el año contributivo 2025."
        ),
        source_url="https://hacienda.pr.gov/sites/default/files/publicaciones/2026/02/cc_ri_26-01_radicacion_del_formulario_modelo_sc_2644_del_ano_contributivo_2025_signed.pdf",
    ),
    PublicationRecord(
        id="pr-cc-ri-26-02",
        category="carta-circular",
        doc_number="CC RI 26-02",
        doc_date="2026-02-05",
        title=(
            "Radicación de la Planilla de Contribución sobre Ingresos de Individuos correspondiente al año contributivo "
            "2025"
        ),
        summary=(
            "A tenor con lo anterior, el Departamento de Hacienda (“Departamento”) emite esta Carta Circular con el "
            "propósito de establecer el procedimiento de radicación electrónica de la Planilla de Individuos para el "
            "año contributivo 2025."
        ),
        source_url="https://hacienda.pr.gov/sites/default/files/publicaciones/2026/02/cc_ri_26-02_radicacion_electronica_planillas_de_individuos_2025_signed.pdf",
    ),
    PublicationRecord(
        id="pr-da-26-01",
        category="determinacion-administrativa",
        doc_number="DA 26-01 / OA DDEC 2026-01",
        doc_date="2026-02-05",
        title=(
            "Requisito de radicar electrónicamente el Informe de Cumplimiento Anual de las Secciones 6020.10 y 6070.66 "
            "del Código de Incentivos de Puerto Rico, según enmendado, junto con la planilla de contribución sobre "
            "ingresos correspondiente"
        ),
        summary=(
            "A tales efectos, el Departamento de Hacienda (“Departamento”) junto con el DDEC emiten esta Determinación "
            "Administrativa con el propósito de posponer la entrada en vigor de la radicación electrónica del IANE a "
            "incluirse con la planilla de contribución sobre ingresos correspondiente a los años contributivos que "
            "comiencen después del 31 de diciembre de 2024."
        ),
        source_url="https://hacienda.pr.gov/sites/default/files/publicaciones/2026/02/da_26-01_posposicion_de_la_integracion_del_informe_anual_exento_ley_65-2025_signed.pdf",
    ),
    PublicationRecord(
        id="pr-bi-26-01",
        category="boletin-informativo",
        doc_number="BI RI 26-01",
        doc_date="2026-02-17",
        title=(
            "Notificación de ajuste por inflación al Crédito por Trabajo para el año contributivo 2025"
        ),
        summary=(
            "A tenor con lo anterior, el Departamento emite este Boletín Informativo, conforme a las disposiciones de "
            "la Sección 1052.01(i) del Código, con el propósito de notificar los nuevos umbrales de ingreso bruto "
            "ganando y el Crédito máximo a considerarse en el Anejo CT Individuo – Crédito por Trabajo de la Planilla "
            "de Contribución sobre Ingresos de Individuos (“Planilla”) correspondiente al año contributivo 2025."
        ),
        source_url="https://hacienda.pr.gov/sites/default/files/publicaciones/2026/02/bi_ri_26-01_ajuste_por_inflacion_al_credito_por_trabajo_para_el_ano_2025_signed.pdf",
    ),
    PublicationRecord(
        id="pr-cc-ri-26-03",
        category="carta-circular",
        doc_number="CC RI 26-03",
        doc_date="2026-02-19",
        title=(
            "Límites aplicables para el año 2026 a planes de retiro cualificados bajo la Sección 1081.01(a) del Código "
            "de Rentas Internas de Puerto Rico de 2011, según enmendado"
        ),
        summary=(
            "A tales efectos, el Departamento de Hacienda (“Departamento”) emite esta Carta Circular con el propósito "
            "de establecer los límites aplicables a los planes de retiro cualificados bajo la Sección 1081.01(a) del "
            "Código para el año contributivo 2026."
        ),
        source_url="https://hacienda.pr.gov/sites/default/files/publicaciones/2026/02/cc_ri_26-03_limites_planes_de_pensiones_2026_rev._ley_179-2025_signed_19-feb-2026.pdf",
    ),
    PublicationRecord(
        id="pr-cc-ri-26-04",
        category="carta-circular",
        doc_number="CC RI 26-04",
        doc_date="2026-02-27",
        title=(
            "Radicación de la Planilla Informativa sobre Ingresos de Entidades Conducto, Formulario 480.2(EC), para el "
            "año contributivo 2025"
        ),
        summary=(
            "A tenor con lo anterior, el Departamento emite esta Carta Circular con el propósito de establecer el "
            "procedimiento de radicación electrónica de la Planilla de Entidad Conducto para el año contributivo 2025 y "
            "de los Formularios 480.6EC que se emitan exclusivamente para dicha planilla."
        ),
        source_url="https://hacienda.pr.gov/sites/default/files/publicaciones/2026/03/cc_ri_26_04_radicacio_n_electro_nica_planillas_de_entidades_conducto.pdf",
    ),
    PublicationRecord(
        id="pr-cc-ri-26-05",
        category="carta-circular",
        doc_number="CC RI 26-05",
        doc_date="2026-02-27",
        title=("Centros de Preparación y Radicación de Planillas Electrónicas"),
        summary=(
            "A tenor con lo anterior, el Departamento emite esta Carta Circular con el propósito de establecer: (i) la "
            "fecha de comienzo de los servicios en los Centros de Preparación y Radicación de Planillas Electrónicas "
            "del año contributivo 2025 (“Centros”), y (ii) los requisitos para la preparación y radicación electrónica "
            "de la Planilla de Individuos y la Planilla Federal para reclamar el CTC, correspondientes al año "
            "contributivo 2025, en dichos Centros."
        ),
        source_url="https://hacienda.pr.gov/sites/default/files/publicaciones/2026/03/cc_ri_26_05_centros_de_preparacio_n_y_radicacio_n_de_planillas_2025.pdf",
    ),
    PublicationRecord(
        id="pr-cc-ri-26-06",
        category="carta-circular",
        doc_number="CC RI 26-06",
        doc_date="2026-03-13",
        title=(
            "Radicación de la Planilla de Contribución sobre Ingresos de Corporaciones correspondiente al año "
            "contributivo 2025"
        ),
        summary=(
            "A tenor con lo anterior, el Departamento emite esta Carta Circular con el propósito de establecer el "
            "procedimiento de radicación electrónica de la Planilla de Corporación para el año contributivo 2025."
        ),
        source_url="https://hacienda.pr.gov/sites/default/files/publicaciones/2026/03/cc_ri_26-06_radicacion_electronica_planillas_de_corporaciones_2025_signed_13-marzo-2026.pdf",
    ),
    PublicationRecord(
        id="pr-bi-26-03",
        category="boletin-informativo",
        doc_number="BI RI 26-03",
        doc_date="2026-04-15",
        title=(
            "Nueva versión del Formulario Modelo SC 2745 “Poder y Declaración de Representación”"
        ),
        summary=(
            "El Departamento emite este Boletín Informativo con el propósito de: (i) notificar la emisión de la nueva "
            "versión del Modelo SC 2745, y (ii) notificar las reglas de transición para la utilización de esta nueva "
            "versión."
        ),
        source_url="https://hacienda.pr.gov/sites/default/files/publicaciones/2026/04/bi_ri_26-03_nueva_version_del_modelo_sc_2745_signed.pdf",
    ),
    PublicationRecord(
        id="pr-cc-ri-26-07",
        category="carta-circular",
        doc_number="CC RI 26-07",
        doc_date="2026-04-17",
        title=(
            "Radicación de la Planilla Informativa de Organización Exenta de Contribución sobre Ingresos "
            "correspondiente al año contributivo 2025"
        ),
        summary=(
            "A tenor con lo anterior, el Departamento emite esta Carta Circular con el propósito de establecer el "
            "procedimiento de radicación electrónica de la Planilla de Organización Exenta para el año contributivo "
            "2025."
        ),
        source_url="https://hacienda.pr.gov/sites/default/files/publicaciones/2026/04/cc_ri_26-07_radicacion_electronica_planilla_organizacion_sin_fines_2025_signed_17-abril-2026.pdf",
    ),
    PublicationRecord(
        id="pr-da-26-02",
        category="determinacion-administrativa",
        doc_number="DA 26-02",
        doc_date="2026-04-23",
        title=(
            "Posposición de la fecha límite para pagar la contribución sobre ingresos adeudada del año contributivo "
            "2025"
        ),
        summary=(
            "A tales efectos, el Departamento de Hacienda (“Departamento”) emite esta Determinación Administrativa con "
            "el propósito de posponer la fecha límite para el pago de la contribución sobre ingresos adeudada con la "
            "Planilla de Contribución sobre Ingresos de Individuos, Formulario 482, correspondiente al año contributivo "
            "2025 (“Planilla 2025”), para los empleados del DHS afectados."
        ),
        source_url="https://hacienda.pr.gov/sites/default/files/publicaciones/2026/04/da_26-02_posposicion_fecha_limite_planilla_empleados_signed.pdf",
    ),
    PublicationRecord(
        id="pr-ddec-ib-2026-004",
        category="boletin-informativo",
        doc_number="IB DDEC 2026-004",
        doc_date="2026-04-24",
        title=(
            "Clarifications and Frequently Asked Questions Regarding Amendments Enacted under Act No. 38-2026"
        ),
        summary=(
            "This Informative Bulletin seeks to clarify the interpretation of certain amendments to the Incentives Code "
            "upon the enactment of Act No. 38-2026 on March 10, 2026, particularly on the income tax rate and "
            "alternatives applicable to individual resident investor (“IRI”) applications pending approval, "
            "applications filed on or before December 31, 2026, updated requirements for compliance with the primary "
            "residential property acquisition commitment under the tax exemption decrees, and on the prospective "
            "applicability of Circular Letter No. DDEC 2026-001 issued by the OIN on January 12, 2026 (“CC 26-001”)."
        ),
        source_url="https://docs.pr.gov/files/DDEC/Aviso%20Pu%CC%81blico/Informative%20Bulletin%20DDEC%20NO.%202026-004%20Final.pdf",
    ),
    PublicationRecord(
        id="pr-cc-ri-26-08",
        category="carta-circular",
        doc_number="CC RI 26-08",
        doc_date="2026-05-01",
        title=(
            "Periodo libre de Impuesto sobre Ventas y Uso por artículos y equipos para la temporada de huracanes"
        ),
        summary=(
            "A tenor con lo anterior, el Departamento emite esta Carta Circular con el propósito de establecer: (i) el "
            "periodo de Exención del IVU en la compra de artículos y equipos de preparación para la temporada de "
            "huracanes para el año natural 2026, y (ii) los artículos y equipos de preparación que estarán exentos del "
            "IVU durante dicho periodo."
        ),
        source_url="https://hacienda.pr.gov/sites/default/files/publicaciones/2026/05/cc_ri_26-08_exencion_anual_ivu_articulos_y_equipo_huracanes_2026_signed.pdf",
    ),
    PublicationRecord(
        id="pr-bi-26-04",
        category="boletin-informativo",
        doc_number="BI RI 26-04",
        doc_date="2026-05-08",
        title=(
            "Enlace para confirmar la cuenta de la institución financiera a ser utilizada para recibir el Alivio "
            "Contributivo 2025"
        ),
        summary=(
            "A tenor con lo anterior, el Departamento emite este Boletín Informativo para notificar a los "
            "contribuyentes que, a partir del lunes, 11 de mayo de 2026, el enlace para informar la cuenta de la "
            "institución financiera (“Enlace”) estará disponible en SURI."
        ),
        source_url="https://hacienda.pr.gov/sites/default/files/publicaciones/2026/05/bi_ri_26-04_alivio_contributivo_-_confirmacion_de_la_cuenta_de_banco_v.5.7.2026.pdf",
    ),
    PublicationRecord(
        id="pr-cc-ri-26-09",
        category="carta-circular",
        doc_number="CC RI 26-09",
        doc_date="2026-05-15",
        title=(
            "Radicación de la planilla de contribución sobre ingresos para negocios con decretos de exención "
            "contributiva (Formulario 480.3(II)) para el año contributivo 2025"
        ),
        summary=(
            "A tenor con lo anterior, el Departamento emite esta Carta Circular con el propósito de establecer el "
            "procedimiento de radicación electrónica de la Planilla de Negocio Exento para el año contributivo 2025."
        ),
        source_url="https://hacienda.pr.gov/sites/default/files/publicaciones/2026/05/cc_ri_26-09_radicacio_n_electro_nica_planillas_incentivos_2025.pdf",
    ),
    PublicationRecord(
        id="pr-cc-ri-26-10",
        category="carta-circular",
        doc_number="CC RI 26-10",
        doc_date="2026-05-20",
        title=(
            "Reglas aplicables al Cheque de Alivio Contributivo para los Trabajadores y la Clase Media para el año "
            "contributivo 2025 conforme a la Resolución Conjunta 6-2026"
        ),
        summary=(
            "A tenor con lo anterior, el Departamento emite esta Carta Circular para establecer los parámetros de "
            "elegibilidad del Alivio Contributivo y la forma en que se efectuará el pago a los individuos elegibles."
        ),
        source_url="https://hacienda.pr.gov/sites/default/files/publicaciones/2026/05/cc_ri_26-10_alivio_contributivo_2025_rc_6-2026_signed.pdf",
    ),
    PublicationRecord(
        id="pr-cc-ri-26-11",
        category="carta-circular",
        doc_number="CC RI 26-11",
        doc_date="2026-06-01",
        title=(
            "Periodo libre de Impuesto sobre Ventas y Uso por regreso a la escuela para el año fiscal 2026-2027"
        ),
        summary=(
            "El Departamento de Hacienda (“Departamento”) emite esta Carta Circular con el propósito de establecer: (i) "
            "el periodo de Exención del IVU en la compra de uniformes y materiales escolares para el año fiscal "
            "2026-2027, y (ii) los artículos que estarán exentos del IVU durante dicho periodo."
        ),
        source_url="https://hacienda.pr.gov/sites/default/files/publicaciones/2026/06/cc_ri_26-11_back_to_school_2026-2027_signed_1-junio-2026.pdf",
    ),
    PublicationRecord(
        id="pr-cc-ri-26-12",
        category="carta-circular",
        doc_number="CC RI 26-12",
        doc_date="2026-06-26",
        title=(
            "Formulario 481.1, Planilla de Crédito para Personas de 65 años o más y Crédito Compensatorio para "
            "Pensionados de Bajos Recursos, para el año contributivo 2025"
        ),
        summary=(
            "A tenor con lo anterior, el Departamento emite esta Carta Circular con el propósito de: (i) notificar la "
            "emisión de la nueva versión del Formulario 481.1 para el año contributivo 2025, y (ii) establecer el "
            "procedimiento de radicación electrónica compulsoria a través de SURI de dicho formulario."
        ),
        source_url="https://hacienda.pr.gov/sites/default/files/publicaciones/2026/06/cc_ri_26-12_formulario_481.1_seniors_-_ano_contributivo_2025_signed.pdf",
    ),
    PublicationRecord(
        id="pr-bi-26-05",
        category="boletin-informativo",
        doc_number="BI RI 26-05",
        doc_date="2026-07-14",
        title=(
            "Compilación del Reglamento Núm. 8049 del Código de Rentas Internas de Puerto Rico de 2011 y sus enmiendas "
            "en un solo documento de referencia"
        ),
        summary=(
            "A tales efectos, el Departamento emite este Boletín Informativo con el propósito de notificar que ya está "
            "disponible la Compilación de Reglamentos del Código y se puede acceder en nuestra página de internet: "
            "www.hacienda.pr.gov, en la sección Reglamentos de Hacienda bajo el Código de Rentas Internas que se "
            "encuentra ubicada en el área de Publicaciones."
        ),
        source_url="https://hacienda.pr.gov/sites/default/files/publicaciones/2026/07/bi_ri_26-05_publicacion_de_la_compilacion_del_reglamento_8049_signed_13-julio-2026.pdf",
    ),
    PublicationRecord(
        id="pr-da-23-04",
        category="determinacion-administrativa",
        doc_number="DA 23-04",
        doc_date="2023-11-28",
        title=(
            "Certain Rules and Tax Considerations Applicable to a Contractor Entering Into a Partnership Contract "
            "With a Partnering Government Entity"
        ),
        summary=(
            "The purpose of this Administrative Determination is to supplement AD 20-06 and AD 22-05 by "
            "addressing certain additional income tax considerations with respect to various matters not "
            "expressly covered in AD 20-06 or AD 22-05."
        ),
        source_url="https://hacienda.pr.gov/sites/default/files/publicaciones/2023/11/ad_23-04_app_entidades_conducto_e_ignoradas_final_nov_28_2023.pdf",
    ),
    PublicationRecord(
        id="pr-da-21-04",
        category="determinacion-administrativa",
        doc_number="DA 21-04",
        doc_date="2021-03-23",
        title=(
            "Posposición de la fecha límite para radicar la Planilla de Contribución Sobre Ingresos del Año "
            "Contributivo 2020"
        ),
        summary=(
            "El Departamento emite esta Determinación Administrativa conforme a lo dispuesto en las Secciones "
            "1061.16(f) y 1061.17(h) del Código, con el propósito de notificar la nueva fecha límite de "
            "radicación de la Planilla del año contributivo 2020 de aquellos Contribuyentes cuyo cierre de "
            "periodo anual de contabilidad sea el 31 de diciembre de 2020 (es decir, Contribuyentes que rinden su "
            "planilla a base de año natural) y su fecha original de vencimiento según el Código sea el 15 de "
            "abril de 2021."
        ),
        source_url="https://hacienda.pr.gov/sites/default/files/publicaciones/2021/03/da_21_04_posposicion_planillas_de_4_15_2020_clean_final_3_22_2021_acapites_corregidos.pdf",
    ),
    PublicationRecord(
        id="pr-bi-26-02",
        category="boletin-informativo",
        doc_number="BI RI 26-02",
        doc_date="2026-03-10",
        title=(
            "Porcentaje de participación de cada municipio para el año fiscal 2026-2027 del IVU municipal sobre "
            "las ventas despachadas por correo de negocios que no poseen local comercial en Puerto Rico"
        ),
        summary=(
            "A tenor con lo anterior, el Departamento emite este Boletín Informativo con el propósito de "
            "notificar los siguientes por cientos de distribución para el año fiscal 2026-2027 que le "
            "corresponderán a cada municipio del IVU Municipal sobre las Ventas Despachadas por Correo de "
            "comerciantes que no poseen una localidad comercial en Puerto Rico, según determinado conforme al "
            "Artículo 6080.14(a)(2)-1(b) del Reglamento."
        ),
        source_url="https://hacienda.pr.gov/sites/default/files/publicaciones/2026/03/bi_ri_26-02_ivu_municipal_-_porcentaje_de_participacion_por_municipio_ventas_por_internet_signed_10-marzo-2026.pdf",
    ),
    PublicationRecord(
        id="pr-cc-ri-25-12",
        category="carta-circular",
        doc_number="CC RI 25-12",
        doc_date="2025-05-23",
        title=(
            "Radicación de la planilla de contribución sobre ingresos para negocios con decretos de exención "
            "contributiva (Formulario 480.3(II)) para el año contributivo 2024"
        ),
        summary=(
            "A tenor con lo anterior, el Departamento emite esta Carta Circular con el propósito de establecer el "
            "procedimiento de radicación electrónica de la Planilla de Negocio Exento para el año contributivo "
            "2024."
        ),
        source_url="https://hacienda.pr.gov/sites/default/files/publicaciones/2025/05/cc_ri_25-12_radicacion_electronica_planillas_incentivos_2024_signed_23-mayo-2025.pdf",
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


# ---------------------------------------------------------------------------
# USLM adapter for Title 26 (Office of the Law Revision Counsel, uscode.house.gov)
# ---------------------------------------------------------------------------

USLM_NS = "{http://xml.house.gov/schemas/uslm/1.0}"
XHTML_NS = "{http://www.w3.org/1999/xhtml}"
# Only these Title 26 sections are ingested from the USLM release point.
USLM_TITLE26_ALLOWLIST: tuple[str, ...] = (
    "1",
    "24",
    "32",
    "61",
    "62",
    "63",
    "865",
    "911",
    "933",
    "937",
    "957",
    "1411",
    "6012",
    "6688",
)
_USLM_SKIP = {f"{USLM_NS}notes", f"{USLM_NS}note", f"{USLM_NS}sourceCredit"}
_USLM_BLOCKS = {
    f"{USLM_NS}{tag}"
    for tag in (
        "subsection",
        "paragraph",
        "subparagraph",
        "clause",
        "subclause",
        "item",
        "subitem",
        "chapeau",
        "content",
        "continuation",
        "p",
    )
} | {f"{XHTML_NS}tr"}
_USLM_LEVEL_TAGS = {
    f"{USLM_NS}{tag}"
    for tag in ("subsection", "paragraph", "subparagraph", "clause", "subclause", "item", "subitem")
}
_NUM_ONLY_RE = re.compile(r"^\s*(\([0-9A-Za-z]+\)\s*)+$")
_YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")


@dataclass
class UslmSection:
    """One Title 26 section parsed from a USLM release point."""

    section: str
    heading: str
    text: str
    enacted_year: int
    release_point: str


def _uslm_render(el: ET.Element, lines: list[str], buf: list[str]) -> None:
    """Append the operative text of ``el`` to ``buf``, breaking lines at block elements."""
    if el.tag in _USLM_SKIP:
        return
    block = el.tag in _USLM_BLOCKS
    if block:
        if _NUM_ONLY_RE.match("".join(buf)):
            if el.tag not in _USLM_LEVEL_TAGS:
                buf.append(" ")
        else:
            _uslm_flush(lines, buf)
    if el.tag in (f"{XHTML_NS}td", f"{XHTML_NS}th") and buf and buf[-1].strip():
        buf.append(" | ")
    if el.tag == f"{XHTML_NS}p":
        buf.append(" ")
    if el.text:
        buf.append(el.text)
    for child in el:
        _uslm_render(child, lines, buf)
        if child.tail:
            buf.append(child.tail)
    if block:
        _uslm_flush(lines, buf)


def _uslm_flush(lines: list[str], buf: list[str]) -> None:
    line = " ".join("".join(buf).split())
    if line:
        lines.append(line)
    buf.clear()


def section_text(el: ET.Element) -> str:
    """Operative statute text of a USLM section, without heading, notes or source credit."""
    lines: list[str] = []
    buf: list[str] = []
    for child in el:
        if child.tag not in (f"{USLM_NS}num", f"{USLM_NS}heading"):
            _uslm_render(child, lines, buf)
        if child.tail:
            buf.append(child.tail)
    _uslm_flush(lines, buf)
    return "\n".join(lines)


def parse_uslm_title26(
    xml_path: Path, allowlist: tuple[str, ...] = USLM_TITLE26_ALLOWLIST
) -> dict[str, UslmSection]:
    """Stream a USLM Title 26 XML file and return the allowlisted sections."""
    wanted = {f"/us/usc/t26/s{num}": num for num in allowlist}
    found: dict[str, UslmSection] = {}
    release_point = ""
    for _event, el in ET.iterparse(xml_path):
        local = el.tag.removeprefix(USLM_NS)
        if local == "docPublicationName" and el.text:
            release_point = el.text.split("@")[-1].strip()
        elif local == "section" and el.get("identifier") in wanted:
            num = wanted[el.get("identifier", "")]
            heading_el = el.find(f"{USLM_NS}heading")
            credit_el = el.find(f"{USLM_NS}sourceCredit")
            credit = "".join(credit_el.itertext()) if credit_el is not None else ""
            year = _YEAR_RE.search(credit)
            if year is None:
                raise ValueError(f"no enactment year in source credit for section {num}")
            found[num] = UslmSection(
                section=num,
                heading=" ".join("".join(heading_el.itertext()).split())
                if heading_el is not None
                else "",
                text=section_text(el),
                enacted_year=int(year.group(1)),
                release_point=release_point,
            )
            el.clear()
    missing = [num for num in allowlist if num not in found]
    if missing:
        raise ValueError(f"sections missing from USLM file: {', '.join(missing)}")
    return found


def apply_uslm_title26(
    sections: dict[str, UslmSection], catalog: list[StatuteRecord]
) -> tuple[list[StatuteRecord], int]:
    """Return a copy of ``catalog`` with allowlisted IRC rows refreshed from USLM text.

    The second value counts rows whose text or enactment year changed.
    """
    refreshed: list[StatuteRecord] = []
    changed = 0
    for rec in catalog:
        sec = sections.get(rec.section)
        if rec.jurisdiction != "US" or rec.corpus != "IRC" or sec is None:
            refreshed.append(rec)
            continue
        if sec.release_point != USLM_TITLE26_RELEASE_POINT:
            raise ValueError(
                f"release point {sec.release_point} does not match pinned "
                f"{USLM_TITLE26_RELEASE_POINT}"
            )
        if rec.text_en != sec.text or rec.effective_year != sec.enacted_year:
            changed += 1
            rec = replace(rec, text_en=sec.text, effective_year=sec.enacted_year)
        refreshed.append(rec)
    return refreshed, changed


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
        "> Puerto Rico Territorial Tax Law (Act 60-2019 and Ley 1-2011), and all 50 US States + District of Columbia.",
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
            "## Administrative Publications & Guidance",
            "",
        ]
    )
    for p in PUBLICATIONS_CATALOG:
        llms_summary.append(f"- **{p.doc_number}** ({p.doc_date}) — {p.title}: {p.summary}")

    llms_txt_path.write_text("\n".join(llms_summary), encoding="utf-8")

    # Generate llms-full.txt
    llms_full_path = output_dir / "llms-full.txt"
    llms_full_content = [
        "# Full Statutory Corpus: US Federal, Puerto Rico & Multi-State Tax Intelligence\n"
    ]
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
    uslm_xml: Path | None = None,
) -> dict[str, int]:
    """Ingest statutes and publications into DuckDB and optionally export docs."""
    logger.info(
        "Initializing ingestion pipeline (sources=%s, db_path=%s, dry_run=%s)",
        sources,
        db_path,
        dry_run,
    )

    catalog = STATUTES_CATALOG
    if uslm_xml is not None:
        catalog, refreshed = apply_uslm_title26(parse_uslm_title26(uslm_xml), catalog)
        logger.info(
            "USLM Title 26 release point %s: %d IRC rows refreshed.",
            USLM_TITLE26_RELEASE_POINT,
            refreshed,
        )

    selected_statutes: list[StatuteRecord] = []
    for s in catalog:
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
        "--uslm-xml",
        type=Path,
        default=None,
        help="Optional USLM Title 26 XML (usc26.xml) to refresh allowlisted IRC sections from.",
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
        uslm_xml=args.uslm_xml,
    )
    logger.info("Pipeline executed successfully: %s", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
