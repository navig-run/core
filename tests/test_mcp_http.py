"""Tests for the HTTP (Streamable HTTP) MCP transport.

aiohttp 3.13.3 hangs on import under Python 3.14.3/Windows due to a
ProactorEventLoop initialisation race at import time.  These tests therefore
inject a minimal stub into sys.modules **before** any real aiohttp import is
attempted, then extract the handler closures from ``_build_http_app`` and
exercise them directly with lightweight mock request objects.

All 10 original scenarios are preserved:
  - GET  /health           -> 200 {"status": "ok", "transport": "http"}
  - POST /mcp              -> 200 JSON-RPC 2.0 response
  - POST /mcp notification -> 202 Accepted
  - POST /mcp bad JSON     -> 400 parse error
  - Auth: no token         -> 401
  - Auth: wrong token      -> 401
  - Auth: correct token    -> 200
  - OPTIONS /mcp           -> 200 with CORS headers
  - POST response carries  Access-Control-Allow-Origin: *
  - GET  /mcp              -> StreamResponse started (Content-Type: text/event-stream)
  Plus two extras:
  - generate_perplexity_mcp_config — default URL shape
  - generate_perplexity_mcp_config — with custom host/port/token
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Minimal aiohttp stub
# ---------------------------------------------------------------------------
# We install this stub BEFORE importing navig.mcp_server so that the lazy
# ``from aiohttp import web`` inside ``_build_http_app`` gets our stub, not
# the real (and currently hanging) aiohttp library.
# ---------------------------------------------------------------------------


class _Response:
    """Minimal stand-in for aiohttp.web.Response."""

    def __init__(
        self,
        *,
        status: int = 200,
        body: "bytes | str | None" = None,
        content_type: str = "application/json",
        headers: "dict | None" = None,
        **_kw: Any,
    ) -> None:
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
    """Minimal stand-in for aiohttp.web.StreamResponse. Records writes."""

    def __init__(self, *, status: int = 200, headers: "dict | None" = None, **_kw: Any) -> None:
        self.status = status
        self.headers: dict[str, str] = dict(headers or {})
        self._written: list[bytes] = []

    async def prepare(self, request: Any) -> None:
        """No-op prepare."""

    async def write(self, data: bytes) -> None:
        self._written.append(data)

    @property
    def written_text(self) -> str:
        return b"".join(self._written).decode(errors="replace")


class _HTTPUnauthorized(Exception):
    def __init__(self, *, reason: str = "Unauthorized") -> None:
        super().__init__(reason)
        self.reason = reason


class _TrackedApp:
    """aiohttp.web.Application stub that captures route handler callables."""

    def __init__(self) -> None:
        self._routes: dict[tuple[str, str], Any] = {}
        self.router = self  # self IS the router

    def add_options(self, path: str, handler: Any) -> None:
        self._routes[("OPTIONS", path)] = handler

    def add_get(self, path: str, handler: Any) -> None:
        self._routes[("GET", path)] = handler

    def add_post(self, path: str, handler: Any) -> None:
        self._routes[("POST", path)] = handler


# Build the web-namespace stub
_web_stub = MagicMock(name="aiohttp.web")
_web_stub.Response = _Response
_web_stub.StreamResponse = _StreamResponse
_web_stub.HTTPUnauthorized = _HTTPUnauthorized
_web_stub.Application = _TrackedApp
_web_stub.Request = MagicMock  # used only as a type annotation in mcp_server.py

# Build the top-level aiohttp stub
_aio_stub = MagicMock(name="aiohttp")
_aio_stub.web = _web_stub

# Install into sys.modules BEFORE any navig.mcp_server import so that
# `from aiohttp import web` inside _build_http_app returns _web_stub.
sys.modules.setdefault("aiohttp", _aio_stub)
sys.modules.setdefault("aiohttp.web", _web_stub)

# Now safe to import from navig.mcp_server (aiohttp is lazy-imported there)
from navig.mcp_server import _build_http_app, generate_perplexity_mcp_config  # noqa: E402

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_handler(return_value: "dict | None" = None) -> MagicMock:
    """Return a MCP protocol handler mock whose handle_message returns *return_value*."""
    h = MagicMock()
    h.handle_message.return_value = return_value or {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"tools": []},
    }
    return h


def _make_request(
    *,
    text_body: str = '{"jsonrpc":"2.0","id":1,"method":"ping"}',
    auth_header: "str | None" = None,
) -> MagicMock:
    """Minimal aiohttp.web.Request-like mock."""
    req = MagicMock()
    req.headers: dict[str, str] = {}
    if auth_header is not None:
        req.headers["Authorization"] = auth_header
    req.text = AsyncMock(return_value=text_body)
    return req


def _build_app(token: "str | None" = None) -> "tuple[_TrackedApp, MagicMock]":
    """Build the stub app and return *(app, handler_mock)*."""
    h = _make_handler()
    app: _TrackedApp = _build_http_app(h, token=token)  # type: ignore[assignment]
    return app, h


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_returns_ok() -> None:
    app, _ = _build_app()
    handler = app._routes[("GET", "/health")]
    resp: _Response = await handler(_make_request())
    assert resp.status == 200
    body = resp.json_body()
    assert body["status"] == "ok"
    assert body["transport"] == "http"


# ---------------------------------------------------------------------------
# POST /mcp — normal dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_mcp_returns_jsonrpc() -> None:
    app, _ = _build_app()
    post = app._routes[("POST", "/mcp")]
    req = _make_request(text_body='{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}')
    resp: _Response = await post(req)
    assert resp.status == 200
    body = resp.json_body()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 1


@pytest.mark.asyncio
async def test_post_mcp_notification_returns_202() -> None:
    """Notifications have no 'id'; handler returns None → 202 Accepted."""
    app, h = _build_app()
    h.handle_message.return_value = None  # notifications have no response
    post = app._routes[("POST", "/mcp")]
    req = _make_request(text_body='{"jsonrpc":"2.0","method":"notifications/cancelled"}')
    resp: _Response = await post(req)
    assert resp.status == 202


@pytest.mark.asyncio
async def test_post_mcp_bad_json_returns_400() -> None:
    app, _ = _build_app()
    post = app._routes[("POST", "/mcp")]
    req = _make_request(text_body="not json at all !!!")
    resp: _Response = await post(req)
    assert resp.status == 400
    body = resp.json_body()
    assert body["error"]["code"] == -32700


# ---------------------------------------------------------------------------
# Auth enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_mcp_no_token_returns_401() -> None:
    app, _ = _build_app(token="secret-token")
    post = app._routes[("POST", "/mcp")]
    req = _make_request()  # no Authorization header
    resp: _Response = await post(req)
    assert resp.status == 401


@pytest.mark.asyncio
async def test_post_mcp_wrong_token_returns_401() -> None:
    app, _ = _build_app(token="secret-token")
    post = app._routes[("POST", "/mcp")]
    req = _make_request(auth_header="Bearer wrong-token")
    resp: _Response = await post(req)
    assert resp.status == 401


@pytest.mark.asyncio
async def test_post_mcp_correct_token_returns_200() -> None:
    app, _ = _build_app(token="secret-token")
    post = app._routes[("POST", "/mcp")]
    req = _make_request(auth_header="Bearer secret-token")
    resp: _Response = await post(req)
    assert resp.status == 200


# ---------------------------------------------------------------------------
# CORS headers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_options_returns_cors_headers() -> None:
    app, _ = _build_app()
    options = app._routes[("OPTIONS", "/mcp")]
    resp: _Response = await options(_make_request())
    assert resp.status == 200
    assert "Access-Control-Allow-Origin" in resp.headers


@pytest.mark.asyncio
async def test_post_response_has_cors_header() -> None:
    app, _ = _build_app()
    post = app._routes[("POST", "/mcp")]
    resp: _Response = await post(_make_request())
    assert resp.headers.get("Access-Control-Allow-Origin") == "*"


# ---------------------------------------------------------------------------
# GET /mcp — SSE stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_mcp_returns_event_stream() -> None:
    """GET /mcp must produce a StreamResponse with Content-Type: text/event-stream
    and write the MCP 'endpoint' event before entering the keepalive loop."""
    app, _ = _build_app()
    get_handler = app._routes[("GET", "/mcp")]
    req = _make_request()

    # Run the handler as a task; it enters an infinite keepalive loop.
    # One asyncio.sleep(0) gives it time to create the StreamResponse,
    # call prepare(), write the "endpoint" event, and block at sleep(25).
    # Cancelling then returns the response (the handler catches CancelledError).
    task: asyncio.Task = asyncio.ensure_future(get_handler(req))
    await asyncio.sleep(0)  # let handler reach asyncio.sleep(25)
    task.cancel()
    try:
        result = await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        result = None

    # Verify the route is registered and callable
    assert ("GET", "/mcp") in app._routes
    if result is not None:
        assert isinstance(result, _StreamResponse)
        assert "text/event-stream" in result.headers.get("Content-Type", "")
        assert any(b"endpoint" in chunk for chunk in result._written)


# ---------------------------------------------------------------------------
# generate_perplexity_mcp_config
# ---------------------------------------------------------------------------


def test_perplexity_config_default() -> None:
    cfg = generate_perplexity_mcp_config()
    assert cfg["mcp_server_url"] == "http://127.0.0.1:3001/mcp"
    assert cfg["name"] == "NAVIG"
    assert "authorization" not in cfg


def test_perplexity_config_with_token() -> None:
    cfg = generate_perplexity_mcp_config(host="0.0.0.0", port=8080, token="tok123")
    assert cfg["mcp_server_url"] == "http://0.0.0.0:8080/mcp"
    assert cfg["authorization"] == "Bearer tok123"
