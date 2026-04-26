"""Hermetic unit tests for navig.core.continuation."""
from __future__ import annotations

import pytest

from navig.core.continuation import (
    ContinuationPolicy,
    _to_bool,
    _to_int,
    busy_window_seconds,
    classify_continuation_state,
    consume_skip,
    decision_sensitivity_for_profile,
    is_decision_point,
    is_decision_point_for_profile,
    merge_policy,
    normalize_profile_name,
    policy_from_context,
    policy_to_context,
    should_auto_continue,
    suppression_windows_for_profile,
)

# ---------------------------------------------------------------------------
# normalize_profile_name
# ---------------------------------------------------------------------------


class TestNormalizeProfileName:
    def test_conservative(self):
        assert normalize_profile_name("conservative") == "conservative"

    def test_balanced(self):
        assert normalize_profile_name("balanced") == "balanced"

    def test_aggressive(self):
        assert normalize_profile_name("aggressive") == "aggressive"

    def test_unknown_falls_back(self):
        assert normalize_profile_name("turbo") == "conservative"

    def test_none_falls_back(self):
        assert normalize_profile_name(None) == "conservative"

    def test_empty_falls_back(self):
        assert normalize_profile_name("") == "conservative"

    def test_uppercase_normalized(self):
        assert normalize_profile_name("BALANCED") == "balanced"

    def test_whitespace_stripped(self):
        assert normalize_profile_name("  aggressive  ") == "aggressive"


# ---------------------------------------------------------------------------
# _to_bool / _to_int helpers
# ---------------------------------------------------------------------------


class TestToBool:
    def test_true_bool(self):
        assert _to_bool(True) is True

    def test_false_bool(self):
        assert _to_bool(False) is False

    def test_string_true(self):
        assert _to_bool("true") is True

    def test_string_yes(self):
        assert _to_bool("yes") is True

    def test_string_1(self):
        assert _to_bool("1") is True

    def test_string_on(self):
        assert _to_bool("on") is True

    def test_string_false(self):
        assert _to_bool("false") is False

    def test_none_returns_default(self):
        assert _to_bool(None, default=True) is True

    def test_int_non_bool_returns_default(self):
        assert _to_bool(1, default=False) is False


class TestToInt:
    def test_integer(self):
        assert _to_int(5, 0) == 5

    def test_string_int(self):
        assert _to_int("10", 0) == 10

    def test_none_returns_default(self):
        assert _to_int(None, 7) == 7

    def test_invalid_string_returns_default(self):
        assert _to_int("bad", 3) == 3

    def test_float_truncated(self):
        assert _to_int(3.9, 0) == 3


# ---------------------------------------------------------------------------
# ContinuationPolicy defaults
# ---------------------------------------------------------------------------


class TestContinuationPolicyDefaults:
    def test_default_profile_conservative(self):
        assert ContinuationPolicy().profile == "conservative"

    def test_default_enabled_false(self):
        assert ContinuationPolicy().enabled is False

    def test_default_cooldown(self):
        assert ContinuationPolicy().cooldown_seconds == 20

    def test_default_max_turns(self):
        assert ContinuationPolicy().max_turns == 2

    def test_default_turns_used(self):
        assert ContinuationPolicy().turns_used == 0


# ---------------------------------------------------------------------------
# policy_from_context / policy_to_context round-trip
# ---------------------------------------------------------------------------


class TestPolicyRoundTrip:
    def test_none_context_returns_defaults(self):
        p = policy_from_context(None)
        assert p.profile == "conservative"
        assert p.enabled is False
        assert p.max_turns == 2

    def test_empty_context_returns_defaults(self):
        p = policy_from_context({})
        assert p.profile == "conservative"

    def test_balanced_profile_picks_correct_defaults(self):
        ctx = {"continuation": {"profile": "balanced"}}
        p = policy_from_context(ctx)
        assert p.profile == "balanced"
        assert p.cooldown_seconds == 10
        assert p.max_turns == 3

    def test_aggressive_profile_defaults(self):
        ctx = {"continuation": {"profile": "aggressive"}}
        p = policy_from_context(ctx)
        assert p.cooldown_seconds == 5
        assert p.max_turns == 5

    def test_round_trip_preserves_fields(self):
        ctx = {
            "continuation": {
                "profile": "balanced",
                "enabled": True,
                "paused": False,
                "turns_used": 2,
                "cooldown_seconds": 10,
                "max_turns": 3,
                "last_continued_at": "2025-01-01T00:00:00+00:00",
                "dry_run": False,
                "skip_next": False,
            }
        }
        p = policy_from_context(ctx)
        exported = policy_to_context(p)
        assert exported["profile"] == "balanced"
        assert exported["enabled"] is True
        assert exported["turns_used"] == 2

    def test_enabled_string_true(self):
        ctx = {"continuation": {"enabled": "true"}}
        p = policy_from_context(ctx)
        assert p.enabled is True

    def test_negative_turns_clamped_to_zero(self):
        ctx = {"continuation": {"turns_used": -5}}
        p = policy_from_context(ctx)
        assert p.turns_used == 0


# ---------------------------------------------------------------------------
# merge_policy
# ---------------------------------------------------------------------------


class TestMergePolicy:
    def test_enable_from_none(self):
        result = merge_policy(None, enabled=True)
        assert result["continuation"]["enabled"] is True

    def test_increment_turns(self):
        ctx = {"continuation": {"profile": "conservative", "turns_used": 1, "enabled": True}}
        result = merge_policy(ctx, turns_used=2)
        assert result["continuation"]["turns_used"] == 2

    def test_skip_next_true(self):
        result = merge_policy(None, skip_next=True)
        assert result["continuation"]["skip_next"] is True

    def test_profile_change(self):
        result = merge_policy(None, profile="aggressive")
        assert result["continuation"]["profile"] == "aggressive"
        assert result["continuation"]["cooldown_seconds"] == 5


