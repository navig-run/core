"""
Batch 120 — commands/triggers: enums, TriggerCondition.evaluate/to_dict/from_dict,
TriggerAction, Trigger.can_fire/_generate_id/to_dict/from_dict, TriggerEvent, TriggerResult.

Pure-unit tests: no network, no file I/O.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta

from navig.commands.triggers import (
    ActionType,
    Trigger,
    TriggerAction,
    TriggerCondition,
    TriggerEvent,
    TriggerResult,
    TriggerStatus,
    TriggerType,
)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestTriggerType:
    def test_health_value(self):
        assert TriggerType.HEALTH.value == "health"

    def test_schedule_value(self):
        assert TriggerType.SCHEDULE.value == "schedule"

    def test_threshold_value(self):
        assert TriggerType.THRESHOLD.value == "threshold"

    def test_webhook_value(self):
        assert TriggerType.WEBHOOK.value == "webhook"

    def test_file_value(self):
        assert TriggerType.FILE.value == "file"

    def test_command_value(self):
        assert TriggerType.COMMAND.value == "command"

    def test_manual_value(self):
        assert TriggerType.MANUAL.value == "manual"

    def test_is_str_subclass(self):
        assert isinstance(TriggerType.HEALTH, str)

    def test_nine_members(self):
        assert len(list(TriggerType)) == 9


class TestTriggerStatus:
    def test_enabled_value(self):
        assert TriggerStatus.ENABLED.value == "enabled"

    def test_disabled_value(self):
        assert TriggerStatus.DISABLED.value == "disabled"

    def test_firing_value(self):
        assert TriggerStatus.FIRING.value == "firing"

    def test_cooldown_value(self):
        assert TriggerStatus.COOLDOWN.value == "cooldown"


class TestActionType:
    def test_command_value(self):
        assert ActionType.COMMAND.value == "command"

    def test_workflow_value(self):
        assert ActionType.WORKFLOW.value == "workflow"

    def test_notify_value(self):
        assert ActionType.NOTIFY.value == "notify"

    def test_webhook_value(self):
        assert ActionType.WEBHOOK.value == "webhook"

    def test_script_value(self):
        assert ActionType.SCRIPT.value == "script"


# ---------------------------------------------------------------------------
# TriggerCondition.evaluate
# ---------------------------------------------------------------------------


class TestTriggerConditionEvaluate:
    def _cond(self, operator, value, target=""):
        return TriggerCondition(type="test", operator=operator, value=value, target=target)

    def test_eq_true(self):
        assert self._cond("eq", "running").evaluate("running") is True

    def test_eq_false(self):
        assert self._cond("eq", "running").evaluate("stopped") is False

    def test_ne_true(self):
        assert self._cond("ne", "stopped").evaluate("running") is True

    def test_ne_false(self):
        assert self._cond("ne", "running").evaluate("running") is False

    def test_gt_true(self):
        assert self._cond("gt", 80).evaluate(90) is True

    def test_gt_false(self):
        assert self._cond("gt", 80).evaluate(70) is False

    def test_lt_true(self):
        assert self._cond("lt", 100).evaluate(50) is True

    def test_lt_false(self):
        assert self._cond("lt", 50).evaluate(100) is False

    def test_gte_equal(self):
        assert self._cond("gte", 80).evaluate(80) is True

    def test_lte_equal(self):
        assert self._cond("lte", 80).evaluate(80) is True

    def test_contains_true(self):
        assert self._cond("contains", "error").evaluate("critical error occurred") is True

    def test_contains_false(self):
        assert self._cond("contains", "error").evaluate("all healthy") is False

    def test_unknown_operator_returns_false(self):
        assert self._cond("unknown_op", "x").evaluate("x") is False

    def test_invalid_numeric_returns_false(self):
        # non-numeric for gt
        assert self._cond("gt", 10).evaluate("not-a-number") is False


# ---------------------------------------------------------------------------
# TriggerCondition.to_dict / from_dict
# ---------------------------------------------------------------------------


class TestTriggerConditionSerialisation:
    def _make(self):
        return TriggerCondition(type="resource", operator="gt", value=90, target="cpu")

    def test_to_dict_type(self):
        assert self._make().to_dict()["type"] == "resource"

    def test_to_dict_operator(self):
        assert self._make().to_dict()["operator"] == "gt"

    def test_to_dict_value(self):
        assert self._make().to_dict()["value"] == 90

    def test_to_dict_target(self):
        assert self._make().to_dict()["target"] == "cpu"

    def test_roundtrip(self):
        original = self._make()
        restored = TriggerCondition.from_dict(original.to_dict())
        assert restored.type == original.type
        assert restored.operator == original.operator
        assert restored.value == original.value
        assert restored.target == original.target

    def test_from_dict_defaults(self):
        c = TriggerCondition.from_dict({})
        assert c.type == ""
        assert c.operator == "eq"
        assert c.target == ""


# ---------------------------------------------------------------------------
# TriggerAction.to_dict / from_dict
# ---------------------------------------------------------------------------


class TestTriggerAction:
    def _make(self):
        return TriggerAction(
            type=ActionType.NOTIFY,
            target="telegram",
            params={"message": "alert"},
            on_failure="continue",
            retries=2,
        )

    def test_type_stored(self):
        assert self._make().type == ActionType.NOTIFY

    def test_target_stored(self):
        assert self._make().target == "telegram"

    def test_retries_stored(self):
        assert self._make().retries == 2

    def test_params_default_empty(self):
        a = TriggerAction(type=ActionType.COMMAND, target="navig run ls")
        assert a.params == {}

    def test_to_dict_type_is_str(self):
        assert self._make().to_dict()["type"] == "notify"

    def test_to_dict_retries(self):
        assert self._make().to_dict()["retries"] == 2

    def test_roundtrip(self):
        original = self._make()
        restored = TriggerAction.from_dict(original.to_dict())
        assert restored.type == original.type
        assert restored.target == original.target
        assert restored.retries == original.retries

    def test_from_dict_defaults(self):
        a = TriggerAction.from_dict({})
        assert a.type == ActionType.COMMAND
        assert a.target == ""
        assert a.on_failure == "continue"


# ---------------------------------------------------------------------------
# Trigger — init, _generate_id, can_fire, to_dict/from_dict
# ---------------------------------------------------------------------------


class TestTrigger:
    def _make(self, **kwargs):
        defaults = dict(id="t-001", name="Test Trigger", type=TriggerType.MANUAL)
        defaults.update(kwargs)
        return Trigger(**defaults)

    def test_id_stored(self):
        assert self._make().id == "t-001"

    def test_name_stored(self):
        assert self._make().name == "Test Trigger"

    def test_type_stored(self):
        assert self._make().type == TriggerType.MANUAL

    def test_status_default_enabled(self):
        assert self._make().status == TriggerStatus.ENABLED

    def test_cooldown_default_60(self):
        assert self._make().cooldown_seconds == 60

    def test_fire_count_default_zero(self):
        assert self._make().fire_count == 0

    def test_created_at_auto_populated(self):
        t = self._make()
        assert len(t.created_at) > 0

    def test_conditions_default_empty(self):
        assert self._make().conditions == []

    def test_actions_default_empty(self):
        assert self._make().actions == []

    def test_tags_default_empty(self):
        assert self._make().tags == []

    # _generate_id
    def test_generate_id_from_name(self):
        t = Trigger(id="", name="My Alert", type=TriggerType.HEALTH)
        assert "my-alert" in t.id

    def test_generate_id_produces_non_empty(self):
        t = Trigger(id="", name="Some Name", type=TriggerType.SCHEDULE)
        assert len(t.id) > 0

    # can_fire
    def test_can_fire_enabled_no_last_fired(self):
        t = self._make()
        assert t.can_fire() is True

    def test_cannot_fire_when_disabled(self):
        t = self._make(status=TriggerStatus.DISABLED)
        assert t.can_fire() is False

    def test_cannot_fire_when_cooldown_status(self):
        t = self._make(status=TriggerStatus.COOLDOWN)
        assert t.can_fire() is False

    def test_cannot_fire_within_cooldown_seconds(self):
        t = self._make(cooldown_seconds=3600)
        t.last_fired = datetime.now().isoformat()
        assert t.can_fire() is False

    def test_can_fire_after_cooldown_elapsed(self):
        t = self._make(cooldown_seconds=1)
        past = datetime.now() - timedelta(seconds=10)
        t.last_fired = past.isoformat()
        assert t.can_fire() is True

    # to_dict / from_dict
    def test_to_dict_id(self):
        assert self._make().to_dict()["id"] == "t-001"

    def test_to_dict_type_is_str(self):
        assert self._make().to_dict()["type"] == "manual"

    def test_to_dict_status_is_str(self):
        assert self._make().to_dict()["status"] == "enabled"

    def test_roundtrip(self):
        original = self._make(name="Roundtrip", type=TriggerType.SCHEDULE)
        restored = Trigger.from_dict(original.to_dict())
        assert restored.id == original.id
        assert restored.name == original.name
        assert restored.type == original.type
        assert restored.status == original.status


# ---------------------------------------------------------------------------
# TriggerEvent & TriggerResult
# ---------------------------------------------------------------------------


class TestTriggerEvent:
    def test_type_stored(self):
        ev = TriggerEvent(type=TriggerType.HEALTH, source="heartbeat", data={"status": "down"})
        assert ev.type == TriggerType.HEALTH

    def test_source_stored(self):
        ev = TriggerEvent(type=TriggerType.MANUAL, source="user", data={})
        assert ev.source == "user"

    def test_data_stored(self):
        ev = TriggerEvent(type=TriggerType.THRESHOLD, source="monitor", data={"cpu": 95})
        assert ev.data["cpu"] == 95

    def test_timestamp_auto_populated(self):
        ev = TriggerEvent(type=TriggerType.FILE, source="watcher", data={})
        assert len(ev.timestamp) > 0


class TestTriggerResult:
    def _make(self):
        return TriggerResult(
            trigger_id="t-001",
            success=True,
            actions_run=2,
            actions_succeeded=2,
            actions_failed=0,
        )

    def test_trigger_id_stored(self):
        assert self._make().trigger_id == "t-001"

    def test_success_stored(self):
        assert self._make().success is True

    def test_actions_run_stored(self):
        assert self._make().actions_run == 2

    def test_actions_failed_zero(self):
        assert self._make().actions_failed == 0

    def test_message_default_empty(self):
        assert self._make().message == ""

    def test_duration_ms_default_zero(self):
        assert self._make().duration_ms == 0

    def test_timestamp_auto_populated(self):
        assert len(self._make().timestamp) > 0
