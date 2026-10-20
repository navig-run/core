"""`/api/deck/engine/*` — the engine controls NAVIG OS and the Mini App drive.

They live under deck auth (the only auth those two surfaces carry) and share their
seams with Telegram's /restart and /doctor, so no surface can disagree. Also pins
the honesty fix on /ops/session and /ops/quick: both answered success while doing
nothing (`enqueue_quick_action` exists nowhere), so the deck showed a phantom.
"""

from __future__ import annotations

import types

import pytest

pytest.importorskip("aiohttp")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from navig.gateway.deck.routes import engine, ops
from navig.gateway.routes import daemon as daemon_routes


def _app(policy=None) -> web.Application:
    async def allow(_action, _actor):
        return None

    gw = types.SimpleNamespace(policy_check=policy or allow, cloud_manager=None)
    app = web.Application()
    app["gateway"] = gw
    engine.register(app)
    app.router.add_post("/api/deck/ops/session", ops.handle_deck_ops_session)
    app.router.add_post("/api/deck/ops/quick", ops.handle_deck_ops_quick)
    return app


@pytest.fixture
def no_cause(monkeypatch):
    monkeypatch.setattr("navig.daemon.restart_cause.record", lambda *a, **k: None)


async def test_status_carries_engine_facts_and_cloud(monkeypatch):
    monkeypatch.setattr(daemon_routes, "_engine_facts", lambda: {"version": "1.0", "restartAvailable": True})
    monkeypatch.setattr(engine, "_daemon_up", lambda: True)
    async with TestClient(TestServer(_app())) as c:
        res = await c.get("/api/deck/engine")
        body = await res.json()
    d = body["data"]
    assert res.status == 200 and d["version"] == "1.0" and d["daemonRunning"] is True
    assert d["cloud"] == {"status": "off", "url": None}


async def test_restart_goes_through_the_shared_seam(monkeypatch, no_cause):
    monkeypatch.setattr(daemon_routes.sys, "platform", "win32")
    monkeypatch.setattr(daemon_routes, "_run_restart_task", lambda: (True, "ok"))
    async with TestClient(TestServer(_app())) as c:
        res = await c.post("/api/deck/engine/restart")
        body = await res.json()
    assert res.status == 200 and body["data"]["result_code"] == "restart_requested_via_task"


async def test_restart_without_the_task_is_a_409_with_the_remedy(monkeypatch, no_cause):
    monkeypatch.setattr(daemon_routes.sys, "platform", "win32")
    monkeypatch.setattr(daemon_routes, "_run_restart_task", lambda: (False, "missing"))
    async with TestClient(TestServer(_app())) as c:
        res = await c.post("/api/deck/engine/restart")
        body = await res.json()
    assert res.status == 409 and body["ok"] is False
    assert "navig service install" in body["error"]


async def test_restart_respects_the_policy_gate(monkeypatch, no_cause):
    def boom():
        raise AssertionError("a denied restart must not run")

    monkeypatch.setattr(daemon_routes, "_run_restart_task", boom)

    async def deny(action, _actor):
        assert action == "system.restart"
        return web.json_response({"ok": False, "error": "denied"}, status=403)

    async with TestClient(TestServer(_app(deny))) as c:
        res = await c.post("/api/deck/engine/restart")
    assert res.status == 403


async def test_doctor_failure_is_reported_not_green(monkeypatch):
    import navig.daemon.engine_ops as eo

    async def broken(*a, **k):
        raise RuntimeError("probe died")

    monkeypatch.setattr(eo, "collect_doctor", broken)
    async with TestClient(TestServer(_app())) as c:
        res = await c.get("/api/deck/engine/doctor")
        body = await res.json()
    assert res.status == 500 and body["ok"] is False and "probe died" in body["error"]


async def test_heal_returns_the_document(monkeypatch):
    import navig.daemon.engine_ops as eo

    async def healed(*a, **k):
        return {"ok": True, "actions": []}

    monkeypatch.setattr(eo, "run_heal", healed)
    async with TestClient(TestServer(_app())) as c:
        res = await c.post("/api/deck/engine/heal")
        body = await res.json()
    assert res.status == 200 and body["data"]["ok"] is True


@pytest.mark.parametrize("path,payload", [
    ("/api/deck/ops/session", {"action": "pause"}),
    ("/api/deck/ops/quick", {"action": "health_check"}),
])
async def test_phantom_controls_no_longer_report_success(path, payload):
    async with TestClient(TestServer(_app())) as c:
        res = await c.post(path, json=payload)
        body = await res.json()
    assert res.status == 501 and body["ok"] is False and body.get("hint")


async def test_heal_runner_rejects_empty_output(monkeypatch):
    import asyncio

    import navig.daemon.engine_ops as eo

    class P:
        returncode = 1

        async def communicate(self):
            return b"", b"boom"

    async def fake_exec(*a, **k):
        return P()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(ValueError):
        await eo.run_heal()


@pytest.mark.parametrize("stored,expected", [("false", False), ("0", False), ("true", True), (True, True), (None, False)])
def test_auto_dispatch_flag_is_coerced(monkeypatch, stored, expected):
    """The one autonomy toggle the OS Engine card exposes must read "false" as off."""
    import navig.core as core_mod
    from navig.requests import registry

    class FakeConfig:
        def get(self, key):
            return {"auto_dispatch": stored} if key == "ai" else None

    monkeypatch.setattr(core_mod, "Config", FakeConfig)
    assert registry._auto_dispatch_enabled() is expected
