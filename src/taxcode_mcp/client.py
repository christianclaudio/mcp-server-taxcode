"""Asynchronous HTTP client with connection pooling, retries, and path sanitization."""

import asyncio
import contextvars
import random
import ssl
import urllib.parse
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

import httpcore
import httpx

from taxcode_mcp import __version__
from taxcode_mcp.config import settings
from taxcode_mcp.errors import (
    AuthenticationError,
    RateLimitError,
    ResourceNotFoundError,
    TemplateError,
    redact_secrets,
)
from taxcode_mcp.ssrf import SSRFValidationError, validate_outbound_url, validate_resolved_host


def _legacy_base_url_message(exc: SSRFValidationError) -> str:
    """Map shared SSRF errors onto the base-URL messages callers already match."""
    host = exc.hostname or ""
    if exc.reason in {"scheme", "insecure_http"}:
        return "Only HTTPS is permitted for base URL."
    if exc.reason == "missing_hostname":
        return "Invalid base URL: missing hostname."
    if exc.reason == "blocked_hostname":
        return f"Blocked internal/loopback hostname in base URL: {host}"
    if exc.reason == "allowlist":
        return f"Hostname '{host}' is not in allowed hosts allowlist."
    if exc.reason == "blocked_ip":
        return f"Blocked private/reserved IP address in base URL: {host}"
    return str(exc)


def _validate_base_url(
    url: str,
    allowed_hosts_str: str | None = None,
    check_dns: bool = False,
) -> str:
    """Validate a config-controlled base URL via the shared SSRF helper.

    Template base URLs stay HTTPS-only. Blank input falls back to the configured
    default. DNS rebinding checks stay opt-in here and always run again at
    request time inside ``SSRFSafeAsyncTransport``.
    """
    if not url:
        return settings.BASE_URL.rstrip("/")

    allowed_raw = allowed_hosts_str if allowed_hosts_str is not None else settings.ALLOWED_HOSTS
    allowed_hosts = allowed_raw if allowed_raw.strip() else None
    try:
        validated = validate_outbound_url(url, allowed_hosts=allowed_hosts)
    except SSRFValidationError as exc:
        raise ValueError(_legacy_base_url_message(exc)) from exc

    if check_dns:
        hostname = urlparse(validated).hostname
        if hostname is not None:
            _validate_hostname_dns(hostname)
    return validated.rstrip("/")


def _validate_hostname_dns(hostname: str) -> tuple[str, ...]:
    """Validate resolved DNS IP addresses to defend against private IP binding and DNS rebinding.

    Returns the checked addresses (see ``validate_resolved_host``).
    """
    try:
        return validate_resolved_host(hostname)
    except SSRFValidationError as exc:
        if exc.reason == "dns_failure":
            raise ValueError(f"Could not resolve hostname in base URL: {hostname}") from exc
        if exc.resolved_ip is not None:
            raise ValueError(
                f"Blocked hostname '{hostname}' resolving to private/reserved IP: {exc.resolved_ip}"
            ) from exc
        if exc.reason == "blocked_ip":
            raise ValueError(f"Blocked private/reserved IP address: {hostname}") from exc
        raise ValueError(str(exc)) from exc


# ---- Connect to the checked IP --------------------------------------------------------
# The transport checks the host once per request and hands httpcore the addresses that
# passed; the network backend dials one of them, so httpcore never resolves the name
# again. The URL keeps the hostname, so the Host header, SNI and certificate
# verification all use the original name, and httpcore pools connections per hostname.

_PINNED_IPS: contextvars.ContextVar[dict[str, tuple[str, ...]] | None] = contextvars.ContextVar(
    "taxcode_pinned_ips", default=None
)


