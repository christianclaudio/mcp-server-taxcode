"""Batch rule: bulk_delete_items processes every item and reports each one's status."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from taxcode_mcp import server
from taxcode_mcp.config import settings

_SECRET = "secret-token-abc"


def _use_upstream(monkeypatch: pytest.MonkeyPatch, failing: set[str]) -> list[str]:
    """Route DELETEs to a mock upstream that fails the ids in ``failing``; return the call log."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        item_id = request.url.path.rsplit("/", 1)[-1]
        calls.append(item_id)
        if item_id in failing:
            raise httpx.RequestError(f"Bearer {_SECRET} failure for {item_id}")
        return httpx.Response(200, json={"deleted": True})

    async_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.example.com"
    )
    monkeypatch.setattr(
        server, "client", server.TemplateClient(http_client=async_client, max_retries=0)
    )
    monkeypatch.setattr(settings, "MCP_ALLOW_BULK_DESTRUCTIVE", True)
    return calls


async def _bulk_delete(item_ids: list[str]) -> Any:
    async with Client(server.create_server()) as mcp_client:
        return await mcp_client.call_tool(
            "items_bulk_delete_items",
            {"item_ids": item_ids, "dry_run": False, "confirm": True},
            raise_on_error=False,
        )


@pytest.mark.asyncio
async def test_bulk_delete_all_succeed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _use_upstream(monkeypatch, failing=set())
    res = await _bulk_delete(["1", "2"])
    assert res.is_error is False
    data = json.loads(res.content[0].text)
    assert data["status"] == "success"
    assert data["deleted_count"] == 2
    assert data["failed_count"] == 0
    assert [r["status"] for r in data["results"]] == ["deleted", "deleted"]
    assert data["errors"] == []
    assert calls == ["1", "2"]


