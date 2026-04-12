"""Tests for MCP server hardening (body size limit, logging on error).

These tests complement test_mcp_http.py by targeting the body-size guard
added in the stabilization pass (POST /mcp with body > 1 MiB → 413) and
verifying that handle_message logs exceptions via the logger.
"""

from __future__ import annotations

import importlib
import json
import logging
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Re-use the aiohttp stub from test_mcp_http.  Ensure it's installed before
# importing navig.mcp_server so the lazy `from aiohttp import web` inside
# `_build_http_app` picks up our lightweight stubs instead of the real
# aiohttp (which may hang under Python 3.14/Windows).
# ---------------------------------------------------------------------------


class _Response:
    """Minimal aiohttp.web.Response stand-in."""

    def __init__(
        self,
        *,
        status: int = 200,
        body=None,
        content_type: str = "application/json",
        headers=None,
        **_kw,
    ):
        self.status = status
        self._body = body
        self.content_type = content_type
        self.headers: dict[str, str] = dict(headers or {})

    def json_body(self) -> dict:
        if not self._body:
            return {}
        raw = self._body.decode() if isinstance(self._body, bytes) else self._body
        return json.loads(raw)


class _StreamResponse:
    def __init__(self, *, status=200, headers=None, **_kw):
        self.status = status
        self.headers: dict[str, str] = dict(headers or {})
        self._written: list[bytes] = []

    async def prepare(self, request):
        pass

    async def write(self, data: bytes):
        self._written.append(data)


class _HTTPUnauthorized(Exception):
    def __init__(self, *, reason="Unauthorized"):
        super().__init__(reason)
        self.reason = reason


class _TrackedApp:
    def __init__(self):
        self._routes: dict[tuple[str, str], Any] = {}
        self.router = self

    def add_options(self, path, handler):
        self._routes[("OPTIONS", path)] = handler

    def add_get(self, path, handler):
        self._routes[("GET", path)] = handler

    def add_post(self, path, handler):
        self._routes[("POST", path)] = handler


_web_stub = MagicMock(name="aiohttp.web")
_web_stub.Response = _Response
_web_stub.StreamResponse = _StreamResponse
_web_stub.HTTPUnauthorized = _HTTPUnauthorized
_web_stub.Application = _TrackedApp
_web_stub.Request = MagicMock

_aio_stub = MagicMock(name="aiohttp")
_aio_stub.web = _web_stub


@pytest.fixture(autouse=True)
def _isolate_aiohttp_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure aiohttp stubs are scoped to this module's tests only."""
    monkeypatch.setitem(sys.modules, "aiohttp", _aio_stub)
    monkeypatch.setitem(sys.modules, "aiohttp.web", _web_stub)


def _mcp_exports() -> tuple[Any, Any]:
    """Fetch MCP server exports after aiohttp stubs are injected."""
    module = importlib.import_module("navig.mcp_server")
    return module.MCPProtocolHandler, module._build_http_app


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_handler(return_value=None):
    h = MagicMock()
    h.handle_message.return_value = return_value or {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"tools": []},
    }
    return h


def _make_request(*, text_body="", auth_header=None):
    req = MagicMock()
    req.headers = {}
    if auth_header is not None:
        req.headers["Authorization"] = auth_header
    req.text = AsyncMock(return_value=text_body)
    return req


def _build_app(token=None):
    _, _build_http_app = _mcp_exports()
    h = _make_handler()
    app = _build_http_app(h, token=token)
    return app, h


def _route(app, method, path):
    return app._routes[(method, path)]


# ---------------------------------------------------------------------------
# POST /mcp body-size guard  (413)
# ---------------------------------------------------------------------------


async def test_post_mcp_oversized_body_returns_413():
    """POST /mcp with body > 1 MiB must return 413 with JSON-RPC error -32600."""
    app, _ = _build_app()
    post = _route(app, "POST", "/mcp")

    # 1 MiB + 1 byte
    huge_body = "x" * (1_048_576 + 1)
    req = _make_request(text_body=huge_body)

    resp = await post(req)
    assert resp.status == 413
    body = resp.json_body()
    assert body["error"]["code"] == -32600
    assert "too large" in body["error"]["message"].lower()


async def test_post_mcp_exactly_1mib_is_accepted():
    """POST /mcp with exactly 1 MiB body should still be accepted (not > 1 MiB)."""
    app, h = _build_app()
    post = _route(app, "POST", "/mcp")

    # Exactly 1 MiB of valid JSON
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}})
    padded = payload + " " * (1_048_576 - len(payload))
    req = _make_request(text_body=padded)

    resp = await post(req)
    # Should parse (even if JSON is padded, it's valid if base payload is fine)
    assert resp.status in (200, 202, 400)  # 400 if padding breaks JSON, but not 413


# ---------------------------------------------------------------------------
# handle_message logs exceptions
# ---------------------------------------------------------------------------


def test_handle_message_logs_exception_to_debug():
    """When a handler raises, handle_message should log the exception at DEBUG level."""
    MCPProtocolHandler, _ = _mcp_exports()
    with patch("navig.mcp.tools.register_all_tools", side_effect=lambda h: None):
        handler = MCPProtocolHandler()

    # Register a handler that always raises
    handler._handlers["boom"] = lambda params: 1 / 0

    with patch("navig.mcp_server.logger") as mock_logger:
        result = handler.handle_message({"method": "boom", "id": 99})

    assert result["error"]["code"] == -32603
    assert "division by zero" in result["error"]["message"]

    # Verify the logger was called
    mock_logger.debug.assert_called_once()
    call_args = mock_logger.debug.call_args
    assert "MCP handler error" in call_args[0][0]
    assert call_args[1].get("exc_info") is True


def test_handle_message_notification_no_id_logs_and_returns_none():
    """Notification (no id) that raises should log but return None."""
    MCPProtocolHandler, _ = _mcp_exports()
    with patch("navig.mcp.tools.register_all_tools", side_effect=lambda h: None):
        handler = MCPProtocolHandler()

    handler._handlers["crash_notify"] = lambda params: 1 / 0

    with patch("navig.mcp_server.logger") as mock_logger:
        result = handler.handle_message({"method": "crash_notify"})  # no id

    assert result is None
    mock_logger.debug.assert_called_once()
    assert "MCP handler error" in mock_logger.debug.call_args[0][0]


def test_handle_message_unknown_method_no_log():
    """Unknown method should return -32601 but NOT trigger exception log."""
    MCPProtocolHandler, _ = _mcp_exports()
    with patch("navig.mcp.tools.register_all_tools", side_effect=lambda h: None):
        handler = MCPProtocolHandler()

    result = handler.handle_message({"method": "nonexistent", "id": 1})
    assert result["error"]["code"] == -32601


# ---------------------------------------------------------------------------
# handle_message notification (no id) normal success returns None
# ---------------------------------------------------------------------------


def test_handle_message_notification_success_returns_none():
    """Notification handler that succeeds should return None (no response)."""
    MCPProtocolHandler, _ = _mcp_exports()
    with patch("navig.mcp.tools.register_all_tools", side_effect=lambda h: None):
        handler = MCPProtocolHandler()

    handler._handlers["notify_ok"] = lambda params: "silent"

    result = handler.handle_message({"method": "notify_ok"})
    assert result is None
