"""MCP over stdio: opted-in tools run off the reader, and notifications/cancelled reaches them.

Drives the real ``run_stdio`` loop through OS pipes with a stand-in slow tool, so this
proves the protocol behaviour without a browser (the browser path is
``apps/dock/extension/harness/bridge_e2e.py``).

The ``sys.stdin`` / ``sys.stdout`` swap happens at CALL start, not in fixture setup:
pytest's capture re-binds ``sys.stdout`` between setup and the call, which silently
reverted a setup-phase patch — the server then answered into pytest's capture instead
of the test's pipe and the test hung (diagnosed via captured-stdout output).
"""

from __future__ import annotations

import io
import json
import os
import sys
import threading
import time


def test_slow_tool_does_not_block_the_reader_and_cancel_reaches_it(monkeypatch):
    from navig.mcp_server import MCPProtocolHandler

    handler = MCPProtocolHandler()
    released = threading.Event()
    cancelled = threading.Event()

    def slow(server, args):  # noqa: ARG001
        server.on_cancel(cancelled.set)
        cancelled.wait(5)
        released.set()
        return {"ok": False, "error": "cancelled"} if cancelled.is_set() else {"ok": True}

    handler.tools["test_slow"] = {"name": "test_slow", "description": "", "inputSchema": {"type": "object"}}
    handler._tool_handlers["test_slow"] = slow
    handler._tool_safety["test_slow"] = "safe"
    handler._concurrent_tools.add("test_slow")

    r_in, w_in = os.pipe()
    r_out, w_out = os.pipe()
    stdin = io.TextIOWrapper(os.fdopen(r_in, "rb"), encoding="utf-8")
    stdout = io.TextIOWrapper(os.fdopen(w_out, "wb"), encoding="utf-8", line_buffering=True)
    # Call-phase patch — see the module docstring.
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    assert sys.stdout is stdout, "the server must write into this test's pipe, not pytest's capture"
    loop = threading.Thread(target=handler.run_stdio, daemon=True)
    loop.start()
    writer = os.fdopen(w_in, "w", encoding="utf-8", buffering=1)
    reader = os.fdopen(r_out, "r", encoding="utf-8")

    def send(obj):
        writer.write(json.dumps(obj) + "\n")
        writer.flush()

    try:
        _scenario(send, reader, released, cancelled)
    finally:
        writer.close()
        loop.join(5)


def _read_until(read_lines, want_id, timeout=8.0):
    """Wait for the response with *want_id* (responses are collected in a thread, so a
    server that never answers fails the test instead of hanging the run)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for line in read_lines():
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") == want_id:
                return msg
        time.sleep(0.02)
    raise AssertionError(f"no response for id {want_id} within {timeout}s (got: {read_lines()})")


def _scenario(send, reader, released, cancelled):
    seen: list[int] = []
    box: list[str] = []
    threading.Thread(target=lambda: [box.append(l.rstrip()) for l in reader], daemon=True).start()

    def read_lines():
        return list(box)

    send({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "test_slow", "arguments": {}}})
    time.sleep(0.1)
    t0 = time.perf_counter()
    send({"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}})
    _read_until(read_lines, 2)
    assert time.perf_counter() - t0 < 1.0, "the reader answered while the slow tool was still running"
    assert not released.is_set()

    send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1, "reason": "test"}})
    assert cancelled.wait(2), "the cancel hook the tool registered ran"
    assert released.wait(2)
    send({"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}})
    _read_until(read_lines, 3)
    time.sleep(0.3)
    for line in read_lines():
        seen.append(json.loads(line).get("id"))
    assert 1 not in seen, "a cancelled request gets no response"


def test_tools_that_did_not_opt_in_stay_in_order():
    from navig.mcp_server import MCPProtocolHandler

    h = MCPProtocolHandler()
    assert not h._runs_concurrently({"id": 1, "method": "tools/call", "params": {"name": "cdp_click"}})
    assert h._runs_concurrently({"id": 1, "method": "tools/call", "params": {"name": "dock_act"}})
    assert not h._runs_concurrently({"method": "tools/call", "params": {"name": "dock_act"}}), "notifications never"
