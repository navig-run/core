"""
navig.identity.seed — Deterministic per-machine seed.

The seed is derived from stable hardware/OS attributes that survive reinstalls
as long as the user's home directory (and MAC address) remain the same.

Fallback chain:
  1. Stable: MAC + hostname + username + OS platform  (primary)
  2. Partial: whatever attributes are available       (degraded but stable)
  3. Random:  uuid4                                   (containerized environments)
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import uuid

logger = logging.getLogger(__name__)


def generate_seed() -> str:
    """
    Return a stable 64-char hex seed for this machine/user combination.

    The seed is deterministic: identical on every call from the same machine.
    It is NOT a secret — it is a visual identity key only.
    """
    parts: list[str] = []

    # 1. MAC address (survives OS reinstalls, changes on NIC swap)
    try:
        parts.append(str(uuid.getnode()))
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    # 2. Hostname
    try:
        parts.append(platform.node())
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    # 3. Username
    try:
        # os.getlogin() can raise in containers/CI — try multiple fallbacks
        parts.append(_get_username())
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    # 4. OS platform
    try:
        parts.append(platform.system())
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    if not parts:
        # Absolute last resort: random (entity won't be stable across sessions)
        logger.warning(
            "navig.identity.seed: could not derive stable attributes; "
            "using random seed — entity will change each run."
        )
        return uuid.uuid4().hex

    raw = "".join(parts)
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def _get_username() -> str:
    """Retrieve the current username with multiple fallback methods."""
    # Try os.getlogin() first
    try:
        return os.getlogin()
    except (OSError, AttributeError):
        pass  # hardware/OS attribute unavailable; skip
    # Environment variables (work in containers / CI)
    for var in ("USERNAME", "USER", "LOGNAME"):
        val = os.environ.get(var)
        if val:
            return val
    # Absolute fallback
    return "operator"
