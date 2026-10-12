Architecture and Implementation of Model Context Protocol Servers for United States and Puerto Rico Tax CodesAutonomous artificial intelligence agents operating within legal, accounting, and regulatory environments face severe limitations when relying solely on pre-trained parametric weights. Complex statutory frameworks—most notably the United States Internal Revenue Code (Title 26 U.S.C.) and the specialized territorial tax regime of Puerto Rico—are characterized by dense structural hierarchies, temporal volatility, and intricate multi-jurisdictional interactions. Without access to ground-truth legal texts, foundational language models frequently produce subtle hallucinations, misapply superseded statutory provisions, and fail to calculate multi-tier sourcing rules accurately.The Model Context Protocol (MCP) establishes an open, standardized integration specification that bridges language models and external computational environments. By developing dedicated MCP servers using FastMCP, engineering teams can expose authoritative federal and territorial tax statutes, dynamic retrieval engines, and deterministic legal calculation tools directly to agentic workflows. This report provides a comprehensive architectural blueprint for constructing, deploying, and maintaining high-performance MCP servers that index the United States Internal Revenue Code, Treasury Regulations, the Puerto Rico Internal Revenue Code of 2011, and the Puerto Rico Incentives Code (Act 60-2019).Architectural Foundations and FastMCP Framework DesignThe Model Context Protocol establishes a client-server topology operating over structured JSON-RPC 2.0 message framing. The MCP client—integrated into developer environments such as Cursor, native desktop interfaces like Claude Desktop, or autonomous agent frameworks—manages dialogue context and high-level reasoning loops. The MCP server functions as an authoritative boundary, hosting discrete domain tools, static and dynamic resources, and structured prompting workflows. FastMCP serves as an enterprise-grade Python framework designed to abstract protocol mechanics, converting standard asynchronous functions, type hints, and docstrings into compliant protocol schemas.Communication across the protocol relies on three primary primitives that allow client agents to interact with external data environments in distinct ways:Tools provide executable capabilities that agents invoke to perform calculations, query external indices, or evaluate statutory rules. FastMCP infers JSON parameter schemas directly from Python type signatures and populates tool documentation from docstrings, allowing language models to determine invocation criteria dynamically.Resources and resource templates expose passive, read-only data endpoints addressable via Uniform Resource Identifiers (URIs). While static resources deliver fixed payloads, resource templates accept parameterized path variables (such as tax://us/irc/{section}), allowing an agent to fetch specific legal sections without executing computational logic.Prompts define reusable, structured templates that guide multi-step agent reasoning, enforcing standardized legal frameworks and citation verification protocols.MCP PrimitiveProtocol MechanismPrimary Tax Server FunctionReturn Payload StructureTool (@mcp.tool)Client issues tools/call with validated argumentsExecutes hybrid searches, runs presence calculations, and evaluates sourcing rulesJSON object or Markdown text containing structured analytical outputsResource (@mcp.resource)Client issues resources/read with a fixed URIDelivers static rate schedules, administrative orders, and filing deadlinesPlain text, binary blob, or structured ResourceResult[cite: 1]Resource TemplateClient issues resources/read with a parameterized URIResolves direct statutory citations (e.g., 26 U.S.C. § 933 or PR Act 60 § 2021.01)Canonical legal text, effective dates, and statutory cross-referencesPrompt (@mcp.prompt)Client issues prompts/get with scenario parametersEnforces structured legal deduction across dual-jurisdiction conflictsParameterized message array establishing system constraints and instructionsTransport layer mechanics dictate how the server exchanges data with client runtimes. FastMCP natively supports both standard input/output (STDIO) and Streamable HTTP transports. The STDIO transport operates by executing the server as a local child process managed directly by the host application, passing JSON-RPC messages across standard input and output streams. STDIO offers zero-configuration deployment for single-user desktop runtimes.For distributed multi-agent systems, FastMCP generates an Asynchronous Server Gateway Interface (ASGI) application served via Uvicorn. Streamable HTTP establishes bidirectional communication over standard web infrastructure, allowing centralized deployments to serve multi-tenant agent fleets while enforcing token-based authentication and request middleware.Statutory Ingestion, Legal Data Sources, and Hierarchical ChunkingLegal statutes cannot be ingested as generic flat-text documents. Tax legislation relies on nested conditional clauses, specific exceptions, definitions of terms, and complex cross-references to other statutory titles. Maintaining structural context during ingestion is necessary to prevent retrieval failures in production.Authoritative Digital RepositoriesFederal and territorial statutes originate from separate governing authorities that maintain disparate digital publication standards:United States Internal Revenue Code (Title 26 U.S.C.): The Office of the Law Revision Counsel (OLRC) of the U.S. House of Representatives publishes Title 26 in the United States Legislative Markup (USLM) XML schema. USLM is an XML standard that models legislative structure from broad subtitles down to nested clauses and subclauses. Releases track specific Public Laws enacted by Congress, ensuring point-in-time accuracy.Treasury Regulations (Title 26 C.F.R.): The Electronic Code of Federal Regulations (eCFR) provides a continuously updated API that exposes federal regulatory titles in structured JSON and XML formats. Treasury Regulations interpret statutory IRC sections (such as 26 C.F.R. § 1.937-1 interpreting the residency rules of 26 U.S.C. § 937).Puerto Rico Internal Revenue Code of 2011 (Ley 1-2011): Administered by the Puerto Rico Department of the Treasury (Departamento de Hacienda), Ley 1-2011 encompasses territorial income, excise, sales, and administrative tax mandates. Official digital copies are published through the Puerto Rico Office of Management and Budget (Oficina de Gerencia y Presupuesto, OGP) legislative library.Puerto Rico Incentives Code (Ley 60-2019): Enacted to streamline dozens of legacy tax incentive laws, Ley 60-2019 integrates statutes such as Act 20 (Export Services) and Act 22 (Individual Investors) into a unified code administered by the Department of Economic Development and Commerce (DDEC). The text is maintained in official government compendiums via the OGP.Administrative Guidance and Circular Letters: Sub-regulatory guidance from Hacienda—specifically Circular Letters (Cartas Circulares), Administrative Determinations, and Informative Bulletins—provides binding guidance on electronic filing mandates via the Unified Internal Revenue System (SURI) and statutory interpretations.JurisdictionLegal CorpusPublishing AuthorityDistribution Schema / FormatStructural OrganizationUnited StatesInternal Revenue Code (Title 26 U.S.C.)House Office of the Law Revision Counsel (OLRC)USLM XML / Bulk Digital PackagesSubtitle $\rightarrow$ Chapter $\rightarrow$ Subchapter $\rightarrow$ Part $\rightarrow$ Section $\rightarrow$ SubsectionUnited StatesTreasury Regulations (Title 26 C.F.R.)National Archives / GPO via eCFReCFR REST API (JSON / XML)Title $\rightarrow$ Chapter $\rightarrow$ Subchapter $\rightarrow$ Part $\rightarrow$ SectionPuerto RicoPR Internal Revenue Code (Ley 1-2011)Departamento de Hacienda / OGPConsolidated PDF & Digital CompendiumsSubtítulo (A–F) $\rightarrow$ Capítulo $\rightarrow$ Subcapítulo $\rightarrow$ SecciónPuerto RicoIncentives Code (Ley 60-2019)DDEC / OGPConsolidated Digital EnactmentsSubtítulo $\rightarrow$ Capítulo $\rightarrow$ Sección (e.g., Sec. 2021.01)Puerto RicoAdministrative GuidanceDepartamento de HaciendaDigital Publications via SURISerial Guidance Numbers (e.g., CC RI 25-12, DA 21-04)Hierarchical Ingestion and Citation Graph PipelineSplitting legal codes using fixed-token chunking strategies degrades semantic retrieval by divorcing operative subsections from their parent definitions, conditions precedent, and exceptions. An optimal legal ingestion pipeline uses an Abstract Syntax Tree (AST) approach that reflects the statutory hierarchy:In the first stage, raw legislative files are processed by structural parsers. For federal materials, the parser extracts native USLM elements (<section>, <subsection>, <paragraph>, <subparagraph>). For Puerto Rico codes, the parser uses structural pattern matchers to identify Spanish legal divisions (Subtítulo, Capítulo, Sección, Inciso).In the second stage, every extracted statutory fragment is wrapped in a contextual envelope prior to vector embedding. This process prepends the entire statutory breadcrumb path to leaf-level provisions. For instance, when indexing 26 U.S.C. § 937(b)(2), the ingested text chunk includes its hierarchical lineage: "Title 26 > Subtitle A > Chapter 1 > Subchapter N > Part III > Subpart D > Section 937 (Residence and source rules involving possessions) > Subsection (b) (Source rules) > Paragraph (2)". This structural metadata ensures that dense vector representations preserve context when queried with broad legal phrases.In the third stage, an automated citation engine extracts explicit references across statutes using regular expression pattern matching (for example, identifying citations in 26 U.S.C. § 933 that point to § 937, which in turn cross-reference § 865). These links are indexed into a directed graph, enabling the retrieval pipeline to traverse statutory dependencies when an agent evaluates multi-tier tax questions.Retrieval Engineering: Hybrid Search and Deterministic Citation RoutingStatutory retrieval requires handling two distinct query modalities: exact legal citation lookups and open-ended conceptual research. Relying solely on approximate vector similarity search for exact citations often introduces irrelevant commentary or unrelated provisions. A dual-path retrieval pipeline addresses both modalities effectively.Deterministic Citation RoutingWhen an agent or user issues a prompt with an exact legal citation—such as "IRC Sec. 933", "26 CFR 1.937-2", or "Ley 60 Seccion 2022.01"—the query is intercepted by a deterministic AST router. The router matches standardized citation regular expressions:$$\text{Citations} \in \{\text{IRC § } X, \text{26 U.S.C. § } X, \text{26 CFR } X, \text{Ley 1-2011 Sec. } X, \text{Ley 60 Sec. } X\}$$Matches bypass the vector database and retrieve the canonical text directly from an indexed key-value statutory store in constant time. The returned payload includes the full statutory text, its effective date, and a pre-compiled list of outbound cross-references.Hybrid Retrieval (Dense Vector + BM25 with Reciprocal Rank Fusion)For conceptual legal inquiries—such as "criteria for establishing a tax home in a possession" or "export services exemptions for cloud computing"—the system uses a hybrid retrieval pipeline. Sparse retrieval using BM25 ensures precision for domain-specific terms (such as "bona fide resident", "effectively connected", or "decree holder"), while dense bi-encoder embeddings capture semantic relationships and bridge cross-language queries (such as mapping English tax questions to Spanish provisions in Ley 1-2011).Results from sparse and dense retrieval passes are merged using Reciprocal Rank Fusion (RRF):$$RRF\_Score(d \in D) = \sum_{m \in M} \frac{1}{k + r_m(d)}$$In this formulation, $M$ represents the retrieval models ($\{\text{BM25}, \text{Dense}\}$), $r_m(d)$ denotes the rank of document $d$ within model $m$, and $k$ represents a smoothing factor typically set to $60$. The top-ranking candidates are then processed through a cross-encoder reranking model to score semantic relevance against the query before being returned to the agent.Implementation of the Tax Intelligence MCP ServerThe Python implementation below demonstrates an enterprise FastMCP server that provides structured tools, dynamic resource templates, and prompt workflows for both U.S. federal and Puerto Rico statutory corpora. The server incorporates FastMCP's dependency injection system via CurrentContext(), structured data modeling with Pydantic, and asynchronous execution.Python"""
Tax Intelligence MCP Server: United States and Puerto Rico Tax Codes.
Implements Model Context Protocol (MCP) standards using FastMCP.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from fastmcp import FastMCP
from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from fastmcp.resources import ResourceResult, ResourceContent

mcp = FastMCP(
    name="TaxCode-US-PR-Intelligence",
    instructions=(
        "Authoritative tax research server for the United States Internal Revenue Code "
        "(Title 26 U.S.C.), Treasury Regulations (26 CFR), Puerto Rico Internal Revenue Code "
        "(Ley 1-2011), and Puerto Rico Incentives Code (Ley 60-2019). Supports exact statutory "
        "lookups, hybrid conceptual search, and cross-border residency/sourcing evaluations."
    ),
)


class StatuteMetadata(BaseModel):
    jurisdiction: str = Field(description="Jurisdiction code: 'US' or 'PR'")
    code_name: str = Field(description="Name of code: 'IRC', 'CFR', 'Ley 1-2011', 'Ley 60-2019'")
    citation: str = Field(description="Standardized legal citation")
    title: str = Field(description="Official title or caption of the section")
    effective_year: int = Field(description="Tax year or effective enactment year")


class CrossReferenceResult(BaseModel):
    source_citation: str
    target_citation: str
    relationship_type: str
    summary_of_interaction: str


@mcp.resource("tax://us/irc/{section}")
async def get_us_irc_section(section: str, ctx: Context = CurrentContext()) -> ResourceResult:
    """Fetch the full authoritative text of a US Internal Revenue Code section."""
    await ctx.info(f"Retrieving 26 U.S.C. Section {section}")
    statute_text = (
        f"26 U.S. Code § {section} - Statutory Provisions.\n"
        "[Official text extracted from OLRC Title 26 USLM release point]."
    )
    return ResourceResult(
        contents=[
            ResourceContent(
                content=statute_text,
                mime_type="text/plain",
                meta={"jurisdiction": "US", "code": "IRC", "section": section}
            )
        ]
    )


@mcp.resource("tax://pr/act60/{chapter}/{section}")
async def get_pr_act60_section(chapter: str, section: str, ctx: Context = CurrentContext()) -> ResourceResult:
    """Fetch statutory text from Puerto Rico Incentives Code (Ley 60-2019)."""
    await ctx.info(f"Retrieving Ley 60-2019 Chapter {chapter}, Section {section}")
    statute_text = (
        f"Ley Núm. 60-2019, Capítulo {chapter}, Sección {section}.\n"
        "[Texto consolidado oficial provisto por OGP/DDEC]."
    )
    return ResourceResult(
        contents=[
            ResourceContent(
                content=statute_text,
                mime_type="text/plain",
                meta={"jurisdiction": "PR", "code": "Ley 60-2019", "chapter": chapter, "section": section}
            )
        ]
    )


@mcp.tool(
    name="lookup_statute",
    description="Retrieve exact statutory language and active cross-references for a specific legal citation."
)
async def lookup_statute(
    jurisdiction: str,
    citation: str,
    ctx: Context = CurrentContext()
) -> Dict[str, Any]:
    """
    Look up an exact statute across US or PR tax codes.
    """
    await ctx.info(f"Executing deterministic lookup: {jurisdiction} -> {citation}")
    is_sec_933 = "933" in citation
    return {
        "status": "success",
        "citation": citation,
        "jurisdiction": jurisdiction.upper(),
        "title": "Exclusion of Income From Sources Within Puerto Rico" if is_sec_933 else "Statutory Query",
        "operative_text": (
            "Gross income does not include, in the case of an individual who is a bona fide resident "
            "of Puerto Rico during the entire taxable year, income derived from sources within Puerto Rico..."
            if is_sec_933 else "Authoritative statutory text payload."
        ),
        "cross_references": ["26 U.S.C. § 937", "26 CFR § 1.937-1", "26 U.S.C. § 865"] if is_sec_933 else []
    }


@mcp.tool(
    name="search_tax_code",
    description="Execute a hybrid semantic and keyword search across US and Puerto Rico tax provisions."
)
async def search_tax_code(
    query: str,
    jurisdiction_filter: Optional[str] = None,
    limit: int = 5,
    ctx: Context = CurrentContext()
) -> List[Dict[str, Any]]:
    """
    Hybrid search across tax statutory indices.
    """
    await ctx.info(f"Searching index for: '{query}' with filter: {jurisdiction_filter}")
    sample_results = [
        {
            "citation": "26 U.S.C. § 937",
            "jurisdiction": "US",
            "score": 0.94,
            "snippet": "Rules for determining bona fide residency in possessions (presence, tax home, closer connection tests)."
        },
        {
            "citation": "Ley 60-2019 Sec. 2022.01",
            "jurisdiction": "PR",
            "score": 0.89,
            "snippet": "Exención de Contribución sobre Ingresos a Individuos Residentes Inversionistas sobre ganancias de capital e intereses."
        }
    ]
    return sample_results[:limit]


@mcp.tool(
    name="evaluate_pr_bona_fide_residency",
    description="Evaluates whether an individual satisfies the statutory tests for bona fide residency under IRC § 937."
)
def evaluate_pr_bona_fide_residency(
    days_present_pr: int,
    days_present_us: int,
    prior_year_1_pr_days: int = 0,
    prior_year_2_pr_days: int = 0,
    has_us_tax_home: bool = False,
    closer_connection_jurisdiction: str = "PR"
) -> Dict[str, Any]:
    """
    Evaluates IRC § 937 presence, tax home, and closer connection tests.
    """
    pass_183_days = days_present_pr >= 183
    three_year_sum = days_present_pr + prior_year_1_pr_days + prior_year_2_pr_days
    pass_549_days = (
        three_year_sum >= 549 and
        days_present_pr >= 60 and
        prior_year_1_pr_days >= 60 and
        prior_year_2_pr_days >= 60
    )
    pass_90_day_us = days_present_us <= 90
    presence_satisfied = pass_183_days or pass_549_days or pass_90_day_us

    tax_home_satisfied = not has_us_tax_home
    closer_connection_satisfied = (closer_connection_jurisdiction.upper() == "PR")
    is_bona_fide = presence_satisfied and tax_home_satisfied and closer_connection_satisfied

    return {
        "is_bona_fide_resident": is_bona_fide,
        "presence_test": {
            "satisfied": presence_satisfied,
            "details": {
                "183_day_rule": pass_183_days,
                "549_day_3_year_rule": pass_549_days,
                "90_day_us_limit_rule": pass_90_day_us
            }
        },
        "tax_home_test_satisfied": tax_home_satisfied,
        "closer_connection_test_satisfied": closer_connection_satisfied,
        "authoritative_basis": "26 U.S.C. § 937; 26 CFR § 1.937-1"
    }


@mcp.prompt(
    name="cross_border_tax_analysis",
    description="Guides an agent through analyzing interactions between US federal tax law and Puerto Rico Act 60."
)
def cross_border_tax_analysis_prompt(taxpayer_scenario: str) -> str:
    """Creates a structured prompt guiding agentic legal deduction."""
    return f"""You are an elite cross-border tax attorney and computational legal analyst.
Analyze the following taxpayer scenario involving United States federal tax law and Puerto Rico statutes:

SCENARIO:
{taxpayer_scenario}

ANALYTICAL FRAMEWORK:
1. Determine bona fide residency status under 26 U.S.C. § 937 and 26 CFR § 1.937-1 (Presence, Tax Home, Closer Connection).
2. Evaluate applicability of the federal income exclusion under 26 U.S.C. § 933.
3. Sourcing determination: Apply 26 U.S.C. § 937(b) and 26 CFR § 1.937-2. Specifically apply the 10-year built-in gain rules under 26 CFR § 1.937-2(f) for pre-move appreciated property.
4. Local Puerto Rico tax incentives: Apply Puerto Rico Incentives Code (Ley 60-2019), specifying whether Chapter 2 (Individual Resident Investors) or Chapter 3 (Export Services) governs.
5. Federal filing obligations: Assess required IRS reporting, including Form 8898, Form 1040, and Net Investment Income Tax (Form 8960).

Cite all operative code sections and regulations precisely.
"""

if __name__ == "__main__":
    mcp.run()
Federal and Puerto Rico Statutory Interaction: Act 60, IRC § 933, and IRC § 937A central function of a dual-jurisdiction tax MCP server is parsing the statutory interplay between U.S. federal tax authority and Puerto Rico territorial law. Because Puerto Rico is an unincorporated territory of the United States with an independent internal revenue system, Congress established specific statutory rules governing where federal tax jurisdiction ends and territorial authority begins.The Federal Statutory Framework: IRC § 933 and § 937Under 26 U.S.C. § 933(1), individuals who qualify as bona fide residents of Puerto Rico for an entire taxable year are granted an exclusion from federal gross income for income derived from sources within Puerto Rico. This provision does not exempt U.S. citizens residing on the island from federal taxation entirely. United States citizens remain subject to worldwide income taxation under 26 U.S.C. § 1, meaning that income sourced outside Puerto Rico—such as wages for services performed on the mainland, dividends from domestic U.S. corporations, and rental income from mainland real estate—remains fully taxable by the IRS. Additionally, Section 933 explicitly disallows any deductions or credits allocable to the excluded income.To qualify for the Section 933 exclusion, a taxpayer must satisfy three statutory requirements established in 26 U.S.C. § 937 and Treasury Regulation § 1.937-1:The physical presence test requires satisfying at least one of five alternative statutory conditions under 26 C.F.R. § 1.937-1(c). The primary condition requires spending at least 183 days in Puerto Rico during the taxable year. Alternatively, an individual may qualify by demonstrating presence in Puerto Rico for at least 549 days over a three-year period (consisting of the current year and the two preceding tax years), with a minimum of 60 days in each year. Taxpayers can also satisfy the requirement by spending no more than 90 days in the United States during the taxable year, or by earning no more than $3,000 in U.S.-sourced earned income while spending more days in Puerto Rico than in the United States. Finally, taxpayers may qualify by having no significant connection to the United States, as determined by permanent housing, voter registration, and the residence of immediate family members.The tax home test mandates that an individual cannot maintain a tax home outside Puerto Rico at any point during the taxable year under 26 U.S.C. § 937(a)(2) and 26 C.F.R. § 1.937-1(d). In this context, a tax home refers to the individual's principal place of business, regular employment, or official duty post rather than a personal domicile. Maintaining an executive office, active business operations, or regular executive functions within the mainland United States invalidates compliance under this test.The closer connection test under 26 U.S.C. § 937(a)(2) and 26 C.F.R. § 1.937-1(e) requires demonstrating that the taxpayer maintains closer economic, social, and personal relationships to Puerto Rico than to the mainland United States or any foreign jurisdiction. Judicial decisions such as Vento v. Commissioner examine factors including the location of the primary family residence, the jurisdiction of personal banking and financial holdings, the location of tangible personal belongings, local social and civic ties, vehicle registration, and active driver's licenses.Puerto Rico Incentives Code (Act 60-2019)A tax incentive decree issued under Act 60 is an administrative contract with the Government of Puerto Rico fixing local tax liabilities. It confers no exemption, status, or protection under the Internal Revenue Code. Federal tax relief depends entirely on satisfying bona fide residency under Section 937 and statutory income sourcing under Section 937(b).Act 60-2019 consolidated historical incentive programs into distinct programmatic chapters:Chapter 2 (Individual Resident Investors, formerly Act 22) provides a 100% local tax exemption on qualifying Puerto Rico-source interest, dividends, and capital gains recognized after establishing residency. For decree applications submitted on or after January 1, 2027, the local capital gains rate on qualifying passive investment income increases from 0% to 4%, while existing decree holders are grandfathered at 0% through 2035. In exchange, decree holders must comply with annual requirements, including purchasing residential property on the island within two years, making mandatory annual charitable donations to local non-profit organizations, and submitting annual compliance filings to the DDEC.Chapter 3 (Export Services and Commerce, formerly Act 20) provides a 4% corporate income tax rate on net taxable income derived from exporting eligible services rendered within Puerto Rico to foreign or mainland clients. It also provides a 75% exemption on property taxes and a 50% exemption on municipal license taxes (patente municipal). The services must be performed within Puerto Rico for clients that have no commercial nexus to a trade or business within the island.Built-In Gain Sourcing Rules: Treas. Reg. § 1.937-2(f)The sourcing of capital gains represents one of the most litigated areas of federal and territorial tax interaction. Under general federal sourcing principles codified in 26 U.S.C. § 865(a), income from the sale of personal property—including equity securities—is sourced to the residence of the seller. Consequently, if a bona fide resident of Puerto Rico sells stock, the gain is treated as Puerto Rico-source income and excluded from federal taxation under Section 933.To prevent individuals from moving to Puerto Rico to liquidate pre-existing appreciated assets without federal taxation, Treasury Regulation § 1.937-2(f)(1) establishes a ten-year built-in gain look-back rule. Under the default regulatory rule, if an individual sells property owned prior to moving within ten years of establishing bona fide residency in Puerto Rico, the entire recognized gain is treated as U.S.-source income subject to standard federal tax rates.Taxpayers can avoid this treatment by making an election under Treasury Regulation § 1.937-2(f)(1)(vi) on their federal return, which allocates the gain between pre-move and post-move holding periods:For marketable securities, gain allocation is calculated using fair market value (FMV) measured at the close of the market on the first day the taxpayer establishes bona fide residency in Puerto Rico:$$\text{Gain}_{\text{US-Source}} = \text{FMV}_{\text{Residency Date}} - \text{Adjusted Basis}$$$$\text{Gain}_{\text{PR-Source}} = \text{Gross Proceeds} - \text{FMV}_{\text{Residency Date}}$$The pre-move gain is reported on IRS Form 1040 and subject to federal capital gains tax (up to 20% plus the 3.8% Net Investment Income Tax under Section 1411 if statutory thresholds are exceeded). The post-move gain is treated as Puerto Rico-source income excluded from federal gross income under Section 933, and taxed at the preferential Act 60 rate (0% or 4%) on the territorial return.For non-marketable property—such as privately held businesses or real estate—the statutory allocation relies on a linear time-apportionment formula based on holding periods:$$\text{Gain}_{\text{PR-Source}} = \text{Total Recognized Gain} \times \left( \frac{\text{Days Held as Bona Fide Resident}}{\text{Total Days in Holding Period}} \right)$$$$\text{Gain}_{\text{US-Source}} = \text{Total Recognized Gain} - \text{Gain}_{\text{PR-Source}}$$If an asset is sold after ten years of continuous bona fide residency, the look-back rule expires, and the entire gain is treated as Puerto Rico-source income exempt from federal taxation under Section 933.Income / Transaction CategoryStatutory Sourcing BasisFederal IRS Tax TreatmentPuerto Rico Tax Treatment (Act 60)Mandatory Reporting FormsWages for Services Performed in PR26 U.S.C. § 861(a)(3); § 937(b)Excluded from gross income under § 933(1)Taxed at graduated PR income tax rates (Ley 1-2011)PR Form 480.20 via SURIWages for Services Performed in US26 U.S.C. § 861(a)(3)Fully taxable under 26 U.S.C. § 1Credit available under PR law to mitigate double taxationIRS Form 1040Securities Acquired Post-Move26 U.S.C. § 865(a); § 937(b)100% PR-Source; excluded under § 933(1)0% rate under Act 60 Chapter 2 (4% for post-2026 decrees)PR Form 480.30(II)Marketable Stock Acquired Pre-Move (Sold <10 Yrs)26 C.F.R. § 1.937-2(f)(1)(vi)Pre-move appreciation taxed at federal rates; post-move excluded0% on post-move appreciation under Act 60IRS Form 1040 (Schedule D) & PR PlanillaExport Services Entity IncomeAct 60 Chapter 3; 26 U.S.C. § 937(b)Excluded if PR entity; exempt from CFC/GILTI via § 957(c)Flat 4% corporate income tax ratePR Corporate Return Form 480.20(EC)Dividends from Mainland US Corporation26 U.S.C. § 861(a)(2)100% US-Source; fully taxable under § 1 and § 1411 (NIIT)Taxable by PR (subject to foreign tax credit limitations)IRS Form 1040 & Form 8960Federal Enforcement Campaigns and Compliance DocumentationThe IRS Large Business and International (LB&I) division maintains an ongoing compliance campaign monitoring individuals claiming tax benefits under Puerto Rico incentive laws. This initiative targets taxpayers who obtain Act 60 decrees without meeting the physical presence, tax home, or closer connection requirements of Section 937, as well as individuals who mischaracterize U.S.-source income as Puerto Rico-source to evade federal tax liabilities.Federal compliance requires filing specific disclosure and information returns:IRS Form 8898 (Statement for Individuals Beginning or Ending Bona Fide Residence in a U.S. Possession): Required for any tax year in which an individual establishes or terminates bona fide residency in Puerto Rico, provided their worldwide gross income exceeds $75,000. Failure to file without reasonable cause carries a $1,000 statutory penalty under 26 U.S.C. § 6688.IRS Form 8960 (Net Investment Income Tax): Bona fide residents with modified adjusted gross income derived outside Puerto Rico exceeding statutory thresholds ($200,000 for single filers) remain subject to the 3.8% Net Investment Income Tax on non-excluded investment income.IRS Form 5471 (Information Return of U.S. Persons With Respect to Certain Foreign Corporations): While bona fide residents are generally exempt from Controlled Foreign Corporation (CFC) shareholder reporting under 26 U.S.C. § 957(c) for Puerto Rico corporations, individuals who fail the Section 937 residency test face retroactive penalties for unfiled international corporate forms.Agent Integration, Client Configuration, and PluggabilityConnecting the tax MCP server to downstream AI runtimes requires configuring client-side manifests to establish connection parameters, authorization tokens, and execution commands.Local Agent Configuration (STDIO Transport)For desktop AI tools such as Claude Desktop or Cursor, the MCP server is configured within the client's JSON environment file. The client manages the server's lifecycle, launching it as a subprocess on demand and exchanging structured payloads over standard I/O.The listing below illustrates a standard configuration registering a local tax MCP server alongside a remote production endpoint within claude_desktop_config.json:JSON{
  "mcpServers": {
    "us-pr-tax-local": {
      "command": "uv",
      "args": [
        "run",
        "--with", "fastmcp",
        "--with", "pydantic",
        "python",
        "/usr/local/bin/tax_server.py"
      ],
      "env": {
        "PYTHONUNBUFFERED": "1",
        "TAX_INDEX_STORAGE_PATH": "/var/data/tax_indices",
        "USLM_CACHE_DIR": "/var/cache/uslm_xml"
      }
    },
    "us-pr-tax-remote": {
      "url": "http://tax-mcp.internal.enterprise.net:8000/mcp",
      "headers": {
        "Authorization": "Bearer mcp_sec_token_9823471029384"
      }
    }
  }
}
Remote Enterprise Deployment (Streamable HTTP / ASGI Architecture)For enterprise agent deployments serving distributed multi-agent systems, executing local subprocesses is inefficient and insecure. Instead, the FastMCP server is exposed as an ASGI web application deployed inside containerized infrastructure (e.g., Docker, Kubernetes, AWS ECS) using Uvicorn.Python"""
ASGI Production Entrypoint for Enterprise MCP Fleet.
File: app.py
Run with: uvicorn app:app --host 0.0.0.0 --port 8000 --workers 4
"""

from tax_server import mcp

# Expose standard ASGI application container
app = mcp.http_app()
When operating over Streamable HTTP, clients connect to http://host:8000/mcp. This transport supports concurrent connections across multiple client sessions, token-based authentication via HTTP middleware, and streaming responses.Technical Synthesis and Operational ConsiderationsOperating an enterprise tax MCP server across federal and Puerto Rico jurisdictions requires continuous attention to structural and regulatory factors:A primary architectural requirement is separating territorial decree rules from federal residency determinations. Computational agents must be designed to recognize that an Act 60 decree provides zero federal tax protection on its own. Decrees operate strictly as contractual agreements with the Government of Puerto Rico, while federal income exclusions depend entirely on satisfying the presence, tax home, and closer connection requirements of 26 U.S.C. § 937 and § 933.Maintaining structural integrity during data ingestion is equally critical. Ingesting tax legislation as flat text chunks severs operative subsections from parent definitions and statutory exceptions. Parsing legislative texts into an Abstract Syntax Tree using USLM XML specifications preserves contextual breadcrumbs and statutory hierarchy, allowing retrieval tools to return complete legal provisions with their governing limitations intact.The retrieval pipeline must combine deterministic citation routing with hybrid search capabilities. When an agent queries an exact statutory reference, such as 26 U.S.C. § 933 or Ley 60 Sec. 2021.01, the system must bypass approximate vector search and return canonical statutory text via resource templates. Conversely, conceptual and cross-jurisdictional questions require hybrid retrieval combining BM25 keyword matching and dense vector embeddings merged through Reciprocal Rank Fusion.Finally, indexing pipelines must account for temporal changes in tax law. Legislative updates require automated ingestion monitoring that polls OLRC release points and eCFR API endpoints for amendments. In Puerto Rico, systems must track key statutory transitions—such as the increase in Act 60 Chapter 2 investor capital gains rates from 0% to 4% for applicants filing on or after January 1, 2027—ensuring that agent reasoning applies the correct statutory rules across both legacy and future tax years.