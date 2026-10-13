<!-- mcp-name: io.github.christianclaudio/taxcode -->

# 📦 mcp-server-taxcode

[![CI](https://github.com/christianclaudio/mcp-server-taxcode/actions/workflows/ci.yml/badge.svg)](https://github.com/christianclaudio/mcp-server-taxcode/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen.svg)](https://github.com/christianclaudio/mcp-server-taxcode)
[![CodeRabbit Reviews](https://img.shields.io/coderabbit/prs/github/christianclaudio/mcp-server-taxcode?labelColor=171717&color=FF570A&label=CodeRabbit+Reviews)](https://coderabbit.ai)

> **Enterprise-grade Model Context Protocol (MCP) server for Federal, Territorial, and Multi-State Tax Law.**  
> Reference statutory catalog covering United States Federal (IRC/CFR), Puerto Rico (Act 60/Ley 1), and state tax corridors (GA, CA, NY, FL, TX, WA, MA, NJ, IL) with computational residency and capital gains models.

> [!WARNING]
> **NOT TAX OR LEGAL ADVICE / NO CONSTITUYE ASESORAMIENTO CONTRIBUTIVO NI LEGAL**  
> **EN:** This software and its tools provide computational models, heuristic checks, and unofficial summaries for informational and research purposes only. Nothing returned by this server constitutes formal legal or tax advice, an official legal opinion, or audit representation. Unofficial translations and summaries are non-binding; official statutory and administrative sources in their enacted language control.  
> **ES:** Este software y sus herramientas proporcionan modelos computacionales, verificaciones heurísticas y resúmenes no oficiales únicamente para fines informativos y de investigación. Ninguna respuesta emitida por este servidor constituye asesoramiento legal o contributivo formal, una opinión legal oficial ni representación ante auditorías. Las traducciones y resúmenes no oficiales no son vinculantes; los textos estatutarios y administrativos oficiales promulgados en su idioma original prevalecen.

---

## 🏗️ Architecture: FastMCP 4 Server Composition

```mermaid
graph TD
    Client["AI Agent / MCP Host<br>(Claude, Cursor, Antigravity, Snowflake Cortex)"]
    Gateway["FastMCP Root Gateway<br>(stdio / streamable-http)"]
    ParentMW["Parent Middleware<br>(Audit Logging, Timing, Secret Redaction, ReadOnly Gate)"]
    SysSub["System Sub-server (mount: 'system')<br>• system_get_health_status<br>• taxcode://system/reference/capabilities"]
    StatSub["Statutes Sub-server (mount: 'statutes')<br>• statutes_lookup_statute<br>• statutes_search_tax_code<br>• statutes_list_publications"]
    CompSub["Compliance Sub-server (mount: 'compliance')<br>• compliance_evaluate_pr_bona_fide_residency<br>• compliance_calculate_built_in_gain_sourcing<br>• compliance_evaluate_georgia_residency<br>• compliance_evaluate_state_residency"]
    DuckDB["DuckDB In-Memory & File Engine<br>(51 Statutes, 7 Publications, Full-Text Search, Temporal Versions)"]

    Client <-->|"JSON-RPC (tools/list, tools/call)"| Gateway
    Gateway --> ParentMW
    ParentMW --> SysSub
    ParentMW --> StatSub
    ParentMW --> CompSub
    StatSub <-->|"Point-in-Time & Full-Text Queries"| DuckDB
    CompSub <-->|"Deterministic Statutory Calculations"| DuckDB
```

---

## 🚀 Key Features

* **Authoritative Multi-Jurisdiction Statutory Catalog**:
  * **Federal (`US`)**: IRC § 933, § 937, § 865, 26 CFR § 1.937-1/2.
  * **Puerto Rico (`PR`)**: Act 60-2019 (Resident Individual Investors & Export Services), Ley 1-2011, Act 22-2012 predecessor versions.
  * **Key State Corridors**: Georgia (O.C.G.A. Title 48), California (RTC § 17014, § 17041, 18 CCR § 17014), New York (Tax Law § 605, § 601, Convenience Rule § 132.18, 5 Primary Domicile Factors § 105.20), Florida (Const. Art. VII § 5, § 222.17 Domicile, § 196.031 Homestead), Texas (Const. art. VIII § 24-a, Franchise Tax § 171.001), Washington (RCW 82.87 Capital Gains Excise Tax), Massachusetts (M.G.L. c. 62 § 4 & Fair Share Surtax), New Jersey (N.J.S.A. 54A & Reciprocal Convenience Rule), Illinois (35 ILCS 5 Flat Tax).
* **Temporal Statutory Versioning**: Point-in-time statutory lookup with `superseded_year` filtering and descending historical ordering (`tax_year` parameter).
* **Deterministic Compliance Engines**:
  * `evaluate_pr_bona_fide_residency`: Evaluates IRC § 937 presence (183-day, 549-day 3-year, 90-day US), tax home, and closer connection tests.
  * `calculate_built_in_gain_sourcing`: Allocates Section 937 / Act 60 capital gains across pre-move and post-move holding periods.
  * `evaluate_state_residency`: Multi-state statutory presence day-counter, permanent place of abode (PPA), domicile audit risk flags, and tax liability computation across 9 states.
* **Profiles & Opt-in ToolSearch**:
  * Domain-mount profiles: `full`, `system`, `statutes`, `compliance`, `readonly`.
  * Job-shaped allowlist profiles: `researcher` (statutes + circulars), `advisor` (cross-border residency + BIG calculations).
* **Zero Tool Bloat**: Exactly 8 lean, read-only MCP tools exposed with explicit MCP 2.0 annotations (`readOnlyHint=True`).

---

## ⚙️ Configuration

| Variable | CLI Flag | Default | Description |
| :--- | :--- | :--- | :--- |
| `TAXCODE_API_KEY` | — | `""` | Service API token for authentication |
| `TAXCODE_BASE_URL` | — | `https://api.example.com` | Upstream REST API base URL |
| `TAXCODE_MCP_READONLY` | — | `0` | Restrict server execution strictly to safe read-only inspection tools |
| `TAXCODE_MCP_PROFILE` | `--profile` | `full` | Server profile: `full`, `system`, `statutes`, `compliance`, `readonly`, `researcher`, `advisor` |
| `TAXCODE_MCP_ENABLE_TOOL_SEARCH` | `--enable-tool-search` | `0` | Replace flat catalog with dynamic search meta-tools (`profile=full` only) |
| `TAXCODE_MCP_ENABLE_CODE_MODE` | `--enable-code-mode` | `0` | Experimental Code Mode on `full` (requires `code-mode` extra) |
| `TAXCODE_MCP_AUTH_TOKEN` | — | `""` | Shared bearer token for HTTP transports (`streamable-http`, `sse`) |
| `TAXCODE_MCP_ALLOW_UNAUTHENTICATED_BIND` | — | `0` | Set to `1` (or `true`/`yes`/`on`) to let an HTTP transport bind a host other than `127.0.0.1`, `::1` or `localhost` with no token. Without it, such a bind exits at startup |

### HTTP transports and authentication

Stdio is the default and needs no token. The HTTP transports follow the MCP guidance that a local server binds only to localhost and authenticates every connection:

* On `127.0.0.1`, `::1` or `localhost` (the `--host` default is `127.0.0.1`), HTTP runs with or without `TAXCODE_MCP_AUTH_TOKEN`; without it, a warning says requests are not authenticated.
* On any other host (`0.0.0.0`, `::`, a LAN address), the server refuses to start unless `TAXCODE_MCP_AUTH_TOKEN` is set, or `TAXCODE_MCP_ALLOW_UNAUTHENTICATED_BIND=1` accepts an unauthenticated bind (logged as a warning), for example behind a gateway that authenticates for you.
* With a token set, every HTTP request needs `Authorization: Bearer <token>`; a missing or wrong token gets HTTP 401.
* In the default (stateful) HTTP mode, a session idle for 30 minutes expires: its next request gets HTTP 404 and the client must start a new session. Change this with FastMCP's `session_idle_timeout` (on `http_app()`) or `FASTMCP_HTTP_SESSION_IDLE_TIMEOUT` (seconds, or `none` to never expire); see the [FastMCP 4.1.0 release](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.1.0) and [#5229](https://github.com/PrefectHQ/fastmcp/pull/5229).

```bash
# Local HTTP, loopback only
taxcode-mcp --transport streamable-http

# Public HTTP with a token
TAXCODE_MCP_AUTH_TOKEN="$(openssl rand -hex 32)" \
  taxcode-mcp --transport streamable-http --host 0.0.0.0 --allowed-host mcp.example.com
```

### Docker

The image's default command is stdio, so it works as a stdio server out of the box:

```bash
docker run -i --rm -e TAXCODE_API_KEY=your-api-key ghcr.io/christianclaudio/<name>
```

To serve Streamable HTTP from the container, bind `0.0.0.0` inside it and pass a token:

```bash
docker run --rm -p 8000:8000 \
  -e TAXCODE_API_KEY=your-api-key \
  -e TAXCODE_MCP_AUTH_TOKEN=your-long-random-token \
  ghcr.io/christianclaudio/<name> \
  --transport streamable-http --host 0.0.0.0 --port 8000 --allowed-host mcp.example.com
```


---

## 🔌 Integration Guides for AI Assistants & IDEs

This server works out-of-the-box with all major AI assistants, IDEs, and CLI tools via standard `stdio` or `streamable-http`.

The template is not published to PyPI, so these configs run its `taxcode-mcp` console script from a local clone with `uv run --directory`. Replace `/absolute/path/to/mcp-server-taxcode` with the path of your clone. After `uv tool install .` (or `pip install .`) puts both console scripts, `taxcode-mcp` and `mcp-server-taxcode` (the same server), on your `PATH`, `"command": "taxcode-mcp"` with no `args` works too.

<details open>
<summary><b>🧡 Claude Desktop & Claude Code</b></summary>

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "taxcode": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/mcp-server-taxcode", "taxcode-mcp"],
      "env": {
        "TAXCODE_API_KEY": "your-api-key"
      }
    }
  }
}
```

For **Claude Code CLI**:
```bash
claude mcp add taxcode -- uv run --directory /absolute/path/to/mcp-server-taxcode taxcode-mcp
```
</details>

<details>
<summary><b>♊ Google Antigravity & Gemini CLI</b></summary>

Add to `.agents/mcp_config.json` (or global `~/.gemini/config/mcp_config.json`):

```json
{
  "mcpServers": {
    "taxcode": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/mcp-server-taxcode", "taxcode-mcp"],
      "env": {
        "TAXCODE_API_KEY": "your-api-key"
      }
    }
  }
}
```
</details>

<details>
<summary><b>❄️ Snowflake Cortex Agent</b></summary>

Add to `~/.snowflake/cortex/mcp.json`:

```json
{
  "mcpServers": {
    "taxcode": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/mcp-server-taxcode", "taxcode-mcp"]
    }
  }
}
```
</details>

<details>
<summary><b>💻 Cursor & Windsurf</b></summary>

Add to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "taxcode": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/mcp-server-taxcode", "taxcode-mcp"]
    }
  }
}
```
</details>

