"""
NAVIG Gateway — CooldownTracker

Per-key rate limiter for privileged / destructive operations.

Usage:
    tracker = CooldownTracker(default_cooldown_seconds=60)

    # Check before executing:
    allowed, wait_s = tracker.check_and_consume("restart:web", actor="telegram:123")
    if not allowed:
        return error(f"Cooldown active — try again in {wait_s:.0f}s")

    # After a successful dangerous action you might want a longer cooldown:
    tracker.set_cooldown("restart:myapp", 300)  # 5-minute cooldown

Design decisions:
  - Thread-safe (single threading.Lock; gateway runs on asyncio but may emit
    from sync helpers too).
  - State is in-memory only (intentional — cooldowns reset on restart to avoid
    permanent lock-outs).
  - Keys are strings of the form "<action_slug>[:<target>]".
  - Separate per-actor tracking to allow global or per-user limits.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class CooldownEntry:
    """State for a single cooldown key."""
    last_used: float = 0.0          # time.monotonic() of last allow
    cooldown_s: float = 60.0        # seconds to wait before next allow
    call_count: int = 0             # total usages since tracker start
    deny_count: int = 0             # total denials since tracker start


class CooldownTracker:
    """
    In-memory per-key cooldown / rate limiter.

    A key is usually ``"<action>:<actor>"`` (e.g. ``"mission.create:telegram:123"``),
    but can be scoped any way the caller needs.

    Thread-safe for use across asyncio tasks and sync helpers.
    """

    DEFAULT_COOLDOWNS: Dict[str, float] = {
        # Dangerous runtime mutations get longer cooldowns
        "mission.complete": 10.0,
        "node.register":    5.0,
        "system.shutdown": 120.0,
        "system.restart":  120.0,
        "system.stop":      60.0,
    }

    def __init__(self, default_cooldown_seconds: float = 30.0) -> None:
        self._default_s = default_cooldown_seconds
        self._entries: Dict[str, CooldownEntry] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_and_consume(
        self,
        key: str,
        actor: Optional[str] = None,
    ) -> Tuple[bool, float]:
        """
        Check whether *key* is past its cooldown, and consume one slot.

        :returns: ``(allowed, wait_seconds)``
                  *wait_seconds* is 0.0 when allowed, or the remaining cooldown.
        """
        full_key = f"{key}:{actor}" if actor else key
        now = time.monotonic()

        with self._lock:
            entry = self._entries.setdefault(
                full_key,
                CooldownEntry(cooldown_s=self._resolve_cooldown(key)),
            )
            elapsed = now - entry.last_used
            if elapsed < entry.cooldown_s:
                wait = entry.cooldown_s - elapsed
                entry.deny_count += 1
                logger.debug(
                    "CooldownTracker DENY key=%s actor=%s wait=%.1fs",
                    key, actor, wait,
                )
                return False, wait

            entry.last_used = now
            entry.call_count += 1
            logger.debug(
                "CooldownTracker ALLOW key=%s actor=%s (call #%d)",
                key, actor, entry.call_count,
            )
            return True, 0.0

    def set_cooldown(self, key: str, seconds: float) -> None:
        """Override the cooldown duration for a specific action key (not actor-scoped)."""
        with self._lock:
            # Update all existing entries that match this key prefix
            for full_key, entry in self._entries.items():
                if full_key == key or full_key.startswith(f"{key}:"):
                    entry.cooldown_s = seconds

    def reset(self, key: str, actor: Optional[str] = None) -> None:
        """Force-reset a cooldown (e.g., after admin override)."""
        full_key = f"{key}:{actor}" if actor else key
        with self._lock:
            self._entries.pop(full_key, None)
        logger.info("CooldownTracker reset key=%s actor=%s", key, actor)

    def stats(self) -> Dict[str, Dict]:
        """Return current state of all tracked keys (for dashboards)."""
        now = time.monotonic()
        with self._lock:
            return {
                k: {
                    "call_count": e.call_count,
                    "deny_count": e.deny_count,
                    "cooldown_s": e.cooldown_s,
                    "remaining_s": max(0.0, e.cooldown_s - (now - e.last_used)),
                }
                for k, e in self._entries.items()
            }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _resolve_cooldown(self, key: str) -> float:
        """Look up per-action default cooldown, falling back to instance default."""
        return self.DEFAULT_COOLDOWNS.get(key, self._default_s)

    def __repr__(self) -> str:  # pragma: no cover
        return f"CooldownTracker(default={self._default_s}s, keys={len(self._entries)})"
