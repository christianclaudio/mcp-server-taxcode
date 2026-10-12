"""Tests for async HTTP client functionality."""

import socket
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from taxcode_mcp.client import TemplateClient
from taxcode_mcp.errors import (
    AuthenticationError,
    RateLimitError,
    ResourceNotFoundError,
    TemplateError,
)


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
