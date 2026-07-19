"""ApprovalGate → ApprovalManager wiring (the agent tool-execution seam).

Regression context (2026-07-16): #299 closed the gateway policy_check fail-open
seam, but the AGENT tool-execution gate (navig.tools.approval.ApprovalGate —
consulted by both agent editions before a destructive tool runs) still
defaulted dangerous→approve-with-warning even inside the gateway, where real
approval consumers (deck Inbox / Telegram / /approval routes) exist. Worse,
both agent dispatch loops swallowed EVERY gate exception and proceeded.

These tests pin the new contract:

- ``bind_approval_manager(mgr, audit)``: gated tools block on
  ``ApprovalManager.request_approval``; approve/deny/timeout each resolve the
  gate; every decision is audited as ``tool.execute.<tool_name>``.
- ``bind_approval_manager(None, audit)``: gateway with no approval subsystem
  → DENY, audited — never approve-with-warning.
- ``gate_agent_tool_call``: the shared agent-loop interlock returns a clean
  denial STRING (the agent reads it as the tool result — no exception crash)
  and fails CLOSED when the gate itself breaks.
- Non-gateway contexts (nothing bound) keep the single-operator default.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

import pytest

from navig.tools.approval import (
    ApprovalDecision,
    ApprovalPolicy,
    bind_approval_manager,
    gate_agent_tool_call,
    get_approval_gate,
    reset_approval_gate,
    set_approval_policy,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _isolated_gate(monkeypatch):
    """Fresh singleton gate + default policy per test; never leak a bound
    backend into the rest of the suite (the gate is process-global)."""
    monkeypatch.delenv("NAVIG_ALLOW_ALL_COMMANDS", raising=False)
    reset_approval_gate()
    set_approval_policy(ApprovalPolicy.CONFIRM_DESTRUCTIVE)
    yield
    reset_approval_gate()
    set_approval_policy(ApprovalPolicy.CONFIRM_DESTRUCTIVE)


def _make_manager(audit_log=None, *, timeout_seconds=5):
    from navig.approval import ApprovalManager
    from navig.approval import ApprovalPolicy as ManagerPolicy

    policy = ManagerPolicy(timeout_seconds=timeout_seconds, default_action="deny")
    return ApprovalManager(policy=policy, audit_log=audit_log)


def _audit(tmp_path):
    from navig.gateway.audit_log import AuditLog

    return AuditLog(path=tmp_path / "audit.jsonl")


def _records(tmp_path, action=None):
    path = tmp_path / "audit.jsonl"
    if not path.exists():
        return []
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if action:
        records = [r for r in records if r.get("action") == action]
    return records


async def _respond_when_pending(mgr, approved: bool):
    """Play the deck-Inbox/Telegram side: resolve the request once it appears."""
    for _ in range(500):
        pending = mgr.list_pending()
        if pending:
            ok = await mgr.respond(pending[0].id, approved)
            assert ok is True
            return
        await asyncio.sleep(0.01)
    raise AssertionError("approval request never appeared")


# ─────────────────── manager-bound gate, end to end ───────────────────


async def test_bound_gate_approves_via_manager_and_audits(tmp_path):
    audit = _audit(tmp_path)
    mgr = _make_manager(audit)
    bind_approval_manager(mgr, audit)

    gate = get_approval_gate()
    decision, _ = await asyncio.gather(
        gate.check(
            "bash_exec",
            "dangerous",
            parameters={"command": "ls /"},
            context={"session_key": "telegram:user:123"},
        ),
        _respond_when_pending(mgr, approved=True),
    )
    assert decision == ApprovalDecision.APPROVED

    records = _records(tmp_path, action="tool.execute.bash_exec")
    assert [r["status"] for r in records] == ["pending_approval", "approved"]
    assert all(r["actor"] == "telegram:user:123" for r in records)
    assert all(r["policy"] == "require_approval" for r in records)
    assert records[-1]["metadata"]["via"] == "approval_manager"
    # parameters are hashed, never stored verbatim
    assert "ls /" not in (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert records[0]["input_hash"].startswith("sha256:")


async def test_bound_gate_denies_via_manager_and_audits(tmp_path):
    audit = _audit(tmp_path)
    mgr = _make_manager(audit)
    bind_approval_manager(mgr, audit)

    decision, _ = await asyncio.gather(
        get_approval_gate().check("cdp_eval", "dangerous"),
        _respond_when_pending(mgr, approved=False),
    )
    assert decision == ApprovalDecision.DENIED

    records = _records(tmp_path, action="tool.execute.cdp_eval")
    assert [r["status"] for r in records] == ["pending_approval", "denied"]
    assert records[0]["actor"] == "agent:local"  # no session context → local operator


async def test_bound_gate_timeout_follows_default_action_deny(tmp_path):
    """No human response + default_action=deny → the gate closes, never opens."""
    audit = _audit(tmp_path)
    mgr = _make_manager(audit, timeout_seconds=1)
    bind_approval_manager(mgr, audit)

    decision = await get_approval_gate().check("bash_exec", "dangerous")
    assert decision == ApprovalDecision.DENIED

    records = _records(tmp_path, action="tool.execute.bash_exec")
    assert [r["status"] for r in records] == ["pending_approval", "denied"]


async def test_bound_gate_manager_crash_fails_closed(tmp_path):
    audit = _audit(tmp_path)

    class _ExplodingManager:
        async def request_approval(self, **kwargs):
            raise RuntimeError("approval flow crashed")

    bind_approval_manager(_ExplodingManager(), audit)
    decision = await get_approval_gate().check("bash_exec", "dangerous")
    assert decision == ApprovalDecision.DENIED
    records = _records(tmp_path, action="tool.execute.bash_exec")
    assert [r["status"] for r in records] == ["pending_approval", "denied"]


async def test_bind_none_denies_and_audits_approval_unavailable(tmp_path):
    """Gateway whose approval subsystem failed to load: DENY, never warn+run."""
    audit = _audit(tmp_path)
    bind_approval_manager(None, audit)

    decision = await get_approval_gate().check("bash_exec", "dangerous")
    assert decision == ApprovalDecision.DENIED

    records = _records(tmp_path, action="tool.execute.bash_exec")
    assert [r["status"] for r in records] == ["denied"]
    assert records[0]["metadata"]["reason"] == "approval_unavailable"


async def test_bound_gate_leaves_safe_tools_alone(tmp_path):
    """Safe/moderate tools never reach the manager — zero prompt, zero audit."""
    audit = _audit(tmp_path)
    mgr = _make_manager(audit)
    bind_approval_manager(mgr, audit)

    decision = await get_approval_gate().check("read_file", "safe")
    assert decision == ApprovalDecision.APPROVED
    assert _records(tmp_path) == []
    assert mgr.list_pending() == []


async def test_manager_policy_disabled_respects_operator_config(tmp_path):
    """`approval.enabled: false` in config flows through: request_approval
    short-circuits to approve — the operator's config section stays live."""
    from navig.approval import ApprovalManager
    from navig.approval import ApprovalPolicy as ManagerPolicy

    audit = _audit(tmp_path)
    policy = ManagerPolicy.from_config({"approval": {"enabled": False}})
    bind_approval_manager(ApprovalManager(policy=policy, audit_log=audit), audit)

    decision = await get_approval_gate().check("bash_exec", "dangerous")
    assert decision == ApprovalDecision.APPROVED


