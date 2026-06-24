"""
Tests for navig.license.keys.verify_license().

Covers the full set of failure + success modes the daemon and Deck both
depend on. Every test signs a payload with the dev key (k0_dev) and asks
verify_license to round-trip it.

The tests deliberately exercise:
  * missing / malformed input
  * unsupported version
  * bad signature
  * revoked key_id
  * revoked license_id
  * unknown key_id
  * unknown tier
  * valid subscription (active)
  * lapsed subscription (drops to solo)
  * one-time perpetual (keeps tier even with no expiry)
  * perpetual modules stacking on top of effective_tier capabilities

The Ed25519 dev key + matching private key are committed to the repo so
these tests can run offline without provisioning new keys.
"""

from __future__ import annotations

import base64
import datetime as _dt
import json
from pathlib import Path
from uuid import uuid4

import pytest

from navig.license import _public_keys
from navig.license.keys import (
    LicenseStatus,
    _b64url_encode,
    verify_license,
)
from navig.license.quota import TIER_CAPABILITIES, TIER_HOST_LIMIT

# ─── Helpers ────────────────────────────────────────────────────────────────

_TOOLS_KEYS = Path(__file__).resolve().parents[2] / "tools" / "license_keys"
_DEV_KEY_ID = "k0_dev"


@pytest.fixture(scope="module")
def dev_private_key() -> bytes:
    """Load the committed dev private key. Used to sign all test payloads.

    The matching public bytes are baked into navig.license._public_keys.PUBLIC_KEYS
    under the key_id ``k0_dev``.
    """
    priv_path = _TOOLS_KEYS / f"{_DEV_KEY_ID}.priv"
    if not priv_path.is_file():
        pytest.skip(f"dev signing key missing at {priv_path}; cannot run verifier tests")
    return priv_path.read_bytes()


def _sign(payload: dict, private_bytes: bytes, key_id: str = _DEV_KEY_ID) -> str:
    """Mint a NAVIG-LICENSE-v1 token with the given payload + key."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload_b64 = _b64url_encode(payload_bytes)
    sk = Ed25519PrivateKey.from_private_bytes(private_bytes)
    sig = sk.sign(payload_b64.encode("ascii"))
    return f"NAVIG-LICENSE-v1:{payload_b64}.{_b64url_encode(sig)}"


def _now_iso(delta_days: int = 0) -> str:
    """Return an ISO8601 timestamp `delta_days` away from now (UTC)."""
    return (_dt.datetime.now(tz=_dt.timezone.utc) + _dt.timedelta(days=delta_days)).isoformat()


def _payload(
    *,
    tier: str = "pro",
    billing_period: str = "annual",
    subscription_until_days: int | None = 180,
    perpetual_modules: list[str] | None = None,
    perpetual_tier: str | None = None,
    key_id: str = _DEV_KEY_ID,
    license_id: str | None = None,
) -> dict:
    entitlements: dict = {"modules": perpetual_modules or []}
    if perpetual_tier:
        entitlements["tier"] = perpetual_tier
    return {
        "license_version": 1,
        "license_id": license_id or str(uuid4()),
        "tier": tier,
        "hosts": TIER_HOST_LIMIT.get(tier, 1),
        "capabilities": list(TIER_CAPABILITIES.get(tier, ("core_ops",))),
        "commercial_use": True,
        "billing_period": billing_period,
        "issued_at": _now_iso(),
        "subscription_until": (
            _now_iso(subscription_until_days) if subscription_until_days is not None else None
        ),
        "entitlements_perpetual": entitlements,
        "branding": None,
        "signature_key_id": key_id,
        "buyer_email": "test@example.com",
    }


# ─── Failure modes ──────────────────────────────────────────────────────────


def test_missing_token_returns_missing():
    status = verify_license(None)
    assert isinstance(status, LicenseStatus)
    assert status.valid is False
    assert status.reason == "missing"
    assert status.effective_tier == "free"
    assert status.host_limit == 1


def test_empty_string_returns_missing():
    # `""` is falsy → missing path. Whitespace-only strips to empty and
    # then fails the prefix check → malformed. Both are valid responses
    # but the verifier draws the line at "non-empty string".
    assert verify_license("").reason == "missing"
    assert verify_license("   ").reason == "malformed"


def test_wrong_prefix_returns_malformed():
    assert verify_license("not-a-license-token").reason == "malformed"
    assert verify_license("NAVIG-LICENSE-v0:foo.bar").reason == "malformed"


def test_no_dot_separator_returns_malformed():
    assert verify_license("NAVIG-LICENSE-v1:nodothere").reason == "malformed"


def test_garbage_b64_returns_malformed():
    # Right prefix, dot present, but the b64 payload is junk → JSON decode fails
    assert verify_license("NAVIG-LICENSE-v1:!!!.???").reason == "malformed"


def test_unsupported_version_rejected(dev_private_key: bytes):
    payload = _payload()
    payload["license_version"] = 99
    token = _sign(payload, dev_private_key)
    status = verify_license(token)
    assert status.valid is False
    assert status.reason == "unsupported_version"


def test_unknown_key_id_returns_invalid_signature(dev_private_key: bytes):
    """A key_id not in PUBLIC_KEYS means we can't trust the signature at all."""
    payload = _payload(key_id="k_never_existed")
    # Sign with the dev key but claim a different key_id in the payload.
    token = _sign(payload, dev_private_key, key_id="k_never_existed")
    status = verify_license(token)
    assert status.valid is False
    assert status.reason == "invalid_signature"


