"""Gate 0C: every deck remote-execution route runs through ``authorize_deck``.

Regression target: ``POST /api/deck/remote/{run,docker,hosts/use,deploy}``, the
read/probe routes and ``POST /api/deck/db/query`` executed over SSH (or mutated
local config, or spawned a deploy) with no policy check, no approval, no host
lock and no audit record — while the identical CLI verbs were gated, and while
the actor was client-settable via ``X-Actor``.

The harness mirrors ``test_policy_approval_gate._build_gateway``: a MagicMock
gateway whose ``policy_check`` is the REAL ``NavigGateway.policy_check`` over a
real PolicyGate + AuditLog + CooldownTracker + BillingEmitter. SSH is faked at
the route's runner; no packet ever leaves the machine.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.integration

# ── commands under test (from ApprovalPolicy's default classification) ──────

#: matches DEFAULT_DANGEROUS_PATTERNS ``* rm -rf *`` and no NEVER pattern.
DANGEROUS_CMD = "sudo rm -rf /tmp/navig-g0c-cache"
#: matches DEFAULT_NEVER_PATTERNS ``run rm -rf /`` exactly.
NEVER_CMD = "run rm -rf /"
#: unlisted → CONFIRM → the base slug.
BENIGN_CMD = "uptime"

# ── policy rule sets (the gateway.policy a deployment gates these slugs with) ─

_REQUIRE_DANGEROUS = [{"pattern": "remote.exec.dangerous", "action": "require_approval"}]
_DENY_DANGEROUS = [{"pattern": "remote.exec.dangerous", "action": "deny"}]
_REQUIRE_DOCKER_MUTATE = [{"pattern": "remote.docker.mutate", "action": "require_approval"}]
_REQUIRE_HOST_SELECT = [{"pattern": "remote.host.select", "action": "require_approval"}]
_REQUIRE_DEPLOY = [{"pattern": "remote.deploy", "action": "require_approval"}]
#: PolicyGate's built-in hard-deny list does not (yet) carry ``*.never``, so the
#: deployment's gateway.policy must deny the ``.never`` slugs the mapping emits.
#: A DENY decision beats an approving manager because policy_check consults
#: policy BEFORE approval — that ordering is what this rule pins from the route side.
_DENY_NEVER = [{"pattern": "*.never", "action": "deny"}]
_DENY_DB_QUERY = [{"pattern": "db.query", "action": "deny"}]


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """Isolated config dir, host-lock storage and session identity."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("NAVIG_HOME", raising=False)
    monkeypatch.setenv("NAVIG_HOST_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.setenv("NAVIG_SESSION_ID", "session-g0c")
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv("NAVIG_HOST_LOCK", raising=False)  # default mode: block
    return tmp_path


@pytest.fixture(autouse=True)
def fake_cm(monkeypatch):
    """A ConfigManager stand-in: no host is local, and config writes are recorded.

    ``authorize_deck`` resolves the host lock through
    ``navig.config.get_config_manager``; tests must never reach the operator's
    real config. The fake also lets a gated route's config mutation be asserted
    as never-happened (``saved`` stays empty).
    """
    import navig.config as config_mod

    class _Cfg:
        def __init__(self):
            self.global_config: dict = {}
            self.saved: list[dict] = []

        def is_local_host(self, host: str) -> bool:
            return False

        def update_global_config(self, gc: dict) -> None:
            self.saved.append(gc)

    cm = _Cfg()
    monkeypatch.setattr(config_mod, "get_config_manager", lambda: cm)
    return cm


@pytest.fixture()
def calls(monkeypatch):
    """Record every SSH attempt the remote routes make; nothing real runs."""
    recorded: list[tuple[str, str]] = []

    async def fake_run(host, command, timeout=30.0):
        recorded.append((host, command))
        return True, "ok", ""

    from navig.gateway.deck.routes import remote

    monkeypatch.setattr(remote, "run_on_host", fake_run)
    return recorded


@pytest.fixture()
def db_calls(monkeypatch):
    """Record every SSH attempt db/query makes; nothing real runs."""
    recorded: list[str] = []

    async def fake_run(host, command, timeout=45.0):
        recorded.append(command)
        return True, "1 row affected", ""

    from navig.gateway.deck.routes import database

    monkeypatch.setattr(database, "_run_remote", fake_run)
    return recorded


# ── harness (mirrors test_policy_approval_gate) ─────────────────────────────


def _build_gateway(tmp_path, *, rules, approval_manager):
    """A gateway stub whose ``policy_check`` / PolicyGate / AuditLog are REAL."""
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


class _Manager:
    """ApprovalManager stand-in: real classification policy, scripted answer."""

    def __init__(self, approve: bool):
        from navig.approval import ApprovalPolicy

        self.policy = ApprovalPolicy()
        self.approve = approve
        self.requests: list[str] = []

    async def request_approval(self, command=None, **kwargs):
        self.requests.append(command)
        return self.approve


def _app(gateway, *, db_query: bool = False):
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.deck.routes import remote as remote_routes

    app = web.Application()
    app["gateway"] = gateway
    app.router.add_post("/api/deck/remote/run", remote_routes.handle_deck_remote_run)
    app.router.add_post("/api/deck/remote/docker", remote_routes.handle_deck_remote_docker)
    app.router.add_post(
        "/api/deck/remote/hosts/use", remote_routes.handle_deck_remote_host_use
    )
    app.router.add_post("/api/deck/remote/deploy", remote_routes.handle_deck_remote_deploy)
    if db_query:
        from navig.gateway.deck.routes.database import handle_deck_db_query

        app.router.add_post("/api/deck/db/query", handle_deck_db_query)
    return app


async def _posts(app, requests):
    """POST each ``(path, body[, headers])`` in ONE client session."""
    from aiohttp.test_utils import TestClient, TestServer

    out = []
    async with TestClient(TestServer(app)) as client:
        for item in requests:
            path, body = item[0], item[1]
            headers = item[2] if len(item) > 2 else None
            r = await client.post(path, json=body, headers=headers or {})
            out.append((r.status, await r.json()))
    return out


async def _post(app, path, body, headers=None):
    return (await _posts(app, [(path, body, headers)]))[0]


def _audit_records(tmp_path):
    p = tmp_path / "audit.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line]


_RUN = "/api/deck/remote/run"
_DOCKER = "/api/deck/remote/docker"
_HOSTS_USE = "/api/deck/remote/hosts/use"
_DEPLOY = "/api/deck/remote/deploy"
_DB_QUERY = "/api/deck/db/query"


# ── 1/3/4: remote/run — classification → policy → approval ─────────────────


async def test_dangerous_command_without_manager_is_403_and_never_runs(isolated, calls):
    gw = _build_gateway(isolated, rules=_REQUIRE_DANGEROUS, approval_manager=None)
    status, body = await _post(
        _app(gw), _RUN, {"host": "vps-1", "command": DANGEROUS_CMD}
    )
    assert status == 403
    assert body["error_code"] == "approval_unavailable"
    assert calls == [], "the runner must never start when the gate blocks"


async def test_dangerous_command_approved_by_manager_runs_exactly_once_and_is_audited(
    isolated, calls
):
    mgr = _Manager(approve=True)
    gw = _build_gateway(isolated, rules=_REQUIRE_DANGEROUS, approval_manager=mgr)
    status, body = await _post(
        _app(gw), _RUN, {"host": "vps-1", "command": DANGEROUS_CMD}
    )
    assert status == 200
    assert body["ok"] is True
    assert len(calls) == 1 and calls[0][1] == DANGEROUS_CMD
    assert len(mgr.requests) == 1

    recs = [r for r in _audit_records(isolated) if r["action"] == "remote.exec.dangerous"]
    assert [r["status"] for r in recs] == ["pending_approval", "approved"]
    assert all(r["policy"] == "require_approval" for r in recs)


async def test_dangerous_command_denied_by_manager_is_403_and_never_runs(isolated, calls):
    mgr = _Manager(approve=False)
    gw = _build_gateway(isolated, rules=_REQUIRE_DANGEROUS, approval_manager=mgr)
    status, body = await _post(
        _app(gw), _RUN, {"host": "vps-1", "command": DANGEROUS_CMD}
    )
    assert status == 403
    assert body["error_code"] == "approval_denied"
    assert calls == []

    recs = [r for r in _audit_records(isolated) if r["action"] == "remote.exec.dangerous"]
    assert [r["status"] for r in recs] == ["pending_approval", "denied"]


# ── 2: DENY rule → 403 + a real audit line ─────────────────────────────────


async def test_policy_deny_rule_blocks_and_writes_a_denied_audit_line(isolated, calls):
    mgr = _Manager(approve=True)  # deny must beat even an approving manager
    gw = _build_gateway(isolated, rules=_DENY_DANGEROUS, approval_manager=mgr)
    status, body = await _post(
        _app(gw), _RUN, {"host": "vps-1", "command": DANGEROUS_CMD}
    )
    assert status == 403
    assert body["error_code"] == "policy_denied"
    assert calls == []
    assert mgr.requests == [], "policy DENY is decided before approval is asked"

    recs = [r for r in _audit_records(isolated) if r["action"] == "remote.exec.dangerous"]
    assert len(recs) == 1
    assert recs[0]["status"] == "denied"
    assert recs[0]["policy"] == "deny"
    assert recs[0]["actor"].startswith("deck:")
    assert recs[0]["metadata"]["matched_rule"] == "remote.exec.dangerous"


# ── 5: hostile X-Actor never reaches the audit ─────────────────────────────


async def test_hostile_x_actor_header_never_reaches_the_audit(isolated, calls):
    gw = _build_gateway(isolated, rules=[], approval_manager=None)
    status, _ = await _post(
        _app(gw),
        _RUN,
        {"host": "vps-1", "command": BENIGN_CMD},
        headers={"X-Actor": "mallory"},
    )
    assert status == 200

    raw = (isolated / "audit.jsonl").read_text(encoding="utf-8")
    assert "mallory" not in raw, "the client-settable header must never be the actor"
    recs = _audit_records(isolated)
    assert recs, "the allowed action must still be audited"
    for r in recs:
        assert r["actor"].startswith(("deck:", "brain-device:")), r["actor"]


# ── 6: a foreign host lock → 409 before execution ──────────────────────────


async def test_foreign_host_lock_is_409_and_the_runner_never_starts(
    isolated, calls, monkeypatch
):
    from navig.core import host_lock

    gw = _build_gateway(isolated, rules=[], approval_manager=None)
    app = _app(gw)

    # Another live session already holds the host (same pattern as test_host_lock).
    monkeypatch.setenv("NAVIG_SESSION_ID", "session-A")
    host_lock.claim("vps-locked", "other session work")
    monkeypatch.setenv("NAVIG_SESSION_ID", "session-B")

    status, body = await _post(app, _RUN, {"host": "vps-locked", "command": BENIGN_CMD})
    assert status == 409
    assert body["error_code"] == "host_locked"
    assert calls == []
    # The loser claimed nothing.
    assert host_lock.lock_state(host_lock.read_lock("vps-locked")).session == "session-A"


# ── 7: docker — reads allow, mutations gate ────────────────────────────────


async def test_docker_ps_is_allowed_and_restart_is_gated(isolated, calls):
    gw = _build_gateway(isolated, rules=_REQUIRE_DOCKER_MUTATE, approval_manager=None)
    app = _app(gw)

    (ps_status, _), (restart_status, restart_body) = await _posts(
        app,
        [
            (_DOCKER, {"host": "vps-1", "action": "ps"}),
            (_DOCKER, {"host": "vps-1", "action": "restart", "container": "web"}),
        ],
    )
    assert ps_status == 200
    assert len(calls) == 1 and "docker ps" in calls[0][1]

    assert restart_status == 403
    assert restart_body["error_code"] == "approval_unavailable"
    assert len(calls) == 1, "the mutation must not reach the runner"

    recs = _audit_records(isolated)
    reads = [r for r in recs if r["action"] == "remote.docker.read"]
    assert reads and reads[0]["status"] == "success"
    mutates = [r for r in recs if r["action"] == "remote.docker.mutate"]
    assert [r["status"] for r in mutates] == ["pending_approval", "denied"]


# ── 8: hosts/use — gated local config mutation ─────────────────────────────


async def test_host_select_is_gated_without_a_manager(isolated, fake_cm):
    gw = _build_gateway(isolated, rules=_REQUIRE_HOST_SELECT, approval_manager=None)
    status, body = await _post(_app(gw), _HOSTS_USE, {"host": "vps-2"})
    assert status == 403
    assert body["error_code"] == "approval_unavailable"
    assert fake_cm.saved == [], "the active host must not switch when gated"


# ── 9: deploy — 403 before any state or spawn ──────────────────────────────


async def test_deploy_is_403_before_any_deploy_state_exists(isolated):
    from navig.gateway.deck.routes import remote as remote_routes

    remote_routes._DEPLOY_STATE.clear()
    gw = _build_gateway(isolated, rules=_REQUIRE_DEPLOY, approval_manager=None)
    status, body = await _post(_app(gw), _DEPLOY, {"host": "vps-3"})
    assert status == 403
    assert body["error_code"] == "approval_unavailable"
    assert remote_routes._DEPLOY_STATE == {}, "no deploy state, so nothing was spawned"


# ── 10: a NEVER command is denied even with an approving manager ───────────


async def test_never_command_is_403_even_with_an_approving_manager(isolated, calls):
    mgr = _Manager(approve=True)
    gw = _build_gateway(isolated, rules=_DENY_NEVER, approval_manager=mgr)
    status, body = await _post(_app(gw), _RUN, {"host": "vps-1", "command": NEVER_CMD})
    assert status == 403
    assert body["error_code"] == "policy_denied"
    assert calls == []
    assert mgr.requests == [], "hard classification must beat approval entirely"

    recs = [r for r in _audit_records(isolated) if r["action"] == "remote.exec.never"]
    assert len(recs) == 1
    assert recs[0]["status"] == "denied"
    assert recs[0]["policy"] == "deny"


# ── 11: db.query — confirm FIRST, policy only after confirm ────────────────


async def test_db_query_confirms_before_policy_then_denies_a_confirmed_write(
    isolated, db_calls
):
    gw = _build_gateway(isolated, rules=_DENY_DB_QUERY, approval_manager=_Manager(True))
    app = _app(gw, db_query=True)

    # Phase 1: unconfirmed write — 409 BEFORE policy is consulted (no audit line).
    (status, body), = await _posts(
        app, [(_DB_QUERY, {"host": "db-1", "query": "DELETE FROM orders"})]
    )
    assert status == 409
    assert body["requires_confirmation"] is True
    assert db_calls == []
    assert not (isolated / "audit.jsonl").exists(), (
        "policy must not be consulted (nor audited) before the confirm gate"
    )

    # Phase 2: confirmed write — policy DENY blocks before any SSH.
    (status, body), = await _posts(
        app,
        [(_DB_QUERY, {"host": "db-1", "query": "DELETE FROM orders", "confirm": True})],
    )
    assert status == 403
    assert body["error_code"] == "policy_denied"
    assert db_calls == []

    recs = [r for r in _audit_records(isolated) if r["action"] == "db.query"]
    assert len(recs) == 1
    assert recs[0]["status"] == "denied"
    assert recs[0]["policy"] == "deny"
    assert recs[0]["actor"].startswith("deck:")
