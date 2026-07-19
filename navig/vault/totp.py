"""RFC 6238 TOTP — stdlib-only (no dependency).

Generates the same 6-digit codes as Google Authenticator / Authy from a base32
secret, so a login that stores ``totp_secret`` can clear its own 2FA step during
auto-login. Pure stdlib (hmac + hashlib) — deliberately no ``pyotp`` dependency.

Security note: storing the TOTP seed next to the password collapses 2FA into a
single factor. This is opt-in per login and clearly surfaced; see the auto-login
threat model.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time

__all__ = ["totp_now", "is_valid_secret"]


def _decode_secret(secret_b32: str) -> bytes:
    s = (secret_b32 or "").strip().replace(" ", "").replace("-", "").upper()
    if not s:
        raise ValueError("empty TOTP secret")
    s += "=" * (-len(s) % 8)  # pad to a multiple of 8 for base32
    return base64.b32decode(s, casefold=True)


def is_valid_secret(secret_b32: str) -> bool:
    """True if *secret_b32* decodes as base32 (a usable TOTP seed)."""
    try:
        _decode_secret(secret_b32)
        return True
    except Exception:  # noqa: BLE001
        return False


def totp_now(
    secret_b32: str,
    *,
    digits: int = 6,
    period: int = 30,
    algorithm: str = "sha1",
    t: float | None = None,
) -> str:
    """Return the current TOTP code for a base32 *secret_b32*.

    Args:
        digits: code length (6 is standard; 8 for some enterprise setups).
        period: time step in seconds (30 is standard).
        algorithm: HMAC hash — "sha1" (default), "sha256", or "sha512".
        t: Unix time override (for testing / RFC vectors); defaults to now.
    """
    key = _decode_secret(secret_b32)
    counter = int((time.time() if t is None else t) // period)
    msg = struct.pack(">Q", counter)
    digestmod = getattr(hashlib, algorithm.lower())
    digest = hmac.new(key, msg, digestmod).digest()
    offset = digest[-1] & 0x0F
    code_int = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code_int % (10 ** digits)).zfill(digits)
