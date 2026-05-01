"""Batch 96 — retry_utils and rate_limit_tracker.

Tests:
- navig.core.retry_utils (jittered_backoff, RetryConfig, async_retry, retry_sync)
- navig.core.rate_limit_tracker (RateLimitBucket, RateLimitState, parse_*, format_*)
"""

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
from navig.core.rate_limit_tracker import (
    RateLimitBucket,
    RateLimitState,
    _fmt_count,
    _fmt_seconds,
    format_rate_limit_compact,
    format_rate_limit_display,
    parse_rate_limit_headers,
)


# ===========================================================================
# jittered_backoff
# ===========================================================================


class TestJitteredBackoff:
    def test_returns_float(self):
        result = jittered_backoff(0)
        assert isinstance(result, float)

    def test_attempt_zero_at_least_base_delay(self):
        # attempt=0 → exponent=max(0,-1)=0 → base_delay*1 + jitter
        base = 5.0
        result = jittered_backoff(0, base_delay=base, jitter_ratio=0.0)
        assert result == pytest.approx(base)

    def test_attempt_one_doubles(self):
        # attempt=1 → exponent=0 → base_delay*1
        result = jittered_backoff(1, base_delay=10.0, jitter_ratio=0.0)
        assert result == pytest.approx(10.0)

    def test_attempt_two_doubles_again(self):
        # attempt=2 → exponent=1 → base_delay*2
        result = jittered_backoff(2, base_delay=10.0, jitter_ratio=0.0)
        assert result == pytest.approx(20.0)

    def test_attempt_three(self):
        # attempt=3 → exponent=2 → base_delay*4
        result = jittered_backoff(3, base_delay=5.0, jitter_ratio=0.0)
        assert result == pytest.approx(20.0)

    def test_capped_at_max_delay(self):
        result = jittered_backoff(100, base_delay=5.0, max_delay=30.0, jitter_ratio=0.0)
        assert result == pytest.approx(30.0)

    def test_jitter_adds_positive_value(self):
        results = [jittered_backoff(0, base_delay=10.0, jitter_ratio=0.5) for _ in range(10)]
        # All should be >= base_delay
        assert all(r >= 10.0 for r in results)

    def test_jitter_bounded_above(self):
        base = 10.0
        ratio = 0.5
        results = [jittered_backoff(0, base_delay=base, jitter_ratio=ratio) for _ in range(20)]
        # max = base + 0.5*base = 15
        assert all(r <= base + ratio * base + 0.01 for r in results)

    def test_jitter_zero_returns_exact(self):
        result = jittered_backoff(0, base_delay=7.0, max_delay=60.0, jitter_ratio=0.0)
        assert result == pytest.approx(7.0)

    def test_custom_base_delay(self):
        result = jittered_backoff(0, base_delay=1.0, jitter_ratio=0.0)
        assert result == pytest.approx(1.0)

    def test_positive_always(self):
        for attempt in range(5):
            assert jittered_backoff(attempt) > 0


# ===========================================================================
# RetryConfig
# ===========================================================================


class TestRetryConfig:
    def test_defaults(self):
        cfg = RetryConfig()
        assert cfg.max_attempts == 3
        assert cfg.base_delay == 5.0
        assert cfg.max_delay == 120.0
        assert cfg.jitter_ratio == 0.5
        assert cfg.reraise_last is True

    def test_default_retryable_exceptions(self):
        cfg = RetryConfig()
        assert Exception in cfg.retryable_exceptions

    def test_custom_max_attempts(self):
        cfg = RetryConfig(max_attempts=5)
        assert cfg.max_attempts == 5

    def test_custom_base_delay(self):
        cfg = RetryConfig(base_delay=1.0)
        assert cfg.base_delay == 1.0

    def test_custom_retryable_exceptions(self):
        cfg = RetryConfig(retryable_exceptions=(ValueError, TypeError))
        assert ValueError in cfg.retryable_exceptions
        assert TypeError in cfg.retryable_exceptions

    def test_reraise_last_false(self):
        cfg = RetryConfig(reraise_last=False)
        assert cfg.reraise_last is False


