"""End-to-end approval gate + audit trail over the real /runtime/* routes.

Proves Milestone 4's acceptance line — "Privileged actions are policy-gated and
auditable" — with the REAL pieces wired together (no fail-open mocks):

    PolicyGate (config rules) → NavigGateway.policy_check → ApprovalManager
    (pending store + respond) → AuditLog (runtime/audit.jsonl)

`mission.create` on POST /runtime/missions is the dangerous operation class
under test; the approver plays the deck Inbox / Telegram side via
``ApprovalManager.respond`` (the same call the /approval/{id}/respond route and
the deck /api/deck/requests/{id}/respond route make).

Regression context (2026-07-16): policy_check's REQUIRE_APPROVAL branch used to
log "pending_approval" and PROCEED — a fail-open gate. These tests pin the
fail-closed contract.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.integration

# ─────────────────────────── helpers ────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_store(tmp_path):
    """Isolate each test with a fresh RuntimeStore in its own temp dir."""
    from navig.contracts.store import reset_runtime_store

    reset_runtime_store(tmp_path / "runtime")
    yield
    reset_runtime_store(tmp_path / "runtime-teardown")


def _build_gateway(tmp_path, *, rules, approval_manager):
    """A gateway stub whose policy_check / PolicyGate / AuditLog are REAL."""
    from navig.gateway import server as srv
    from navig.gateway.audit_log import AuditLog
    from navig.gateway.billing_emitter import BillingEmitter
    from navig.gateway.cooldown import CooldownTracker
    from navig.gateway.policy_gate import PolicyGate

    gw = MagicMock()
    gw.config = SimpleNamespace(auth_token=None)
    gw.policy_gate = PolicyGate.from_config({"policy": {"default": "allow", "rules": rules}})
    gw.audit_log = AuditLog(path=tmp_path / "audit.jsonl")
    gw.billing_emitter = BillingEmitter(log_path=tmp_path / "billing.jsonl")
    gw.cooldown = CooldownTracker(default_cooldown_seconds=0.0)
    gw.approval_manager = approval_manager
    gw.policy_check = lambda *a, **kw: srv.NavigGateway.policy_check(gw, *a, **kw)
    return gw


def _make_manager(audit_log=None, *, timeout_seconds=5):
    from navig.approval import ApprovalManager, ApprovalPolicy

    policy = ApprovalPolicy(timeout_seconds=timeout_seconds, default_action="deny")
    return ApprovalManager(policy=policy, audit_log=audit_log)


def _build_app(gateway):
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.routes.runtime import register

    app = web.Application()
    register(app, gateway)
    return app


async def _respond_when_pending(mgr, approved: bool):
    """Play the human side: wait for the request to appear, then resolve it."""
    for _ in range(500):
        pending = mgr.list_pending()
        if pending:
            ok = await mgr.respond(pending[0].id, approved)
            assert ok is True
            return
        await asyncio.sleep(0.01)
    raise AssertionError("approval request never appeared")


def _audit_records(tmp_path, action=None):
    path = tmp_path / "audit.jsonl"
    if not path.exists():
        return []
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if action:
        records = [r for r in records if r.get("action") == action]
    return records


_REQUIRE_MISSION = [{"pattern": "mission.*", "action": "require_approval"}]
_HEADERS = {"X-Actor": "test:e2e"}


def _node_payload():
    return {
        "node_id": "node-e2e",
        "hostname": "testhost",
        "os": "linux",
        "version": "0.1.0",
        "status": "online",
    }


def _mission_payload():
    return {
        "mission_id": "mission-e2e",
        "node_id": "node-e2e",
        "title": "E2E gated mission",
        "capability": "test",
    }


# ──────────────────── the gate, end to end ───────────────────────────


async def test_mission_create_approved_end_to_end(tmp_path):
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    mgr = _make_manager()
    gw = _build_gateway(tmp_path, rules=_REQUIRE_MISSION, approval_manager=mgr)
    app = _build_app(gw)

    async with TestClient(TestServer(app)) as client:
        # node.register is not covered by the rule → proceeds without approval
        r = await client.post("/runtime/nodes", json=_node_payload(), headers=_HEADERS)
        assert r.status == 201

        # mission.create requires approval → blocks until the human approves
        resp, _ = await asyncio.gather(
            client.post("/runtime/missions", json=_mission_payload(), headers=_HEADERS),
            _respond_when_pending(mgr, approved=True),
        )
        assert resp.status == 201
        body = await resp.json()
        assert body["ok"] is True

    # Audit trail: who/what/when/decision, on the gateway's privileged-action log
    records = _audit_records(tmp_path, action="mission.create")
    statuses = [r["status"] for r in records]
    assert statuses == ["pending_approval", "approved"]
    assert all(r["actor"] == "test:e2e" for r in records)
    assert records[-1]["metadata"]["via"] == "approval_manager"
    assert records[-1]["policy"] == "require_approval"


async def test_mission_create_denied_end_to_end(tmp_path):
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    mgr = _make_manager()
    gw = _build_gateway(tmp_path, rules=_REQUIRE_MISSION, approval_manager=mgr)
    app = _build_app(gw)

    async with TestClient(TestServer(app)) as client:
        resp, _ = await asyncio.gather(
            client.post("/runtime/missions", json=_mission_payload(), headers=_HEADERS),
            _respond_when_pending(mgr, approved=False),
        )
        assert resp.status == 403
        body = await resp.json()
        assert body["ok"] is False
        assert body["error_code"] == "approval_denied"

    records = _audit_records(tmp_path, action="mission.create")
    assert [r["status"] for r in records] == ["pending_approval", "denied"]

    # The mission must NOT exist — the gate ran before the store write.
    from navig.contracts.store import get_runtime_store

    assert get_runtime_store().get_mission("mission-e2e") is None


async def test_mission_create_timeout_defaults_to_deny(tmp_path):
    """No human response + default_action=deny → the gate closes, never opens."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    mgr = _make_manager(timeout_seconds=1)
    gw = _build_gateway(tmp_path, rules=_REQUIRE_MISSION, approval_manager=mgr)
    app = _build_app(gw)

    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/runtime/missions", json=_mission_payload(), headers=_HEADERS)
        assert resp.status == 403
        body = await resp.json()
        assert body["error_code"] == "approval_denied"

    records = _audit_records(tmp_path, action="mission.create")
    assert [r["status"] for r in records] == ["pending_approval", "denied"]


