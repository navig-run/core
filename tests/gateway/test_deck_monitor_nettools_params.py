"""Contract tests for the settings-driven monitor + nettools query params.

The desktop OS per-app settings drive these (apps/system + apps/nettools):
- /api/deck/monitor?services=all and /monitor/services?all=1 must request the
  full service list (include_stopped) and must NOT share a cache entry with the
  running-only variant.
- /api/deck/net/dns validates ?resolver= and ?type= (both become subprocess
  argv members) and hands the resolver to nslookup as its server argument.
- get_services_info(include_stopped=True) returns real statuses, running-first.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

pytest.importorskip("aiohttp")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from navig.commands import monitor as monitor_cmd
from navig.gateway.deck.routes import monitor as monitor_routes
from navig.gateway.deck.routes import nettools as nettools_routes

# ── get_services_info filtering + ordering (platform-independent via stubs) ──


class _FakeSvc:
    def __init__(self, name: str, status: str):
        self._name = name
        self._status = status

    def name(self) -> str:
        return self._name

    def display_name(self) -> str:
        return f"Svc {self._name}"

    def status(self) -> str:
        return self._status


class _FakePsutil:
    @staticmethod
    def win_service_iter():
        return [_FakeSvc("beta", "stopped"), _FakeSvc("alpha", "running"), _FakeSvc("gamma", "running")]


@pytest.fixture()
def windows_services(monkeypatch):
    monkeypatch.setattr(monitor_cmd.platform, "system", lambda: "Windows")
    monkeypatch.setattr(monitor_cmd, "_psutil_available", lambda: True)
    monkeypatch.setattr(monitor_cmd, "psutil", _FakePsutil)


def test_services_running_only_by_default(windows_services):
    info = monitor_cmd.get_services_info()
    assert info["count"] == 2
    assert [s["name"] for s in info["services"]] == ["alpha", "gamma"]
    assert all(s["status"] == "running" for s in info["services"])


def test_services_include_stopped_running_first(windows_services):
    info = monitor_cmd.get_services_info(include_stopped=True)
    assert info["count"] == 3
    assert [s["name"] for s in info["services"]] == ["alpha", "gamma", "beta"]
    assert [s["status"] for s in info["services"]] == ["running", "running", "stopped"]


def test_all_services_snapshot_raises_the_row_cap(monkeypatch):
    """Regression (live 2026-07-12): with >40 running services and running-first
    ordering, the default cap hid every stopped service from the all-view — only
    the count changed. The snapshot must raise the cap when all_services=True."""
    seen: list[tuple[int, bool]] = []

    def fake_services(max_services: int = 40, include_stopped: bool = False):
        seen.append((max_services, include_stopped))
        return {"platform": "test", "count": 0, "services": []}

    monkeypatch.setattr(monitor_cmd, "get_services_info", fake_services)
    for name in ("get_system_disk", "get_memory_info", "get_cpu_info",
                 "get_uptime_info", "get_ports_info"):
        monkeypatch.setattr(monitor_cmd, name, lambda: {})
    monitor_cmd.get_all_monitoring()
    monitor_cmd.get_all_monitoring(all_services=True)
    assert seen == [(40, False), (1000, True)]


# ── Route param plumbing + cache-key separation ──────────────────────────────


def _monitor_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/deck/monitor", monitor_routes.handle_deck_monitor_all)
    app.router.add_get("/api/deck/monitor/services", monitor_routes.handle_deck_monitor_services)
    return app


async def test_monitor_all_services_param_and_cache_split(monkeypatch):
    calls: list[bool] = []

    def fake_all(all_services: bool = False):
        calls.append(all_services)
        return {"services": {"count": 99 if all_services else 1}}

    monkeypatch.setattr(monitor_routes.monitor, "get_all_monitoring", fake_all)
    monkeypatch.setattr(monitor_routes, "_cache", {})
    monkeypatch.setattr(monitor_routes, "_locks", {})

    client = TestClient(TestServer(_monitor_app()))
    await client.start_server()
    try:
        r1 = await client.get("/api/deck/monitor")
        r2 = await client.get("/api/deck/monitor?services=all")
        b1, b2 = await r1.json(), await r2.json()
        assert b1["data"]["services"]["count"] == 1
        assert b2["data"]["services"]["count"] == 99
        assert calls == [False, True]
        # Cached separately — repeating both hits neither collector again.
        await client.get("/api/deck/monitor")
        await client.get("/api/deck/monitor?services=all")
        assert calls == [False, True]
    finally:
        await client.close()


async def test_monitor_services_all_param(monkeypatch):
    seen: list[bool] = []

    def fake_services(max_services: int = 40, include_stopped: bool = False):
        seen.append(include_stopped)
        return {"count": 0, "services": []}

    monkeypatch.setattr(monitor_routes.monitor, "get_services_info", fake_services)
    monkeypatch.setattr(monitor_routes, "_cache", {})
    monkeypatch.setattr(monitor_routes, "_locks", {})

    client = TestClient(TestServer(_monitor_app()))
    await client.start_server()
    try:
        await client.get("/api/deck/monitor/services")
        await client.get("/api/deck/monitor/services?all=1")
        assert seen == [False, True]
    finally:
        await client.close()


# ── net/dns resolver + type validation ───────────────────────────────────────


def _dns_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/deck/net/dns", nettools_routes.handle_deck_net_dns)
    return app


async def test_dns_rejects_bad_resolver_and_type():
    client = TestClient(TestServer(_dns_app()))
    await client.start_server()
    try:
        r = await client.get("/api/deck/net/dns?domain=example.com&resolver=1.1.1.1;rm")
        assert r.status == 400
        assert "resolver" in (await r.json())["error"]
        r2 = await client.get("/api/deck/net/dns?domain=example.com&type=--server")
        assert r2.status == 400
        assert "type" in (await r2.json())["error"]
    finally:
        await client.close()


async def test_dns_resolver_becomes_nslookup_server_arg(monkeypatch):
    captured: list[tuple[str, ...]] = []

    class _FakeProc:
        async def communicate(self):
            return b"fake nslookup output", b""

    async def fake_exec(*argv, **kwargs):
        captured.append(argv)
        return _FakeProc()

    monkeypatch.setattr(nettools_routes.asyncio, "create_subprocess_exec", fake_exec)

    client = TestClient(TestServer(_dns_app()))
    await client.start_server()
    try:
        # A custom resolver forces the nslookup path even for A records.
        r = await client.get("/api/deck/net/dns?domain=example.com&type=A&resolver=1.1.1.1")
        body = await r.json()
        assert r.status == 200 and body["ok"] is True
        assert body["data"]["resolver"] == "1.1.1.1"
        assert captured == [("nslookup", "-type=A", "example.com", "1.1.1.1")]
    finally:
        await client.close()
