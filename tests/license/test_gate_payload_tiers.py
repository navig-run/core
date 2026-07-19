"""The 402 capability payload must point users at LIVE Harbor tiers.

Regression for the drift bug where ``gate._SMALLEST_TIER_FOR_MODULE`` mapped
modules onto retired legacy tier names (``solo/pro/business/fleet``). The Deck
renders ``tier_required`` directly into an "Upgrade to <tier>" CTA — so a legacy
name sends the user to a tier that no longer exists in checkout. These tests
lock the payload to the live tiers sold today (free/plus/max/team/enterprise).
"""

from __future__ import annotations

from navig.license.gate import _SMALLEST_TIER_FOR_MODULE, capability_payload

# The tiers actually sold + enforced today (quota.py `live`); legacy names
# (solo/personal/pro/business/fleet) are recognized for back-compat but must
# NEVER surface in an upgrade CTA.
_LIVE_HARBOR_TIERS = {"free", "plus", "max", "team", "enterprise"}


def test_module_upgrade_hints_are_live_harbor_tiers():
    for module, tier in _SMALLEST_TIER_FOR_MODULE.items():
        assert tier in _LIVE_HARBOR_TIERS, (
            f"{module} → {tier!r} is a retired/legacy tier; the 402 CTA would "
            f"point at a tier not sold in checkout"
        )


def test_business_ops_points_at_plus_not_pro():
    payload = capability_payload("business_ops", "free")
    assert payload["tier_required"] == "plus"  # was the retired "pro"
    assert payload["error"] == "capability_required"


def test_client_ops_starts_at_team():
    assert capability_payload("client_ops", "free")["tier_required"] == "team"


def test_unknown_module_falls_back_to_a_live_tier():
    # An unmapped capability must still yield a sellable tier, never "pro".
    assert capability_payload("some_future_module", "free")["tier_required"] in _LIVE_HARBOR_TIERS


def test_item_grant_has_no_tier_requirement_but_a_checkout_url():
    payload = capability_payload("item:company-space", "free")
    assert payload["tier_required"] is None
    assert payload["checkout_url"].endswith("/api/checkout?item=company-space")
