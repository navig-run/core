"""
Batch 74: hermetic unit tests for
  - navig/core/rate_limit_tracker.py  (parsing, formatting, data structures)
  - navig/core/thresholds.py          (Threshold, resolve)
  - navig/core/tokens.py              (estimate_tokens)
"""

from __future__ import annotations

import time

import pytest


# ---------------------------------------------------------------------------
# navig/core/tokens.py
# ---------------------------------------------------------------------------

class TestEstimateTokens:
    def test_empty_string_returns_zero(self) -> None:
        from navig.core.tokens import estimate_tokens
        assert estimate_tokens("") == 0

    def test_basic_estimation(self) -> None:
        from navig.core.tokens import estimate_tokens
        # 8 chars / 4.0 = 2
        assert estimate_tokens("abcdefgh") == 2

    def test_minimum_one_for_nonempty(self) -> None:
        from navig.core.tokens import estimate_tokens
        assert estimate_tokens("x") == 1

    def test_custom_ratio(self) -> None:
        from navig.core.tokens import estimate_tokens
        # 7 chars / 3.5 = 2
        assert estimate_tokens("1234567", chars_per_token=3.5) == 2

    def test_large_text(self) -> None:
        from navig.core.tokens import estimate_tokens
        text = "a" * 4000
        assert estimate_tokens(text) == 1000


# ---------------------------------------------------------------------------
# navig/core/thresholds.py
# ---------------------------------------------------------------------------

class TestThreshold:
    def test_frozen_dataclass(self) -> None:
        from navig.core.thresholds import Threshold
        t = Threshold(warn_pct=80.0, crit_pct=95.0)
        with pytest.raises((AttributeError, TypeError)):
            t.warn_pct = 70.0

    def test_fields(self) -> None:
        from navig.core.thresholds import Threshold
        t = Threshold(warn_pct=75.0, crit_pct=90.0)
        assert t.warn_pct == 75.0
        assert t.crit_pct == 90.0


class TestThresholdResolve:
    def test_resolves_known_metric(self) -> None:
        from navig.core.thresholds import resolve, REGISTRY
        t = resolve("cpu_usage")
        assert t == REGISTRY["cpu_usage"]

    def test_falls_back_to_defaults(self) -> None:
        from navig.core.thresholds import resolve, DEFAULTS
        t = resolve("nonexistent_metric_xyz")
        assert t == DEFAULTS

    def test_all_registry_metrics_resolvable(self) -> None:
        from navig.core.thresholds import resolve, REGISTRY
        for name in REGISTRY:
            t = resolve(name)
            assert 0 < t.warn_pct < t.crit_pct <= 100


# ---------------------------------------------------------------------------
# navig/core/rate_limit_tracker.py
# ---------------------------------------------------------------------------

class TestRateLimitBucket:
    def test_used_property(self) -> None:
        from navig.core.rate_limit_tracker import RateLimitBucket
        b = RateLimitBucket(limit=100, remaining=40, reset_seconds=30.0, captured_at=time.time())
        assert b.used == 60

    def test_used_never_negative(self) -> None:
        from navig.core.rate_limit_tracker import RateLimitBucket
        b = RateLimitBucket(limit=10, remaining=20)
        assert b.used == 0

    def test_usage_pct(self) -> None:
        from navig.core.rate_limit_tracker import RateLimitBucket
        b = RateLimitBucket(limit=200, remaining=100, reset_seconds=0.0, captured_at=time.time())
        assert b.usage_pct == pytest.approx(50.0)

    def test_usage_pct_zero_limit(self) -> None:
        from navig.core.rate_limit_tracker import RateLimitBucket
        b = RateLimitBucket(limit=0, remaining=0)
        assert b.usage_pct == 0.0

    def test_remaining_seconds_now_decreases(self) -> None:
        from navig.core.rate_limit_tracker import RateLimitBucket
        b = RateLimitBucket(limit=100, remaining=50, reset_seconds=60.0, captured_at=time.time())
        r = b.remaining_seconds_now
        assert 0 <= r <= 60.0

    def test_remaining_seconds_never_negative(self) -> None:
        from navig.core.rate_limit_tracker import RateLimitBucket
        past = time.time() - 9999
        b = RateLimitBucket(limit=100, remaining=50, reset_seconds=1.0, captured_at=past)
        assert b.remaining_seconds_now == 0.0


