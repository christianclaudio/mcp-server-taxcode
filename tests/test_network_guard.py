"""The autouse network guard in ``tests/conftest.py`` refuses real DNS lookups.

It raises ``RealNetworkUsedError``, an ``AssertionError``; the class is matched by its base
because pytest loads ``conftest`` under a different module name than ``tests.conftest``.
"""

import socket

import pytest


def test_guard_refuses_lookup_of_a_real_name() -> None:
    with pytest.raises(AssertionError, match="real DNS lookup of 'example.com'"):
        socket.getaddrinfo("example.com", 443)


def test_guard_refuses_bytes_name() -> None:
    with pytest.raises(AssertionError, match="real DNS lookup"):
        socket.getaddrinfo(b"example.com", 443)


def test_guard_allows_ip_literals_and_localhost() -> None:
    assert socket.getaddrinfo("127.0.0.1", 443, type=socket.SOCK_STREAM)
    assert socket.getaddrinfo("::1", 443, type=socket.SOCK_STREAM)
    assert socket.getaddrinfo("localhost", 443, type=socket.SOCK_STREAM)