# ===========================================================================
# async_retry
# ===========================================================================


class TestAsyncRetry:
    async def test_succeeds_on_first_try(self):
        cfg = RetryConfig(max_attempts=3)

        @async_retry(cfg)
        async def fn():
            return 42

        assert await fn() == 42

    async def test_retries_on_exception_and_succeeds(self):
        call_count = 0

        cfg = RetryConfig(max_attempts=3, base_delay=0.001, jitter_ratio=0.0)

        @async_retry(cfg)
        async def fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("not yet")
            return "ok"

        result = await fn()
        assert result == "ok"
        assert call_count == 3

    async def test_reraises_after_max_attempts(self):
        cfg = RetryConfig(max_attempts=2, base_delay=0.001, jitter_ratio=0.0)

        @async_retry(cfg)
        async def fn():
            raise RuntimeError("always fails")

        with pytest.raises(RuntimeError, match="always fails"):
            await fn()

    async def test_does_not_retry_non_retryable(self):
        call_count = 0
        cfg = RetryConfig(
            max_attempts=3,
            base_delay=0.001,
            retryable_exceptions=(ValueError,),
        )

        @async_retry(cfg)
        async def fn():
            nonlocal call_count
            call_count += 1
            raise TypeError("wrong type")

        with pytest.raises(TypeError):
            await fn()

        # Non-retryable → should not have retried
        assert call_count == 1

    async def test_on_retry_callback_called(self):
        retry_calls = []
        cfg = RetryConfig(max_attempts=3, base_delay=0.001, jitter_ratio=0.0)

        @async_retry(cfg, on_retry=lambda attempt, exc, delay: retry_calls.append(attempt))
        async def fn():
            raise ValueError("fail")

        with pytest.raises(ValueError):
            await fn()

        assert len(retry_calls) == 2  # called before attempts 2 and 3

    async def test_reraise_false_returns_none(self):
        cfg = RetryConfig(max_attempts=2, base_delay=0.001, reraise_last=False, jitter_ratio=0.0)

        @async_retry(cfg)
        async def fn():
            raise ValueError("nope")

        result = await fn()
        assert result is None

    async def test_preserves_return_value(self):
        @async_retry()
        async def fn():
            return {"key": "value"}

        assert await fn() == {"key": "value"}

    async def test_passes_args_and_kwargs(self):
        @async_retry()
        async def fn(a, b=0):
            return a + b

        assert await fn(3, b=4) == 7


# ===========================================================================
# retry_sync
# ===========================================================================


class TestRetrySync:
    def test_succeeds_on_first_try(self):
        cfg = RetryConfig(max_attempts=3)
        result = retry_sync(lambda: 99, config=cfg)
        assert result == 99

    def test_retries_and_succeeds(self):
        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("not yet")
            return "done"

        cfg = RetryConfig(max_attempts=3, base_delay=0.001, jitter_ratio=0.0)
        result = retry_sync(fn, config=cfg)
        assert result == "done"
        assert call_count == 3

    def test_reraises_after_max_attempts(self):
        cfg = RetryConfig(max_attempts=2, base_delay=0.001, jitter_ratio=0.0)

        def fn():
            raise ValueError("always")

        with pytest.raises(ValueError, match="always"):
            retry_sync(fn, config=cfg)

    def test_reraise_false_returns_none(self):
        cfg = RetryConfig(max_attempts=1, reraise_last=False)

        def fn():
            raise ValueError("oops")

        assert retry_sync(fn, config=cfg) is None

    def test_passes_args(self):
        cfg = RetryConfig(max_attempts=1)
        result = retry_sync(lambda x, y: x + y, 3, 4, config=cfg)
        assert result == 7

    def test_passes_kwargs(self):
        def fn(a, b=0):
            return a * b

        cfg = RetryConfig(max_attempts=1)
        assert retry_sync(fn, 5, config=cfg, b=6) == 30


# ===========================================================================
# RateLimitBucket
# ===========================================================================