async def test_mission_create_fails_closed_without_manager(tmp_path):
    """require_approval rule + no approval_manager wired → 403, not fail-open."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway(tmp_path, rules=_REQUIRE_MISSION, approval_manager=None)
    app = _build_app(gw)

    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/runtime/missions", json=_mission_payload(), headers=_HEADERS)
        assert resp.status == 403
        body = await resp.json()
        assert body["error_code"] == "approval_unavailable"

    records = _audit_records(tmp_path, action="mission.create")
    assert [r["status"] for r in records] == ["pending_approval", "denied"]
    assert records[-1]["metadata"]["reason"] == "approval_unavailable"


async def test_policy_deny_blocks_mission_create(tmp_path):
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway(
        tmp_path,
        rules=[{"pattern": "mission.*", "action": "deny"}],
        approval_manager=_make_manager(),
    )
    app = _build_app(gw)

    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/runtime/missions", json=_mission_payload(), headers=_HEADERS)
        assert resp.status == 403
        body = await resp.json()
        assert body["error_code"] == "policy_denied"

    records = _audit_records(tmp_path, action="mission.create")
    assert [r["status"] for r in records] == ["denied"]


async def test_allow_actions_do_not_touch_approval(tmp_path):
    """Default-allow actions proceed instantly and are still audited."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway(tmp_path, rules=[], approval_manager=None)
    app = _build_app(gw)

    async with TestClient(TestServer(app)) as client:
        r = await client.post("/runtime/nodes", json=_node_payload(), headers=_HEADERS)
        assert r.status == 201

    records = _audit_records(tmp_path, action="node.register")
    assert [r["status"] for r in records] == ["success"]


# ──────────────────── wiring regression guards ───────────────────────


def test_gateway_wires_policy_from_config_and_audit_log():
    """The live gateway must build ApprovalPolicy from the operator's config and
    hand the ApprovalManager the audit log — both were dropped once, which made
    the `approval:` config section dead AND left every approval decision
    unaudited (and auto-evolve permanently disabled: is_audit_log_live() gates
    it). Source-level guard, same pattern as test_doctor_honesty."""
    import inspect

    from navig.gateway import server as srv

    src = inspect.getsource(srv.NavigGateway._init_autonomous_modules)
    assert "ApprovalPolicy.from_config" in src, "approval policy must come from config"
    assert "audit_log=self.audit_log" in src, "ApprovalManager must receive the audit log"


async def test_verify_mission_records_audit(tmp_path, monkeypatch):
    """Verifier verdicts must land in the gateway audit log. The old call used a
    non-existent record(detail=...) signature and TypeError'd into a bare
    except on every run — verdicts were never audited."""
    from navig.agent import verifier as verifier_mod
    from navig.contracts.mission import Mission
    from navig.gateway.audit_log import AuditLog
    from navig.missions.executor import MissionExecutor

    class _Verdict:
        safe = False
        reason = "destructive"

        def to_dict(self):
            return {"safe": False, "confidence": 0.9, "reason": "destructive"}

    class _Verifier:
        enabled = True

        async def verify_mission(self, mission):
            return _Verdict()

    monkeypatch.setattr(verifier_mod, "get_verifier", lambda: _Verifier())

    gw = MagicMock()
    gw.config_manager.global_config = {}
    gw.audit_log = AuditLog(path=tmp_path / "audit.jsonl")

    ex = MissionExecutor(gw)
    mission = Mission(title="risky thing", capability="agentic")
    verdict = await ex._verify_mission(mission)

    assert verdict is not None and verdict.safe is False
    records = gw.audit_log.tail()
    assert len(records) == 1
    assert records[0]["action"] == "mission.verify"
    assert records[0]["status"] == "denied"
    assert records[0]["actor"] == "mission:verifier"
    assert records[0]["metadata"]["mission"] == mission.mission_id
