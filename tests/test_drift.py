"""Unit tests for the enterprise OpenAPI and parameter drift detection engine."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tests.fake_network import FakeNetwork, answer

scripts_dir = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(scripts_dir))

from check_openapi_drift import (  # noqa: E402
    DEFAULT_TAXCODE_SPEC,
    is_parameter_deprecated,
    main,
    normalize_path,
    parse_spec,
    run_drift_check,
)


def test_normalize_path() -> None:
    assert normalize_path("items") == "/items"
    assert normalize_path("/items/") == "/items"
    assert normalize_path("/items/{id}") == "/items/{}"
    assert normalize_path("/v2/members/{memberId}/files") == "/v2/members/{}/files"
    assert normalize_path("/items?limit=50") == "/items"


def test_is_parameter_deprecated() -> None:
    assert is_parameter_deprecated({"name": "search", "deprecated": True}) is True
    assert (
        is_parameter_deprecated(
            {"name": "search", "description": "**[Deprecated]** Use email instead"}
        )
        is True
    )
    assert (
        is_parameter_deprecated(
            {"name": "search", "description": "This reaches end of support soon"}
        )
        is True
    )
    assert (
        is_parameter_deprecated({"name": "email", "description": "Filter by user email"}) is False
    )


def test_parse_spec() -> None:
    spec = {
        "paths": {
            "/v2/members": {
                "get": {
                    "parameters": [
                        {"name": "search", "in": "query", "deprecated": True},
                        {"name": "email", "in": "query", "required": True},
                    ]
                }
            }
        }
    }
    endpoints = parse_spec(spec)
    key = ("GET", "/v2/members")
    assert key in endpoints
    ep = endpoints[key]
    assert "search" in ep.deprecated_params
    assert "email" in ep.required_params


def test_run_drift_check_clean(tmp_path: Path) -> None:
    src_dir = Path(__file__).parent.parent / "src"
    code, lines = run_drift_check(DEFAULT_TAXCODE_SPEC, src_dir, strict=False)
    assert code == 0
    assert any("No breaking drift" in line for line in lines)


def test_run_drift_check_detects_deprecated_param(tmp_path: Path) -> None:
    # Create mock client file that passes a deprecated parameter "search"
    mock_src = tmp_path / "mock_client.py"
    mock_src.write_text(
        'client.request("GET", "items", params={"search": "query", "limit": 10})\n',
        encoding="utf-8",
    )
    code, lines = run_drift_check(DEFAULT_TAXCODE_SPEC, tmp_path, strict=False)
    assert code == 0  # Warning in non-strict
    assert any("DEPRECATED PARAMETER IN USE: 'search'" in line for line in lines)

    # In strict mode, it must fail
    code_strict, lines_strict = run_drift_check(DEFAULT_TAXCODE_SPEC, tmp_path, strict=True)
    assert code_strict == 1
    assert any("DEPRECATED PARAMETER IN USE: 'search'" in line for line in lines_strict)


def test_run_drift_check_detects_phantom_route(tmp_path: Path) -> None:
    mock_src = tmp_path / "mock_client.py"
    mock_src.write_text(
        'client.request("GET", "unknown_route")\n',
        encoding="utf-8",
    )
    code, lines = run_drift_check(DEFAULT_TAXCODE_SPEC, tmp_path, strict=False)
    assert code == 1
    assert any("PHANTOM ROUTE" in line for line in lines)


def test_main_cli_default() -> None:
    with patch("sys.argv", ["check_openapi_drift.py"]):
        assert main() == 0


def test_main_cli_spec_file(tmp_path: Path) -> None:
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(DEFAULT_TAXCODE_SPEC), encoding="utf-8")
    with patch("sys.argv", ["check_openapi_drift.py", "--spec-file", str(spec_file)]):
        assert main() == 0

    # Non-existent file
    missing = str(tmp_path / "missing.json")
    with patch("sys.argv", ["check_openapi_drift.py", "--spec-file", missing]):
        assert main() == 2


SPEC_HOST = "specs.vendor.test"
SPEC_URL = f"https://{SPEC_HOST}/openapi.json"
PUBLIC_IP = "93.184.215.14"


def _spec_network(**kwargs: Any) -> FakeNetwork:
    return FakeNetwork(body=kwargs.pop("body", DEFAULT_TAXCODE_SPEC), **kwargs)


def _run(url: str) -> int:
    with patch("sys.argv", ["check_openapi_drift.py", "--spec-url", url]):
        return main()


def test_spec_fetch_resolves_once_and_connects_to_the_checked_ip() -> None:
    """The fetch connects to the address the SSRF check saw; it never looks the host up again.

    The hostname still goes in the Host header and is the TLS server name, so SNI and
    certificate verification use it.
    """
    net = _spec_network()
    with net.patches(), patch("socket.getaddrinfo", return_value=answer(PUBLIC_IP)) as dns:
        assert _run(SPEC_URL) == 0
    assert dns.call_count == 1
    assert dns.call_args.args[0] == SPEC_HOST
    assert net.connects == [(PUBLIC_IP, 443)]
    assert net.tls_server_names == [SPEC_HOST]
    assert f"\r\nHost: {SPEC_HOST}\r\n".encode() in net.sent
    assert net.sent.startswith(b"GET /openapi.json HTTP/1.1\r\n")


def test_spec_fetch_ignores_environment_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    """httpx ignores env proxies when a transport is passed: the fetch connects directly."""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.vendor.test:3128")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.vendor.test:3128")
    net = _spec_network()
    with net.patches(), patch("socket.getaddrinfo", return_value=answer(PUBLIC_IP)) as dns:
        assert _run(SPEC_URL) == 0
    assert dns.call_count == 1
    assert net.connects == [(PUBLIC_IP, 443)]
    assert b"CONNECT " not in net.sent


def test_spec_fetch_refuses_placeholder_host_without_connecting(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An example.com placeholder skips DNS, so it has no checked address and is refused."""
    net = _spec_network()
    with net.patches(), patch("socket.getaddrinfo", return_value=answer(PUBLIC_IP)) as dns:
        assert _run("https://api.example.com/openapi.json") == 2
    dns.assert_not_called()
    assert net.attempts == []
    err = capsys.readouterr().err
    assert "refused to fetch spec URL" in err
    assert "no checked address" in err


