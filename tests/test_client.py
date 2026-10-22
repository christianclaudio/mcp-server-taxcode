"""Tests for async HTTP client functionality."""

import socket
from typing import Any
from unittest.mock import patch

import httpcore
import httpx
import pytest

from taxcode_mcp.client import TemplateClient
from taxcode_mcp.errors import (
    AuthenticationError,
    RateLimitError,
    ResourceNotFoundError,
    TemplateError,
)
from tests.fake_network import FakeNetwork, answer


@pytest.mark.asyncio
async def test_client_request_success(mock_transport: httpx.MockTransport) -> None:
    """Verify that successful client requests return expected parsed JSON."""
    async_client = httpx.AsyncClient(transport=mock_transport, base_url="https://api.example.com")
    client = TemplateClient(api_key="test-key", http_client=async_client)

    res = await client.request("GET", "health")
    assert res["status"] == "healthy"

    empty_res = await client.request("GET", "empty")
    assert empty_res == {}

    await client.close()


@pytest.mark.asyncio
async def test_client_path_sanitization() -> None:
    """Verify that path parameters are safely URL-encoded to prevent directory traversal."""
    client = TemplateClient()
    sanitized = client.sanitize_path_param("../../../etc/passwd")
    assert ".." not in sanitized
    assert "%2E%2E" in sanitized or "%2F" in sanitized


@pytest.mark.asyncio
async def test_client_errors() -> None:
    """Verify mapping of HTTP status codes to specialized TemplateError subclasses."""

    def error_transport(request: httpx.Request) -> httpx.Response:
        """Simulate HTTP errors based on requested path."""
        url = str(request.url)
        if "401" in url:
            return httpx.Response(401, text="Unauthorized")
        if "404" in url:
            return httpx.Response(404, text="Not Found")
        if "429" in url:
            return httpx.Response(429, text="Rate Limited")
        if "500" in url:
            return httpx.Response(500, text="Server Error")
        return httpx.Response(200)

    async_client = httpx.AsyncClient(
        transport=httpx.MockTransport(error_transport), base_url="https://api.example.com"
    )
    client = TemplateClient(max_retries=1, http_client=async_client)

    with pytest.raises(AuthenticationError):
        await client.request("GET", "401")

    with pytest.raises(ResourceNotFoundError):
        await client.request("GET", "404")

    with pytest.raises(RateLimitError):
        await client.request("GET", "429")

    with pytest.raises(TemplateError):
        await client.request("GET", "500")


@pytest.mark.asyncio
async def test_client_network_error() -> None:
    """Verify that connection errors raise TemplateError with helpful messaging."""

    def fail_transport(request: httpx.Request) -> httpx.Response:
        """Simulate low-level network failure."""
        raise httpx.ConnectError("Connection failed")

    async_client = httpx.AsyncClient(
        transport=httpx.MockTransport(fail_transport), base_url="https://api.example.com"
    )
    client = TemplateClient(max_retries=1, http_client=async_client)

    with pytest.raises(TemplateError) as exc_info:
        await client.request("GET", "network-fail")
    assert "Request failed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_client_lifecycle() -> None:
    """Verify that client initialization and authorization header propagation work."""
    client = TemplateClient(api_key="key123")
    c = await client.get_client()
    assert c is not None
    assert "Bearer key123" in c.headers.get("Authorization", "")
    await client.close()


def test_extract_list_safe_parsing() -> None:
    """Verify safe extraction of list values from polymorphic JSON payloads."""
    # Direct container without key
    assert TemplateClient.extract_list([1, 2, 3]) == [1, 2, 3]
    assert TemplateClient.extract_list({"key": "val"}) == []
    assert TemplateClient.extract_list(None) == []

    # Valid list extraction with key
    assert TemplateClient.extract_list({"items": [1, 2, 3]}, "items") == [1, 2, 3]

    # Non-list value under key
    assert TemplateClient.extract_list({"items": {"nested": "dict"}}, "items") == []
    assert TemplateClient.extract_list({"items": "scalar_string"}, "items") == []
    assert TemplateClient.extract_list({"items": None}, "items") == []

    # Missing key
    assert TemplateClient.extract_list({"other": [1]}, "items") == []

    # Non-dictionary root container with key
    assert TemplateClient.extract_list([1, 2, 3], "items") == []
    assert TemplateClient.extract_list(None, "items") == []
    assert TemplateClient.extract_list("invalid", "items") == []


