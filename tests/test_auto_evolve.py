"""
Tests for Auto-Evolve / Auto-Approve integration.

Coverage:
  - ApprovalPolicy.is_auto_evolve_allowed() — whitelist, safety levels, audit gate
  - ApprovalPolicy.from_config() — new auto_evolve section
  - ApprovalManager.set_auto_evolve() — toggle + audit-log guard
  - ApprovalManager.request_approval() — auto-evolve short-circuit
  - GET/POST /approval/auto-evolve HTTP routes
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── path setup ──────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

from navig.approval.manager import ApprovalManager
from navig.approval.policies import DEFAULT_AUTO_EVOLVE_WHITELIST, ApprovalPolicy
from navig.gateway.audit_log import AuditLog

# ════════════════════════════════════════════════════════════════════════════
# ApprovalPolicy — is_auto_evolve_allowed()
# ════════════════════════════════════════════════════════════════════════════


class TestAutoEvolvePolicy:

    def _policy(self, enabled: bool = True) -> ApprovalPolicy:
        p = ApprovalPolicy()
        p.auto_evolve_enabled = enabled
        return p

    def test_disabled_returns_false(self):
        policy = self._policy(enabled=False)
        assert policy.is_auto_evolve_allowed("fix", audit_log_live=True) is False

    def test_no_audit_log_returns_false(self):
        policy = self._policy(enabled=True)
        assert policy.is_auto_evolve_allowed("fix", audit_log_live=False) is False

    def test_whitelisted_safe_allowed(self):
        policy = self._policy(enabled=True)
        for cmd in ["fix", "skill.patch", "workflow.update", "run", "file.write"]:
            assert policy.is_auto_evolve_allowed(cmd, audit_log_live=True) is True, cmd

    def test_non_whitelisted_command_denied(self):
        policy = self._policy(enabled=True)
        assert (
            policy.is_auto_evolve_allowed("db drop production", audit_log_live=True)
            is False
        )

    def test_dangerous_command_never_auto_approved(self):
        """run rm -rf * is DANGEROUS — must not be auto-approved regardless of whitelist."""
        policy = self._policy(enabled=True)
        # Force it onto whitelist to prove the level check blocks it
        policy.auto_evolve_whitelist = ["run rm *"]
        assert (
            policy.is_auto_evolve_allowed("run rm -rf *", audit_log_live=True) is False
        )

    def test_never_command_never_auto_approved(self):
        policy = self._policy(enabled=True)
        policy.auto_evolve_whitelist = ["run rm -rf /"]
        assert (
            policy.is_auto_evolve_allowed("run rm -rf /", audit_log_live=True) is False
        )

    def test_custom_whitelist(self):
        policy = self._policy(enabled=True)
        policy.auto_evolve_whitelist = ["custom.*"]
        assert (
            policy.is_auto_evolve_allowed("custom.deploy", audit_log_live=True) is True
        )
        assert (
            policy.is_auto_evolve_allowed("fix", audit_log_live=True) is False
        )  # not in custom list

    def test_case_insensitive_match(self):
        policy = self._policy(enabled=True)
        assert policy.is_auto_evolve_allowed("FIX", audit_log_live=True) is True
        assert policy.is_auto_evolve_allowed("Run", audit_log_live=True) is True


# ════════════════════════════════════════════════════════════════════════════
# ApprovalPolicy — from_config() auto_evolve section
# ════════════════════════════════════════════════════════════════════════════


class TestAutoEvolvePolicyFromConfig:

    def test_defaults_when_no_auto_evolve_key(self):
        policy = ApprovalPolicy.from_config({})
        assert policy.auto_evolve_enabled is False
        assert policy.auto_evolve_whitelist == DEFAULT_AUTO_EVOLVE_WHITELIST

    def test_enabled_from_config(self):
        cfg = {"approval": {"auto_evolve": {"enabled": True}}}
        policy = ApprovalPolicy.from_config(cfg)
        assert policy.auto_evolve_enabled is True

    def test_custom_whitelist_from_config(self):
        cfg = {"approval": {"auto_evolve": {"whitelist": ["my.action"]}}}
        policy = ApprovalPolicy.from_config(cfg)
        assert policy.auto_evolve_whitelist == ["my.action"]


# ════════════════════════════════════════════════════════════════════════════
# ApprovalManager — set_auto_evolve() + guard
# ════════════════════════════════════════════════════════════════════════════


class TestApprovalManagerSetAutoEvolve:

    def _manager_with_live_log(self) -> tuple[ApprovalManager, Path]:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "audit.jsonl"
            audit = AuditLog(path=log_path)
            mgr = ApprovalManager(audit_log=audit)
            return mgr, log_path

    def test_enable_without_audit_raises(self):
        mgr = ApprovalManager()  # no audit log wired
        with pytest.raises(RuntimeError, match="audit log is not live"):
            mgr.set_auto_evolve(True)

    def test_disable_without_audit_succeeds(self):
        mgr = ApprovalManager()
        # Disable should always succeed (no guard needed for turning off)
        mgr.set_auto_evolve(False)
        assert mgr.policy.auto_evolve_enabled is False

    def test_enable_with_live_log_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "audit.jsonl"
            audit = AuditLog(path=log_path)
            mgr = ApprovalManager(audit_log=audit)
            mgr.set_auto_evolve(True)
            assert mgr.policy.auto_evolve_enabled is True

    def test_toggle_writes_audit_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "audit.jsonl"
            audit = AuditLog(path=log_path)
            mgr = ApprovalManager(audit_log=audit)
            mgr.set_auto_evolve(True)
            records = audit.tail(5)
            assert any(r["action"] == "approval.auto_evolve.toggle" for r in records)

    def test_set_audit_log_method(self):
        mgr = ApprovalManager()
        with tempfile.TemporaryDirectory() as tmp:
            audit = AuditLog(path=Path(tmp) / "audit.jsonl")
            mgr.set_audit_log(audit)
            assert mgr.is_audit_log_live() is True

    def test_is_audit_log_live_false_without_wiring(self):
        mgr = ApprovalManager()
        assert mgr.is_audit_log_live() is False


# ════════════════════════════════════════════════════════════════════════════
# ApprovalManager — request_approval() auto-evolve short-circuit
# ════════════════════════════════════════════════════════════════════════════


class TestApprovalManagerAutoEvolveApproval:

    def _manager_auto_evolve_on(self) -> ApprovalManager:
        with tempfile.TemporaryDirectory() as tmp:
            audit = AuditLog(path=Path(tmp) / "audit.jsonl")
            mgr = ApprovalManager(audit_log=audit)
            mgr.set_auto_evolve(True)
            return mgr

    @pytest.mark.asyncio
    async def test_whitelisted_command_approved_silently(self):
        mgr = self._manager_auto_evolve_on()
        result = await mgr.request_approval(
            command="fix", user_id="agent", channel="api"
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_non_whitelisted_goes_to_normal_flow(self):
        """Non-whitelisted CONFIRM command still creates a pending request."""
        mgr = self._manager_auto_evolve_on()
        # Patch the future to resolve after a tick so we can observe pending creation
        called = []

        async def _probe(req):
            called.append(req.command)

        mgr.on_request(_probe)
        # "host remove myserver" is CONFIRM-level but not in default whitelist
        task = asyncio.create_task(
            mgr.request_approval(command="host remove myserver", user_id="agent")
        )
        await asyncio.sleep(0.05)
        assert "host remove myserver" in called
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_auto_evolve_approval_recorded_in_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "audit.jsonl"
            audit = AuditLog(path=log_path)
            mgr = ApprovalManager(audit_log=audit)
            mgr.set_auto_evolve(True)
            await mgr.request_approval(command="skill.patch", user_id="agent")
            records = audit.tail(10)
            auto_records = [
                r for r in records if r.get("action") == "approval.auto_evolve"
            ]
            assert len(auto_records) == 1
            assert auto_records[0]["metadata"]["auto_evolve"] is True

    @pytest.mark.asyncio
    async def test_auto_evolve_off_does_not_short_circuit(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = AuditLog(path=Path(tmp) / "audit.jsonl")
            mgr = ApprovalManager(audit_log=audit)
            # auto_evolve stays OFF
            called = []
            mgr.on_request(lambda req: called.append(req))
            task = asyncio.create_task(
                mgr.request_approval(command="fix", user_id="agent")
            )
            await asyncio.sleep(0.05)
            assert len(called) == 1  # request was queued, not auto-approved
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


# ════════════════════════════════════════════════════════════════════════════
# HTTP routes: GET/POST /approval/auto-evolve
# ════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════════
# HTTP routes: GET/POST /approval/auto-evolve
# ════════════════════════════════════════════════════════════════════════════


def _build_auto_evolve_app(mgr: ApprovalManager):
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.routes.approval import register

    gw = MagicMock()
    gw.approval_manager = mgr
    # No auth token → require_bearer_auth passes through
    from types import SimpleNamespace

    gw.config = SimpleNamespace(auth_token=None)

    app = web.Application()
    register(app, gw)
    return app


class TestAutoEvolveRoutes:

    @pytest.mark.asyncio
    async def test_get_status_disabled(self):
        pytest.importorskip("aiohttp")
        from aiohttp.test_utils import TestClient, TestServer

        with tempfile.TemporaryDirectory() as tmp:
            audit = AuditLog(path=Path(tmp) / "audit.jsonl")
            mgr = ApprovalManager(audit_log=audit)
            app = _build_auto_evolve_app(mgr)
            async with TestClient(TestServer(app)) as client:
                resp = await client.get("/approval/auto-evolve")
                assert resp.status == 200
                body = await resp.json()
                data = body["data"]
                assert data["auto_evolve_enabled"] is False
                assert data["can_enable"] is True
                assert isinstance(data["whitelist"], list)

    @pytest.mark.asyncio
    async def test_post_toggle_enable(self):
        pytest.importorskip("aiohttp")
        from aiohttp.test_utils import TestClient, TestServer

        with tempfile.TemporaryDirectory() as tmp:
            audit = AuditLog(path=Path(tmp) / "audit.jsonl")
            mgr = ApprovalManager(audit_log=audit)
            app = _build_auto_evolve_app(mgr)
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    "/approval/auto-evolve", json={"enabled": True}
                )
                assert resp.status == 200
                body = await resp.json()
                data = body["data"]
                assert data["auto_evolve_enabled"] is True
                assert data["audit_log_live"] is True

    @pytest.mark.asyncio
    async def test_post_toggle_enable_without_audit_returns_409(self):
        pytest.importorskip("aiohttp")
        from aiohttp.test_utils import TestClient, TestServer

        mgr = ApprovalManager()  # no audit log
        app = _build_auto_evolve_app(mgr)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/approval/auto-evolve", json={"enabled": True})
            assert resp.status == 409
            body = await resp.json()
            assert body["error_code"] == "audit_log_required"

    @pytest.mark.asyncio
    async def test_post_toggle_disable_always_succeeds(self):
        pytest.importorskip("aiohttp")
        from aiohttp.test_utils import TestClient, TestServer

        mgr = ApprovalManager()  # no audit log — disabling should always work
        app = _build_auto_evolve_app(mgr)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/approval/auto-evolve", json={"enabled": False})
            assert resp.status == 200
            body = await resp.json()
            data = body["data"]
            assert data["auto_evolve_enabled"] is False
