"""Tests for navig.mcp.transport — SSETransport timeout/cleanup and
transport-dispatch utility helpers."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.integration

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

    async def test_send_raises_on_timeout(self):
        """POST returns 202 (empty body) → future is created → wait_for times out."""
        t = _make_sse_transport()
        t._session = _fake_session(post_status=202, post_body="")

        # Patch asyncio.wait_for so it immediately raises TimeoutError
        with patch("asyncio.wait_for", new=AsyncMock(side_effect=asyncio.TimeoutError)):
            with pytest.raises(RuntimeError, match="timed out"):
                await t.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))

    async def test_pending_dict_cleaned_up_on_timeout(self):
        """_pending must be empty after a timeout (finally block runs)."""
        t = _make_sse_transport()
        req = {"jsonrpc": "2.0", "id": 42, "method": "ping"}
        t._session = _fake_session(post_status=202, post_body="")

        with patch("asyncio.wait_for", new=AsyncMock(side_effect=asyncio.TimeoutError)):
            with pytest.raises(RuntimeError):
                await t.send(json.dumps(req))

        assert 42 not in t._pending  # finally block must have popped it

    async def test_send_returns_inline_body_when_post_returns_200(self):
        """POST returns 200 with a non-empty body → return it directly, no future."""
        t = _make_sse_transport()
        inline = json.dumps({"jsonrpc": "2.0", "id": 7, "result": {}})
        t._session = _fake_session(post_status=200, post_body=inline)

        result = await t.send(json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}))
        assert result == inline
        # No leftover pending future
        assert not t._pending

    async def test_send_raises_on_post_error_status(self):
        """Non-200/202 status → RuntimeError immediately, no future left behind."""
        t = _make_sse_transport()
        t._session = _fake_session(post_status=500, post_body="server error")

        with pytest.raises(RuntimeError, match="500"):
            await t.send(json.dumps({"jsonrpc": "2.0", "id": 5, "method": "ping"}))

        assert not t._pending  # pop(req_id, None) should clear it


# ── SSETransport.disconnect() cleanup ─────────────────────────────────────────


class TestSSETransportDisconnect:
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

    def test_true_when_session_open_and_listener_alive(self):
        t = _make_sse_transport()
        t._session = MagicMock()
        t._session.closed = False
        t._sse_task = MagicMock()
        t._sse_task.done.return_value = False
        assert t.is_connected()

    def test_false_when_listener_task_dead(self):
        """Session open but the SSE listener died (non-200 / stream drop) → NOT connected:
        every send() would hang with no listener to resolve it."""
        t = _make_sse_transport()
        t._session = MagicMock()
        t._session.closed = False
        t._sse_task = MagicMock()
        t._sse_task.done.return_value = True
        assert not t.is_connected()


# ── StdioTransport helpers ────────────────────────────────────────────────────


def _make_stdio_transport():
    from navig.mcp.transport import StdioTransport

    return StdioTransport(command="fake-mcp-server")


def _fake_stdio_process(*, returncode=None):
    """Build a minimal asyncio.subprocess.Process stub."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock()
    return proc


# ── StdioTransport.is_connected() ────────────────────────────────────────────


class TestStdioTransportIsConnected:
    def test_false_when_no_process(self):
        t = _make_stdio_transport()
        assert not t.is_connected()

    def test_false_when_process_exited(self):
        """returncode is set (non-None) → process has terminated → not connected."""
        t = _make_stdio_transport()
        t._process = _fake_stdio_process(returncode=0)
        assert not t.is_connected()

    def test_true_when_process_running(self):
        """returncode is None AND the reader task is alive → connected."""
        t = _make_stdio_transport()
        t._process = _fake_stdio_process(returncode=None)
        t._reader_task = MagicMock()
        t._reader_task.done.return_value = False
        assert t.is_connected()

    def test_false_when_reader_task_dead(self):
        """Process alive but the reader loop died (e.g. a pre-limit stdout overrun) → NOT
        connected: the client would hang every call, so it must read as disconnected so the
        manager stops routing to it."""
        t = _make_stdio_transport()
        t._process = _fake_stdio_process(returncode=None)
        t._reader_task = MagicMock()
        t._reader_task.done.return_value = True
        assert not t.is_connected()


class TestStdioTransportDisconnect:
    async def test_disconnect_ignores_process_lookup_error(self):
        """disconnect() should not fail if terminate() races with process exit."""
        t = _make_stdio_transport()
        proc = _fake_stdio_process(returncode=None)
        proc.terminate = MagicMock(side_effect=ProcessLookupError)
        proc.wait = AsyncMock(return_value=0)
        t._process = proc

        await t.disconnect()

        assert t._process is None


# ── StdioTransport.send() ─────────────────────────────────────────────────────


