"""
Hermetic unit tests for navig.agent.proactive.user_state

Covers:
- OperatorState / TimeOfDay enum values
- UserPreferences defaults and valid value sets
- InteractionRecord fields
- UsageStats defaults
- UserStateTracker class constants and init
- UserStateTracker.record_interaction stats update
"""

from pathlib import Path
from unittest.mock import patch, MagicMock
import time

import pytest


# Patch config_dir and debug logger before import
with (
    patch("navig.debug_logger.get_debug_logger"),
    patch("navig.platform.paths.config_dir", return_value=Path("/tmp/navig_test_state")),
):
    from navig.agent.proactive.user_state import (
        InteractionRecord,
        OperatorState,
        TimeOfDay,
        UsageStats,
        UserPreferences,
        UserStateTracker,
    )


# ─────────────────────────────────────────────────────────────
# OperatorState
# ─────────────────────────────────────────────────────────────


class TestOperatorState:
    def test_unknown_value(self):
        assert OperatorState.UNKNOWN.value == "unknown"

    def test_active_value(self):
        assert OperatorState.ACTIVE.value == "active"

    def test_idle_value(self):
        assert OperatorState.IDLE.value == "idle"

    def test_away_value(self):
        assert OperatorState.AWAY.value == "away"

    def test_deep_work_value(self):
        assert OperatorState.DEEP_WORK.value == "deep_work"

    def test_just_arrived_value(self):
        assert OperatorState.JUST_ARRIVED.value == "just_arrived"

    def test_winding_down_value(self):
        assert OperatorState.WINDING_DOWN.value == "winding_down"

    def test_all_values_unique(self):
        vals = [s.value for s in OperatorState]
        assert len(vals) == len(set(vals))


# ─────────────────────────────────────────────────────────────
# TimeOfDay
# ─────────────────────────────────────────────────────────────


class TestTimeOfDay:
    def test_morning_value(self):
        assert TimeOfDay.MORNING.value == "morning"

    def test_afternoon_value(self):
        assert TimeOfDay.AFTERNOON.value == "afternoon"

    def test_evening_value(self):
        assert TimeOfDay.EVENING.value == "evening"

    def test_night_value(self):
        assert TimeOfDay.NIGHT.value == "night"

    def test_late_night_value(self):
        assert TimeOfDay.LATE_NIGHT.value == "late_night"

    def test_early_morning_value(self):
        assert TimeOfDay.EARLY_MORNING.value == "early_morning"


# ─────────────────────────────────────────────────────────────
# UserPreferences
# ─────────────────────────────────────────────────────────────


class TestUserPreferences:
    def test_defaults(self):
        p = UserPreferences()
        assert p.chat_mode == "work"
        assert p.verbosity == "normal"
        assert p.voice_enabled is False
        assert p.quiet_hours_start == 23
        assert p.quiet_hours_end == 7
        assert p.autonomy_level == "balanced"
        assert p.notifications_enabled is True

    def test_valid_modes_contains_work(self):
        assert "work" in UserPreferences._VALID_MODES

    def test_valid_modes_contains_sleep(self):
        assert "sleep" in UserPreferences._VALID_MODES

    def test_valid_verbosity(self):
        assert "brief" in UserPreferences._VALID_VERBOSITY
        assert "normal" in UserPreferences._VALID_VERBOSITY
        assert "detailed" in UserPreferences._VALID_VERBOSITY

    def test_valid_autonomy(self):
        assert "cautious" in UserPreferences._VALID_AUTONOMY
        assert "balanced" in UserPreferences._VALID_AUTONOMY
        assert "autonomous" in UserPreferences._VALID_AUTONOMY


# ─────────────────────────────────────────────────────────────
# InteractionRecord
# ─────────────────────────────────────────────────────────────


