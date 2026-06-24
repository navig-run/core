"""
NAVIG license quota tables — what each tier grants.

The canonical, language-neutral spec lives in ``navig/license/tiers.json``
(``legacy`` section). These dicts mirror it, as do the TS duplicates in
``navig-api/src/lib/billing.ts`` and ``navig-deck/lib/license.ts``. They MUST
agree — ``tests/license/test_tiers_parity.py`` enforces it. See
``docs/BUSINESS-PLAN.md`` for the NAVIG Harbor consolidation that will fold
these into Free/Plus/Max + Team/Enterprise.
"""

from __future__ import annotations

from typing import Literal

# Live (NAVIG Harbor) tiers we sell + enforce, PLUS retired legacy names kept
# recognized so old/stray signed tokens still verify. Mirrors tiers.json `live`.
TierName = Literal[
    # live (Harbor)
    "free", "plus", "max", "team", "enterprise",
    # legacy (retired; recognized for back-compat verification only)
    "solo", "personal", "pro", "business", "fleet",
]

# Retired legacy tier → Harbor equivalent (≥ value). Applied at verify time so
# an old token resolves to its Harbor tier; nobody loses entitlements.
LEGACY_TO_HARBOR: dict[str, str] = {
    "solo": "free",
    "personal": "plus",
    "pro": "plus",
    "business": "max",
    "fleet": "max",
}

# Tier → host_limit. /api/deck/hosts uses this to slice the host inventory.
# Plus and Max share capabilities but differ on host scale (+ AI allowance,
# priority relay, lifetime — enforced elsewhere).
TIER_HOST_LIMIT: dict[TierName, int] = {
    # live
    "free": 1,
    "plus": 5,
    "max": 200,
    "team": 200,
    "enterprise": 100_000,  # effectively unlimited; sized for safety net
    # legacy (recognized; never sold)
    "solo": 1,
    "personal": 5,
    "pro": 10,
    "business": 50,
    "fleet": 200,
}

# Tier → capabilities (internal capability flags unlocked). Modules are no
# longer sold à-la-carte (see tiers.json) — only granted by tier. Plus/Max
# carry the full operator set; Team/Enterprise add client_ops.
TIER_CAPABILITIES: dict[TierName, list[str]] = {
    # live (Harbor)
    "free":       ["core_ops"],
    "plus":       ["core_ops", "business_ops", "ai_operator", "security_ops", "deploy_ops", "echo"],
    "max":        ["core_ops", "business_ops", "ai_operator", "security_ops", "deploy_ops", "echo"],
    "team":       ["core_ops", "business_ops", "ai_operator", "security_ops", "deploy_ops", "client_ops", "echo"],
    "enterprise": ["core_ops", "business_ops", "ai_operator", "security_ops", "deploy_ops", "client_ops", "echo"],
    # legacy (recognized; capabilities ≤ their Harbor mapping)
    "solo":       ["core_ops"],
    "personal":   ["core_ops", "echo"],
    "pro":        ["core_ops", "business_ops", "ai_operator", "echo"],
    "business":   ["core_ops", "business_ops", "ai_operator", "security_ops", "deploy_ops", "echo"],
    "fleet":      ["core_ops", "business_ops", "ai_operator", "security_ops", "deploy_ops", "client_ops", "echo"],
}

# Every module name in canonical order. Used by the Deck's module manifest
# and by the license signer's validation.
# `echo` = the NAVIG Echo pro surface (cloud voice, always-on multi-channel,
# premium packs). Granted by `plus` and every operator tier; not a Deck UI module.
ALL_MODULES: tuple[str, ...] = (
    "core_ops",
    "business_ops",
    "ai_operator",
    "security_ops",
    "deploy_ops",
    "client_ops",
    "echo",
)


def effective_host_limit() -> int:
    """Return the host_limit currently in effect for THIS daemon.

    Reads the persisted license via the public ``navig.license`` API. Used
    by ``/api/deck/hosts`` to slice the inventory. Falls back to Solo's
    host_limit (1) on any error so the daemon never crashes a request when
    the license module misbehaves.
    """
    try:
        from navig.license import current_status
        return current_status().host_limit
    except Exception:  # noqa: BLE001
        return TIER_HOST_LIMIT["free"]


def current_tier_name() -> str:
    """Return the effective tier name (e.g. 'solo', 'pro')."""
    try:
        from navig.license import current_status
        return current_status().effective_tier
    except Exception:  # noqa: BLE001
        return "free"
