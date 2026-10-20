"""`/monitor?scope=vitals` answers the dashboards' strip and skips the expensive half.

Every dashboard polls the monitor route every few seconds. The full snapshot also
enumerates installed services and listening ports — on Windows the expensive half —
and no dashboard shows either. The vitals scope is a separate cache key, so it can
never serve (or be served by) the full snapshot.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration


class _Req:
    def __init__(self, query: dict | None = None) -> None:
        self.query = query or {}
        self.app: dict = {}


def _body(resp) -> dict:
    return json.loads(resp.body.decode())


@pytest.fixture
def monitor_mod(monkeypatch):
    from navig.gateway.deck.routes import monitor as mon

    calls: list[str] = []
    monkeypatch.setattr(mon, "_cache", {})

    def _vitals():
        calls.append("vitals")
        return {"cpu": {"percent": 1}, "memory": {}, "disk": [], "uptime": {}, "ts": "now"}

    def _all(all_services: bool = False):
        calls.append("all_full" if all_services else "all")
        return {"cpu": {}, "memory": {}, "disk": [], "uptime": {}, "services": [], "ports": []}

    monkeypatch.setattr(mon.monitor, "get_vitals", _vitals)
    monkeypatch.setattr(mon.monitor, "get_all_monitoring", _all)
    return mon, calls


async def test_vitals_scope_answers_the_strip_without_services_or_ports(monitor_mod):
    mon, calls = monitor_mod
    body = _body(await mon.handle_deck_monitor_all(_Req({"scope": "vitals"})))
    assert calls == ["vitals"], "the full collector (services + ports) must not run"
    data = body["data"]
    assert "cpu" in data and "memory" in data and "disk" in data and "uptime" in data
    assert "services" not in data and "ports" not in data


async def test_vitals_and_full_do_not_share_a_cache_key(monitor_mod):
    mon, calls = monitor_mod
    await mon.handle_deck_monitor_all(_Req({"scope": "vitals"}))
    await mon.handle_deck_monitor_all(_Req())
    assert calls == ["vitals", "all"], "each variant must collect its own payload"
