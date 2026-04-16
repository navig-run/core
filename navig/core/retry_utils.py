"""
Retry utilities for NAVIG — jittered exponential back-off with optional async support.

Ported and extended from``agent/retry_utils.py``.

Key design choices:
- Thread-safe jitter seeding (monotonic counter XOR'd with nanosecond timestamp).
- ``RetryConfig`` dataclass keeps retry policy in one place per the single-source-of-truth rule.
- ``async_retry`` decorator wraps coroutines without blocking the event-loop.

Usage::

    from navig.core.retry_utils import jittered_backoff, RetryConfig, async_retry
    import asyncio, time

    # Direct usage
    for attempt in range(5):
        delay = jittered_backoff(attempt)
        time.sleep(delay)

    # Decorator
    @async_retry(RetryConfig(max_attempts=4, base_delay=2.0))
    async def call_api():
        ...
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thread-safe jitter state
# ---------------------------------------------------------------------------

_jitter_counter: int = 0
_jitter_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Core back-off function
# ---------------------------------------------------------------------------

def jittered_backoff(
    attempt: int,
    *,
    base_delay: float = 5.0,
    max_delay: float = 120.0,
    jitter_ratio: float = 0.5,
) -> float:
    """Return a jittered exponential back-off delay (in seconds).

    The delay grows as ``base_delay * 2^(attempt-1)``, capped at *max_delay*,
    with a random jitter up to ``jitter_ratio * delay`` added.  The pseudo-random
    seed is derived from a monotonic counter XOR'd with the current nanosecond
    timestamp so even threads that call simultaneously receive different delays.

    Parameters
    ----------
    attempt:
        0-based attempt index.  attempt=0 → ``base_delay``; attempt=1 →
        ``base_delay * 2``; …
    base_delay:
        Starting delay in seconds before jitter (default: 5 s).
    max_delay:
        Hard ceiling on the delay in seconds (default: 120 s).
    jitter_ratio:
        Fraction of the computed delay to add as random jitter (default: 0.5).

    Returns
    -------
    float
        Seconds to wait before the next attempt.

    Examples::

        delays = [jittered_backoff(i) for i in range(5)]
        # e.g. [5.7, 9.3, 18.2, 36.1, 75.0]
    """
    global _jitter_counter

    with _jitter_lock:
        _jitter_counter += 1
        tick = _jitter_counter

    exponent = max(0, attempt - 1)
    delay = min(base_delay * (2 ** exponent), max_delay)

    # Deterministic-per-call seed: XOR nanosecond timestamp with counter × prime
    seed = (time.time_ns() ^ (tick * 0x9E3779B9)) & 0xFFFFFFFF
    rng = random.Random(seed)
    return delay + rng.uniform(0, jitter_ratio * delay)


# ---------------------------------------------------------------------------
# RetryConfig — single source of truth for retry policy
# ---------------------------------------------------------------------------

@dataclass
class RetryConfig:
    """Retry policy configuration.

    Attributes
    ----------
    max_attempts:
        Total number of attempts (including the first).  Set to 1 for no retries.
    base_delay:
        Base back-off delay in seconds passed to :func:`jittered_backoff`.
    max_delay:
        Maximum back-off delay cap in seconds.
    jitter_ratio:
        Fraction of the computed delay to add as random jitter.
    retryable_exceptions:
        Tuple of exception types that should trigger a retry.  Defaults to
        ``(Exception,)`` — retry on anything.
    reraise_last:
        When *True* (default), the last exception is re-raised after all
        attempts are exhausted.
    """

    max_attempts: int = 3
    base_delay: float = 5.0
    max_delay: float = 120.0
    jitter_ratio: float = 0.5
    retryable_exceptions: tuple[type[BaseException], ...] = field(
        default_factory=lambda: (Exception,)
    )
    reraise_last: bool = True


# ---------------------------------------------------------------------------
# Async retry decorator
# ---------------------------------------------------------------------------

def async_retry(
    config: RetryConfig | None = None,
    *,
    on_retry: Callable[[int, BaseException, float], None] | None = None,
) -> Callable:
    """Decorator that retries an ``async`` function according to *config*.

    Parameters
    ----------
    config:
        :class:`RetryConfig` instance.  Defaults to ``RetryConfig()`` (3 attempts).
    on_retry:
        Optional callback invoked before each sleep with
        ``(attempt_number, exception, sleep_seconds)``.  Useful for logging.

    Usage::

        @async_retry(RetryConfig(max_attempts=5, base_delay=2.0))
        async def unstable_api_call():
            ...
    """
    cfg = config or RetryConfig()

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            last_exc: BaseException | None = None
            for attempt in range(cfg.max_attempts):
                try:
                    return await fn(*args, **kwargs)
                except cfg.retryable_exceptions as exc:  # type: ignore[misc]
                    last_exc = exc
                    if attempt >= cfg.max_attempts - 1:
                        break  # exhausted

                    delay = jittered_backoff(
                        attempt,
                        base_delay=cfg.base_delay,
                        max_delay=cfg.max_delay,
                        jitter_ratio=cfg.jitter_ratio,
                    )
                    if on_retry is not None:
                        try:
                            on_retry(attempt + 1, exc, delay)
                        except Exception:  # noqa: BLE001
                            pass
                    else:
                        logger.debug(
                            "retry_utils.async_retry: attempt %d/%d failed (%s), sleeping %.1fs",
                            attempt + 1,
                            cfg.max_attempts,
                            type(exc).__name__,
                            delay,
                        )
                    await asyncio.sleep(delay)

            if cfg.reraise_last and last_exc is not None:
                raise last_exc
            return None

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Sync retry helper (simple, no decorator overhead)
# ---------------------------------------------------------------------------

def retry_sync(
    fn: Callable,
    *args,
    config: RetryConfig | None = None,
    **kwargs,
):
    """Call *fn* with *args*/*kwargs*, retrying according to *config*.

    Simpler alternative to the decorator for one-off call sites::

        result = retry_sync(requests.get, url, timeout=5)
    """
    cfg = config or RetryConfig()
    last_exc: BaseException | None = None

    for attempt in range(cfg.max_attempts):
        try:
            return fn(*args, **kwargs)
        except cfg.retryable_exceptions as exc:  # type: ignore[misc]
            last_exc = exc
            if attempt >= cfg.max_attempts - 1:
                break

            delay = jittered_backoff(
                attempt,
                base_delay=cfg.base_delay,
                max_delay=cfg.max_delay,
                jitter_ratio=cfg.jitter_ratio,
            )
            logger.debug(
                "retry_utils.retry_sync: attempt %d/%d failed (%s), sleeping %.1fs",
                attempt + 1,
                cfg.max_attempts,
                type(exc).__name__,
                delay,
            )
            time.sleep(delay)

    if cfg.reraise_last and last_exc is not None:
        raise last_exc
    return None
