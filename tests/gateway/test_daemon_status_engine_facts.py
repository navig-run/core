"""`/api/daemon/status` carries what a manager surface needs to control the engine.

NAVIG OS and the Mini App render a Restart button only when restart can actually
work (`restartAvailable`), and show version/uptime. Each fact is best-effort: a probe
that fails reports `None` ("could not tell"), never a guessed healthy value, and the
original three fields stay exactly as they were.
"""

from __future__ import annotations

import types

import pytest

pytest.importorskip("aiohttp")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from navig.gateway.routes import daemon as daemon_routes


def _app() -> web.Application:
    gw = types.SimpleNamespace(config=types.SimpleNamespace(auth_token=None))
    app = web.Application()
    daemon_routes.register(app, gw)
    return app


async def test_status_keeps_the_original_fields_and_adds_engine_facts(monkeypatch):
    monkeypatch.setattr(
        daemon_routes,
        "_engine_facts",
        lambda: {"version": "1.2.3", "uptimeSeconds": 42, "restartAvailable": True},
    )
    async with TestClient(TestServer(_app())) as client:
        res = await client.get("/api/daemon/status")
        body = await res.json()
    data = body["data"]
    assert res.status == 200
    for key in ("daemonStatus", "activeFormation", "nodes"):
        assert key in data
    assert data["version"] == "1.2.3" and data["restartAvailable"] is True


@pytest.fixture
def no_real_probes(monkeypatch):
    """Never touch the operator's real pid file or service registration."""
    import navig.daemon.launch as launch
    from navig.daemon.supervisor import NavigDaemon

    monkeypatch.setattr(NavigDaemon, "read_pid", staticmethod(lambda: None))
    monkeypatch.setattr(launch, "supervisor_runs_the_bot", lambda: None)
    monkeypatch.setattr(launch, "supervisor_runs_the_gateway", lambda: None)
    monkeypatch.setattr(launch, "service_is_installed", lambda: False)
    return launch


def test_engine_facts_never_raises_and_says_unknown(monkeypatch, no_real_probes):
    def boom():
        raise RuntimeError("probe failed")

    monkeypatch.setattr(no_real_probes, "supervisor_runs_the_bot", boom)
    monkeypatch.setattr(daemon_routes.sys, "platform", "linux")
    facts = daemon_routes._engine_facts()
    assert facts["supervisesBot"] is None
    assert facts["restartAvailable"] is True  # POSIX: service restart is always the path
    assert "daemonPid" not in facts, "the unauthenticated status route must not leak a pid"


def test_restart_unavailable_on_windows_without_the_task(monkeypatch, no_real_probes):
    import navig.daemon.service_manager as sm

    monkeypatch.setattr(daemon_routes.sys, "platform", "win32")
    monkeypatch.setattr(sm, "task_scheduler_restart_task_installed", lambda: False)
    assert daemon_routes._engine_facts()["restartAvailable"] is False
