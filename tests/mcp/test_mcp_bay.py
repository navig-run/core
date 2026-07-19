"""Tests for navig.mcp.tools.bay — register + the list/acquire tool handlers.

The handlers reuse the daemon Bay engine (gateway/deck/routes/catalog) via lazy
imports, so we patch those module attributes to keep the tests hermetic.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import navig.gateway.deck.routes.catalog as cat
from navig.mcp.tools.bay import _tool_bay_acquire, _tool_bay_list, register


def _server() -> MagicMock:
    server = MagicMock()
    server.tools = {}
    server._tool_handlers = {}
    return server


def test_register_adds_tools_and_handlers():
    server = _server()
    register(server)
    assert "navig_bay_list" in server.tools
    assert "navig_bay_acquire" in server.tools
    assert server._tool_handlers["navig_bay_list"] is _tool_bay_list
    assert server._tool_handlers["navig_bay_acquire"] is _tool_bay_acquire


def test_bay_list_projects_a_lean_item(monkeypatch):
    monkeypatch.setattr(cat, "gather_bay_items", lambda **kw: {
        "items": [{
            "slug": "s", "name": "S", "kind": "space", "tagline": "t",
            "unlocked": True, "installed": False, "spec": "github:x", "extra": "dropped",
        }],
        "count": 1, "license_tier": "plus",
    })
    out = _tool_bay_list(_server(), {})
    assert out["count"] == 1 and out["license_tier"] == "plus"
    assert out["items"][0] == {
        "slug": "s", "name": "S", "kind": "space", "tagline": "t",
        "unlocked": True, "installed": False,
    }  # lean projection — internal fields (spec/extra) dropped


def test_bay_list_forwards_filters_and_nulls_empties(monkeypatch):
    seen: dict = {}

    def fake_gather(**kw):
        seen.update(kw)
        return {"items": [], "count": 0, "license_tier": "free"}

    monkeypatch.setattr(cat, "gather_bay_items", fake_gather)
    _tool_bay_list(_server(), {"kind": "plugin", "surface": ""})
    assert seen == {"kind": "plugin", "surface": None}  # empty string → None


def test_bay_acquire_requires_slug():
    out = _tool_bay_acquire(_server(), {})
    assert out["ok"] is False and "slug" in out["error"]


def test_bay_acquire_unknown_slug(monkeypatch):
    monkeypatch.setattr(cat, "_bay_item", lambda slug: None)
    out = _tool_bay_acquire(_server(), {"slug": "nope"})
    assert out["ok"] is False and "not in the Bay" in out["error"]


def test_bay_acquire_installs(monkeypatch):
    monkeypatch.setattr(cat, "_bay_item", lambda slug: {"slug": slug, "kind": "space"})
    monkeypatch.setattr(
        cat, "acquire_bay_item",
        lambda slug, *a, **k: {"slug": slug, "action": "install", "installed": True},
    )
    out = _tool_bay_acquire(_server(), {"slug": "homelab-space"})
    assert out == {"ok": True, "slug": "homelab-space", "action": "install", "installed": True}


def test_bay_acquire_locked_never_installs(monkeypatch):
    monkeypatch.setattr(cat, "_bay_item", lambda slug: {"slug": slug, "kind": "space"})
    monkeypatch.setattr(
        cat, "acquire_bay_item",
        lambda slug, *a, **k: {"action": "locked", "tier_required": "plus", "capability": "item:x"},
    )
    out = _tool_bay_acquire(_server(), {"slug": "x"})
    assert out["ok"] is False and out["locked"] is True and out["tier_required"] == "plus"


def test_bay_acquire_persona_returns_activate(monkeypatch):
    monkeypatch.setattr(cat, "_bay_item", lambda slug: {"slug": slug, "kind": "persona"})
    monkeypatch.setattr(
        cat, "acquire_bay_item",
        lambda slug, *a, **k: {"slug": slug, "action": "activate"},
    )
    out = _tool_bay_acquire(_server(), {"slug": "the-navigator"})
    assert out["ok"] is True and out["action"] == "activate"


def test_bay_acquire_surfaces_error(monkeypatch):
    monkeypatch.setattr(cat, "_bay_item", lambda slug: {"slug": slug, "kind": "plugin"})
    monkeypatch.setattr(
        cat, "acquire_bay_item",
        lambda slug, *a, **k: {"action": "error", "error": "plugin install failed: boom"},
    )
    out = _tool_bay_acquire(_server(), {"slug": "telegram"})
    assert out["ok"] is False and "boom" in out["error"]
