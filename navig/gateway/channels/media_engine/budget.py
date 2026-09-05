"""
Monthly API cost budget tracker for the Media Context Engine.

Stores per-month totals in ``~/.navig/media_budget.json``.
All writes are atomic (write-to-temp then rename).

Usage::

    from navig.gateway.channels.media_engine.budget import BudgetGuard

    guard = BudgetGuard(monthly_limit_usd=5.0)
    guard.charge("audd", 0.002)           # raises BudgetExceeded when over limit
    guard.charge("openai_vision", 0.01)
    print(guard.remaining())              # remaining USD this month
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from navig.core.json_io import (
    JsonReadError,
    atomic_write_json,
    load_json_for_update,
    load_json_safe,
)
from navig.platform.paths import media_budget_path

logger = logging.getLogger(__name__)

# Default cost estimates per call (USD) — conservative upper bounds
DEFAULT_COSTS: dict[str, float] = {
    "audd": 0.002,  # AudD API per fingerprint
    "openai_whisper": 0.006,  # Whisper API per minute (avg 1 min)
    "openai_vision": 0.015,  # GPT-4o vision per image (~500 tokens out)
    "spotify": 0.0,  # Spotify API — free tier
    "lastfm": 0.0,  # Last.fm API — free tier
    "serpapi": 0.005,  # SerpAPI Google Lens per call
    "google_vision": 0.0015,  # Google Cloud Vision per image
    "mutagen": 0.0,  # local — free
    "tesseract": 0.0,  # local — free
    "pillow": 0.0,  # local — free
}


class BudgetExceeded(RuntimeError):
    """Raised when the current month's API budget would be exceeded."""

    def __init__(self, used: float, limit: float, service: str) -> None:
        self.used = used
        self.limit = limit
        self.service = service
        super().__init__(
            f"Monthly media-API budget of ${limit:.2f} would be exceeded "
            f"(current: ${used:.4f}) — skipping enrichment via {service}"
        )


class BudgetGuard:
    """Tracks monthly API spend in ~/.navig/media_budget.json."""

    def __init__(
        self,
        monthly_limit_usd: float = 5.0,
        budget_file: Path | None = None,
    ) -> None:
        self._limit = monthly_limit_usd
        if budget_file is None:
            budget_file = media_budget_path()
        self._path = budget_file
        self._path.parent.mkdir(parents=True, exist_ok=True)

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _month_key(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    def _load(self) -> dict:
        # Read-only view (used/remaining/can_afford) — degrade to {} on any failure so a
        # status read never crashes. The MUTATING path (charge) reads via
        # load_json_for_update directly, so a transient lock there raises instead of
        # returning {} and letting the save wipe the spend history.
        return load_json_safe(self._path, default={})

    def _save(self, data: dict) -> None:
        try:
            atomic_write_json(data, self._path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("BudgetGuard: failed to save %s: %s", self._path, exc)

    # ── Public API ────────────────────────────────────────────────────────────

    def used(self) -> float:
        """Return total USD spent in the current calendar month."""
        return self._load().get(self._month_key(), 0.0)

    def remaining(self) -> float:
        """Return remaining USD budget for the current month."""
        return max(0.0, self._limit - self.used())

    def charge(self, service: str, amount: float | None = None) -> None:
        """Add *amount* to this month's spend, or use DEFAULT_COSTS[service].

        Raises ``BudgetExceeded`` *before* adding if the charge would push the
        total over the monthly limit.  Pass ``amount=0.0`` for free services to
        still record the call without risk.
        """
        cost = amount if amount is not None else DEFAULT_COSTS.get(service, 0.001)
        if cost <= 0.0:
            return  # free service — nothing to track

        try:
            data = load_json_for_update(self._path, default={})
        except JsonReadError:
            # Budget file exists but is transiently unreadable (a lock). Skip billing
            # this ONE call rather than save {} over the whole spend history — which
            # would both LOSE the history AND reset the guard (0 spent → the monthly
            # limit stops biting until the file is rewritten).
            logger.warning(
                "BudgetGuard: %s unreadable — not recording the %s charge", self._path, service
            )
            return

        key = self._month_key()
        current = data.get(key, 0.0)

        if self._limit > 0 and (current + cost) > self._limit:
            raise BudgetExceeded(used=current, limit=self._limit, service=service)

        data[key] = round(current + cost, 6)
        self._save(data)

    def can_afford(self, service: str, amount: float | None = None) -> bool:
        """Return True if charging *service* would NOT exceed the monthly budget."""
        cost = amount if amount is not None else DEFAULT_COSTS.get(service, 0.001)
        if self._limit <= 0 or cost <= 0.0:
            return True
        return (self.used() + cost) <= self._limit

    def reset_month(self) -> None:  # pragma: no cover — test utility
        data = self._load()
        data[self._month_key()] = 0.0
        self._save(data)
