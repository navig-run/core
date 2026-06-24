"""
Batch 96 -- tests for rate_limit_tracker, thresholds, retry_utils
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# =============================================================================
# thresholds (simplest — read-only registry)
# =============================================================================


class TestThresholdDataclass:
    def test_frozen(self):
        from navig.core.thresholds import Threshold
        t = Threshold(warn_pct=80.0, crit_pct=95.0)
        with pytest.raises((AttributeError, TypeError)):
            t.warn_pct = 50.0  # frozen=True

    def test_fields(self):
        from navig.core.thresholds import Threshold
        t = Threshold(warn_pct=75.0, crit_pct=90.0)
        assert t.warn_pct == 75.0
        assert t.crit_pct == 90.0


class TestThresholdResolve:
    def test_known_metric_cpu(self):
        from navig.core.thresholds import resolve
        t = resolve("cpu_usage")
        assert t.warn_pct == 75.0
        assert t.crit_pct == 90.0

    def test_known_metric_memory(self):
        from navig.core.thresholds import resolve
        t = resolve("memory_usage")
        assert t.warn_pct == 80.0
        assert t.crit_pct == 95.0

    def test_known_metric_disk_io(self):
        from navig.core.thresholds import resolve
        t = resolve("disk_io")
        assert t.warn_pct == 70.0

    def test_unknown_metric_returns_defaults(self):
        from navig.core.thresholds import resolve, DEFAULTS
        t = resolve("nonexistent_metric")
        assert t == DEFAULTS

    def test_defaults_values(self):
        from navig.core.thresholds import DEFAULTS
        assert DEFAULTS.warn_pct == 80.0
        assert DEFAULTS.crit_pct == 95.0

    def test_registry_has_expected_keys(self):
        from navig.core.thresholds import REGISTRY
        for key in ("cpu_usage", "memory_usage", "disk_usage", "error_rate"):
            assert key in REGISTRY

    def test_error_rate_low_thresholds(self):
        from navig.core.thresholds import resolve
        t = resolve("error_rate")
        assert t.warn_pct < 20.0  # much lower than defaults


# =============================================================================
# rate_limit_tracker
# =============================================================================


class TestRateLimitBucket:
    def _bucket(self, limit=100, remaining=40, reset_seconds=30.0):
        from navig.core.rate_limit_tracker import RateLimitBucket
        return RateLimitBucket(
            limit=limit,
            remaining=remaining,
            reset_seconds=reset_seconds,
            captured_at=time.time(),
        )

    def test_used(self):
        b = self._bucket(100, 40)
        assert b.used == 60

    def test_used_clamps_to_zero_if_remaining_exceeds_limit(self):
        from navig.core.rate_limit_tracker import RateLimitBucket
        b = RateLimitBucket(limit=10, remaining=15, reset_seconds=5, captured_at=time.time())
        assert b.used == 0

    def test_usage_pct(self):
        b = self._bucket(100, 40)
        assert b.usage_pct == pytest.approx(60.0)

    def test_usage_pct_zero_limit(self):
        from navig.core.rate_limit_tracker import RateLimitBucket
        b = RateLimitBucket(limit=0, remaining=0, reset_seconds=0, captured_at=time.time())
        assert b.usage_pct == 0.0

    def test_remaining_seconds_decreases_over_time(self):
        from navig.core.rate_limit_tracker import RateLimitBucket
        b = RateLimitBucket(limit=100, remaining=50, reset_seconds=60.0,
                            captured_at=time.time() - 10.0)
        # 10 seconds ago, 60s reset → ~50s remaining now
        assert b.remaining_seconds_now < 60.0

    def test_remaining_seconds_never_negative(self):
        from navig.core.rate_limit_tracker import RateLimitBucket
        b = RateLimitBucket(limit=100, remaining=50, reset_seconds=1.0,
                            captured_at=time.time() - 100.0)
        assert b.remaining_seconds_now == 0.0


class TestRateLimitState:
    def _state(self, **kw):
        from navig.core.rate_limit_tracker import RateLimitState
        return RateLimitState(captured_at=time.time(), **kw)

    def test_has_data_when_captured(self):
        s = self._state()
        assert s.has_data is True

    def test_has_data_false_when_zero(self):
        from navig.core.rate_limit_tracker import RateLimitState
        s = RateLimitState(captured_at=0.0)
        assert s.has_data is False

    def test_age_seconds_reasonable(self):
        s = self._state()
        assert 0 <= s.age_seconds < 5.0

    def test_age_seconds_infinity_when_no_data(self):
        from navig.core.rate_limit_tracker import RateLimitState
        s = RateLimitState(captured_at=0.0)
        assert s.age_seconds == float("inf")


class TestParseRateLimitHeaders:
    def _headers(self, **kw):
        base = {
            "x-ratelimit-limit-requests": "60",
            "x-ratelimit-remaining-requests": "45",
            "x-ratelimit-reset-requests": "30",
            "x-ratelimit-limit-tokens": "90000",
            "x-ratelimit-remaining-tokens": "88000",
            "x-ratelimit-reset-tokens": "15",
        }
        base.update(kw)
        return base

    def test_returns_none_when_no_rl_headers(self):
        from navig.core.rate_limit_tracker import parse_rate_limit_headers
        result = parse_rate_limit_headers({"content-type": "application/json"})
        assert result is None

    def test_parses_requests_min(self):
        from navig.core.rate_limit_tracker import parse_rate_limit_headers
        state = parse_rate_limit_headers(self._headers())
        assert state is not None
        assert state.requests_min.limit == 60
        assert state.requests_min.remaining == 45

    def test_parses_tokens_min(self):
        from navig.core.rate_limit_tracker import parse_rate_limit_headers
        state = parse_rate_limit_headers(self._headers())
        assert state.tokens_min.limit == 90000
        assert state.tokens_min.remaining == 88000

    def test_case_insensitive_headers(self):
        from navig.core.rate_limit_tracker import parse_rate_limit_headers
        headers = {k.upper(): v for k, v in self._headers().items()}
        state = parse_rate_limit_headers(headers)
        assert state is not None
        assert state.requests_min.limit == 60

    def test_stores_provider(self):
        from navig.core.rate_limit_tracker import parse_rate_limit_headers
        state = parse_rate_limit_headers(self._headers(), provider="openai")
        assert state.provider == "openai"

    def test_hour_bucket_parsed(self):
        from navig.core.rate_limit_tracker import parse_rate_limit_headers
        headers = self._headers()
        headers["x-ratelimit-limit-requests-1h"] = "3600"
        headers["x-ratelimit-remaining-requests-1h"] = "3500"
        state = parse_rate_limit_headers(headers)
        assert state.requests_hour.limit == 3600
        assert state.requests_hour.remaining == 3500

    def test_invalid_header_values_default_to_zero(self):
        from navig.core.rate_limit_tracker import parse_rate_limit_headers
        headers = self._headers(**{"x-ratelimit-limit-requests": "invalid"})
        state = parse_rate_limit_headers(headers)
        assert state.requests_min.limit == 0


class TestRateLimitFormatHelpers:
    def test_fmt_count_small(self):
        from navig.core.rate_limit_tracker import _fmt_count
        assert _fmt_count(799) == "799"

    def test_fmt_count_thousands(self):
        from navig.core.rate_limit_tracker import _fmt_count
        assert _fmt_count(33600) == "33.6K"

    def test_fmt_count_millions(self):
        from navig.core.rate_limit_tracker import _fmt_count
        result = _fmt_count(7_999_856)
        assert result.endswith("M")

    def test_fmt_seconds_under_minute(self):
        from navig.core.rate_limit_tracker import _fmt_seconds
        assert _fmt_seconds(58) == "58s"

    def test_fmt_seconds_minutes(self):
        from navig.core.rate_limit_tracker import _fmt_seconds
        result = _fmt_seconds(134)
        assert "m" in result

    def test_fmt_seconds_hours(self):
        from navig.core.rate_limit_tracker import _fmt_seconds
        result = _fmt_seconds(3661)
        assert "h" in result

    def test_bar_full(self):
        from navig.core.rate_limit_tracker import _bar
        result = _bar(100.0)
        assert "[" in result
        assert "█" in result

    def test_bar_empty(self):
        from navig.core.rate_limit_tracker import _bar
        result = _bar(0.0)
        assert "░" in result


# =============================================================================
# retry_utils
# =============================================================================


class TestJitteredBackoff:
    def test_returns_float(self):
        from navig.core.retry_utils import jittered_backoff
        delay = jittered_backoff(0)
        assert isinstance(delay, float)

    def test_within_bounds(self):
        from navig.core.retry_utils import jittered_backoff
        for attempt in range(10):
            delay = jittered_backoff(attempt, base_delay=1.0, max_delay=60.0)
            assert delay >= 1.0
            assert delay <= 60.0 * 1.5  # allow jitter overflow

    def test_increases_with_attempt(self):
        from navig.core.retry_utils import jittered_backoff
        d0 = jittered_backoff(0, base_delay=1.0, jitter_ratio=0.0)
        d3 = jittered_backoff(3, base_delay=1.0, jitter_ratio=0.0)
        assert d3 > d0

    def test_zero_jitter_deterministic_base(self):
        from navig.core.retry_utils import jittered_backoff
        d = jittered_backoff(0, base_delay=5.0, jitter_ratio=0.0)
        assert d == pytest.approx(5.0, abs=0.01)

    def test_max_delay_respected_with_zero_jitter(self):
        from navig.core.retry_utils import jittered_backoff
        d = jittered_backoff(100, base_delay=1.0, max_delay=10.0, jitter_ratio=0.0)
        assert d == pytest.approx(10.0)

    def test_thread_safety_no_crash(self):
        import threading
        from navig.core.retry_utils import jittered_backoff
        errors = []

        def call():
            try:
                jittered_backoff(1)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=call) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


class TestRetryConfig:
    def test_defaults(self):
        from navig.core.retry_utils import RetryConfig
        cfg = RetryConfig()
        assert cfg.max_attempts == 3
        assert cfg.base_delay == 5.0
        assert cfg.reraise_last is True
        assert Exception in cfg.retryable_exceptions

    def test_custom_values(self):
        from navig.core.retry_utils import RetryConfig
        cfg = RetryConfig(max_attempts=5, base_delay=1.0, reraise_last=False)
        assert cfg.max_attempts == 5
        assert cfg.base_delay == 1.0
        assert cfg.reraise_last is False


class TestAsyncRetry:
    def test_success_on_first_attempt(self):
        from navig.core.retry_utils import async_retry, RetryConfig

        @async_retry(RetryConfig(max_attempts=3, base_delay=0.001))
        async def fn():
            return "ok"

        result = asyncio.run(fn())
        assert result == "ok"

    def test_retries_and_succeeds(self):
        from navig.core.retry_utils import async_retry, RetryConfig
        call_count = [0]

        @async_retry(RetryConfig(max_attempts=3, base_delay=0.001, max_delay=0.001))
        async def fn():
            call_count[0] += 1
            if call_count[0] < 3:
                raise ValueError("fail")
            return "done"

        with patch("asyncio.sleep", new=AsyncMock()):
            result = asyncio.run(fn())
        assert result == "done"
        assert call_count[0] == 3

    def test_reraises_after_exhausted(self):
        from navig.core.retry_utils import async_retry, RetryConfig

        @async_retry(RetryConfig(max_attempts=2, base_delay=0.001, max_delay=0.001))
        async def fn():
            raise RuntimeError("always fails")

        with patch("asyncio.sleep", new=AsyncMock()):
            with pytest.raises(RuntimeError, match="always fails"):
                asyncio.run(fn())

    def test_no_reraise_when_disabled(self):
        from navig.core.retry_utils import async_retry, RetryConfig

        @async_retry(RetryConfig(max_attempts=2, base_delay=0.001, max_delay=0.001,
                                  reraise_last=False))
        async def fn():
            raise ValueError("should not propagate")

        with patch("asyncio.sleep", new=AsyncMock()):
            result = asyncio.run(fn())  # should not raise
        assert result is None

    def test_non_retryable_exception_bubbles_immediately(self):
        from navig.core.retry_utils import async_retry, RetryConfig
        call_count = [0]

        @async_retry(RetryConfig(max_attempts=3, base_delay=0.001,
                                  retryable_exceptions=(ValueError,)))
        async def fn():
            call_count[0] += 1
            raise TypeError("not retryable")  # not in retryable_exceptions

        with pytest.raises(TypeError):
            asyncio.run(fn())
        assert call_count[0] == 1  # only one attempt

    def test_on_retry_callback_called(self):
        from navig.core.retry_utils import async_retry, RetryConfig
        calls = []

        def on_retry(attempt, exc, delay):
            calls.append((attempt, type(exc).__name__))

        @async_retry(RetryConfig(max_attempts=3, base_delay=0.001, max_delay=0.001),
                     on_retry=on_retry)
        async def fn():
            raise ValueError("oops")

        with patch("asyncio.sleep", new=AsyncMock()):
            with pytest.raises(ValueError):
                asyncio.run(fn())
        # on_retry is called for each intermediate attempt
        assert len(calls) >= 1
        assert calls[0][1] == "ValueError"
