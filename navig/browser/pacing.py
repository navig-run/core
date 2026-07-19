"""Human-cadence pacing — think-time jitter, jittered backoff, per-host throttle.

A *constant* request cadence is itself a detection signal, so every wait here is jittered.
Blocks back off
exponentially with ±jitter and honour a server ``Retry-After``; a per-host throttle keeps a
minimum (jittered) gap between hits to the same host. All sync+async, clock-injectable for
tests.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Callable

__all__ = ["jittered", "think", "think_sync", "Backoff", "HostThrottle"]


def jittered(base: float, jitter: float = 0.25) -> float:
    """*base* seconds ± *jitter* fraction, floored at 0 (e.g. 2.0 ± 25% → 1.5–2.5)."""
    if base <= 0:
        return 0.0
    return max(0.0, base * (1 + random.uniform(-jitter, jitter)))


async def think(base: float, jitter: float = 0.25) -> None:
    await asyncio.sleep(jittered(base, jitter))


def think_sync(base: float, jitter: float = 0.25) -> None:
    time.sleep(jittered(base, jitter))


class Backoff:
    """Exponential backoff with ±jitter, capped, honouring a server ``Retry-After``."""

    def __init__(self, base: float = 1.0, factor: float = 2.0, cap: float = 60.0,
                 jitter: float = 0.25):
        self.base = base
        self.factor = factor
        self.cap = cap
        self.jitter = jitter
        self._attempt = 0

    @property
    def attempt(self) -> int:
        return self._attempt

    def next_delay(self, retry_after: float | None = None) -> float:
        """Advance one attempt; return the delay. A valid *retry_after* takes precedence."""
        self._attempt += 1
        if retry_after is not None and retry_after > 0:
            return min(float(retry_after), self.cap)
        raw = self.base * (self.factor ** (self._attempt - 1))
        return min(jittered(raw, self.jitter), self.cap)

    def reset(self) -> None:
        self._attempt = 0

    async def sleep(self, retry_after: float | None = None) -> float:
        d = self.next_delay(retry_after)
        await asyncio.sleep(d)
        return d


class HostThrottle:
    """Enforce a minimum (jittered) interval between requests to the SAME host."""

    def __init__(self, min_interval: float = 1.0, jitter: float = 0.25,
                 clock: Callable[[], float] = time.monotonic):
        self.min_interval = min_interval
        self.jitter = jitter
        self._clock = clock
        self._last: dict[str, float] = {}

    def wait_needed(self, host: str) -> float:
        """Seconds to wait before hitting *host* again (0 if enough time has passed)."""
        now = self._clock()
        last = self._last.get(host)
        if last is None:
            return 0.0
        gap = jittered(self.min_interval, self.jitter)
        remaining = (last + gap) - now
        return max(0.0, remaining)

    def record(self, host: str) -> None:
        self._last[host] = self._clock()

    async def acquire(self, host: str) -> float:
        """Sleep as long as needed for *host*, mark it hit, return the slept seconds."""
        wait = self.wait_needed(host)
        if wait > 0:
            await asyncio.sleep(wait)
        self.record(host)
        return wait
