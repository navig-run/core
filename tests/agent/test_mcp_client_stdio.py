"""Regression tests for navig.agent.mcp_client._StdioTransport — the #692 class.

A JSON-RPC response over asyncio's default 64 KiB StreamReader limit makes ``readline()``
raise, which kills the reader loop. Before the fix that (a) had no ``limit=`` on the
subprocess, (b) left in-flight futures pending so ``send_request`` hung the full
``_RPC_TIMEOUT``, and (c) reported ``is_alive`` True (only the subprocess was checked) so the
client was phantom-connected forever and never reconnected. This mirrors the fix already made
to ``navig.mcp.transport.StdioTransport`` (#692) in the sibling agent-facing client.
"""

from __future__ import annotations

import asyncio

import pytest

from navig.agent.mcp_client import _STDOUT_LIMIT, _StdioTransport


class _FakeStdout:
    """Async stdout whose readline() either EOFs or raises (the overrun)."""

    def __init__(self, behavior: str) -> None:
        self._behavior = behavior  # "eof" | "raise"

    async def readline(self) -> bytes:
        if self._behavior == "raise":
            # what asyncio raises when a line exceeds the StreamReader limit
            raise ValueError("Separator is not found, and chunk exceed the limit")
        return b""  # EOF


class _FakeProc:
    def __init__(self, stdout: _FakeStdout, returncode: int | None = None) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.stdin = None


async def _completed_task() -> asyncio.Task:
    t = asyncio.create_task(asyncio.sleep(0))
    await t
    return t


async def test_start_passes_generous_stdout_limit(monkeypatch):
    """The subprocess must be spawned with the 8 MiB limit, not asyncio's 64 KiB default."""
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured.update(kwargs)
        return _FakeProc(_FakeStdout("eof"))

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    t = _StdioTransport(["dummy-server"])
    await t.start()
    try:
        assert captured.get("limit") == _STDOUT_LIMIT
        assert _STDOUT_LIMIT >= 8 * 1024 * 1024
    finally:
        if t._reader_task:
            t._reader_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t._reader_task


async def test_reader_death_on_overrun_fails_pending_immediately():
    """A readline() overrun that kills the reader must fail every in-flight future now —
    NOT leave it pending until _RPC_TIMEOUT (a 30s hang per call)."""
    t = _StdioTransport(["dummy-server"])
    t._process = _FakeProc(_FakeStdout("raise"))
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    t._pending[1] = fut

    await t._reader_loop()

    assert fut.done()
    with pytest.raises(ConnectionError):
        fut.result()
    assert not t._pending  # cleared, no leak


async def test_reader_death_on_eof_fails_pending():
    """Same guarantee when the server closes stdout (EOF), not just on an overrun."""
    t = _StdioTransport(["dummy-server"])
    t._process = _FakeProc(_FakeStdout("eof"))
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    t._pending[7] = fut

    await t._reader_loop()

    assert fut.done()
    with pytest.raises(ConnectionError):
        fut.result()


async def test_is_alive_false_when_reader_task_is_dead():
    """is_alive must require a LIVE reader — a running subprocess with a dead reader is
    phantom-connected: every call would hang and no reconnect would fire."""
    t = _StdioTransport(["dummy-server"])
    t._process = _FakeProc(_FakeStdout("eof"), returncode=None)

    t._reader_task = await _completed_task()
    assert t.is_alive is False  # reader dead → not alive, even though returncode is None

    alive = asyncio.create_task(asyncio.sleep(3600))
    t._reader_task = alive
    try:
        assert t.is_alive is True  # reader live + process running → alive
    finally:
        alive.cancel()
        with pytest.raises(asyncio.CancelledError):
            await alive


async def test_is_alive_false_when_no_process():
    t = _StdioTransport(["dummy-server"])
    assert t.is_alive is False  # never started
