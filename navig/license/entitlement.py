"""Harbor Bay entitlement — the ONE canonical unlock rule the whole daemon shares.

Every surface that reasons about whether a priced Bay item is unlocked routes through
here: the Bay catalog gate + install doors (``gateway/deck/routes/catalog.py``) and the
Store's Bay-apps collector (``hub/aggregator.py``). It lived only in ``catalog.py`` — a
deck route the ``hub`` can't import without a layering cycle — so the Store re-derived
its own (broken) version. Now there is a single home, neutral to both.

The TypeScript SDK ``packages/shared/bay-sdk/src/entitlement.ts`` mirrors this for
client-side surfaces; ``TIER_RANK`` parity is asserted by
``tests/gateway/test_entitlement_parity.py``. Keep the two in sync.
"""

from __future__ import annotations

from typing import Any

# Harbor tier ladder. Legacy names map onto comparable live ranks so old tokens still
# resolve. MIRRORED in bay-sdk/src/entitlement.ts (guarded by test_entitlement_parity).
TIER_RANK: dict[str, int] = {
    "free": 0, "solo": 0,
    "plus": 1, "personal": 1,
    "max": 2, "pro": 2, "business": 2,
    "team": 3, "fleet": 3,
    "enterprise": 4,
}


def tier_rank(tier: str | None) -> int:
    """Rank of a tier name; unknown names rank as ``free`` (0)."""
    return TIER_RANK.get(str(tier or "free"), 0)


def live_caps_and_rank() -> tuple[list[str], int]:
    """The current license's capability list + tier rank.

    Raises if the license subsystem is unavailable — callers decide the fail-open policy
    (Bay surfaces fail OPEN so licensing hiccups never hide the catalog).
    """
    from navig.license import current_status  # noqa: PLC0415

    status = current_status()
    caps = list(status.capabilities or [])
    return caps, tier_rank(status.effective_tier)


def is_unlocked(
    *,
    model: str,
    capability: str | None,
    included_in: str | None,
    caps: list[str],
    tier_rank: int,  # noqa: A002 — matches the historical keyword name
) -> bool:
    """Whether a priced item is unlocked for a license with ``caps`` / ``tier_rank``.

    Free items are always unlocked; a bought ``item:`` grant unlocks forever; a
    tier-included item unlocks while the current tier rank covers ``included_in``. Pure —
    the caller fetches the live license once and passes it in.
    """
    if model not in ("tier", "buy_once"):
        return True
    if capability and capability in caps:
        return True  # owned forever
    if included_in is not None:
        return tier_rank >= TIER_RANK.get(included_in, 1)
    return False


def capability_for(pricing: dict[str, Any] | None, slug: str) -> str | None:
    """The perpetual grant key ``item:<slug>`` for a ``buy_once`` item, else None.

    Mirrors the SDK's ``capabilityFor``. ``pricing`` is the item's nested Bay-catalog
    ``pricing`` block (camelCase ``{model, includedInTier, …}``).
    """
    p = pricing if isinstance(pricing, dict) else {}
    return f"item:{slug.lower()}" if p.get("model") == "buy_once" and slug else None


def is_entry_unlocked(
    pricing: dict[str, Any] | None, slug: str, *, caps: list[str], tier_rank: int  # noqa: A002
) -> bool:
    """Unlock state for a Bay-catalog entry from its nested ``pricing`` block + ``slug``.

    The nested-schema convenience over :func:`is_unlocked` — mirrors the SDK's
    ``isUnlockedFor``. Reads ``pricing.model`` / ``pricing.includedInTier`` (camelCase, as
    the Bay catalog serves them), NOT the flat ``price_cents`` the Store used to read
    (which never existed in the schema, so every Bay app resolved as free/unlocked).
    """
    p = pricing if isinstance(pricing, dict) else {}
    model = str(p.get("model") or "free")
    included = p.get("includedInTier")
    return is_unlocked(
        model=model,
        capability=capability_for(p, slug),
        included_in=str(included) if included else None,
        caps=caps,
        tier_rank=tier_rank,
    )
