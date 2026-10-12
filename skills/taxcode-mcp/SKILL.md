---
name: taxcode-mcp
description: Enterprise Agent Skill template for operating MCP server tools, safety gates, and client recipes.
---

# Tax Code MCP Server (`mcp-server-taxcode`) Agent Skill

This skill provides operational playbooks, safety constraints, and client recipes for AI agents interfacing with this MCP server.

---

## 🎯 Core Agent Recipes & Playbooks

### 1. Resource Inspection & Discovery
- **Step 1**: Discover capabilities by querying `server/discover` or reading the system resource `taxcode://system/reference/capabilities`.
- **Step 2**: Inspect items using `items_get_item(item_id=...)` (read-only, cached).

### 2. Gated Mutations & Destructive Operations
- **Single Destructive Deletions**: Deleting single resources requires explicit operator confirmation (`confirm=True`). If `confirm=False`, the server initiates an MCP 2.0 Modern Roots Tool Run (MRTR) elicitation request when supported by the client.
- **Bulk Operations**: Bulk mutations require setting `MCP_ALLOW_BULK_DESTRUCTIVE=1` in the server environment, setting `confirm=True`, and default to `dry_run=True`.

---

## 🛠️ Tool Suite Reference

| Tool | Purpose | Annotations | Key Parameters |
| :--- | :--- | :--- | :--- |
| `system_get_health_status` | Check API connectivity & health | `readOnlyHint=True` | None |
| `items_get_item` | Fetch resource by ID | `readOnlyHint=True` | `item_id` |
| `items_list_items` | List available resources | `readOnlyHint=True` | `limit`, `offset` |
| `items_create_item`| Create new resource (client_id injected) | `readOnlyHint=False` | `name`, `payload` |
| `items_delete_item`| Delete single resource (Gated) | `destructiveHint=True` | `item_id`, `confirm` |
| `items_bulk_delete_items` | Bulk resource cleanup (Gated) | `destructiveHint=True` | `item_ids`, `dry_run`, `confirm` |


---

## ⚡ Agent Operating Guidelines

1. **Read-Only Mode Respect**:
   When `TAXCODE_MCP_READONLY=1` or `--profile readonly` is active, only tools annotated `readOnlyHint=True` are listed or callable; calls to any other tool (directly, or through `call_tool` when Tool Search is on) are refused with `isError: true`. A name that does not exist returns `Unknown tool`; without Tool Search that includes `call_tool` itself. The `readOnlyHint` annotation is the single source of truth. Agents must switch to query-only workflows.
2. **Deterministic Caching (SEP-2549)**:
   Catalog endpoints are cached with `ttl_ms=3600000` (1 hr). Avoid rapid redundant re-queries.
3. **Graceful Fail-Closed Handling**:
   A failed call (upstream API error, a safety-gate refusal, or a live bulk delete in which every item failed, `"type": "batch_failed"`) returns `isError: true` with a sanitized message. Check `isError` before using the result; a successful result carries `status: "success"`, `"dry_run"` for a bulk-delete preview, or `"partial_success"` for a live bulk delete in which some items failed (check `failed_count` and `errors`).

4. **Pick a profile first**:
   Pick the profile that matches the job. Domain-mount profiles: `system`, `items`, `readonly`. Job-shaped allowlist profiles span domains: `intake` (confirm upstream health, look up items, create new ones). `full` is the complete catalog; `items_delete_item` and `items_bulk_delete_items` are in no job profile and are reachable in `full` (and `items`).
5. **Discovery only on `full`**:
   Tool Search (`search_tools` + `call_tool`) and experimental Code Mode attach only when `profile=full` and explicitly enabled. Do not expect search meta-tools on curated profiles. Do not enable Tool Search and Code Mode together. Code Mode needs the package's `code-mode` extra (install `mcp-server-taxcode[code-mode]`, which pulls `fastmcp[code-mode]`); without it the server logs a warning, skips Code Mode, and serves the flat catalog, so `execute` is not listed.
6. **Hybrid A+B tool names**:
   Wired names are domain-mounted (`system_*`, `items_*`). Do not invent a product-wide `template_*` stamp on every tool.
7. **No stub tools / no full-catalog dump**:
   Every tool must back a real client method. Large catalogs use Tool Search or Code Mode — not guidance to dump the entire catalog into the prompt.