def test_extract_dict_safe_parsing() -> None:
    """Verify safe extraction of dictionary values from polymorphic JSON payloads."""
    # Direct container without key
    assert TemplateClient.extract_dict({"id": 123}) == {"id": 123}
    assert TemplateClient.extract_dict([1, 2, 3]) == {}
    assert TemplateClient.extract_dict(None) == {}

    # Valid dict extraction with key
    assert TemplateClient.extract_dict({"data": {"id": 123}}, "data") == {"id": 123}

    # Non-dict value under key
    assert TemplateClient.extract_dict({"data": [1, 2, 3]}, "data") == {}
    assert TemplateClient.extract_dict({"data": "scalar_string"}, "data") == {}
    assert TemplateClient.extract_dict({"data": None}, "data") == {}

    # Missing key
    assert TemplateClient.extract_dict({"other": {}}, "data") == {}

    # Non-dictionary root container with key
    assert TemplateClient.extract_dict([1, 2, 3], "data") == {}
    assert TemplateClient.extract_dict(None, "data") == {}
    assert TemplateClient.extract_dict("invalid", "data") == {}


def test_validate_base_url_ssrf_protections() -> None:
    """Verify base URL validation protects against SSRF and non-global destinations."""
    from taxcode_mcp.client import _validate_base_url

    # Default fallback
    assert _validate_base_url("") == "https://api.example.com"

    # HTTPS enforcement
    with pytest.raises(ValueError, match="Only HTTPS is permitted"):
        _validate_base_url("http://api.example.com")

    # Missing hostname
    with pytest.raises(ValueError, match="missing hostname"):
        _validate_base_url("https://")

    # Internal/loopback hostnames
    for host in ["localhost", "service.local", "db.internal"]:
        with pytest.raises(ValueError, match="Blocked internal/loopback"):
            _validate_base_url(f"https://{host}")

    # Private, loopback, link-local, and metadata IP literals
    for ip in [
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "127.0.0.1",
        "127.0.0.2",
        "[::1]",
    ]:
        with pytest.raises(ValueError, match="Blocked private/reserved"):
            _validate_base_url(f"https://{ip}")

    # Credentials and malformed numeric hosts are rejected rather than fetched.
    with pytest.raises(ValueError, match="userinfo"):
        _validate_base_url("https://user:pass@api.example.com")
    with pytest.raises(ValueError, match="not a valid IP address"):
        _validate_base_url("https://08.8.8.8")

    # Valid globally routable public IP
    assert _validate_base_url("https://93.184.216.34") == "https://93.184.216.34"

    # Allowed hosts check
    with pytest.raises(ValueError, match="not in allowed hosts allowlist"):
        _validate_base_url("https://untrusted.example.com", allowed_hosts_str="api.example.com")
    assert (
        _validate_base_url("https://api.example.com", allowed_hosts_str="api.example.com")
        == "https://api.example.com"
    )

    # Valid global domain name via DNS resolution
    with patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    ):
        assert (
            _validate_base_url("https://api.customdomain.org", check_dns=True)
            == "https://api.customdomain.org"
        )

    # DNS resolving to private IP
    with patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443))],
    ):
        with pytest.raises(ValueError, match="resolving to private/reserved IP"):
            _validate_base_url("https://malicious-rebinding.com", check_dns=True)

    # DNS resolution failure (fail-closed)
    with patch("socket.getaddrinfo", side_effect=socket.gaierror("Name or service not known")):
        with pytest.raises(ValueError, match="Could not resolve hostname in base URL"):
            _validate_base_url("https://unresolvable-domain.com", check_dns=True)

    # Private IP literal in _validate_hostname_dns
    from taxcode_mcp.client import _validate_hostname_dns

    with pytest.raises(ValueError, match="Blocked private/reserved"):
        _validate_hostname_dns("10.0.0.1")

    # Example.com and global public IP in _validate_hostname_dns
    _validate_hostname_dns("example.com")
    _validate_hostname_dns("api.example.com")
    _validate_hostname_dns("93.184.216.34")

    with pytest.raises(ValueError, match="not a valid IP address"):
        _validate_hostname_dns("08.8.8.8")


