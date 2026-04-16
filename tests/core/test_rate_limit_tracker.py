"""Tests for navig.core.rate_limit_tracker.

Covers:
  - parse_rate_limit_headers() — full headers, partial headers, no headers
  - RateLimitBucket computed properties
  - Formatting helpers: format_rate_limit_display(), format_rate_limit_compact()
"""

from __future__ import annotations

import time

import pytest

from navig.core.rate_limit_tracker import (
    RateLimitBucket,
    RateLimitState,
    _fmt_count,
    _fmt_seconds,
    format_rate_limit_compact,
    format_rate_limit_display,
    parse_rate_limit_headers,
)


# ---------------------------------------------------------------------------
# RateLimitBucket properties
# ---------------------------------------------------------------------------

class TestRateLimitBucket:
    def test_used(self):
        b = RateLimitBucket(limit=100, remaining=60)
        assert b.used == 40

    def test_used_clamps_at_zero(self):
        # remaining > limit (shouldn't happen but must not go negative)
        b = RateLimitBucket(limit=50, remaining=60)
        assert b.used == 0

    def test_usage_pct(self):
        b = RateLimitBucket(limit=100, remaining=25)
        assert b.usage_pct == pytest.approx(75.0)

    def test_usage_pct_zero_when_no_limit(self):
        b = RateLimitBucket(limit=0)
        assert b.usage_pct == pytest.approx(0.0)

    def test_remaining_seconds_now_decreases(self):
        b = RateLimitBucket(reset_seconds=60.0, captured_at=time.time() - 5)
        val = b.remaining_seconds_now
        assert 50 < val < 60  # ~55 seconds left after 5s elapsed

    def test_remaining_seconds_now_clamps_at_zero(self):
        b = RateLimitBucket(reset_seconds=1.0, captured_at=time.time() - 100)
        assert b.remaining_seconds_now == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# parse_rate_limit_headers
# ---------------------------------------------------------------------------

_FULL_HEADERS = {
    "x-ratelimit-limit-requests": "100",
    "x-ratelimit-limit-requests-1h": "1000",
    "x-ratelimit-limit-tokens": "50000",
    "x-ratelimit-limit-tokens-1h": "500000",
    "x-ratelimit-remaining-requests": "85",
    "x-ratelimit-remaining-requests-1h": "940",
    "x-ratelimit-remaining-tokens": "42000",
    "x-ratelimit-remaining-tokens-1h": "463000",
    "x-ratelimit-reset-requests": "30.5",
    "x-ratelimit-reset-requests-1h": "3600",
    "x-ratelimit-reset-tokens": "28",
    "x-ratelimit-reset-tokens-1h": "3580",
}


class TestParseRateLimitHeaders:
    def test_full_headers_parsed(self):
        state = parse_rate_limit_headers(_FULL_HEADERS, provider="openai")
        assert state is not None
        assert state.has_data
        assert state.provider == "openai"
        assert state.requests_min.limit == 100
        assert state.requests_min.remaining == 85
        assert state.tokens_min.limit == 50_000
        assert state.requests_hour.limit == 1000
        assert state.tokens_hour.remaining == 463_000

    def test_returns_none_with_no_ratelimit_headers(self):
        headers = {"content-type": "application/json", "x-request-id": "abc123"}
        state = parse_rate_limit_headers(headers)
        assert state is None

    def test_case_insensitive_headers(self):
        # HTTP headers are case-insensitive
        headers = {
            "X-RateLimit-Limit-Requests": "200",
            "X-RateLimit-Remaining-Requests": "150",
        }
        state = parse_rate_limit_headers(headers)
        assert state is not None
        assert state.requests_min.limit == 200

    def test_partial_headers(self):
        """Some providers send only a subset of headers."""
        headers = {"x-ratelimit-limit-requests": "60", "x-ratelimit-remaining-requests": "42"}
        state = parse_rate_limit_headers(headers)
        assert state is not None
        assert state.requests_min.limit == 60
        # Missing fields should default to 0
        assert state.tokens_min.limit == 0


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

class TestFormatting:
    def test_fmt_count_millions(self):
        assert "M" in _fmt_count(1_500_000)

    def test_fmt_count_thousands(self):
        assert "K" in _fmt_count(15_000)

    def test_fmt_count_small(self):
        assert _fmt_count(42) == "42"

    def test_fmt_seconds_sub_minute(self):
        assert _fmt_seconds(45) == "45s"

    def test_fmt_seconds_minutes(self):
        assert "m" in _fmt_seconds(90)

    def test_fmt_seconds_hours(self):
        assert "h" in _fmt_seconds(3700)

    def test_format_display_no_data(self):
        state = RateLimitState()
        msg = format_rate_limit_display(state)
        assert "No rate limit data" in msg

    def test_format_display_with_data(self):
        state = parse_rate_limit_headers(_FULL_HEADERS, provider="openai")
        msg = format_rate_limit_display(state)
        assert "Openai" in msg or "openai" in msg.lower()
        assert "Requests" in msg or "requests" in msg.lower()

    def test_format_compact_no_data(self):
        state = RateLimitState()
        msg = format_rate_limit_compact(state)
        assert "No rate limit data" in msg

    def test_format_compact_with_data(self):
        state = parse_rate_limit_headers(_FULL_HEADERS)
        msg = format_rate_limit_compact(state)
        assert "RPM" in msg or "RPH" in msg

    def test_format_display_shows_warning_at_80pct(self):
        # Craft a state where requests/min is at 90% usage
        state = RateLimitState(captured_at=time.time())
        state.requests_min = RateLimitBucket(
            limit=100, remaining=10, reset_seconds=30, captured_at=time.time()
        )
        msg = format_rate_limit_display(state)
        assert "90%" in msg or "requests/min" in msg
