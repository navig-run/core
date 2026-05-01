"""
Batch 79: navig/core/continuation.py, navig/core/rate_limit_tracker.py
"""
from __future__ import annotations

import time
import pytest


# ---------------------------------------------------------------------------
# core/continuation.py
# ---------------------------------------------------------------------------
from navig.core.continuation import (
    normalize_profile_name,
    ContinuationPolicy,
    policy_from_context,
    policy_to_context,
    merge_policy,
    classify_continuation_state,
    is_decision_point,
    is_decision_point_for_profile,
    decision_sensitivity_for_profile,
    suppression_windows_for_profile,
    busy_window_seconds,
    apply_busy_suppression,
    get_busy_suppression,
    should_auto_continue,
    consume_skip,
    mark_continued,
)


class TestNormalizeProfileName:
    def test_conservative(self):
        assert normalize_profile_name("conservative") == "conservative"

    def test_balanced(self):
        assert normalize_profile_name("balanced") == "balanced"

    def test_aggressive(self):
        assert normalize_profile_name("aggressive") == "aggressive"

    def test_unknown_falls_back_to_conservative(self):
        assert normalize_profile_name("extreme") == "conservative"

    def test_none_falls_back(self):
        assert normalize_profile_name(None) == "conservative"

    def test_empty_string_falls_back(self):
        assert normalize_profile_name("") == "conservative"

    def test_case_insensitive(self):
        assert normalize_profile_name("BALANCED") == "balanced"


class TestContinuationPolicy:
    def test_defaults(self):
        p = ContinuationPolicy()
        assert p.profile == "conservative"
        assert p.enabled is False
        assert p.paused is False
        assert p.max_turns == 2
        assert p.turns_used == 0

    def test_frozen(self):
        p = ContinuationPolicy()
        with pytest.raises((AttributeError, TypeError)):
            p.enabled = True  # type: ignore[misc]


class TestPolicyFromContext:
    def test_empty_context_returns_defaults(self):
        p = policy_from_context(None)
        assert p.profile == "conservative"
        assert p.enabled is False

    def test_reads_enabled_flag(self):
        ctx = {"continuation": {"enabled": True, "profile": "balanced"}}
        p = policy_from_context(ctx)
        assert p.enabled is True
        assert p.profile == "balanced"

    def test_cooldown_defaults_by_profile(self):
        ctx = {"continuation": {"profile": "aggressive"}}
        p = policy_from_context(ctx)
        assert p.cooldown_seconds == 5  # aggressive default

    def test_turns_used_clamped(self):
        ctx = {"continuation": {"turns_used": -5}}
        p = policy_from_context(ctx)
        assert p.turns_used == 0


class TestPolicyToContext:
    def test_roundtrip(self):
        p = ContinuationPolicy(enabled=True, profile="balanced", max_turns=5)
        d = policy_to_context(p)
        p2 = policy_from_context({"continuation": d})
        assert p2.enabled is True
        assert p2.profile == "balanced"
        assert p2.max_turns == 5


class TestMergePolicy:
    def test_enable_from_disabled(self):
        ctx = {}
        result = merge_policy(ctx, enabled=True)
        assert result["continuation"]["enabled"] is True

    def test_update_turns_used(self):
        ctx = {"continuation": {"enabled": True, "turns_used": 2}}
        result = merge_policy(ctx, turns_used=3)
        assert result["continuation"]["turns_used"] == 3

    def test_preserves_other_keys(self):
        ctx = {"other_key": "value"}
        result = merge_policy(ctx)
        assert result["other_key"] == "value"


class TestClassifyContinuationState:
    def test_empty_text(self):
        state, reason = classify_continuation_state("")
        assert state == "wait"

    def test_blocked_signal(self):
        state, reason = classify_continuation_state("I cannot proceed — permission denied")
        assert state == "blocked"

    def test_continue_signal(self):
        state, reason = classify_continuation_state("Should I continue with the next step?")
        assert state == "continue"

    def test_choice_signal(self):
        state, reason = classify_continuation_state("Choose which option you prefer: A or B")
        assert state == "choice"

    def test_neutral_low_confidence(self):
        state, reason = classify_continuation_state("The task is complete.")
        assert state == "neutral"


class TestIsDecisionPoint:
    def test_decision_phrase(self):
        assert is_decision_point("Should I continue with this approach?") is True

    def test_blocked_text_not_decision(self):
        assert is_decision_point("I cannot proceed — missing information") is False

    def test_neutral_not_decision(self):
        assert is_decision_point("File saved successfully.") is False


class TestDecisionSensitivity:
    def test_aggressive_profile(self):
        assert decision_sensitivity_for_profile("aggressive") == "eager"

    def test_balanced_profile(self):
        assert decision_sensitivity_for_profile("balanced") == "standard"

    def test_conservative_profile(self):
        assert decision_sensitivity_for_profile("conservative") == "strict"


class TestSuppressionWindows:
    def test_conservative_windows(self):
        w = suppression_windows_for_profile("conservative")
        assert w["wait"] == 45
        assert w["blocked"] == 120

    def test_aggressive_windows(self):
        w = suppression_windows_for_profile("aggressive")
        assert w["wait"] == 15


