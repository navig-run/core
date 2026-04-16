"""Typed backoff policy for reconnect and retry loops.

Single source of truth for exponential-backoff-with-jitter logic used by:
- SSH tunnel reconnect (navig/tunnel.py)
- Telegram gateway polling recovery (navig/gateway/channels/telegram.py)
- Any future channel or service reconnect loop

Usage
-----
from navig.retry_policy import BackoffPolicy, backoff_sleep

_TUNNEL_POLICY = BackoffPolicy(initial_ms=2_000, max_ms=120_000, factor=2.0, jitter=0.1)

for attempt in range(MAX_ATTEMPTS):
    await backoff_sleep(_TUNNEL_POLICY, attempt)
    try:
        ...
    except Exception:
        continue
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Policy definitions — one canonical constant per concern; add new ones here
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BackoffPolicy:
    """Immutable backoff configuration.

    Parameters
    ----------
    initial_ms:
        Sleep duration (ms) on the first retry attempt (attempt == 0).
    max_ms:
        Upper cap on sleep duration regardless of attempt count.
    factor:
        Multiplicative growth factor per attempt.
    jitter:
        Fractional random spread applied to each computed delay.
        E.g. 0.1 means ±10 % of the delay is added/removed randomly.
    """

    initial_ms: int = 5_000
    max_ms: int = 300_000
    factor: float = 2.0
    jitter: float = 0.1

    def delay_ms(self, attempt: int) -> float:
        """Return the jittered delay in milliseconds for *attempt* (0-based)."""
        raw = min(self.initial_ms * (self.factor ** attempt), float(self.max_ms))
        spread = raw * self.jitter
        return raw + random.uniform(-spread, spread)  # noqa: S311 (not crypto)

    def delay_s(self, attempt: int) -> float:
        """Return the jittered delay in seconds for *attempt* (0-based)."""
        return self.delay_ms(attempt) / 1_000.0


# ---------------------------------------------------------------------------
# Pre-defined canonical policies per concern
# ---------------------------------------------------------------------------

#: Tunnel reconnect — starts fast (2 s), caps at 2 min.
TUNNEL_RECONNECT: BackoffPolicy = BackoffPolicy(
    initial_ms=2_000,
    max_ms=120_000,
    factor=2.0,
    jitter=0.15,
)

#: Telegram polling recovery — starts at 5 s, caps at 5 min.
TELEGRAM_POLLING: BackoffPolicy = BackoffPolicy(
    initial_ms=5_000,
    max_ms=300_000,
    factor=2.0,
    jitter=0.1,
)

#: Generic API rate-limit backoff — starts at 1 s, caps at 64 s.
API_RATE_LIMIT: BackoffPolicy = BackoffPolicy(
    initial_ms=1_000,
    max_ms=64_000,
    factor=2.0,
    jitter=0.2,
)

#: Channel health-monitor restart — starts at 5 s, caps at 5 min.
CHANNEL_RESTART: BackoffPolicy = BackoffPolicy(
    initial_ms=5_000,
    max_ms=300_000,
    factor=2.0,
    jitter=0.1,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def backoff_sleep(policy: BackoffPolicy, attempt: int) -> None:
    """Async sleep for the jittered delay computed from *policy* and *attempt*.

    Skips the sleep entirely on attempt 0 so the first retry is immediate
    (the caller can choose to start attempts at 1 instead to add an initial
    delay).
    """
    if attempt <= 0:
        return
    secs = policy.delay_s(attempt)
    await asyncio.sleep(secs)


def backoff_sleep_sync(policy: BackoffPolicy, attempt: int) -> None:
    """Synchronous equivalent of :func:`backoff_sleep` for non-async contexts."""
    if attempt <= 0:
        return
    time.sleep(policy.delay_s(attempt))
