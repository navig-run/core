"""The deck console ``cli/exec`` route runs through the ONE approval path.

Regression target: the route used to classify the command itself, hardcode the
identity ``session_key="deck:console" / user_id="console"``, and run its own
bespoke approval prompt — bypassing ``NavigGateway.policy_check`` (policy →
cooldown → ApprovalManager fail-closed → audit). Every spawn now sits behind
that single composite, with a server-derived ``deck_actor``.

Harness: aiohttp TestClient + SSE frames, the real ``policy_check`` binding
(the ``test_policy_approval_gate._build_gateway`` pattern), and a recorder in
place of ``asyncio.create_subprocess_exec``.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.integration


# ── subprocess recorder ──────────────────────────────────────────────────────


class _FakeStream:
    def __init__(self, lines: list[bytes]):
        self._lines = list(lines)

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        return b""


class _FakeProc:
    """Just enough of an asyncio subprocess: pipes that EOF, then exit 0."""

    def __init__(self):
        self.pid = 4242
        self.stdout = _FakeStream([b"hello from navig\n", b"second line\n"])
        self.stderr = _FakeStream([])
        self.returncode = 0

    async def wait(self) -> int:
        return 0


def _record_spawn(monkeypatch) -> list[dict]:
    """Swap asyncio.create_subprocess_exec for a recorder returning a fake proc."""
    calls: list[dict] = []

    async def _fake_exec(*args, **kwargs):
        calls.append({"args": args, "env": kwargs.get("env")})
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    return calls


# ── gateway harness (real policy_check / PolicyGate / AuditLog) ──────────────


def _build_gateway(tmp_path, *, rules, approval_manager):
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


def _make_manager(*, timeout_seconds=5):
    from navig.approval import ApprovalManager, ApprovalPolicy

    policy = ApprovalPolicy(timeout_seconds=timeout_seconds, default_action="deny")
    return ApprovalManager(policy=policy)


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


def _audit_records(tmp_path) -> list[dict]:
    path = tmp_path / "audit.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ── app + SSE client ─────────────────────────────────────────────────────────


def _app(gateway=..., *, identity_user=7):
    """A minimal deck app: the exec route + a middleware that stamps the deck identity."""
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.deck.routes import cli as cli_mod

    @web.middleware
    async def identity(request, handler):
        if identity_user is not None:
            request["deck_user_id"] = identity_user
        return await handler(request)

    app = web.Application(middlewares=[identity])
    app.router.add_post("/cli/exec", cli_mod.handle_deck_cli_exec)
    if gateway is not ...:
        app["gateway"] = gateway
    return app


async def _exec(client, command: str, **kw) -> list[dict]:
    """POST a command and parse the SSE response into its JSON frames."""
    resp = await client.post("/cli/exec", json={"command": command}, **kw)
    assert resp.status == 200
    text = await resp.text()
    frames = []
    for line in text.splitlines():
        if line.startswith("data: "):
            frames.append(json.loads(line[6:]))
    assert frames, f"no SSE frames in response: {text!r}"
    return frames


def _errs(frames) -> list[str]:
    return [f["line"] for f in frames if f.get("type") == "err"]


def _assert_exited(frames, code: int):
    assert frames[-1] == {"type": "exit", "code": code}, frames


# ── 1. never-classified command: blocked up front, never spawned ─────────────


async def test_never_command_is_blocked_before_policy_check(tmp_path, monkeypatch):
    calls = _record_spawn(monkeypatch)
    gw = _build_gateway(tmp_path, rules=[], approval_manager=None)

    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(_app(gw))) as client:
        frames = await _exec(client, "navig run rm -rf /")

    assert any("never-allow" in line for line in _errs(frames)), frames
    _assert_exited(frames, -2)
    assert calls == [], "a never-classified command must never spawn"


# ── 2. dangerous + policy DENY rule: denied, never spawned ───────────────────


async def test_dangerous_command_denied_by_policy_rule(tmp_path, monkeypatch):
    calls = _record_spawn(monkeypatch)
    gw = _build_gateway(
        tmp_path,
        rules=[{"pattern": "cli.exec.dangerous", "action": "deny"}],
        approval_manager=None,
    )

    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(_app(gw))) as client:
        frames = await _exec(client, "navig db drop production")

    assert any("denied" in line for line in _errs(frames)), frames
    _assert_exited(frames, -2)
    assert calls == []
    records = _audit_records(tmp_path)
    assert [r["status"] for r in records] == ["denied"]
    assert records[0]["action"] == "cli.exec.dangerous"


# ── 3. dangerous + REQUIRE_APPROVAL, no manager: fail CLOSED ─────────────────


async def test_requires_approval_without_manager_fails_closed(tmp_path, monkeypatch):
    calls = _record_spawn(monkeypatch)
    gw = _build_gateway(
        tmp_path,
        rules=[{"pattern": "cli.exec.dangerous", "action": "require_approval"}],
        approval_manager=None,
    )

    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(_app(gw))) as client:
        frames = await _exec(client, "navig db drop production")

    assert any("requires approval" in line for line in _errs(frames)), frames
    _assert_exited(frames, -2)
    assert calls == []
    records = _audit_records(tmp_path)
    assert [r["status"] for r in records] == ["pending_approval", "denied"]
    assert records[-1]["metadata"]["reason"] == "approval_unavailable"


# ── 4. dangerous + manager approves: spawn once, non-interactive child ───────


async def test_approved_dangerous_command_spawns_once(tmp_path, monkeypatch):
    calls = _record_spawn(monkeypatch)
    mgr = _make_manager()
    gw = _build_gateway(
        tmp_path,
        rules=[{"pattern": "cli.exec.dangerous", "action": "require_approval"}],
        approval_manager=mgr,
    )

    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(_app(gw))) as client:
        frames, _ = await asyncio.gather(
            _exec(client, "navig db drop production"),
            _respond_when_pending(mgr, approved=True),
        )

    assert len(calls) == 1, f"spawn must run exactly once, got {len(calls)}"
    env = calls[0]["env"]
    assert env is not None and env.get("NAVIG_NONINTERACTIVE") == "1"
    assert env.get("NO_COLOR") == "1"

    out_lines = [f["line"] for f in frames if f.get("type") == "out"]
    assert "hello from navig" in out_lines
    assert "second line" in out_lines
    _assert_exited(frames, 0)

    records = _audit_records(tmp_path)
    assert [r["status"] for r in records] == ["pending_approval", "approved"]
    assert all(r["action"] == "cli.exec.dangerous" for r in records)


# ── 5. safe/unlisted + default allow: base cli.exec slug, audited ────────────


async def test_safe_command_allows_on_base_slug_and_audits_deck_actor(tmp_path, monkeypatch):
    calls = _record_spawn(monkeypatch)
    gw = _build_gateway(tmp_path, rules=[], approval_manager=None)

    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(_app(gw))) as client:
        frames = await _exec(client, "navig host list")

    assert len(calls) == 1
    _assert_exited(frames, 0)

    records = _audit_records(tmp_path)
    assert [r["status"] for r in records] == ["success"]
    assert records[0]["action"] == "cli.exec"  # base slug — the baseline allows it
    assert records[0]["actor"].startswith("deck:")


# ── 6. no gateway / gateway without policy_check: fail closed, no spawn ──────


async def test_missing_gateway_fails_closed_without_spawning(tmp_path, monkeypatch):
    calls = _record_spawn(monkeypatch)

    from aiohttp.test_utils import TestClient, TestServer

    # (a) app with no gateway wired at all
    async with TestClient(TestServer(_app(None))) as client:
        frames = await _exec(client, "navig host list")
    assert any("authorization unavailable" in line for line in _errs(frames)), frames
    _assert_exited(frames, -2)

    # (b) gateway object present but without a policy_check attribute
    async with TestClient(TestServer(_app(SimpleNamespace()))) as client:
        frames = await _exec(client, "navig host list")
    assert any("authorization unavailable" in line for line in _errs(frames)), frames
    _assert_exited(frames, -2)

    assert calls == []


# ── 7. hostile X-Actor header is ignored: the audit carries the deck actor ───


async def test_hostile_x_actor_header_never_reaches_the_audit(tmp_path, monkeypatch):
    calls = _record_spawn(monkeypatch)
    gw = _build_gateway(tmp_path, rules=[], approval_manager=None)

    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(_app(gw))) as client:
        frames = await _exec(client, "navig host list", headers={"X-Actor": "mallory"})

    assert len(calls) == 1
    _assert_exited(frames, 0)

    records = _audit_records(tmp_path)
    assert records, "the allow path must leave an audit record"
    assert records[0]["actor"] == "deck:7"  # middleware-stamped deck identity
    assert records[0]["actor"] != "mallory"
    assert "mallory" not in json.dumps(records)
