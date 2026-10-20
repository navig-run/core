"""Deck actions that need approval answer 202 pending instead of freezing the screen.

Regression target: ``POST /api/deck/remote/run`` (and docker mutations, host select,
deploy) held the HTTP request open inside ``policy_check`` for the approval policy's
whole timeout (900 s). The OS client aborts at 15 s, so the operator saw an error —
and when they approved later, the command still ran with nobody watching its output.

Now the route answers ``202 {"status": "pending_approval", "request_id": …}`` the
moment the approval request exists; ``GET /api/deck/approvals/{id}`` reports
``pending_approval`` → ``done`` (with the output) / ``denied`` / ``expired`` /
``failed``. Every immediate decision keeps its old response (covered unchanged by
test_remote_run_gate.py).

REAL ApprovalManager, PolicyGate, AuditLog and host lock; only SSH is faked.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.integration

DANGEROUS_CMD = "sudo rm -rf /tmp/navig-deferred-cache"
_REQUIRE_DANGEROUS = [{"pattern": "remote.exec.dangerous", "action": "require_approval"}]
_REQUIRE_DOCKER_MUTATE = [{"pattern": "remote.docker.mutate", "action": "require_approval"}]
_RUN = "/api/deck/remote/run"
_DOCKER = "/api/deck/remote/docker"


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("NAVIG_HOME", raising=False)
    monkeypatch.setenv("NAVIG_HOST_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.setenv("NAVIG_SESSION_ID", "session-deferred")
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv("NAVIG_HOST_LOCK", raising=False)
    from navig.gateway.deck.routes import _enforce

    _enforce._APPROVAL_JOBS.clear()
    yield tmp_path
    _enforce._APPROVAL_JOBS.clear()


@pytest.fixture(autouse=True)
def fake_cm(monkeypatch):
    import navig.config as config_mod

    class _Cfg:
        global_config: dict = {}

        def is_local_host(self, host: str) -> bool:
            return False

        def update_global_config(self, gc: dict) -> None:
            pass

    monkeypatch.setattr(config_mod, "get_config_manager", lambda: _Cfg())


@pytest.fixture()
def calls(monkeypatch):
    recorded: list[tuple[str, str]] = []

    async def fake_run(host, command, timeout=30.0):
        recorded.append((host, command))
        return True, f"ran: {command}", ""

    from navig.gateway.deck.routes import remote

    monkeypatch.setattr(remote, "run_on_host", fake_run)
    return recorded


def _manager(timeout_seconds=5):
    from navig.approval import ApprovalManager, ApprovalPolicy

    return ApprovalManager(policy=ApprovalPolicy(timeout_seconds=timeout_seconds, default_action="deny"))


def _gateway(tmp_path, *, rules, manager):
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
    gw.approval_manager = manager
    gw.policy_check = lambda *a, **kw: srv.NavigGateway.policy_check(gw, *a, **kw)
    return gw


def _app(gateway):
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.deck.routes import remote
    from navig.gateway.deck.routes._enforce import handle_deck_approval_status

    @web.middleware
    async def _who(request, handler):
        # Stand-in for the deck auth middleware: the server-derived identity.
        user = request.headers.get("X-Test-User")
        if user:
            request["deck_user_id"] = user
        return await handler(request)

    app = web.Application(middlewares=[_who])
    app["gateway"] = gateway
    app.router.add_post(_RUN, remote.handle_deck_remote_run)
    app.router.add_post(_DOCKER, remote.handle_deck_remote_docker)
    app.router.add_get("/api/deck/approvals/{request_id}", handle_deck_approval_status)
    return app


async def _status_until(client, request_id, want, *, user="alice", tries=300):
    for _ in range(tries):
        r = await client.get(f"/api/deck/approvals/{request_id}", headers={"X-Test-User": user})
        body = await r.json()
        if r.status == 200 and body["data"]["status"] in want:
            return body["data"]
        await asyncio.sleep(0.02)
    raise AssertionError(f"approval job never reached {want}: last={body}")


def _audit(tmp_path, action):
    path = tmp_path / "audit.jsonl"
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x] if path.exists() else []
    return [r["status"] for r in rows if r["action"] == action]


async def test_dangerous_run_answers_pending_at_once_and_runs_once_when_approved(isolated, calls):
    from aiohttp.test_utils import TestClient, TestServer

    mgr = _manager()
    async with TestClient(TestServer(_app(_gateway(isolated, rules=_REQUIRE_DANGEROUS, manager=mgr)))) as client:
        r = await asyncio.wait_for(
            client.post(_RUN, json={"host": "vps-1", "command": DANGEROUS_CMD}, headers={"X-Test-User": "alice"}),
            timeout=3,  # the whole point: it must not hold the request for the approval wait
        )
        assert r.status == 202
        data = (await r.json())["data"]
        assert data["status"] == "pending_approval"
        rid = data["request_id"]
        assert calls == [], "nothing may run before a human approves"

        # The approver sees the real command, not only the policy slug.
        pending = mgr.list_pending()
        assert len(pending) == 1 and pending[0].id == rid
        assert DANGEROUS_CMD in pending[0].description and "vps-1" in pending[0].description

        assert await mgr.respond(rid, True) is True
        done = await _status_until(client, rid, {"done"})
        assert done["result"]["stdout"] == f"ran: {DANGEROUS_CMD}"
        assert calls == [("vps-1", DANGEROUS_CMD)]

    assert _audit(isolated, "remote.exec.dangerous") == ["pending_approval", "approved"]


async def test_denied_never_runs(isolated, calls):
    from aiohttp.test_utils import TestClient, TestServer

    mgr = _manager()
    async with TestClient(TestServer(_app(_gateway(isolated, rules=_REQUIRE_DANGEROUS, manager=mgr)))) as client:
        r = await client.post(_RUN, json={"host": "vps-1", "command": DANGEROUS_CMD}, headers={"X-Test-User": "alice"})
        rid = (await r.json())["data"]["request_id"]
        assert await mgr.respond(rid, False) is True
        job = await _status_until(client, rid, {"denied", "expired", "done", "failed"})
        assert job["status"] == "denied"
    assert calls == []
    assert _audit(isolated, "remote.exec.dangerous") == ["pending_approval", "denied"]


async def test_unanswered_request_expires_and_never_runs(isolated, calls):
    from aiohttp.test_utils import TestClient, TestServer

    mgr = _manager(timeout_seconds=1)
    async with TestClient(TestServer(_app(_gateway(isolated, rules=_REQUIRE_DANGEROUS, manager=mgr)))) as client:
        r = await client.post(_RUN, json={"host": "vps-1", "command": DANGEROUS_CMD}, headers={"X-Test-User": "alice"})
        assert r.status == 202
        rid = (await r.json())["data"]["request_id"]
        job = await _status_until(client, rid, {"denied", "expired", "done", "failed"}, tries=400)
        assert job["status"] == "expired"
    assert calls == []


async def test_a_lock_taken_during_the_wait_stops_the_approved_run(isolated, calls, monkeypatch):
    """The host lock is checked when the action RUNS, not when it was asked for."""
    from aiohttp.test_utils import TestClient, TestServer

    from navig.core import host_lock

    mgr = _manager()
    async with TestClient(TestServer(_app(_gateway(isolated, rules=_REQUIRE_DANGEROUS, manager=mgr)))) as client:
        r = await client.post(_RUN, json={"host": "vps-1", "command": DANGEROUS_CMD}, headers={"X-Test-User": "alice"})
        rid = (await r.json())["data"]["request_id"]

        monkeypatch.setenv("NAVIG_SESSION_ID", "session-other")
        host_lock.claim("vps-1", "someone else's maintenance")
        monkeypatch.setenv("NAVIG_SESSION_ID", "session-deferred")

        assert await mgr.respond(rid, True) is True
        job = await _status_until(client, rid, {"done", "failed", "denied", "expired"})
        assert job["status"] == "failed" and job["error_code"] == "host_locked"
    assert calls == []


async def test_only_the_asker_can_read_the_outcome(isolated, calls):
    from aiohttp.test_utils import TestClient, TestServer

    mgr = _manager()
    async with TestClient(TestServer(_app(_gateway(isolated, rules=_REQUIRE_DANGEROUS, manager=mgr)))) as client:
        r = await client.post(_RUN, json={"host": "vps-1", "command": DANGEROUS_CMD}, headers={"X-Test-User": "alice"})
        rid = (await r.json())["data"]["request_id"]
        other = await client.get(f"/api/deck/approvals/{rid}", headers={"X-Test-User": "mallory"})
        assert other.status == 404
        unknown = await client.get("/api/deck/approvals/nope", headers={"X-Test-User": "alice"})
        assert unknown.status == 404
        await mgr.respond(rid, False)


async def test_docker_restart_is_deferred_too(isolated, calls):
    from aiohttp.test_utils import TestClient, TestServer

    mgr = _manager()
    async with TestClient(TestServer(_app(_gateway(isolated, rules=_REQUIRE_DOCKER_MUTATE, manager=mgr)))) as client:
        r = await client.post(
            _DOCKER, json={"host": "vps-1", "action": "restart", "container": "web"},
            headers={"X-Test-User": "alice"},
        )
        assert r.status == 202
        rid = (await r.json())["data"]["request_id"]
        assert calls == []
        await mgr.respond(rid, True)
        job = await _status_until(client, rid, {"done"})
        assert job["result"]["action"] == "restart"
    assert len(calls) == 1 and "docker restart" in calls[0][1]


async def test_allowed_command_still_answers_200_synchronously(isolated, calls):
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(_app(_gateway(isolated, rules=[], manager=_manager())))) as client:
        r = await client.post(_RUN, json={"host": "vps-1", "command": "uptime"}, headers={"X-Test-User": "alice"})
        assert r.status == 200
        assert (await r.json())["data"]["stdout"] == "ran: uptime"
