"""A gateway stopped mid-boot must not leave a cloud tunnel running.

``stop()`` stops ``self.cloud_manager`` — but when it runs while ``start()`` is still
booting, the manager does not exist yet. ``start()`` then carried on, spawned
cloudflared, and nothing ever stopped it: an orphaned public tunnel (six of them in one
session, measured, from e2e tests whose teardown raced the boot).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from navig.gateway import server as gateway_server


class _FakeManager:
    instances: list[_FakeManager] = []

    def __init__(self, **kwargs):
        self.started = False
        self.stopped = False
        self.on_start = None
        _FakeManager.instances.append(self)

    async def start(self):
        self.started = True
        if self.on_start:
            self.on_start()

    async def stop(self):
        self.stopped = True


@pytest.fixture
def gw(monkeypatch):
    import navig.cloud

    _FakeManager.instances = []
    monkeypatch.setattr(navig.cloud, "CloudManager", _FakeManager)
    g = gateway_server.NavigGateway.__new__(gateway_server.NavigGateway)
    g.running = True
    g.cloud_manager = None
    g.channels = {}
    g.system_events = None
    g.config = SimpleNamespace(port=12345)
    g.config_manager = SimpleNamespace(
        global_config={
            "cloud": {"enabled": True, "public_url": "https://example.invalid"},
            "deck": {"api_key": "k" * 32},
        }
    )
    return g


async def test_stop_during_manager_start_stops_the_manager(gw):
    # stop() lands while the manager is starting (the e2e teardown race).
    def _stop_now():
        gw.running = False

    orig_init = _FakeManager.__init__

    def _init(self, **kw):
        orig_init(self, **kw)
        self.on_start = _stop_now

    _FakeManager.__init__ = _init
    try:
        await gw._start_cloud_manager()
    finally:
        _FakeManager.__init__ = orig_init

    (mgr,) = _FakeManager.instances
    assert mgr.started and mgr.stopped
    assert gw.cloud_manager is None


async def test_stopped_gateway_never_starts_a_manager(gw):
    gw.running = False
    await gw._start_cloud_manager()
    assert _FakeManager.instances == []
    assert gw.cloud_manager is None


async def test_running_gateway_keeps_its_manager(gw):
    await gw._start_cloud_manager()
    (mgr,) = _FakeManager.instances
    assert mgr.started and not mgr.stopped
    assert gw.cloud_manager is mgr


async def test_suite_guard_refuses_a_real_cloudflared():
    from navig.cloud.manager import CloudManager

    m = CloudManager.__new__(CloudManager)
    with pytest.raises(RuntimeError, match="must not spawn a real cloudflared"):
        await m._spawn_cloudflared()
