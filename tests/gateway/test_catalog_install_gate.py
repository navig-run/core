"""Harbor Bay install gate — locked priced items 402, free/owned/foreign pass.

Covers `handle_deck_catalog_install`'s entitlement check and the
`_pricing_fields` unlock derivation in
core/navig/gateway/deck/routes/catalog.py.
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


class _FakeRequest:
    def __init__(self, body: dict):
        self._body = body

    async def json(self):
        return self._body


def _community(tmp_path, marketplace: dict | None):
    """Build a minimal community root with one space entry."""
    (tmp_path / "spaces").mkdir(parents=True)
    entry: dict = {"id": "homelab-space", "name": "Homelab", "status": "active"}
    if marketplace is not None:
        entry["marketplace"] = marketplace
    (tmp_path / "spaces" / "registry.json").write_text(
        json.dumps({"spaces": [entry]}), encoding="utf-8"
    )
    return tmp_path


def _patch(monkeypatch, tmp_path, *, marketplace, status: _FakeStatus, installed=None):
    _community(tmp_path, marketplace)
    monkeypatch.setattr(cat, "_community_root", lambda: tmp_path)
    import navig.license as lic

    monkeypatch.setattr(lic, "current_status", lambda: status)
    calls: list[str] = installed if installed is not None else []

    import navig.commands.install as inst

    monkeypatch.setattr(inst, "install_asset", lambda spec, **kw: calls.append(spec))
    return calls


SPEC = "github:navig-run/community/spaces/homelab-space"
PRICED = {"pricing_model": "buy_once", "included_in_tier": "plus", "price_cents": 1900}


async def test_locked_priced_item_returns_402(monkeypatch, tmp_path):
    calls = _patch(monkeypatch, tmp_path, marketplace=PRICED, status=_FakeStatus())
    resp = await cat.handle_deck_catalog_install(_FakeRequest({"spec": SPEC}))
    assert resp.status == 402
    payload = json.loads(resp.body.decode())
    assert payload["error"] == "capability_required"
    assert payload["capability"] == "item:homelab-space"
    assert payload["tier_required"] == "plus"
    assert payload["price_cents"] == 1900
    assert "checkout_url" in payload
    assert calls == []  # installer never ran


async def test_owned_item_installs(monkeypatch, tmp_path):
    status = _FakeStatus(capabilities=["core_ops", "item:homelab-space"])
    calls = _patch(monkeypatch, tmp_path, marketplace=PRICED, status=status)
    resp = await cat.handle_deck_catalog_install(_FakeRequest({"spec": SPEC}))
    assert resp.status == 200
    assert calls == [SPEC]


async def test_subscribed_tier_unlocks_item(monkeypatch, tmp_path):
    status = _FakeStatus(effective_tier="plus", capabilities=["core_ops", "business_ops"])
    calls = _patch(monkeypatch, tmp_path, marketplace=PRICED, status=status)
    resp = await cat.handle_deck_catalog_install(_FakeRequest({"spec": SPEC}))
    assert resp.status == 200
    assert calls == [SPEC]


async def test_free_item_installs_without_license(monkeypatch, tmp_path):
    calls = _patch(monkeypatch, tmp_path, marketplace=None, status=_FakeStatus())
    resp = await cat.handle_deck_catalog_install(_FakeRequest({"spec": SPEC}))
    assert resp.status == 200
    assert calls == [SPEC]


async def test_foreign_spec_bypasses_gate(monkeypatch, tmp_path):
    """Arbitrary github specs (not our registry) install ungated."""
    calls = _patch(monkeypatch, tmp_path, marketplace=PRICED, status=_FakeStatus())
    foreign = "github:someone/else/spaces/homelab-space"
    resp = await cat.handle_deck_catalog_install(_FakeRequest({"spec": foreign}))
    assert resp.status == 200
    assert calls == [foreign]


def test_pricing_fields_unlock_matrix(monkeypatch):
    import navig.license as lic

    # Locked for free tier.
    monkeypatch.setattr(lic, "current_status", lambda: _FakeStatus())
    fields = cat._pricing_fields("homelab-space", PRICED)
    assert fields["pricing_model"] == "buy_once"
    assert fields["unlocked"] is False
    # Max tier outranks plus → unlocked.
    monkeypatch.setattr(lic, "current_status", lambda: _FakeStatus(effective_tier="max"))
    assert cat._pricing_fields("homelab-space", PRICED)["unlocked"] is True
    # Absent block → free, unlocked.
    assert cat._pricing_fields("x", {})["pricing_model"] == "free"
    assert cat._pricing_fields("x", {})["unlocked"] is True


# ── the no-restart contract on /catalog/install (rearm discovery + push SSE) ──
# handle_deck_catalog_install must, on a SUCCESSFUL install, rearm entry-point
# discovery (fresh pip plugin appears next GET) AND emit modules_update — and do
# NEITHER when the gate blocks the install. This is the same contract /bay/acquire
# was missing; both now route through notify_modules_changed.

def _spy_contract(monkeypatch):
    """Spy the two no-restart-contract primitives (rearm + SSE emit)."""
    rearms: list[bool] = []
    events: list[dict] = []
    import navig.modules.registry as reg

    monkeypatch.setattr(reg, "reset_entry_points", lambda: rearms.append(True))

    async def _fake_emit(request, payload):
        events.append(payload)

    import navig.gateway.deck.routes.modules as mod

    monkeypatch.setattr(mod, "emit_modules_update", _fake_emit)
    return rearms, events


async def test_successful_install_fires_no_restart_contract(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path, marketplace=None, status=_FakeStatus())  # free → installs
    rearms, events = _spy_contract(monkeypatch)
    resp = await cat.handle_deck_catalog_install(_FakeRequest({"spec": SPEC}))
    assert resp.status == 200
    assert rearms == [True]                                   # rearmed → no daemon restart
    assert events and events[0] == {"kind": "install", "spec": SPEC}


async def test_locked_install_fires_no_contract(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path, marketplace=PRICED, status=_FakeStatus())  # free tier → 402
    rearms, events = _spy_contract(monkeypatch)
    resp = await cat.handle_deck_catalog_install(_FakeRequest({"spec": SPEC}))
    assert resp.status == 402
    assert rearms == [] and events == []                      # gate blocked → nothing refreshed
