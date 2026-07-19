"""Regression: the Store's Bay-app collector (`hub/aggregator._bay_apps`) gates priced
webapp/app entries correctly.

It used to read a flat `entry.get("price_cents")` — a field the Bay catalog schema never
carries (pricing is a nested `{model, priceUsd, includedInTier}` block) — so `cap` was always
None and EVERY Bay app resolved as free/unlocked. The gate was dead. It now shares the one
canonical rule (`navig.license.entitlement.is_entry_unlocked`) with the Bay catalog + the SDK.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


class _Status:
    def __init__(self, tier: str = "free", caps=()):
        self.effective_tier = tier
        self.capabilities = list(caps)


_ENTRIES = [
    {"kind": "webapp", "slug": "free-app", "name": "Free App", "pricing": {"model": "free"}},
    {"kind": "webapp", "slug": "paid-app", "name": "Paid App",
     "pricing": {"model": "buy_once", "priceUsd": 19}},
    {"kind": "app", "slug": "tier-app", "name": "Tier App",
     "pricing": {"model": "buy_once", "priceUsd": 9, "includedInTier": "plus"}},
]


def _collect(monkeypatch, status: _Status) -> dict:
    import navig.hub.bay as bay
    import navig.license as lic

    monkeypatch.setattr(bay, "fetch_bay_catalog", lambda *, refresh=False: list(_ENTRIES))
    monkeypatch.setattr(lic, "current_status", lambda: status)
    from navig.hub.aggregator import _bay_apps

    return {i.id: i for i in _bay_apps()}


def test_free_tier_locks_priced_bay_apps(monkeypatch):
    items = _collect(monkeypatch, _Status(tier="free"))
    assert items["webapp:Free App"].locked is False            # free → open
    assert items["webapp:Paid App"].locked is True             # buy_once, not owned → locked
    assert items["app:Tier App"].locked is True                # plus-included, free tier → locked
    # The dead-gate bug made ALL of these locked=False.
    assert items["webapp:Paid App"].actions == ["unlock"]
    assert items["webapp:Free App"].actions == ["open"]


def test_covering_tier_unlocks_included_bay_app(monkeypatch):
    items = _collect(monkeypatch, _Status(tier="plus"))
    assert items["app:Tier App"].locked is False               # plus covers includedInTier=plus
    assert items["webapp:Paid App"].locked is True             # buy_once w/o tier inclusion → still locked


def test_owned_item_unlocks_bay_app(monkeypatch):
    items = _collect(monkeypatch, _Status(tier="free", caps=["item:paid-app"]))
    assert items["webapp:Paid App"].locked is False            # owned forever via item:<slug>


def test_price_cents_derived_from_nested_pricing(monkeypatch):
    items = _collect(monkeypatch, _Status(tier="free"))
    assert items["webapp:Paid App"].detail["price_cents"] == 1900   # priceUsd 19 → cents
    assert items["webapp:Free App"].detail["price_cents"] is None
