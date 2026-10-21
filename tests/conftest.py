"""Pytest fixtures and mocked HTTP transports."""

import importlib.util
import os
import socket
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

# ``taxcode_mcp.server`` builds its module-level ``mcp`` from these variables when it
# is imported, which happens at collection, before any fixture runs. Clear the shell's
# values first so a token exported for a manual run can't leak into the suite.
AUTH_ENV_NAMES = (
    "TAXCODE_MCP_AUTH_TOKEN",
    "TAXCODE_MCP_ALLOW_UNAUTHENTICATED_BIND",
)
for _name in AUTH_ENV_NAMES:
    os.environ.pop(_name, None)


@pytest.fixture(autouse=True)
def _clear_template_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a token or opt-in already set in the shell out of every test.

    Tests that need auth set the variables themselves with ``monkeypatch.setenv``.
    """
    for name in AUTH_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    # The module-level ``mcp`` was built at import, so a shell token may already be
    # attached to it even after the env vars are cleared.
    import taxcode_mcp.server as server_mod

    if getattr(server_mod, "mcp", None) is not None:
        monkeypatch.setattr(server_mod.mcp, "auth", None)


class RealNetworkUsedError(AssertionError):
    """A test opened a real socket connection instead of using the fake network."""


def _refuse_real_connect(*args: Any, **_kwargs: Any) -> Any:
    raise RealNetworkUsedError(
        f"test tried to open a real network connection {args[1:] or args!r}; "
        "use tests.fake_network or a mock transport"
    )


@pytest.fixture(autouse=True)
def _no_real_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that dials a real host (live ``e2e`` tests excepted).

    ``socket.socket.connect`` / ``connect_ex`` sit under every client stack (httpx and
    httpcore through anyio and asyncio, ``urllib``, ``socket.create_connection``), so an
    unmocked path fails here instead of reaching the network. The fake network patches
    httpcore's backends above this layer and never opens a socket.
    """
    if request.node.get_closest_marker("e2e") is not None:
        return
    monkeypatch.setattr(socket.socket, "connect", _refuse_real_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse_real_connect)
    monkeypatch.setattr(socket, "create_connection", _refuse_real_connect)


def code_mode_ci_guard_error(environ: dict[str, str] | None = None) -> str | None:
    """Return an error when CI runs without the Code Mode sandbox, else None.

    Code Mode tests ``pytest.importorskip("pydantic_monty")`` so a local run without the
    ``code-mode`` extra still passes. In CI (``CI`` set, as on GitHub Actions) a missing
    sandbox would turn those tests into silent skips, so the session aborts instead.
    """
    env = os.environ if environ is None else environ
    if not env.get("CI"):
        return None
    if importlib.util.find_spec("pydantic_monty") is not None:
        return None
    return (
        "CI is set but pydantic_monty is not installed: install the code-mode extra "
        "(uv sync --locked --extra dev --extra code-mode) so Code Mode tests run, not skip."
    )


def pytest_sessionstart(session: pytest.Session) -> None:
    """Abort a CI session that would silently skip the Code Mode tests."""
    error = code_mode_ci_guard_error()
    if error is not None:
        pytest.exit(error, returncode=3)


@pytest.fixture
def mock_transport() -> httpx.MockTransport:
    """Create a mock transport with pre-configured responses."""

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)

        if "health" in url_str:
            return httpx.Response(200, json={"status": "healthy", "uptime": 1234})
        if request.method == "GET" and "items/" in url_str:
            item_id = url_str.split("items/")[-1].split("?")[0]
            if item_id == "not-found":
                return httpx.Response(404, json={"error": "Not Found"})
            return httpx.Response(200, json={"id": item_id, "name": f"Item {item_id}"})
        if request.method == "GET" and "items" in url_str:
            return httpx.Response(200, json={"items": [{"id": "1", "name": "Item 1"}]})
        if request.method == "POST" and "items" in url_str:
            return httpx.Response(201, json={"id": "new-1", "created": True})
        if request.method == "DELETE" and "items/" in url_str:
            return httpx.Response(200, json={"deleted": True})
        if "auth-fail" in url_str:
            return httpx.Response(401, text="Unauthorized")
        if "server-error" in url_str:
            return httpx.Response(500, text="Internal Server Error")
        if "empty" in url_str:
            return httpx.Response(204)

        return httpx.Response(200, json={"ok": True})

    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def mock_server_client(mock_transport: httpx.MockTransport) -> Iterator[None]:
    """Ensure taxcode_mcp.server.client uses mock_transport during test runs."""
    from taxcode_mcp import server

    orig_custom = server.client._custom_client
    server.client._custom_client = httpx.AsyncClient(
        transport=mock_transport, base_url="https://api.example.com"
    )
    yield
    server.client._custom_client = orig_custom