@pytest.mark.asyncio
async def test_ssrf_safe_async_transport() -> None:
    """Verify SSRFSafeAsyncTransport blocks outbound requests to private/reserved destinations."""
    from taxcode_mcp.client import SSRFSafeAsyncTransport
    from taxcode_mcp.errors import TemplateError

    transport = SSRFSafeAsyncTransport()

    # Valid public resolution passes to base transport and offloads to worker thread
    import threading

    loop_thread = threading.get_ident()
    dns_thread = None

    def fake_getaddrinfo(*args: Any, **kwargs: Any) -> list[Any]:
        """Record the calling thread identifier and return a valid public IPv4 address."""
        nonlocal dns_thread
        dns_thread = threading.get_ident()
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    with patch("socket.getaddrinfo", side_effect=fake_getaddrinfo):
        with patch.object(
            httpx.AsyncHTTPTransport,
            "handle_async_request",
            return_value=httpx.Response(200, json={"ok": True}),
        ):
            req = httpx.Request("GET", "https://api.customdomain.org/data")
            resp = await transport.handle_async_request(req)
            assert resp.status_code == 200
            assert dns_thread is not None
            assert dns_thread != loop_thread

    # Private IP resolution raises TemplateError at request time
    with patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.1", 443))],
    ):
        req = httpx.Request("GET", "https://malicious.com/data")
        with pytest.raises(TemplateError, match="SSRF validation blocked request"):
            await transport.handle_async_request(req)


V6 = "2606:2800:21f:cb07:6820:80da:af6b:8b2c"
V4 = "93.184.216.34"


@pytest.mark.asyncio
async def test_ssrf_safe_async_transport_connects_to_the_checked_address() -> None:
    """One lookup; the connect goes to a checked address; the URL keeps the hostname.

    The Host header and the TLS server name (SNI and certificate verification) are the
    hostname, and the caller's request is left as it was.
    """
    from taxcode_mcp.client import SSRFSafeAsyncTransport

    net = FakeNetwork()
    request = httpx.Request("POST", "https://api.customdomain.org:8443/data?q=1", json={"a": 1})
    async with SSRFSafeAsyncTransport() as transport:
        with net.patches(), patch("socket.getaddrinfo", return_value=answer(V6, V4)) as dns:
            response = await transport.handle_async_request(request)
            await response.aread()
    assert response.status_code == 200
    dns.assert_called_once()
    assert dns.call_args.args[0] == "api.customdomain.org"
    assert net.connects == [(V6, 8443)]
    assert net.tls_server_names == ["api.customdomain.org"]
    assert net.sent.startswith(b"POST /data?q=1 HTTP/1.1\r\n")
    assert b"\r\nHost: api.customdomain.org:8443\r\n" in net.sent
    assert net.sent.endswith(b'{"a":1}')
    assert str(request.url) == "https://api.customdomain.org:8443/data?q=1"
    assert "sni_hostname" not in request.extensions


@pytest.mark.asyncio
async def test_ssrf_safe_async_transport_falls_back_to_the_next_checked_address() -> None:
    """A failed connect moves to the next checked address in resolver order, never further."""
    from taxcode_mcp.client import SSRFSafeAsyncTransport

    url = "https://api.customdomain.org/data"
    net = FakeNetwork(fail={V6})
    async with SSRFSafeAsyncTransport() as transport:
        with net.patches(), patch("socket.getaddrinfo", return_value=answer(V6, V4)) as dns:
            response = await transport.handle_async_request(httpx.Request("GET", url))
            await response.aread()
    assert response.status_code == 200
    dns.assert_called_once()
    assert net.attempts == [(V6, 443), (V4, 443)]
    assert net.connects == [(V4, 443)]
    assert net.tls_server_names == ["api.customdomain.org"]

    # Every checked address failing is a connect error; nothing unchecked is tried.
    net = FakeNetwork(fail={V6, V4})
    async with SSRFSafeAsyncTransport() as transport:
        with net.patches(), patch("socket.getaddrinfo", return_value=answer(V6, V4)) as dns:
            with pytest.raises(httpx.ConnectError, match=f"connect to {V4} failed"):
                await transport.handle_async_request(httpx.Request("GET", url))
    dns.assert_called_once()
    assert net.attempts == [(V6, 443), (V4, 443)]
    assert net.connects == []