# ─────────────────── gate_agent_tool_call — the agent-loop interlock ──────────


async def test_agent_interlock_returns_none_for_ungated_tool():
    assert await gate_agent_tool_call("read_file") is None


async def test_agent_interlock_returns_denial_string_not_exception():
    async def deny(req):
        return ApprovalDecision.DENIED

    get_approval_gate().backend = deny
    msg = await gate_agent_tool_call("bash_exec", parameters={"command": "rm -rf /"})
    assert isinstance(msg, str)
    assert "bash_exec" in msg and msg.startswith("[Denied")


async def test_agent_interlock_fails_closed_when_gate_itself_breaks(monkeypatch):
    """The old seam swallowed gate exceptions and RAN the tool. Now: deny."""
    import navig.tools.approval as approval_mod

    def _boom():
        raise RuntimeError("gate machinery broken")

    monkeypatch.setattr(approval_mod, "get_approval_gate", _boom)
    msg = await approval_mod.gate_agent_tool_call("bash_exec")
    assert isinstance(msg, str)
    assert "failing closed" in msg


async def test_agent_interlock_preserves_single_operator_default():
    """Non-gateway context (nothing bound): dangerous tools still auto-approve
    with a warning — headless CLI / tests keep working unchanged."""
    msg = await gate_agent_tool_call("bash_exec", parameters={"command": "echo hi"})
    assert msg is None


# ─────────────────── wiring regression guards (source-level) ──────────────────


def test_gateway_binds_gate_to_approval_manager():
    """The live gateway must bind the agent ApprovalGate to its
    ApprovalManager + AuditLog at startup — the bind IS the fix; if it is
    removed the gate silently reverts to approve-with-warning."""
    from navig.gateway import server as srv

    src = inspect.getsource(srv.NavigGateway._init_autonomous_modules)
    assert "bind_approval_manager" in src, "gateway must bind ApprovalGate to ApprovalManager"
    assert "self.audit_log" in src


def test_both_agent_dispatch_seams_use_the_fail_closed_interlock():
    """Both agent editions must route tool calls through gate_agent_tool_call
    (fail closed) — the old inline block swallowed gate exceptions and ran the
    tool anyway."""
    import navig

    pkg = Path(navig.__file__).parent
    for rel in ("agent/conv/agent.py", "agent/conversational_legacy.py"):
        src = (pkg / rel).read_text(encoding="utf-8")
        assert "gate_agent_tool_call" in src, f"{rel} lost the approval interlock"
        assert "failing closed" in src, f"{rel} lost the fail-closed denial path"
