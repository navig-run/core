"""Tests for navig.core.retry_utils.

Covers:
  - jittered_backoff() — range, bounds, non-negative, jitter entropy
  - RetryConfig defaults
  - async_retry decorator — succeeds, retries on transient failure, re-raises last
  - retry_sync() — same flow as async
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from navig.core.retry_utils import RetryConfig, async_retry, jittered_backoff, retry_sync


# ---------------------------------------------------------------------------
# jittered_backoff
# ---------------------------------------------------------------------------

class TestJitteredBackoff:
    def test_returns_positive(self):
        for attempt in range(6):
            assert jittered_backoff(attempt) > 0

    def test_respects_max_delay(self):
        for attempt in range(10):
            assert jittered_backoff(attempt, base_delay=5.0, max_delay=120.0) <= 120.0 * 1.5 + 1

    def test_grows_with_attempt(self):
        # The base (no jitter) grows; median should increase.
        d0 = jittered_backoff(0, jitter_ratio=0)
        d3 = jittered_backoff(3, jitter_ratio=0)
        assert d3 >= d0

    def test_jitter_ratio_zero_is_deterministic(self):
        d1 = jittered_backoff(2, jitter_ratio=0)
        d2 = jittered_backoff(2, jitter_ratio=0)
        # Both should be exactly base_delay * 2^(attempt-1) = 5 * 2 = 10
        assert d1 == pytest.approx(10.0)
        assert d2 == pytest.approx(10.0)

    def test_thread_safe_distinct_seeds(self):
        """Concurrent calls should not produce identical results due to counter."""
        import threading

        results: list[float] = []
        lock = threading.Lock()

        def worker():
            val = jittered_backoff(3, jitter_ratio=0.9)
            with lock:
                results.append(val)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # At least some variance expected across 20 concurrent calls
        assert len(set(results)) > 1

    def test_attempt_zero_gives_base_delay(self):
        d = jittered_backoff(0, base_delay=7.0, jitter_ratio=0)
        assert d == pytest.approx(7.0)


# ---------------------------------------------------------------------------
# RetryConfig
# ---------------------------------------------------------------------------

class TestRetryConfig:
    def test_defaults(self):
        cfg = RetryConfig()
        assert cfg.max_attempts == 3
        assert cfg.base_delay == pytest.approx(5.0)
        assert cfg.max_delay == pytest.approx(120.0)
        assert cfg.reraise_last is True

    def test_custom_values(self):
        cfg = RetryConfig(max_attempts=5, base_delay=2.0, max_delay=60.0)
        assert cfg.max_attempts == 5
        assert cfg.base_delay == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# async_retry decorator
# ---------------------------------------------------------------------------

class TestAsyncRetry:
    @pytest.mark.asyncio
    async def test_success_first_attempt(self):
        calls = []

        @async_retry(RetryConfig(max_attempts=3))
        async def fn():
            calls.append(1)
            return "ok"

        result = await fn()
        assert result == "ok"
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_retries_on_transient_failure(self):
        attempt_count = [0]

        @async_retry(RetryConfig(max_attempts=3, base_delay=0.01, max_delay=0.05))
        async def fn():
            attempt_count[0] += 1
            if attempt_count[0] < 3:
                raise ConnectionError("transient")
            return "done"

        result = await fn()
        assert result == "done"
        assert attempt_count[0] == 3

    @pytest.mark.asyncio
    async def test_reraises_after_exhaustion(self):
        @async_retry(RetryConfig(max_attempts=2, base_delay=0.01, max_delay=0.05))
        async def fn():
            raise ValueError("always fails")

        with pytest.raises(ValueError, match="always fails"):
            await fn()

    @pytest.mark.asyncio
    async def test_on_retry_callback_called(self):
        retried: list[tuple] = []

        def on_retry(attempt, exc, delay):
            retried.append((attempt, type(exc).__name__, delay))

        @async_retry(RetryConfig(max_attempts=3, base_delay=0.01), on_retry=on_retry)
        async def fn():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            await fn()

        assert len(retried) == 2  # 3 attempts → 2 on_retry calls

    @pytest.mark.asyncio
    async def test_no_retry_for_excluded_exception(self):
        """When retryable_exceptions is restricted, other exceptions propagate immediately."""
        attempt_count = [0]

        @async_retry(
            RetryConfig(
                max_attempts=5,
                retryable_exceptions=(ConnectionError,),
                base_delay=0.01,
            )
        )
        async def fn():
            attempt_count[0] += 1
            raise TypeError("not retryable")

        with pytest.raises(TypeError):
            await fn()

        assert attempt_count[0] == 1  # no retries


# ---------------------------------------------------------------------------
# retry_sync
# ---------------------------------------------------------------------------

class TestRetrySync:
    def test_success_on_first(self):
        result = retry_sync(lambda: 42, config=RetryConfig(max_attempts=3))
        assert result == 42

    def test_retries_and_succeeds(self):
        calls = [0]

        def fn():
            calls[0] += 1
            if calls[0] < 2:
                raise IOError("transient")
            return "ok"

        result = retry_sync(fn, config=RetryConfig(max_attempts=3, base_delay=0.01))
        assert result == "ok"
        assert calls[0] == 2

    def test_raises_after_exhaustion(self):
        def fn():
            raise RuntimeError("gone")

        with pytest.raises(RuntimeError):
            retry_sync(fn, config=RetryConfig(max_attempts=2, base_delay=0.01))
