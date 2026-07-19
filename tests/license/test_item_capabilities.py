"""Harbor Bay `item:<id>` grants — the Game-Pass entitlement invariants.

Item grants ride ``entitlements_perpetual.modules`` (the perpetual_modules
seam): the verifier appends them into ``capabilities`` verbatim (lowercased),
so ``@requires_capability("item:<id>")`` gates work with zero verifier changes.
These tests lock the three behaviors the whole commerce model depends on:

  1. an ``item:`` grant lands in capabilities;
  2. a bought item SURVIVES a lapsed subscription (yours forever);
  3. tier capabilities DROP on lapse (subscription items re-lock).
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from uuid import uuid4

import pytest

from navig.license.keys import sign_payload, verify_license
from navig.license.quota import TIER_CAPABILITIES, TIER_HOST_LIMIT

_TOOLS_KEYS = Path(__file__).resolve().parents[2] / "tools" / "license_keys"
_DEV_KEY_ID = "k0_dev"


@pytest.fixture()
def dev_private_key(monkeypatch) -> bytes:
    """The committed dev key when present; else an ephemeral keypair whose
    public half is monkeypatched into the verifier — so these invariants run
    everywhere, not just on machines with tools/license_keys checked out."""
    priv_path = _TOOLS_KEYS / f"{_DEV_KEY_ID}.priv"
    if priv_path.is_file():
        return priv_path.read_bytes()

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )

    sk = Ed25519PrivateKey.generate()
    priv = sk.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    pub = sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    import navig.license.keys as keys_mod

    orig = keys_mod.get_public_key
    monkeypatch.setattr(
        keys_mod, "get_public_key",
        lambda kid: pub if kid == _DEV_KEY_ID else orig(kid),
    )
    return priv


def _now_iso(offset_days: int = 0) -> str:
    return (
        _dt.datetime.now(tz=_dt.timezone.utc) + _dt.timedelta(days=offset_days)
    ).isoformat()


def _payload(
    *,
    tier: str = "plus",
    subscription_until_days: int | None = 180,
    perpetual_modules: list[str] | None = None,
) -> dict:
    return {
        "license_version": 1,
        "license_id": str(uuid4()),
        "tier": tier,
        "hosts": TIER_HOST_LIMIT.get(tier, 1),
        "capabilities": list(TIER_CAPABILITIES.get(tier, ("core_ops",))),
        "commercial_use": True,
        "billing_period": "annual",
        "issued_at": _now_iso(),
        "subscription_until": (
            _now_iso(subscription_until_days)
            if subscription_until_days is not None
            else None
        ),
        "entitlements_perpetual": {"modules": perpetual_modules or []},
        "fallback_major": "v1",
    }


def test_item_grant_lands_in_capabilities(dev_private_key: bytes):
    token = sign_payload(
        _payload(perpetual_modules=["item:security-audit"]),
        dev_private_key,
        _DEV_KEY_ID,
    )
    status = verify_license(token)
    assert status.valid
    assert "item:security-audit" in status.capabilities
    assert "item:security-audit" in status.perpetual_modules


def test_item_grant_survives_lapsed_subscription(dev_private_key: bytes):
    """The 'yours forever' promise: bought items outlive the subscription."""
    token = sign_payload(
        _payload(subscription_until_days=-10, perpetual_modules=["item:company-space"]),
        dev_private_key,
        _DEV_KEY_ID,
    )
    status = verify_license(token)
    assert status.valid
    # Subscription lapsed → tier drops to free…
    assert status.effective_tier == "free"
    assert not status.subscription_active
    # …but the bought item is still a capability.
    assert "item:company-space" in status.capabilities


def test_tier_capabilities_drop_on_lapse(dev_private_key: bytes):
    """Subscription-included ('moored') items re-lock when the Harbor lapses."""
    active = verify_license(
        sign_payload(_payload(subscription_until_days=180), dev_private_key, _DEV_KEY_ID)
    )
    lapsed = verify_license(
        sign_payload(_payload(subscription_until_days=-10), dev_private_key, _DEV_KEY_ID)
    )
    plus_only = set(active.capabilities) - set(lapsed.capabilities)
    assert plus_only, "lapse must remove tier capabilities"
    assert lapsed.effective_tier == "free"


def test_item_grants_are_lowercased(dev_private_key: bytes):
    """keys.py lowercases perpetual entries — catalog ids must be lowercase."""
    token = sign_payload(
        _payload(perpetual_modules=["ITEM:Notion"]), dev_private_key, _DEV_KEY_ID
    )
    status = verify_license(token)
    assert "item:notion" in status.capabilities
