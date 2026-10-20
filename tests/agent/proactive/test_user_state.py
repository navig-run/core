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

import json
import time
from unittest.mock import patch

import pytest

# Imported WITHOUT patching config_dir. This used to import inside
# `patch("navig.platform.paths.config_dir", return_value=Path("/tmp/navig_test_state"))`,
# which poisoned every module loaded for the first time inside that block: they bind the
# function by value (`from navig.platform.paths import config_dir`), so the MagicMock stayed
# bound in their namespace AFTER the patch exited. `navig.agent.soul` is one of them, and its
# `SOUL_FILE` property then answered `/tmp/navig_test_state/workspace/SOUL.md` for the rest of
# the xdist worker -- failing tests/core/test_call_time_paths.py::test_soul_file_property,
# in a different file, whenever the two landed in the same worker.
#
# The patch was also unnecessary: user_state resolves `config_dir()` in
# `UserStateTracker.__init__` (call time), not at import, and soul.py's SOUL_FILE is a
# property for exactly that reason. Nothing here is frozen at import any more.
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
        assert p.chat_mode == "standard"
        assert p.verbosity == "normal"
        assert p.voice_enabled is False
        assert p.quiet_hours_start == 23
        assert p.quiet_hours_end == 7
        assert p.autonomy_level == "balanced"
        assert p.notifications_enabled is True
        assert p.dnd_enabled is False
        assert p.mode_migrated is False

    def test_valid_modes_are_standard_and_creative(self):
        assert "standard" in UserPreferences._VALID_MODES
        assert "creative" in UserPreferences._VALID_MODES
        assert set(UserPreferences._VALID_MODES) == {"standard", "creative"}

    def test_legacy_modes_are_retired_from_the_valid_set(self):
        for legacy in ("work", "deep-focus", "planning", "relax", "sleep"):
            assert legacy not in UserPreferences._VALID_MODES
        # …and named only so load-migration can map them all onto Standard.
        assert set(UserPreferences._LEGACY_MODES) == {
            "work", "deep-focus", "planning", "creative", "relax", "sleep",
        }

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
        assert tracker.preferences.chat_mode == "standard"

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


# ─────────────────────────────────────────────────────────────
# Agent-mode migration + DND decoupling (owner decision 2026-10-20)
# ─────────────────────────────────────────────────────────────


def _seed_state(state_dir, **prefs):
    """Write a user_state.json exactly as the PRE-migration system would."""
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "user_state.json").write_text(
        json.dumps(
            {"stats": {}, "config": {}, "preferences": prefs, "updated_at": "2026-01-01T00:00:00"}
        ),
        encoding="utf-8",
    )