<details>
<summary><b>⚡ VS Code (Cline, Roo Code, GitHub Copilot Agent Mode)</b></summary>

Add to `cline_mcp_settings.json`:

```json
{
  "mcpServers": {
    "taxcode": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/mcp-server-taxcode", "taxcode-mcp"]
    }
  }
}
```
</details>

---

## 📚 Canonical Documentation & Live Doc MCPs

When developing, hardening, or extending MCP servers, consult the official framework and protocol references:

* **FastMCP 4 Framework Reference**: [`https://gofastmcp.com/llms.txt`](https://gofastmcp.com/llms.txt) — Server composition (`mount`), hierarchical middleware, transforms (`ToolTransform`, `ToolSearch`), lifespans, and in-memory test clients.
* **Model Context Protocol Specification**: [`https://modelcontextprotocol.io/llms.txt`](https://modelcontextprotocol.io/llms.txt) — Official Spec (2026-07-28), wire-level JSON-RPC schemas, annotations, and transport framing.

### Live Documentation MCP Endpoints (SSE / Streamable HTTP)
Connect your AI coding agent directly to live documentation servers:
* **FastMCP Documentation Server**: `https://gofastmcp.com/mcp` (Tools: `search_fast_mcp`, `query_docs_filesystem_fast_mcp`, `submit_feedback`)
* **Anthropic MCP Documentation Server**: `https://modelcontextprotocol.io/mcp` (Tools: `search_model_context_protocol`, `query_docs_filesystem_model_context_protocol`, `submit_feedback`)

```json
{
  "mcpServers": {
    "fastmcp-docs": { "type": "sse", "url": "https://gofastmcp.com/mcp" },
    "mcp-official-docs": { "type": "sse", "url": "https://modelcontextprotocol.io/mcp" }
  }
}
```

---

## 🏃 Local Development & Verification

```bash
# 1. Install dependencies from the lockfile
uv sync --locked --extra dev

# 2. Run unit tests with 100% coverage requirement
uv run pytest

# 3. Static type checks & linting
uv run mypy --strict src/
uv run ruff check .
uv run ruff format --check .

# 4. Tool contract, protocol & drift assertions
uv run python scripts/check_tool_contract.py
uv run python scripts/check_openapi_drift.py
uv run pytest tests/test_protocol.py
uv run pytest tests/test_e2e_live.py -m e2e   # on demand; default CI uses -m 'not e2e'
./scripts/check_conformance.sh

# 5. AI code review gate (CodeRabbit)
coderabbit review --agent --uncommitted
```

---

## 📄 License

Licensed under the **Apache License 2.0**. See [`LICENSE`](LICENSE) for details.
