"""Hermetic unit tests for navig.core.retry_utils."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from navig.core.retry_utils import (
    RetryConfig,
    async_retry,
    jittered_backoff,
    retry_sync,
)

# ---------------------------------------------------------------------------
# RetryConfig defaults
# ---------------------------------------------------------------------------


class TestRetryConfigDefaults:
    def test_max_attempts(self):
        assert RetryConfig().max_attempts == 3

    def test_base_delay(self):
        assert RetryConfig().base_delay == 5.0

    def test_max_delay(self):
        assert RetryConfig().max_delay == 120.0

    def test_jitter_ratio(self):
        assert RetryConfig().jitter_ratio == 0.5

    def test_reraise_last_true(self):
        assert RetryConfig().reraise_last is True

    def test_retryable_exceptions_default(self):
        assert RetryConfig().retryable_exceptions == (Exception,)

    def test_custom_values(self):
        cfg = RetryConfig(max_attempts=5, base_delay=2.0, max_delay=60.0)
        assert cfg.max_attempts == 5
        assert cfg.base_delay == 2.0
        assert cfg.max_delay == 60.0


# ---------------------------------------------------------------------------
# jittered_backoff
# ---------------------------------------------------------------------------


class TestJitteredBackoff:
    def test_attempt_0_base_delay(self):
        # attempt=0 → exponent=max(0,-1)=0 → delay = base_delay=5.0 (no doubling)
        delay = jittered_backoff(0, base_delay=5.0, jitter_ratio=0.0)
        assert delay == pytest.approx(5.0)

    def test_attempt_1_doubles(self):
        # attempt=1 → exponent=0 → delay = base_delay * 1 = 5.0
        delay = jittered_backoff(1, base_delay=5.0, jitter_ratio=0.0)
        assert delay == pytest.approx(5.0)

    def test_attempt_2_doubles(self):
        # attempt=2 → exponent=1 → delay = base_delay * 2 = 10.0
        delay = jittered_backoff(2, base_delay=5.0, jitter_ratio=0.0)
        assert delay == pytest.approx(10.0)

    def test_attempt_3_quadruples(self):
        # attempt=3 → exponent=2 → delay = base_delay * 4 = 20.0
        delay = jittered_backoff(3, base_delay=5.0, jitter_ratio=0.0)
        assert delay == pytest.approx(20.0)

    def test_capped_by_max_delay(self):
        delay = jittered_backoff(20, base_delay=5.0, max_delay=30.0, jitter_ratio=0.0)
        assert delay == pytest.approx(30.0)

    def test_jitter_within_range(self):
        # With jitter, delay should be between base and base*(1+jitter_ratio)
        for attempt in range(5):
            delay = jittered_backoff(attempt, base_delay=5.0, max_delay=120.0, jitter_ratio=0.5)
            assert delay >= 5.0
            assert delay <= 120.0 * 1.5  # max + jitter headroom

    def test_returns_float(self):
        assert isinstance(jittered_backoff(0), float)

    def test_unique_values_across_calls(self):
        # Seeding ensures different jitter per call
        delays = [jittered_backoff(0, base_delay=5.0, jitter_ratio=0.5) for _ in range(10)]
        # Not all identical (jitter should vary)
        assert len(set(delays)) > 1


# ---------------------------------------------------------------------------
# retry_sync
# ---------------------------------------------------------------------------


class TestRetrySync:
    def test_success_first_try(self):
        fn = MagicMock(return_value=42)
        cfg = RetryConfig(max_attempts=3, base_delay=0.0)
        result = retry_sync(fn, config=cfg)
        assert result == 42
        assert fn.call_count == 1

    def test_retries_on_exception(self):
        calls = []

        def fn():
            calls.append(1)
            if len(calls) < 3:
                raise ValueError("fail")
            return "ok"

        with patch("navig.core.retry_utils.time.sleep"):
            result = retry_sync(fn, config=RetryConfig(max_attempts=3, base_delay=0.0))
        assert result == "ok"
        assert len(calls) == 3

    def test_reraises_after_exhausted(self):
        fn = MagicMock(side_effect=RuntimeError("boom"))
        cfg = RetryConfig(max_attempts=2, base_delay=0.0, reraise_last=True)
        with patch("navig.core.retry_utils.time.sleep"):
            with pytest.raises(RuntimeError, match="boom"):
                retry_sync(fn, config=cfg)

    def test_no_reraise_returns_none(self):
        fn = MagicMock(side_effect=RuntimeError("boom"))
        cfg = RetryConfig(max_attempts=2, base_delay=0.0, reraise_last=False)
        with patch("navig.core.retry_utils.time.sleep"):
            result = retry_sync(fn, config=cfg)
        assert result is None

    def test_specific_exception_type(self):
        calls = []

        def fn():
            calls.append(1)
            if len(calls) < 2:
                raise ValueError("retry me")
            return "done"

        cfg = RetryConfig(
            max_attempts=3,
            base_delay=0.0,
            retryable_exceptions=(ValueError,),
        )
        with patch("navig.core.retry_utils.time.sleep"):
            result = retry_sync(fn, config=cfg)
        assert result == "done"

    def test_non_retryable_exception_propagates(self):
        def fn():
            raise KeyError("not retryable")

        cfg = RetryConfig(
            max_attempts=3,
            base_delay=0.0,
            retryable_exceptions=(ValueError,),
        )
        with pytest.raises(KeyError):
            retry_sync(fn, config=cfg)

    def test_passes_args_to_fn(self):
        def fn(x, y):
            return x + y

        result = retry_sync(fn, 3, 4, config=RetryConfig(max_attempts=1))
        assert result == 7

    def test_passes_kwargs_to_fn(self):
        def fn(a, b=10):
            return a + b

        result = retry_sync(fn, 5, config=RetryConfig(max_attempts=1), b=20)
        assert result == 25


# ---------------------------------------------------------------------------
# async_retry decorator
# ---------------------------------------------------------------------------


class TestAsyncRetry:
    def test_success_first_attempt(self):
        @async_retry(RetryConfig(max_attempts=3, base_delay=0.0))
        async def fn():
            return "hello"

        result = asyncio.run(fn())
        assert result == "hello"

    def test_retries_then_succeeds(self):
        calls = []

        @async_retry(RetryConfig(max_attempts=3, base_delay=0.0))
        async def fn():
            calls.append(1)
            if len(calls) < 3:
                raise ValueError("try again")
            return "success"

        async def mock_sleep(*_a, **_kw):
            pass

        with patch("navig.core.retry_utils.asyncio.sleep", side_effect=mock_sleep):
            result = asyncio.run(fn())
        assert result == "success"

    def test_exhausted_reraises(self):
        @async_retry(RetryConfig(max_attempts=2, base_delay=0.0, reraise_last=True))
        async def fn():
            raise RuntimeError("always fail")

        async def mock_sleep(*_a, **_kw):
            pass

        with patch("navig.core.retry_utils.asyncio.sleep", side_effect=mock_sleep):
            with pytest.raises(RuntimeError):
                asyncio.run(fn())

    def test_on_retry_callback_called(self):
        calls_made = []

        def on_retry(attempt, exc, delay):
            calls_made.append((attempt, type(exc).__name__))

        @async_retry(RetryConfig(max_attempts=3, base_delay=0.0), on_retry=on_retry)
        async def fn():
            raise ValueError("fail")

        async def mock_sleep(*_a, **_kw):
            pass

        with patch("navig.core.retry_utils.asyncio.sleep", side_effect=mock_sleep):
            with pytest.raises(ValueError):
                asyncio.run(fn())
        assert len(calls_made) == 2  # 2 retries before exhaust

    def test_default_config_used_when_none(self):
        @async_retry()
        async def fn():
            return 99

        result = asyncio.run(fn())
        assert result == 99
