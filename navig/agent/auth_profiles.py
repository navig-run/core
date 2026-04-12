"""
navig.agent.auth_profiles — Auth profile rotation with failure tracking.

Provides a pool of named API credentials that rotate in round-robin order.
Profiles that fail are placed on exponential cooldown and skipped until the
backoff window expires.  The pool never raises; callers receive ``None`` when
no healthy profile is available.

Configuration
-------------
    auth:
      profiles:
        - name: primary
          api_key: "<your-openai-key>"
          provider: openai
          weight: 2
        - name: backup
          api_key: "<your-openai-key>"
          provider: openai
          weight: 1

Usage
-----
    from navig.agent.auth_profiles import get_profile_pool

    pool = get_profile_pool()
    profile = pool.next_available()
    if profile is None:
        raise RuntimeError("No healthy auth profiles")
    try:
        result = call_api(api_key=profile.api_key)
        pool.mark_good(profile.name)
    except AuthError:
        pool.mark_failure(profile.name)
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

__all__ = [
    "AuthProfile",
    "ProfileCooldown",
    "AuthProfilePool",
    "get_profile_pool",
]

# Cooldown constants
_BASE_COOLDOWN_SECONDS: float = 5.0
_MAX_COOLDOWN_SECONDS: float = 300.0


# =============================================================================
# Data types
# =============================================================================


@dataclass
class AuthProfile:
    """A single named credential entry."""

    name: str
    api_key: str
    provider: str = "openai"
    weight: int = 1  # higher weight = picked more often in round-robin
    extra: dict[str, Any] = field(default_factory=dict)  # provider-specific extras


@dataclass
class ProfileCooldown:
    """Per-profile failure state used to compute exponential back-off."""

    failure_count: int = 0
    last_failure_ts: float = 0.0
    cooldown_seconds: float = 0.0

    def is_on_cooldown(self) -> bool:
        if self.cooldown_seconds <= 0:
            return False
        return (time.monotonic() - self.last_failure_ts) < self.cooldown_seconds

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_ts = time.monotonic()
        # Exponential back-off: base × 2^(failures-1), capped at MAX
        raw = _BASE_COOLDOWN_SECONDS * math.pow(2, self.failure_count - 1)
        self.cooldown_seconds = min(raw, _MAX_COOLDOWN_SECONDS)

    def reset(self) -> None:
        self.failure_count = 0
        self.last_failure_ts = 0.0
        self.cooldown_seconds = 0.0

    def remaining_seconds(self) -> float:
        """Seconds until this profile becomes available again (0 if ready)."""
        if not self.is_on_cooldown():
            return 0.0
        return max(0.0, self.cooldown_seconds - (time.monotonic() - self.last_failure_ts))


# =============================================================================
# AuthProfilePool
# =============================================================================


class AuthProfilePool:
    """
    Thread-safe round-robin pool of auth profiles with failure tracking.

    Round-robin respects ``weight``: a profile with weight=2 is inserted twice
    in the rotation list.  Profiles on active cooldown are skipped transparently.
    """

    def __init__(self, profiles: list[AuthProfile]) -> None:
        self._lock = threading.Lock()
        self._profiles: dict[str, AuthProfile] = {p.name: p for p in profiles}
        self._cooldowns: dict[str, ProfileCooldown] = {p.name: ProfileCooldown() for p in profiles}

        # Build weighted rotation list (name → popped in order)
        self._rotation: list[str] = []
        for p in profiles:
            self._rotation.extend([p.name] * max(1, p.weight))
        self._cursor: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def next_available(self) -> AuthProfile | None:
        """
        Return the next healthy (not on cooldown) profile in rotation.

        Scans up to ``len(rotation)`` positions starting from the current
        cursor.  Returns ``None`` if every profile is on cooldown.
        """
        with self._lock:
            if not self._rotation:
                return None

            n = len(self._rotation)
            for _ in range(n):
                name = self._rotation[self._cursor % n]
                self._cursor = (self._cursor + 1) % n
                cd = self._cooldowns.get(name)
                if cd and cd.is_on_cooldown():
                    logger.debug(
                        "auth_profiles: skipping '{}' (cooldown {:.0f}s left)",
                        name,
                        cd.remaining_seconds(),
                    )
                    continue
                profile = self._profiles.get(name)
                if profile:
                    return profile

            logger.warning("auth_profiles: all profiles are on cooldown")
            return None

    def mark_failure(self, name: str) -> None:
        """Record a failure for the named profile and apply exponential back-off."""
        with self._lock:
            cd = self._cooldowns.get(name)
            if cd is None:
                return
            cd.record_failure()
            logger.warning(
                "auth_profiles: '{}' marked failed (count={}, cooldown={:.0f}s)",
                name,
                cd.failure_count,
                cd.cooldown_seconds,
            )

    def mark_good(self, name: str) -> None:
        """Reset the failure state for the named profile."""
        with self._lock:
            cd = self._cooldowns.get(name)
            if cd:
                cd.reset()

    def add_profile(self, profile: AuthProfile) -> None:
        """Dynamically add a profile to the pool (thread-safe)."""
        with self._lock:
            if profile.name in self._profiles:
                self._rotation = [n for n in self._rotation if n != profile.name]
            self._profiles[profile.name] = profile
            self._cooldowns.setdefault(profile.name, ProfileCooldown())
            self._rotation.extend([profile.name] * max(1, profile.weight))

    def remove_profile(self, name: str) -> None:
        """Remove a profile from the pool (thread-safe)."""
        with self._lock:
            self._profiles.pop(name, None)
            self._cooldowns.pop(name, None)
            self._rotation = [n for n in self._rotation if n != name]

    def status(self) -> list[dict[str, Any]]:
        """Return a monitoring snapshot for all profiles."""
        with self._lock:
            rows = []
            for name, profile in self._profiles.items():
                cd = self._cooldowns[name]
                rows.append(
                    {
                        "name": name,
                        "provider": profile.provider,
                        "weight": profile.weight,
                        "healthy": not cd.is_on_cooldown(),
                        "failure_count": cd.failure_count,
                        "cooldown_remaining_s": round(cd.remaining_seconds(), 1),
                    }
                )
            return rows

    def __len__(self) -> int:
        return len(self._profiles)

    def healthy_count(self) -> int:
        return sum(1 for name, cd in self._cooldowns.items() if not cd.is_on_cooldown())


# =============================================================================
# Singleton factory
# =============================================================================

_pool_instance: AuthProfilePool | None = None
_pool_lock = threading.Lock()


def get_profile_pool() -> AuthProfilePool:
    """
    Return the global AuthProfilePool singleton.

    Loads profile definitions from ``auth.profiles`` in the global config.
    Returns an empty (0-profile) pool gracefully if no config is present.
    """
    global _pool_instance
    if _pool_instance is not None:
        return _pool_instance

    with _pool_lock:
        if _pool_instance is not None:
            return _pool_instance

        profiles: list[AuthProfile] = []
        try:
            from navig.config import get_config_manager

            raw_profiles = get_config_manager().global_config.get("auth", {}).get("profiles", [])
            for entry in raw_profiles:
                if not isinstance(entry, dict):
                    continue
                key = entry.get("api_key", "")
                name = entry.get("name", "")
                if not key or not name:
                    logger.warning("auth_profiles: skipping profile with missing name/api_key")
                    continue
                try:
                    weight = int(entry.get("weight", 1))
                except (ValueError, TypeError):
                    weight = 1

                profiles.append(
                    AuthProfile(
                        name=name,
                        api_key=key,
                        provider=entry.get("provider", "openai"),
                        weight=weight,
                        extra={
                            k: v
                            for k, v in entry.items()
                            if k not in {"name", "api_key", "provider", "weight"}
                        },
                    )
                )
        except Exception as exc:
            logger.warning("auth_profiles: failed to load profiles from config: {}", exc)

        _pool_instance = AuthProfilePool(profiles)
        logger.debug("auth_profiles: pool initialized with {} profile(s)", len(profiles))
        return _pool_instance


def reset_profile_pool() -> None:
    """Reset the singleton (used in tests)."""
    global _pool_instance
    with _pool_lock:
        _pool_instance = None