class TestAgentModeMigration:
    def test_six_legacy_values_migrate_to_standard(self, tmp_path):
        """All six focus modes — including the OLD tone creative — land on Standard,
        and deep-focus/sleep additionally arm DND so suppression is not lost."""
        for legacy in UserPreferences._LEGACY_MODES:
            state_dir = tmp_path / f"mig-{legacy}"
            _seed_state(state_dir, chat_mode=legacy)
            tracker = UserStateTracker(state_dir=state_dir)
            assert tracker.get_preference("chat_mode") == "standard", legacy
            expect_dnd = legacy in UserPreferences._LEGACY_DND_MODES
            assert tracker.preferences.dnd_enabled is expect_dnd, legacy
            assert tracker.preferences.mode_migrated is True

    def test_migration_is_persisted_once_and_idempotent(self, tmp_path):
        state_dir = tmp_path / "mig-once"
        _seed_state(state_dir, chat_mode="deep-focus")
        UserStateTracker(state_dir=state_dir)  # first load migrates + re-saves
        saved = json.loads((state_dir / "user_state.json").read_text(encoding="utf-8"))
        assert saved["preferences"]["chat_mode"] == "standard"
        assert saved["preferences"]["mode_migrated"] is True
        assert saved["preferences"]["dnd_enabled"] is True
        # A user who then turns DND off is never re-armed by a later load.
        tracker = UserStateTracker(state_dir=state_dir)
        assert tracker.set_preference("dnd_enabled", False) is True
        again = UserStateTracker(state_dir=state_dir)
        assert again.preferences.dnd_enabled is False

    def test_legacy_creative_never_becomes_capability_creative(self, tmp_path):
        """A stored OLD `creative` (chat tone) must not arm capability authoring."""
        state_dir = tmp_path / "mig-oldcreative"
        _seed_state(state_dir, chat_mode="creative")
        tracker = UserStateTracker(state_dir=state_dir)
        assert tracker.get_preference("chat_mode") == "standard"

    def test_new_schema_creative_survives_reload(self, tmp_path):
        """Once written under the new schema, `creative` IS capability mode and must
        not be re-migrated on the next boot."""
        state_dir = tmp_path / "mig-newcreative"
        _seed_state(state_dir, chat_mode="standard", mode_migrated=True)
        tracker = UserStateTracker(state_dir=state_dir)
        assert tracker.set_preference("chat_mode", "creative") is True
        reloaded = UserStateTracker(state_dir=state_dir)
        assert reloaded.get_preference("chat_mode") == "creative"

    def test_unknown_value_on_migrated_file_degrades_to_standard(self, tmp_path):
        state_dir = tmp_path / "mig-unknown"
        _seed_state(state_dir, chat_mode="half-life", mode_migrated=True)
        tracker = UserStateTracker(state_dir=state_dir)
        assert tracker.get_preference("chat_mode") == "standard"

    def test_set_preference_rejects_retired_ids(self, tmp_path):
        with patch("navig.agent.proactive.user_state.UserStateTracker._load_state"):
            tracker = UserStateTracker(state_dir=tmp_path / "reject")
        for retired in ("work", "deep-focus", "sleep", "balance"):
            assert tracker.set_preference("chat_mode", retired) is False


class TestDndDecoupledFromMode:
    def test_mode_never_gates_do_not_disturb(self, tmp_path):
        with patch("navig.agent.proactive.user_state.UserStateTracker._load_state"):
            tracker = UserStateTracker(state_dir=tmp_path / "dnd")
        # Both modes, notifications on, DND off → never DND.
        for mode in ("standard", "creative"):
            assert tracker.set_preference("chat_mode", mode) is True
            assert tracker.is_do_not_disturb() is False

    def test_dnd_preference_holds_in_either_mode(self, tmp_path):
        with patch("navig.agent.proactive.user_state.UserStateTracker._load_state"):
            tracker = UserStateTracker(state_dir=tmp_path / "dnd2")
        assert tracker.set_preference("dnd_enabled", True) is True
        for mode in ("standard", "creative"):
            tracker.set_preference("chat_mode", mode)
            assert tracker.is_do_not_disturb() is True

    def test_notifications_disabled_alone_is_still_dnd(self, tmp_path):
        with patch("navig.agent.proactive.user_state.UserStateTracker._load_state"):
            tracker = UserStateTracker(state_dir=tmp_path / "dnd3")
        assert tracker.set_preference("notifications_enabled", False) is True
        assert tracker.is_do_not_disturb() is True

    def test_suppression_priority_matrix_unchanged(self, tmp_path):
        """DND blocks LOW/NORMAL/HIGH, CRITICAL (4) always passes; quiet hours block
        LOW/NORMAL only — the exact behaviour that hung off the old modes."""
        with patch("navig.agent.proactive.user_state.UserStateTracker._load_state"):
            tracker = UserStateTracker(state_dir=tmp_path / "dnd4")
        tracker.set_preference("dnd_enabled", True)
        for prio in (1, 2, 3):
            assert tracker.should_suppress_notification(priority=prio) is True, prio
        assert tracker.should_suppress_notification(priority=4) is False

    def test_dnd_enabled_accepts_string_false(self, tmp_path):
        """`"false"` must never become truthy — the coerce_bool trap."""
        with patch("navig.agent.proactive.user_state.UserStateTracker._load_state"):
            tracker = UserStateTracker(state_dir=tmp_path / "dnd5")
        assert tracker.set_preference("dnd_enabled", "true") is True
        assert tracker.preferences.dnd_enabled is True
        assert tracker.set_preference("dnd_enabled", "false") is True
        assert tracker.preferences.dnd_enabled is False