@pytest.mark.asyncio
async def test_bulk_delete_partial_success_is_a_normal_result(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls = _use_upstream(monkeypatch, failing={"2"})
    caplog.set_level(logging.DEBUG)
    res = await _bulk_delete(["1", "2", "3"])
    assert res.is_error is False
    text = res.content[0].text
    assert _SECRET not in text
    data = json.loads(text)
    assert data["status"] == "partial_success"
    assert data["deleted_count"] == 2
    assert data["failed_count"] == 1
    assert [(r["id"], r["status"]) for r in data["results"]] == [("1", "deleted"), ("3", "deleted")]
    assert [(e["id"], e["status"]) for e in data["errors"]] == [("2", "failed")]
    assert "Bearer [REDACTED]" in data["errors"][0]["error"]["message"]
    # Every item is attempted: the failure on "2" does not stop the batch.
    assert calls == ["1", "2", "3"]
    assert _SECRET not in caplog.text


@pytest.mark.asyncio
async def test_bulk_delete_partial_redacts_a_raw_item_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A per-item error that arrives unredacted is redacted by bulk_delete_items itself."""
    raw_token = "sk-live-abc123"

    async def request(method: str, path: str, **_: Any) -> dict[str, Any]:
        if path == "items/2":
            raise RuntimeError(f"upstream rejected Authorization: Bearer {raw_token}")
        return {"deleted": True}

    monkeypatch.setattr(server.client, "request", request)
    monkeypatch.setattr(settings, "MCP_ALLOW_BULK_DESTRUCTIVE", True)
    caplog.set_level(logging.DEBUG)
    res = await _bulk_delete(["1", "2", "3"])
    assert res.is_error is False
    text = res.content[0].text
    assert raw_token not in text
    data = json.loads(text)
    assert data["status"] == "partial_success"
    assert [(e["id"], e["status"]) for e in data["errors"]] == [("2", "failed")]
    assert "Bearer [REDACTED]" in data["errors"][0]["error"]["message"]
    assert raw_token not in caplog.text


@pytest.mark.asyncio
async def test_bulk_delete_all_failed_is_redacted_batch_failed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls = _use_upstream(monkeypatch, failing={"1", "2"})
    caplog.set_level(logging.DEBUG)
    res = await _bulk_delete(["1", "2"])
    assert res.is_error is True
    text = res.content[0].text
    assert _SECRET not in text
    err = json.loads(text)["error"]
    assert err["type"] == "batch_failed"
    assert err["message"] == "All 2 item deletions failed; nothing was deleted."
    assert err["failed_count"] == 2
    assert [(e["id"], e["status"]) for e in err["errors"]] == [("1", "failed"), ("2", "failed")]
    assert all("Bearer [REDACTED]" in e["error"]["message"] for e in err["errors"])
    assert calls == ["1", "2"]
    assert _SECRET not in caplog.text


_TOKEN_SECRETS = ("SECRET1", "SECRET2", "SECRET3")
_TOKEN_ERROR = (
    "upstream 401 for https://api.example.com/items/{id}?token=SECRET3 "
    "with api_token=SECRET1 access_token=SECRET2"
)


def _raise_token_errors(monkeypatch: pytest.MonkeyPatch, failing: set[str]) -> None:
    """Make the ids in ``failing`` raise a raw error carrying query and key=value tokens."""

    async def request(method: str, path: str, **_: Any) -> dict[str, Any]:
        item_id = path.rsplit("/", 1)[-1]
        if item_id in failing:
            raise RuntimeError(_TOKEN_ERROR.format(id=item_id))
        return {"deleted": True}

    monkeypatch.setattr(server.client, "request", request)
    monkeypatch.setattr(settings, "MCP_ALLOW_BULK_DESTRUCTIVE", True)


@pytest.mark.asyncio
async def test_bulk_delete_partial_redacts_token_forms(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A partial batch redacts api_token=, access_token= and ?token= in each item's message."""
    _raise_token_errors(monkeypatch, failing={"2"})
    caplog.set_level(logging.DEBUG)
    res = await _bulk_delete(["1", "2"])
    assert res.is_error is False
    text = res.content[0].text
    data = json.loads(text)
    assert data["status"] == "partial_success"
    assert [(r["id"], r["status"]) for r in data["results"]] == [("1", "deleted")]
    assert [(e["id"], e["status"]) for e in data["errors"]] == [("2", "failed")]
    message = data["errors"][0]["error"]["message"]
    assert message == (
        "upstream 401 for https://api.example.com/items/2?token=[REDACTED] "
        "with api_token=[REDACTED] access_token=[REDACTED]"
    )
    assert res.structured_content is not None
    assert res.structured_content["errors"][0]["error"]["message"] == message
    for secret in _TOKEN_SECRETS:
        assert secret not in text
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_bulk_delete_all_failed_redacts_token_forms(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """All items fail: the batch_failed ToolError text and its audit log line are redacted."""
    _raise_token_errors(monkeypatch, failing={"1", "2"})
    caplog.set_level(logging.DEBUG)
    res = await _bulk_delete(["1", "2"])
    assert res.is_error is True
    text = res.content[0].text
    err = json.loads(text)["error"]
    assert err["type"] == "batch_failed"
    assert [e["error"]["message"] for e in err["errors"]] == [
        "upstream 401 for https://api.example.com/items/1?token=[REDACTED] "
        "with api_token=[REDACTED] access_token=[REDACTED]",
        "upstream 401 for https://api.example.com/items/2?token=[REDACTED] "
        "with api_token=[REDACTED] access_token=[REDACTED]",
    ]
    failed_lines = [
        r.getMessage() for r in caplog.records if "MCP Request failed" in r.getMessage()
    ]
    assert len(failed_lines) == 1
    assert "batch_failed" in failed_lines[0]
    assert "api_token=[REDACTED]" in failed_lines[0]
    assert "?token=[REDACTED]" in failed_lines[0]
    for secret in _TOKEN_SECRETS:
        assert secret not in text
        assert secret not in caplog.text

    # The ToolError raised by the handler itself carries the same redacted text.
    with pytest.raises(ToolError) as excinfo:
        await server.bulk_delete_items(item_ids=["1", "2"], dry_run=False, confirm=True)
    for secret in _TOKEN_SECRETS:
        assert secret not in str(excinfo.value)
    assert "access_token=[REDACTED]" in str(excinfo.value)


@pytest.mark.asyncio
async def test_bulk_delete_empty_batch_is_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_upstream(monkeypatch, failing=set())
    res = await _bulk_delete([])
    assert res.is_error is False
    data = json.loads(res.content[0].text)
    assert (data["status"], data["deleted_count"], data["failed_count"]) == ("success", 0, 0)


@pytest.mark.asyncio
async def test_bulk_delete_cancellation_is_not_a_failed_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation stops the batch; it is never caught and reported as a failed item."""
    calls: list[str] = []

    async def request(method: str, path: str, **_: Any) -> dict[str, Any]:
        calls.append(path)
        if path == "items/2":
            raise asyncio.CancelledError
        return {"deleted": True}

    monkeypatch.setattr(server.client, "request", request)
    monkeypatch.setattr(settings, "MCP_ALLOW_BULK_DESTRUCTIVE", True)
    # Called directly so the CancelledError itself is observable: a per-item catch of
    # BaseException would swallow it and return a partial_success result instead.
    with pytest.raises(asyncio.CancelledError):
        await server.bulk_delete_items(item_ids=["1", "2", "3"], dry_run=False, confirm=True)
    # The call stops at the cancelled item: "3" is never attempted.
    assert calls == ["items/1", "items/2"]
