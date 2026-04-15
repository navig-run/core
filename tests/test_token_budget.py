"""
Tests for navig.token_budget — diminishing-returns continuation guard.
"""
from __future__ import annotations

import pytest

from navig.token_budget import (
    BudgetTracker,
    ContinueDecision,
    StopDecision,
    check_budget,
    create_budget_tracker,
    update_tracker,
)

# Reference config with explicit values so tests aren't config-dependent.
_CFG = {
    "max_continuations": 8,
    "min_continuation_count": 3,
    "min_delta_tokens": 500,
    "consecutive_low_delta": 2,
}


class TestCreateBudgetTracker:
    def test_starts_at_zero(self):
        t = create_budget_tracker()
        assert t.continuation_count == 0
        assert t.last_total_tokens == 0
        assert t.delta_history == []


class TestUpdateTracker:
    def test_increments_count(self):
        t = create_budget_tracker()
        t2 = update_tracker(t, 1000)
        assert t2.continuation_count == 1
        assert t.continuation_count == 0  # original unchanged (pure)

    def test_delta_recorded(self):
        t = create_budget_tracker()
        t = update_tracker(t, 1000)
        t = update_tracker(t, 1800)
        assert t.delta_history == [1000, 800]

    def test_negative_delta_clamped_to_zero(self):
        t = create_budget_tracker()
        t = update_tracker(t, 1000)
        t = update_tracker(t, 900)  # negative delta → clamped
        assert t.delta_history[-1] == 0


class TestCheckBudgetHardCap:
    def test_hard_cap_returns_stop_decision(self):
        t = create_budget_tracker()
        for i in range(8):  # push to max_continuations
            t = update_tracker(t, (i + 1) * 1000)
        decision = check_budget(t, _CFG)
        assert isinstance(decision, StopDecision)
        assert "cap" in decision.reason.lower()
        assert decision.completion_event == "token_budget_hard_cap"

    def test_before_cap_returns_continue(self):
        t = create_budget_tracker()
        for i in range(7):  # one short of cap
            t = update_tracker(t, (i + 1) * 1000)
        decision = check_budget(t, _CFG)
        assert isinstance(decision, ContinueDecision)


class TestCheckBudgetDiminishingReturns:
    def _run_turns(self, deltas: list[int]) -> BudgetTracker:
        t = create_budget_tracker()
        total = 0
        for d in deltas:
            total += d
            t = update_tracker(t, total)
        return t

    def test_diminishing_returns_triggers_stop(self):
        # 3 turns meet min_continuation_count; last 2 deltas both below 500
        t = self._run_turns([2000, 300, 200])  # deltas: 2000, 300, 200
        decision = check_budget(t, _CFG)
        assert isinstance(decision, StopDecision)
        assert decision.completion_event == "token_budget_diminishing"

    def test_one_high_delta_prevents_stop(self):
        # Last 2 deltas: 600 and 200 → not both low
        t = self._run_turns([2000, 600, 200])
        decision = check_budget(t, _CFG)
        # continuation_count == 3 (==min_cont), last 2 deltas [600, 200]
        # 600 >= 500, so NOT diminishing → should continue
        assert isinstance(decision, ContinueDecision)

    def test_below_min_continuation_count_never_stops(self):
        # Only 2 turns (< min_continuation_count=3), both low
        t = self._run_turns([100, 50])
        decision = check_budget(t, _CFG)
        assert isinstance(decision, ContinueDecision)

    def test_nudge_shown_near_cap(self):
        # 6 turns → count=6, near cap of 8 (cap-2)
        t = self._run_turns([1000, 1000, 1000, 1000, 1000, 1000])
        decision = check_budget(t, _CFG)
        assert isinstance(decision, ContinueDecision)
        assert decision.nudge_message != ""


class TestCheckBudgetCustomConfig:
    def test_custom_cap(self):
        cfg = {**_CFG, "max_continuations": 3}
        t = create_budget_tracker()
        for _ in range(3):
            t = update_tracker(t, 1000)
        decision = check_budget(t, cfg)
        assert isinstance(decision, StopDecision)

    def test_custom_min_delta(self):
        cfg = {**_CFG, "min_delta_tokens": 50, "min_continuation_count": 2}
        # Both deltas 10 < 50: should stop
        t = self._run_turns_custom([100, 10, 10])
        decision = check_budget(t, cfg)
        assert isinstance(decision, StopDecision)

    @staticmethod
    def _run_turns_custom(deltas: list[int]) -> BudgetTracker:
        t = create_budget_tracker()
        total = 0
        for d in deltas:
            total += d
            t = update_tracker(t, total)
        return t