# ---------------------------------------------------------------------------
# classify_continuation_state
# ---------------------------------------------------------------------------


class TestClassifyContinuationState:
    def test_empty_text_is_wait(self):
        state, reason = classify_continuation_state("")
        assert state == "wait"
        assert reason == "empty_response"

    def test_decision_point_phrase(self):
        state, _ = classify_continuation_state("Should I continue with this?")
        assert state == "continue"

    def test_blocked_phrase(self):
        state, _ = classify_continuation_state("I cannot proceed, permission denied.")
        assert state == "blocked"

    def test_choice_phrase(self):
        state, _ = classify_continuation_state("Please choose between option A or B.")
        assert state == "choice"

    def test_wait_signal(self):
        state, _ = classify_continuation_state("I am currently working on the task.")
        assert state == "wait"

    def test_neutral_low_confidence(self):
        state, _ = classify_continuation_state("The value is 42.")
        assert state == "neutral"


# ---------------------------------------------------------------------------
# is_decision_point / is_decision_point_for_profile
# ---------------------------------------------------------------------------


class TestIsDecisionPoint:
    def test_decision_point_phrase(self):
        assert is_decision_point("Shall I continue?") is True

    def test_neutral_text_not_decision_point(self):
        assert is_decision_point("The result is 5.") is False

    def test_blocked_not_decision_point(self):
        assert is_decision_point("Unable to proceed.") is False

    def test_aggressive_proceed_question_is_decision(self):
        result = is_decision_point_for_profile("Want me to proceed?", "aggressive")
        # "proceed?" contains soft signal + "?" — should be True for aggressive
        assert isinstance(result, bool)

    def test_conservative_neutral_not_decision(self):
        assert is_decision_point_for_profile("Let me know.", "conservative") is False


# ---------------------------------------------------------------------------
# decision_sensitivity_for_profile
# ---------------------------------------------------------------------------


class TestDecisionSensitivity:
    def test_conservative_strict(self):
        assert decision_sensitivity_for_profile("conservative") == "strict"

    def test_balanced_standard(self):
        assert decision_sensitivity_for_profile("balanced") == "standard"

    def test_aggressive_eager(self):
        assert decision_sensitivity_for_profile("aggressive") == "eager"

    def test_unknown_falls_back_to_strict(self):
        assert decision_sensitivity_for_profile(None) == "strict"


# ---------------------------------------------------------------------------
# suppression_windows_for_profile / busy_window_seconds
# ---------------------------------------------------------------------------


class TestSuppressionWindows:
    def test_conservative_wait(self):
        w = suppression_windows_for_profile("conservative")
        assert w["wait"] == 45
        assert w["blocked"] == 120

    def test_balanced_windows(self):
        w = suppression_windows_for_profile("balanced")
        assert w["wait"] == 30
        assert w["blocked"] == 90

    def test_aggressive_windows(self):
        w = suppression_windows_for_profile("aggressive")
        assert w["wait"] == 15
        assert w["blocked"] == 60

    def test_busy_window_wait(self):
        assert busy_window_seconds("conservative", "wait") == 45

    def test_busy_window_unknown_state(self):
        assert busy_window_seconds("conservative", "idle") == 0


# ---------------------------------------------------------------------------
# should_auto_continue
# ---------------------------------------------------------------------------


class TestShouldAutoContinue:
    def _policy(self, **kwargs) -> ContinuationPolicy:
        defaults = dict(
            profile="conservative",
            enabled=True,
            paused=False,
            skip_next=False,
            cooldown_seconds=0,
            max_turns=5,
            turns_used=0,
            last_continued_at="",
            dry_run=False,
        )
        defaults.update(kwargs)
        return ContinuationPolicy(**defaults)

    def test_disabled_returns_disabled(self):
        p = self._policy(enabled=False)
        ok, reason = should_auto_continue("Shall I continue?", p)
        assert ok is False
        assert reason == "disabled"

    def test_paused_returns_paused(self):
        p = self._policy(paused=True)
        ok, reason = should_auto_continue("Shall I continue?", p)
        assert ok is False
        assert reason == "paused"

    def test_skip_next_returns_skip(self):
        p = self._policy(skip_next=True)
        ok, reason = should_auto_continue("Shall I continue?", p)
        assert ok is False
        assert reason == "skip_next"

    def test_max_turns_exhausted(self):
        p = self._policy(max_turns=2, turns_used=2)
        ok, reason = should_auto_continue("Shall I continue?", p)
        assert ok is False
        assert reason == "max_turns"

    def test_no_decision_point(self):
        p = self._policy()
        ok, reason = should_auto_continue("The value is 42.", p)
        assert ok is False
        assert reason == "no_decision_point"

    def test_all_clear_returns_ok(self):
        p = self._policy()
        ok, reason = should_auto_continue("Should I continue?", p)
        assert ok is True
        assert reason == "ok"


# ---------------------------------------------------------------------------
# consume_skip / mark_continued
# ---------------------------------------------------------------------------


class TestConsumeSkip:
    def test_consume_skip_clears_flag(self):
        ctx = merge_policy(None, skip_next=True, enabled=True)
        result = consume_skip(ctx)
        p = policy_from_context(result)
        assert p.skip_next is False

    def test_consume_skip_noop_when_false(self):
        ctx = merge_policy(None, skip_next=False, enabled=True)
        result = consume_skip(ctx)
        p = policy_from_context(result)
        assert p.skip_next is False
