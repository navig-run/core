"""The approval gate, driven end to end with the REAL tool registry.

`tests/tools/test_mcp_cdp_gate.py` covers the mechanics on a hand-built handler with two
tools in it. That is the right unit test, and it passed throughout the period when 61 of
122 real tools were unclassified — because it never registers the real registry.

These tests drive `MCPProtocolHandler._execute_tool` against `register_all_tools`, which
is the only way to catch "the mechanism works, and almost nothing is wired to it". They
pin, for tools that actually ship:

* a dangerous tool is gated, and the handler does not run when the gate denies;
* a denial reaches the MCP client as `isError`, not as a phantom success;
* a safe tool never consults the gate at all (the zero-overhead promise);
* the audit line names the tool and its argument KEYS, and never a value — a secret can
  live in `cdp_type`'s `text` or `cdp_eval`'s `expression`;
* the gate fails CLOSED when the approval backend cannot be imported.

The handler is built with `object.__new__` because `MCPProtocolHandler.__init__` wires
transports; only the dispatch attributes matter here.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from navig.tools.approval import ApprovalDecision, get_approval_gate, reset_approval_gate

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clean_gate(monkeypatch):
    """No env bypass, and a fresh gate per test so a backend cannot leak."""
    monkeypatch.delenv("NAVIG_ALLOW_ALL_COMMANDS", raising=False)
    reset_approval_gate()
    yield
    reset_approval_gate()


@pytest.fixture
def server():
    """A dispatcher with the REAL registry loaded."""
    from navig.mcp.tools import register_all_tools
    from navig.mcp_server import MCPProtocolHandler

    srv = object.__new__(MCPProtocolHandler)
    srv.tools = {}
    srv._tool_handlers = {}
    register_all_tools(srv)
    return srv


def _stub(server, name: str, calls: list) -> None:
    """Replace a real handler so nothing actually executes."""
    server._tool_handlers[name] = lambda _s, args: calls.append(args) or {"ok": True}


# ── a shipped dangerous tool is really gated ────────────────────────────────────


def test_a_dangerous_tool_runs_when_the_gate_approves(server) -> None:
    calls: list = []
    _stub(server, "desktop_powershell", calls)

    assert server._execute_tool("desktop_powershell", {"command": "Get-Process"}) == {"ok": True}
    assert calls == [{"command": "Get-Process"}]


def test_a_dangerous_tool_is_blocked_when_the_gate_denies(server) -> None:
    calls: list = []
    _stub(server, "desktop_powershell", calls)

    async def deny(_req):
        return ApprovalDecision.DENIED

    get_approval_gate().backend = deny

    with pytest.raises(PermissionError):
        server._execute_tool("desktop_powershell", {"command": "Get-Process"})
    assert calls == [], "the handler must not run after a denial"


def test_a_denial_reaches_the_client_as_an_error(server) -> None:
    """A blocked tool must not look like a successful call that returned nothing."""
    _stub(server, "desktop_powershell", [])
    server.tools["desktop_powershell"] = {"name": "desktop_powershell"}

    async def deny(_req):
        return ApprovalDecision.DENIED

    get_approval_gate().backend = deny

    envelope = server._handle_tools_call(
        {"name": "desktop_powershell", "arguments": {"command": "x"}}
    )

    assert envelope.get("isError") is True
    assert "not approved" in envelope["content"][0]["text"]


def test_a_connector_write_tool_is_gated(server) -> None:
    """`connector_*_act` sends mail / deletes messages — it must reach the gate."""
    act = sorted(
        n for n in server._tool_handlers if n.startswith("connector_") and n.endswith("_act")
    )
    if not act:
        pytest.skip("no connector with write capability is registered here")
    name = act[0]
    calls: list = []
    _stub(server, name, calls)

    async def deny(_req):
        return ApprovalDecision.DENIED

    get_approval_gate().backend = deny

    with pytest.raises(PermissionError):
        server._execute_tool(name, {"action_type": "send"})
    assert calls == []


# ── safe tools stay free ────────────────────────────────────────────────────────


def test_a_safe_tool_never_consults_the_gate(server) -> None:
    """The zero-overhead promise: a read-only call must not pay for the gate."""
    calls: list = []
    _stub(server, "navig_list_hosts", calls)

    with patch(
        "navig.tools.approval.check_sync", side_effect=AssertionError("gate was consulted")
    ):
        assert server._execute_tool("navig_list_hosts", {}) == {"ok": True}
    assert calls == [{}]


# ── the audit trail ─────────────────────────────────────────────────────────────


def test_the_audit_line_names_the_tool_and_its_arg_keys(server, caplog) -> None:
    _stub(server, "desktop_powershell", [])

    # navig loggers set propagate=False, so caplog needs the logger attached directly.
    logger = logging.getLogger("navig.mcp_server")
    with caplog.at_level(logging.WARNING, logger="navig.mcp_server"):
        logger.propagate = True
        try:
            server._execute_tool("desktop_powershell", {"command": "Get-Process"})
        finally:
            logger.propagate = False

    line = "\n".join(r.getMessage() for r in caplog.records)
    assert "[mcp.gate]" in line
    assert "desktop_powershell" in line
    assert "dangerous" in line
    assert "command" in line, "the argument KEY should be recorded"


def test_the_audit_line_never_records_an_argument_value(server) -> None:
    """A secret can live in cdp_type's `text` or cdp_eval's `expression`."""
    _stub(server, "cdp_eval", [])
    secret = "hunter2-do-not-log-this"

    records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    logger = logging.getLogger("navig.mcp_server")
    handler = _Capture()
    logger.addHandler(handler)
    try:
        server._execute_tool("cdp_eval", {"expression": secret})
    finally:
        logger.removeHandler(handler)

    joined = "\n".join(records)
    assert "expression" in joined, "the key should be there"
    assert secret not in joined, "the VALUE must never be logged"


# ── fail closed ─────────────────────────────────────────────────────────────────


def test_the_gate_fails_closed_when_approval_cannot_load(server) -> None:
    """An unavailable gate must block a dangerous tool, not wave it through."""
    calls: list = []
    _stub(server, "desktop_powershell", calls)

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def broken(name, *a, **k):
        if name == "navig.tools.approval":
            raise ImportError("approval backend unavailable")
        return real_import(name, *a, **k)

    with patch("builtins.__import__", side_effect=broken):
        with pytest.raises(PermissionError, match="approval gate unavailable"):
            server._execute_tool("desktop_powershell", {"command": "x"})
    assert calls == []


def test_every_dangerous_tool_would_reach_the_gate(server) -> None:
    """No dangerous tool can be missing from the registry the gate reads."""
    dangerous = {n for n, lvl in server._tool_safety.items() if lvl == "dangerous"}
    registered = set(server._tool_handlers)

    assert dangerous, "no dangerous tools registered — the check would be vacuous"
    orphaned = sorted(dangerous - registered)
    # A level for a connector that is not installed here is expected.
    assert not [o for o in orphaned if not o.startswith("connector_")], (
        f"classified dangerous but not registered: {orphaned}"
    )
