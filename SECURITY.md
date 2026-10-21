# Security Policy

## Supported Versions

Only the latest release receives security fixes. Upgrade to the newest version on [Releases](https://github.com/christianclaudio/mcp-server-taxcode/releases).

## Outbound Connections and Proxies

Every outbound request (the upstream API client, the drift monitor's `--spec-url` fetch and `ingest_statutes.py --check-upstream`) goes through `SSRFSafeAsyncTransport`. It looks the host up once per request, checks every address against the private, loopback, link-local, reserved and metadata ranges, and connects only to a checked address, keeping the hostname for the Host header, TLS SNI and certificate verification. Redirects are not followed. httpx ignores env proxies whenever a transport is passed (httpx `AsyncClient`: `allow_env_proxies = trust_env and transport is None`), so connections always go direct through the pin; egress-proxy deployments need a proxy-aware transport, which isn't provided. `trust_env=False` on the drift fetch is belt-and-braces.

## Reporting a Vulnerability

If you discover a security vulnerability, please do NOT open a public GitHub issue.

Please report vulnerabilities directly to the maintainer:
* **Email**: `christianclaudio@mail.com`

All reports will be acknowledged within 24 hours, and patches will be published with high priority.
