---
vcs:
  system: github
  remote: https://github.com/christianclaudio/mcp-server-taxcode
  owner: christianclaudio
  repo: mcp-server-taxcode
  default_branch: main
  branch_policy: pr_only
  merge_method: squash
  delete_branch_on_merge: true
---

# AGENTS.md

Instructions for AI coding agents working on this repository or developing MCP tools for tax codes and statutory regulations.

---

## 🎯 Project Overview

This is `mcp-server-taxcode` — an enterprise Python Model Context Protocol (MCP) server for tax codes, statutory definitions, regulations, and tax reference guidance.

**Primary Purpose**:
Expose tax code navigation, statutory lookups, rate schedules, and cross-reference definitions to AI agents with strict enterprise safety gates, offline test isolation, and FastMCP 4 server composition.

---

## 📚 Canonical Documentation & Live Doc MCPs

Before designing, implementing, or updating any MCP tool, always consult the official machine-readable documentation indexes ("the bibles") and live documentation MCP servers:

| Resource | `llms.txt` index | Live doc MCP (SSE / Streamable HTTP) |
| :--- | :--- | :--- |
| **FastMCP 4 Framework** | [`https://gofastmcp.com/llms.txt`](https://gofastmcp.com/llms.txt) | `https://gofastmcp.com/mcp` (`search_fast_mcp`, `query_docs_filesystem_fast_mcp`) |
| **Model Context Protocol (Official)** | [`https://modelcontextprotocol.io/llms.txt`](https://modelcontextprotocol.io/llms.txt) | `https://modelcontextprotocol.io/mcp` (`search_model_context_protocol`, `query_docs_filesystem_model_context_protocol`) |

Host configuration JSON for these doc servers (`mcp_config.json` and other desktop or agent-host configs) lives in `README.md` (Canonical Documentation & Live Doc MCPs).

---

## 🗺️ Key Paths

- `src/<name>_mcp/server.py` — root gateway, `create_server` factory, parent middleware, `mount()`. Streamable HTTP and legacy SSE both pass `host_origin_protection=True` with `allowed_hosts` / optional `allowed_origins` (`COOKBOOK.md` Recipe 8).
- `src/<name>_mcp/auth.py` — `SharedTokenVerifier` (FastMCP `TokenVerifier`) for `<NAME>_MCP_AUTH_TOKEN` and the HTTP bind policy; the verifier is attached at server build, so `fastmcp run` and `http_app()` enforce the token too (`COOKBOOK.md` Recipe 8).
- `src/<name>_mcp/tools/<domain>.py` — domain sub-servers (template ships `system.py`, `items.py`); re-exported from `tools/__init__.py`.
- `src/<name>_mcp/client.py` — async HTTP client; `ssrf.py` — SSRF / DNS-rebinding guard; `errors.py` — exceptions and secret redaction; `middleware.py` — parent and child middleware; `config.py` — settings.
- `src/taxcode_mcp/data/` — the single source of truth for statute and publication rows: `jurisdictions.json` (catalog order), `statutes/<code>.json`, `publications/<code>.json`, `manifest/<code>.json` (one source record per row), and `rates/<code>.json` (the compliance tools' rate tables, one per state in `RATE_TABLE_MODELS`). `src/taxcode_mcp/catalog.py` loads and validates them; `db.py` seeds DuckDB from the rows, and `tools/compliance.py` reads every rate from the rate tables. Row layout, manifest fields and how to add a row: `CONTRIBUTING.md` (Adding or changing a row).
- `scripts/build_catalog.py` — rewrites the data files in canonical form and generates `docs/<code>/*.md`, `docs/<code>/llms-full.txt` (one jurisdiction's full text), `docs/llms.txt` and `docs/llms-full.txt` (the index of the per-jurisdiction files) from them; `--check` (run in CI and by `tests/test_catalog.py`) fails on a schema or canonical-form error (rate tables included, and a missing or stray rate table), an incomplete or mismatched manifest entry, or docs that differ from the generated output. Never edit generated docs by hand.
- `src/taxcode_mcp/paging.py` — hard response cap (32,000 characters of JSON) and paging for the statutes tools: `statutes_lookup_statute` pages long text (`page_cursor` / `next_cursor`), search and publications clamp `limit` to 1..25 items per call (`offset` / `next_offset`), and the `taxcode://statutes/{jurisdiction}/{section}{?page_cursor}` resource returns the same 8,000-character pages, ending a non-final page with the full URI of the next one (`...?page_cursor=`). The template has no `statutes/` segment of its own: the `statutes` mount namespace supplies it.
- `scripts/check_tool_contract.py` — source of truth for the expected tool set and annotations. Do not hard-code tool counts elsewhere.
- `scripts/check_openapi_drift.py`, `scripts/check_conformance.sh` + `conformance-baseline.yml` — drift and protocol conformance.
- `tests/` — offline unit, layered-composition, and protocol tests; `test_e2e_live.py` is opt-in (`-m e2e`).
- `.github/workflows/` — `ci.yml`, `dependabot-automerge.yml`, and `release.yml` (on a `v*` tag: build with full history, verify the wheel version matches the tag, publish to PyPI via OIDC, create the GitHub Release from `scripts/release_notes.py`, build and push the container image to GHCR, attest build provenance for both wheel and container digests, and publish `server.json` to the MCP Registry). `ci.yml`'s `docker-build` job builds the image on every PR and checks its `io.modelcontextprotocol.server.name` label matches `server.json`.
- `scripts/release_notes.py` — release body from squash commits since the previous `v*` tag. `scripts/check_version.py` — runs after `uv build` and reads the version from the single wheel in `dist/` (the file that ships, as release.yml's tag check does); fails on `0.0.0` (no git metadata) or `0.0.1.devN` (no reachable tag, a shallow checkout).
- `server.json` (MCP Registry metadata), `Dockerfile`, `pyproject.toml`, `fastmcp.json`.
- `COOKBOOK.md` — private maintainer runbook (release SOP, drift, testing, composition, payload hardening, naming recipes). Template-only.

---

## 🧩 FastMCP 4 Server Composition & Hierarchical Layering

House tool naming is **hybrid A+B** (`COOKBOOK.md` Recipe 11). Structure a multi-domain catalog into domain sub-servers and mount them on a root gateway. A small single-vendor catalog stays on one server with bare tool names.

### Tool naming (hybrid A+B)

* **A — bare names** for a small single-vendor catalog: `get_forecast`, `get_markets`, `place_order`. Omit `namespace`.
* **B — domain layering** for a multi-domain or large in-server catalog:
  * `mount(..., namespace="weather")` so composed modules stay unique (`weather_get_forecast`, `calendar_list_events`, `docs_search`), or
  * flat domain prefixes on one server (`admin_*`, `example_domain_*`).
* The prefix is the domain. A product-wide `<name>_*` or `vendor_*` stamp on every tool is not the house default.
* MCP tool-name uniqueness is per server ([Tool names](https://modelcontextprotocol.io/specification/2026-07-28/server/tools#tool-names)). FastMCP `mount(..., namespace=...)` separates domain modules and avoids composition collisions ([Namespacing](https://gofastmcp.com/servers/composition#namespacing)). It does not brand the product. A host that aggregates many servers disambiguates on the client side.

### Composition rules

Full code samples (sub-servers, root gateway, middleware classes, `ToolTransform`, profiles, in-memory testing, lifespan, `fastmcp.json`) live in **`COOKBOOK.md`** Recipe 7.

* **Domain sub-servers:** each `tools/<domain>.py` owns one `FastMCP("<name>-<domain>")` instance. Pattern A may keep a single module.
* **Root gateway:** pattern B mounts each domain with its namespace, which prefixes tools (`system_*`, `items_*`), prompts, and resources (`data://system/*`):
  ```python
  root.mount(system_server, namespace="system")
  root.mount(items_server, namespace="items")
  ```
  Pattern A mounts without `namespace`, or skips `mount` and registers bare names on the root server.
* **Hierarchical middleware:** parent middleware runs on every request (`ParentAuditMiddleware` for timing, audit logging, secret redaction; `ReadOnlyGateMiddleware`). Child middleware runs on its sub-server (`ItemsDomainGuardMiddleware` for domain checks and bulk destructive gates).
* **`ToolTransform`:** hide infrastructure arguments (e.g. `client_id`) from the LLM-visible schema and inject defaults at runtime.
* **Profiles first, then discovery:** Agents pick `--profile` (or `<NAME>_MCP_PROFILE`) before enabling discovery. Profiles live in `src/<name>_mcp/profiles.py` and come in two kinds that coexist: **domain-mount profiles** (`full|system|items|readonly` — which domains mount) and **tool-name allowlist profiles** (`intake` — explicit client-visible names over the full catalog, for jobs that span domains). Shape allowlist profiles around a job a user does, one line per profile on that job; list tools in no job profile in `FULL_ONLY_TOOLS` (reachable in `full`). An unknown profile or an allowlisted name missing from the catalog raises `ValueError` at build time. Allowlists filter tools only (prompts and resources stay). Curated profiles keep a flat `tools/list` — never attach Tool Search or Code Mode there (log a warning and skip if requested).
* **Read-only = `readOnlyHint`:** The MCP `readOnlyHint` annotation is the single source of truth for read-only classification. `--profile readonly` / `<NAME>_MCP_READONLY=1` keep only tools annotated `readOnlyHint=True` (`ReadOnlyToolFilter`), and `ReadOnlyGateMiddleware` refuses any other real tool, including writes hidden by that filter and calls through the Tool Search `call_tool` proxy (unwrapped only when `call_tool` is a real tool, i.e. Tool Search is attached; otherwise `call_tool` itself is an unknown name). A missing annotation or `readOnlyHint` other than `True` is a write (fail closed). A name that is not a tool in the active profile keeps FastMCP's normal `Unknown tool` error, never the read-only refusal. No name or tag lists.
* **Tool Search / Code Mode on `full` only:** Opt-in via `--enable-tool-search` / `<NAME>_MCP_ENABLE_TOOL_SEARCH` (default backend regex; optional `bm25`) or experimental `--enable-code-mode` / `<NAME>_MCP_ENABLE_CODE_MODE`. The two modes are mutually exclusive. Code Mode stays optional and needs the public `code-mode` extra (`code-mode = ["fastmcp[code-mode]>=<fastmcp floor>"]`, the `pydantic-monty` sandbox), so users install `mcp-server-<name>[code-mode]`, not `dev`: check `pydantic_monty` directly, and when it is missing skip attach with a warning naming the extra and serve the flat catalog. `dev` repeats the same pin so tests run `execute`; tests that need the sandbox use `pytest.importorskip("pydantic_monty")`, CI installs `--extra code-mode`, and `tests/conftest.py` aborts a CI run without the sandbox so those tests never skip silently. Any doc or post quoting a Code Mode token figure must say it needs the `code-mode` extra. Default remains a flat unaugmented `tools/list` for standard MCP hosts. See `COOKBOOK.md` Recipe 7 §4.
* **Hybrid A+B naming:** Domain `namespace` (`system_*`, `items_*`) is not a product-wide `<name>_*` stamp. Pattern A (bare names) stays valid for small single-vendor catalogs (Recipe 11).

---

## ⚡ The Canonical Workflow: Building Tools from API / llm.txt

When translating an API documentation page, `llm.txt`, or OpenAPI specification into MCP tools, repeat these 4 steps in exact order:

### 1. Client Method (`client.py`)
- Implement a dedicated `async def` method on the client class.
- Type all arguments strictly. Never use bare `dict` or `Any` when a concrete schema or literal is known.
- URL path parameters **must** be quoted using `quote(str(param), safe="")` to eliminate path traversal vulnerabilities.
- Call `await self._request("METHOD", path, params=..., json=...)`.

### 2. Tool Handler (Domain Sub-server: `tools/<domain>.py`)
- Register the tool with `@<domain>_server.tool()` on its appropriate domain sub-server (e.g. `src/<name>_mcp/tools/<domain>.py`).
- Name the tool with hybrid A+B (Recipe 11): a bare name on a small single-vendor server, or a domain prefix (`weather_*`, `admin_*`) on a multi-domain server. Do not prefix every tool with the product or package name.
- Provide an explicit, agent-friendly docstring describing:
  - What the tool does.
  - Required and optional arguments.
  - Return structure.
- Destructive operations (`POST`, `PUT`, `PATCH`, `DELETE` that modify state) **must** include `confirm: bool = False`.
- Report a failed call by raising, never by returning an error payload. Wrap upstream and execution failures with `tool_error(exc)` (a FastMCP `ToolError` carrying the redacted message; JSON inside the message that parses is redacted value by value and re-serialized, JSON that does not parse is regex-redacted whole and may lose its shape) and gate refusals as `SafetyViolationError`, so the client receives `isError: true` (`COOKBOOK.md` Recipe 7 §1). Assign `failure = tool_error(exc)` inside the `except` block and `raise failure from None` after the block ends, never inside it: `from None` inside the block still leaves the unredacted original on `__context__`. A returned `{"status": "error", ...}` dict reaches the client as a success (`isError: false`). Batch tools process every item; partial success returns a normal result listing each item's status, and when every item fails they raise the redacted `batch_failed` error.
- Re-export the tool function in `src/<name>_mcp/tools/__init__.py` and `server.py` to maintain 100% backward-compatible programmatic imports and direct test invocations.

### 3. Tool Annotations & Gating
- Apply MCP `ToolAnnotations` either during registration or via the post-registration loop:
  - `readOnlyHint`: `True` for inspection/GET; `False` for mutations.
  - `destructiveHint`: `True` for delete/drop/archive/update actions; `False` otherwise.
  - `idempotentHint`: `True` for GET, PUT, idempotent operations; `False` for non-idempotent creations.
  - `openWorldHint`: `True` when interacting with external networks/APIs.
- Gating:
  - Support `READONLY` mode (environment variable `<NAME>_MCP_READONLY=1` or `--profile readonly`). It omits and blocks every tool not annotated `readOnlyHint=True`, so a correct `readOnlyHint` is required on every tool (Recipe 7 §4).
  - Support bulk protection (`<NAME>_MCP_ALLOW_BULK_DESTRUCTIVE=1`) for mass-delete actions.

### 4. Pure Offline Testing & Contract Sync
- Add unit tests in `tests/` mocking responses via `httpx.MockTransport` or `AsyncMock`.
- **Zero live network calls during tests.** Tests must run 100% offline in CI.
- Update expected tool count in `scripts/check_tool_contract.py`.
- Place the new tool in a job (allowlist) profile in `profiles.py` or in `FULL_ONLY_TOOLS`.
- Ensure test statement coverage remains at **100.0%**.

---

## 🛡️ Non-Negotiable Safety & Security Rules

1. **Destructive Confirmation Gate**:
   - Every mutating tool must accept `confirm: bool = False`. If `False`, return a dry-run / confirmation preview without executing the side-effect.
2. **Secret Redaction**:
   - Error messages, logs, and tracebacks must pass through regex redaction (`redact_secrets` in `errors.py`) stripping Bearer tokens, passwords, API keys, client secrets, private keys, PEM blocks, and access/refresh/session tokens. Every secret becomes the fixed mask `[REDACTED]`, which never reveals its length; short values are redacted in keyed forms (`key=x`, `"key": "x"`, `Authorization: Bearer x`). A secret is redacted whole: a quoted value up to its closing unescaped quote (also backslash-escaped inside an already-serialized JSON string), an unquoted `key=value` of a credential key (`password`, `api_key`, `client_secret`, `private_key`) to the end of the line, spaces included, a `{...}` / `[...]` / `(...)` value after such a key or a token key, quoted or not (a Python `repr` such as `{'api_key': ['x']}` or `{'api_key': ('a', 'x')}`), to its balanced bracket, with brackets inside double- or single-quoted strings skipped (or the end of the line when it never balances), a bare number, `True` or word under a quoted key (`{'password': 12345}`; `None`/`null` stays), and a PEM block from `-----BEGIN <label>-----` to the `-----END <label>-----` with the same label (any case; an END with another label does not end it, and a `-----BEGIN` with no matching END is masked to the end of the text). A single-quoted value that contains a newline is masked only to the end of its line (a known limit). In `redact_message`, once 64 brackets have failed to parse (abusive input), the rest of the message is masked. `tool_failure` redacts its payload value by value before `json.dumps` (`redact_payload`), so its JSON message always parses. A bare short `Bearer abc` without `Authorization:` is not redacted. Floods of `(` right after `:` or `=` (e.g. `=(`, `:(`) cost about 25x more per byte than plain text, because each one checks the 256 characters before it for a credential key; it stays linear and nothing leaks, but hundreds of KB of that input take seconds. A value on the line after `password=` (or any credential key) is not masked, because a value on the next line isn't treated as keyed.
   - Tool execution errors reach the client as `tools/call` results with `isError: true` and the redacted message (`tool_error`, raised `from None` after the `except` block ends, so neither `__cause__` nor `__context__` holds the original exception); `ParentAuditMiddleware` logs the same redacted message. A batch tool's per-item errors and its `batch_failed` error (`tool_failure`, raised `from None` outside any `except` block) carry redacted messages too.
   - An unknown tool stays FastMCP 4.0.11's tool result (`isError: true`). The spec's JSON-RPC `-32602` (`Unknown tool: invalid_tool_name`) is a known gap; do not change the wire format. See `COOKBOOK.md` Recipe 12.
3. **Multi-Stage Non-Root Containers**:
   - The `Dockerfile` must use a multi-stage build (`python:3.14-slim AS builder` $\rightarrow$ runtime, pulled from Docker Hub and pinned by digest) creating and executing under non-root `USER mcp` with virtual environment `/opt/venv`. The runtime stage carries `LABEL io.modelcontextprotocol.server.name` equal to the `server.json` `name` (MCP Registry OCI ownership check), and the default command stays stdio.
   - Trust stores follow FastMCP's [certificate authority incident notice (October 2026)](https://github.com/PrefectHQ/fastmcp/blob/main/SECURITY.md#certificate-authority-incident-october-2026): both stages upgrade the OS `ca-certificates` package and fail the build below the patched Debian version (`ARG CA_CERTIFICATES_FLOOR`, declared once), the builder does so before any pip runs because pip trusts the OS store as well as its bundled certifi, the builder bootstraps pip 26.2.2, and `certifi>=2026.10.19` is a direct dependency. `tests/test_dockerfile.py` and CI's `docker-build` job enforce the order and the floor.
   - The pip bootstrap is hash-pinned in `docker/pip-bootstrap.txt` and installed with `--require-hashes`, so it does not depend on the TLS trust of the base image's older pip and certifi; whoever raises the pip floor updates that pin and its sha256 from PyPI (`https://pypi.org/pypi/pip/<version>/json`, the `py3-none-any` wheel's `digests.sha256`), because Dependabot does not track the file.
   - Runtime dependencies install from `uv.lock`, so the image runs the versions CI tests: the builder installs uv from `docker/uv-bootstrap.txt` (hash-pinned, `--require-hashes`, into `/tmp/uv` outside the venv, so the runtime image has no uv), runs `uv export --locked --no-dev --no-emit-project --format requirements.txt` (base dependencies only, a hash for every package, and the build fails if `uv.lock` is stale), installs that file with pip in hash-checking mode, then installs the project with `pip install --no-deps .`. Never install the project with its dependencies (`pip install .`), which resolves them fresh at build time. Whoever changes `[tool.uv] required-version` keeps the uv pin within it and updates its sha256 values from `https://pypi.org/pypi/uv/<version>/json` (the manylinux x86_64 and aarch64 wheels' `digests.sha256`), because Dependabot does not track the file. `tests/test_dockerfile.py` enforces the install steps and the pin.
   - Lowering `CA_CERTIFICATES_FLOOR` with `--build-arg` is unsupported; the build-arg exists only so CI can raise it for the negative floor check, and CI and the release workflow never pass it otherwise.
4. **Registry Metadata Constraint**:
   - In `server.json`, the root `description` must be **strictly $\le$ 100 characters** to pass MCP Registry schema validation (longer strings trigger HTTP 422 errors).
   - The default package transport is `stdio`, and packages must specify `"runtimeHint": "uvx"`.
5. **HTTP Authentication & Bind Policy**:
   - `<NAME>_MCP_AUTH_TOKEN` is read with `.strip()`; empty or whitespace-only means no auth plus a warning. When set, every HTTP entry point (`main()`, `fastmcp run`, `http_app()`) requires `Authorization: Bearer <token>` (`SharedTokenVerifier`: constant-time compare, token hidden from `repr`, blank tokens refused).
   - An HTTP transport on a host other than `127.0.0.1`, `::1` or `localhost` refuses to start without a token unless `<NAME>_MCP_ALLOW_UNAUTHENTICATED_BIND=1`. Stdio is unaffected and stays the image default (`COOKBOOK.md` Recipe 8).
6. **SSRF & DNS Rebinding Defense**:
   - Outbound requests must enforce HTTPS, disallow loopback/private/internal IP addresses, and offload DNS hostname verification to worker threads via `asyncio.to_thread` in `SSRFSafeAsyncTransport` to protect event loop responsiveness. The transport then connects only to an address it checked, trying the next checked address if a connect fails while the request's connect timeout lasts (one deadline shared by all the addresses, not a fresh timeout for each), and keeps the hostname in the URL for the Host header, TLS SNI, certificate verification and per-hostname connection pooling, so the name is never looked up a second time. The client, the drift fetch (`--spec-url`) and `ingest_statutes.py --check-upstream` all use it, and none follows redirects. httpx ignores env proxies whenever a transport is passed (`allow_env_proxies = trust_env and transport is None`), so connections always go direct through the pin; egress-proxy deployments need a proxy-aware transport, which isn't provided; `trust_env=False` on the drift fetch and the `--check-upstream` probe is belt-and-braces.
7. **Defensive API Contracts & Payload Hardening**:
   - Guard against polymorphic nulls and missing keys with typed extraction helpers (`extract_dict`, `extract_list`); never chain naked `.get()` calls without type assertions. Status-classification and selection-loop patterns are in **`COOKBOOK.md`** Recipe 10.
8. **Git Safety & Streamlined Releases**:
   - Never commit secrets or API keys.
   - Never develop or push directly to `main` branch. All work goes through feature branches and PRs.
   - **The git tag is the version.** `uv-dynamic-versioning` reads the `vX.Y.Z` tag at build time; `pyproject.toml` declares `dynamic = ["version"]`, `__version__` comes from `importlib.metadata`, and `server.json` commits `0.0.0` (a publishing release workflow stamps the tag version into it). PRs never edit a version: no bump in `pyproject.toml`, `src/<package>/__init__.py`, `server.json`, `uv.lock`, or `CHANGELOG.md`. Untagged builds report `X.Y.(Z+1).devN+<sha>`; a build with no git metadata reports the fallback `0.0.0`, which `scripts/check_version.py` rejects when run on the built wheel after `uv build`.
   - **Breaking changes:** every `feat!` / `fix!` PR (any `type!:` title) ends its PR body with one `BREAKING CHANGE:` paragraph, starting at the beginning of a line, as the final paragraph (before the CodeRabbit marker if present), listing the breaking items. No migration steps in the footer. `BREAKING CHANGE:` (or its synonym `BREAKING-CHANGE:`) is the only footer token; do not add a separate migration token. `scripts/release_notes.py` stops at the CodeRabbit marker line (outside a code fence) `<!-- This is an auto-generated comment: release notes by coderabbit.ai -->` and ignores everything after it, so the footer goes before CodeRabbit's generated summary, never inside it.
   - **Squash merges use the PR body as the commit message** (repo settings: PR title as squash title, PR body as squash message). Keep the PR body accurate up to the merge, because `scripts/release_notes.py` reads it from the squash commit.
   - **`CHANGELOG.md` is frozen** as of 1.3.0. GitHub Releases are the changelog: `scripts/release_notes.py` builds each release body from the squash commits since the previous tag (every `BREAKING CHANGE:` footer verbatim, then the commit subjects). Do not add CHANGELOG entries.
   - `skills/<name>-mcp/SKILL.md` carries no version: the [Agent Skills specification](https://agentskills.io/specification) has no top-level `version` field. Do not add one. Update the skill only when its operator guidance changes.
   - **Only the maintainer's release go names the template tag.** Nothing auto-bumps from `feat!`, so a breaking template change does not by itself move the template's major version. The template is copied, not installed, so its version marks a revision of the standard, not an API contract.
   - Do not create tags or releases unless the maintainer asks. Releases go out on any weekday on the maintainer's go; no fixed release day. Before the tag, the release owner previews `uv run python scripts/release_notes.py` on `main` and posts it with the release Ask, and the reviewer checks the proposed version against the commit types since the last tag (`!` / `BREAKING CHANGE:` → major, or minor below 1.0.0 per [SemVer §4](https://semver.org/#spec-item-4), `feat` → minor, otherwise patch, except the template rule above), that the version is unused on all three places it could already exist (git tags on `origin`, PyPI, and the MCP Registry; a repo that does not publish to PyPI or the registry, such as the template, checks git only), and that a build at that commit reports the expected wheel version. Full SOP: **`COOKBOOK.md`** Recipe 1.
   - **README is outside the release version ceremony.** Do not add or chase `README.md` `==X.Y.Z` install pins. Update `README.md` only when project behavior, install method, config, or commands actually change. Prefer unpinned install examples (`uvx --from mcp-server-<name> <server-cmd>`) or point readers to GitHub Releases. Full SOP: **`COOKBOOK.md`** Recipe 1 (README and versions).

---

## 🛠️ Development & Verification Commands

uv 0.13.4 or later is required (`[tool.uv] required-version` in `pyproject.toml`, from FastMCP's certificate authority incident notice); older uv refuses to run, and CI's `setup-uv` installs from that setting.

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

# Live end-to-end sandbox testing (on demand; default CI uses -m 'not e2e')
uv run pytest tests/test_e2e_live.py -m e2e

# MCP Protocol Conformance Verification (SEP-2577 / 2026-07-28)
./scripts/check_conformance.sh

# Local pre-commit CodeRabbit CLI review
coderabbit review --agent --uncommitted
```

---

## 🔄 CI & Releases

CI is defined in `.github/workflows/ci.yml` (jobs: `lint-and-types`, `test` on Python 3.11–3.14, `tool-contract`, `build-check`, `docker-build` (builds the image without pushing, checks its MCP server-name label against `server.json`, and checks that the builder stage fails at its `ca-certificates` floor check, before any pip step, when the floor is above the installed version), and `drift-check` (the same OpenAPI drift check as its own job; scheduled and manual runs only)). It runs on pushes and pull requests to `main`, weekly on `main` on Sunday at 15:53 America/New_York (`schedule` with `timezone`, after the Sunday 09:30 America/New_York Dependabot run in `.github/dependabot.yml`), and on manual dispatch (`workflow_dispatch`); the weekly run exists because merges by `dependabot-automerge.yml` use `GITHUB_TOKEN`, and pushes made with `GITHUB_TOKEN` don't trigger push workflows, so those merges get no `push` run. Run the commands above before opening a PR. `dependabot-automerge.yml` turns on squash auto-merge only for Dependabot PRs whose highest update is minor or patch; major updates wait for a human review. Docker base image updates (`package-ecosystem` `docker`) never auto-merge and always wait for a human review: Dependabot's docker ecosystem can move the image tag, not just its digest (for example `python:3.12-slim` to `python:3.14-slim`, a Python runtime upgrade that Dependabot labels a minor update), so the job logs a notice and leaves the PR open.

`release.yml` runs on `v*` tag pushes only: it creates the GitHub Release, then builds the container image, pushes it to GHCR, attests it, and publishes to the MCP Registry. In the template repo the image is built only (no push), and the attest and registry jobs skip. Release procedure (tag-based versioning, release notes, pre-tag checks): **`COOKBOOK.md`** Recipes 1–2; product-repo migration steps: Recipe 9.
