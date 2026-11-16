"""Pytest fixtures and mocked HTTP transports."""

import importlib.util
import ipaddress
import os
import socket
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

# The floor test drives a child pytest with this plugin's pytester fixture.
pytest_plugins = ["pytester"]

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
    """A test opened a real socket connection or DNS lookup instead of the fake network."""


_LOOKUP_ALLOWED_NAMES = frozenset({"localhost", "localhost.", "ip6-localhost"})


def _local_addrinfo(host: str, *args: Any, **kwargs: Any) -> list[Any]:
    """Answer an IP literal or localhost without calling the resolver.

    Allowed lookups stay inside the process. A resolver that refuses every name,
    including literals, cannot turn this path into a network call.
    """
    port = args[0] if args else kwargs.get("port")
    socktype = socket.SOCK_STREAM
    if len(args) > 2 and args[2]:
        socktype = args[2]
    elif kwargs.get("type"):
        socktype = kwargs["type"]
    lowered = host.lower()
    if lowered == "ip6-localhost":
        address: str | ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address("::1")
    elif lowered in {"localhost", "localhost."}:
        address = ipaddress.ip_address("127.0.0.1")
    else:
        address = ipaddress.ip_address(host)
    family = socket.AF_INET if isinstance(address, ipaddress.IPv4Address) else socket.AF_INET6
    sockaddr: tuple[Any, ...] = (
        (str(address), port) if family == socket.AF_INET else (str(address), port, 0, 0)
    )
    proto = socket.IPPROTO_TCP if socktype == socket.SOCK_STREAM else 0
    return [(family, socktype, proto, "", sockaddr)]


def _refuse_real_lookup(host: Any, *args: Any, **kwargs: Any) -> Any:
    name = host.decode() if isinstance(host, bytes) else host
    if name is not None and name.lower() not in _LOOKUP_ALLOWED_NAMES:
        try:
            ipaddress.ip_address(name)
        except ValueError:
            raise RealNetworkUsedError(
                f"test tried a real DNS lookup of {name!r}; patch socket.getaddrinfo "
                "or use tests.fake_network"
            ) from None
    if not isinstance(name, str):
        raise RealNetworkUsedError(
            f"test tried a real DNS lookup of {name!r}; patch socket.getaddrinfo "
            "or use tests.fake_network"
        )
    return _local_addrinfo(name, *args, **kwargs)


def _refuse_real_connect(*args: Any, **_kwargs: Any) -> Any:
    raise RealNetworkUsedError(
        f"test tried to open a real network connection {args[1:] or args!r}; "
        "use tests.fake_network or a mock transport"
    )


@pytest.fixture(autouse=True)
def _no_real_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that dials a real host or looks up a real name (``live`` excepted).

    ``socket.socket.connect`` / ``connect_ex`` sit under every client stack (httpx and
    httpcore through anyio and asyncio, ``urllib``, ``socket.create_connection``), so an
    unmocked path fails here instead of reaching the network. ``socket.getaddrinfo`` is
    refused for any name other than localhost or an IP literal, and those are answered
    locally, so an unmocked lookup fails before it leaves the machine. The fake network
    patches httpcore's backends
    above this layer and never opens a socket; tests that check DNS patch
    ``socket.getaddrinfo`` themselves, which replaces this guard for that test.
    """
    if request.node.get_closest_marker("live") is not None:
        return
    monkeypatch.setattr(socket.socket, "connect", _refuse_real_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse_real_connect)
    monkeypatch.setattr(socket, "create_connection", _refuse_real_connect)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse_real_lookup)


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


LIVE_REQUIRED_ENV = ("TAXCODE_BASE_URL", "TAXCODE_API_KEY")

# Host of the default base URL (config.py). The skip reason names the variable, never this value.
_PLACEHOLDER_BASE_HOST = "api.example.com"


def live_skip_reason(environ: dict[str, str] | None = None) -> str | None:
    """Return why a live test must skip, or None when it may run.

    Live tests run only when ``MCP_LIVE_TESTS`` is ``1`` and every name in
    ``LIVE_REQUIRED_ENV`` is non-empty. A placeholder base URL skips too.
    The reason lists missing names and never a value.
    """
    env = os.environ if environ is None else environ
    if env.get("MCP_LIVE_TESTS") != "1":
        return "MCP_LIVE_TESTS is not 1"
    missing = [name for name in LIVE_REQUIRED_ENV if not env.get(name, "").strip()]
    if missing:
        return "missing or empty: " + ", ".join(missing)
    if _PLACEHOLDER_BASE_HOST in env.get("TAXCODE_BASE_URL", ""):
        return "TAXCODE_BASE_URL is the placeholder base URL"
    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip live items unless the opt-in, credentials, and a real base URL are set."""
    del config
    reason = live_skip_reason()
    if reason is None:
        return
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if item.get_closest_marker("live") is not None:
            item.add_marker(skip)


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
def mock_server_client(
    request: pytest.FixtureRequest, mock_transport: httpx.MockTransport
) -> Iterator[None]:
    """Ensure taxcode_mcp.server.client uses mock_transport during test runs.

    A live item keeps the real client. The autouse mock must not wrap it, or
    ``-m live`` never reaches an upstream.
    """
    if request.node.get_closest_marker("live") is not None:
        yield
        return
    from taxcode_mcp import server

    orig_custom = server.client._custom_client
    server.client._custom_client = httpx.AsyncClient(
        transport=mock_transport, base_url="https://api.example.com"
    )
    yield
    server.client._custom_client = orig_custom