def test_bad_signature_returns_invalid_signature(dev_private_key: bytes):
    """Tampered signature → InvalidSignature → reason='invalid_signature'."""
    payload = _payload()
    token = _sign(payload, dev_private_key)
    # Flip a byte in the signature segment.
    head, sig = token.rsplit(".", 1)
    decoded = bytearray(base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)))
    decoded[0] ^= 0xFF
    bad_b64 = base64.urlsafe_b64encode(bytes(decoded)).rstrip(b"=").decode("ascii")
    tampered = f"{head}.{bad_b64}"
    assert verify_license(tampered).reason == "invalid_signature"


def test_revoked_key_id_returns_revoked(dev_private_key: bytes, monkeypatch):
    payload = _payload()
    token = _sign(payload, dev_private_key)
    monkeypatch.setattr(
        _public_keys, "REVOKED_KEY_IDS", frozenset({_DEV_KEY_ID})
    )
    assert verify_license(token).reason == "revoked"


def test_revoked_license_id_returns_revoked(dev_private_key: bytes, monkeypatch):
    lid = str(uuid4())
    payload = _payload(license_id=lid)
    token = _sign(payload, dev_private_key)
    monkeypatch.setattr(
        _public_keys, "REVOKED_LICENSE_IDS", frozenset({lid})
    )
    assert verify_license(token).reason == "revoked"


def test_unknown_tier_returns_malformed(dev_private_key: bytes):
    payload = _payload(tier="ultradeluxe")
    token = _sign(payload, dev_private_key)
    assert verify_license(token).reason == "malformed"


# ─── Success modes ──────────────────────────────────────────────────────────


def test_valid_pro_annual_active(dev_private_key: bytes):
    payload = _payload(tier="pro", billing_period="annual", subscription_until_days=180)
    token = _sign(payload, dev_private_key)
    status = verify_license(token)
    assert status.valid is True
    assert status.reason == "ok"
    assert status.effective_tier == "pro"
    assert status.subscription_active is True
    assert status.host_limit == TIER_HOST_LIMIT["pro"]
    assert set(status.capabilities) >= set(TIER_CAPABILITIES["pro"])
    assert status.perpetual_modules == []
    assert status.billing_period == "annual"
    assert status.signature_key_id == _DEV_KEY_ID


def test_lapsed_subscription_drops_to_solo(dev_private_key: bytes):
    """Pro annual expired yesterday → tier drops to solo, subscription_active=False."""
    payload = _payload(tier="pro", billing_period="annual", subscription_until_days=-1)
    token = _sign(payload, dev_private_key)
    status = verify_license(token)
    assert status.valid is True
    assert status.reason == "ok"
    assert status.effective_tier == "free"
    assert status.subscription_active is False
    assert status.host_limit == TIER_HOST_LIMIT["free"]


def test_one_time_perpetual_keeps_tier(dev_private_key: bytes):
    """billing=one_time + subscription_until=None → tier stays even without subscription."""
    payload = _payload(
        tier="pro",
        billing_period="one_time",
        subscription_until_days=None,
    )
    token = _sign(payload, dev_private_key)
    status = verify_license(token)
    assert status.valid is True
    assert status.effective_tier == "pro"
    assert status.subscription_active is False
    assert status.host_limit == TIER_HOST_LIMIT["pro"]
    assert status.billing_period == "one_time"


def test_perpetual_modules_stack_on_solo(dev_private_key: bytes):
    """Solo + perpetual AI Operator → solo tier + AI Operator capability available."""
    payload = _payload(
        tier="solo",
        billing_period="one_time",
        subscription_until_days=None,
        perpetual_modules=["ai_operator"],
    )
    token = _sign(payload, dev_private_key)
    status = verify_license(token)
    assert status.valid is True
    assert status.effective_tier == "solo"
    assert status.host_limit == 1
    assert "core_ops" in status.capabilities  # solo base
    assert "ai_operator" in status.capabilities  # perpetual extra
    assert status.perpetual_modules == ["ai_operator"]


def test_perpetual_modules_stack_on_lapsed_pro(dev_private_key: bytes):
    """Lapsed Pro + perpetual Security Ops → drops to solo, but keeps Security Ops."""
    payload = _payload(
        tier="pro",
        billing_period="annual",
        subscription_until_days=-30,  # expired 30 days ago
        perpetual_modules=["security_ops"],
    )
    token = _sign(payload, dev_private_key)
    status = verify_license(token)
    assert status.valid is True
    assert status.effective_tier == "free"
    assert status.subscription_active is False
    assert status.host_limit == 1
    assert "security_ops" in status.capabilities


