# AGENTS.md

Instructions for AI coding agents working on `mcp-server-taxcode` or developing MCP tools for tax codes and statutory regulations.

---

## 🎯 Project Overview

`mcp-server-taxcode` is an enterprise Python Model Context Protocol (MCP) server exposing tax code navigation, statutory lookups, publication searches, rate schedules, and cross-border compliance calculators with strict safety gates, offline test isolation, and FastMCP 4 composition.

---

## 📚 Canonical Documentation & Live Doc MCPs

Always consult the official machine-readable indexes and live documentation MCP servers before designing or changing tools:

| Resource | `llms.txt` index | Live doc MCP (SSE / Streamable HTTP) |
| :--- | :--- | :--- |
| **FastMCP 4 Framework** | [`https://gofastmcp.com/llms.txt`](https://gofastmcp.com/llms.txt) | `https://gofastmcp.com/mcp` (`search_fast_mcp`, `query_docs_filesystem_fast_mcp`) |
| **Model Context Protocol (Official)** | [`https://modelcontextprotocol.io/llms.txt`](https://modelcontextprotocol.io/llms.txt) | `https://modelcontextprotocol.io/mcp` (`search_model_context_protocol`, `query_docs_filesystem_model_context_protocol`) |

Host config JSON for these doc servers lives in `README.md`.

---

## 🗺️ Key Paths

- `src/taxcode_mcp/server.py` — root gateway, `create_server` factory, parent middleware, `mount()`. Streamable HTTP and legacy SSE pass `host_origin_protection=True` with `allowed_hosts` / optional `allowed_origins`.
- `src/taxcode_mcp/auth.py` — `SharedTokenVerifier` for `TAXCODE_MCP_AUTH_TOKEN` and the HTTP bind policy; attached at server build so `main()`, `fastmcp run`, and `http_app()` all enforce the token.
- `src/taxcode_mcp/tools/` — domain modules (`system.py`, `statutes.py`, `compliance.py`, `canada.py`), re-exported from `tools/__init__.py` and mounted as `system_server`, `statutes_server`, `compliance_server`.
- `src/taxcode_mcp/data/` — source of truth for statutes and publications: `jurisdictions.json`, `statutes/<code>.json`, `publications/<code>.json`, `manifest/<code>.json`, and `rates/<code>.json` (rate tables per `RATE_TABLE_MODELS`). `catalog.py` validates them, `db.py` seeds DuckDB, `disclaimers.py` holds compliance disclaimers, and `tools/compliance.py` reads rates from `rates/`. Layout & row rules: `CONTRIBUTING.md`.
- `scripts/build_catalog.py` — canonicalizes data files and generates `docs/<code>/*.md`, `docs/<code>/llms-full.txt`, `docs/llms.txt`, and `docs/llms-full.txt`. `--check` (CI & `tests/test_catalog.py`) fails on schema, rate-table, manifest, or doc drift. Never hand-edit generated `docs/`.
- `src/taxcode_mcp/paging.py` — hard 32,000-char JSON cap and paging: `statutes_lookup_statute` pages via `page_cursor` / `next_cursor`, search/publications clamp `limit` to `1..25` (`offset` / `next_offset`), and `taxcode://statutes/{jurisdiction}/{section*}{?page_cursor}` returns 8,000-char pages ending non-final pages with the next URI (`statutes` mount supplies the `statutes/` path segment).
- `src/taxcode_mcp/client.py`, `ssrf.py`, `errors.py`, `middleware.py`, `config.py`, `profiles.py` — async HTTP client (`TemplateClient`), SSRF/DNS guard, secret redaction, parent/read-only middleware, Pydantic settings, and profiles.
- `scripts/check_tool_contract.py` — source of truth for expected tool set and annotations (never hard-code counts elsewhere).
- `scripts/check_openapi_drift.py`, `scripts/check_conformance.sh` + `conformance-baseline.yml` — drift (`# drift-ignore: <reason>` for deliberate exceptions) and conformance.
- `scripts/release_notes.py`, `scripts/check_version.py` — release notes from squash commits since last `v*` tag; wheel version check after `uv build` (rejects `0.0.0` / `0.0.1.devN`).
- `tests/` — 100% offline unit, catalog, composition, and protocol tests; `test_live.py` is opt-in (`-m live`).
- `.github/workflows/` — `ci.yml`, `dependabot-automerge.yml`, `release.yml`.
- `server.json`, `Dockerfile`, `pyproject.toml`, `fastmcp.json`.

---

## 🧩 FastMCP 4 Server Composition & Hierarchical Layering

Tool naming follows **hybrid A+B**: domain sub-servers mount on `root` with matching namespaces (`system_*`, `statutes_*`, `compliance_*`), never a product-wide `taxcode_*` stamp on every tool ([MCP tool names](https://modelcontextprotocol.io/specification/2026-07-28/server/tools#tool-names); [FastMCP namespacing](https://gofastmcp.com/servers/composition#namespacing)).

### Composition rules
- **Domain sub-servers & middleware:** `server.py` mounts `system_server`, `statutes_server`, and `compliance_server`; `ParentAuditMiddleware` (timing, audit, redaction) and `ReadOnlyGateMiddleware` wrap requests.
- **Profiles (`src/taxcode_mcp/profiles.py`):** selected via `--profile` / `TAXCODE_MCP_PROFILE`:
  - **Domain-mount profiles:** `full`, `system`, `statutes`, `compliance`, `readonly`.
  - **Job allowlist profiles:** `researcher`, `advisor` (explicit tool allowlists; unused tools belong in `FULL_ONLY_TOOLS`). Unknown profiles or missing tool names raise `ValueError` at build time. Allowlists filter tools only (prompts/resources stay) and keep a flat `tools/list` (never attach Tool Search or Code Mode; log warning and skip).
- **Read-only = `readOnlyHint`:** single source of truth (`--profile readonly` / `TAXCODE_MCP_READONLY=1`). `ReadOnlyToolFilter` keeps only `readOnlyHint=True` tools; `ReadOnlyGateMiddleware` refuses all other real tools (including calls unwrapped through Tool Search's `call_tool`). Missing `readOnlyHint` or non-`True` fails closed; unknown tool names keep `Unknown tool`. No name/tag lists.
- **Tool Search / Code Mode (`full` only, mutually exclusive):** opt-in via `--enable-tool-search` / `TAXCODE_MCP_ENABLE_TOOL_SEARCH` (`regex` default or `bm25`) or experimental `--enable-code-mode` / `TAXCODE_MCP_ENABLE_CODE_MODE`. Code Mode needs `mcp-server-taxcode[code-mode]` (`pydantic_monty`); check `pydantic_monty` directly and skip with a warning if missing (`tests/conftest.py` aborts CI without it; tests use `pytest.importorskip("pydantic_monty")`). Default stays a flat `tools/list`.

---

## ⚡ Canonical Workflow: Building Tools from API / `llms.txt`

Repeat in exact order:
1. **Client method (`client.py`):** add typed `async def` method (no bare `dict`/`Any` when a schema is known), escape URL path params with `quote(str(param), safe="")`, and call `await self._request("METHOD", path, params=..., json=...)`.
2. **Tool handler (`tools/<domain>.py`):** register with `@<domain>_server.tool()`, use domain-namespaced naming (no `taxcode_` prefix), write explicit docstrings (purpose, args, return shape), require `confirm: bool = False` on state-mutating operations, and re-export in `tools/__init__.py` and `server.py`. Raise redacted `ToolError` via `tool_error(exc)` or `SafetyViolationError` (`isError: true`) — assign `failure = tool_error(exc)` inside `except` and `raise failure from None` **after** `except` ends so `__context__` stays clean. Never return `{"status": "error", ...}` as a normal result (`isError: false`). Batch tools process all items, return partial results, and raise redacted `batch_failed` only if all items fail.
3. **Annotations & gating:** set `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint` on every tool; support `TAXCODE_MCP_READONLY=1` (`--profile readonly`) and `TAXCODE_MCP_ALLOW_BULK_DESTRUCTIVE=1`.
4. **Offline tests & contract sync (`tests/`):** mock HTTP via `httpx.MockTransport` or `AsyncMock` (**zero live calls in default suite**), update `scripts/check_tool_contract.py`, map new tools in `profiles.py` (`PROFILES` or `FULL_ONLY_TOOLS`), run `scripts/build_catalog.py --check` when data/docs change, and maintain **100.0%** statement coverage.

---

## 🛡️ Non-Negotiable Safety & Security Rules

1. **Destructive Confirmation Gate:** mutating tools must default `confirm: bool = False` and return a dry-run preview without side effects when `False`.
2. **Secret Redaction:** `redact_secrets` / `redact_message` / `redact_payload` in `errors.py` mask tokens, passwords, API keys, client secrets, private keys, and PEM blocks with fixed `[REDACTED]`. Tool errors (`tool_error`, `tool_failure`) must raise `from None` outside `except`; `ParentAuditMiddleware` logs redacted messages. Unknown tools stay FastMCP 4.0.11's tool result (`isError: true`), not JSON-RPC `-32602`.
3. **Multi-Stage Non-Root Containers (`Dockerfile`):**
   - Multi-stage `python:3.14-slim-trixie AS builder` → runtime pinned by digest, non-root `USER mcp`, venv `/opt/venv`, stdio default, `LABEL io.modelcontextprotocol.server.name` matching `server.json` `name`.
   - Trust-store floor (FastMCP Oct 2026 notice): both stages upgrade OS `ca-certificates` and enforce `ARG CA_CERTIFICATES_FLOOR` (before any pip run in builder), bootstrap pip `26.2.2` (`docker/pip-bootstrap.txt`, `--require-hashes`), and require `certifi>=2026.10.19`. Never lower `CA_CERTIFICATES_FLOOR` via `--build-arg`.
   - Reproducible lock install: builder installs uv (`docker/uv-bootstrap.txt`, `--require-hashes`, outside venv), exports `uv export --locked --no-dev --no-emit-project --format requirements.txt` with hashes, installs build backend (`docker/build-backend.txt`, `--require-hashes --only-binary=:all:` outside venv), and runs `PYTHONPATH=/tmp/build-backend pip install --no-deps --no-build-isolation .` (never `pip install .`). Regenerate `docker/build-backend.txt` after `uv lock` via `uv run python scripts/gen_build_backend.py` (never hand-edit; Dependabot excludes it). Enforced by `tests/test_dockerfile.py` & `tests/test_build_backend_lock.py`.
4. **Registry Metadata (`server.json`):** root `description` **≤ 100 chars** (HTTP 422 otherwise); default transport `stdio`, `"runtimeHint": "uvx"`.
5. **HTTP Auth & Bind Policy:** `TAXCODE_MCP_AUTH_TOKEN` (`.strip()`; non-empty enables `SharedTokenVerifier` across `main()`, `fastmcp run`, `http_app()`). Non-localhost HTTP binds without a token refuse to start unless `TAXCODE_MCP_ALLOW_UNAUTHENTICATED_BIND=1`.
6. **SSRF & DNS Rebinding Defense:** `SSRFSafeAsyncTransport` (used by client, `check_openapi_drift.py --spec-url`, and `ingest_statutes.py --check-upstream`) enforces HTTPS, blocks loopback/private/internal IPs, resolves DNS off-loop via `asyncio.to_thread`, connects directly to pinned checked addresses (no redirects, no env proxies; `trust_env=False`), and preserves hostname for TLS SNI / Host header.
7. **Defensive Payloads:** use typed extraction helpers (`extract_dict`, `extract_list`) instead of chained naked `.get()` calls.
8. **Git Safety & Releases:**
   - Never commit secrets or push to `main`; use feature branches and PRs.
   - **Tag is version (`uv-dynamic-versioning`):** `pyproject.toml` uses `dynamic = ["version"]`; `server.json` commits `0.0.0`. Never bump versions in PRs (`pyproject.toml`, `__init__.py`, `server.json`, `uv.lock`, `CHANGELOG.md`); `scripts/check_version.py` rejects `0.0.0` / `0.0.1.devN`.
   - **Breaking changes (`feat!` / `fix!`):** end PR body with one `BREAKING CHANGE:` paragraph (before `<!-- This is an auto-generated comment: release notes by coderabbit.ai -->` if present) listing breaking items only — **no migration steps** (`scripts/release_notes.py` stops at the CodeRabbit marker). Keep PR body accurate before squash merge (becomes commit message).
   - **`CHANGELOG.md` frozen at 1.3.0:** GitHub Releases are the changelog (`scripts/release_notes.py`). Leave `README.md` unpinned (`uvx --from mcp-server-taxcode taxcode-mcp`).
   - **Tagging:** only on maintainer go. Preview `uv run python scripts/release_notes.py` on `main`; verify proposed SemVer version is unused on git `origin` tags, PyPI, and MCP Registry, and passes `uv build` wheel version check before tagging.

---

## 🛠️ Development & Verification Commands

Requires `uv >= 0.13.4` (`[tool.uv] required-version` in `pyproject.toml`).

```bash
# Install editable with dev dependencies
uv sync --locked --extra dev

# Lint and formatting
uv run ruff check . && uv run ruff format --check .

# Strict type checking
uv run mypy --strict src/

# Test suite with 100% coverage requirement
uv run pytest --cov=src/taxcode_mcp --cov-fail-under=100 -v

# Tool contract verification
uv run python scripts/check_tool_contract.py

# Upstream OpenAPI / route drift check
uv run python scripts/check_openapi_drift.py

# Data files, source manifest and generated docs are current
uv run python scripts/build_catalog.py --check

# Protocol integration tests (stdio handshake & stateless streamable HTTP)
uv run pytest tests/test_protocol.py

# Live end-to-end sandbox testing (on demand; default CI uses -m 'not live')
uv run pytest tests/test_live.py -m live

# MCP Protocol Conformance Verification (SEP-2577 / 2026-07-28)
./scripts/check_conformance.sh

# Local pre-commit CodeRabbit CLI review
coderabbit review --agent --uncommitted
```

---

## 🔄 CI & Releases

- **CI (`.github/workflows/ci.yml`):** runs on `push`/`pull_request` to `main`, weekly schedule, and `workflow_dispatch`. Jobs: `lint-and-types`, `test` (Python 3.11–3.14) + aggregate `tests`, `tool-contract`, `conformance`, `build-check`, `docker-build` (builds without pushing, validates OCI label, stripped build deps, stdio `initialize`/`tools/list`, and negative `CA_CERTIFICATES_FLOOR` check), and `drift-check` (schedule/manual only).
- **Dependabot (`.github/dependabot.yml` + `dependabot-automerge.yml`):** squash-merges minor/patch PRs after CI passes on that head commit; major and `docker` ecosystem updates always wait for human review.
- **Releases (`.github/workflows/release.yml`):** triggered on `v*` tags (`fetch-depth: 0`); builds & verifies wheel version matches tag, publishes to PyPI via OIDC, creates GitHub Release from `scripts/release_notes.py`, pushes container image to GHCR, attests provenance for wheel and container digests (`actions/attest-build-provenance`), and publishes `server.json` to the MCP Registry.

---

## VCS

```yaml
vcs:
  host: github
  remote: origin
  repository: christianclaudio/mcp-server-taxcode
  default_branch: main
  protected_branches: [main]
  branch_policy: pr_only
  merge_strategy: squash
  delete_branch_on_merge: true
  require_coderabbit: true
  branch_naming: type/scope-desc
```
