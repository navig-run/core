"""Tests for navig.mcp.transport — SSETransport timeout/cleanup and
transport-dispatch utility helpers."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_sse_transport(url: str = "http://host/mcp"):
    from navig.mcp.transport import SSETransport

    return SSETransport(url=url, headers={"Authorization": "Bearer tok"})


def _fake_session(*, post_status: int = 202, post_body: str = "") -> MagicMock:
    """Build a minimal aiohttp.ClientSession stub."""
    resp = MagicMock()
    resp.status = post_status
    resp.text = AsyncMock(return_value=post_body)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.closed = False
    session.post = MagicMock(return_value=resp)
    session.close = AsyncMock()
    return session


# ── SSETransport.send() timeout ───────────────────────────────────────────────


class TestSSETransportSendTimeout:
    """send() raises RuntimeError after 30 s when the SSE listener never fires."""

    @pytest.mark.asyncio
    async def test_send_raises_on_timeout(self):
        """POST returns 202 (empty body) → future is created → wait_for times out."""
        t = _make_sse_transport()
        t._session = _fake_session(post_status=202, post_body="")

        # Patch asyncio.wait_for so it immediately raises TimeoutError
        with patch("asyncio.wait_for", new=AsyncMock(side_effect=asyncio.TimeoutError)):
            with pytest.raises(RuntimeError, match="timed out"):
                await t.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))

    @pytest.mark.asyncio
    async def test_pending_dict_cleaned_up_on_timeout(self):
        """_pending must be empty after a timeout (finally block runs)."""
        t = _make_sse_transport()
        req = {"jsonrpc": "2.0", "id": 42, "method": "ping"}
        t._session = _fake_session(post_status=202, post_body="")

        with patch("asyncio.wait_for", new=AsyncMock(side_effect=asyncio.TimeoutError)):
            with pytest.raises(RuntimeError):
                await t.send(json.dumps(req))

        assert 42 not in t._pending  # finally block must have popped it

    @pytest.mark.asyncio
    async def test_send_returns_inline_body_when_post_returns_200(self):
        """POST returns 200 with a non-empty body → return it directly, no future."""
        t = _make_sse_transport()
        inline = json.dumps({"jsonrpc": "2.0", "id": 7, "result": {}})
        t._session = _fake_session(post_status=200, post_body=inline)

        result = await t.send(json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}))
        assert result == inline
        # No leftover pending future
        assert not t._pending

    @pytest.mark.asyncio
    async def test_send_raises_on_post_error_status(self):
        """Non-200/202 status → RuntimeError immediately, no future left behind."""
        t = _make_sse_transport()
        t._session = _fake_session(post_status=500, post_body="server error")

        with pytest.raises(RuntimeError, match="500"):
            await t.send(json.dumps({"jsonrpc": "2.0", "id": 5, "method": "ping"}))

        assert not t._pending  # pop(req_id, None) should clear it


# ── SSETransport.disconnect() cleanup ─────────────────────────────────────────


class TestSSETransportDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_cancels_pending_futures(self):
        """Any in-flight pending futures should be cancelled on disconnect."""
        t = _make_sse_transport()
        t._session = _fake_session()

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        t._pending[99] = fut

        await t.disconnect()

        assert fut.cancelled()
        assert not t._pending
        assert t._session is None

    @pytest.mark.asyncio
    async def test_disconnect_cancels_sse_task(self):
        """_sse_task should be cancelled and awaited during disconnect."""
        t = _make_sse_transport()
        t._session = _fake_session()

        # Simulate a running SSE task
        async def _forever():
            await asyncio.sleep(3600)

        task = asyncio.create_task(_forever())
        t._sse_task = task

        await t.disconnect()

        assert task.cancelled()


# ── SSETransport.is_connected() ───────────────────────────────────────────────


class TestSSETransportIsConnected:
    def test_false_when_no_session(self):
        t = _make_sse_transport()
        assert not t.is_connected()

    def test_false_when_session_closed(self):
        t = _make_sse_transport()
        t._session = MagicMock()
        t._session.closed = True
        assert not t.is_connected()

    def test_true_when_session_open(self):
        t = _make_sse_transport()
        t._session = MagicMock()
        t._session.closed = False
        assert t.is_connected()
