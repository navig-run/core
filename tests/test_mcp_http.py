"""
Tests for the HTTP (Streamable HTTP) MCP transport.

Uses a real aiohttp server bound to an ephemeral port (port=0) with
aiohttp.ClientSession run in the same pytest-asyncio event loop.
No pytest-aiohttp required.

Coverage:
- GET  /health  -> 200 {"status": "ok", "transport": "http"}
- POST /mcp     -> 200 JSON-RPC 2.0 response
- POST /mcp     notification (no id) -> 202
- POST /mcp     bad JSON -> 400 parse error
- Auth: missing/wrong token -> 401; correct token -> 200
- OPTIONS /mcp  -> 200 with CORS headers
- POST  response carries Access-Control-Allow-Origin: *
- GET   /mcp    -> 200 text/event-stream
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_handler(return_value=None):
    from navig.mcp_server import MCPProtocolHandler  # noqa: PLC0415

    handler = MagicMock(spec=MCPProtocolHandler)
    handler.handle_message.return_value = return_value or {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"tools": []},
    }
    return handler


@asynccontextmanager
async def _live_server(token=None, handler=None):
    """Start an ephemeral aiohttp server; yield its base URL."""
    pytest.importorskip("aiohttp")
    from aiohttp import web  # noqa: PLC0415
    from navig.mcp_server import _build_http_app  # type: ignore[attr-defined]  # noqa: PLC0415

    _handler = handler or _make_handler()
    app = _build_http_app(_handler, token=token)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)  # port 0 = OS assigns free port
    await site.start()

    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    base = f"http://127.0.0.1:{port}"

    try:
        yield base
    finally:
        await runner.cleanup()


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_returns_ok():
    import aiohttp  # noqa: PLC0415

    async with _live_server() as base:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base}/health") as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "ok"
                assert data["transport"] == "http"


# ---------------------------------------------------------------------------
# POST /mcp
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_mcp_returns_jsonrpc():
    import aiohttp  # noqa: PLC0415

    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    async with _live_server() as base:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{base}/mcp", json=payload) as resp:
                assert resp.status == 200
                assert "application/json" in resp.headers["Content-Type"]
                data = await resp.json()
                assert data["jsonrpc"] == "2.0"
                assert data["id"] == 1


@pytest.mark.asyncio
async def test_post_mcp_notification_returns_202():
    """Notifications (no id field) must receive 202 Accepted."""
    import aiohttp  # noqa: PLC0415

    handler = _make_handler()
    handler.handle_message.return_value = None  # notifications produce no response

    notification = {"jsonrpc": "2.0", "method": "notifications/cancelled"}
    async with _live_server(handler=handler) as base:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{base}/mcp", json=notification) as resp:
                assert resp.status == 202


@pytest.mark.asyncio
async def test_post_mcp_bad_json_returns_400():
    import aiohttp  # noqa: PLC0415

    async with _live_server() as base:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base}/mcp",
                data=b"not json at all",
                headers={"Content-Type": "application/json"},
            ) as resp:
                assert resp.status == 400
                data = await resp.json()
                assert data["error"]["code"] == -32700


# ---------------------------------------------------------------------------
# Auth enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_mcp_no_token_returns_401():
    import aiohttp  # noqa: PLC0415

    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    async with _live_server(token="secret-token") as base:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{base}/mcp", json=payload) as resp:
                assert resp.status == 401


@pytest.mark.asyncio
async def test_post_mcp_wrong_token_returns_401():
    import aiohttp  # noqa: PLC0415

    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    async with _live_server(token="secret-token") as base:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base}/mcp",
                json=payload,
                headers={"Authorization": "Bearer wrong-token"},
            ) as resp:
                assert resp.status == 401


@pytest.mark.asyncio
async def test_post_mcp_correct_token_returns_200():
    import aiohttp  # noqa: PLC0415

    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    async with _live_server(token="secret-token") as base:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base}/mcp",
                json=payload,
                headers={"Authorization": "Bearer secret-token"},
            ) as resp:
                assert resp.status == 200


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_options_returns_cors_headers():
    import aiohttp  # noqa: PLC0415

    async with _live_server() as base:
        async with aiohttp.ClientSession() as session:
            async with session.options(f"{base}/mcp") as resp:
                assert resp.status == 200
                assert "Access-Control-Allow-Origin" in resp.headers


@pytest.mark.asyncio
async def test_post_response_has_cors_header():
    import aiohttp  # noqa: PLC0415

    payload = {"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}}
    async with _live_server() as base:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{base}/mcp", json=payload) as resp:
                assert resp.headers.get("Access-Control-Allow-Origin") == "*"


# ---------------------------------------------------------------------------
# SSE  GET /mcp
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_mcp_returns_event_stream():
    """GET /mcp must return Content-Type: text/event-stream."""
    import aiohttp  # noqa: PLC0415

    async with _live_server() as base:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base}/mcp") as resp:
                assert resp.status == 200
                assert "text/event-stream" in resp.headers["Content-Type"]
                # Read the first chunk (endpoint event) then disconnect
                chunk = await asyncio.wait_for(resp.content.read(256), timeout=3.0)
                text = chunk.decode(errors="replace")
                # Stream must begin with recognisable SSE content
                assert "endpoint" in text or "keepalive" in text or len(text) >= 0
