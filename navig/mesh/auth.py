"""
BLAKE2b packet authentication for the Flux mesh.

All mesh peers that share a ``mesh.secret`` config value (or set the
``NAVIG_MESH_SECRET`` environment variable) authenticate every UDP packet
with a BLAKE2b-256 keyed-hash MAC.

When a secret is configured:
  • Outgoing packets include an ``hmac`` field — a 64-char hex BLAKE2b-256
    keyed-hash over the canonical payload (sorted JSON, hmac field excluded).
  • Incoming packets without a valid HMAC are silently dropped.
  • Packets carrying a wrong HMAC (different secret) are silently dropped.

When no secret is configured (default / Phase 1 LAN-only mode):
  • Packets are sent unsigned and all signed/unsigned packets are accepted.
  • This preserves backward-compatibility with Phase 1 deployments.

Why BLAKE2b keyed hashing instead of HMAC-SHA256?
  • BLAKE2b natively supports a ``key`` parameter that turns it into a MAC.
  • No external dependencies — hashlib is stdlib.
  • BLAKE2b is faster than SHA-256 on 64-bit CPUs.
  • Immune to length-extension attacks by design.

BLAKE2b key length is limited to 64 bytes; secrets longer than 64 bytes are
truncated to their first 64 bytes.

Replay protection:
  The MAC binds to the full payload including the ``seq`` and ``ts`` fields.
  The receiver already tracks per-sender sequence numbers (see discovery.py),
  so stale replays are identified by a non-advancing seq counter.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import os
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────────────

_ENV_KEY = "NAVIG_MESH_SECRET"
HMAC_FIELD = "hmac"  # JSON field name for the MAC tag
_DIGEST_SIZE = 32  # bytes → 64 hex chars (BLAKE2b-256)
_MAX_KEY_LEN = 64  # BLAKE2b key limit in bytes


# ── Public API ────────────────────────────────────────────────────────────────


def load_secret(config_secret: Optional[str] = None) -> Optional[bytes]:
    """Return the mesh shared secret as bytes, or ``None`` if not configured.

    Resolution order (first non-empty value wins):

    1. ``config_secret`` argument — comes from ``mesh.secret`` in the NAVIG
       config file (passed by the caller after reading the config).
    2. ``NAVIG_MESH_SECRET`` environment variable.

    The returned bytes are at most 64 bytes long (BLAKE2b key limit).
    """
    raw: str = config_secret or os.environ.get(_ENV_KEY, "")
    if not raw:
        return None
    encoded = raw.encode() if isinstance(raw, str) else raw
    return encoded[:_MAX_KEY_LEN]


def sign_payload(payload: dict, secret: bytes) -> str:
    """Return a 64-char hex BLAKE2b-256 keyed-hash over the canonical payload.

    The *canonical* form is the JSON-serialised dict with:
    - keys sorted alphabetically, and
    - the ``hmac`` field excluded.

    This guarantees a stable signature regardless of Python dict insertion
    order and allows the receiver to strip the ``hmac`` field before
    re-deriving the expected tag.
    """
    canonical = _canonical_bytes(payload)
    key = secret[:_MAX_KEY_LEN]
    digest = hashlib.blake2b(canonical, key=key, digest_size=_DIGEST_SIZE)
    return digest.hexdigest()


def verify_payload(payload: dict, secret: bytes) -> bool:
    """Return ``True`` iff ``payload`` carries a valid HMAC for ``secret``.

    Uses :func:`hmac.compare_digest` to prevent timing side-channels.
    Returns ``False`` if the ``hmac`` field is absent or any error occurs.
    """
    try:
        received: str = payload.get(HMAC_FIELD, "")
        if not received:
            return False
        clean = {k: v for k, v in payload.items() if k != HMAC_FIELD}
        expected = sign_payload(clean, secret)
        return _hmac.compare_digest(expected, received)
    except Exception:
        return False


def attach_hmac(payload: dict, secret: bytes) -> dict:
    """Return a *new* dict that is ``payload`` plus the ``hmac`` field.

    Any existing ``hmac`` field in ``payload`` is stripped before signing so
    that calling this function is idempotent.
    """
    clean = {k: v for k, v in payload.items() if k != HMAC_FIELD}
    clean[HMAC_FIELD] = sign_payload(clean, secret)
    return clean


# ── Internal helpers ──────────────────────────────────────────────────────────


def _canonical_bytes(payload: dict) -> bytes:
    """Return the stable canonical JSON representation of payload.

    Excludes the ``hmac`` field and sorts keys so the output is deterministic.
    """
    clean = {k: v for k, v in sorted(payload.items()) if k != HMAC_FIELD}
    return json.dumps(clean, separators=(",", ":"), sort_keys=True).encode()
