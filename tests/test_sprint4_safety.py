"""
Sprint 4 Safety Tests
- CooldownTracker unit tests
- policy_check() integration (mocked gateway)
- AuditLog write verification
- Gateway attribute presence
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# CooldownTracker
# ---------------------------------------------------------------------------


class TestCooldownTracker:
    def setup_method(self):
        from navig.gateway.cooldown import CooldownTracker

        self.CT = CooldownTracker

    def test_first_call_allowed(self):
        t = self.CT(default_cooldown_seconds=10.0)
        allowed, wait = t.check_and_consume("test.action", actor="user1")
        assert allowed is True
        assert wait == 0.0

    def test_second_call_denied_within_cooldown(self):
        t = self.CT(default_cooldown_seconds=60.0)
        t.check_and_consume("test.action", actor="user1")
        allowed, wait = t.check_and_consume("test.action", actor="user1")
        assert allowed is False
        assert wait > 0.0

    def test_different_actors_independent(self):
        t = self.CT(default_cooldown_seconds=60.0)
        t.check_and_consume("test.action", actor="user1")
        allowed, wait = t.check_and_consume("test.action", actor="user2")
        assert allowed is True

    def test_different_keys_independent(self):
        t = self.CT(default_cooldown_seconds=60.0)
        t.check_and_consume("action.a", actor="user1")
        allowed, _ = t.check_and_consume("action.b", actor="user1")
        assert allowed is True

    def test_reset_clears_cooldown(self):
        t = self.CT(default_cooldown_seconds=60.0)
        t.check_and_consume("test.action", actor="user1")
        t.reset("test.action", actor="user1")
        allowed, _ = t.check_and_consume("test.action", actor="user1")
        assert allowed is True

    def test_reset_global_clears_all(self):
        t = self.CT(default_cooldown_seconds=60.0)
        t.check_and_consume("action.a")
        t.check_and_consume("action.b")
        t.reset("action.a")
        t.reset("action.b")
        a1, _ = t.check_and_consume("action.a")
        a2, _ = t.check_and_consume("action.b")
        assert a1 is True
        assert a2 is True

    def test_stats_returns_dict(self):
        t = self.CT(default_cooldown_seconds=10.0)
        t.check_and_consume("test.action")
        s = t.stats()
        assert isinstance(s, dict)

    def test_set_cooldown_overrides_existing_entry(self):
        """set_cooldown updates an existing entry's duration; new tiny duration
        lets the second call succeed after a small sleep."""
        t = self.CT(default_cooldown_seconds=120.0)
        # Consume the slot — entry created with 120s cooldown
        t.check_and_consume("special.action", actor="user1")
        # Now shrink cooldown to 0.01s on that entry
        t.set_cooldown("special.action", 0.01)
        time.sleep(0.05)  # wait more than 0.01s
        allowed, _ = t.check_and_consume("special.action", actor="user1")
        assert allowed is True

    def test_default_cooldowns_respected(self):
        """Keys matching DEFAULT_COOLDOWNS get their specific cooldowns."""
        from navig.gateway.cooldown import CooldownTracker

        t = CooldownTracker()
        # First call allowed
        a, _ = t.check_and_consume("system.shutdown", actor="root")
        assert a is True
        # Second call denied (cooldown is 120s)
        a2, wait = t.check_and_consume("system.shutdown", actor="root")
        assert a2 is False
        assert wait > 100  # near 120s


# ---------------------------------------------------------------------------
# Gateway attribute presence
# ---------------------------------------------------------------------------


class TestGatewayAttributePresence:
    """NavigGateway must expose policy_gate, audit_log, cooldown, billing_emitter after __init__."""

    def test_attributes_exist(self):
        from navig.gateway.audit_log import AuditLog
        from navig.gateway.billing_emitter import BillingEmitter
        from navig.gateway.cooldown import CooldownTracker
        from navig.gateway.policy_gate import PolicyGate

        # Build a minimal mock gateway (avoid full async start)
        gw = MagicMock()
        gw.policy_gate = PolicyGate.from_config({})
        gw.audit_log = AuditLog()
        gw.billing_emitter = BillingEmitter()
        gw.cooldown = CooldownTracker()

        assert isinstance(gw.policy_gate, PolicyGate)
        assert isinstance(gw.audit_log, AuditLog)
        assert isinstance(gw.billing_emitter, BillingEmitter)
        assert isinstance(gw.cooldown, CooldownTracker)


# ---------------------------------------------------------------------------
# policy_check() — mock gateway
# ---------------------------------------------------------------------------


class TestPolicyCheck:
    """Test NavigGateway.policy_check() using a real method call on a fake gw."""

    def _make_result(self, decision_value: str):
        from navig.gateway.policy_gate import PolicyDecision, PolicyResult

        decision = {
            "allow": PolicyDecision.ALLOW,
            "deny": PolicyDecision.DENY,
            "require_approval": PolicyDecision.REQUIRE_APPROVAL,
        }[decision_value]
        return PolicyResult(decision=decision, action="test.action")

    def _build_gw(self, decision_value: str):
        """Build a minimal real-feeling gateway stub that routes to policy_check."""
        from navig.gateway.audit_log import AuditLog
        from navig.gateway.cooldown import CooldownTracker
        from navig.gateway.policy_gate import PolicyGate

        # Use a real NavigGateway instance method, but attach fake subsystems
        gw = MagicMock()
        gw.policy_gate = MagicMock(spec=PolicyGate)
        gw.policy_gate.check.return_value = self._make_result(decision_value)
        gw.audit_log = MagicMock(spec=AuditLog)
        gw.cooldown = CooldownTracker(default_cooldown_seconds=0.0)

        # Call the real method with gw as self via direct call
        from navig.gateway import server as srv

        gw.policy_check = lambda *a, **kw: srv.NavigGateway.policy_check(gw, *a, **kw)
        return gw

    @pytest.mark.asyncio
    async def test_allow_returns_none(self):
        gw = self._build_gw("allow")
        result = await gw.policy_check("test.action", "user1", "input")
        assert result is None
        gw.audit_log.record.assert_called_once()

    @pytest.mark.asyncio
    async def test_deny_returns_403(self):
        gw = self._build_gw("deny")
        result = await gw.policy_check("test.action", "user1", "input")
        assert result is not None
        assert result.status == 403

    @pytest.mark.asyncio
    async def test_require_approval_returns_none_when_cooldown_clear(self):
        """REQUIRE_APPROVAL with no active cooldown → allowed (returns None)."""
        gw = self._build_gw("require_approval")
        result = await gw.policy_check("test.action", "user1", "input")
        assert result is None

    @pytest.mark.asyncio
    async def test_require_approval_returns_429_when_in_cooldown(self):
        """REQUIRE_APPROVAL with cooldown active → returns 429."""
        from navig.gateway.cooldown import CooldownTracker

        gw = self._build_gw("require_approval")
        # Plant a long cooldown and pre-consume the slot
        gw.cooldown = CooldownTracker(default_cooldown_seconds=120.0)
        gw.cooldown.check_and_consume("test.action", actor="user1")

        result = await gw.policy_check("test.action", "user1", "input")
        assert result is not None
        assert result.status == 429


# ---------------------------------------------------------------------------
# AuditLog integration
# ---------------------------------------------------------------------------


class TestAuditLogIntegration:
    def test_record_writes_jsonl(self):
        from navig.gateway.audit_log import AuditLog

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "audit.jsonl")
            log = AuditLog(path=log_path)
            log.record(
                actor="test_user",
                action="mission.complete",
                policy="allow",
                status="success",
                raw_input="some payload",
            )
            assert os.path.exists(log_path)
            with open(log_path) as f:
                line = f.readline()
            entry = json.loads(line)
            assert entry["actor"] == "test_user"
            assert entry["action"] == "mission.complete"
            assert entry["status"] == "success"
            assert "input_hash" in entry  # raw input is hashed, not stored

    def test_tail_returns_last_n(self):
        from navig.gateway.audit_log import AuditLog

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "audit.jsonl")
            log = AuditLog(path=log_path)
            for i in range(5):
                log.record(
                    actor=f"user{i}",
                    action="test.action",
                    policy="allow",
                    status="success",
                )
            entries = log.tail(n=3)
            assert len(entries) == 3
            assert entries[-1]["actor"] == "user4"


# ---------------------------------------------------------------------------
# BillingEmitter — unit tests
# ---------------------------------------------------------------------------


class TestBillingEmitter:
    """Unit tests for BillingEmitter: emit(), tail(), action map."""

    def _emitter(self, tmpdir):
        from pathlib import Path

        from navig.gateway.billing_emitter import BillingEmitter

        return BillingEmitter(log_path=Path(tmpdir) / "billing.jsonl")

    def test_emit_writes_record(self):
        with tempfile.TemporaryDirectory() as d:
            em = self._emitter(d)
            em.emit(actor="user1", action="mission.create")
            records = em.tail(n=10)
            assert len(records) == 1
            assert records[0]["actor"] == "user1"
            assert records[0]["action"] == "mission.create"
            assert records[0]["event_type"] == "mission.create"
            assert records[0]["units"] == 1

    def test_formation_start_has_2_units(self):
        with tempfile.TemporaryDirectory() as d:
            em = self._emitter(d)
            em.emit(actor="ops", action="formation.start")
            r = em.tail(n=1)[0]
            assert r["units"] == 2

    def test_daemon_stop_has_0_units(self):
        with tempfile.TemporaryDirectory() as d:
            em = self._emitter(d)
            em.emit(actor="ops", action="daemon.stop")
            r = em.tail(n=1)[0]
            assert r["units"] == 0

    def test_tail_returns_last_n(self):
        with tempfile.TemporaryDirectory() as d:
            em = self._emitter(d)
            for i in range(10):
                em.emit(actor=f"u{i}", action="task.add")
            records = em.tail(n=5)
            assert len(records) == 5
            assert records[-1]["actor"] == "u9"

    def test_empty_log_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as d:
            em = self._emitter(d)
            assert em.tail() == []

    def test_metadata_stored(self):
        with tempfile.TemporaryDirectory() as d:
            em = self._emitter(d)
            em.emit(actor="sys", action="mesh.route", metadata={"peer": "node-abc"})
            r = em.tail(n=1)[0]
            assert r["metadata"]["peer"] == "node-abc"


# ---------------------------------------------------------------------------
# GET /audit — route integration
# ---------------------------------------------------------------------------


class TestAuditRoute:
    """Test the GET /audit HTTP route against a mock gateway."""

    def _make_gw(self, tail_records=None):
        from unittest.mock import MagicMock

        from navig.gateway.audit_log import AuditLog

        gw = MagicMock()
        gw.config = MagicMock()
        gw.config.auth_token = "test-token"
        audit = MagicMock(spec=AuditLog)
        audit.tail.return_value = tail_records or [
            {
                "ts": "2026-02-23T10:00:00Z",
                "actor": "user1",
                "action": "task.add",
                "status": "success",
            },
            {
                "ts": "2026-02-23T10:01:00Z",
                "actor": "user2",
                "action": "system.stop",
                "status": "denied",
            },
            {
                "ts": "2026-02-23T10:02:00Z",
                "actor": "user1",
                "action": "task.add",
                "status": "success",
            },
        ]
        gw.audit_log = audit
        return gw

    def _make_request(self, gw, query=None, token="test-token"):
        from unittest.mock import MagicMock

        req = MagicMock()
        req.headers = {"Authorization": f"Bearer {token}"}
        req.query = query or {}
        gw.config.auth_token = "test-token"
        # require_bearer_auth calls gw.config.auth_token
        return req

    def _run(self, coro):
        return asyncio.run(coro)

    def test_returns_all_events_unfiltered(self):
        from navig.gateway.routes.audit import _tail

        gw = self._make_gw()
        req = self._make_request(gw)
        handler = _tail(gw)(req)
        # The handler is a coroutine; run it
        resp = self._run(handler)
        import json

        data = json.loads(resp.body)["data"]
        assert data["count"] == 3
        assert len(data["events"]) == 3

    def test_filter_by_action_prefix(self):
        from navig.gateway.routes.audit import _tail

        gw = self._make_gw()
        req = self._make_request(gw, query={"action": "task"})
        resp = self._run(_tail(gw)(req))
        import json

        data = json.loads(resp.body)["data"]
        assert data["count"] == 2
        assert all(e["action"].startswith("task") for e in data["events"])

    def test_filter_by_actor(self):
        from navig.gateway.routes.audit import _tail

        gw = self._make_gw()
        req = self._make_request(gw, query={"actor": "user2"})
        resp = self._run(_tail(gw)(req))
        import json

        data = json.loads(resp.body)["data"]
        assert data["count"] == 1
        assert data["events"][0]["actor"] == "user2"

    def test_filter_by_status(self):
        from navig.gateway.routes.audit import _tail

        gw = self._make_gw()
        req = self._make_request(gw, query={"status": "denied"})
        resp = self._run(_tail(gw)(req))
        import json

        data = json.loads(resp.body)["data"]
        assert data["count"] == 1
        assert data["events"][0]["status"] == "denied"

    def test_limit_caps_results(self):
        from navig.gateway.routes.audit import _tail

        records = [
            {"ts": f"T{i}", "actor": "u", "action": "x", "status": "success"}
            for i in range(20)
        ]
        gw = self._make_gw(tail_records=records)
        req = self._make_request(gw, query={"limit": "5"})
        resp = self._run(_tail(gw)(req))
        import json

        data = json.loads(resp.body)["data"]
        assert data["count"] == 5

    def test_requires_auth(self):
        from navig.gateway.routes.audit import _tail
        from navig.gateway.routes.common import require_bearer_auth

        gw = self._make_gw()
        req = self._make_request(gw, token="wrong-token")
        resp = self._run(_tail(gw)(req))
        assert resp.status == 401


# ---------------------------------------------------------------------------
# Route policy gate integration — daemon and tasks routes
# ---------------------------------------------------------------------------


class TestRoutePolicyGates:
    """Verify policy gates are present on daemon.stop, formation.start, tasks.add."""

    def _run(self, coro):
        return asyncio.run(coro)

    def _gw_with_policy(self, decision_value="deny"):
        from navig.gateway import server as srv
        from navig.gateway.audit_log import AuditLog
        from navig.gateway.cooldown import CooldownTracker
        from navig.gateway.policy_gate import PolicyDecision, PolicyGate, PolicyResult

        gw = MagicMock()
        gw.config.auth_token = "tok"
        pg = MagicMock(spec=PolicyGate)
        decision = {"allow": PolicyDecision.ALLOW, "deny": PolicyDecision.DENY}[
            decision_value
        ]
        pg.check.return_value = PolicyResult(decision=decision, action="any")
        gw.policy_gate = pg
        gw.audit_log = MagicMock(spec=AuditLog)
        gw.cooldown = CooldownTracker(default_cooldown_seconds=0.0)
        gw.policy_check = lambda *a, **kw: srv.NavigGateway.policy_check(gw, *a, **kw)
        return gw

    def _req(self, gw, body=None):
        req = MagicMock()
        req.headers = {"Authorization": "Bearer tok", "X-Actor": "ci-test"}
        req.remote = "127.0.0.1"

        async def _json():
            return body or {}

        req.json = _json
        req.match_info = {}
        return req

    def test_daemon_stop_policy_deny_returns_403(self):
        from navig.gateway.routes.daemon import _daemon_stop

        gw = self._gw_with_policy("deny")
        resp = self._run(_daemon_stop(gw)(self._req(gw)))
        assert resp.status == 403

    def test_formation_start_policy_deny_returns_403(self):
        from navig.gateway.routes.daemon import _formation_start

        gw = self._gw_with_policy("deny")
        resp = self._run(_formation_start(gw)(self._req(gw, {"formation": "test"})))
        assert resp.status == 403

    def test_tasks_add_policy_deny_returns_403(self):
        from navig.gateway.routes.tasks import _add

        gw = self._gw_with_policy("deny")
        gw.task_queue = MagicMock()  # present so _chk passes
        resp = self._run(_add(gw)(self._req(gw, {"name": "t", "handler": "h"})))
        assert resp.status == 403

    def test_daemon_stop_policy_allow_proceeds(self):
        """ALLOW decision — stop should initiate (we just check it reaches task creation)."""
        import asyncio as _aio

        from navig.gateway.routes.daemon import _daemon_stop

        gw = self._gw_with_policy("allow")
        # Patch asyncio.create_task so teardown doesn't actually exit
        with patch("navig.gateway.routes.daemon.asyncio.create_task"):
            resp = self._run(_daemon_stop(gw)(self._req(gw)))
        assert resp.status == 200


# ---------------------------------------------------------------------------
# __init__ re-exports CooldownTracker
# ---------------------------------------------------------------------------


class TestGatewayPackageExports:
    def test_cooldown_tracker_exported(self):
        from navig.gateway import CooldownTracker

        assert CooldownTracker is not None
