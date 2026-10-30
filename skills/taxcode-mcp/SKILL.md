---
name: taxcode-mcp
description: Operational playbooks, statutory lookup patterns, and compliance workflows for the Tax Code MCP Server (US Federal, Puerto Rico Act 60, and 50 US States + DC).
---

# Tax Code MCP Server (`mcp-server-taxcode`) Agent Skill

This skill provides operational playbooks, safety constraints, and client recipes for AI agents interfacing with this MCP server.

---

## 🎯 Core Agent Recipes & Playbooks

### 1. Statutory Research & Full-Text Search
- **Search Tax Code**: Use `statutes_search_tax_code(query="convenience of employer", jurisdiction="NY")` to locate relevant statutory provisions and regulatory citations. Each result is a short `snippet` around the first matched term plus its `id`, citation, title, jurisdiction, year, validity and content labels, not the full text.
- **Full Text of a Search Result**: Pass the result's `id` to `statutes_lookup_statute(citation_or_section="us-irc-933")` to read the full statutory text (the search response's `full_text` field names the tool and argument). Long text comes in pages of up to 8,000 characters per text field: while `data.next_cursor` is not null, call `statutes_lookup_statute` again with `page_cursor` set to it and join the pages in order.
- **Statute Resource**: Reading `taxcode://statutes/{jurisdiction}/{section*}` (for example `taxcode://statutes/US/933`, or `taxcode://statutes/IL/5/201` when the section id contains `/`) returns the same 8,000-character pages. A page that is not the last ends with `Next page: taxcode://statutes/<jurisdiction>/<section>?page_cursor=...`; read that URI for the next page. Percent-encoded section ids such as `5%2F201` also work.
- **Paging Lists**: `statutes_search_tax_code` and `statutes_list_publications` clamp `limit` to 1..25 items per call (`limit_applied` shows the limit used). While `next_offset` is not null, call again with `offset` set to it.
- **Point-in-Time Statutory Lookup**: Use `statutes_lookup_statute(citation_or_section="26 U.S.C. § 933", tax_year=2024)` to retrieve historical or current statutory text with bilingual summaries (English and Spanish).
- **Administrative Publications**: Use `statutes_list_publications(category="carta-circular", since_date="2023-01-01")` to retrieve binding circular letters, revenue bulletins, and administrative determination numbers.

### 2. Cross-Border & Multi-State Compliance Workflows
- **Puerto Rico Bona Fide Residency (IRC § 937)**: Use `compliance_evaluate_pr_bona_fide_residency(days_present_pr=184, days_present_us=30, has_us_tax_home=False, closer_connection_jurisdiction="PR")` to assess compliance with the physical presence tests (183-day, 549-day 3-year, 90-day US), tax home requirement, and closer connection standard.
- **Built-In Gain Sourcing (Act 60 / IRC § 937)**: Use `compliance_calculate_built_in_gain_sourcing(is_marketable=True, total_gain=700000, fmv_on_residency_date=500000, adjusted_basis=100000, years_since_residency=5)` to compute mandatory allocation between pre-move built-in gains and post-move Puerto Rico exempt gains.
- **Multi-State Residency & Tax Liabilities**: Use `compliance_evaluate_state_residency(jurisdiction="NY", days_in_state=190, maintains_permanent_abode=True, federal_agi=250000, filing_status="single", tax_year=2024)` to evaluate statutory residency (e.g. 183-day rule + permanent place of abode), domicile audit risk flags, and calculate estimated state tax liability across all 50 US states and District of Columbia.
- **Georgia Residency (Specialized Engine)**: Use `compliance_evaluate_georgia_residency(days_present_ga=240, is_domiciled_in_ga=True, federal_agi=100000, filing_status="single", tax_year=2025)` for Georgia-specific 183-day presence calculations and flat-rate transitions.

---

## 🛠️ Tool Suite Reference

The server exposes exactly **8 lean, read-only MCP tools**, all annotated with `readOnlyHint=True`:

| Tool | Purpose | Annotations | Key Parameters |
| :--- | :--- | :--- | :--- |
| `system_get_health_status` | Check server and database engine health | `readOnlyHint=True` | None |
| `statutes_lookup_statute` | Point-in-time statutory lookup by citation or search result `id` (full text) | `readOnlyHint=True` | `citation_or_section`, `jurisdiction`, `tax_year` |
| `statutes_search_tax_code` | Full-text statutory search across catalog; returns snippets and ids | `readOnlyHint=True` | `query`, `jurisdiction`, `limit` |
| `statutes_list_publications` | List administrative circulars and bulletins | `readOnlyHint=True` | `category`, `since_date`, `limit` |
| `compliance_evaluate_pr_bona_fide_residency` | IRC § 937 Bona Fide Residency test engine | `readOnlyHint=True` | `days_present_pr`, `days_present_us`, `has_us_tax_home`, `closer_connection_jurisdiction` |
| `compliance_calculate_built_in_gain_sourcing`| Section 937 / Act 60 capital gains allocation | `readOnlyHint=True` | `is_marketable`, `total_gain`, `fmv_on_residency_date`, `adjusted_basis`, `years_since_residency` |
| `compliance_evaluate_georgia_residency` | Georgia O.C.G.A. Title 48 residency & tax liability | `readOnlyHint=True` | `days_present_ga`, `is_domiciled_in_ga`, `federal_agi`, `filing_status`, `tax_year` |
| `compliance_evaluate_state_residency` | 50-State + DC statutory residency & tax engine | `readOnlyHint=True` | `jurisdiction`, `days_in_state`, `maintains_permanent_abode`, `federal_agi`, `filing_status`, `tax_year` |

---

## ⚡ Agent Operating Guidelines

1. **Strict Read-Only Server**:
   All 8 tools are read-only (`readOnlyHint=True`). The server performs no mutations, deletions, or external writes. When `TAXCODE_MCP_READONLY=1` or `--profile readonly` is active, all real inspection tools remain fully available.
2. **Profiles**:
   Select the profile that fits the job:
   - **Domain-mount profiles**: `full` (all domains), `system` (`system_*`), `statutes` (`statutes_*`), `compliance` (`compliance_*`), `readonly` (all read-only tools).
   - **Job-shaped allowlist profiles**: `researcher` (`system_get_health_status`, `statutes_lookup_statute`, `statutes_search_tax_code`, `statutes_list_publications`), `advisor` (`system_get_health_status`, `statutes_lookup_statute`, `compliance_evaluate_pr_bona_fide_residency`, `compliance_calculate_built_in_gain_sourcing`, `compliance_evaluate_georgia_residency`, `compliance_evaluate_state_residency`).
3. **Statutory Integrity & Temporal Versioning**:
   Always pass `tax_year` when querying statutes or evaluating tax liabilities to guarantee point-in-time accuracy against historical amendments or scheduled rate changes (e.g., Indiana flat rate 2024–2027+, Louisiana Act 11 standard deduction indexing, North Dakota zero brackets).
4. **Non-Binding Guidance Disclaimer**:
   Computational models and statutory summaries returned by this server are for informational and research purposes only and do not constitute formal legal or tax opinions. Official statutory and administrative sources in their enacted language control.
