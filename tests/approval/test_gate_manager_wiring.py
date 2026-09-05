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
import re
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


# A dispatcher may skip the interlock only for a reason written down here, and each
# reason is a CLAIM about a file that was checked before it was written.
_UNGATED_DISPATCHERS_OK: dict[str, str] = {
    "agent/agent_tool_registry.py": (
        "Defines the registry. Its only reference is a usage example in the module "
        "docstring — there is no dispatch call in this file."
    ),
    "tools/router.py": (
        "IS the second registry — it defines `get_tool_router` and the dispatch itself. "
        "Its own policy layer (SafetyLevel x safety_mode, blocked_tools, "
        "require_confirmation) is separate from the approval gate; the gate belongs to "
        "the CALLERS, which are asserted above."
    ),
    "tools/__init__.py": (
        "Re-export only: a `get_tool_router` shim forwarding to `.router`. No dispatch."
    ),
    "agent/speculative.py": (
        "A DELEGATE, not an entry point: it receives `dispatch_fn` and is only ever "
        "reached through a caller that has already gated. Verified — conv/agent.py "
        "gates at :1165 before delegating at :1232, and conversational_legacy.py gates "
        "at :1165 before :1182; both are asserted below. Its own background "
        "speculation is additionally restricted to READ_ONLY_TOOLS in code (not just "
        "in the docstring's 'safety invariants'), so it cannot pre-execute a write."
    ),
}


#: NAVIG has TWO tool registries, and a module that reaches EITHER is a dispatcher.
#:
#: This started as `_AGENT_REGISTRY.dispatch` alone — which is a PATH, not the surface.
#: `agent/conv/executor.py` dispatches through `ToolRouter` instead, so the guard could
#: not see it, and that dispatcher reached `bash_exec` (registered DANGEROUS in the
#: router's exec_pack, with a live handler) carrying no interlock at all. The guard
#: written to stop a fourth ungated dispatcher was blind to the fourth one.
#: The interlock has TWO legitimate entry points, and a guard that accepts only one
#: reports a correctly-gated module as ungated. `gate_agent_tool_call` is the async seam;
#: `check_sync` is the synchronous bridge — what `mcp_server.py` has always used, and the
#: only option inside a sync function like `llm.generate._maybe_execute_tools`.
_INTERLOCK_SEAMS = ("gate_agent_tool_call", "check_sync")

_DISPATCH_SHAPES = ("_AGENT_REGISTRY.dispatch", "get_tool_router(")


def _agent_dispatch_sites() -> dict[str, str]:
    """Every module that reaches EITHER tool registry's dispatch, by shape."""
    import navig

    pkg = Path(navig.__file__).parent
    found: dict[str, str] = {}
    for path in pkg.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        try:
            src = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        if any(shape in src for shape in _DISPATCH_SHAPES):
            found[path.relative_to(pkg).as_posix()] = src
    return found


def test_every_agent_dispatcher_uses_the_fail_closed_interlock():
    """Derived from the SHAPE that makes a module a dispatcher, not a hardcoded list.

    This test used to name two files. NAVIG had three: `agent/plan_execute.py` reached
    `_AGENT_REGISTRY.dispatch` directly and `gate_agent_tool_call` appeared **zero**
    times in it, so `navig agent plan` ran every tool — first-party destructive ones
    included — with no interlock, and nothing here noticed because the guard was not
    looking. A list of known dispatchers cannot protect against the next one.
    """
    sites = _agent_dispatch_sites()

    assert len(sites) >= 3, (
        f"only {len(sites)} dispatch sites found — this guard is looking in the wrong "
        "place and checking nothing."
    )
    for expected in ("agent/conv/agent.py", "agent/plan_execute.py"):
        assert expected in sites, f"{expected} no longer reaches the registry — scan is stale"

    ungated = sorted(
        rel
        for rel, src in sites.items()
        if not any(seam in src for seam in _INTERLOCK_SEAMS)
        and rel not in _UNGATED_DISPATCHERS_OK
    )
    assert not ungated, (
        "These modules dispatch agent tools without the approval interlock, so a "
        "destructive tool runs with no confirmation and no audit line. Call "
        "`gate_agent_tool_call` before dispatching (fail closed — a broken gate must "
        "deny, never proceed), or add a written reason to "
        "_UNGATED_DISPATCHERS_OK:\n" + "\n".join(f"  {u}" for u in ungated)
    )


def test_the_gated_dispatchers_fail_closed():
    """Gating is not enough — the gate's own failure must deny, not proceed.

    The marker is searched after joining implicit string concatenations: a wrapped
    ``"… failing " "closed: %s"`` is the same code as the unwrapped form, and a guard
    that fails on where the author pressed enter teaches people to fight the guard
    rather than write the denial path.
    """
    joined = re.compile(r'"\s*"')
    for rel, src in _agent_dispatch_sites().items():
        if rel in _UNGATED_DISPATCHERS_OK:
            continue
        assert "failing closed" in joined.sub("", src), (
            f"{rel} calls the interlock but has no fail-closed denial path. A gate that "
            "breaks must not become a gate that waves things through."
        )


def test_the_allowlist_entries_still_describe_real_files():
    """An exemption is a claim about a file — a stale one silently exempts code nobody
    reviewed."""
    sites = _agent_dispatch_sites()
    stale = sorted(rel for rel in _UNGATED_DISPATCHERS_OK if rel not in sites)
    assert not stale, (
        f"These exemptions name files that no longer dispatch: {stale}. Remove them, or "
        "the next file to take that path inherits an unreviewed pass."
    )




def test_a_failed_bind_installs_deny_all_not_the_fail_open_default():
    """The one fail-OPEN branch left in the gateway's approval wiring.

    `bind_approval_manager` is called inside `try/except Exception` so a failure can
    never block boot. But leaving the gate untouched restores its single-operator
    default — approve-dangerous-with-a-warning — which is precisely the fail-open state
    the bind exists to remove, in the one process where real approval consumers (deck
    Inbox, Telegram, /approval) are wired. The comment above the block named that
    outcome; the handler only logged a warning and moved on.

    The handler now installs the deny-all backend instead and logs at ERROR: still no
    raise, so boot is never blocked, but an unbindable gate refuses rather than waves
    through.
    """
    from navig.gateway import server as srv

    src = inspect.getsource(srv.NavigGateway._init_autonomous_modules)
    _, _, after = src.partition("ApprovalGate binding failed")

    assert after, "the binding-failure handler should still exist"
    assert "_bind_deny" in after, (
        "a failed bind must install the deny-all backend, not leave the "
        "approve-with-warning default in place"
    )
    assert "logger.error" in after, "a fail-open gate in the gateway is an error, not a warning"
