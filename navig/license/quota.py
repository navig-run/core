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

TierName = Literal["solo", "plus", "personal", "pro", "business", "fleet", "enterprise"]

# Tier → host_limit. Phase 3.1's /api/deck/hosts endpoint uses this to
# slice the unified host inventory before returning it to the Deck.
# `plus` is the consumer rung (NAVIG Echo pro) — 1 host like solo; it buys the
# `echo` capability, not operator host scale.
TIER_HOST_LIMIT: dict[TierName, int] = {
    "solo": 1,
    "plus": 1,
    "personal": 5,
    "pro": 10,
    "business": 50,
    "fleet": 200,
    "enterprise": 100_000,  # effectively unlimited; sized for safety net
}

# Tier → capabilities (which product modules are unlocked).
#
# Modules are STRICT SUPERSETS down the ladder. See the plan's "Capability
# inclusion matrix" -- this map is the executable version of that table.
TIER_CAPABILITIES: dict[TierName, list[str]] = {
    "solo":       ["core_ops"],
    "plus":       ["core_ops", "echo"],
    "personal":   ["core_ops", "echo"],
    "pro":        ["core_ops", "business_ops", "ai_operator", "echo"],
    "business":   ["core_ops", "business_ops", "ai_operator", "security_ops", "deploy_ops", "echo"],
    "fleet":      ["core_ops", "business_ops", "ai_operator", "security_ops", "deploy_ops", "client_ops", "echo"],
    "enterprise": ["core_ops", "business_ops", "ai_operator", "security_ops", "deploy_ops", "client_ops", "echo"],
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
        return TIER_HOST_LIMIT["solo"]


def current_tier_name() -> str:
    """Return the effective tier name (e.g. 'solo', 'pro')."""
    try:
        from navig.license import current_status
        return current_status().effective_tier
    except Exception:  # noqa: BLE001
        return "solo"
