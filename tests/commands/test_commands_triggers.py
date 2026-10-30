"""
Batch 120 — commands/triggers: enums, TriggerCondition.evaluate/to_dict/from_dict,
TriggerAction, Trigger.can_fire/_generate_id/to_dict/from_dict, TriggerEvent, TriggerResult.

Pure-unit tests: no network, no file I/O.

Plus (Gate 1e): the `notify` action's telegram channel through the REAL
notification pipeline — a recording fake channel on the router, quiet hours,
and the history row an action failure must produce.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from navig.commands.triggers import (
    ActionType,
    Trigger,
    TriggerAction,
    TriggerCondition,
    TriggerEvent,
    TriggerManager,
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

    # rate limit (max_fires_per_hour) — enforced over a rolling 60-min window
    def test_rate_limit_blocks_after_max_fires_in_window(self):
        now = datetime.now()
        recent = [(now - timedelta(minutes=m)).isoformat() for m in (1, 2, 3)]
        t = self._make(max_fires_per_hour=3, cooldown_seconds=0, fire_times=recent)
        assert t.can_fire() is False

    def test_rate_limit_allows_when_under_max(self):
        now = datetime.now()
        recent = [(now - timedelta(minutes=m)).isoformat() for m in (1, 2)]
        t = self._make(max_fires_per_hour=3, cooldown_seconds=0, fire_times=recent)
        assert t.can_fire() is True

    def test_rate_limit_ignores_fires_older_than_an_hour(self):
        now = datetime.now()
        old = [(now - timedelta(minutes=m)).isoformat() for m in (61, 90, 120)]
        t = self._make(max_fires_per_hour=3, cooldown_seconds=0, fire_times=old)
        assert t.can_fire() is True  # all aged out of the window

    def test_rate_limit_disabled_when_max_not_positive(self):
        now = datetime.now()
        many = [(now - timedelta(minutes=1)).isoformat() for _ in range(50)]
        t = self._make(max_fires_per_hour=0, cooldown_seconds=0, fire_times=many)
        assert t.can_fire() is True  # 0 == no limit

    def test_record_fire_appends_and_prunes_old(self):
        now = datetime.now()
        t = self._make(fire_times=[(now - timedelta(hours=2)).isoformat()])  # stale
        t.record_fire(now)
        assert t.fire_times == [now.isoformat()]  # stale pruned, this fire kept

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

    def test_fire_times_roundtrips(self):
        t = self._make(fire_times=[datetime.now().isoformat()])
        assert Trigger.from_dict(t.to_dict()).fire_times == t.fire_times

    def test_fire_times_defaults_empty_for_legacy_configs(self):
        # Configs written before the field existed have no "fire_times" key.
        legacy = self._make().to_dict()
        del legacy["fire_times"]
        assert Trigger.from_dict(legacy).fire_times == []


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


# ---------------------------------------------------------------------------
# notify action → the real notification pipeline (Gate 1e)
# ---------------------------------------------------------------------------
#
# `_send_notification("telegram", …)` used to post straight to the Bot API: no
# routing matrix, no quiet hours, no master toggle, no fan-out, no delivery
# verification. These tests pin the new contract against the REAL router with a
# recording `_send_one` (the house fake-channel pattern, see
# tests/notify/test_system_events_bridge.py), plus the history honesty an action
# failure must produce. The dispatch-seam unit tests live in
# test_triggers_telegram_notify.py.


class _Recorder:
    """The fake channel: every `_send_one` the router makes lands here."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[SimpleNamespace] = []

    async def _send_one(self, channel, type_key, title, body, priority, data, **_kw):
        self.calls.append(
            SimpleNamespace(
                channel=channel,
                type=type_key,
                title=title,
                body=body,
                priority=priority,
                data=dict(data),
            )
        )
        return (self.ok, "recorded" if self.ok else "channel down")


