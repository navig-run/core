"""
NAVIG license token verification.

Token format
------------
    NAVIG-LICENSE-v1:<base64url(payload_json)>.<base64url(signature)>

The payload is a JSON object with the schema documented in
navig.license.schema. The signature is computed over the base64url-encoded
payload bytes (NOT the parsed JSON) using the founder's Ed25519 private
key, so it's deterministic and stable regardless of dict ordering.

Verification
------------
Verification is **fully offline** -- no phone-home, no network. The Deck
and daemon both bundle the public key set in ``_public_keys.py`` and run
this same verifier on the token they read from ``~/.navig/license.key``.

The verifier returns a structured ``LicenseStatus`` describing the
effective entitlement (tier, host limit, capabilities, subscription
status, perpetual modules) plus a ``reason`` enum explaining any failure
mode for the UI.
"""

from __future__ import annotations

import base64
import datetime as _dt
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "navig.license requires the `cryptography` package "
        "(it should already be installed via navig-core's requirements.txt)."
    ) from exc

from navig.license._public_keys import (
    get_public_key,
    is_key_revoked,
    is_license_revoked,
)
from navig.license.quota import (
    TIER_CAPABILITIES,
    TIER_HOST_LIMIT,
    TierName,
)

logger = logging.getLogger(__name__)

_TOKEN_PREFIX = "NAVIG-LICENSE-v1:"
_TOKEN_VERSION = 1

Reason = Literal[
    "ok",
    "missing",
    "malformed",
    "invalid_signature",
    "unsupported_version",
    "revoked",
    "over_host_limit_soft_cap",
]


@dataclass
class LicenseStatus:
    """Structured result of verifying a license token."""

    valid: bool
    effective_tier: TierName = "solo"
    host_limit: int = 1
    capabilities: list[str] = field(default_factory=lambda: ["core_ops"])
    subscription_until: _dt.datetime | None = None
    subscription_active: bool = False
    perpetual_modules: list[str] = field(default_factory=list)
    reason: Reason = "missing"

    # Echoed straight from the payload for display (truncated key, etc.)
    license_id: str | None = None
    issued_at: _dt.datetime | None = None
    billing_period: str | None = None
    branding: dict[str, Any] | None = None
    signature_key_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """JSON-serialisable representation (for the /api/deck/license/status endpoint)."""
        return {
            "valid": self.valid,
            "effective_tier": self.effective_tier,
            "host_limit": self.host_limit,
            "capabilities": list(self.capabilities),
            "subscription_until": (
                self.subscription_until.isoformat() if self.subscription_until else None
            ),
            "subscription_active": self.subscription_active,
            "perpetual_modules": list(self.perpetual_modules),
            "reason": self.reason,
            "license_id": self.license_id,
            "issued_at": self.issued_at.isoformat() if self.issued_at else None,
            "billing_period": self.billing_period,
            "branding": self.branding,
            "signature_key_id": self.signature_key_id,
        }


# ─── Base64url helpers ──────────────────────────────────────────────────────

def _b64url_decode(s: str) -> bytes:
    # urlsafe_b64decode requires correct padding; add it if missing.
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


# ─── Token parse + verify ───────────────────────────────────────────────────

def _free_status(reason: Reason = "missing") -> LicenseStatus:
    """Default Solo (Free) tier status returned when no valid license exists."""
    return LicenseStatus(
        valid=False,
        effective_tier="solo",
        host_limit=TIER_HOST_LIMIT["solo"],
        capabilities=list(TIER_CAPABILITIES["solo"]),
        subscription_active=False,
        reason=reason,
    )