class TestRateLimitBucket:
    def test_used_property(self):
        b = RateLimitBucket(limit=100, remaining=60)
        assert b.used == 40

    def test_used_never_negative(self):
        b = RateLimitBucket(limit=50, remaining=80)
        assert b.used == 0  # max(0, 50-80)

    def test_usage_pct_normal(self):
        b = RateLimitBucket(limit=100, remaining=25)
        assert b.usage_pct == pytest.approx(75.0)

    def test_usage_pct_zero_limit(self):
        b = RateLimitBucket(limit=0, remaining=0)
        assert b.usage_pct == 0.0

    def test_usage_pct_fully_used(self):
        b = RateLimitBucket(limit=100, remaining=0)
        assert b.usage_pct == pytest.approx(100.0)

    def test_remaining_seconds_now_decreases(self):
        now = time.time()
        b = RateLimitBucket(reset_seconds=60.0, captured_at=now - 10.0)
        # 60 - 10 = 50 seconds remaining now
        assert b.remaining_seconds_now == pytest.approx(50.0, abs=0.5)

    def test_remaining_seconds_never_negative(self):
        b = RateLimitBucket(reset_seconds=5.0, captured_at=time.time() - 100.0)
        assert b.remaining_seconds_now == 0.0


# ===========================================================================
# RateLimitState
# ===========================================================================


class TestRateLimitState:
    def test_has_data_false_when_no_captured_at(self):
        state = RateLimitState()
        assert state.has_data is False

    def test_has_data_true_when_captured(self):
        state = RateLimitState(captured_at=time.time())
        assert state.has_data is True

    def test_age_seconds_inf_when_no_data(self):
        state = RateLimitState()
        assert state.age_seconds == float("inf")

    def test_age_seconds_approximate(self):
        state = RateLimitState(captured_at=time.time() - 5.0)
        assert 4.0 < state.age_seconds < 7.0


# ===========================================================================
# parse_rate_limit_headers
# ===========================================================================


class TestParseRateLimitHeaders:
    def test_returns_none_for_empty_headers(self):
        assert parse_rate_limit_headers({}) is None

    def test_returns_none_for_unrelated_headers(self):
        assert parse_rate_limit_headers({"content-type": "application/json"}) is None

    def test_returns_state_for_valid_headers(self):
        headers = {
            "x-ratelimit-limit-requests": "60",
            "x-ratelimit-remaining-requests": "45",
            "x-ratelimit-reset-requests": "30",
        }
        state = parse_rate_limit_headers(headers)
        assert state is not None

    def test_case_insensitive(self):
        headers = {
            "X-RateLimit-Limit-Requests": "100",
            "X-RateLimit-Remaining-Requests": "80",
        }
        state = parse_rate_limit_headers(headers)
        assert state is not None
        assert state.requests_min.limit == 100

    def test_provider_stored(self):
        headers = {"x-ratelimit-limit-requests": "60"}
        state = parse_rate_limit_headers(headers, provider="openai")
        assert state.provider == "openai"

    def test_requests_min_bucket_parsed(self):
        headers = {
            "x-ratelimit-limit-requests": "60",
            "x-ratelimit-remaining-requests": "45",
            "x-ratelimit-reset-requests": "30",
        }
        state = parse_rate_limit_headers(headers)
        assert state.requests_min.limit == 60
        assert state.requests_min.remaining == 45
        assert state.requests_min.reset_seconds == pytest.approx(30.0)

    def test_hour_bucket_parsed(self):
        headers = {
            "x-ratelimit-limit-requests-1h": "1000",
            "x-ratelimit-remaining-requests-1h": "800",
        }
        state = parse_rate_limit_headers(headers)
        assert state.requests_hour.limit == 1000
        assert state.requests_hour.remaining == 800

    def test_tokens_min_parsed(self):
        headers = {
            "x-ratelimit-limit-tokens": "40000",
            "x-ratelimit-remaining-tokens": "30000",
        }
        state = parse_rate_limit_headers(headers)
        assert state.tokens_min.limit == 40000

    def test_captured_at_set(self):
        before = time.time()
        headers = {"x-ratelimit-limit-requests": "60"}
        state = parse_rate_limit_headers(headers)
        after = time.time()
        assert before <= state.captured_at <= after

    def test_has_data_true_after_parse(self):
        headers = {"x-ratelimit-limit-requests": "60"}
        state = parse_rate_limit_headers(headers)
        assert state.has_data is True


