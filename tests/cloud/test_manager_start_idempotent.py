"""CloudManager.start() must be idempotent in lighthouse mode.

Lighthouse mode's "running" marker is ``self._uplink`` — it sets neither ``_proc``
nor ``_heartbeat_task``. The old idempotency guard only checked those two, so a
second ``start()`` overwrote a live uplink and orphaned its reconnect task + aiohttp
session forever. The guard now also checks ``_uplink``; a FAILED start clears it so a
retry is still allowed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from navig.cloud import CloudManager


def _mgr(**kw) -> CloudManager:
    return CloudManager(
        api_key="k", broker_url="https://api.navig.run", gateway_port=8765, **kw
    )


class _StubConnectivity:
    def on_status(self, *a, **k) -> None:
        pass


def _patch_connectivity(monkeypatch) -> None:
    monkeypatch.setattr(
        "navig.notify.producers.connectivity.ConnectivityReporter",
        lambda **kw: _StubConnectivity(),
    )


async def test_second_start_does_not_spawn_a_second_uplink(monkeypatch):
    import navig.cloud.uplink as uplink_mod

    created: list = []

    class _FakeUplink:
        def __init__(self, **kw):
            self.status = "online"
            self.started = 0
            self.stopped = 0
            created.append(self)

        async def start(self):
            self.started += 1

        async def stop(self):
            self.stopped += 1

        def snapshot(self):
            return {"status": self.status}

    monkeypatch.setattr(uplink_mod, "UplinkClient", _FakeUplink)
    _patch_connectivity(monkeypatch)

    m = _mgr(lighthouse_url="https://x.workers.dev")
    m._register_current_url = AsyncMock()  # skip broker network

    await m.start()
    assert len(created) == 1
    assert m._uplink is created[0]
    assert created[0].started == 1

    # A second start() while the uplink is live must be a no-op: no new uplink, and
    # the first is NOT orphaned. Pre-fix this created a 2nd uplink (len == 2).
    await m.start()
    assert len(created) == 1
    assert created[0].stopped == 0


async def test_failed_start_clears_uplink_so_retry_is_allowed(monkeypatch):
    import navig.cloud.uplink as uplink_mod

    created: list = []

    class _BoomUplink:
        def __init__(self, **kw):
            self.status = "off"
            self.stopped = 0
            created.append(self)

        async def start(self):
            raise RuntimeError("edge unreachable")

        async def stop(self):
            self.stopped += 1

        def snapshot(self):
            return {"status": self.status}

    monkeypatch.setattr(uplink_mod, "UplinkClient", _BoomUplink)
    _patch_connectivity(monkeypatch)

    m = _mgr(lighthouse_url="https://x.workers.dev")

    with pytest.raises(RuntimeError):
        await m.start()
    # A failed start must leave no stale reference — otherwise the new _uplink guard
    # would wrongly block a retry. Pre-fix _uplink stayed pointing at the dead client.
    assert m._uplink is None
    assert created[0].stopped == 1  # best-effort teardown ran

    # The retry is not blocked by a stale marker: it reaches the uplink again.
    with pytest.raises(RuntimeError):
        await m.start()
    assert len(created) == 2