class TestShouldAutoContinue:
    def test_disabled_policy(self):
        p = ContinuationPolicy(enabled=False)
        ok, reason = should_auto_continue("Should I continue?", p)
        assert ok is False
        assert reason == "disabled"

    def test_paused_policy(self):
        p = ContinuationPolicy(enabled=True, paused=True)
        ok, reason = should_auto_continue("Should I continue?", p)
        assert ok is False
        assert reason == "paused"

    def test_max_turns_reached(self):
        p = ContinuationPolicy(enabled=True, max_turns=2, turns_used=2)
        ok, reason = should_auto_continue("Should I continue?", p)
        assert ok is False
        assert reason == "max_turns"

    def test_ok_when_conditions_met(self):
        p = ContinuationPolicy(
            enabled=True, paused=False, skip_next=False,
            max_turns=5, turns_used=0, cooldown_seconds=0,
            last_continued_at=""
        )
        ok, reason = should_auto_continue("Should I continue with the next step?", p)
        assert ok is True
        assert reason == "ok"


class TestMarkContinuedAndConsume:
    def test_mark_continued_increments_turns(self):
        ctx = {"continuation": {"turns_used": 2}}
        result = mark_continued(ctx)
        assert result["continuation"]["turns_used"] == 3

    def test_consume_skip_clears_flag(self):
        ctx = {"continuation": {"skip_next": True}}
        result = consume_skip(ctx)
        assert result["continuation"]["skip_next"] is False

    def test_consume_skip_noop_when_not_set(self):
        ctx = {}
        result = consume_skip(ctx)
        assert result == ctx


# ---------------------------------------------------------------------------
# core/rate_limit_tracker.py
# ---------------------------------------------------------------------------
from navig.core.rate_limit_tracker import (
    RateLimitBucket,
    RateLimitState,
    parse_rate_limit_headers,
    format_rate_limit_display,
    format_rate_limit_compact,
    _fmt_count,
    _fmt_seconds,
    _bar,
)


class TestRateLimitBucket:
    def test_used_calculation(self):
        b = RateLimitBucket(limit=100, remaining=60)
        assert b.used == 40

    def test_usage_pct(self):
        b = RateLimitBucket(limit=100, remaining=75)
        assert b.usage_pct == pytest.approx(25.0)

    def test_zero_limit_gives_zero_pct(self):
        b = RateLimitBucket(limit=0, remaining=0)
        assert b.usage_pct == 0.0

    def test_used_never_negative(self):
        b = RateLimitBucket(limit=50, remaining=100)
        assert b.used == 0


class TestParseRateLimitHeaders:
    def test_no_headers_returns_none(self):
        result = parse_rate_limit_headers({})
        assert result is None

    def test_parses_standard_headers(self):
        headers = {
            "x-ratelimit-limit-requests": "100",
            "x-ratelimit-remaining-requests": "80",
            "x-ratelimit-reset-requests": "30",
            "x-ratelimit-limit-tokens": "50000",
            "x-ratelimit-remaining-tokens": "40000",
        }
        state = parse_rate_limit_headers(headers, provider="openai")
        assert state is not None
        assert state.provider == "openai"
        assert state.requests_min.limit == 100
        assert state.requests_min.remaining == 80
        assert state.tokens_min.limit == 50000

    def test_case_insensitive_headers(self):
        headers = {
            "X-RateLimit-Limit-Requests": "200",
            "X-RateLimit-Remaining-Requests": "150",
        }
        state = parse_rate_limit_headers(headers)
        assert state is not None
        assert state.requests_min.limit == 200

    def test_has_data_true_when_parsed(self):
        headers = {"x-ratelimit-limit-requests": "10"}
        state = parse_rate_limit_headers(headers)
        assert state.has_data is True


class TestFormatHelpers:
    def test_fmt_count_small(self):
        assert _fmt_count(500) == "500"

    def test_fmt_count_thousands(self):
        assert "K" in _fmt_count(5000)

    def test_fmt_count_millions(self):
        assert "M" in _fmt_count(2_000_000)

    def test_fmt_seconds_under_60(self):
        assert _fmt_seconds(45) == "45s"

    def test_fmt_seconds_minutes(self):
        result = _fmt_seconds(125)
        assert "m" in result

    def test_fmt_seconds_hours(self):
        result = _fmt_seconds(3700)
        assert "h" in result

    def test_bar_empty(self):
        result = _bar(0.0)
        assert "[" in result and "]" in result

    def test_bar_full(self):
        result = _bar(100.0, width=10)
        assert result.startswith("[")


class TestFormatDisplay:
    def test_no_data_message(self):
        state = RateLimitState()
        text = format_rate_limit_display(state)
        assert "No rate limit" in text

    def test_with_data_contains_provider(self):
        headers = {
            "x-ratelimit-limit-requests": "100",
            "x-ratelimit-remaining-requests": "60",
        }
        state = parse_rate_limit_headers(headers, provider="openai")
        text = format_rate_limit_display(state)
        assert "Openai" in text or "openai" in text.lower()

    def test_compact_no_data(self):
        state = RateLimitState()
        text = format_rate_limit_compact(state)
        assert "No rate limit" in text

    def test_compact_with_data(self):
        headers = {
            "x-ratelimit-limit-requests": "100",
            "x-ratelimit-remaining-requests": "50",
        }
        state = parse_rate_limit_headers(headers)
        text = format_rate_limit_compact(state)
        assert "RPM" in text