@pytest.mark.asyncio
async def test_ssrf_safe_async_transport_pools_connections_per_hostname() -> None:
    """Two hostnames on one IP never share a TLS connection; one hostname reuses its own."""
    from taxcode_mcp.client import SSRFSafeAsyncTransport

    net = FakeNetwork()
    async with SSRFSafeAsyncTransport() as transport:
        with net.patches(), patch("socket.getaddrinfo", return_value=answer(V4)) as dns:
            for host in ("a.customdomain.org", "b.customdomain.org", "a.customdomain.org"):
                request = httpx.Request("GET", f"https://{host}/{host[0]}")
                response = await transport.handle_async_request(request)
                await response.aread()
                await response.aclose()
    # The address is checked for every request, pooled or not.
    assert [c.args[0] for c in dns.call_args_list] == [
        "a.customdomain.org",
        "b.customdomain.org",
        "a.customdomain.org",
    ]
    assert net.connects == [(V4, 443), (V4, 443)]
    assert net.tls_server_names == ["a.customdomain.org", "b.customdomain.org"]
    assert net.requests_by_connection() == [
        [b"GET /a HTTP/1.1", b"GET /a HTTP/1.1"],
        [b"GET /b HTTP/1.1"],
    ]


@pytest.mark.asyncio
async def test_ssrf_safe_async_transport_refuses_a_host_with_no_checked_address() -> None:
    """Placeholders have no checked address and are refused; IP literals need no lookup."""
    from taxcode_mcp.client import SSRFSafeAsyncTransport, _PinnedNetworkBackend

    net = FakeNetwork()
    async with SSRFSafeAsyncTransport() as transport:
        with net.patches(), patch("socket.getaddrinfo") as dns:
            # example.com placeholders skip DNS, so there is nothing checked to dial.
            with pytest.raises(TemplateError, match="api.example.com: no checked address"):
                await transport.handle_async_request(
                    httpx.Request("GET", "https://api.example.com/x")
                )
            assert net.attempts == []
            # An IP literal is its own checked address.
            response = await transport.handle_async_request(httpx.Request("GET", f"https://{V4}/x"))
            await response.aread()
            # A request with no host is passed straight to httpx, which rejects it.
            with pytest.raises(httpx.UnsupportedProtocol):
                await transport.handle_async_request(httpx.Request("GET", "/relative"))
    dns.assert_not_called()
    assert net.connects == [(V4, 443)]
    assert net.tls_server_names == [V4]

    # Outside a transport request nothing is pinned, so the backend dials nothing.
    backend = _PinnedNetworkBackend(httpcore.AsyncMockBackend([]))
    with pytest.raises(TemplateError, match="no checked address"):
        await backend.connect_tcp("api.customdomain.org", 443)
    await backend.sleep(0)


@pytest.mark.asyncio
async def test_ssrf_safe_async_transport_checks_the_name_it_dials() -> None:
    """An internationalized host is checked under the ASCII (IDNA) name used to connect.

    The check, the pin and the TLS server name all use ``raw_host``, so they cannot
    disagree on which name was resolved.
    """
    from taxcode_mcp.client import SSRFSafeAsyncTransport

    ascii_host = "xn--fa-hia.customdomain.org"
    net = FakeNetwork()
    async with SSRFSafeAsyncTransport() as transport:
        with net.patches(), patch("socket.getaddrinfo", return_value=answer(V4)) as dns:
            response = await transport.handle_async_request(
                httpx.Request("GET", "https://fa\u00df.customdomain.org/x")
            )
            await response.aread()
    dns.assert_called_once()
    assert dns.call_args.args[0] == ascii_host
    assert net.connects == [(V4, 443)]
    assert net.tls_server_names == [ascii_host]
    assert f"\r\nHost: {ascii_host}\r\n".encode() in net.sent


@pytest.mark.asyncio
async def test_ssrf_safe_async_transport_refuses_unix_sockets() -> None:
    """The pinned backend never dials a Unix socket."""
    from taxcode_mcp.client import _PinnedNetworkBackend

    backend = _PinnedNetworkBackend(httpcore.AsyncMockBackend([]))
    with pytest.raises(TemplateError, match="unix sockets are not allowed"):
        await backend.connect_unix_socket("/var/run/docker.sock")


