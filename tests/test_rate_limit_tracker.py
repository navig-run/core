"""Hermetic unit tests for navig.core.rate_limit_tracker — pure logic."""

from __future__ import annotations

import pytest

from navig.core.rate_limit_tracker import (
    RateLimitBucket,
    RateLimitState,
    _bar,
    _fmt_count,
    _fmt_seconds,
    parse_rate_limit_headers,
)

# ---------------------------------------------------------------------------
# _fmt_count
# ---------------------------------------------------------------------------


class TestFmtCount:
    def test_small(self):
        assert _fmt_count(799) == "799"

    def test_thousands(self):
        assert _fmt_count(1000) == "1.0K"
        assert _fmt_count(33600) == "33.6K"

    def test_millions(self):
        assert _fmt_count(1_000_000) == "1.0M"
        assert _fmt_count(7_999_856) == "8.0M"

    def test_zero(self):
        assert _fmt_count(0) == "0"


# ---------------------------------------------------------------------------
# _fmt_seconds
# ---------------------------------------------------------------------------


class TestFmtSeconds:
    def test_seconds_only(self):
        assert _fmt_seconds(58) == "58s"

    def test_minutes_with_seconds(self):
        result = _fmt_seconds(134)  # 2m 14s
        assert "2m" in result and "14s" in result

    def test_exact_minutes(self):
        assert _fmt_seconds(120) == "2m"

    def test_hours(self):
        result = _fmt_seconds(3600)
        assert "1h" in result

    def test_hours_and_minutes(self):
        result = _fmt_seconds(3720)  # 1h 2m
        assert "1h" in result and "2m" in result

    def test_zero(self):
        assert _fmt_seconds(0) == "0s"


# ---------------------------------------------------------------------------
# _bar
# ---------------------------------------------------------------------------


class TestBar:
    def test_zero_pct(self):
        b = _bar(0.0)
        assert "█" not in b or b.count("█") == 0

    def test_hundred_pct(self):
        b = _bar(100.0)
        assert "░" not in b or b.count("░") == 0

    def test_fifty_pct(self):
        b = _bar(50.0, width=20)
        assert b.count("█") == 10
        assert b.count("░") == 10

    def test_contains_brackets(self):
        assert _bar(50.0).startswith("[")
        assert _bar(50.0).endswith("]")

    def test_custom_width(self):
        b = _bar(50.0, width=10)
        assert b.count("█") == 5


# ---------------------------------------------------------------------------
# RateLimitBucket properties
# ---------------------------------------------------------------------------


class TestRateLimitBucketProperties:
    def test_used(self):
        b = RateLimitBucket(limit=100, remaining=60)
        assert b.used == 40

    def test_used_floored_at_zero(self):
        b = RateLimitBucket(limit=50, remaining=80)  # remaining > limit
        assert b.used == 0

    def test_usage_pct(self):
        b = RateLimitBucket(limit=200, remaining=100)
        assert b.usage_pct == pytest.approx(50.0, abs=0.1)

    def test_usage_pct_zero_limit(self):
        b = RateLimitBucket(limit=0, remaining=0)
        assert b.usage_pct == 0.0


# ---------------------------------------------------------------------------
# parse_rate_limit_headers
# ---------------------------------------------------------------------------


class TestParseRateLimitHeaders:
    _HEADERS = {
        "x-ratelimit-limit-requests": "1000",
        "x-ratelimit-remaining-requests": "800",
        "x-ratelimit-reset-requests": "30",
        "x-ratelimit-limit-tokens": "100000",
        "x-ratelimit-remaining-tokens": "90000",
        "x-ratelimit-reset-tokens": "45",
    }

    def test_returns_state_when_headers_present(self):
        state = parse_rate_limit_headers(self._HEADERS, provider="openai")
        assert state is not None

    def test_returns_none_when_no_headers(self):
        assert parse_rate_limit_headers({"content-type": "application/json"}) is None

    def test_returns_none_on_empty(self):
        assert parse_rate_limit_headers({}) is None

    def test_provider_stored(self):
        state = parse_rate_limit_headers(self._HEADERS, provider="openai")
        assert state.provider == "openai"

    def test_requests_min_parsed(self):
        state = parse_rate_limit_headers(self._HEADERS)
        assert state.requests_min.limit == 1000
        assert state.requests_min.remaining == 800

    def test_tokens_min_parsed(self):
        state = parse_rate_limit_headers(self._HEADERS)
        assert state.tokens_min.limit == 100_000
        assert state.tokens_min.remaining == 90_000

    def test_case_insensitive_headers(self):
        headers = {k.upper(): v for k, v in self._HEADERS.items()}
        state = parse_rate_limit_headers(headers)
        assert state is not None
        assert state.requests_min.limit == 1000

    def test_has_data(self):
        state = parse_rate_limit_headers(self._HEADERS)
        assert state.has_data

    def test_hourly_bucket_zero_when_absent(self):
        state = parse_rate_limit_headers(self._HEADERS)
        # No *-1h headers in _HEADERS, so hourly buckets should have 0 limit
        assert state.requests_hour.limit == 0