@pytest.fixture
def notify_env(tmp_path, monkeypatch):
    """Isolated notify store + one recording fake channel (house pattern).

    Isolation is NAVIG_DATA_DIR/NAVIG_CONFIG_DIR into tmp (never NAVIG_HOME) with
    `store._initialised` reset so the schema and matrix re-seed for this store —
    and the assert proves the DB really is under tmp_path: a fixture that trusts
    its own env var has written the real store before.
    """
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))

    from navig.notify import prefs, store

    monkeypatch.setattr(store, "_initialised", False)
    store.init_db()
    assert str(store._db_path()).startswith(str(tmp_path)), "notify store must be isolated"

    from navig.notify.router import NotificationRouter

    # Deck off → exactly one recorded send per dispatch (the telegram channel).
    prefs.set_cell("system_alert", "deck", False)
    rec = _Recorder()
    monkeypatch.setattr(NotificationRouter, "_send_one", rec._send_one)
    return SimpleNamespace(prefs=prefs, rec=rec)


def _bare_manager() -> TriggerManager:
    # `_send_notification` uses no instance state, so skip __init__ (which would
    # create ~/.navig/triggers/ on the real machine) — same pattern as
    # test_triggers_telegram_notify.
    return TriggerManager.__new__(TriggerManager)


def _threshold_trigger() -> Trigger:
    return Trigger(id="t-notify", name="Disk Alert", type=TriggerType.THRESHOLD)


def _threshold_event() -> TriggerEvent:
    return TriggerEvent(type=TriggerType.THRESHOLD, source="host_checks", data={"disk": 93})


def test_notify_telegram_dispatches_once_with_the_trigger_as_title(notify_env):
    ok, err, suppressed = _bare_manager()._send_notification(
        "telegram", _threshold_trigger(), _threshold_event(), {"message": "disk at 93%"}
    )

    assert (ok, err) == (True, "")
    assert suppressed is False, "a delivered alert is not suppressed"
    assert len(notify_env.rec.calls) == 1, "exactly one message on the one enabled channel"
    call = notify_env.rec.calls[0]
    assert call.channel == "telegram"
    assert call.type == "system_alert"
    assert call.title == "Disk Alert"  # title = the trigger name
    assert call.body == "disk at 93%"  # body = the message the old code sent
    assert call.priority == "normal"
    # The deck renders the source of the alert from data.
    assert call.data["trigger_id"] == "t-notify"
    assert call.data["trigger_type"] == "threshold"
    assert call.data["trigger_source"] == "host_checks"
    assert call.data["event_type"] == "threshold"


def test_notify_default_body_is_the_default_message(notify_env):
    _bare_manager()._send_notification("telegram", _threshold_trigger(), _threshold_event(), {})
    assert notify_env.rec.calls[0].body == "Trigger 'Disk Alert' fired"


def test_quiet_hours_mute_the_noisy_channel_and_keep_the_silent_deck(notify_env):
    # Mirror of tests/notify/test_notify.py::test_router_quiet_hours_mutes_non_deck:
    # both channels on, quiet hours around the current hour — a NORMAL-priority
    # alert must not ring; only the silent in-app deck channel fires.
    prefs = notify_env.prefs
    prefs.set_cell("system_alert", "deck", True)
    h = datetime.now().hour
    prefs.set_setting("quiet_hours_enabled", True)
    prefs.set_setting("quiet_hours_start", h)
    prefs.set_setting("quiet_hours_end", (h + 1) % 24)

    ok, err, suppressed = _bare_manager()._send_notification(
        "telegram", _threshold_trigger(), _threshold_event(), {"message": "hi"}
    )
    assert suppressed is False, "deck delivery means the alert was NOT withheld"

    channels = [c.channel for c in notify_env.rec.calls]
    assert "telegram" not in channels, "a normal-priority alert must not ring in quiet hours"
    assert channels == ["deck"], "only the silent in-app channel fires during quiet hours"
    assert (ok, err) == (True, "")  # delivered to deck → the action delivered


def test_action_priority_param_is_honoured_and_validated(notify_env):
    mgr = _bare_manager()
    trig, ev = _threshold_trigger(), _threshold_event()

    mgr._send_notification("telegram", trig, ev, {"message": "x", "priority": "critical"})
    assert notify_env.rec.calls[-1].priority == "critical"

    # The words differ on purpose: the notify router collapses a repeat of the same
    # rendered message within dedupe.COLLAPSE_WINDOW_S (300 s), so two identical bodies
    # would test the dedupe wall instead of the priority fallback.
    mgr._send_notification("telegram", trig, ev, {"message": "y", "priority": "banana"})
    assert notify_env.rec.calls[-1].priority == "normal", "an invalid priority falls back"


