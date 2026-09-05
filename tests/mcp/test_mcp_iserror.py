"""The MCP dispatch must surface a tool's RETURNED failure as protocol `isError`.

NAVIG's MCP tools report failure by returning a sentinel (`{"error": ...}`,
`{"ok": False}`, `{"success": False}`, or `{"isError": True}`) rather than raising.
`_handle_tools_call` used to set `isError` only for an unknown tool or a *raised*
exception, so every returned-sentinel failure reached the client as a phantom
success. `_result_signals_error` recognizes the conventions and the dispatch hoists
them to the envelope-level `isError`.
"""
from __future__ import annotations

from navig.mcp_server import MCPProtocolHandler, _result_signals_error

# ── the pure classifier ──────────────────────────────────────────────────────


def test_error_conventions_are_flagged():
    assert _result_signals_error({"error": "permission denied"}) is True
    assert _result_signals_error({"isError": True, "error": "x"}) is True
    assert _result_signals_error({"ok": False}) is True
    assert _result_signals_error({"success": False, "message": "install failed"}) is True


def test_success_and_ambiguous_shapes_are_not_flagged():
    assert _result_signals_error({"ok": True, "data": "written"}) is False
    assert _result_signals_error({"success": True}) is False
    assert _result_signals_error({"stdout": "hi", "result": "done"}) is False
    # Falsy/absent error must NOT flag (a success result may carry error=None/"").
    assert _result_signals_error({"error": None}) is False
    assert _result_signals_error({"error": ""}) is False
    # A non-zero returncode is deliberately NOT treated as an error (grep/diff/test
    # exit non-zero normally); the tool must add {"ok": False} to signal failure.
    assert _result_signals_error({"stdout": "", "returncode": 1}) is False
    # Non-dict results (bare strings/None) are never auto-flagged.
    assert _result_signals_error("Error: could not read file") is False
    assert _result_signals_error(None) is False
    assert _result_signals_error(["a", "b"]) is False


# ── the dispatch hoist ───────────────────────────────────────────────────────


def _bare_handler() -> MCPProtocolHandler:
    """A dispatch instance without the heavy __init__ (no tool registration)."""
    h = MCPProtocolHandler.__new__(MCPProtocolHandler)
    h.tools = {}
    h._tool_handlers = {}
    h._tool_safety = {}  # every tool "safe" → gate is a no-op
    return h


def _register(h: MCPProtocolHandler, name: str, result):
    h.tools[name] = {"name": name}
    h._tool_handlers[name] = lambda _server, _args: result


def test_returned_error_becomes_protocol_iserror():
    h = _bare_handler()
    _register(h, "write", {"error": "Permission denied"})
    resp = h._handle_tools_call({"name": "write", "arguments": {}})
    assert resp.get("isError") is True  # NOT a phantom success


def test_ok_false_becomes_protocol_iserror():
    h = _bare_handler()
    _register(h, "install", {"ok": False, "message": "service install failed"})
    resp = h._handle_tools_call({"name": "install", "arguments": {}})
    assert resp.get("isError") is True


def test_success_result_carries_no_iserror():
    h = _bare_handler()
    _register(h, "read", {"ok": True, "content": "file body"})
    resp = h._handle_tools_call({"name": "read", "arguments": {}})
    assert "isError" not in resp
    assert resp["content"][0]["type"] == "text"


def test_unknown_tool_still_iserror():
    h = _bare_handler()
    resp = h._handle_tools_call({"name": "nope", "arguments": {}})
    assert resp.get("isError") is True
