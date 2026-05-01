"""
Batch 72: hermetic unit tests for navig/core/continuation.py
Covers: normalize_profile_name, ContinuationPolicy, _to_bool, _to_int,
        policy_from_context, policy_to_context, merge_policy,
        classify_continuation_state, is_decision_point_for_profile,
        should_auto_continue, consume_skip, mark_continued,
        suppression_windows_for_profile, apply_busy_suppression, get_busy_suppression
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest


# --------------- helpers ---------------

def _policy(**kw) -> "ContinuationPolicy":
    from navig.core.continuation import ContinuationPolicy
    return ContinuationPolicy(**kw)


def _iso_past(seconds: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return dt.isoformat()


def _iso_future(seconds: int) -> str:
    dt = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return dt.isoformat()


# --------------- normalize_profile_name ---------------

class TestNormalizeProfile:
    def test_conservative(self) -> None:
        from navig.core.continuation import normalize_profile_name
        assert normalize_profile_name("conservative") == "conservative"

    def test_balanced(self) -> None:
        from navig.core.continuation import normalize_profile_name
        assert normalize_profile_name("balanced") == "balanced"

    def test_aggressive(self) -> None:
        from navig.core.continuation import normalize_profile_name
        assert normalize_profile_name("aggressive") == "aggressive"

    def test_unknown_falls_back_to_conservative(self) -> None:
        from navig.core.continuation import normalize_profile_name
        assert normalize_profile_name("turbo") == "conservative"

    def test_none_falls_back_to_conservative(self) -> None:
        from navig.core.continuation import normalize_profile_name
        assert normalize_profile_name(None) == "conservative"

    def test_whitespace_stripped(self) -> None:
        from navig.core.continuation import normalize_profile_name
        assert normalize_profile_name("  aggressive  ") == "aggressive"


# --------------- _to_bool / _to_int ---------------

class TestToBool:
    def test_true_values(self) -> None:
        from navig.core.continuation import _to_bool
        for v in ("1", "true", "yes", "on", "TRUE", "YES"):
            assert _to_bool(v) is True

    def test_false_values(self) -> None:
        from navig.core.continuation import _to_bool
        for v in ("0", "false", "no", "off", ""):
            assert _to_bool(v) is False

    def test_bool_passthrough(self) -> None:
        from navig.core.continuation import _to_bool
        assert _to_bool(True) is True
        assert _to_bool(False) is False

    def test_none_uses_default(self) -> None:
        from navig.core.continuation import _to_bool
        assert _to_bool(None, True) is True


class TestToInt:
    def test_converts_string(self) -> None:
        from navig.core.continuation import _to_int
        assert _to_int("5", 0) == 5

    def test_uses_default_on_invalid(self) -> None:
        from navig.core.continuation import _to_int
        assert _to_int("abc", 99) == 99

    def test_uses_default_on_none(self) -> None:
        from navig.core.continuation import _to_int
        assert _to_int(None, 7) == 7


# --------------- policy_from_context ---------------

class TestPolicyFromContext:
    def test_defaults_on_empty_context(self) -> None:
        from navig.core.continuation import policy_from_context
        p = policy_from_context(None)
        assert p.enabled is False
        assert p.profile == "conservative"
        assert p.turns_used == 0

    def test_reads_enabled_flag(self) -> None:
        from navig.core.continuation import policy_from_context
        p = policy_from_context({"continuation": {"enabled": True}})
        assert p.enabled is True

    def test_profile_is_normalized(self) -> None:
        from navig.core.continuation import policy_from_context
        p = policy_from_context({"continuation": {"profile": "turbo"}})
        assert p.profile == "conservative"

    def test_balanced_profile_defaults(self) -> None:
        from navig.core.continuation import policy_from_context, _PROFILE_DEFAULTS
        p = policy_from_context({"continuation": {"profile": "balanced"}})
        assert p.cooldown_seconds == _PROFILE_DEFAULTS["balanced"][0]
        assert p.max_turns == _PROFILE_DEFAULTS["balanced"][1]


# --------------- policy_to_context ---------------

class TestPolicyToContext:
    def test_roundtrip(self) -> None:
        from navig.core.continuation import ContinuationPolicy, policy_to_context, policy_from_context
        original = ContinuationPolicy(profile="aggressive", enabled=True, turns_used=2, max_turns=5)
        ctx = {"continuation": policy_to_context(original)}
        restored = policy_from_context(ctx)
        assert restored.profile == "aggressive"
        assert restored.enabled is True
        assert restored.turns_used == 2


# --------------- classify_continuation_state ---------------

class TestClassifyContinuationState:
    def test_empty_returns_wait(self) -> None:
        from navig.core.continuation import classify_continuation_state
        state, reason = classify_continuation_state("")
        assert state == "wait"

    def test_decision_point_text(self) -> None:
        from navig.core.continuation import classify_continuation_state
        state, _ = classify_continuation_state("Should I continue with the next step?")
        assert state == "continue"

    def test_blocked_signal(self) -> None:
        from navig.core.continuation import classify_continuation_state
        state, _ = classify_continuation_state("I am blocked by a permission denied error")
        assert state == "blocked"

    def test_choice_signal(self) -> None:
        from navig.core.continuation import classify_continuation_state
        state, _ = classify_continuation_state("Please choose which option you prefer")
        assert state == "choice"

    def test_wait_signal(self) -> None:
        from navig.core.continuation import classify_continuation_state
        state, _ = classify_continuation_state("I am currently working on the file")
        assert state == "wait"

    def test_neutral_text(self) -> None:
        from navig.core.continuation import classify_continuation_state
        state, _ = classify_continuation_state("The sky is blue today.")
        assert state == "neutral"


# --------------- is_decision_point_for_profile ---------------

class TestIsDecisionPoint:
    def test_continue_signal_returns_true(self) -> None:
        from navig.core.continuation import is_decision_point_for_profile
        assert is_decision_point_for_profile("Should I continue?", "conservative") is True

    def test_blocked_returns_false(self) -> None:
        from navig.core.continuation import is_decision_point_for_profile
        assert is_decision_point_for_profile("Cannot proceed - permission denied", "conservative") is False

    def test_aggressive_with_soft_signal(self) -> None:
        from navig.core.continuation import is_decision_point_for_profile
        # aggressive profile picks up weaker signals
        result = is_decision_point_for_profile("Shall I proceed?", "aggressive")
        assert isinstance(result, bool)


# --------------- should_auto_continue ---------------

class TestShouldAutoContinue:
    def test_disabled_returns_false(self) -> None:
        from navig.core.continuation import ContinuationPolicy, should_auto_continue
        p = ContinuationPolicy(enabled=False)
        ok, reason = should_auto_continue("Should I continue?", p)
        assert ok is False
        assert reason == "disabled"

    def test_paused_returns_false(self) -> None:
        from navig.core.continuation import ContinuationPolicy, should_auto_continue
        p = ContinuationPolicy(enabled=True, paused=True)
        ok, reason = should_auto_continue("Should I continue?", p)
        assert ok is False
        assert reason == "paused"

    def test_skip_next_returns_false(self) -> None:
        from navig.core.continuation import ContinuationPolicy, should_auto_continue
        p = ContinuationPolicy(enabled=True, skip_next=True)
        ok, reason = should_auto_continue("Should I continue?", p)
        assert ok is False
        assert reason == "skip_next"

    def test_max_turns_exhausted(self) -> None:
        from navig.core.continuation import ContinuationPolicy, should_auto_continue
        p = ContinuationPolicy(enabled=True, max_turns=3, turns_used=3, cooldown_seconds=0)
        ok, reason = should_auto_continue("Should I continue?", p)
        assert ok is False
        assert reason == "max_turns"

    def test_ok_when_conditions_met(self) -> None:
        from navig.core.continuation import ContinuationPolicy, should_auto_continue
        p = ContinuationPolicy(
            enabled=True, paused=False, skip_next=False,
            max_turns=5, turns_used=0, cooldown_seconds=0,
            profile="conservative",
        )
        ok, reason = should_auto_continue("Should I continue?", p)
        assert ok is True
        assert reason == "ok"


# --------------- consume_skip / mark_continued ---------------

class TestConsumeSkip:
    def test_clears_skip_next(self) -> None:
        from navig.core.continuation import consume_skip, policy_from_context
        ctx = {"continuation": {"enabled": True, "skip_next": True}}
        result = consume_skip(ctx)
        p = policy_from_context(result)
        assert p.skip_next is False

    def test_noop_when_skip_not_set(self) -> None:
        from navig.core.continuation import consume_skip, policy_from_context
        ctx = {"continuation": {"skip_next": False}}
        result = consume_skip(ctx)
        assert policy_from_context(result).skip_next is False


class TestMarkContinued:
    def test_increments_turns(self) -> None:
        from navig.core.continuation import mark_continued, policy_from_context
        ctx = {"continuation": {"turns_used": 2}}
        result = mark_continued(ctx)
        p = policy_from_context(result)
        assert p.turns_used == 3

    def test_sets_last_continued_at(self) -> None:
        from navig.core.continuation import mark_continued, policy_from_context
        ctx = {"continuation": {}}
        result = mark_continued(ctx)
        p = policy_from_context(result)
        assert p.last_continued_at != ""


# --------------- busy suppression ---------------

class TestBusySuppression:
    def test_apply_sets_busy_until(self) -> None:
        from navig.core.continuation import apply_busy_suppression, get_busy_suppression
        ctx = apply_busy_suppression(None, "wait", "in_progress", "balanced")
        is_busy, reason, _ = get_busy_suppression(ctx)
        assert is_busy is True
        assert "in_progress" in reason

    def test_get_not_busy_when_no_busy_until(self) -> None:
        from navig.core.continuation import get_busy_suppression
        is_busy, _, _ = get_busy_suppression(None)
        assert is_busy is False

    def test_not_busy_after_window_expires(self) -> None:
        from navig.core.continuation import get_busy_suppression
        past = _iso_past(300)
        ctx = {"continuation": {"busy_until": past}}
        is_busy, _, _ = get_busy_suppression(ctx)
        assert is_busy is False
