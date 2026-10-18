# Multi-stage build for mcp-server-taxcode
# Produces a minimal runtime image (~150MB) with no dev tooling.
#
# The base image is the Docker Official python image (python:3.12-slim), pulled
# from Docker Hub by tag. Dependabot's docker ecosystem watches it for updates.
# No `# syntax=` line: the Dockerfile uses only features of BuildKit's built-in
# frontend, so no frontend image is pulled.

# ─── Stage 1: Builder ─────────────────────────────────────────────────────────
FROM python:3.14-slim AS builder

WORKDIR /app

# Install build deps in a virtualenv so we can copy it cleanly
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml README.md LICENSE ./
COPY src/ src/

# The build context has no .git, so uv-dynamic-versioning cannot read the tag and
# falls back to 0.0.0. Release builds pass the tag version, for example
# --build-arg UV_DYNAMIC_VERSIONING_BYPASS=1.3.1 (an unset or empty value keeps 0.0.0).
ARG UV_DYNAMIC_VERSIONING_BYPASS
ENV UV_DYNAMIC_VERSIONING_BYPASS=${UV_DYNAMIC_VERSIONING_BYPASS}

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# ─── Stage 2: Runtime ─────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime

# MCP Registry ownership check for the OCI package: must equal server.json "name".
# https://modelcontextprotocol.io/registry/package-types#docker%2Foci-images
LABEL io.modelcontextprotocol.server.name="io.github.christianclaudio/taxcode"

# Security: run as non-root
RUN useradd --create-home --shell /bin/bash mcp
USER mcp
WORKDIR /home/mcp

# Copy the pre-built virtualenv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Default transport is stdio, so no port is exposed
ENTRYPOINT ["taxcode-mcp"]