class _TimedNetwork(httpcore.AsyncNetworkBackend):
    """Fake connect with a fake clock: no socket, no sleep, time moves only on a connect.

    Each address either hangs until its timeout (``"hang"``), is refused after ``cost``
    seconds (``"fail"``), or connects after ``cost`` seconds (``"ok"``).
    """

    def __init__(self, plan: dict[str, tuple[str, float]]) -> None:
        self.plan = plan
        self.now = 0.0
        self.attempts: list[tuple[str, float | None]] = []

    def clock(self) -> float:
        return self.now

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        self.attempts.append((host, timeout))
        kind, cost = self.plan[host]
        if kind == "hang":
            assert timeout is not None, "a hanging connect with no timeout never returns"
            self.now += timeout
            raise httpcore.ConnectTimeout(f"connect to {host} timed out")
        if timeout is not None and cost >= timeout:
            self.now += timeout
            raise httpcore.ConnectTimeout(f"connect to {host} timed out")
        self.now += cost
        if kind == "fail":
            raise httpcore.ConnectError(f"connect to {host} failed")
        return httpcore.AsyncMockStream([])


async def _pinned_connect(
    net: _TimedNetwork, ips: tuple[str, ...], timeout: float | None
) -> httpcore.AsyncNetworkStream:
    from taxcode_mcp.client import _PINNED_IPS, _PinnedNetworkBackend

    backend = _PinnedNetworkBackend(net)
    backend._clock = net.clock
    token = _PINNED_IPS.set({"api.customdomain.org": ips})
    try:
        return await backend.connect_tcp("api.customdomain.org", 443, timeout=timeout)
    finally:
        _PINNED_IPS.reset(token)


@pytest.mark.asyncio
async def test_pinned_connect_shares_one_deadline_across_hanging_addresses() -> None:
    """Template v1.8.2: addresses that hang share one connect deadline, not a full timeout each."""
    a, b, c = "203.0.113.1", "203.0.113.2", "203.0.113.3"
    net = _TimedNetwork({a: ("fail", 2.0), b: ("hang", 0.0), c: ("hang", 0.0)})
    with pytest.raises(httpcore.ConnectTimeout, match=f"connect to {b} timed out"):
        await _pinned_connect(net, (a, b, c), timeout=10.0)
    # Before the fix: three attempts of 10s each, 22s in total.
    assert net.now <= 10.0
    assert net.attempts == [(a, 10.0), (b, 8.0)]


@pytest.mark.asyncio
async def test_pinned_connect_moves_on_after_a_fast_failure() -> None:
    """A refused first address leaves the rest of the deadline for the next one."""
    a, b = "203.0.113.1", "203.0.113.2"
    net = _TimedNetwork({a: ("fail", 0.5), b: ("ok", 1.0)})
    stream = await _pinned_connect(net, (a, b), timeout=10.0)
    assert isinstance(stream, httpcore.AsyncMockStream)
    assert net.attempts == [(a, 10.0), (b, 9.5)]
    assert net.now == 1.5


@pytest.mark.asyncio
async def test_pinned_connect_stops_when_the_deadline_is_spent() -> None:
    """Once the deadline passes no other address is tried; the last error is raised as before."""
    a, b, c, d = "203.0.113.1", "203.0.113.2", "203.0.113.3", "203.0.113.4"
    net = _TimedNetwork({ip: ("fail", 4.0) for ip in (a, b, c, d)})
    with pytest.raises(httpcore.ConnectTimeout, match=f"connect to {c} timed out"):
        await _pinned_connect(net, (a, b, c, d), timeout=10.0)
    assert net.attempts == [(a, 10.0), (b, 6.0), (c, 2.0)]
    assert net.now == 10.0

    # With no connect timeout there is no deadline: every address is tried, as before.
    net = _TimedNetwork({a: ("fail", 4.0), b: ("fail", 4.0)})
    with pytest.raises(httpcore.ConnectError, match=f"connect to {b} failed"):
        await _pinned_connect(net, (a, b), timeout=None)
    assert net.attempts == [(a, None), (b, None)]


@pytest.mark.asyncio
async def test_ssrf_safe_async_transport_connect_timeout_is_one_deadline() -> None:
    """Through httpx: the request's connect timeout bounds the whole call, and the error
    a caller sees is still ``httpx.ConnectTimeout``."""
    from taxcode_mcp.client import SSRFSafeAsyncTransport, _PinnedNetworkBackend

    net = _TimedNetwork({V6: ("hang", 0.0), V4: ("hang", 0.0)})

    async def connect(self: Any, host: str, port: int, **kwargs: Any) -> Any:
        return await net.connect_tcp(host, port, **kwargs)

    request = httpx.Request(
        "GET",
        "https://api.customdomain.org/data",
        extensions={"timeout": httpx.Timeout(10.0).as_dict()},
    )
    async with SSRFSafeAsyncTransport() as transport:
        pool = vars(transport)["_pool"]
        (backend,) = [v for v in vars(pool).values() if isinstance(v, _PinnedNetworkBackend)]
        backend._clock = net.clock
        with (
            patch.object(httpcore.AnyIOBackend, "connect_tcp", connect),
            patch("socket.getaddrinfo", return_value=answer(V6, V4)) as dns,
        ):
            with pytest.raises(httpx.ConnectTimeout, match=f"connect to {V6} timed out"):
                await transport.handle_async_request(request)
    dns.assert_called_once()
    assert net.attempts == [(V6, 10.0)]
    assert net.now == 10.0


