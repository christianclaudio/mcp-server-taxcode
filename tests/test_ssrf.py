"""Unit tests for the shared outbound SSRF helper."""

from __future__ import annotations

import socket
from typing import Any
from unittest.mock import patch

import pytest

from taxcode_mcp import validate_outbound_url
from taxcode_mcp.ssrf import SSRFValidationError, validate_resolved_host


def test_allows_public_https_url() -> None:
    """Public HTTPS URLs are returned unchanged."""
    url = "https://api.example.com/v1/items"
    assert validate_outbound_url(url) == url
    assert validate_outbound_url(" https://93.184.216.34/health ") == "https://93.184.216.34/health"
    assert validate_outbound_url("https://[2001:4860:4860::8888]/dns") == (
        "https://[2001:4860:4860::8888]/dns"
    )


def test_blocks_private_link_local_loopback_and_metadata() -> None:
    """Non-global and metadata destinations are rejected with an explicit error."""
    blocked = [
        "https://10.0.0.1/latest",
        "https://172.16.0.1/",
        "https://192.168.1.1/",
        "https://127.0.0.2/",
        "https://127.0.0.1/",
        "https://[::1]/",
        "https://169.254.169.254/latest/meta-data",
        "https://169.254.1.1/",
        "https://[fd00:ec2::254]/",
        "https://0.0.0.0/",
        "https://224.0.0.1/",
        "https://100.64.0.1/",
        "https://[::ffff:7f00:1]/",
        "https://localhost/admin",
        "https://metadata.goog/",
        "https://printer.local/",
        "https://db.internal/",
        "https://2130706433/",
        "https://127.1/",
        "https://127.0.1/",
        "https://0x7f000001/",
        "https://0177.0.0.1/",
        "https://0x7f.0.0.1/",
    ]
    for url in blocked:
        with pytest.raises(SSRFValidationError, match="Blocked") as exc_info:
            validate_outbound_url(url)
        assert str(exc_info.value)


def test_blocks_non_http_schemes_and_empty_urls() -> None:
    """Weird schemes and empty input fail closed instead of being fetched."""
    schemes = (
        "file:///etc/passwd",
        "gopher://evil.com/",
        "ftp://files.example.com/a",
        "javascript:alert(1)",
        "example.com",
    )
    for url in schemes:
        with pytest.raises(SSRFValidationError, match="scheme") as exc_info:
            validate_outbound_url(url)
        assert exc_info.value.reason == "scheme"

    with pytest.raises(SSRFValidationError, match="empty") as empty:
        validate_outbound_url("   ")
    assert empty.value.reason == "empty"

    with pytest.raises(SSRFValidationError, match="illegal characters"):
        validate_outbound_url("https://api.example.com/a\\b")
    with pytest.raises(SSRFValidationError, match="illegal characters"):
        validate_outbound_url("https://api.example.com/\npath")

    with pytest.raises(SSRFValidationError, match="missing a hostname"):
        validate_outbound_url("https://")

    with pytest.raises(SSRFValidationError, match="userinfo"):
        validate_outbound_url("https://user:secret@api.example.com/data")


def test_allowlist_miss_and_http_rules() -> None:
    """Allowlists are opt-in, and HTTP is limited to hosts that explicitly require it."""
    with pytest.raises(SSRFValidationError, match="allowlist") as missed:
        validate_outbound_url(
            "https://untrusted.example.com/v1",
            allowed_hosts=["api.example.com"],
        )
    assert missed.value.reason == "allowlist"
    assert missed.value.hostname == "untrusted.example.com"

    assert (
        validate_outbound_url(
            "https://API.Example.COM/v1",
            allowed_hosts="api.example.com, other.example.com",
        )
        == "https://API.Example.COM/v1"
    )

    with pytest.raises(SSRFValidationError, match="allowlist"):
        validate_outbound_url("https://api.example.com/", allowed_hosts=[])

    with pytest.raises(SSRFValidationError, match="must use HTTPS") as http_default:
        validate_outbound_url("http://api.example.com/health")
    assert http_default.value.reason == "insecure_http"

    with pytest.raises(SSRFValidationError, match="must use HTTPS"):
        validate_outbound_url(
            "http://api.example.com/health",
            allowed_hosts=["api.example.com", "legacy.example.com"],
            http_required_hosts=["legacy.example.com"],
        )

    allowed = validate_outbound_url(
        "http://legacy.example.com/health",
        allowed_hosts=["legacy.example.com"],
        http_required_hosts="legacy.example.com",
    )
    assert allowed == "http://legacy.example.com/health"

    # Without a general allowlist, http_required_hosts is the HTTP allowlist.
    assert (
        validate_outbound_url(
            "http://legacy.example.com/health",
            http_required_hosts=["legacy.example.com"],
        )
        == "http://legacy.example.com/health"
    )

    with pytest.raises(SSRFValidationError, match="allowlist"):
        validate_outbound_url(
            "http://legacy.example.com/health",
            allowed_hosts=["api.example.com"],
            http_required_hosts=["legacy.example.com"],
        )


def test_malformed_numeric_hosts_do_not_pass() -> None:
    """Numeric hosts that are not legal addresses are rejected, not treated as names."""
    for host in ("08.8.8.8", "999.1", "4294967296"):
        with pytest.raises(SSRFValidationError, match="not a valid IP address") as exc_info:
            validate_outbound_url(f"https://{host}/")
        assert exc_info.value.reason == "invalid_ip"


def test_validate_resolved_host_blocks_rebinding_and_literals() -> None:
    """Resolver checks fail closed for private answers, empty answers, and literals."""
    validate_resolved_host("example.com")
    validate_resolved_host("api.example.com")
    validate_resolved_host("93.184.216.34")

    with pytest.raises(SSRFValidationError, match="Blocked"):
        validate_resolved_host("10.0.0.1")
    with pytest.raises(SSRFValidationError, match="not a valid IP address"):
        validate_resolved_host("08.8.8.8")

    with patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    ):
        validate_resolved_host("api.customdomain.org")

    with patch(
        "socket.getaddrinfo",
        return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443)),
        ],
    ):
        with pytest.raises(SSRFValidationError, match="10.0.0.5") as rebound:
            validate_resolved_host("malicious-rebinding.com")
        assert rebound.value.resolved_ip == "10.0.0.5"

    with patch("socket.getaddrinfo", return_value=[]):
        with pytest.raises(SSRFValidationError, match="Could not resolve") as empty:
            validate_resolved_host("no-addresses.example.org")
        assert empty.value.reason == "dns_failure"

    with patch("socket.getaddrinfo", side_effect=socket.gaierror("Name or service not known")):
        with pytest.raises(SSRFValidationError, match="Could not resolve") as failed:
            validate_resolved_host("unresolvable-domain.com")
        assert failed.value.reason == "dns_failure"


def test_resolved_host_patch_accepts_arbitrary_addrinfo_shape() -> None:
    """The resolver only needs the sockaddr tuple from getaddrinfo."""

    def fake_getaddrinfo(*_args: Any, **_kwargs: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]

    with patch("socket.getaddrinfo", side_effect=fake_getaddrinfo):
        validate_resolved_host("dns.google")
