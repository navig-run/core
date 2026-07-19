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

import datetime as _dt
import json
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
    "dev_override_path",
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


# ─── Dev license override (local testing only) ──────────────────────────────
#
# A DEV-ONLY escape hatch so a developer can flip the daemon between Harbor
# tiers (Free / Pass / Max / Team / Enterprise) — and simulate owned Bay
# `item:<id>` grants — WITHOUT the founder signing key and WITHOUT touching the
# real ``license.key`` (which stays byte-for-byte intact, so it's always
# restorable by simply deleting the override file).
#
# It works by short-circuiting ``current_status()`` when the override file
# exists. Because that function is the single chokepoint every surface reads
# through (the 402 gate, the Bay catalog's unlock state, the Deck's license
# panel), a synthesized status flows through the ENTIRE enforcement + UI chain
# exactly like a real token — only the Ed25519 signature check is skipped.
#
# Safety: the file lives under ``~/.navig/dev/`` (created only by the
# ``dev_license.py`` tool / ``npm run license:*``), is absent on every end-user
# install, is gitignored, and the daemon logs a loud WARNING whenever it is in
# effect. Written/cleared by ``core/tools/dev_license.py`` (excluded from the
# shipped wheel, like ``license_sign.py``).

# Accept the Harbor display aliases the switcher exposes → internal tier keys.
_DEV_TIER_ALIASES = {
    "pass": "plus",
    "harbor_pass": "plus",
    "harbor-pass": "plus",
    "harbor_max": "max",
    "harbor-max": "max",
    "harbor_team": "team",
    "harbor_enterprise": "enterprise",
}