def test_console_and_log_stay_local_and_unknown_is_still_rejected(notify_env):
    mgr = _bare_manager()
    trig, ev = _threshold_trigger(), _threshold_event()

    assert mgr._send_notification("console", trig, ev, {"message": "hello"}) == (
        True, "", False
    )
    assert mgr._send_notification("log", trig, ev, {"message": "hello"}) == (
        True, "", False
    )
    ok, err, _supp = mgr._send_notification("carrier-pigeon", trig, ev, {})
    assert _supp is False, "an unknown channel is a failure, not a suppression"
    assert ok is False
    assert "Unknown notification channel" in err

    assert notify_env.rec.calls == [], "local channels must never reach the pipeline"


def test_notify_dispatches_from_inside_a_running_loop(notify_env):
    """The proactive engine fires `process_event` from INSIDE the daemon's event
    loop — the sync bridge must offload to a worker loop instead of raising
    "asyncio.run() cannot be called from a running event loop"."""
    import asyncio

    trig, ev = _threshold_trigger(), _threshold_event()

    async def _fire():
        return _bare_manager()._send_notification("telegram", trig, ev, {"message": "hi"})

    ok, err, suppressed = asyncio.run(_fire())

    assert (ok, err) == (True, "")
    assert suppressed is False
    assert [c.channel for c in notify_env.rec.calls] == ["telegram"]


def test_dispatch_failure_records_a_failed_action_in_history(tmp_path, notify_env):
    notify_env.rec.ok = False  # every channel the router tries is down

    mgr = TriggerManager(config_manager=SimpleNamespace(global_config_dir=str(tmp_path / "cfg")))
    trigger = Trigger(
        id="t-fail",
        name="Broken notify",
        type=TriggerType.MANUAL,
        actions=[TriggerAction(type=ActionType.NOTIFY, target="telegram")],
    )
    assert mgr.add_trigger(trigger) is True

    # No exception may escape process_event; the failure must reach the result…
    results = mgr.process_event(TriggerEvent(type=TriggerType.MANUAL, source="test", data={}))
    assert len(results) == 1
    res = results[0]
    assert res.success is False
    assert res.actions_failed == 1
    assert "notify:" in res.message and "channel down" in res.message

    # …and the history row must say the alert did NOT reach the operator.
    rows = [
        json.loads(line)
        for line in mgr.history_file.read_text(encoding="utf-8").splitlines()
    ]
    assert rows[-1]["trigger_id"] == "t-fail"
    assert rows[-1]["success"] is False
    assert rows[-1]["actions_failed"] == 1
    assert "channel down" in rows[-1]["message"]


def test_a_raising_dispatch_is_contained_and_recorded(tmp_path, monkeypatch, notify_env):
    import navig.notify as notify_mod

    async def _boom(*_a, **_k):
        raise RuntimeError("router is broken")

    monkeypatch.setattr(notify_mod, "dispatch", _boom)

    mgr = TriggerManager(config_manager=SimpleNamespace(global_config_dir=str(tmp_path / "cfg")))
    trigger = Trigger(
        id="t-boom",
        name="Raising notify",
        type=TriggerType.MANUAL,
        actions=[TriggerAction(type=ActionType.NOTIFY, target="telegram")],
    )
    assert mgr.add_trigger(trigger) is True

    results = mgr.process_event(TriggerEvent(type=TriggerType.MANUAL, source="test", data={}))

    assert len(results) == 1, "a raising dispatch must not escape process_event"
    assert results[0].success is False
    assert "router is broken" in results[0].message
    rows = [
        json.loads(line)
        for line in mgr.history_file.read_text(encoding="utf-8").splitlines()
    ]
    assert rows[-1]["success"] is False


def test_ignored_target_params_say_so_on_every_fire(notify_env, navig_log_capture):
    """The old direct-send destination is dropped — silently would be a lie.

    Hand-written triggers with `target`/`chat_id` used to aim the bot at any chat;
    the pipeline resolves the chat itself now. The delivery must still succeed, and
    the log must say where to move the destination (`navig notify route set ...`).
    """
    ok, err, suppressed = _bare_manager()._send_notification(
        "telegram",
        _threshold_trigger(),
        _threshold_event(),
        {"message": "disk at 93%", "target": "chat-12345", "chat_id": "chat-999"},
    )
    assert suppressed is False

    assert (ok, err) == (True, ""), "the ignored params must not change delivery"
    assert len(notify_env.rec.calls) == 1
    hits = [m for m in navig_log_capture if "no longer choose the Telegram chat" in m]
    assert hits, f"expected the ignored-params warning, got: {navig_log_capture[-5:]}"
    assert "chat_id" in hits[0] and "target" in hits[0]
    assert "navig notify route" in hits[0], "the warning must name the supported replacement"