class TestStdioTransportSend:
    async def test_send_raises_when_not_connected(self):
        """send() with no process raises RuntimeError immediately."""
        t = _make_stdio_transport()
        with pytest.raises(RuntimeError, match="not connected"):
            await t.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}))

    async def test_send_timeout_raises_runtime_error(self):
        """send() raises RuntimeError('Request timeout: N') after asyncio.wait_for times out."""
        t = _make_stdio_transport()
        t._process = _fake_stdio_process()

        with patch("asyncio.wait_for", new=AsyncMock(side_effect=asyncio.TimeoutError)):
            with pytest.raises(RuntimeError, match="timeout"):
                await t.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}))

    async def test_send_timeout_cleans_pending(self):
        """pending dict must be empty after a TimeoutError (finally block)."""
        t = _make_stdio_transport()
        t._process = _fake_stdio_process()

        with patch("asyncio.wait_for", new=AsyncMock(side_effect=asyncio.TimeoutError)):
            with pytest.raises(RuntimeError):
                await t.send(json.dumps({"jsonrpc": "2.0", "id": 77, "method": "ping"}))

        assert 77 not in t._pending

    async def test_send_notification_returns_none(self):
        """Messages without 'id' are notifications; send() writes and returns None without
        registering a future in _pending."""
        t = _make_stdio_transport()
        t._process = _fake_stdio_process()

        result = await t.send(
            json.dumps({"jsonrpc": "2.0", "method": "notify/progress", "params": {}})
        )

        assert result is None
        assert not t._pending  # no future created for notification


# ── StdioTransport: large responses + reader-death recovery ───────────────────


class TestStdioTransportLargeResponse:
    async def test_response_over_64kib_is_not_dropped(self, monkeypatch):
        """A JSON-RPC response larger than asyncio's default 64 KiB StreamReader limit must
        be read in full — pre-fix (no limit=) it overruns readline(), kills the reader loop,
        strands the request, and send() times out while is_connected() stays True."""
        import sys

        from navig.mcp.transport import StdioTransport

        # Child: read one JSON-RPC request line, echo a ~100 KiB single-line result (> 64 KiB).
        child = (
            "import sys, json, time\n"
            "req = json.loads(sys.stdin.readline())\n"
            "big = 'x' * 100000\n"
            "sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': req['id'],"
            " 'result': {'content': [{'type': 'text', 'text': big}]}}) + '\\n')\n"
            "sys.stdout.flush()\n"
            "time.sleep(5)\n"
        )
        # Keep a pre-fix failure fast instead of a 30 s hang.
        monkeypatch.setattr("navig.mcp.transport._REQUEST_TIMEOUT", 5.0)

        t = StdioTransport(command=sys.executable, args=["-c", child])
        await t.connect()
        try:
            resp = await t.send(
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call"})
            )
            assert resp is not None
            payload = json.loads(resp)
            assert payload["result"]["content"][0]["text"] == "x" * 100000
            # The reader is still alive and the transport still reports connected.
            assert t._reader_task is not None and not t._reader_task.done()
            assert t.is_connected()
        finally:
            await t.disconnect()


class TestStdioTransportReaderDeath:
    async def test_reader_exit_fails_pending_requests(self):
        """When the reader loop exits (EOF / overrun / error), every in-flight request future
        is failed immediately instead of hanging until _REQUEST_TIMEOUT."""
        t = _make_stdio_transport()
        proc = _fake_stdio_process(returncode=None)
        proc.stdout = MagicMock()
        proc.stdout.readline = AsyncMock(return_value=b"")  # immediate EOF → loop breaks
        t._process = proc

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        t._pending[42] = fut

        await t._read_loop()  # runs to completion, then fails pending

        assert fut.done()
        with pytest.raises(RuntimeError, match="reader terminated"):
            fut.result()


# ── SSETransport: listener death fails pending ────────────────────────────────


class TestSSETransportListenerDeath:
    async def test_listener_exit_fails_pending_requests(self):
        """When the SSE listener exits (non-200 / stream drop / error), every in-flight
        request future is failed immediately instead of hanging until _REQUEST_TIMEOUT."""
        t = _make_sse_transport()
        t._session = MagicMock()
        t._session.closed = False
        t._session.get = MagicMock(side_effect=RuntimeError("stream dropped"))

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        t._pending[7] = fut

        await t._sse_listen_loop()  # raises internally → except → finally fails pending

        assert fut.done()
        with pytest.raises(RuntimeError, match="SSE listener terminated"):
            fut.result()


# ── WebSocketTransport: is_connected() + reader death ─────────────────────────


def _make_ws_transport():
    from navig.mcp.transport import WebSocketTransport

    return WebSocketTransport(url="ws://host/mcp")


class TestWebSocketTransportIsConnected:
    def test_false_when_no_ws(self):
        assert not _make_ws_transport().is_connected()

    def test_true_when_ws_open_and_reader_alive(self):
        t = _make_ws_transport()
        t._ws = MagicMock()
        t._ws.closed = False
        t._reader_task = MagicMock()
        t._reader_task.done.return_value = False
        assert t.is_connected()

    def test_false_when_reader_task_dead(self):
        """ws open but the reader loop died (recv error / oversized frame) → NOT connected:
        no reader means every call would hang, so it must read as disconnected."""
        t = _make_ws_transport()
        t._ws = MagicMock()
        t._ws.closed = False
        t._reader_task = MagicMock()
        t._reader_task.done.return_value = True
        assert not t.is_connected()


class TestWebSocketTransportReaderDeath:
    async def test_reader_exit_fails_pending_requests(self):
        t = _make_ws_transport()
        ws = MagicMock()
        ws.recv = AsyncMock(side_effect=RuntimeError("connection reset"))
        t._ws = ws

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        t._pending[9] = fut

        await t._read_loop()  # recv raises → except break → post-loop fails pending

        assert fut.done()
        with pytest.raises(RuntimeError, match="WebSocket reader terminated"):
            fut.result()
