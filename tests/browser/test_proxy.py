"""Stage 2 — the BYO proxy pool (parsing, rotation, cooldown, per-profile override)."""

from __future__ import annotations

import pytest

from navig.browser import profiles as p
from navig.browser import proxy as px

# ── ProxySpec parsing / adapters ──────────────────────────────────────────────

def test_from_url_splits_credentials():
    s = px.ProxySpec.from_url("http://user:p%40ss@host.example:8080")
    assert s.server == "http://host.example:8080"
    assert s.username == "user"
    assert s.password == "p@ss"  # percent-decoded


def test_from_url_bare_hostport_defaults_http():
    s = px.ProxySpec.from_url("1.2.3.4:9000")
    assert s.server == "http://1.2.3.4:9000"
    assert s.username is None


def test_from_config_dict_creds_win_over_url():
    s = px.ProxySpec.from_config(
        {"server": "socks5://old:bad@h:1", "username": "u2", "password": "p2", "label": "res"})
    assert s.server == "socks5://h:1"
    assert (s.username, s.password, s.label) == ("u2", "p2", "res")


def test_from_config_rejects_empty():
    assert px.ProxySpec.from_config("") is None
    assert px.ProxySpec.from_config({"server": ""}) is None


def test_to_playwright_omits_missing_creds():
    assert px.ProxySpec.from_url("http://h:1").to_playwright() == {"server": "http://h:1"}
    d = px.ProxySpec("http://h:1", "u", "p").to_playwright()
    assert d == {"server": "http://h:1", "username": "u", "password": "p"}


def test_to_url_roundtrips_and_encodes():
    s = px.ProxySpec("http://h:1", "u", "p@ss")
    assert s.to_url() == "http://u:p%40ss@h:1"
    # round-trip back to the same spec
    again = px.ProxySpec.from_url(s.to_url())
    assert (again.server, again.username, again.password) == ("http://h:1", "u", "p@ss")


def test_redacted_hides_password():
    r = px.ProxySpec("http://h:1", "user", "secret", "lbl").redacted()
    assert "secret" not in r
    assert "user" in r


# ── ProxyPool rotation + cooldown ─────────────────────────────────────────────

def _pool(n=3, **kw):
    specs = [px.ProxySpec(f"http://h{i}:1", label=f"p{i}") for i in range(n)]
    return px.ProxyPool(specs, **kw)


def test_round_robin_cycles():
    pool = _pool(3)
    got = [pool.next().server for _ in range(4)]
    assert got == ["http://h0:1", "http://h1:1", "http://h2:1", "http://h0:1"]


def test_empty_pool_returns_none():
    assert px.ProxyPool([]).next() is None


def test_cooldown_sidelines_then_restores():
    clock = {"t": 1000.0}
    pool = _pool(2, cooldown_seconds=60, clock=lambda: clock["t"])
    first = pool.next()
    pool.mark_blocked(first)
    # blocked one is skipped while cooling down
    for _ in range(4):
        assert pool.next().server != first.server
    # after the cooldown it's usable again
    clock["t"] += 61
    assert first in pool.available()


def test_all_blocked_returns_none():
    clock = {"t": 0.0}
    pool = _pool(2, cooldown_seconds=100, clock=lambda: clock["t"])
    for s in list(pool.available()):
        pool.mark_blocked(s)
    assert pool.next() is None


def test_random_rotation_only_returns_available():
    clock = {"t": 0.0}
    pool = _pool(3, rotation="random", cooldown_seconds=100, clock=lambda: clock["t"])
    blocked = pool.next()
    pool.mark_blocked(blocked)
    for _ in range(20):
        assert pool.next().server != blocked.server


# ── config-backed pool + resolver ─────────────────────────────────────────────

@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("navig.browser.targets.probe_port", lambda *a, **k: None)
    px.reset_pool()
    yield tmp_path
    px.reset_pool()


def test_get_pool_reads_config(cfg, monkeypatch):
    monkeypatch.setattr(px, "_load_browser_config", lambda: {
        "proxies": ["http://a:1", {"server": "socks5://b:2", "username": "u"}],
        "proxy_rotation": "round-robin",
    })
    px.reset_pool()
    pool = px.get_pool()
    assert len(pool) == 2
    assert pool.next().server == "http://a:1"


def test_get_pool_rebuilds_when_config_changes(cfg, monkeypatch):
    monkeypatch.setattr(px, "_load_browser_config", lambda: {"proxies": ["http://a:1"]})
    px.reset_pool()
    assert len(px.get_pool()) == 1
    monkeypatch.setattr(px, "_load_browser_config", lambda: {"proxies": ["http://a:1", "http://b:2"]})
    assert len(px.get_pool()) == 2  # signature changed → rebuilt


def test_resolve_proxy_profile_override_wins(cfg, monkeypatch):
    monkeypatch.setattr(px, "_load_browser_config", lambda: {"proxies": ["http://pool:1"]})
    px.reset_pool()
    spec = px.resolve_proxy("http://user:pw@personal:9")
    assert spec.server == "http://personal:9"
    assert spec.username == "user"


def test_resolve_proxy_url_none_when_empty(cfg, monkeypatch):
    monkeypatch.setattr(px, "_load_browser_config", lambda: {})
    px.reset_pool()
    assert px.resolve_proxy_url() is None


# ── per-profile proxy persistence ─────────────────────────────────────────────

def test_set_profile_proxy_roundtrip(cfg):
    p.create_profile("work")
    assert p.set_profile_proxy("work", "http://u:pw@h:1") is True
    assert p.get_profile("work").proxy == "http://u:pw@h:1"
    # clearing removes it
    assert p.set_profile_proxy("work", None) is True
    assert p.get_profile("work").proxy is None


def test_set_profile_proxy_unknown_profile(cfg):
    assert p.set_profile_proxy("nope", "http://h:1") is False