def test_no_target_params_no_warning(notify_env, navig_log_capture):
    ok, _err, _supp = _bare_manager()._send_notification(
        "telegram", _threshold_trigger(), _threshold_event(), {"message": "hi"}
    )
    assert ok
    assert not [m for m in navig_log_capture if "no longer choose the Telegram chat" in m]



# ---------------------------------------------------------------------------
# Suppression vs failure — quiet hours must not halt an on_failure:stop chain
# ---------------------------------------------------------------------------


def _suppressed_env(notify_env):
    """Silence EVERY channel so the pipeline withholds delivery by policy."""
    prefs = notify_env.prefs
    for channel in ("telegram", "deck"):
        prefs.set_cell("system_alert", channel, False)
    return notify_env


def test_full_suppression_is_flagged_suppressed_not_just_failed(notify_env):
    _suppressed_env(notify_env)
    ok, err, suppressed = _bare_manager()._send_notification(
        "telegram", _threshold_trigger(), _threshold_event(), {"message": "hi"}
    )
    assert (ok, suppressed) == (False, True), (
        "policy-withheld delivery must be distinguishable from a broken channel: "
        f"got ok={ok} err={err!r}"
    )
    assert "quiet hours" in err or "muted" in err or "master" in err


def test_genuine_channel_failure_is_not_suppressed(notify_env):
    notify_env.rec.ok = False  # channels exist but every send errors
    ok, err, suppressed = _bare_manager()._send_notification(
        "telegram", _threshold_trigger(), _threshold_event(), {"message": "hi"}
    )
    assert (ok, suppressed) == (False, False), (
        "an errored channel is a real failure — it must keep stopping on_failure:stop chains"
    )


def test_suppressed_notify_does_not_stop_the_chain(tmp_path, notify_env):
    """The rock: quiet hours halting `notify -> backup` turned policy into an outage."""
    _suppressed_env(notify_env)
    mgr = TriggerManager(config_manager=SimpleNamespace(global_config_dir=str(tmp_path / "cfg")))
    ran: list[str] = []

    def _mark_command(command, params):
        ran.append(command)
        return True, ""

    mgr._run_command = _mark_command
    trigger = Trigger(
        id="t-chain",
        name="Alert then backup",
        type=TriggerType.MANUAL,
        actions=[
            TriggerAction(type=ActionType.NOTIFY, target="telegram", on_failure="stop"),
            TriggerAction(type=ActionType.COMMAND, target="navig cron run nightly-backup"),
        ],
    )
    assert mgr.add_trigger(trigger) is True

    results = mgr.process_event(TriggerEvent(type=TriggerType.MANUAL, source="test", data={}))
    assert len(results) == 1
    res = results[0]
    assert ran == ["navig cron run nightly-backup"], (
        "a policy-suppressed alert must not halt an on_failure:stop chain"
    )
    # History stays honest: the alert reached nobody, so the run is not a success.
    assert res.actions_run == 2
    assert res.success is False
    assert "notify:" in res.message


def test_genuine_failure_still_stops_the_chain(tmp_path, notify_env):
    notify_env.rec.ok = False  # real channel failure, not policy
    mgr = TriggerManager(config_manager=SimpleNamespace(global_config_dir=str(tmp_path / "cfg")))
    ran: list[str] = []
    mgr._run_command = lambda command, params: (ran.append(command), (True, ""))[1]

    trigger = Trigger(
        id="t-chain2",
        name="Alert then backup",
        type=TriggerType.MANUAL,
        actions=[
            TriggerAction(type=ActionType.NOTIFY, target="telegram", on_failure="stop"),
            TriggerAction(type=ActionType.COMMAND, target="navig cron run nightly-backup"),
        ],
    )
    assert mgr.add_trigger(trigger) is True

    results = mgr.process_event(TriggerEvent(type=TriggerType.MANUAL, source="test", data={}))
    assert ran == [], "a genuine failure must still stop an on_failure:stop chain"
    assert results[0].actions_failed == 1
    assert results[0].success is False
