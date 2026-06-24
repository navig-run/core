"""
NAVIG license — the daemon-side license verification & persistence layer.

Public API
----------
- ``verify_license(token)``       — pure verifier; returns LicenseStatus
- ``current_status()``            — reads ~/.navig/license.key + verifies
- ``paste_license(token)``        — validates + persists; returns LicenseStatus
- ``remove_license()``            — deletes the persisted license
- ``read_raw_token()``            — returns the raw token string (for /raw endpoint)
- ``current_tier_name()``         — convenience: just the effective tier
- ``effective_host_limit()``      — convenience: just the host_limit

The license file lives at ``~/.navig/license.key`` with 0600 permissions on
Unix-like systems (best-effort on Windows where chmod is a no-op). The
plaintext token is what's stored -- there's no encryption layer; the
signature is what protects it from tampering.
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from navig.license.gate import requires_capability
from navig.license.keys import (
    LicenseStatus,
    Reason,
    sign_payload,
    verify_license,
)
from navig.license.quota import (
    ALL_MODULES,
    TIER_CAPABILITIES,
    TIER_HOST_LIMIT,
    TierName,
    current_tier_name,
    effective_host_limit,
)
from navig.platform import paths

logger = logging.getLogger(__name__)

__all__ = [
    "ALL_MODULES",
    "LicenseStatus",
    "Reason",
    "TierName",
    "TIER_CAPABILITIES",
    "TIER_HOST_LIMIT",
    "current_status",
    "current_tier_name",
    "effective_host_limit",
    "license_path",
    "paste_license",
    "read_raw_token",
    "remove_license",
    "requires_capability",
    "sign_payload",
    "verify_license",
]


def license_path() -> Path:
    """Canonical path to the persisted license token."""
    return paths.config_dir() / "license.key"


def read_raw_token() -> str | None:
    """Return the raw token string from disk, or None if no file."""
    p = license_path()
    try:
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8").strip() or None
    except OSError as exc:
        logger.debug("read_raw_token failed: %r", exc)
        return None


# Module-level cache. The license rarely changes during a process lifetime
# (paste / remove invalidate it explicitly). Reading + verifying on every
# /api/deck/hosts call is wasteful when there's no change.
_CACHED_STATUS: LicenseStatus | None = None
_CACHED_TOKEN: str | None = None


def _invalidate_cache() -> None:
    global _CACHED_STATUS, _CACHED_TOKEN
    _CACHED_STATUS = None
    _CACHED_TOKEN = None


def current_status() -> LicenseStatus:
    """Read the persisted token and return its verified status.

    Cached for the process lifetime; cache is invalidated on paste/remove.
    """
    global _CACHED_STATUS, _CACHED_TOKEN
    token = read_raw_token()
    if token == _CACHED_TOKEN and _CACHED_STATUS is not None:
        return _CACHED_STATUS
    status = verify_license(token)
    _CACHED_TOKEN = token
    _CACHED_STATUS = status
    return status


def paste_license(token: str) -> LicenseStatus:
    """Validate ``token``; on success, persist it to ~/.navig/license.key.

    Returns the LicenseStatus regardless of success. On failure the existing
    file is left untouched (don't trash a working license with a typo'd
    paste). On success the cache is invalidated so the next ``current_status()``
    re-reads from disk.
    """
    if not isinstance(token, str):
        return verify_license(None)

    token = token.strip()
    status = verify_license(token)
    if not status.valid:
        # Reject -- don't write garbage to disk.
        return status

    p = license_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp + rename for atomic replace.
        tmp = p.with_suffix(".key.tmp")
        tmp.write_text(token + "\n", encoding="utf-8")
        # Best-effort 0600. Windows ignores POSIX mode; the file inherits
        # the user profile's ACL which is already restricted.
        try:
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        os.replace(tmp, p)
    except OSError as exc:
        logger.error("license paste write failed: %r", exc)
        # Treat write failure as a soft error -- return the would-be-valid
        # status so the caller can decide; the file just isn't persisted.
        # Next process start will fall back to Solo.
        return status

    _invalidate_cache()
    return current_status()


def remove_license() -> LicenseStatus:
    """Delete the persisted license. Returns the post-removal Solo status."""
    p = license_path()
    try:
        if p.exists():
            p.unlink()
    except OSError as exc:
        logger.error("license remove failed: %r", exc)
    _invalidate_cache()
    return current_status()


def truncate_for_display(token: str | None, *, prefix: int = 12, suffix: int = 6) -> str:
    """Render a license token safely for logs / status output (never log full)."""
    if not token:
        return "<none>"
    if len(token) <= prefix + suffix + 3:
        return "***"
    return f"{token[:prefix]}…{token[-suffix:]}"
