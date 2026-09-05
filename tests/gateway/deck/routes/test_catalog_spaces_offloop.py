"""catalog.py space handlers — scan / enable / disable / activate run off the event loop.

Every one drove blocking filesystem work (discover_space_paths enumeration, manifest loads, ROADMAP
parses, registry + working-dir writes) directly in the async handler. They now run inside
asyncio.to_thread (the treatment handle_deck_spaces (#441) and the memory/context handlers got).
These tests drive the handlers through a real TestServer so the thread hop is exercised, and pin the
behaviour that must survive the move: scan's sort order + empty case, enable/disable's 200-vs-404,
and activate's unknown-space 404 vs happy path.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.integration


def _app():
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.deck.routes import catalog as cat

    app = web.Application()
    app.router.add_get("/spaces/scan", cat.handle_deck_spaces_scan)
    app.router.add_post("/spaces/{id}/enable", cat.handle_deck_space_enable)
    app.router.add_post("/spaces/{id}/disable", cat.handle_deck_space_disable)
    app.router.add_post("/spaces/{id}/activate", cat.handle_deck_space_activate)
    return app, cat


async def _client(app):
    from aiohttp.test_utils import TestClient, TestServer

    c = TestClient(TestServer(app))
    await c.start_server()
    return c


async def test_scan_empty(monkeypatch):
    app, cat = _app()
    import navig.spaces.resolver as resolver

    monkeypatch.setattr(resolver, "discover_space_paths", lambda **k: {})
    monkeypatch.setattr(cat, "_active_path", lambda: None)
    c = await _client(app)
    try:
        r = await c.get("/spaces/scan")
        assert r.status == 200
        data = (await r.json())["data"]
        assert data == {"spaces": [], "active": None}
    finally:
        await c.close()


async def test_scan_sorts_active_then_enabled_then_name(monkeypatch):
    app, cat = _app()
    import navig.spaces.resolver as resolver

    cfgs = {"z": object(), "a": object(), "m": object()}
    cards = {
        "z": {"id": "z", "name": "z", "active": False, "enabled": True},
        "a": {"id": "a", "name": "a", "active": False, "enabled": False},
        "m": {"id": "m", "name": "m", "active": True, "enabled": True},
    }
    monkeypatch.setattr(resolver, "discover_space_paths", lambda **k: cfgs)
    monkeypatch.setattr(cat, "_active_path", lambda: "m")
    monkeypatch.setattr(cat, "_space_card", lambda name, cfg, *, active_path: cards[name])
    c = await _client(app)
    try:
        data = (await (await c.get("/spaces/scan")).json())["data"]
        # active(m) first, then enabled(z), then disabled(a)
        assert [s["id"] for s in data["spaces"]] == ["m", "z", "a"]
        assert data["active"] == "m"
    finally:
        await c.close()


@pytest.mark.parametrize(("registered", "status"), [(True, 200), (False, 404)])
async def test_enable_disable(monkeypatch, registered, status):
    app, cat = _app()
    import navig.spaces.registry as registry

    calls = []

    def _set_enabled(sid, enabled):
        calls.append((sid, enabled))
        return registered

    monkeypatch.setattr(registry, "set_enabled", _set_enabled)
    c = await _client(app)
    try:
        r = await c.post("/spaces/demo/enable")
        assert r.status == status
        r2 = await c.post("/spaces/demo/disable")
        assert r2.status == status
        assert calls == [("demo", True), ("demo", False)]
    finally:
        await c.close()


async def test_activate_unknown_space_is_404(monkeypatch):
    app, cat = _app()
    import navig.spaces.resolver as resolver

    monkeypatch.setattr(resolver, "discover_space_paths", lambda **k: {})
    c = await _client(app)
    try:
        r = await c.post("/spaces/ghost/activate")
        assert r.status == 404
        assert "ghost" in (await r.json())["error"]
    finally:
        await c.close()


async def test_activate_happy_path(monkeypatch, tmp_path):
    app, cat = _app()
    import navig.spaces.active as active
    import navig.spaces.registry as registry
    import navig.spaces.resolver as resolver
    import navig.spaces.space_manifest as sm

    space = tmp_path / "myspace"
    space.mkdir()
    cfg = SimpleNamespace(path=str(space))
    monkeypatch.setattr(resolver, "discover_space_paths", lambda **k: {"myspace": cfg})
    monkeypatch.setattr(sm, "load_space_manifest", lambda p: SimpleNamespace(root="."))

    marked = {}
    monkeypatch.setattr(active, "set_active_working_dir", lambda wd: marked.setdefault("wd", wd))
    monkeypatch.setattr(registry, "mark_active", lambda p: marked.setdefault("active", p))

    c = await _client(app)
    try:
        r = await c.post("/spaces/myspace/activate")
        assert r.status == 200
        data = (await r.json())["data"]
        assert data["id"] == "myspace"
        assert data["active"] is True
        assert data["working_dir"] == str(space.resolve())
        assert marked["active"] == str(space)
    finally:
        await c.close()