def test_spec_fetch_errors_are_visible(capsys: pytest.CaptureFixture[str]) -> None:
    with patch(
        "check_openapi_drift.SSRFSafeAsyncTransport.handle_async_request",
        side_effect=RuntimeError("boom"),
    ):
        assert _run(SPEC_URL) == 2
    assert "Error fetching spec from URL: boom" in capsys.readouterr().err

    with (
        _spec_network(status=503).patches(),
        patch("socket.getaddrinfo", return_value=answer(PUBLIC_IP)),
    ):
        assert _run(SPEC_URL) == 2
    assert "Server error '503 Error'" in capsys.readouterr().err


def test_spec_fetch_does_not_follow_redirects(capsys: pytest.CaptureFixture[str]) -> None:
    """A 3xx is an error, not a hop to a target the SSRF check never saw."""
    net = _spec_network(status=302)
    with net.patches(), patch("socket.getaddrinfo", return_value=answer(PUBLIC_IP)):
        assert _run(SPEC_URL) == 2
    assert len(net.connects) == 1
    assert "Redirect response '302" in capsys.readouterr().err


def test_main_cli_spec_url_rejects_metadata_target(capsys: pytest.CaptureFixture[str]) -> None:
    """Caller-controlled spec URLs must fail closed before any HTTP fetch."""
    net = _spec_network()
    with net.patches(), patch("socket.getaddrinfo") as dns:
        assert _run("http://169.254.169.254/latest/meta-data") == 2
    dns.assert_not_called()
    assert net.attempts == []
    assert "refused to fetch spec URL" in capsys.readouterr().err


@pytest.mark.parametrize("reserved", ["10.0.0.5", "198.18.0.1", "169.254.169.254"])
def test_spec_fetch_rejects_name_resolving_to_reserved_address(
    reserved: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """A public-looking name that resolves to a non-global address is refused before connecting."""
    net = _spec_network()
    with (
        net.patches(),
        patch("socket.getaddrinfo", return_value=answer(PUBLIC_IP, reserved)) as dns,
    ):
        assert _run(SPEC_URL) == 2
        dns.assert_called_once()
    assert net.attempts == []
    err = capsys.readouterr().err
    assert "refused to fetch spec URL" in err
    assert reserved in err