# ===========================================================================
# _fmt_count
# ===========================================================================


class TestFmtCount:
    def test_small_number(self):
        assert _fmt_count(799) == "799"

    def test_thousands(self):
        result = _fmt_count(1500)
        assert "K" in result

    def test_millions(self):
        result = _fmt_count(2_000_000)
        assert "M" in result

    def test_exact_1k(self):
        result = _fmt_count(1000)
        assert result == "1.0K"

    def test_exact_1m(self):
        result = _fmt_count(1_000_000)
        assert result == "1.0M"


# ===========================================================================
# _fmt_seconds
# ===========================================================================


class TestFmtSeconds:
    def test_seconds_only(self):
        assert _fmt_seconds(45) == "45s"

    def test_zero_seconds(self):
        assert _fmt_seconds(0) == "0s"

    def test_one_minute_no_seconds(self):
        assert _fmt_seconds(60) == "1m"

    def test_minutes_and_seconds(self):
        assert _fmt_seconds(75) == "1m 15s"

    def test_one_hour_no_minutes(self):
        assert _fmt_seconds(3600) == "1h"

    def test_hours_and_minutes(self):
        assert _fmt_seconds(3660) == "1h 1m"

    def test_negative_returns_zero(self):
        # max(0, int(-5)) = 0
        assert _fmt_seconds(-5) == "0s"


# ===========================================================================
# format_rate_limit_display
# ===========================================================================


class TestFormatRateLimitDisplay:
    def test_no_data_message(self):
        state = RateLimitState()
        result = format_rate_limit_display(state)
        assert "No rate limit data" in result

    def test_with_data_contains_provider(self):
        headers = {
            "x-ratelimit-limit-requests": "60",
            "x-ratelimit-remaining-requests": "30",
            "x-ratelimit-reset-requests": "20",
        }
        state = parse_rate_limit_headers(headers, provider="myai")
        result = format_rate_limit_display(state)
        assert "Myai" in result or "myai" in result.lower()

    def test_with_data_multiline(self):
        headers = {"x-ratelimit-limit-requests": "60"}
        state = parse_rate_limit_headers(headers)
        result = format_rate_limit_display(state)
        assert "\n" in result

    def test_high_usage_shows_warning(self):
        headers = {
            "x-ratelimit-limit-requests": "100",
            "x-ratelimit-remaining-requests": "5",
            "x-ratelimit-reset-requests": "30",
        }
        state = parse_rate_limit_headers(headers)
        result = format_rate_limit_display(state)
        assert "⚠" in result or "warning" in result.lower() or "%" in result


# ===========================================================================
# format_rate_limit_compact
# ===========================================================================


class TestFormatRateLimitCompact:
    def test_no_data_returns_message(self):
        state = RateLimitState()
        result = format_rate_limit_compact(state)
        assert "No rate limit data" in result

    def test_with_rpm_data(self):
        headers = {
            "x-ratelimit-limit-requests": "60",
            "x-ratelimit-remaining-requests": "45",
        }
        state = parse_rate_limit_headers(headers)
        result = format_rate_limit_compact(state)
        assert "RPM" in result

    def test_with_tpm_data(self):
        headers = {
            "x-ratelimit-limit-tokens": "40000",
            "x-ratelimit-remaining-tokens": "30000",
        }
        state = parse_rate_limit_headers(headers)
        result = format_rate_limit_compact(state)
        assert "TPM" in result

    def test_compact_is_single_line(self):
        headers = {
            "x-ratelimit-limit-requests": "60",
            "x-ratelimit-remaining-requests": "30",
        }
        state = parse_rate_limit_headers(headers)
        result = format_rate_limit_compact(state)
        assert "\n" not in result
