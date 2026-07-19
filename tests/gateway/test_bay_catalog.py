"""GET /api/deck/bay — the full Harbor Bay catalog + live entitlement enrichment.

Covers `handle_deck_bay` in core/navig/gateway/deck/routes/catalog.py: the
per-item unlock/capability/installed derivation, the ?kind/?surface filters,
and graceful degradation when the generated artifact is absent or the license
subsystem is down. The catalog artifact + license are stubbed so the test is
hermetic (no real ~/.navig, no generated file dependency).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

pytest.importorskip("aiohttp")

import navig.gateway.deck.routes.catalog as cat


@dataclass
class _FakeStatus:
    effective_tier: str = "free"
    capabilities: list[str] = field(default_factory=lambda: ["core_ops"])


class _Req:
    """Minimal stand-in for aiohttp.web.Request (only .query is read)."""

    def __init__(self, *, kind: str | None = None, surface: str | None = None):
        self.query: dict[str, str] = {}
        if kind is not None:
            self.query["kind"] = kind
        if surface is not None:
            self.query["surface"] = surface


CATALOG = {
    "generatedAt": "2026-07-09",
    "items": [
        {"slug": "free-skill", "kind": "skill", "surfaces": ["cli"], "pricing": {"model": "free"}},
        {"slug": "plus-space", "kind": "space", "surfaces": ["cli", "deck"],
         "pricing": {"model": "tier", "includedInTier": "plus"}},
        {"slug": "buy-space", "kind": "space", "surfaces": ["deck"],
         "pricing": {"model": "buy_once", "priceUsd": 19, "includedInTier": "plus"}},
    ],
}


def _patch(monkeypatch, tmp_path, *, catalog=CATALOG, status=None, installed=frozenset()):
    p = tmp_path / "bay-catalog.json"
    p.write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setattr(cat, "_bay_catalog_path", lambda: p)
    monkeypatch.setattr(cat, "_installed_space_ids", lambda: set(installed))
    import navig.license as lic

    monkeypatch.setattr(lic, "current_status", lambda: status or _FakeStatus())


def _data(resp):
    assert resp.status == 200
    body = json.loads(resp.body.decode())
    assert body["ok"] is True
    return body["data"]


async def test_free_tier_locks_priced_and_shows_free(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path, status=_FakeStatus())
    data = _data(await cat.handle_deck_bay(_Req()))
    assert data["source"] == "bundled"
    assert data["count"] == 3
    by = {i["slug"]: i for i in data["items"]}
    assert by["free-skill"]["unlocked"] is True and by["free-skill"]["capability"] is None
    assert by["plus-space"]["unlocked"] is False  # tier-gated, free doesn't cover
    assert by["buy-space"]["unlocked"] is False
    assert by["buy-space"]["capability"] == "item:buy-space"


async def test_owned_item_unlocked_forever(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path, status=_FakeStatus(capabilities=["core_ops", "item:buy-space"]))
    by = {i["slug"]: i for i in _data(await cat.handle_deck_bay(_Req()))["items"]}
    assert by["buy-space"]["unlocked"] is True   # bought → forever
    assert by["plus-space"]["unlocked"] is False  # still gated on free tier


async def test_covering_tier_unlocks(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path, status=_FakeStatus(effective_tier="max"))
    by = {i["slug"]: i for i in _data(await cat.handle_deck_bay(_Req()))["items"]}
    assert by["plus-space"]["unlocked"] is True  # max outranks plus
    assert by["buy-space"]["unlocked"] is True    # covered while subscribed


async def test_installed_flag_only_on_spaces(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path, status=_FakeStatus(), installed={"plus-space"})
    by = {i["slug"]: i for i in _data(await cat.handle_deck_bay(_Req()))["items"]}
    assert by["plus-space"]["installed"] is True
    assert by["buy-space"]["installed"] is False
    assert "installed" not in by["free-skill"]  # non-space carries no installed flag


async def test_kind_and_surface_filters(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path, status=_FakeStatus())
    spaces = _data(await cat.handle_deck_bay(_Req(kind="space")))
    assert spaces["count"] == 2 and all(i["kind"] == "space" for i in spaces["items"])
    cli = _data(await cat.handle_deck_bay(_Req(surface="cli")))
    assert {i["slug"] for i in cli["items"]} == {"free-skill", "plus-space"}  # buy-space is deck-only


async def test_missing_artifact_degrades_gracefully(monkeypatch, tmp_path):
    monkeypatch.setattr(cat, "_bay_catalog_path", lambda: None)
    monkeypatch.setattr(cat, "_installed_space_ids", lambda: set())
    import navig.license as lic

    monkeypatch.setattr(lic, "current_status", lambda: _FakeStatus())
    data = _data(await cat.handle_deck_bay(_Req()))  # 200, not an error
    assert data["source"] == "unavailable"
    assert data["items"] == [] and data["count"] == 0


async def test_license_down_fails_open(monkeypatch, tmp_path):
    def boom():
        raise RuntimeError("license subsystem down")

    p = tmp_path / "bay-catalog.json"
    p.write_text(json.dumps(CATALOG), encoding="utf-8")
    monkeypatch.setattr(cat, "_bay_catalog_path", lambda: p)
    monkeypatch.setattr(cat, "_installed_space_ids", lambda: set())
    import navig.license as lic

    monkeypatch.setattr(lic, "current_status", boom)
    data = _data(await cat.handle_deck_bay(_Req()))
    # License unreadable → never hide the catalog; everything shows unlocked.
    assert all(i["unlocked"] for i in data["items"])
    assert data["license_tier"] == "free"


async def test_items_carry_install_ready_spec(monkeypatch, tmp_path):
    catalog = {
        "items": [
            {"slug": "s", "kind": "space", "surfaces": ["cli"], "pricing": {"model": "free"},
             "install": "navig space install github:navig-run/community/spaces/s"},
            {"slug": "w", "kind": "webapp", "surfaces": ["cli"], "pricing": {"model": "free"},
             "install": "navig install webapp github:navig-run/community/library/apps/ai"},
            {"slug": "p", "kind": "persona", "surfaces": ["cli"], "pricing": {"model": "free"},
             "install": "navig persona use p"},
            {"slug": "b", "kind": "block", "surfaces": ["cli"], "pricing": {"model": "free"},
             "install": "navig apply b"},
        ]
    }
    _patch(monkeypatch, tmp_path, catalog=catalog, status=_FakeStatus())
    by = {i["slug"]: i for i in _data(await cat.handle_deck_bay(_Req()))["items"]}
    # github-installable kinds get the bare spec the install endpoint accepts…
    assert by["s"]["spec"] == "github:navig-run/community/spaces/s"
    assert by["w"]["spec"] == "github:navig-run/community/library/apps/ai"
    # …persona (use) and block (apply) are acquired differently → no spec.
    assert by["p"]["spec"] is None
    assert by["b"]["spec"] is None


async def test_non_dict_items_are_skipped(monkeypatch, tmp_path):
    bad = {"items": [None, "nope", 42,
                     {"slug": "ok", "kind": "skill", "surfaces": ["cli"], "pricing": {"model": "free"}}]}
    _patch(monkeypatch, tmp_path, catalog=bad, status=_FakeStatus())
    data = _data(await cat.handle_deck_bay(_Req()))
    assert data["count"] == 1 and data["items"][0]["slug"] == "ok"


# ── /api/deck/bay/acquire — dispatch by kind + server-side gate ────────────────

class _AcqReq:
    def __init__(self, body: dict):
        self._body = body

    async def json(self):
        return self._body


def _patch_acquire(monkeypatch, *, item=None, status=None):
    """Gate lookup: ``_bay_item`` returns *item*; license via ``current_status``."""
    monkeypatch.setattr(cat, "_bay_item", lambda slug: item)
    import navig.license as lic

    monkeypatch.setattr(lic, "current_status", lambda: status or _FakeStatus())


async def test_acquire_github_item_installs(monkeypatch):
    calls: list[str] = []
    import navig.commands.install as inst

    monkeypatch.setattr(inst, "install_asset", lambda spec, **kw: calls.append(spec))
    _patch_acquire(monkeypatch)
    data = _data(await cat.handle_deck_bay_acquire(_AcqReq(
        {"slug": "s", "kind": "space",
         "install": "navig space install github:navig-run/community/spaces/s"}
    )))
    assert data["action"] == "install" and data["installed"] is True
    assert calls == ["github:navig-run/community/spaces/s"]


async def test_acquire_plugin_installs_via_pluginhost(monkeypatch):
    installed: list[str] = []

    class _FakeHost:
        def install(self, name):
            installed.append(name)

    import navig.plugins.host as host

    monkeypatch.setattr(host, "PluginHost", _FakeHost)
    _patch_acquire(monkeypatch)
    data = _data(await cat.handle_deck_bay_acquire(_AcqReq(
        {"slug": "telegram", "kind": "plugin", "install": "navig plugin install telegram"}
    )))
    assert data["action"] == "install"
    assert installed == ["telegram"]


async def test_acquire_persona_returns_activate_not_switch(monkeypatch):
    _patch_acquire(monkeypatch)
    data = _data(await cat.handle_deck_bay_acquire(_AcqReq(
        {"slug": "p", "kind": "persona", "install": "navig persona use p"}
    )))
    assert data["action"] == "activate"  # a switch in the caller's context, not here


async def test_acquire_block_returns_apply_never_executes(monkeypatch):
    _patch_acquire(monkeypatch)
    data = _data(await cat.handle_deck_bay_acquire(_AcqReq(
        {"slug": "b", "kind": "block", "install": "navig apply b"}
    )))
    assert data["action"] == "apply"  # blocks execute w/ inputs+approvals → open the flow


async def test_acquire_lens_returns_manual_command(monkeypatch):
    _patch_acquire(monkeypatch)
    data = _data(await cat.handle_deck_bay_acquire(_AcqReq(
        {"slug": "l", "kind": "lens", "install": "Enable in the Dock bar"}
    )))
    assert data["action"] == "manual" and data["command"] == "Enable in the Dock bar"


async def test_acquire_locked_priced_item_returns_402_and_never_installs(monkeypatch):
    priced = {
        "slug": "x", "kind": "space",
        "pricing": {"model": "buy_once", "includedInTier": "plus"},
        "install": "navig space install github:navig-run/community/spaces/x",
    }
    _patch_acquire(monkeypatch, item=priced, status=_FakeStatus())  # free tier → locked
    calls: list[str] = []
    import navig.commands.install as inst

    monkeypatch.setattr(inst, "install_asset", lambda spec, **kw: calls.append(spec))
    resp = await cat.handle_deck_bay_acquire(_AcqReq(
        {"slug": "x", "kind": "space", "install": priced["install"]}
    ))
    assert resp.status == 402
    payload = json.loads(resp.body.decode())
    assert payload["error"] == "capability_required"
    assert payload["tier_required"] == "plus"
    assert calls == []  # gate blocked install


async def test_acquire_owned_priced_item_installs(monkeypatch):
    priced = {
        "slug": "x", "kind": "space",
        "pricing": {"model": "buy_once", "includedInTier": "plus"},
        "install": "navig space install github:navig-run/community/spaces/x",
    }
    owner = _FakeStatus(capabilities=["core_ops", "item:x"])  # owns it forever
    _patch_acquire(monkeypatch, item=priced, status=owner)
    calls: list[str] = []
    import navig.commands.install as inst

    monkeypatch.setattr(inst, "install_asset", lambda spec, **kw: calls.append(spec))
    data = _data(await cat.handle_deck_bay_acquire(_AcqReq(
        {"slug": "x", "kind": "space", "install": priced["install"]}
    )))
    assert data["action"] == "install"
    assert calls == ["github:navig-run/community/spaces/x"]


async def test_acquire_requires_slug(monkeypatch):
    _patch_acquire(monkeypatch)
    resp = await cat.handle_deck_bay_acquire(_AcqReq({"kind": "space"}))
    assert resp.status == 400


# ── /bay/acquire — the no-restart contract (rearm discovery + push a refresh) ──
# A Bay install through this path must do what handle_deck_catalog_install does:
# forget the entry-point once-guard (so a fresh pip plugin appears on the next
# catalog GET, no daemon restart) AND emit modules_update (so live surfaces
# re-hydrate). Before this was wired, the desktop OS Bay — which calls
# /bay/acquire — installed a plugin that stayed invisible until a restart.

def _spy_rearm(monkeypatch) -> list[bool]:
    """Record calls to registry.reset_entry_points (what _rearm_entry_points runs)."""
    calls: list[bool] = []
    import navig.modules.registry as reg

    monkeypatch.setattr(reg, "reset_entry_points", lambda: calls.append(True))
    return calls


def _spy_emit(monkeypatch) -> list[dict]:
    """Record modules_update SSE emits from the acquire handler."""
    events: list[dict] = []

    async def _fake_emit(request, payload):
        events.append(payload)

    import navig.gateway.deck.routes.modules as mod

    monkeypatch.setattr(mod, "emit_modules_update", _fake_emit)
    return events


async def test_acquire_github_install_rearms_and_emits(monkeypatch):
    import navig.commands.install as inst

    monkeypatch.setattr(inst, "install_asset", lambda spec, **kw: None)
    _patch_acquire(monkeypatch)
    rearms = _spy_rearm(monkeypatch)
    events = _spy_emit(monkeypatch)

    data = _data(await cat.handle_deck_bay_acquire(_AcqReq(
        {"slug": "s", "kind": "space",
         "install": "navig space install github:navig-run/community/spaces/s"}
    )))
    assert data["action"] == "install"
    assert rearms == [True]                      # discovery rearmed → no daemon restart
    assert events and events[0]["slug"] == "s"   # live surfaces told to re-hydrate


async def test_acquire_plugin_install_rearms_and_emits(monkeypatch):
    class _FakeHost:
        def install(self, name):
            pass

    import navig.plugins.host as host

    monkeypatch.setattr(host, "PluginHost", _FakeHost)
    _patch_acquire(monkeypatch)
    rearms = _spy_rearm(monkeypatch)
    events = _spy_emit(monkeypatch)

    data = _data(await cat.handle_deck_bay_acquire(_AcqReq(
        {"slug": "telegram", "kind": "plugin", "install": "navig plugin install telegram"}
    )))
    assert data["action"] == "install"
    assert rearms == [True]                             # the fresh plugin appears next GET
    assert events and events[0]["slug"] == "telegram"


async def test_acquire_persona_neither_rearms_nor_emits(monkeypatch):
    """A non-install action (persona activate) installs nothing — it must not
    rearm discovery or push a modules refresh."""
    _patch_acquire(monkeypatch)
    rearms = _spy_rearm(monkeypatch)
    events = _spy_emit(monkeypatch)

    data = _data(await cat.handle_deck_bay_acquire(_AcqReq(
        {"slug": "p", "kind": "persona", "install": "navig persona use p"}
    )))
    assert data["action"] == "activate"
    assert rearms == [] and events == []


async def test_acquire_locked_neither_rearms_nor_emits(monkeypatch):
    priced = {
        "slug": "x", "kind": "space",
        "pricing": {"model": "buy_once", "includedInTier": "plus"},
        "install": "navig space install github:navig-run/community/spaces/x",
    }
    _patch_acquire(monkeypatch, item=priced, status=_FakeStatus())  # free tier → locked
    rearms = _spy_rearm(monkeypatch)
    events = _spy_emit(monkeypatch)

    resp = await cat.handle_deck_bay_acquire(_AcqReq(
        {"slug": "x", "kind": "space", "install": priced["install"]}
    ))
    assert resp.status == 402
    assert rearms == [] and events == []  # gate blocked → nothing installed, nothing refreshed


async def test_acquire_gates_the_spec_when_slug_lookup_misses(monkeypatch):
    """Regression: when _bay_item(slug) MISSES, acquire used to skip the entitlement gate and
    install whatever spec was passed — so a priced registry item installed via a mismatched slug
    bypassed the paywall the install door enforces. Now the spec is gated too."""
    _patch_acquire(monkeypatch, item=None)  # slug miss → the slug-based gate is skipped; free tier
    monkeypatch.setattr(
        cat, "_find_market_pricing",
        lambda spec: ("x", {"pricing_model": "buy_once", "included_in_tier": "plus"}),
    )
    calls: list[str] = []
    import navig.commands.install as inst

    monkeypatch.setattr(inst, "install_asset", lambda spec, **kw: calls.append(spec))

    resp = await cat.handle_deck_bay_acquire(_AcqReq(
        {"slug": "mismatch", "kind": "space",
         "install": "navig space install github:navig-run/community/spaces/x"}
    ))
    assert resp.status == 402
    payload = json.loads(resp.body.decode())
    assert payload["tier_required"] == "plus"
    assert calls == []  # gate blocked the install


async def test_acquire_foreign_spec_still_installs_on_slug_miss(monkeypatch):
    """A slug miss + a spec OUTSIDE our registry (arbitrary github/local) stays ungated by design —
    the gate must not block installs it can't price."""
    _patch_acquire(monkeypatch, item=None)
    monkeypatch.setattr(cat, "_find_market_pricing", lambda spec: None)  # foreign → ungated
    calls: list[str] = []
    import navig.commands.install as inst

    monkeypatch.setattr(inst, "install_asset", lambda spec, **kw: calls.append(spec))

    data = _data(await cat.handle_deck_bay_acquire(_AcqReq(
        {"slug": "mismatch", "kind": "space",
         "install": "navig space install github:someone/repo/spaces/x"}
    )))
    assert data["action"] == "install"
    assert calls == ["github:someone/repo/spaces/x"]
