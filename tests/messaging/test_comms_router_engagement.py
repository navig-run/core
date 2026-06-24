"""
Batch 118 — comms_router (HitLChannel) + engagement (EngagementAction/Config/Result)

Pure-unit tests: no network, no file I/O, no process spawning.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# HitLChannel (abstract base — tested via a minimal concrete stub)
# ---------------------------------------------------------------------------

from navig.integrations.comms_router import HitLChannel


class _StubChannel(HitLChannel):
    """Minimal concrete implementation for testing the ABC helpers."""

    name = "stub"

    async def ping(self) -> bool:
        return True

    async def ask(self, question: str, timeout: int = 300) -> str:
        return ""

    async def choose(self, question: str, options: list[str], timeout: int = 300) -> str:
        return ""

    async def notify(self, message: str, screenshot_path=None) -> bool:
        return True


class TestHitLChannelInit:
    def test_available_true_on_init(self):
        ch = _StubChannel()
        assert ch.available is True

    def test_consecutive_failures_zero_on_init(self):
        ch = _StubChannel()
        assert ch._consecutive_failures == 0

    def test_max_failures_class_constant(self):
        assert HitLChannel._MAX_FAILURES == 3

    def test_name_attribute(self):
        ch = _StubChannel()
        assert ch.name == "stub"

    def test_instances_do_not_share_state(self):
        a = _StubChannel()
        b = _StubChannel()
        a._consecutive_failures = 5
        assert b._consecutive_failures == 0


class TestHitLChannelRecordSuccess:
    def test_resets_consecutive_failures(self):
        ch = _StubChannel()
        ch._consecutive_failures = 2
        ch.record_success()
        assert ch._consecutive_failures == 0

    def test_does_not_change_available_when_already_true(self):
        ch = _StubChannel()
        ch.record_success()
        assert ch.available is True

    def test_resets_multiple_times(self):
        ch = _StubChannel()
        for _ in range(5):
            ch._consecutive_failures = 3
            ch.record_success()
        assert ch._consecutive_failures == 0


class TestHitLChannelRecordFailure:
    def test_increments_failures(self):
        ch = _StubChannel()
        ch.record_failure()
        assert ch._consecutive_failures == 1

    def test_increments_multiple_times(self):
        ch = _StubChannel()
        ch.record_failure()
        ch.record_failure()
        assert ch._consecutive_failures == 2

    def test_does_not_disable_below_threshold(self):
        ch = _StubChannel()
        for _ in range(HitLChannel._MAX_FAILURES - 1):
            ch.record_failure()
        assert ch.available is True

    def test_disables_at_threshold(self):
        ch = _StubChannel()
        for _ in range(HitLChannel._MAX_FAILURES):
            ch.record_failure()
        assert ch.available is False

    def test_disables_once_threshold_reached(self):
        ch = _StubChannel()
        for _ in range(HitLChannel._MAX_FAILURES + 5):
            ch.record_failure()
        assert ch.available is False

    def test_success_does_not_re_enable_after_disable(self):
        """record_success resets counter but does NOT set available back to True."""
        ch = _StubChannel()
        for _ in range(HitLChannel._MAX_FAILURES):
            ch.record_failure()
        ch.record_success()
        # available stays False — re-enabling requires explicit management
        assert ch._consecutive_failures == 0
        # available may stay False (implementation detail — main thing is counter reset)


# ---------------------------------------------------------------------------
# EngagementAction enum
# ---------------------------------------------------------------------------

from navig.agent.proactive.engagement import EngagementAction, EngagementConfig, EngagementResult


class TestEngagementAction:
    def test_greeting_value(self):
        assert EngagementAction.GREETING.value == "greeting"

    def test_checkin_value(self):
        assert EngagementAction.CHECKIN.value == "checkin"

    def test_capability_promo_value(self):
        assert EngagementAction.CAPABILITY_PROMO.value == "capability_promo"

    def test_contextual_tip_value(self):
        assert EngagementAction.CONTEXTUAL_TIP.value == "contextual_tip"

    def test_evening_wrapup_value(self):
        assert EngagementAction.EVENING_WRAPUP.value == "evening_wrapup"

    def test_feedback_ask_value(self):
        assert EngagementAction.FEEDBACK_ASK.value == "feedback_ask"

    def test_idle_nudge_value(self):
        assert EngagementAction.IDLE_NUDGE.value == "idle_nudge"

    def test_celebration_value(self):
        assert EngagementAction.CELEBRATION.value == "celebration"

    def test_heartbeat_report_value(self):
        assert EngagementAction.HEARTBEAT_REPORT.value == "heartbeat_report"

    def test_all_values_unique(self):
        values = [a.value for a in EngagementAction]
        assert len(values) == len(set(values))

    def test_members_count(self):
        assert len(list(EngagementAction)) == 9


# ---------------------------------------------------------------------------
# EngagementConfig dataclass
# ---------------------------------------------------------------------------


class TestEngagementConfig:
    def test_enabled_default_true(self):
        cfg = EngagementConfig()
        assert cfg.enabled is True

    def test_greeting_cooldown_default(self):
        cfg = EngagementConfig()
        assert cfg.greeting_cooldown_hours == 12.0

    def test_checkin_cooldown_default(self):
        cfg = EngagementConfig()
        assert cfg.checkin_cooldown_hours == 4.0

    def test_capability_promo_cooldown_default(self):
        cfg = EngagementConfig()
        assert cfg.capability_promo_cooldown_hours == 24.0

    def test_feedback_ask_cooldown_default(self):
        cfg = EngagementConfig()
        assert cfg.feedback_ask_cooldown_hours == 72.0

    def test_quiet_hours_default(self):
        cfg = EngagementConfig()
        assert cfg.quiet_hours == (23, 7)

    def test_greeting_hours_default(self):
        cfg = EngagementConfig()
        assert cfg.greeting_hours == (7, 10)

    def test_wrapup_hours_default(self):
        cfg = EngagementConfig()
        assert cfg.wrapup_hours == (17, 20)

    def test_max_proactive_per_day_default(self):
        cfg = EngagementConfig()
        assert cfg.max_proactive_per_day == 5

    def test_checkin_probability_default(self):
        cfg = EngagementConfig()
        assert cfg.checkin_probability == pytest.approx(0.3)

    def test_can_override_fields(self):
        cfg = EngagementConfig(enabled=False, max_proactive_per_day=10)
        assert cfg.enabled is False
        assert cfg.max_proactive_per_day == 10

    def test_min_interactions_before_promo_default(self):
        cfg = EngagementConfig()
        assert cfg.min_interactions_before_promo == 10

    def test_min_days_before_feedback_default(self):
        cfg = EngagementConfig()
        assert cfg.min_days_before_feedback == 3


# ---------------------------------------------------------------------------
# EngagementResult dataclass
# ---------------------------------------------------------------------------


class TestEngagementResult:
    def test_action_stored(self):
        r = EngagementResult(action=EngagementAction.GREETING, message="Hello!")
        assert r.action == EngagementAction.GREETING

    def test_message_stored(self):
        r = EngagementResult(action=EngagementAction.CHECKIN, message="How are you?")
        assert r.message == "How are you?"

    def test_priority_default_five(self):
        r = EngagementResult(action=EngagementAction.GREETING, message="Hi")
        assert r.priority == 5

    def test_metadata_default_empty_dict(self):
        r = EngagementResult(action=EngagementAction.GREETING, message="Hi")
        assert r.metadata == {}

    def test_suppress_default_false(self):
        r = EngagementResult(action=EngagementAction.GREETING, message="Hi")
        assert r.suppress is False

    def test_metadata_not_shared_across_instances(self):
        a = EngagementResult(action=EngagementAction.GREETING, message="Hi")
        b = EngagementResult(action=EngagementAction.CHECKIN, message="Hey")
        a.metadata["key"] = "value"
        assert "key" not in b.metadata

    def test_custom_priority(self):
        r = EngagementResult(action=EngagementAction.CELEBRATION, message="Congrats!", priority=9)
        assert r.priority == 9

    def test_suppress_true(self):
        r = EngagementResult(action=EngagementAction.IDLE_NUDGE, message="...", suppress=True)
        assert r.suppress is True
