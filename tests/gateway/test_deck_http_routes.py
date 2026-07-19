"""HTTP-level contract tests for the deck routes the DESKTOP OS consumes.

The os brain proxy (`apps/os/packages/server-core/.../brain.ts:deckFetch`)
unwraps `{ok, data}` envelopes and passes FLAT bodies through whole. Two
response shapes therefore coexist on /api/deck/* and each side depends on one:

- `/modules` is FLAT (`{ok, modules, ...}` — the deck reads it directly, so it
  can never be re-enveloped). If it grew a `data` key, the proxy would start
  returning only that key and starve the desktop catalog.
- `/bay` is ENVELOPED (`_ok({items, ...})`). If it went flat, the proxy would
  hand consumers the raw envelope and every field access would shift.

These tests pin both shapes OVER REAL HTTP (TestClient), plus the app-surface
fields the desktop sidebar renders from. A prior shape mismatch here shipped a
desktop that silently ran on its offline fallback — this is the tripwire.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration

pytest.importorskip("aiohttp")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import navig.gateway.deck.routes.catalog as cat
from navig.gateway.deck.routes.modules import handle_deck_modules
from navig.modules.registry import BUILTIN_MODULES, ModuleRegistry


def _seeded_registry() -> ModuleRegistry:
    reg = ModuleRegistry()
    reg._defs = {m.id: m for m in BUILTIN_MODULES}
    reg._discovered = True
    return reg


def _app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/deck/modules", handle_deck_modules)
    app.router.add_get("/api/deck/bay", cat.handle_deck_bay)
    return app


async def test_modules_is_flat_and_carries_app_surface_fields(monkeypatch):
    monkeypatch.setattr("navig.modules.registry.get_registry", _seeded_registry)

    async with TestClient(TestServer(_app())) as client:
        res = await client.get("/api/deck/modules")
        assert res.status == 200
        body = await res.json()

    # FLAT shape contract (see module docstring) — no `data` key, ever.
    assert body["ok"] is True
    assert "data" not in body
    assert isinstance(body["modules"], list) and body["modules"]

    by_id = {m["id"]: m for m in body["modules"]}
    system = by_id["system"]
    assert system["app_category"] == "Systems"
    assert system["scope"] == "brain"
    assert system["hidden"] is False
    assert any(s.startswith("os-tile:") for s in system["surfaces"])
    # Deck-only split survives serialization.
    assert not any(s.startswith("os-tile:") for s in by_id["telegram"]["surfaces"])
    assert by_id["contacts"]["merged_into"] == "messages"


async def test_bay_is_enveloped_and_enriched(monkeypatch, tmp_path):
    catalog = {
        "generatedAt": "2026-07-10",
        "items": [
            {"slug": "free-skill", "name": "Free Skill", "kind": "skill",
             "surfaces": ["cli"], "pricing": {"model": "free"}, "install": ""},
            {"slug": "buy-space", "name": "Buy Space", "kind": "space",
             "surfaces": ["deck"], "install": "",
             "pricing": {"model": "buy_once", "priceUsd": 19, "includedInTier": "plus"}},
        ],
    }
    p = tmp_path / "bay-catalog.json"
    p.write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setattr(cat, "_bay_catalog_path", lambda: p)
    monkeypatch.setattr(cat, "_installed_space_ids", set)

    class _Free:
        effective_tier = "free"
        capabilities = ["core_ops"]

    monkeypatch.setattr("navig.license.current_status", lambda: _Free())

    async with TestClient(TestServer(_app())) as client:
        res = await client.get("/api/deck/bay")
        assert res.status == 200
        body = await res.json()

    # ENVELOPE shape contract — everything lives under `data`.
    assert body["ok"] is True
    assert "data" in body
    data = body["data"]
    assert data["count"] == 2
    by_slug = {i["slug"]: i for i in data["items"]}
    assert by_slug["free-skill"]["unlocked"] is True
    assert by_slug["buy-space"]["unlocked"] is False        # free tier, unowned
    assert by_slug["buy-space"]["capability"] == "item:buy-space"
    assert by_slug["buy-space"]["installed"] is False