class TestInteractionRecord:
    def test_with_all_fields(self):
        now = time.time()
        rec = InteractionRecord(
            timestamp=now,
            message_type="command",
            command="navig deploy",
            response_time_ms=120.5,
            sentiment="positive",
        )
        assert rec.timestamp == now
        assert rec.message_type == "command"
        assert rec.command == "navig deploy"
        assert rec.response_time_ms == 120.5
        assert rec.sentiment == "positive"

    def test_optional_defaults_none(self):
        rec = InteractionRecord(timestamp=time.time(), message_type="chat")
        assert rec.command is None
        assert rec.response_time_ms is None
        assert rec.sentiment is None


# ─────────────────────────────────────────────────────────────
# UsageStats
# ─────────────────────────────────────────────────────────────


class TestUsageStats:
    def test_defaults_zero(self):
        s = UsageStats()
        assert s.total_messages == 0
        assert s.total_commands == 0
        assert s.active_days == 0
        assert s.avg_session_length_min == 0.0

    def test_defaults_none(self):
        s = UsageStats()
        assert s.first_seen is None
        assert s.last_seen is None
        assert s.last_greeting is None
        assert s.last_checkin is None

    def test_defaults_empty_collections(self):
        s = UsageStats()
        assert s.command_counts == {}
        assert s.peak_hours == []
        assert s.features_used == {}


# ─────────────────────────────────────────────────────────────
# UserStateTracker class constants
# ─────────────────────────────────────────────────────────────


class TestUserStateTrackerConstants:
    def test_idle_threshold(self):
        assert UserStateTracker.IDLE_THRESHOLD_MIN == 15

    def test_away_threshold(self):
        assert UserStateTracker.AWAY_THRESHOLD_HOURS == 1

    def test_just_arrived_threshold(self):
        assert UserStateTracker.JUST_ARRIVED_THRESHOLD_HOURS == 2


# ─────────────────────────────────────────────────────────────
# UserStateTracker init and record_interaction
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def tracker(tmp_path):
    """Create a tracker with a temp state dir, bypassing filesystem load."""
    with patch("navig.agent.proactive.user_state.UserStateTracker._load_state"):
        t = UserStateTracker(state_dir=tmp_path / "engagement")
    return t


class TestUserStateTrackerInit:
    def test_stats_defaults(self, tracker):
        assert tracker.stats.total_messages == 0

    def test_preferences_defaults(self, tracker):
        assert tracker.preferences.chat_mode == "work"

    def test_interactions_list_empty(self, tracker):
        assert tracker._interactions == []

    def test_active_hours(self, tracker):
        assert tracker.active_hours_start == 8
        assert tracker.active_hours_end == 23


class TestRecordInteraction:
    def test_increments_total_messages(self, tracker):
        tracker.record_interaction(message_type="chat")
        assert tracker.stats.total_messages == 1

    def test_sets_first_seen(self, tracker):
        assert tracker.stats.first_seen is None
        tracker.record_interaction()
        assert tracker.stats.first_seen is not None

    def test_updates_last_seen(self, tracker):
        tracker.record_interaction()
        first = tracker.stats.last_seen
        time.sleep(0.01)
        tracker.record_interaction()
        assert tracker.stats.last_seen >= first

    def test_command_increments_total_commands(self, tracker):
        tracker.record_interaction(message_type="command", command="navig deploy")
        assert tracker.stats.total_commands == 1

    def test_command_count_tracked(self, tracker):
        tracker.record_interaction(message_type="command", command="navig deploy")
        tracker.record_interaction(message_type="command", command="navig deploy")
        assert tracker.stats.command_counts.get("navig deploy") == 2

    def test_chat_message_no_command_count(self, tracker):
        tracker.record_interaction(message_type="chat")
        assert tracker.stats.total_commands == 0

    def test_feature_usage_tracked_from_command(self, tracker):
        tracker.record_interaction(message_type="command", command="navig status")
        assert "navig" in tracker.stats.features_used

    def test_interactions_appended(self, tracker):
        tracker.record_interaction(message_type="chat")
        tracker.record_interaction(message_type="command", command="ls")
        assert len(tracker._interactions) == 2
