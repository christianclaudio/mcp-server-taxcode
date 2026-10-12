"""Tests for allowlist profiles, domain-mount profiles, and the annotation-driven readonly gate."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import NotFoundError, ToolError
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.server.transforms.search import RegexSearchTransform
from mcp.types import ToolAnnotations

from scripts.check_tool_contract import EXPECTED_TOOLS
from taxcode_mcp import middleware, profiles, server
from taxcode_mcp.config import settings
from taxcode_mcp.errors import SafetyViolationError
from taxcode_mcp.middleware import ReadOnlyGateMiddleware
from taxcode_mcp.profiles import (
    FULL_ONLY_TOOLS,
    PROFILES,
    Profile,
    ReadOnlyToolFilter,
    is_read_only_tool,
)
from taxcode_mcp.server import create_server

# Tests that run the Code Mode sandbox skip without the code-mode extra; in CI the
# conftest guard aborts the session instead, so these never skip silently there.
CODE_MODE_SKIP_REASON = "needs the code-mode extra (fastmcp[code-mode] / pydantic-monty)"

INTAKE = PROFILES["intake"]
JOB_PROFILES = [p for p in PROFILES.values() if p.is_allowlist]


async def _tool_names(app: FastMCP) -> set[str]:
    return {t.name for t in await app.list_tools()}


# ---------------------------------------------------------------------------
# Profile kinds: domain mounts and allowlists coexist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allowlist_profile_exposes_exactly_its_tools() -> None:
    """intake exposes exactly its allowlist, spanning the system and items domains."""
    assert INTAKE.tools is not None
    names = await _tool_names(create_server(profile="intake"))
    assert names == set(INTAKE.tools)
    assert {n.split("_", 1)[0] for n in names} == {"system", "items"}


@pytest.mark.asyncio
async def test_domain_mount_and_allowlist_profiles_coexist() -> None:
    """A domain-mount profile (items) and an allowlist profile (intake) both build."""
    assert not PROFILES["items"].is_allowlist
    assert INTAKE.is_allowlist
    items_names = await _tool_names(create_server(profile="items"))
    intake_names = await _tool_names(create_server(profile="intake"))
    assert items_names == {n for n in EXPECTED_TOOLS if n.startswith("items_")}
    assert "system_get_health_status" in intake_names
    assert "items_delete_item" in items_names
    assert "items_delete_item" not in intake_names


@pytest.mark.asyncio
async def test_allowlist_keeps_prompts_and_resources() -> None:
    """Allowlist filtering is tools-only: prompts and resources match the full profile."""
    full = create_server(profile="full")
    intake = create_server(profile="intake")
    assert {p.name for p in await intake.list_prompts()} == {
        p.name for p in await full.list_prompts()
    }
    assert {str(r.uri) for r in await intake.list_resources()} == {
        str(r.uri) for r in await full.list_resources()
    }
    assert "items_analyze_item" in {p.name for p in await intake.list_prompts()}
    rendered = await intake.render_prompt("items_analyze_item", {"item_id": "42"})
    assert "42" in str(rendered)


@pytest.mark.asyncio
async def test_allowlist_hides_tools_outside_the_list() -> None:
    """A tool outside the allowlist cannot be called on that profile."""
    app = create_server(profile="intake")
    with pytest.raises(Exception, match="Unknown tool"):
        await app.call_tool("items_delete_item", {"item_id": "1", "confirm": True})


def test_unknown_allowlist_name_fails_at_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every allowlisted name must exist in the full catalog; no silent drop."""
    broken = Profile(
        name="broken",
        job="Test only.",
        tools=frozenset({"items_list_items", "items_nonexistent", "system_typo"}),
    )
    monkeypatch.setitem(profiles.PROFILES, "broken", broken)
    with pytest.raises(ValueError, match="items_nonexistent, system_typo"):
        create_server(profile="broken")


def test_unknown_profile_fails_at_build() -> None:
    """An unknown profile name raises instead of building an empty server."""
    with pytest.raises(ValueError, match="Unknown profile 'nope'"):
        create_server(profile="nope")


@pytest.mark.asyncio
async def test_full_is_exhaustive_and_every_tool_is_placed() -> None:
    """full lists every tool; each tool is in a job profile or marked full-only."""
    full_names = await _tool_names(create_server(profile="full"))
    assert full_names == set(EXPECTED_TOOLS)
    in_jobs = set().union(*(p.tools or frozenset() for p in JOB_PROFILES))
    assert in_jobs | FULL_ONLY_TOOLS == full_names
    assert not in_jobs & FULL_ONLY_TOOLS


