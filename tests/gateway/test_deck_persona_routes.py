"""Contract tests for GET/POST /api/deck/persona.

* the owner comes from ``resolve_owner_user_id`` (not the Bearer sentinel);
* GET with no resolvable owner reads the default; POST refuses with 409;
* ``available`` lists community personas that are NOT installed, and an
  unreachable source degrades to ``[]`` + ``available_error``.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration

pytest.importorskip("aiohttp")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

#: What the auth middleware stamps for a Bearer / local-desktop request.
SENTINEL = 1


def _app(user_id: int = SENTINEL) -> web.Application:
    from navig.gateway.deck.routes.persona import (
        handle_deck_persona_get,
        handle_deck_persona_post,
    )

    @web.middleware
    async def _stamp(request, handler):
        request["deck_user_id"] = user_id
        return await handler(request)

    app = web.Application(middlewares=[_stamp])
    app.router.add_get("/api/deck/persona", handle_deck_persona_get)
    app.router.add_post("/api/deck/persona", handle_deck_persona_post)
    return app


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    from navig.config import reset_config_manager
    from navig.core.shared_config import ConfigSingleton
    from navig.store import runtime as runtime_mod

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    reset_config_manager()
    ConfigSingleton._instance = None
    store = runtime_mod.RuntimeStore(tmp_path / "runtime.db")
    monkeypatch.setattr(runtime_mod, "_store", store)
    yield tmp_path
    store.close()
    reset_config_manager()
    ConfigSingleton._instance = None


def _allow(*users: int) -> None:
    from navig.config import ConfigManager

    ConfigManager().set_global("telegram.allowed_users", list(users))


def _community(tmp_path, monkeypatch, ids: list[str], upstream: list[str] | None = None):
    """A fake community registry with soul.yaml personas.

    *upstream* is what the published repo holds (default: the same ids) — the
    route only offers what Install can actually download.
    """
    from navig.gateway.deck.routes import catalog
    from navig.gateway.deck.routes import persona as persona_route

    published = frozenset(ids if upstream is None else upstream)
    monkeypatch.setattr(persona_route, "_upstream_persona_ids", lambda: published)

    root = tmp_path / "community"
    pdir = root / "personas"
    pdir.mkdir(parents=True)
    entries = []
    for pid in ids:
        (pdir / pid).mkdir()
        (pdir / pid / "soul.yaml").write_text(
            f"id: {pid}\nidentity:\n  name: \"{pid.title()}\"\ntone:\n  primary: technical\n",
            encoding="utf-8",
        )
        entries.append({"id": pid, "name": pid.title(), "description": f"about {pid}"})
    (pdir / "registry.json").write_text(json.dumps({"personas": entries}), encoding="utf-8")
    monkeypatch.setattr(catalog, "_community_root", lambda: root)
    return root


async def _get(app):
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/deck/persona")
        return resp.status, await resp.json()


async def _post(app, body):
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/deck/persona", json=body)
        return resp.status, await resp.json()


async def test_get_lists_default_first_and_available_excludes_installed(isolated, monkeypatch):
    from navig.platform.paths import config_dir

    # "senior-engineer" is installed in the user store; "the-navigator" is not.
    installed = config_dir() / "personas" / "senior-engineer"
    installed.mkdir(parents=True)
    (installed / "soul.yaml").write_text(
        "id: senior-engineer\nidentity:\n  name: Senior Engineer\n", encoding="utf-8"
    )
    _community(isolated, monkeypatch, ["the-navigator", "senior-engineer"])

    status, data = await _get(_app())
    assert status == 200
    names = [p["name"] for p in data["personas"]]
    assert names[0] == "default" and data["personas"][0]["is_default"] is True
    assert "senior-engineer" in names
    available = [a["name"] for a in data["available"]]
    assert available == ["the-navigator"]
    nav = data["available"][0]
    assert nav["display_name"] == "The-Navigator"
    assert nav["tone"] == "direct"  # technical → direct, via the soul.yaml bridge
    assert nav["description"] == "about the-navigator"
    assert "available_error" not in data


async def test_get_available_source_failure_degrades(isolated, monkeypatch):
    from navig.gateway.deck.routes import catalog

    monkeypatch.setattr(catalog, "_community_root", lambda: None)
    status, data = await _get(_app())
    assert status == 200
    assert data["available"] == []
    assert data["available_error"]
    assert data["personas"]  # installed list still served


async def test_get_without_owner_reads_default(isolated, monkeypatch):
    from navig.personas.store import set_active_persona

    _community(isolated, monkeypatch, [])
    set_active_persona(SENTINEL, SENTINEL, "tyler")  # a sentinel row must NOT be read
    _allow(11, 22)  # ambiguous → no owner
    status, data = await _get(_app())
    assert status == 200
    assert data["active"] == "default"


async def test_get_reads_the_owner_not_the_sentinel(isolated, monkeypatch):
    from navig.personas.store import set_active_persona

    _community(isolated, monkeypatch, [])
    _allow(4242)
    set_active_persona(4242, 4242, "tyler")
    status, data = await _get(_app())
    assert data["active"] == "tyler"


async def test_post_without_owner_is_409(isolated):
    _allow(11, 22)
    status, data = await _post(_app(), {"name": "tyler"})
    assert status == 409
    assert data["ok"] is False


async def test_post_switches_the_owner_and_marks_explicit(isolated):
    from navig.personas.store import get_persona_choice

    _allow(4242)
    status, data = await _post(_app(), {"name": "tyler"})
    assert status == 200, data
    assert data["active"] == "tyler"
    assert get_persona_choice(4242) == ("tyler", True)
    assert get_persona_choice(SENTINEL) == ("default", False)


async def test_post_unknown_persona_is_400(isolated):
    _allow(4242)
    status, data = await _post(_app(), {"name": "no-such-persona-xyz"})
    assert status == 400
    assert data["ok"] is False


async def test_a_persona_missing_from_the_published_repo_is_not_offered(isolated, monkeypatch):
    """The local index listed the-navigator while upstream did not — Install could only fail."""
    _community(
        isolated, monkeypatch, ["the-navigator", "senior-engineer"], upstream=["senior-engineer"]
    )
    status, data = await _get(_app())
    assert status == 200
    assert [a["name"] for a in data["available"]] == ["senior-engineer"]


async def test_unreachable_published_repo_reports_instead_of_offering_dead_buttons(
    isolated, monkeypatch
):
    from navig.gateway.deck.routes import persona as persona_route

    _community(isolated, monkeypatch, ["senior-engineer"])

    def _offline():
        raise OSError("no network")

    monkeypatch.setattr(persona_route, "_upstream_persona_ids", _offline)
    status, data = await _get(_app())
    assert status == 200
    assert data["available"] == []
    assert "community" in data["available_error"]
    assert data["personas"]