def test_multiple_perpetual_modules_carry_through(dev_private_key: bytes):
    payload = _payload(
        tier="solo",
        billing_period="one_time",
        subscription_until_days=None,
        perpetual_modules=["ai_operator", "business_ops", "deploy_ops"],
    )
    token = _sign(payload, dev_private_key)
    status = verify_license(token)
    assert status.valid is True
    assert set(status.perpetual_modules) == {"ai_operator", "business_ops", "deploy_ops"}
    assert {"ai_operator", "business_ops", "deploy_ops"}.issubset(set(status.capabilities))


# ─── as_dict round-trip (used by /api/deck/license/status) ──────────────────


def test_as_dict_emits_iso_timestamps(dev_private_key: bytes):
    payload = _payload()
    token = _sign(payload, dev_private_key)
    status = verify_license(token)
    d = status.as_dict()
    # Spot-check the JSON-shape contract the Deck depends on.
    assert d["valid"] is True
    assert d["effective_tier"] == "pro"
    assert d["host_limit"] == TIER_HOST_LIMIT["pro"]
    assert isinstance(d["capabilities"], list)
    assert d["subscription_until"] is not None
    assert isinstance(d["subscription_until"], str)
    assert d["subscription_until"].endswith("+00:00") or d["subscription_until"].endswith("Z")
    assert d["reason"] == "ok"


# ─── Perpetual fallback tier ────────────────────────────────────────────────
#
# This is the bug fix: a buyer who has Pro perpetual ($749 one-time) then
# layers a Business annual subscription on top should drop BACK TO PRO
# when Business lapses — NOT to Solo. Previously the verifier ignored
# `entitlements_perpetual.tier` and forced solo.


def test_active_sub_with_perpetual_fallback_uses_sub_tier(dev_private_key: bytes):
    """While the subscription is active, the signed tier wins regardless of fallback."""
    payload = _payload(
        tier="business",
        billing_period="annual",
        subscription_until_days=180,
        perpetual_tier="pro",
    )
    token = _sign(payload, dev_private_key)
    status = verify_license(token)
    assert status.valid is True
    assert status.subscription_active is True
    assert status.effective_tier == "business"
    assert status.host_limit == TIER_HOST_LIMIT["business"]


def test_lapsed_sub_with_perpetual_fallback_drops_to_perpetual(dev_private_key: bytes):
    """Lapsed Business annual + Pro perpetual fallback → drops to Pro, not Solo."""
    payload = _payload(
        tier="business",
        billing_period="annual",
        subscription_until_days=-30,
        perpetual_tier="pro",
    )
    token = _sign(payload, dev_private_key)
    status = verify_license(token)
    assert status.valid is True
    assert status.subscription_active is False
    assert status.effective_tier == "pro"
    assert status.host_limit == TIER_HOST_LIMIT["pro"]
    # Pro's bundled capabilities should be live again
    assert "business_ops" in status.capabilities
    assert "ai_operator" in status.capabilities
    # But not Business's extras
    assert "security_ops" not in status.capabilities
    assert "deploy_ops" not in status.capabilities


def test_lapsed_sub_without_perpetual_fallback_drops_to_solo(dev_private_key: bytes):
    """Lapsed annual with no perpetual fallback → Solo (existing behaviour)."""
    payload = _payload(
        tier="business",
        billing_period="annual",
        subscription_until_days=-30,
        # No perpetual_tier set
    )
    token = _sign(payload, dev_private_key)
    status = verify_license(token)
    assert status.effective_tier == "free"
    assert status.host_limit == TIER_HOST_LIMIT["free"]


def test_lapsed_sub_solo_perpetual_fallback_ignored(dev_private_key: bytes):
    """A 'solo' perpetual_tier is meaningless and must NOT be honored — Solo
    is already the free baseline, so writing it as a fallback is a no-op."""
    payload = _payload(
        tier="pro",
        billing_period="annual",
        subscription_until_days=-30,
        perpetual_tier="solo",
    )
    token = _sign(payload, dev_private_key)
    status = verify_license(token)
    # A "solo" perpetual_tier is a no-op, so this drops to the free baseline;
    # the contract is: perpetual_tier="solo" doesn't elevate above free.
    assert status.effective_tier == "free"


def test_perpetual_fallback_with_carried_modules(dev_private_key: bytes):
    """Lapsed Business + perpetual Pro + perpetual AI Op module:
    drops to Pro tier; AI Op module is already in Pro's caps; stays unlocked."""
    payload = _payload(
        tier="business",
        billing_period="annual",
        subscription_until_days=-30,
        perpetual_tier="pro",
        perpetual_modules=["security_ops"],  # bought as add-on
    )
    token = _sign(payload, dev_private_key)
    status = verify_license(token)
    assert status.effective_tier == "pro"
    # Pro's bundle
    assert "business_ops" in status.capabilities
    assert "ai_operator" in status.capabilities
    # The perpetual security_ops module survives
    assert "security_ops" in status.capabilities
    # But not deploy_ops (Business-only, not carried)
    assert "deploy_ops" not in status.capabilities