@pytest.mark.asyncio
async def test_bulk_tool_listed_in_full_but_blocked_without_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bulk destructive tools are listed in full but refused while the bulk gate is off."""
    monkeypatch.setattr(settings, "MCP_ALLOW_BULK_DESTRUCTIVE", False)
    app = create_server(profile="full")
    assert "items_bulk_delete_items" in await _tool_names(app)
    with pytest.raises(ToolError, match="Bulk destructive operations disabled"):
        await app.call_tool("items_bulk_delete_items", {"item_ids": ["1"]})


def test_main_accepts_allowlist_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """--profile intake is a valid CLI choice and rebuilds the gateway."""
    monkeypatch.setattr(FastMCP, "run", MagicMock())
    monkeypatch.setattr("sys.argv", ["taxcode-mcp", "--profile", "intake"])
    server.main()
    assert server.mcp is not None


# ---------------------------------------------------------------------------
# Discovery stays full-only on allowlist profiles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allowlist_profile_with_search_stays_flat(caplog: pytest.LogCaptureFixture) -> None:
    """Requesting Tool Search or Code Mode on an allowlist profile logs and stays flat."""
    with caplog.at_level(logging.WARNING):
        searched = create_server(profile="intake", enable_tool_search=True)
        coded = create_server(profile="intake", enable_code_mode=True)
    assert INTAKE.tools is not None
    assert await _tool_names(searched) == set(INTAKE.tools)
    assert await _tool_names(coded) == set(INTAKE.tools)
    messages = [r.message for r in caplog.records]
    assert any("Tool Search requested with profile='intake'" in m for m in messages)
    assert any("Code Mode requested with profile='intake'" in m for m in messages)


def test_discovery_modes_mutually_exclusive_on_allowlist_profile() -> None:
    """Mutual exclusion is checked before profile handling."""
    with pytest.raises(ValueError, match="mutually exclusive"):
        create_server(profile="intake", enable_tool_search=True, enable_code_mode=True)


# ---------------------------------------------------------------------------
# Read-only: readOnlyHint is the single source of truth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_readonly_profile_equals_read_only_annotated_set() -> None:
    """The readonly profile exposes exactly the full tools annotated readOnlyHint=True."""
    full_tools = await create_server(profile="full").list_tools()
    annotated = {t.name for t in full_tools if is_read_only_tool(t)}
    assert annotated == {n for n, spec in EXPECTED_TOOLS.items() if spec["read_only"]}
    assert await _tool_names(create_server(profile="readonly")) == annotated


@pytest.mark.asyncio
async def test_readonly_profile_gate_enforced_without_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """profile=readonly enforces the gate itself, even with MCP_READONLY unset."""
    monkeypatch.setattr(settings, "MCP_READONLY", False)
    app = create_server(profile="readonly")
    with pytest.raises(SafetyViolationError, match="items_create_item"):
        await app.call_tool("items_create_item", {"name": "x"})
    res = await app.call_tool("system_get_health_status", {})
    assert not res.is_error


@pytest.mark.asyncio
async def test_readonly_enforced_under_allowlist_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """MCP_READONLY on an allowlist profile hides and refuses its write tools."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = create_server(profile="intake")
    assert await _tool_names(app) == {
        "system_get_health_status",
        "items_list_items",
        "items_get_item",
    }
    with pytest.raises(SafetyViolationError, match="items_create_item"):
        await app.call_tool("items_create_item", {"name": "x"})
    res = await app.call_tool("items_get_item", {"item_id": "7"})
    assert not res.is_error


def _annotation_probe_server() -> FastMCP:
    """Minimal server with one tool per annotation state, gate on, and Tool Search."""
    app = FastMCP("annotation-probe")
    app.add_middleware(ReadOnlyGateMiddleware())

    @app.tool(annotations=ToolAnnotations(read_only_hint=True))
    def annotated_read() -> str:
        return "read"

    @app.tool(annotations=ToolAnnotations(read_only_hint=False))
    def annotated_write() -> str:
        return "write"

    @app.tool
    def unannotated() -> str:
        return "unannotated"

    app.add_transform(RegexSearchTransform())
    return app


@pytest.mark.asyncio
async def test_gate_allows_read_only_annotation_directly_and_via_call_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """readOnlyHint=True is allowed under readonly, directly and through call_tool."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = _annotation_probe_server()
    direct = await app.call_tool("annotated_read", {})
    assert "read" in str(direct.content)
    proxied = await app.call_tool("call_tool", {"name": "annotated_read", "arguments": {}})
    assert "read" in str(proxied.content)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["annotated_write", "unannotated"])
async def test_gate_refuses_false_or_missing_annotation(
    monkeypatch: pytest.MonkeyPatch, tool_name: str
) -> None:
    """readOnlyHint=False or no annotation is a write: refused directly and via call_tool."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = _annotation_probe_server()
    with pytest.raises(SafetyViolationError, match=tool_name):
        await app.call_tool(tool_name, {})
    with pytest.raises(SafetyViolationError, match=tool_name):
        await app.call_tool("call_tool", {"name": tool_name, "arguments": {}})


