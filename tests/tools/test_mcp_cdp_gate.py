"""Stage 0 — the MCP/CDP approval gate.

Before this, the MCP stdio server dispatched every tool directly
(`_execute_tool` → handler) with no approval gate, so an autonomous or
malicious MCP client could call ``cdp_eval`` to run arbitrary JS on a
logged-in page (e.g. read a password field) with zero interlock.

These tests pin the new behaviour:
- ``approval.check_sync`` is the synchronous bridge the sync dispatcher uses.
- ``cdp_eval``/``cdp_launch``/``cdp_stop`` are classified "dangerous".
- ``_execute_tool`` routes dangerous tools through the gate and raises when
  denied; safe tools are never gated.
"""

from __future__ import annotations

import pytest

from navig.tools.approval import (
    ApprovalDecision,
    check_sync,
    get_approval_gate,
    reset_approval_gate,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clean_gate(monkeypatch):
    monkeypatch.delenv("NAVIG_ALLOW_ALL_COMMANDS", raising=False)
    reset_approval_gate()
    yield
    reset_approval_gate()


# ---------------------------------------------------------------------------
# check_sync — the synchronous bridge
# ---------------------------------------------------------------------------


def test_check_sync_safe_is_approved():
    assert check_sync("cdp_snapshot", "safe") == ApprovalDecision.APPROVED


def test_check_sync_dangerous_default_backend_approves():
    # Default single-operator backend logs a warning and approves.
    assert check_sync("cdp_eval", "dangerous") == ApprovalDecision.APPROVED


def test_check_sync_bypass_env_wins(monkeypatch):
    monkeypatch.setenv("NAVIG_ALLOW_ALL_COMMANDS", "1")

    async def deny(_req):
        return ApprovalDecision.DENIED

    get_approval_gate().backend = deny
    # Env bypass short-circuits before the backend is ever consulted.
    assert check_sync("cdp_eval", "dangerous") == ApprovalDecision.APPROVED


def test_check_sync_deny_backend_denies():
    async def deny(_req):
        return ApprovalDecision.DENIED

    get_approval_gate().backend = deny
    assert check_sync("cdp_eval", "dangerous") == ApprovalDecision.DENIED


def test_check_sync_backend_raises_denies():
    async def boom(_req):
        raise RuntimeError("backend down")

    get_approval_gate().backend = boom
    # Fail closed, never fail open.
    assert check_sync("cdp_eval", "dangerous") == ApprovalDecision.DENIED


# ---------------------------------------------------------------------------
# cdp bundle classification
# ---------------------------------------------------------------------------


def test_cdp_bundle_classifies_dangerous_tools():
    from navig.mcp.tools import cdp

    class _FakeServer:
        tools: dict = {}
        _tool_handlers: dict = {}

    srv = _FakeServer()
    srv.tools = {}
    srv._tool_handlers = {}
    cdp.register(srv)

    assert srv._tool_safety["cdp_eval"] == "dangerous"
    assert srv._tool_safety["cdp_launch"] == "dangerous"
    assert srv._tool_safety["cdp_stop"] == "dangerous"
    # read-only tools are not classified (default "safe")
    assert "cdp_snapshot" not in srv._tool_safety
    assert "cdp_targets" not in srv._tool_safety


# ---------------------------------------------------------------------------
# MCPProtocolHandler._execute_tool gating (no heavy registration)
# ---------------------------------------------------------------------------


def _bare_handler():
    """A handler with the gate wiring but no full tool registration."""
    from navig.mcp_server import MCPProtocolHandler

    h = object.__new__(MCPProtocolHandler)
    h._tool_handlers = {}
    h._tool_safety = {}
    return h


def test_execute_tool_denies_dangerous_when_backend_denies():
    async def deny(_req):
        return ApprovalDecision.DENIED

    get_approval_gate().backend = deny

    h = _bare_handler()
    h._tool_handlers["cdp_eval"] = lambda _self, _args: {"ran": True}
    h._tool_safety["cdp_eval"] = "dangerous"

    with pytest.raises(PermissionError):
        h._execute_tool("cdp_eval", {"expression": "1+1"})


def test_execute_tool_runs_dangerous_when_approved():
    h = _bare_handler()
    h._tool_handlers["cdp_eval"] = lambda _self, _args: {"ran": True}
    h._tool_safety["cdp_eval"] = "dangerous"

    # default backend approves-with-audit
    assert h._execute_tool("cdp_eval", {"expression": "1+1"}) == {"ran": True}


def test_execute_tool_never_gates_safe_tool():
    async def deny(_req):
        return ApprovalDecision.DENIED

    get_approval_gate().backend = deny  # would deny if consulted

    h = _bare_handler()
    h._tool_handlers["cdp_snapshot"] = lambda _self, _args: {"ok": True}
    # no _tool_safety entry → "safe" → gate skipped

    assert h._execute_tool("cdp_snapshot", {}) == {"ok": True}
