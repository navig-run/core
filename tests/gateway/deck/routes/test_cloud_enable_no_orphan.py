"""The deck 'enable cloud' handler must not orphan an existing uplink.

``gw._start_cloud_manager()`` REPLACES ``gw.cloud_manager`` with a fresh instance
WITHOUT stopping the old one. In lighthouse mode the old manager still owns a live
uplink task + aiohttp session, so replacing it leaks that task (it keeps reconnecting
forever). This happens whenever the toggle re-fires while the edge is down
(``cm.status == "error"`` is not in the online/starting short-circuit) — a path that
opened once ``cm.status`` became honest (#649). The handler now stops an existing
non-online manager first, exactly like the restart handler.
"""

from __future__ import annotations

import pytest


class _FakeCM:
    def __init__(self, status: str, events: list) -> None:
        self.status = status
        self._events = events

    async def stop(self) -> None:
        self._events.append("stop")


class _FakeGateway:
    def __init__(self, cm, events: list) -> None:
        self.cloud_manager = cm
        self._events = events

    async def _start_cloud_manager(self) -> None:
        self._events.append("start")


class _App:
    def __init__(self, gw) -> None:
        self._gw = gw

    def get(self, key):
        return self._gw if key in ("gateway", "navig_gateway") else None


class _Req:
    def __init__(self, gw, enabled: bool) -> None:
        self.app = _App(gw)
        self._enabled = enabled

    async def json(self):
        return {"enabled": self._enabled}


class _Cfg:
    """Minimal config stub: writes are no-ops; public_url bypasses the relay gate."""

    def set(self, *a, **k):
        pass

    def save(self, *a, **k):
        pass

    def get(self, key, default=None):
        if key == "cloud.public_url":
            return "https://vps.example.com"
        return default


def _patch_config(monkeypatch):
    pytest.importorskip("aiohttp")
    from navig.gateway.deck.routes import cloud as cloud_mod

    monkeypatch.setattr(cloud_mod, "_config", lambda: _Cfg())
    return cloud_mod


async def test_enable_stops_errored_manager_before_replacing_it(monkeypatch):
    cloud_mod = _patch_config(monkeypatch)
    events: list = []
    gw = _FakeGateway(_FakeCM("error", events), events)

    resp = await cloud_mod.handle_deck_cloud_enabled(_Req(gw, True))

    # Stop must run BEFORE the replacement start. Pre-fix: events == ["start"].
    assert events == ["stop", "start"]
    assert resp.status == 200


async def test_enable_leaves_a_healthy_manager_untouched(monkeypatch):
    cloud_mod = _patch_config(monkeypatch)
    events: list = []
    gw = _FakeGateway(_FakeCM("online", events), events)

    resp = await cloud_mod.handle_deck_cloud_enabled(_Req(gw, True))

    # A live manager short-circuits as already_running — never stopped, never replaced.
    assert events == []
    assert resp.status == 200