@pytest.mark.asyncio
async def test_readonly_tool_filter_get_tool() -> None:
    """ReadOnlyToolFilter.get_tool returns read-only tools and drops the rest."""
    app = _annotation_probe_server()
    app.add_transform(ReadOnlyToolFilter())
    assert await app.get_tool("annotated_read") is not None
    assert await app.get_tool("annotated_write") is None
    assert await app.get_tool("unannotated") is None


# ---------------------------------------------------------------------------
# Read-only through the Tool Search proxy on full
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_search_readonly_read_succeeds_write_refused_on_wire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On full + Tool Search + readonly: a READ via call_tool works; a WRITE is isError."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = create_server(profile="full", enable_tool_search=True)
    async with Client(app) as client:
        read = await client.call_tool(
            "call_tool",
            {"name": "items_list_items", "arguments": {}},
            raise_on_error=False,
        )
        assert not read.is_error
        assert "Item 1" in str(read.content)

        write = await client.call_tool(
            "call_tool",
            {"name": "items_create_item", "arguments": {"name": "blocked"}},
            raise_on_error=False,
        )
        assert write.is_error
        assert "read-only mode" in str(write.content)
        assert "items_create_item" in str(write.content)


@pytest.mark.asyncio
async def test_gate_refuses_on_outer_call_tool_invocation(monkeypatch: pytest.MonkeyPatch) -> None:
    """The refusal comes from the gate unwrapping the outer call_tool, not an inner re-run."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    seen: list[tuple[str, str]] = []
    real_resolve = middleware._resolve_effective_tool_name

    def spy(context: MiddlewareContext) -> str:
        effective = real_resolve(context)
        seen.append((context.message.name, effective))
        return effective

    monkeypatch.setattr(middleware, "_resolve_effective_tool_name", spy)
    app = create_server(profile="full", enable_tool_search=True)
    with pytest.raises(SafetyViolationError, match="items_create_item"):
        await app.call_tool("call_tool", {"name": "items_create_item", "arguments": {}})
    # Exactly one gate decision: on the outer proxy call. The inner call never ran.
    assert seen == [("call_tool", "items_create_item")]


@pytest.mark.asyncio
async def test_without_unwrap_the_proxy_gate_behaviour_breaks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: with the unwrap removed, reads via call_tool are refused and the
    write refusal names the proxy instead of the proxied tool."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)

    def no_unwrap(context: MiddlewareContext) -> str:
        name: Any = getattr(context.message, "name", "")
        return str(name)

    monkeypatch.setattr(middleware, "_resolve_effective_tool_name", no_unwrap)
    app = create_server(profile="full", enable_tool_search=True)
    with pytest.raises(SafetyViolationError, match="'call_tool' blocked"):
        await app.call_tool("call_tool", {"name": "items_list_items", "arguments": {}})
    with pytest.raises(SafetyViolationError) as exc_info:
        await app.call_tool("call_tool", {"name": "items_create_item", "arguments": {}})
    assert "items_create_item" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_search_tools_annotated_read_only_and_usable_under_readonly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """search_tools carries readOnlyHint=True, works under readonly, and finds only reads."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = create_server(profile="full", enable_tool_search=True)
    listed = {t.name: t for t in await app.list_tools()}
    assert is_read_only_tool(listed["search_tools"])
    assert not is_read_only_tool(listed["call_tool"])
    res = await app.call_tool("search_tools", {"pattern": "item"})
    assert not res.is_error
    assert "items_list_items" in str(res.content)
    assert "items_create_item" not in str(res.content)
    assert await app.get_tool("not_a_tool") is None


@pytest.mark.asyncio
async def test_code_mode_discovery_read_only_and_execute_refused_under_readonly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Code Mode search/get_schema are annotated read-only; execute is refused under readonly."""
    pytest.importorskip("pydantic_monty", reason=CODE_MODE_SKIP_REASON)

    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = create_server(profile="full", enable_code_mode=True)
    listed = {t.name: t for t in await app.list_tools()}
    assert is_read_only_tool(listed["search"])
    assert is_read_only_tool(listed["get_schema"])
    assert not is_read_only_tool(listed["execute"])
    with pytest.raises(SafetyViolationError, match="'execute' blocked"):
        await app.call_tool("execute", {"code": "return 1"})


# ---------------------------------------------------------------------------
# Read-only: unknown names keep FastMCP's "Unknown tool"; real writes are refused
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_readonly_unknown_name_direct_is_unknown_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct call to a name outside the catalog gets Unknown tool, not the read-only refusal."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = create_server(profile="full")
    with pytest.raises(NotFoundError, match="Unknown tool: 'items_nope'"):
        await app.call_tool("items_nope", {})
    async with Client(app) as client:
        res = await client.call_tool("items_nope", {}, raise_on_error=False)
    assert res.is_error
    assert "Unknown tool" in str(res.content)
    assert "read-only" not in str(res.content)


@pytest.mark.asyncio
async def test_readonly_unknown_name_via_call_tool_is_unknown_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """call_tool with a name outside the catalog gets Unknown tool on full + search."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = create_server(profile="full", enable_tool_search=True)
    with pytest.raises(ToolError, match="Unknown tool: 'items_nope'") as exc_info:
        await app.call_tool("call_tool", {"name": "items_nope", "arguments": {}})
    assert not isinstance(exc_info.value, SafetyViolationError)
    async with Client(app) as client:
        res = await client.call_tool(
            "call_tool", {"name": "items_nope", "arguments": {}}, raise_on_error=False
        )
    assert res.is_error
    assert "Unknown tool" in str(res.content)
    assert "read-only" not in str(res.content)


