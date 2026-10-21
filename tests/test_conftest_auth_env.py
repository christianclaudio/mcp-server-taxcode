"""The test suite never sees an auth token or opt-in exported in the shell."""

import os

from taxcode_mcp import server
from taxcode_mcp.auth import ALLOW_UNAUTHENTICATED_BIND_ENV, AUTH_TOKEN_ENV
from tests.conftest import AUTH_ENV_NAMES


def test_auth_env_names_match_auth_module() -> None:
    assert set(AUTH_ENV_NAMES) == {AUTH_TOKEN_ENV, ALLOW_UNAUTHENTICATED_BIND_ENV}


def test_auth_env_cleared_for_each_test() -> None:
    assert AUTH_TOKEN_ENV not in os.environ
    assert ALLOW_UNAUTHENTICATED_BIND_ENV not in os.environ


def test_module_level_server_has_no_auth() -> None:
    assert server.mcp.auth is None


def test_real_network_connects_are_refused() -> None:
    """The autouse guard refuses any real socket connect, so no test can reach a host."""
    import socket

    import pytest

    with pytest.raises(AssertionError, match="real network connection"):
        socket.create_connection(("192.0.2.1", 443), timeout=0.01)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(AssertionError, match="real network connection"):
            sock.connect(("192.0.2.1", 443))
        with pytest.raises(AssertionError, match="real network connection"):
            sock.connect_ex(("192.0.2.1", 443))
