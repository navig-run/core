"""POST /spaces/register with ``auto`` is the background back-fill — it never resurrects.

Regression (2026-10-18): NAVIG OS's workspace sync re-registers every local workspace on
each run. Its ``registerBrainSpace`` scaffolds a bare ``.navig/`` first, so the endpoint's
``is_space_dir`` check passed for ghost folders and ``register`` lifted the tombstone —
``navig space forget`` stopped sticking within minutes while the OS was running (exactly
the two ghosts carrying an empty ``.navig/`` came back).

The contract now splits:
- ``auto: true`` → core answers ``{skipped: "forgotten"}`` (tombstone intact, nothing
  written) or ``{skipped: "not_a_space"}`` (bare folder; the caller may scaffold, then
  retry explicitly). Never a lift, never a scaffold here.
- no ``auto`` → unchanged: an explicit register is the documented way to bring a
  forgotten space back, and a bare folder is still refused with 400.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def _app():
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.deck.routes import catalog as catalog_mod

    app = web.Application()
    app.router.add_post("/spaces/register", catalog_mod.handle_deck_space_register)
    return app


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("NAVIG_HOME", raising=False)
    monkeypatch.delenv("NAVIG_SPACE", raising=False)
    from navig.config import reset_config_manager

    reset_config_manager()
    yield tmp_path
    reset_config_manager()


def _real_space(root, name: str):
    """A folder that qualifies as a space: non-empty .navig/ (the strict rule)."""
    space = root / name
    (space / ".navig").mkdir(parents=True)
    (space / ".navig" / "plans").mkdir()
    (space / ".navig" / "plans" / "current.md").write_text("# plan\n", encoding="utf-8")
    return space


async def test_isolation_guard(tmp_path, monkeypatch):
    """The registry must live under the temp config dir — never the real ~/.navig."""
    from navig.config import reset_config_manager
    from navig.platform import paths

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("NAVIG_HOME", raising=False)
    reset_config_manager()
    try:
        assert str(paths.config_dir()) == str(tmp_path), (
            f"config_dir resolved to {paths.config_dir()} — the test would touch the real store"
        )
    finally:
        reset_config_manager()


async def test_auto_never_resurrects_a_forgotten_path(isolated):
    from aiohttp.test_utils import TestClient, TestServer

    from navig.spaces import registry as _registry

    space = _real_space(isolated, "ghost-space")
    _registry.register(space, id="ghost", name="Ghost", source="external", enabled=True)
    _registry.forget(str(space))
    assert _registry.is_forgotten(space)

    async with TestClient(TestServer(_app())) as client:
        r = await client.post("/spaces/register", json={"path": str(space), "auto": True})
        assert r.status == 200
        body = await r.json()

    assert body["ok"] is True
    assert body["data"] == {"skipped": "forgotten"}
    assert _registry.is_forgotten(space), "auto must not lift the tombstone"
    rows = [s for s in _registry.load_registry().get("spaces", []) if str(s.get("path")) == str(space)]
    assert rows == [], f"auto resurrected a forgotten row: {rows}"


async def test_auto_never_scaffolds_on_a_bare_folder(isolated):
    from aiohttp.test_utils import TestClient, TestServer

    from navig.spaces import registry as _registry

    bare = isolated / "bare-workspace"
    bare.mkdir()

    async with TestClient(TestServer(_app())) as client:
        r = await client.post("/spaces/register", json={"path": str(bare), "auto": True})
        assert r.status == 200
        body = await r.json()

    assert body["ok"] is True
    assert body["data"] == {"skipped": "not_a_space"}
    assert not (bare / ".navig").exists(), "the endpoint must not scaffold in auto mode"
    rows = [s for s in _registry.load_registry().get("spaces", []) if str(s.get("path")) == str(bare)]
    assert rows == []


async def test_auto_registers_a_real_space(isolated):
    from aiohttp.test_utils import TestClient, TestServer

    from navig.spaces import registry as _registry

    space = _real_space(isolated, "real-space")

    async with TestClient(TestServer(_app())) as client:
        r = await client.post("/spaces/register", json={"path": str(space), "auto": True})
        assert r.status == 201
        body = await r.json()

    assert body["ok"] is True
    rows = [s for s in _registry.load_registry().get("spaces", []) if str(s.get("path")) == str(space)]
    assert len(rows) == 1


async def test_explicit_register_still_refuses_a_bare_folder(isolated):
    """Unchanged contract: without auto a bare folder is a 400, not a scaffold."""
    from aiohttp.test_utils import TestClient, TestServer

    bare = isolated / "bare-explicit"
    bare.mkdir()

    async with TestClient(TestServer(_app())) as client:
        r = await client.post("/spaces/register", json={"path": str(bare)})
        assert r.status == 400
        body = await r.json()

    assert body["ok"] is False
    assert "not a space" in body["error"]
    assert not (bare / ".navig").exists()


async def test_explicit_register_lifts_a_forget(isolated):
    """The documented escape hatch stays open: register <path> brings it back."""
    from aiohttp.test_utils import TestClient, TestServer

    from navig.spaces import registry as _registry

    space = _real_space(isolated, "revivable-space")
    _registry.register(space, id="revivable", name="Revivable", source="external", enabled=True)
    _registry.forget(str(space))
    assert _registry.is_forgotten(space)

    async with TestClient(TestServer(_app())) as client:
        r = await client.post("/spaces/register", json={"path": str(space)})
        assert r.status == 201
        body = await r.json()

    assert body["ok"] is True
    assert not _registry.is_forgotten(space), "explicit register must lift the forget"
    rows = [s for s in _registry.load_registry().get("spaces", []) if str(s.get("path")) == str(space)]
    assert len(rows) == 1
