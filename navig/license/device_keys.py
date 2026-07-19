"""Device-local Ed25519 signing for block execution receipts.

This is what turns a receipt from *tamper-evident* (digest + append-only journal)
into *tamper-evident **and** attributable*: every receipt is signed by a stable
per-device keypair, so a verifier can prove "this exact receipt was produced by
this device and has not been altered since."

Deliberately separate from license keys (`license/keys.py`): the founder key
signs entitlements and is never on a user's disk; the **device** key is generated
locally on first use and only ever signs receipts. Cross-party attestation
(registering a device pubkey to a NAVIG account so third parties trust it) is a
later network feature and does not change this on-disk format.

Signature covers the canonical JSON of the receipt with the three signature
fields blanked, so inserting the signature does not invalidate it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from navig.license.keys import _b64url_decode, _b64url_encode

_SIG_FIELDS = ("signature", "signer_pubkey", "signer_kind")


def _device_key_path() -> Path:
    from navig.platform.paths import config_dir

    return config_dir() / "keys" / "device.key"


def device_keypair() -> tuple[bytes, bytes]:
    """Return (private_bytes, public_bytes), generating + persisting on first use."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key_path = _device_key_path()
    if key_path.exists():
        raw = key_path.read_bytes()
        sk = Ed25519PrivateKey.from_private_bytes(raw)
    else:
        sk = Ed25519PrivateKey.generate()
        raw = sk.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(raw)
        try:
            key_path.chmod(0o600)  # best-effort on POSIX; no-op on Windows
        except OSError:
            pass
        # Also drop the public key beside it for convenience / export.
        pub = sk.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )
        (key_path.parent / "device.pub").write_text(_b64url_encode(pub), encoding="utf-8")
    pub_bytes = sk.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return raw, pub_bytes


def _signing_message(receipt_dict: dict[str, Any]) -> bytes:
    """Canonical bytes signed: the receipt with signature fields blanked."""
    snap = json.loads(json.dumps(receipt_dict))  # deep copy
    artifacts = snap.get("artifacts")
    if isinstance(artifacts, dict):
        for f in _SIG_FIELDS:
            artifacts[f] = None
    return json.dumps(snap, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_receipt_dict(receipt_dict: dict[str, Any]) -> dict[str, str]:
    """Sign a receipt dict; returns {signature, signer_pubkey, signer_kind}.

    The caller inserts these into ``receipt_dict['artifacts']``.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv, pub = device_keypair()
    sk = Ed25519PrivateKey.from_private_bytes(priv)
    sig = sk.sign(_signing_message(receipt_dict))
    return {
        "signature": _b64url_encode(sig),
        "signer_pubkey": _b64url_encode(pub),
        "signer_kind": "device",
    }


def sign_bytes(message: bytes) -> dict[str, str]:
    """Device-sign an arbitrary message (e.g. a block manifest digest)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv, pub = device_keypair()
    sk = Ed25519PrivateKey.from_private_bytes(priv)
    return {
        "signature": _b64url_encode(sk.sign(message)),
        "signer_pubkey": _b64url_encode(pub),
        "signer_kind": "device",
    }


def verify_bytes(message: bytes, signature: str, signer_pubkey: str) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        pk = Ed25519PublicKey.from_public_bytes(_b64url_decode(signer_pubkey))
        pk.verify(_b64url_decode(signature), message)
        return True
    except (InvalidSignature, Exception):  # noqa: BLE001
        return False


def verify_receipt_dict(receipt_dict: dict[str, Any]) -> tuple[bool | None, str]:
    """Verify a receipt's device signature.

    Returns (True, "ok") / (False, reason) / (None, "unsigned").
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    artifacts = receipt_dict.get("artifacts") or {}
    sig = artifacts.get("signature")
    pub = artifacts.get("signer_pubkey")
    if not sig:
        return None, "unsigned"
    if not pub:
        return False, "signature present but signer_pubkey missing"
    try:
        pk = Ed25519PublicKey.from_public_bytes(_b64url_decode(pub))
        pk.verify(_b64url_decode(sig), _signing_message(receipt_dict))
        return True, "ok"
    except InvalidSignature:
        return False, "signature does not match receipt contents (tampered)"
    except Exception as exc:  # noqa: BLE001
        return False, f"verify error: {exc}"