class TestRateLimitState:
    def test_has_data_when_captured_at_nonzero(self) -> None:
        from navig.core.rate_limit_tracker import RateLimitState
        s = RateLimitState(captured_at=time.time())
        assert s.has_data is True

    def test_not_has_data_default(self) -> None:
        from navig.core.rate_limit_tracker import RateLimitState
        s = RateLimitState()
        assert s.has_data is False

    def test_age_seconds_infinite_without_data(self) -> None:
        from navig.core.rate_limit_tracker import RateLimitState
        s = RateLimitState()
        assert s.age_seconds == float("inf")


class TestParseRateLimitHeaders:
    def test_returns_none_when_no_headers(self) -> None:
        from navig.core.rate_limit_tracker import parse_rate_limit_headers
        result = parse_rate_limit_headers({})
        assert result is None

    def test_parses_basic_headers(self) -> None:
        from navig.core.rate_limit_tracker import parse_rate_limit_headers
        headers = {
            "x-ratelimit-limit-requests": "100",
            "x-ratelimit-remaining-requests": "60",
            "x-ratelimit-reset-requests": "30",
        }
        state = parse_rate_limit_headers(headers, provider="openai")
        assert state is not None
        assert state.requests_min.limit == 100
        assert state.requests_min.remaining == 60
        assert state.provider == "openai"

    def test_parses_hour_buckets(self) -> None:
        from navig.core.rate_limit_tracker import parse_rate_limit_headers
        headers = {
            "x-ratelimit-limit-requests-1h": "5000",
            "x-ratelimit-remaining-requests-1h": "4900",
            "x-ratelimit-reset-requests-1h": "3600",
        }
        state = parse_rate_limit_headers(headers)
        assert state is not None
        assert state.requests_hour.limit == 5000

    def test_case_insensitive_headers(self) -> None:
        from navig.core.rate_limit_tracker import parse_rate_limit_headers
        headers = {
            "X-RateLimit-Limit-Requests": "50",
            "X-RateLimit-Remaining-Requests": "25",
        }
        state = parse_rate_limit_headers(headers)
        assert state is not None
        assert state.requests_min.limit == 50


class TestFormatHelpers:
    def test_fmt_count_large(self) -> None:
        from navig.core.rate_limit_tracker import _fmt_count
        assert "M" in _fmt_count(1_500_000)

    def test_fmt_count_k(self) -> None:
        from navig.core.rate_limit_tracker import _fmt_count
        assert "K" in _fmt_count(1_500)

    def test_fmt_count_small(self) -> None:
        from navig.core.rate_limit_tracker import _fmt_count
        assert _fmt_count(42) == "42"

    def test_fmt_seconds_minutes(self) -> None:
        from navig.core.rate_limit_tracker import _fmt_seconds
        result = _fmt_seconds(125)
        assert "m" in result

    def test_fmt_seconds_hours(self) -> None:
        from navig.core.rate_limit_tracker import _fmt_seconds
        result = _fmt_seconds(3700)
        assert "h" in result

    def test_fmt_seconds_under_minute(self) -> None:
        from navig.core.rate_limit_tracker import _fmt_seconds
        assert _fmt_seconds(45) == "45s"


class TestFormatRateLimitDisplay:
    def test_no_data_message(self) -> None:
        from navig.core.rate_limit_tracker import RateLimitState, format_rate_limit_display
        s = RateLimitState()
        result = format_rate_limit_display(s)
        assert "No rate limit data" in result

    def test_display_includes_provider(self) -> None:
        from navig.core.rate_limit_tracker import parse_rate_limit_headers, format_rate_limit_display
        headers = {
            "x-ratelimit-limit-requests": "100",
            "x-ratelimit-remaining-requests": "60",
            "x-ratelimit-reset-requests": "10",
        }
        state = parse_rate_limit_headers(headers, provider="openai")
        result = format_rate_limit_display(state)
        assert "Openai" in result or "openai" in result.lower()

    def test_compact_format_no_data(self) -> None:
        from navig.core.rate_limit_tracker import RateLimitState, format_rate_limit_compact
        s = RateLimitState()
        result = format_rate_limit_compact(s)
        assert "No rate limit data" in result

    def test_compact_format_with_data(self) -> None:
        from navig.core.rate_limit_tracker import parse_rate_limit_headers, format_rate_limit_compact
        headers = {
            "x-ratelimit-limit-requests": "100",
            "x-ratelimit-remaining-requests": "75",
            "x-ratelimit-reset-requests": "30",
        }
        state = parse_rate_limit_headers(headers)
        result = format_rate_limit_compact(state)
        assert "RPM" in result