def verify_license(token: str | None) -> LicenseStatus:
    """Parse + cryptographically verify a license token.

    Returns a ``LicenseStatus`` describing the effective entitlement.
    On any error (missing, malformed, bad signature, revoked, etc.) returns
    a Solo (Free) tier status with the appropriate ``reason`` so callers can
    UI the failure without crashing.

    This function is **pure** -- no I/O, no network, no clock side-effects
    beyond ``datetime.now()`` for subscription expiry. Safe to call on every
    request.
    """
    if not token or not isinstance(token, str):
        return _free_status("missing")

    token = token.strip()
    if not token.startswith(_TOKEN_PREFIX):
        return _free_status("malformed")

    body = token[len(_TOKEN_PREFIX):]
    if body.count(".") != 1:
        return _free_status("malformed")
    payload_b64, signature_b64 = body.split(".", 1)
    if not payload_b64 or not signature_b64:
        return _free_status("malformed")

    # Decode signature + payload
    try:
        payload_bytes = _b64url_decode(payload_b64)
        signature = _b64url_decode(signature_b64)
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("license token decode failed: %r", exc)
        return _free_status("malformed")

    if not isinstance(payload, dict):
        return _free_status("malformed")

    # Version gate
    version = payload.get("license_version")
    if version != _TOKEN_VERSION:
        return _free_status("unsupported_version")

    # Find the matching public key
    key_id = str(payload.get("signature_key_id") or "")
    if not key_id or is_key_revoked(key_id):
        return _free_status("revoked")
    pk_bytes = get_public_key(key_id)
    if pk_bytes is None:
        # Unknown key id -> can't trust this signature.
        return _free_status("invalid_signature")

    # Per-license revocation (key still trusted, but this license is bad)
    license_id = str(payload.get("license_id") or "")
    if license_id and is_license_revoked(license_id):
        return _free_status("revoked")

    # Cryptographic verify -- signature is over the base64url payload string.
    try:
        public_key = Ed25519PublicKey.from_public_bytes(pk_bytes)
        public_key.verify(signature, payload_b64.encode("ascii"))
    except InvalidSignature:
        return _free_status("invalid_signature")
    except Exception as exc:  # noqa: BLE001
        logger.debug("license verify raised %r", exc)
        return _free_status("invalid_signature")

    # ── Signature valid -- assemble the entitlement ─────────────────────────

    # Tier + subscription
    raw_tier = str(payload.get("tier") or "solo").lower()
    if raw_tier not in TIER_HOST_LIMIT:
        # Unknown tier name in a signed payload -> distrust the whole token.
        return _free_status("malformed")
    tier: TierName = raw_tier  # type: ignore[assignment]

    subscription_until = _parse_iso(payload.get("subscription_until"))
    now = _dt.datetime.now(tz=_dt.timezone.utc)
    subscription_active = (
        subscription_until is not None and subscription_until > now
    )

    # Perpetual entitlements -- modules the user owns forever regardless of
    # subscription state.
    perpetual = payload.get("entitlements_perpetual") or {}
    perpetual_modules = [
        str(m).lower() for m in (perpetual.get("modules") or [])
        if isinstance(m, str)
    ]
    # Perpetual fallback tier — set when the buyer has a one-time tier
    # purchase that should survive subscription lapses. Example: bought
    # Pro perpetual ($749), then later took Business annual on top. When
    # Business lapses we want them dropping back to Pro perpetual, NOT
    # all the way down to Solo. The webhook pipeline carries the prior
    # one-time tier into this field when issuing a new subscription token
    # for an existing perpetual buyer.
    raw_fallback = str(perpetual.get("tier") or "").lower()
    perpetual_tier: TierName | None = (
        raw_fallback if raw_fallback in TIER_HOST_LIMIT and raw_fallback != "solo"
        else None  # type: ignore[assignment]
    )

    # Effective tier resolution:
    #   1. Active subscription      → use signed tier
    #   2. Signed one-time purchase → use signed tier (no expiry to lapse)
    #   3. Lapsed sub with perpetual_tier → fall back to perpetual_tier
    #   4. Otherwise                → drop to Solo
    if subscription_active or _is_one_time_tier(payload):
        effective_tier: TierName = tier
    elif perpetual_tier is not None:
        effective_tier = perpetual_tier  # type: ignore[assignment]
    else:
        effective_tier = "solo"

    base_caps = list(TIER_CAPABILITIES[effective_tier])
    capabilities = base_caps[:]
    for m in perpetual_modules:
        if m not in capabilities:
            capabilities.append(m)

    return LicenseStatus(
        valid=True,
        effective_tier=effective_tier,
        host_limit=TIER_HOST_LIMIT[effective_tier],
        capabilities=capabilities,
        subscription_until=subscription_until,
        subscription_active=subscription_active,
        perpetual_modules=perpetual_modules,
        reason="ok",
        license_id=license_id or None,
        issued_at=_parse_iso(payload.get("issued_at")),
        billing_period=str(payload.get("billing_period") or "") or None,
        branding=payload.get("branding") if isinstance(payload.get("branding"), dict) else None,
        signature_key_id=key_id,
    )


def _is_one_time_tier(payload: dict[str, Any]) -> bool:
    """One-time tier purchases have no expiry and grant their tier forever.

    Signaled by billing_period="one_time" with subscription_until=null.
    """
    return (
        str(payload.get("billing_period") or "").lower() == "one_time"
        and payload.get("subscription_until") in (None, "", "null")
    )


def _parse_iso(value: Any) -> _dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        # Accept trailing "Z" by converting to "+00:00".
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = _dt.datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# ─── Sign side (founder's tool only; never invoked from the daemon) ─────────

def sign_payload(payload: dict[str, Any], private_key_bytes: bytes, key_id: str) -> str:
    """Build a NAVIG-LICENSE-v1 token. ONLY used by tools/license_sign.py.

    The daemon and Deck never call this -- they only verify. Kept here so
    the sign + verify pair are in one place for easy auditing during
    development, but the runtime never exercises this path.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    payload = dict(payload)  # don't mutate caller's dict
    payload["license_version"] = _TOKEN_VERSION
    payload["signature_key_id"] = key_id

    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload_b64 = _b64url_encode(payload_bytes)

    sk = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
    sig = sk.sign(payload_b64.encode("ascii"))
    sig_b64 = _b64url_encode(sig)

    return f"{_TOKEN_PREFIX}{payload_b64}.{sig_b64}"