def test_ssrf_safe_async_transport_builds_its_pool_through_public_httpcore_api() -> None:
    """Guard for dependency bumps: fail loudly if the pinned pool stops being used.

    ``network_backend`` is a documented ``httpcore.AsyncConnectionPool`` argument; httpx
    serves requests through the transport's ``_pool``. The connect tests above fail too if
    httpx stops doing that, but this names the cause.
    """
    import inspect

    from taxcode_mcp.client import SSRFSafeAsyncTransport, _PinnedNetworkBackend

    assert "network_backend" in inspect.signature(httpcore.AsyncConnectionPool).parameters
    transport = SSRFSafeAsyncTransport(limits=httpx.Limits(max_connections=7))
    pool = vars(transport).get("_pool")
    assert isinstance(pool, httpcore.AsyncConnectionPool), (
        "httpx.AsyncHTTPTransport no longer serves requests through self._pool; "
        "SSRFSafeAsyncTransport must be rebuilt for this httpx version"
    )
    assert "_pool" in inspect.getsource(httpx.AsyncHTTPTransport.handle_async_request)
    backends = [v for v in vars(pool).values() if isinstance(v, _PinnedNetworkBackend)]
    assert len(backends) == 1


@pytest.mark.asyncio
async def test_client_request_end_to_end_through_the_pin() -> None:
    """TemplateClient's own transport: one lookup per request, connect to the checked IP."""
    net = FakeNetwork(body={"items": []})
    client = TemplateClient(base_url="https://api.customdomain.org", max_retries=0)
    try:
        with net.patches(), patch("socket.getaddrinfo", return_value=answer(V4)) as dns:
            assert await client.request("GET", "items") == {"items": []}
            assert await client.request("GET", "health") == {"items": []}
    finally:
        await client.close()
    assert [c.args[0] for c in dns.call_args_list] == ["api.customdomain.org"] * 2
    # The second request reuses the pooled connection to the same checked address.
    assert net.connects == [(V4, 443)]
    assert net.tls_server_names == ["api.customdomain.org"]
    assert b"\r\nHost: api.customdomain.org\r\n" in net.sent


@pytest.mark.asyncio
@pytest.mark.parametrize("reserved", ["10.0.0.5", "198.18.0.1", "169.254.169.254"])
async def test_client_refuses_a_name_resolving_to_a_reserved_address(reserved: str) -> None:
    """A public-looking name that resolves to a non-global address is never dialled."""
    net = FakeNetwork()
    client = TemplateClient(base_url="https://api.customdomain.org", max_retries=0)
    try:
        with net.patches(), patch("socket.getaddrinfo", return_value=answer(V4, reserved)):
            with pytest.raises(TemplateError, match=reserved.replace(".", r"\.")):
                await client.request("GET", "items")
    finally:
        await client.close()
    assert net.attempts == []


@pytest.mark.asyncio
async def test_401_json_body_with_credential_keeps_its_shape() -> None:
    """#69 item 3: a 401 JSON body is redacted value by value and still parses."""
    import json

    body = {"message": "login failed password=hunter2 x", "code": 7, "api_key": "k-123"}

    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json=body)

    async_client = httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="https://api.example.com"
    )
    client = TemplateClient(max_retries=0, http_client=async_client)
    with pytest.raises(AuthenticationError) as exc:
        await client.request("GET", "me")
    message = exc.value.message
    assert message.startswith("HTTP 401: ")
    assert json.loads(message.removeprefix("HTTP 401: ")) == {
        "message": "login failed password=[REDACTED]",
        "code": 7,
        "api_key": "[REDACTED]",
    }
    assert "hunter2" not in message and "k-123" not in message
    assert str(exc.value) == message