class _PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Dial only an address that passed ``validate_resolved_host`` for the requested host."""

    def __init__(self, inner: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        pins = _PINNED_IPS.get() or {}
        ips = pins.get(host.lower())
        if not ips:
            raise TemplateError(f"SSRF validation blocked request to {host}: no checked address")
        last: Exception | None = None
        for ip in ips:
            try:
                return await self._inner.connect_tcp(
                    ip,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout, OSError) as exc:
                last = exc
        assert last is not None
        raise last

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise TemplateError("SSRF validation blocked request: unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class SSRFSafeAsyncTransport(httpx.AsyncHTTPTransport):
    """Async transport that connects only to an address checked for the request's host.

    The hostname is resolved once per request, in a worker thread, and every address is
    checked. The connection pool is built with a network backend that dials only those
    addresses, in resolver order, so a second DNS answer cannot send the request
    somewhere unchecked. A host with no checked address (an ``example.com`` placeholder)
    is refused at connect. Unix sockets are refused.

    httpx ignores env proxies whenever a transport is passed
    (``allow_env_proxies = trust_env and transport is None``), so connections always go
    direct through the pin; egress-proxy deployments need a proxy-aware transport, which
    isn't provided.
    """

    def __init__(
        self,
        verify: ssl.SSLContext | str | bool = True,
        cert: Any = None,
        trust_env: bool = True,
        http1: bool = True,
        http2: bool = False,
        limits: httpx.Limits | None = None,
        retries: int = 0,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> None:
        limits = limits or httpx.Limits(max_connections=100, max_keepalive_connections=20)
        ssl_context = httpx.create_ssl_context(verify=verify, cert=cert, trust_env=trust_env)
        super().__init__(verify=ssl_context, limits=limits)
        # httpcore documents ``network_backend`` as a public AsyncConnectionPool argument
        # (https://www.encode.io/httpcore/network-backends/). httpx.AsyncHTTPTransport does
        # not take one, so the pool it serves requests through is rebuilt here with the
        # same settings plus the pinned backend.
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl_context,
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            http1=http1,
            http2=http2,
            retries=retries,
            local_address=local_address,
            socket_options=socket_options,
            network_backend=_PinnedNetworkBackend(),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Check the destination off the event loop, then connect only to a checked address."""
        # httpcore dials, and TLS checks, the ASCII (IDNA) form of the host, so the check
        # resolves that same name and the pin is keyed by it.
        hostname = request.url.raw_host.decode("ascii")
        if not hostname:
            return await super().handle_async_request(request)
        try:
            ips = await asyncio.to_thread(_validate_hostname_dns, hostname)
        except ValueError as exc:
            raise TemplateError(f"SSRF validation blocked request to {hostname}: {exc}") from exc
        token = _PINNED_IPS.set({hostname.lower(): ips})
        try:
            return await super().handle_async_request(request)
        finally:
            _PINNED_IPS.reset(token)


class TemplateClient:
    """Hardened async client for interacting with upstream APIs."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        raw_url = (base_url or settings.BASE_URL).rstrip("/")
        self.base_url = _validate_base_url(raw_url, check_dns=False)
        self.api_key = api_key or settings.API_KEY
        self.timeout = timeout if timeout is not None else settings.TIMEOUT_SECONDS
        self.max_retries = max_retries if max_retries is not None else settings.MAX_RETRIES
        self._custom_client = http_client
        self._client: httpx.AsyncClient | None = None

    async def get_client(self) -> httpx.AsyncClient:
        """Retrieve or initialize persistent AsyncClient pool."""
        if self._custom_client is not None:
            return self._custom_client
        if self._client is None or self._client.is_closed:
            headers = {
                "Accept": "application/json",
                "User-Agent": f"mcp-server-taxcode/{__version__}",
            }
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            transport = SSRFSafeAsyncTransport(
                verify=True,
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
            )
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=httpx.Timeout(self.timeout),
                transport=transport,
            )
        return self._client

    async def close(self) -> None:
        """Close underlying connection pool."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def sanitize_path_param(self, segment: str) -> str:
        """Encode path parameters to prevent directory traversal and path injection."""
        return urllib.parse.quote(str(segment), safe="").replace("..", "%2E%2E")

    @staticmethod
    def extract_list(container: Any, key: str | None = None) -> list[Any]:
        """Safely extract a list from a polymorphic upstream JSON response or field."""
        if key is not None:
            target = container.get(key) if isinstance(container, dict) else None
        else:
            target = container
        return target if isinstance(target, list) else []

    @staticmethod
    def extract_dict(container: Any, key: str | None = None) -> dict[str, Any]:
        """Safely extract a dictionary from a polymorphic upstream JSON response or field."""
        if key is not None:
            target = container.get(key) if isinstance(container, dict) else None
        else:
            target = container
        return target if isinstance(target, dict) else {}

    async def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute request with exponential backoff and randomized jitter."""
        client = await self.get_client()
        url = f"{self.base_url}/{path.lstrip('/')}"
        last_exception: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                response = await client.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json_data,
                )

                if response.status_code in (401, 403):
                    raise AuthenticationError(f"HTTP {response.status_code}: {response.text}")
                if response.status_code == 404:
                    raise ResourceNotFoundError(f"HTTP 404: Resource at '{path}' not found.")
                if response.status_code == 429:
                    if attempt < self.max_retries:
                        backoff = (2**attempt) + random.uniform(0.1, 0.5)
                        await asyncio.sleep(backoff)
                        continue
                    raise RateLimitError("HTTP 429: Rate limit exceeded after retries.")

                if response.status_code >= 500:
                    if attempt < self.max_retries:
                        backoff = (2**attempt) + random.uniform(0.1, 0.5)
                        await asyncio.sleep(backoff)
                        continue
                    raise TemplateError(f"HTTP {response.status_code}: Upstream server error.")

                response.raise_for_status()
                if not response.content:
                    return {}
                return response.json()  # type: ignore[no-any-return]

            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                last_exception = exc
                if attempt < self.max_retries:
                    await asyncio.sleep((2**attempt) + random.uniform(0.1, 0.5))
                    continue

        raise TemplateError(f"Request failed: {redact_secrets(str(last_exception))}")
