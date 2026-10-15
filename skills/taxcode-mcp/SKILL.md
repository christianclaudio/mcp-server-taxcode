---
name: taxcode-mcp
description: Operational playbooks, statutory lookup patterns, and compliance workflows for the Tax Code MCP Server (US Federal, Puerto Rico Act 60, and 50 US States + DC).
---

# Tax Code MCP Server (`mcp-server-taxcode`) Agent Skill

This skill provides operational playbooks, safety constraints, and client recipes for AI agents interfacing with this MCP server.

---

## 🎯 Core Agent Recipes & Playbooks

### 1. Statutory Research & Full-Text Search
- **Search Tax Code**: Use `statutes_search_tax_code(query="convenience of employer", jurisdiction="NY")` to locate relevant statutory provisions, regulatory citations, and enacted language.
- **Point-in-Time Statutory Lookup**: Use `statutes_lookup_statute(citation="26 U.S.C. § 933", tax_year=2024)` to retrieve historical or current statutory text with bilingual summaries (English and Spanish).
- **Administrative Publications**: Use `statutes_list_publications(jurisdiction="PR")` or `jurisdiction="GA"` to retrieve binding circular letters, revenue bulletins, and administrative determination numbers.

### 2. Cross-Border & Multi-State Compliance Workflows
- **Puerto Rico Bona Fide Residency (IRC § 937)**: Use `compliance_evaluate_pr_bona_fide_residency(presence_days_pr=184, presence_days_us=30, tax_home_pr=True, closer_connection_pr=True)` to assess compliance with the physical presence tests (183-day, 549-day 3-year, 90-day US), tax home requirement, and closer connection standard.
- **Built-In Gain Sourcing (Act 60 / IRC § 937)**: Use `compliance_calculate_built_in_gain_sourcing(cost_basis=100000, value_at_move=500000, sale_price=800000, holding_period_years=5)` to compute mandatory allocation between pre-move built-in gains and post-move Puerto Rico exempt gains.
- **Multi-State Residency & Tax Liabilities**: Use `compliance_evaluate_state_residency(state="NY", days_in_state=190, permanent_abode=True, taxable_income=250000, filing_status="single", tax_year=2024)` to evaluate statutory residency (e.g. 183-day rule + permanent place of abode), domicile audit risk flags, and calculate estimated state tax liability across all 50 US states and District of Columbia.
- **Georgia Residency (Specialized Engine)**: Use `compliance_evaluate_georgia_residency(months_in_georgia=8, legal_domicile=True, taxable_income=100000, filing_status="single", tax_year=2025)` for Georgia-specific 183-day/month presence calculations and flat-rate transitions.

---

## 🛠️ Tool Suite Reference

The server exposes exactly **8 lean, read-only MCP tools**, all annotated with `readOnlyHint=True`:

| Tool | Purpose | Annotations | Key Parameters |
| :--- | :--- | :--- | :--- |
| `system_get_health_status` | Check server and database engine health | `readOnlyHint=True` | None |
| `statutes_lookup_statute` | Point-in-time statutory lookup by citation | `readOnlyHint=True` | `citation`, `jurisdiction`, `tax_year` |
| `statutes_search_tax_code` | Full-text statutory search across catalog | `readOnlyHint=True` | `query`, `jurisdiction`, `limit` |
| `statutes_list_publications` | List administrative circulars and bulletins | `readOnlyHint=True` | `jurisdiction`, `limit` |
| `compliance_evaluate_pr_bona_fide_residency` | IRC § 937 Bona Fide Residency test engine | `readOnlyHint=True` | `presence_days_pr`, `presence_days_us`, `tax_home_pr`, `closer_connection_pr` |
| `compliance_calculate_built_in_gain_sourcing`| Section 937 / Act 60 capital gains allocation | `readOnlyHint=True` | `cost_basis`, `value_at_move`, `sale_price`, `holding_period_years` |
| `compliance_evaluate_georgia_residency` | Georgia O.C.G.A. Title 48 residency & tax liability | `readOnlyHint=True` | `months_in_georgia`, `legal_domicile`, `taxable_income`, `filing_status`, `tax_year` |
| `compliance_evaluate_state_residency` | 50-State + DC statutory residency & tax engine | `readOnlyHint=True` | `state`, `days_in_state`, `permanent_abode`, `taxable_income`, `filing_status`, `tax_year` |

---

## ⚡ Agent Operating Guidelines

1. **Strict Read-Only Server**:
   All 8 tools are read-only (`readOnlyHint=True`). The server performs no mutations, deletions, or external writes. When `TAXCODE_MCP_READONLY=1` or `--profile readonly` is active, all real inspection tools remain fully available.
2. **Profiles**:
   Select the profile that fits the job:
   - **Domain-mount profiles**: `full` (all domains), `system` (`system_*`), `statutes` (`statutes_*`), `compliance` (`compliance_*`), `readonly` (all read-only tools).
   - **Job-shaped allowlist profiles**: `researcher` (`statutes_lookup_statute`, `statutes_search_tax_code`, `statutes_list_publications`), `advisor` (`compliance_evaluate_pr_bona_fide_residency`, `compliance_calculate_built_in_gain_sourcing`, `compliance_evaluate_state_residency`).
3. **Statutory Integrity & Temporal Versioning**:
   Always pass `tax_year` when querying statutes or evaluating tax liabilities to guarantee point-in-time accuracy against historical amendments or scheduled rate changes (e.g., Indiana flat rate 2024–2027+, Louisiana Act 11 standard deduction indexing, North Dakota zero brackets).
4. **Non-Binding Guidance Disclaimer**:
   Computational models and statutory summaries returned by this server are for informational and research purposes only and do not constitute formal legal or tax opinions. Official statutory and administrative sources in their enacted language control.
