"""The system-event → notification bridge: events become alerts, once each.

Real-execution style, cloned from ``test_resource_monitor.py``: a real
``SystemEventQueue``, the real ``notify.dispatch`` router, and a recording
``_send_one`` standing in for the channel senders. Every assertion is about what
actually left the dispatcher, not about a stubbed-out ``dispatch``.

Isolation follows the house pattern — ``NAVIG_DATA_DIR`` + ``NAVIG_CONFIG_DIR``
into tmp (never ``NAVIG_HOME``), with ``store._initialised`` reset so the schema
and matrix are re-seeded for this store.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

from navig.gateway import system_events as se
from navig.notify import dedupe, prefs, store
from navig.notify.producers.system_events_bridge import (
    SUBSCRIBED_EVENT_TYPES,
    SYSTEM_EVENT_SPECS,
    dedupe_key,
    emit_or_notify,
    install_event_notify_bridge,
)
from navig.notify.router import NotificationRouter


class _Recorder:
    """The fake channel: every ``_send_one`` the router makes lands here."""

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

    @property
    def alerts(self) -> list[SimpleNamespace]:
        return [c for c in self.calls if c.type == "system_alert"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))

    monkeypatch.setattr(store, "_initialised", False)
    store.init_db()
    assert str(store._db_path()).startswith(str(tmp_path))

    # One recorded send per dispatch: the default system_alert row is deck+telegram.
    prefs.set_cell("system_alert", "telegram", False)

    recorder = _Recorder()
    monkeypatch.setattr(NotificationRouter, "_send_one", recorder._send_one)

    # SystemEventQueue.__init__ publishes itself as the process-global queue.
    # Snapshot it so this test's queue cannot leak into the next test.
    monkeypatch.setattr(se, "_GLOBAL_QUEUE", se._GLOBAL_QUEUE)

    queue = se.SystemEventQueue(tmp_path / "queue")
    handle = install_event_notify_bridge(queue)
    yield SimpleNamespace(
        queue=queue, handle=handle, bridge=handle.bridge, rec=recorder
    )
    handle()


async def _drain(queue) -> None:
    """Run every queued event through the queue's OWN subscriber path.

    This is what ``_process_loop`` calls per event; driving it directly keeps the
    tests deterministic (no 1 s queue timeout) while still exercising the real
    handler invocation, error containment and history bookkeeping.
    """
    while True:
        try:
            event = queue._queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        await queue._process_event(event)


def _alert_states() -> dict:
    return store.load_alert_states()


# ── the map: what we consume, and at what priority ───────────────────────────


def test_every_subscribed_type_exists_on_EventTypes():
    known = {
        value
        for key, value in vars(se.EventTypes).items()
        if not key.startswith("_") and isinstance(value, str)
    }
    unknown = set(SUBSCRIBED_EVENT_TYPES) - known
    assert not unknown, f"bridge subscribes to types EventTypes does not define: {unknown}"


def test_priority_map_is_the_gate_plan():
    high = {et for et, s in SYSTEM_EVENT_SPECS.items() if s.priority == "high"}
    assert high == {
        "host_down",
        "cert_expired",
        "service_down",
        "cron_job_failed",
        "site_down",  # site-probe monitor: an outage is an outage
    }

    normal = {et for et, s in SYSTEM_EVENT_SPECS.items() if s.priority == "normal"}
    assert normal == {
        "host_up",
        "host_disk_warning",
        "host_disk_ok",
        "host_memory_warning",
        "host_memory_ok",
        "host_cpu_warning",
        "host_cpu_ok",
        "cert_expiry_warning",
        "cert_ok",
        "service_up",
        "cron_job_complete",
        "site_up",
    }

    recoveries = {et for et, s in SYSTEM_EVENT_SPECS.items() if s.recovery}
    assert recoveries == {
        "host_up",
        "host_disk_ok",
        "host_memory_ok",
        "host_cpu_ok",
        "service_up",
        "cron_job_complete",
        "cert_ok",
        "site_up",
    }
    # A recovery can never be high: an all-clear must not ring like an alarm.
    assert all(SYSTEM_EVENT_SPECS[et].priority == "normal" for et in recoveries)


# ── alert_state store helpers ────────────────────────────────────────────────


def test_alert_state_helpers_upsert_and_forget(env):
    assert _alert_states() == {}

    store.save_alert_state("nas", "disk", "firing", last_value=93.0)
    store.save_alert_state("nas", "host", "firing")
    states = _alert_states()
    assert set(states) == {("nas", "disk"), ("nas", "host")}
    assert states[("nas", "disk")]["state"] == "firing"
    assert states[("nas", "disk")]["last_value"] == 93.0

    # Upsert: same (host, metric) replaces the row instead of adding one.
    store.save_alert_state("nas", "disk", "cleared", last_value=12.0)
    states = _alert_states()
    assert states[("nas", "disk")]["state"] == "cleared"
    assert states[("nas", "disk")]["last_value"] == 12.0
    assert len(states) == 2

    assert store.clear_alert_state("nas", "disk") is True
    assert store.clear_alert_state("nas", "disk") is False
    assert set(_alert_states()) == {("nas", "host")}


# ── 1. one alert per occurrence, then cooldown ───────────────────────────────


async def test_alert_dispatches_once_then_the_repeats_are_gated(env):
    payload = {"host": "nas", "usage": 93}

    await env.queue.emit("host_disk_warning", dict(payload))
    await _drain(env.queue)
    assert len(env.rec.alerts) == 1, "the first crossing must reach the channel"

    alert = env.rec.alerts[0]
    assert alert.priority == "normal"  # WARNING → normal
    assert alert.data["metric"] == "disk" and alert.data["host"] == "nas"
    assert alert.data["event_type"] == "host_disk_warning"
    assert "tidy" not in alert.data, "an alert is never chat-tidied"
    assert alert.title.startswith("Disk space low")
    assert "usage: 93" in alert.body

    # Same event again — the latch already says "firing".
    await env.queue.emit("host_disk_warning", dict(payload))
    await _drain(env.queue)
    assert len(env.rec.alerts) == 1
    assert env.bridge.stats()["suppressed"] == 1

    # Drop the latch: now only the dedupe COOLDOWN stands in the way.
    assert store.clear_alert_state("nas", "disk") is True
    await env.queue.emit("host_disk_warning", dict(payload))
    await _drain(env.queue)
    assert len(env.rec.alerts) == 1, "a claim inside its cooldown must stay quiet"
    assert env.bridge.stats()["suppressed"] == 2

    # Give the claim back (what a failed delivery does) and it re-arms.
    dedupe.release(dedupe_key("host_disk_warning", payload))
    await env.queue.emit("host_disk_warning", dict(payload))
    await _drain(env.queue)
    assert len(env.rec.alerts) == 2
    assert env.bridge.stats()["dispatched"] == 2
    assert _alert_states()[("nas", "disk")]["state"] == "firing"
    # The two withheld alerts were not thrown away: they ride along with this one,
    # so a quiet dropper is never indistinguishable from a quiet system.
    assert "(+2 further alert(s) suppressed since the last one)" in env.rec.alerts[1].body
    assert env.bridge.stats()["pending_alerts"] == 0


# ── 2. the all-clear ─────────────────────────────────────────────────────────


async def test_recovery_announces_the_all_clear_once_with_tidy_status(env):
    await env.queue.emit("host_down", {"host": "nas", "reason": "ping timeout"})
    await _drain(env.queue)
    assert len(env.rec.alerts) == 1
    assert env.rec.alerts[0].priority == "high"  # DOWN → high
    assert "reason: ping timeout" in env.rec.alerts[0].body

    await env.queue.emit("host_up", {"host": "nas", "downtime_s": 42})
    await _drain(env.queue)
    assert len(env.rec.alerts) == 2
    recovery = env.rec.alerts[1]
    assert recovery.data["tidy"] == "status"
    assert recovery.priority == "normal"
    assert recovery.title.startswith("Host up")
    assert _alert_states()[("nas", "host")]["state"] == "cleared"

    # A second all-clear (a flapping check) does not repeat it.
    await env.queue.emit("host_up", {"host": "nas", "downtime_s": 43})
    await _drain(env.queue)
    assert len(env.rec.alerts) == 2
    assert env.bridge.stats()["suppressed"] == 1


async def test_a_recovery_is_never_announced_without_an_outstanding_alert(env):
    # A routine successful cron run must NOT say "recovered" every time.
    await env.queue.emit("cron_job_complete", {"job_id": "j1", "job_name": "backup"})
    await _drain(env.queue)
    assert env.rec.alerts == []
    assert env.bridge.stats()["suppressed"] == 1
    # …but it is not a WITHHELD ALERT, so it must not ride along on the next one.
    assert env.bridge.stats()["pending_alerts"] == 0
    assert _alert_states() == {}  # nothing was announced, so nothing is latched


# ── 3. delivery-verified latch rollback ──────────────────────────────────────


async def test_failed_delivery_releases_the_claim_and_the_next_event_retries(env):
    env.rec.ok = False
    payload = {"host": "nas", "usage": 95}

    await env.queue.emit("host_disk_warning", dict(payload))
    await _drain(env.queue)
    assert len(env.rec.alerts) == 1
    # Nothing was delivered → no latch written (gate 1 must not block the retry)…
    assert ("nas", "disk") not in _alert_states()
    assert env.bridge.stats()["dispatched"] == 0

    # …and the claim was given back: this SECOND attempt is inside the 900 s
    # cooldown, so it can only happen because release() ran.
    await env.queue.emit("host_disk_warning", dict(payload))
    await _drain(env.queue)
    assert len(env.rec.alerts) == 2
    assert env.bridge.stats()["suppressed"] == 0

    # Restore delivery and the retry lands, writing the latch.
    env.rec.ok = True
    await env.queue.emit("host_disk_warning", dict(payload))
    await _drain(env.queue)
    assert len(env.rec.alerts) == 3
    assert _alert_states()[("nas", "disk")]["state"] == "firing"


async def test_an_intentional_mute_still_latches(env):
    """``channels == []`` (master off / muted everywhere) is not a delivery
    failure — it must not churn, same rule as the resource monitor."""
    prefs.set_setting("master_enabled", False)

    await env.queue.emit("host_disk_warning", {"host": "nas", "usage": 99})
    await _drain(env.queue)
    assert env.rec.alerts == []  # muted: nothing reached the fake channel
    assert env.bridge.stats()["dispatched"] == 1  # …but the router accepted it
    assert _alert_states()[("nas", "disk")]["state"] == "firing"


# ── 4. restart persistence ───────────────────────────────────────────────────


async def test_a_restart_does_not_re_fire_a_still_firing_alert(env):
    env.bridge.cooldown_s = 0.0  # neutralise the cooldown: only the latch may gate
    # What a previous run of the daemon left behind: we already said "down".
    store.save_alert_state("nas", "host", "firing", since=time.time() - 3600)

    await env.queue.emit("host_down", {"host": "nas", "reason": "unreachable"})
    await _drain(env.queue)
    assert env.rec.alerts == [], "an unresolved alert already announced must not repeat"

    # The all-clear still goes out, and clears the latch.
    await env.queue.emit("host_up", {"host": "nas"})
    await _drain(env.queue)
    assert len(env.rec.alerts) == 1
    assert env.rec.alerts[0].data["tidy"] == "status"
    assert _alert_states()[("nas", "host")]["state"] == "cleared"

    # With the latch cleared, a fresh failure is news again.
    await env.queue.emit("host_down", {"host": "nas", "reason": "unreachable"})
    await _drain(env.queue)
    assert len(env.rec.alerts) == 2
    assert env.rec.alerts[1].priority == "high"
    assert _alert_states()[("nas", "host")]["state"] == "firing"


# ── 5. detached callers ──────────────────────────────────────────────────────


async def test_detached_emit_or_notify_dispatches_without_a_queue(env, monkeypatch):
    monkeypatch.setattr(se, "_GLOBAL_QUEUE", None)

    outcome = await emit_or_notify("service_down", {"service": "nginx", "host": "nas"})

    assert isinstance(outcome, dict), "with no queue it returns the dispatch outcome"
    assert outcome["type"] == "system_alert"
    assert len(env.rec.alerts) == 1
    alert = env.rec.alerts[0]
    assert alert.priority == "high"
    assert alert.title == "Service down: nginx"
    assert alert.data["event_type"] == "service_down"
    assert alert.data["host"] == "nas"


async def test_detached_recovery_carries_tidy_and_an_unknown_type_still_notifies(
    env, monkeypatch
):
    monkeypatch.setattr(se, "_GLOBAL_QUEUE", None)

    await emit_or_notify("service_up", {"service": "nginx"})
    assert env.rec.alerts[-1].data["tidy"] == "status"

    await emit_or_notify("something_new_happened", {"detail": "x"})
    unknown = env.rec.alerts[-1]
    assert unknown.title == "Something new happened"
    assert unknown.data["event_type"] == "something_new_happened"


async def test_emit_or_notify_prefers_a_real_queue_when_one_exists(env):
    outcome = await emit_or_notify("host_down", {"host": "nas"})
    assert isinstance(outcome, str), "with a queue it returns the event id"
    assert env.rec.alerts == []  # nothing yet — it is queued, not dispatched

    await _drain(env.queue)
    assert len(env.rec.alerts) == 1


# ── 6. exception safety ──────────────────────────────────────────────────────


async def test_a_raising_dispatch_neither_escapes_nor_kills_the_queue(env, monkeypatch):
    env.bridge.cooldown_s = 0.0
    import navig.notify as notify_mod

    real = notify_mod.dispatch
    state = {"boom": True}

    async def _swap(*args, **kwargs):
        if state["boom"]:
            raise RuntimeError("dispatch path is broken")
        return await real(*args, **kwargs)

    monkeypatch.setattr(notify_mod, "dispatch", _swap)

    # Directly at the subscriber: the bridge's own guard must contain it.
    event = se.SystemEvent(
        id="evt_test_1",
        event_type="host_down",
        payload={"host": "nas"},
        priority=se.EventPriority.HIGH,
        timestamp=datetime.now(),
    )
    await env.bridge.handle(event)
    assert env.bridge.stats()["errors"] == 1

    # Through the queue: the event completes and the queue keeps running.
    await env.queue.emit("host_down", {"host": "nas"})
    await _drain(env.queue)
    assert env.bridge.stats()["errors"] == 2
    assert env.queue.pending_count == 0, "a bridge raise must not wedge the queue"

    # Once dispatch works again the very next event still gets through.
    state["boom"] = False
    await env.queue.emit("host_down", {"host": "nas"})
    await _drain(env.queue)
    assert len(env.rec.alerts) == 1
    assert _alert_states()[("nas", "host")]["state"] == "firing"


# ── install / uninstall ──────────────────────────────────────────────────────


async def test_install_is_idempotent(env):
    again = install_event_notify_bridge(env.queue)
    assert again is env.handle, "a second install must not stack a duplicate subscriber"

    await env.queue.emit("host_disk_warning", {"host": "nas", "usage": 91})
    await _drain(env.queue)
    assert len(env.rec.alerts) == 1, "one subscriber → exactly one dispatch"


async def test_unsubscribe_stops_delivery(env):
    env.handle()  # unsubscribe
    assert env.handle.installed is False
    env.handle()  # idempotent

    await env.queue.emit("host_disk_warning", {"host": "nas", "usage": 91})
    await _drain(env.queue)
    assert env.rec.alerts == []
    assert env.bridge.stats()["dispatched"] == 0


# ---------------------------------------------------------------------------
# alert_state retention — a retired host's latch must not live forever
# ---------------------------------------------------------------------------


def test_prune_alert_state_rows_drops_only_stale_latches(env):
    stale = time.time() - store.ALERT_STATE_RETENTION_S - 60
    store.save_alert_state("dead-nas", "disk", "firing", since=stale)
    store.save_alert_state("dead-nas", "mem", "cleared", since=stale)
    store.save_alert_state("live-nas", "disk", "firing")  # fresh

    removed = store.prune_alert_state_rows()

    assert removed == 2, "both stale latches go"
    left = store.load_alert_states()
    assert set(left) == {("live-nas", "disk")}, "a fresh latch is never pruned"


def test_save_triggers_an_amortized_prune(env, monkeypatch):
    monkeypatch.setattr(store, "_ALERT_STATE_PRUNE_EVERY", 2)
    monkeypatch.setattr(store, "_alert_saves_since_prune", 1)  # one save from firing
    stale = time.time() - store.ALERT_STATE_RETENTION_S - 60

    store.save_alert_state("ghost", "disk", "firing", since=stale)

    assert store.load_alert_states() == {}, (
        "the second save must run the amortized retention and drop the stale row"
    )


def test_a_failing_prune_never_breaks_the_save(env, monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("retention exploded")

    monkeypatch.setattr(store, "prune_alert_state_rows", _boom)
    monkeypatch.setattr(store, "_alert_saves_since_prune", store._ALERT_STATE_PRUNE_EVERY - 1)

    store.save_alert_state("nas", "disk", "firing")

    assert ("nas", "disk") in store.load_alert_states(), (
        "retention is best-effort: the latch write must survive a prune failure"
    )