@pytest.mark.asyncio
@pytest.mark.parametrize("via_proxy", [False, True])
async def test_readonly_hidden_write_refused_and_read_succeeds(
    monkeypatch: pytest.MonkeyPatch, via_proxy: bool
) -> None:
    """A real write hidden by the read-only filter is refused; a real read still works."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = create_server(profile="full", enable_tool_search=via_proxy)

    def call(name: str, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if via_proxy:
            return "call_tool", {"name": name, "arguments": arguments}
        return name, arguments

    async with Client(app) as client:
        write = await client.call_tool(
            *call("items_create_item", {"name": "x"}), raise_on_error=False
        )
        read = await client.call_tool(
            *call("items_get_item", {"item_id": "7"}), raise_on_error=False
        )
    assert write.is_error
    assert "read-only mode" in str(write.content)
    assert "items_create_item" in str(write.content)
    assert not read.is_error
    assert "Item 7" in str(read.content)
    with pytest.raises(SafetyViolationError, match="items_create_item"):
        await app.call_tool(*call("items_create_item", {"name": "x"}))


@pytest.mark.asyncio
async def test_readonly_outside_allowlist_is_unknown_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Under read-only, a tool outside the active allowlist profile is unknown, like without it."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = create_server(profile="intake")
    with pytest.raises(NotFoundError, match="Unknown tool"):
        await app.call_tool("items_delete_item", {"item_id": "1"})
    with pytest.raises(SafetyViolationError, match="items_create_item"):
        await app.call_tool("items_create_item", {"name": "x"})


@pytest.mark.asyncio
async def test_readonly_switched_on_after_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """With read-only switched on at runtime (no filter, empty gate catalog), unknown names
    still reach Unknown tool and real writes are still refused."""
    monkeypatch.setattr(settings, "MCP_READONLY", False)
    app = create_server(profile="full")
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    with pytest.raises(NotFoundError, match="Unknown tool"):
        await app.call_tool("items_nope", {})
    with pytest.raises(SafetyViolationError, match="items_create_item"):
        await app.call_tool("items_create_item", {"name": "x"})


# ---------------------------------------------------------------------------
# Read-only without Tool Search: call_tool is an unknown name, not a proxy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["full", "intake", "readonly"])
@pytest.mark.parametrize("inner", ["items_create_item", "items_list_items"])
async def test_readonly_call_tool_without_search_is_unknown_tool(
    monkeypatch: pytest.MonkeyPatch, profile: str, inner: str
) -> None:
    """Without Tool Search, call_tool is not on the server: Unknown tool 'call_tool' for both
    a write and a read inner name, never the read-only refusal."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = create_server(profile=profile, enable_tool_search=False)
    arguments = {"name": inner, "arguments": {}}
    with pytest.raises(NotFoundError, match="Unknown tool: 'call_tool'") as exc_info:
        await app.call_tool("call_tool", arguments)
    assert not isinstance(exc_info.value, SafetyViolationError)
    async with Client(app) as client:
        res = await client.call_tool("call_tool", arguments, raise_on_error=False)
    assert res.is_error
    assert "Unknown tool: 'call_tool'" in str(res.content)
    assert "read-only" not in str(res.content)


@pytest.mark.asyncio
async def test_readonly_call_tool_with_search_still_unwraps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With Tool Search on full, call_tool is real: unwrap and judge the proxied tool."""
    monkeypatch.setattr(settings, "MCP_READONLY", True)
    app = create_server(profile="full", enable_tool_search=True)
    with pytest.raises(SafetyViolationError, match="items_create_item"):
        await app.call_tool("call_tool", {"name": "items_create_item", "arguments": {}})
    read = await app.call_tool("call_tool", {"name": "items_list_items", "arguments": {}})
    assert not read.is_error