def _coerce_bool(value: object, default: bool) -> bool:
    """Coerce a JSON/env value to bool. Strings 'false'/'0'/'no'/'off' → False
    (plain ``bool("false")`` is True — the sharp edge this avoids)."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def dev_override_path() -> Path:
    """Canonical path to the dev license-override file (may not exist)."""
    return paths.config_dir() / "dev" / "license-override.json"


_WARNED_OVERRIDE_IGNORED = False


def _dev_overrides_allowed() -> bool:
    """Whether a dev license-override file may be honored.

    SECURITY: the override synthesizes an arbitrary entitlement WITHOUT any
    signature check, so it must never be honored on a shipped install — else any
    user could write ``{"tier":"enterprise"}`` and bypass the Ed25519 license.
    It is allowed ONLY when:
      - ``NAVIG_DEV_LICENSE`` is an explicit truthy opt-in, OR
      - the writer tool ``core/tools/dev_license.py`` is present on disk, which
        is true for a source/editable checkout but NOT the wheel (``tools/`` is
        excluded from packaging), so it doubles as source-install detection.
    """
    if _coerce_bool(os.environ.get("NAVIG_DEV_LICENSE"), False):
        return True
    try:
        # this module: <root>/navig/license/__init__.py → parents[2] == <root>
        # source checkout: <root> == core/ (so core/tools/dev_license.py exists);
        # installed wheel: <root> == site-packages/ (no tools/dev_license.py).
        return (Path(__file__).resolve().parents[2] / "tools" / "dev_license.py").is_file()
    except Exception:  # noqa: BLE001
        return False


def _read_dev_override_raw() -> str | None:
    """Return the raw override JSON text, or None when no override is in effect.

    Returns None (override ignored) unless dev overrides are allowed on this
    install — see :func:`_dev_overrides_allowed`.
    """
    global _WARNED_OVERRIDE_IGNORED
    try:
        p = dev_override_path()
        if not p.is_file():
            return None
        if not _dev_overrides_allowed():
            if not _WARNED_OVERRIDE_IGNORED:
                _WARNED_OVERRIDE_IGNORED = True
                logger.warning(
                    "Ignoring dev license override at %s on a non-dev install "
                    "(set NAVIG_DEV_LICENSE=1 to opt in). The signed license is authoritative.",
                    p,
                )
            return None
        return p.read_text(encoding="utf-8").strip() or None
    except OSError as exc:
        logger.debug("dev override read failed: %r", exc)
        return None


def _synth_dev_status(data: dict) -> LicenseStatus:
    """Build a LicenseStatus from a dev-override dict, mirroring the real
    entitlement assembly in ``verify_license`` (tier caps + appended perpetual
    item grants) so downstream behaviour is identical."""
    raw_tier = str(data.get("tier") or "free").strip().lower()
    tier = _DEV_TIER_ALIASES.get(raw_tier, raw_tier)
    if tier not in TIER_HOST_LIMIT:
        tier = "free"

    perpetual_modules = [
        str(m).lower()
        for m in (data.get("perpetual_modules") or data.get("items") or [])
        if isinstance(m, str) and str(m).strip()
    ]

    capabilities = list(TIER_CAPABILITIES[tier])
    for m in perpetual_modules:
        if m not in capabilities:
            capabilities.append(m)

    # Default: any paid tier is an "active subscription"; Free is not. The
    # switcher can force this (e.g. Free tier that still owns an item → the
    # "moored, sails again with your Harbor" lapse state). Coerce properly so a
    # hand-edited "subscription_active": "false" isn't truthy.
    subscription_active = _coerce_bool(data.get("subscription_active"), tier != "free")
    now = _dt.datetime.now(tz=_dt.timezone.utc)
    subscription_until = now + _dt.timedelta(days=365) if subscription_active else None

    return LicenseStatus(
        valid=True,
        effective_tier=tier,  # type: ignore[arg-type]
        host_limit=TIER_HOST_LIMIT[tier],
        capabilities=capabilities,
        subscription_until=subscription_until,
        subscription_active=subscription_active,
        perpetual_modules=perpetual_modules,
        reason="ok",
        license_id="dev-override",
        billing_period="dev_override",
    )


_WARNED_OVERRIDE_RAW: str | None = None


def _warn_dev_override_once(raw: str, status: LicenseStatus) -> None:
    """Log a loud warning the first time each distinct override takes effect —
    a dev override must never be silent."""
    global _WARNED_OVERRIDE_RAW
    if raw == _WARNED_OVERRIDE_RAW:
        return
    _WARNED_OVERRIDE_RAW = raw
    logger.warning(
        "[DEV LICENSE OVERRIDE ACTIVE] effective tier=%s (real license.key untouched). "
        "Delete %s or run `npm run license:restore` to disable.",
        status.effective_tier,
        dev_override_path(),
    )


# Module-level cache. The license rarely changes during a process lifetime
# (paste / remove invalidate it explicitly). Reading + verifying on every
# /api/deck/hosts call is wasteful when there's no change. The cache key is the
# raw token for a real license, or ``"dev-override:<json>"`` for an override —
# real tokens start with ``NAVIG-LICENSE-v1:`` so the two never collide.
_CACHED_STATUS: LicenseStatus | None = None
_CACHED_TOKEN: str | None = None


def _invalidate_cache() -> None:
    global _CACHED_STATUS, _CACHED_TOKEN
    _CACHED_STATUS = None
    _CACHED_TOKEN = None


def current_status() -> LicenseStatus:
    """Read the effective license status.

    A dev override (``~/.navig/dev/license-override.json``) wins when present;
    otherwise the persisted token is read and cryptographically verified.
    Cached for the process lifetime and re-derived whenever the override or
    token content changes (both are read from disk on each call, matching the
    existing behaviour).
    """
    global _CACHED_STATUS, _CACHED_TOKEN

    override_raw = _read_dev_override_raw()
    if override_raw is not None:
        cache_key = "dev-override:" + override_raw
        if cache_key == _CACHED_TOKEN and _CACHED_STATUS is not None:
            return _CACHED_STATUS
        try:
            data = json.loads(override_raw)
        except (ValueError, TypeError):
            data = None
        if isinstance(data, dict):
            status = _synth_dev_status(data)
            _warn_dev_override_once(override_raw, status)
            _CACHED_TOKEN = cache_key
            _CACHED_STATUS = status
            return status
        # Corrupt / non-object override → fall through to the REAL signed license
        # rather than silently synthesizing a Free tier that shadows a paid key.
        logger.warning("dev license override is not a JSON object — ignoring it, using the real license.")

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
