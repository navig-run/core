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
    # Read-only tools are now classified EXPLICITLY as "safe" rather than left out.
    # This assertion used to be `"cdp_snapshot" not in srv._tool_safety`, which pinned
    # the older design where absence meant safe. Absence is indistinguishable from
    # "nobody classified it", and that ambiguity is exactly what let 61 of 122 tools —
    # `desktop_powershell` and every `connector_*_act` among them — run ungated and
    # unaudited. Naming them is the stronger statement, so the check is now positive.
    assert srv._tool_safety["cdp_snapshot"] == "safe"
    assert srv._tool_safety["cdp_targets"] == "safe"
    # The one cdp tool that WRITES: a remote page chooses the filename (a
    # Content-Disposition of "..\..\x.exe" is observable against the local fixture) and
    # the agent never sees the bytes it would be vouching for.
    assert srv._tool_safety["cdp_download"] == "dangerous"


def test_cdp_download_is_gated_on_both_paths_and_cannot_choose_where_it_writes():
    """The two registries must agree (parity), and the surface must refuse a destination.

    A caller-chosen path turns "a file arrived from this page" into "write bytes anywhere
    this process can reach" — the class `desktop_filesystem` is gated for. It also has to
    survive the definition budget, whose ceiling for tools lacking routing guidance or a
    stated result is ZERO.
    """
    from unittest.mock import MagicMock

    from navig.mcp.annotations import annotate_tools
    from navig.mcp.catalogue_audit import signals
    from navig.mcp.tools import register_all_tools
    from navig.tools.approval import is_destructive_tool

    srv = MagicMock()
    srv.tools = {}
    srv._tool_handlers = {}
    del srv._tool_safety
    register_all_tools(srv)
    annotate_tools(srv.tools, srv._tool_safety)

    assert "cdp_download" in srv.tools, "not advertised"
    assert srv._tool_safety["cdp_download"] == "dangerous"  # the MCP gate
    assert is_destructive_tool("cdp_download")  # the agent gate

    tool = srv.tools["cdp_download"]
    assert tool["annotations"]["readOnlyHint"] is False
    assert tool["annotations"]["destructiveHint"] is True

    sig = signals(tool)
    assert sig["mentions_sibling_tool"], "name the tool that continues the workflow"
    assert sig["states_result"], "say what comes back"
    assert sig["all_params_documented"], "every parameter needs a description"
    assert sig["strict_schema"], "additionalProperties must be false"

    props = set(tool["inputSchema"]["properties"])
    assert not ({"path", "out_dir", "out", "file", "dir", "directory"} & props), sorted(props)


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
