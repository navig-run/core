"""Tests for navig/core/retry_utils.py — batch 88."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.core.retry_utils import (
    RetryConfig,
    async_retry,
    jittered_backoff,
    retry_sync,
)


# ---------------------------------------------------------------------------
# jittered_backoff
# ---------------------------------------------------------------------------

class TestJitteredBackoff:
    def test_returns_float(self):
        result = jittered_backoff(0)
        assert isinstance(result, float)

    def test_attempt_0_is_base_delay_plus_jitter(self):
        delay = jittered_backoff(0, base_delay=5.0, jitter_ratio=0.5)
        assert 5.0 <= delay <= 7.5

    def test_attempt_1_doubles(self):
        delay = jittered_backoff(1, base_delay=5.0, jitter_ratio=0.0)
        assert delay == 5.0  # attempt 1: base * 2^0 = base

    def test_attempt_2_is_2x_base(self):
        delay = jittered_backoff(2, base_delay=5.0, jitter_ratio=0.0)
        assert delay == 10.0  # 5 * 2^1

    def test_delays_capped_at_max(self):
        delay = jittered_backoff(100, base_delay=5.0, max_delay=30.0, jitter_ratio=0.0)
        assert delay == 30.0

    def test_jitter_ratio_zero_no_extra(self):
        delay = jittered_backoff(0, base_delay=10.0, jitter_ratio=0.0)
        assert delay == 10.0

    def test_multiple_calls_differ(self):
        # With jitter enabled, calls should differ (by high probability)
        delays = [jittered_backoff(1, base_delay=5.0, jitter_ratio=0.5) for _ in range(5)]
        assert len(set(delays)) > 1

    def test_negative_attempt_treated_as_0(self):
        delay = jittered_backoff(-1, base_delay=5.0, jitter_ratio=0.0)
        assert delay == 5.0


# ---------------------------------------------------------------------------
# RetryConfig
# ---------------------------------------------------------------------------

class TestRetryConfig:
    def test_defaults(self):
        cfg = RetryConfig()
        assert cfg.max_attempts == 3
        assert cfg.base_delay == 5.0
        assert cfg.max_delay == 120.0
        assert cfg.jitter_ratio == 0.5
        assert cfg.reraise_last is True

    def test_custom_values(self):
        cfg = RetryConfig(max_attempts=5, base_delay=1.0, max_delay=10.0)
        assert cfg.max_attempts == 5
        assert cfg.base_delay == 1.0

    def test_retryable_exceptions_default(self):
        cfg = RetryConfig()
        assert Exception in cfg.retryable_exceptions

    def test_retryable_exceptions_custom(self):
        cfg = RetryConfig(retryable_exceptions=(ValueError, RuntimeError))
        assert ValueError in cfg.retryable_exceptions
        assert RuntimeError in cfg.retryable_exceptions


# ---------------------------------------------------------------------------
# async_retry decorator
# ---------------------------------------------------------------------------

class TestAsyncRetry:
    @pytest.mark.asyncio
    async def test_success_on_first_try(self):
        @async_retry(RetryConfig(max_attempts=3))
        async def fn():
            return "ok"

        result = await fn()
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_retries_on_failure_then_succeeds(self):
        call_count = 0

        @async_retry(RetryConfig(max_attempts=3, base_delay=0.001))
        async def fn():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ValueError("transient")
            return "success"

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await fn()

        assert result == "success"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_reraises_after_max_attempts(self):
        call_count = 0

        @async_retry(RetryConfig(max_attempts=3, base_delay=0.001))
        async def fn():
            nonlocal call_count
            call_count += 1
            raise RuntimeError("always fails")

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(RuntimeError, match="always fails"):
                await fn()

        assert call_count == 3

    @pytest.mark.asyncio
    async def test_no_reraise_returns_none(self):
        @async_retry(RetryConfig(max_attempts=2, base_delay=0.001, reraise_last=False))
        async def fn():
            raise ValueError("x")

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await fn()

        assert result is None

    @pytest.mark.asyncio
    async def test_non_retryable_exception_bubbles_immediately(self):
        call_count = 0

        @async_retry(RetryConfig(max_attempts=5, base_delay=0.001, retryable_exceptions=(ValueError,)))
        async def fn():
            nonlocal call_count
            call_count += 1
            raise TypeError("not retryable")

        with pytest.raises(TypeError):
            await fn()

        assert call_count == 1  # only tried once — not retried

    @pytest.mark.asyncio
    async def test_on_retry_callback_called(self):
        callback = MagicMock()

        @async_retry(RetryConfig(max_attempts=3, base_delay=0.001), on_retry=callback)
        async def fn():
            raise ValueError("retry me")

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ValueError):
                await fn()

        assert callback.call_count == 2  # called before each retry (not the last failed attempt)

    @pytest.mark.asyncio
    async def test_default_config_used_when_none(self):
        @async_retry()
        async def fn():
            return 42

        result = await fn()
        assert result == 42

    @pytest.mark.asyncio
    async def test_sleeps_between_retries(self):
        @async_retry(RetryConfig(max_attempts=3, base_delay=0.001))
        async def fn():
            raise ValueError("fail")

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(ValueError):
                await fn()

        assert mock_sleep.await_count == 2  # sleep between attempt 1→2 and 2→3


# ---------------------------------------------------------------------------
# retry_sync
# ---------------------------------------------------------------------------

class TestRetrySync:
    def test_success_on_first_try(self):
        fn = MagicMock(return_value="done")
        result = retry_sync(fn, config=RetryConfig(max_attempts=3))
        assert result == "done"
        fn.assert_called_once()

    def test_retries_on_failure_then_succeeds(self):
        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ValueError("transient")
            return "ok"

        with patch("time.sleep"):
            result = retry_sync(fn, config=RetryConfig(max_attempts=3, base_delay=0.001))

        assert result == "ok"
        assert call_count == 2

    def test_reraises_after_max_attempts(self):
        fn = MagicMock(side_effect=RuntimeError("always"))

        with patch("time.sleep"):
            with pytest.raises(RuntimeError, match="always"):
                retry_sync(fn, config=RetryConfig(max_attempts=3, base_delay=0.001))

        assert fn.call_count == 3

    def test_no_reraise_returns_none(self):
        fn = MagicMock(side_effect=ValueError("x"))

        with patch("time.sleep"):
            result = retry_sync(fn, config=RetryConfig(max_attempts=2, base_delay=0.001, reraise_last=False))

        assert result is None

    def test_default_config_used_when_none(self):
        fn = MagicMock(return_value=99)
        result = retry_sync(fn)
        assert result == 99

    def test_passes_args_and_kwargs(self):
        fn = MagicMock(return_value="val")
        retry_sync(fn, "a", "b", key="c", config=RetryConfig(max_attempts=1))
        fn.assert_called_once_with("a", "b", key="c")

    def test_non_retryable_exception_bubbles_immediately(self):
        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            raise TypeError("not retryable")

        cfg = RetryConfig(max_attempts=5, retryable_exceptions=(ValueError,))
        with pytest.raises(TypeError):
            retry_sync(fn, config=cfg)

        assert call_count == 1
