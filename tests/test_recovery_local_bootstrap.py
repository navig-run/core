from __future__ import annotations

from types import SimpleNamespace


def test_bootstrap_local_host_prefers_existing_localhost():
    from navig.cli import recovery

    cfg = SimpleNamespace(active=None)
    cfg.list_hosts = lambda: ["alpha", "localhost"]
    cfg.set_active_host = lambda name: setattr(cfg, "active", name)

    selected = recovery._bootstrap_local_host(cfg)

    assert selected == "localhost"
    assert cfg.active == "localhost"


def test_bootstrap_local_host_creates_localhost(monkeypatch):
    from navig.cli import recovery
    from navig.commands import local_discovery

    cfg = SimpleNamespace()
    cfg.list_hosts = lambda: []

    called = {"ok": False}

    def _fake_discover(name, auto_confirm, set_active, progress):
        called["ok"] = True
        assert name == "localhost"
        assert auto_confirm is True
        assert set_active is True
        assert progress is False
        return {"host": "localhost"}

    monkeypatch.setattr(local_discovery, "discover_local_host", _fake_discover)

    selected = recovery._bootstrap_local_host(cfg)

    assert selected == "localhost"
    assert called["ok"] is True


def test_require_active_host_uses_bootstrap(monkeypatch):
    from navig.cli import recovery

    cfg = SimpleNamespace()
    cfg.get_active_host = lambda: None
    cfg.list_hosts = lambda: []

    monkeypatch.setattr(recovery, "_bootstrap_local_host", lambda _cfg: "localhost")

    selected = recovery.require_active_host({}, cfg)

    assert selected == "localhost"


def test_require_active_server_uses_bootstrap(monkeypatch):
    from navig.cli import recovery

    cfg = SimpleNamespace()
    cfg.get_active_server = lambda: None
    cfg.list_hosts = lambda: []

    monkeypatch.setattr(recovery, "_bootstrap_local_host", lambda _cfg: "localhost")

    selected = recovery.require_active_server({}, cfg)

    assert selected == "localhost"
