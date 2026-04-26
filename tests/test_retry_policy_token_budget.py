"""
Batch 41 — navig/retry_policy.py + navig/token_budget.py
Pure-logic tests, no I/O. Async helpers mocked to avoid real sleeps.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

# ─────────────────────────────────────────────────────────────
# navig.retry_policy
# ─────────────────────────────────────────────────────────────

from navig.retry_policy import (
    BackoffPolicy,
    TUNNEL_RECONNECT,
    TELEGRAM_POLLING,
    API_RATE_LIMIT,
    CHANNEL_RESTART,
    backoff_sleep,
    backoff_sleep_sync,
)


class TestBackoffPolicyDefaults:
    def test_default_initial_ms(self):
        p = BackoffPolicy()
        assert p.initial_ms == 5_000

    def test_default_max_ms(self):
        p = BackoffPolicy()
        assert p.max_ms == 300_000

    def test_default_factor(self):
        p = BackoffPolicy()
        assert p.factor == 2.0

    def test_default_jitter(self):
        p = BackoffPolicy()
        assert p.jitter == 0.1

    def test_frozen(self):
        p = BackoffPolicy()
        with pytest.raises((AttributeError, TypeError)):
            p.initial_ms = 9999


class TestBackoffPolicyDelayMs:
    def test_attempt_0_returns_near_initial(self):
        p = BackoffPolicy(initial_ms=1000, max_ms=60_000, factor=2.0, jitter=0.0)
        assert p.delay_ms(0) == pytest.approx(1000.0)

    def test_attempt_1_doubles(self):
        p = BackoffPolicy(initial_ms=1000, max_ms=60_000, factor=2.0, jitter=0.0)
        assert p.delay_ms(1) == pytest.approx(2000.0)

    def test_attempt_2_quadruples(self):
        p = BackoffPolicy(initial_ms=1000, max_ms=60_000, factor=2.0, jitter=0.0)
        assert p.delay_ms(2) == pytest.approx(4000.0)

    def test_capped_at_max_ms(self):
        p = BackoffPolicy(initial_ms=1000, max_ms=5_000, factor=2.0, jitter=0.0)
        # attempt 10 → 1000 * 2^10 = 1_024_000, capped at 5_000
        assert p.delay_ms(10) == pytest.approx(5000.0)

    def test_jitter_adds_randomness(self):
        p = BackoffPolicy(initial_ms=1000, max_ms=60_000, factor=2.0, jitter=0.5)
        results = {p.delay_ms(0) for _ in range(20)}
        # With jitter=0.5, spread is ±500ms — should get different values
        assert len(results) > 1

    def test_delay_s_is_delay_ms_divided_by_1000(self):
        p = BackoffPolicy(initial_ms=2000, max_ms=60_000, factor=2.0, jitter=0.0)
        assert p.delay_s(0) == pytest.approx(p.delay_ms(0) / 1000.0)


class TestPreDefinedPolicies:
    def test_tunnel_reconnect_is_backoff_policy(self):
        assert isinstance(TUNNEL_RECONNECT, BackoffPolicy)

    def test_tunnel_initial_ms(self):
        assert TUNNEL_RECONNECT.initial_ms == 2_000

    def test_tunnel_max_ms(self):
        assert TUNNEL_RECONNECT.max_ms == 120_000

    def test_telegram_polling_is_backoff_policy(self):
        assert isinstance(TELEGRAM_POLLING, BackoffPolicy)

    def test_telegram_initial_ms(self):
        assert TELEGRAM_POLLING.initial_ms == 5_000

    def test_api_rate_limit_is_backoff_policy(self):
        assert isinstance(API_RATE_LIMIT, BackoffPolicy)

    def test_api_rate_limit_initial_ms(self):
        assert API_RATE_LIMIT.initial_ms == 1_000

    def test_channel_restart_is_backoff_policy(self):
        assert isinstance(CHANNEL_RESTART, BackoffPolicy)


class TestBackoffSleepAsync:
    def test_attempt_0_does_not_sleep(self):
        p = BackoffPolicy(initial_ms=5000, max_ms=60_000, factor=2.0)
        slept = []

        async def run():
            with patch("asyncio.sleep", AsyncMock(side_effect=lambda s: slept.append(s))):
                await backoff_sleep(p, 0)

        asyncio.run(run())
        assert slept == []

    def test_negative_attempt_does_not_sleep(self):
        p = BackoffPolicy()
        slept = []

        async def run():
            with patch("asyncio.sleep", AsyncMock(side_effect=lambda s: slept.append(s))):
                await backoff_sleep(p, -1)

        asyncio.run(run())
        assert slept == []

    def test_attempt_1_sleeps(self):
        p = BackoffPolicy(initial_ms=1000, max_ms=60_000, factor=2.0, jitter=0.0)
        slept = []

        async def run():
            with patch("asyncio.sleep", AsyncMock(side_effect=lambda s: slept.append(s))):
                await backoff_sleep(p, 1)

        asyncio.run(run())
        assert len(slept) == 1
        assert slept[0] == pytest.approx(2.0)  # 1000 * 2^1 / 1000 = 2s


class TestBackoffSleepSync:
    def test_attempt_0_does_not_sleep(self):
        p = BackoffPolicy()
        with patch("time.sleep") as mock_sleep:
            backoff_sleep_sync(p, 0)
            mock_sleep.assert_not_called()

    def test_negative_does_not_sleep(self):
        p = BackoffPolicy()
        with patch("time.sleep") as mock_sleep:
            backoff_sleep_sync(p, -5)
            mock_sleep.assert_not_called()

    def test_attempt_1_sleeps(self):
        p = BackoffPolicy(initial_ms=1000, max_ms=60_000, factor=2.0, jitter=0.0)
        with patch("time.sleep") as mock_sleep:
            backoff_sleep_sync(p, 1)
            mock_sleep.assert_called_once()
            args = mock_sleep.call_args[0]
            assert args[0] == pytest.approx(2.0)


# ─────────────────────────────────────────────────────────────
# navig.token_budget
# ─────────────────────────────────────────────────────────────

from navig.token_budget import (
    BudgetTracker,
    ContinueDecision,
    StopDecision,
    create_budget_tracker,
    update_tracker,
    check_budget,
)

# cfg that matches the documented defaults
_CFG = {
    "max_continuations": 8,
    "min_continuation_count": 3,
    "min_delta_tokens": 500,
    "consecutive_low_delta": 2,
}


class TestBudgetTracker:
    def test_defaults(self):
        t = BudgetTracker()
        assert t.continuation_count == 0
        assert t.last_total_tokens == 0
        assert t.delta_history == []

    def test_started_at_set(self):
        t = BudgetTracker()
        assert t.started_at > 0


class TestCreateBudgetTracker:
    def test_returns_budget_tracker(self):
        t = create_budget_tracker()
        assert isinstance(t, BudgetTracker)

    def test_fresh_tracker_has_zero_count(self):
        t = create_budget_tracker()
        assert t.continuation_count == 0


class TestUpdateTracker:
    def test_increments_continuation_count(self):
        t = create_budget_tracker()
        t2 = update_tracker(t, 1000)
        assert t2.continuation_count == 1

    def test_original_tracker_unmodified(self):
        t = create_budget_tracker()
        update_tracker(t, 1000)
        assert t.continuation_count == 0

    def test_delta_appended(self):
        t = create_budget_tracker()
        t2 = update_tracker(t, 1000)
        assert t2.delta_history == [1000]

    def test_delta_is_difference_from_previous(self):
        t = update_tracker(create_budget_tracker(), 1000)
        t2 = update_tracker(t, 1500)
        assert t2.delta_history[-1] == 500

    def test_negative_total_clamped_to_zero_delta(self):
        t = update_tracker(create_budget_tracker(), 2000)
        t2 = update_tracker(t, 1000)  # smaller → delta clamped to 0
        assert t2.delta_history[-1] == 0

    def test_last_total_tokens_updated(self):
        t = update_tracker(create_budget_tracker(), 1234)
        assert t.last_total_tokens == 1234


class TestCheckBudgetContinue:
    def test_fresh_tracker_continues(self):
        t = create_budget_tracker()
        result = check_budget(t, _CFG)
        assert isinstance(result, ContinueDecision)
        assert result.action == "continue"

    def test_below_min_cont_count_continues(self):
        t = create_budget_tracker()
        for i in range(2):  # 2 turns < min_continuation_count=3
            t = update_tracker(t, (i + 1) * 100)
        result = check_budget(t, _CFG)
        assert isinstance(result, ContinueDecision)

    def test_high_deltas_continue(self):
        # 4 turns, all with large deltas
        t = create_budget_tracker()
        for i in range(4):
            t = update_tracker(t, (i + 1) * 5000)
        result = check_budget(t, _CFG)
        assert isinstance(result, ContinueDecision)

    def test_nudge_message_appears_near_cap(self):
        # Push continuation_count to max_continuations - 2 = 6
        t = create_budget_tracker()
        t2 = BudgetTracker(continuation_count=6, last_total_tokens=10000, delta_history=[1000] * 6, started_at=t.started_at)
        result = check_budget(t2, _CFG)
        assert isinstance(result, ContinueDecision)
        assert result.nudge_message  # non-empty


class TestCheckBudgetStop:
    def test_hard_cap_stops(self):
        t = BudgetTracker(continuation_count=8, delta_history=[1000] * 8, last_total_tokens=8000, started_at=0.0)
        result = check_budget(t, _CFG)
        assert isinstance(result, StopDecision)
        assert result.action == "stop"

    def test_hard_cap_completion_event(self):
        t = BudgetTracker(continuation_count=10, delta_history=[1000] * 10, last_total_tokens=10000, started_at=0.0)
        result = check_budget(t, _CFG)
        assert result.completion_event == "token_budget_hard_cap"

    def test_diminishing_returns_stops(self):
        # 3 continuations, last 2 deltas both below 500
        t = BudgetTracker(
            continuation_count=3,
            delta_history=[5000, 100, 50],  # last 2 < 500
            last_total_tokens=5150,
            started_at=0.0,
        )
        result = check_budget(t, _CFG)
        assert isinstance(result, StopDecision)
        assert result.completion_event == "token_budget_diminishing"

    def test_diminishing_returns_reason_contains_count(self):
        t = BudgetTracker(
            continuation_count=4,
            delta_history=[5000, 200, 100, 50],
            last_total_tokens=5350,
            started_at=0.0,
        )
        result = check_budget(t, _CFG)
        assert "4" in result.reason

    def test_one_low_delta_not_enough_to_stop(self):
        t = BudgetTracker(
            continuation_count=3,
            delta_history=[5000, 5000, 50],  # only 1 low delta, needs 2
            last_total_tokens=10050,
            started_at=0.0,
        )
        result = check_budget(t, _CFG)
        assert isinstance(result, ContinueDecision)

    def test_custom_cfg_overrides_defaults(self):
        # max_continuations=2, any 2 continuations should hard stop
        cfg = dict(_CFG)
        cfg["max_continuations"] = 2
        t = BudgetTracker(continuation_count=2, delta_history=[1000, 1000], last_total_tokens=2000, started_at=0.0)
        result = check_budget(t, cfg)
        assert isinstance(result, StopDecision)
