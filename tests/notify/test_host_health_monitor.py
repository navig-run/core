"""Gate 1d — the host health-check monitor: sampled edges become system events,
each with a paired clear, through a REAL queue + the 1c event→notify bridge.

Cloned isolation and recording style from ``test_system_events_bridge.py``:
``NAVIG_DATA_DIR`` + ``NAVIG_CONFIG_DIR`` into tmp (never ``NAVIG_HOME``), a real
``SystemEventQueue`` with ``install_event_notify_bridge``, and a recording
``_send_one`` standing in for the channels — every assertion is about what
actually left the dispatcher.

What lives only here:

* the warn → recovery pair (``host_disk_warning`` → ``host_disk_ok``) end to end;
* restart persistence from ``alert_state`` (a firing latch never re-announces);
* maintenance windows: emit suppressed, edge remembered, delivered post-window;
* hard-failure → ``host_down`` once, then ``host_up`` on recovery;
* CPU evidence: the monitor's own ``cpu_times()`` window (the shared psutil
  sampler on the daemon's pool is NOT ours) and its per-poll baseline;
* designed CI load: a held heavy lane parks local CPU edges until the queue
  drains — announce-if-still-hot, silence-if-resolved, remote never consulted;
* the trigger feed (best-effort THRESHOLD events) and the gateway bridge install;
* the EventTypes ↔ SYSTEM_EVENT_SPECS drift guard for the alert families.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from navig.gateway import system_events as se
from navig.monitoring.checks import CheckFailure, Sample
from navig.notify import prefs, store
from navig.notify.monitors import host_health as m
from navig.notify.producers.system_events_bridge import (
    SYSTEM_EVENT_SPECS,
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


class _TriggerRecorder:
    """Stands in for TriggerManager: records the edges handed to automation."""

    def __init__(self) -> None:
        self.events: list = []

    def process_event(self, event):
        self.events.append(event)
        return []


class _Collect:
    """Controllable stand-in for ``collect_with_errors``."""

    def __init__(self) -> None:
        self.samples: list[Sample] = []
        self.failures: list[CheckFailure] = []

    async def __call__(self, host, checks, **_kw):
        return list(self.samples), list(self.failures)


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
    yield SimpleNamespace(queue=queue, handle=handle, bridge=handle.bridge, rec=recorder)
    handle()


async def _drain(queue) -> None:
    """Run every queued event through the queue's OWN subscriber path."""
    while True:
        try:
            event = queue._queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        await queue._process_event(event)


CFG = {
    "hosts": ["nas"],
    "thresholds": {"disk_pct": {"warn": 85, "crit": 95}},
    "services": ["nginx"],
}


def _state(env, cfg=None) -> tuple[m.MonitorState, _TriggerRecorder]:
    state = m.build_state(cfg or CFG, event_queue=env.queue)
    triggers = _TriggerRecorder()
    state.trigger_manager = triggers  # never let a real TriggerManager into a test
    state.window_cpu = False  # …and never let THIS machine's real CPU into a fake collect
    return state, triggers


def _samples(label="nas", *, disk=None, mem=10.0, cpu=0.0, services=None):
    ts = time.time()
    out: list[Sample] = []
    if disk is not None:
        out.append(Sample(label, "disk_pct", disk, "%", ts, "local"))
    if mem is not None:
        out.append(Sample(label, "mem_pct", mem, "%", ts, "local"))
    if cpu is not None:
        out.append(Sample(label, "cpu_pct", cpu, "%", ts, "local"))
    for name, status in (services or {}).items():
        out.append(Sample(label, f"service.{name}.status", status, "state", ts, "local"))
    return out


# ── 1. the threshold edge: warn once, clear once ──────────────────────────────


async def test_disk_edge_emits_warning_once_then_a_paired_all_clear(env, monkeypatch):
    collect = _Collect()
    monkeypatch.setattr(m, "collect_with_errors", collect)
    state, triggers = _state(env)

    # Crossing: one host_disk_warning → one system_alert on the recording channel.
    collect.samples = _samples(disk=95)
    await m.poll_once(state)
    await _drain(env.queue)
    assert len(env.rec.alerts) == 1, "the crossing must reach the channel"
    alert = env.rec.alerts[0]
    assert alert.title.startswith("Disk space low")
    assert alert.data["event_type"] == "host_disk_warning"
    assert alert.data["metric"] == "disk" and alert.data["host"] == "nas"
    assert alert.data["value"] == 95 and alert.data["level"] == "crit"
    assert "tidy" not in alert.data, "an alert is never chat-tidied"

    # Stays high: no second edge (tracker latch) and no second dispatch (bridge latch).
    await m.poll_once(state)
    await _drain(env.queue)
    assert len(env.rec.alerts) == 1

    # Recovery below the low band: the paired host_disk_ok clears the latch.
    collect.samples = _samples(disk=50)
    await m.poll_once(state)
    await _drain(env.queue)
    assert len(env.rec.alerts) == 2
    recovery = env.rec.alerts[1]
    assert recovery.title.startswith("Disk space OK")
    assert recovery.data["event_type"] == "host_disk_ok"
    assert recovery.data["tidy"] == "status"
    assert store.load_alert_states()[("nas", "disk")]["state"] == "cleared"

    # Both edges were also handed to the trigger feed (THRESHOLD events).
    assert [e.data["state"] for e in triggers.events] == ["alert", "clear"]
    assert all(e.type.value == "threshold" for e in triggers.events)


# ── 2. restart persistence ────────────────────────────────────────────────────


async def test_a_restart_does_not_re_fire_a_still_firing_disk_alert(env, monkeypatch):
    # What a previous run of the daemon left behind: we already said "disk low".
    store.save_alert_state("nas", "disk", "firing", since=time.time() - 3600)

    collect = _Collect()
    monkeypatch.setattr(m, "collect_with_errors", collect)
    state, triggers = _state(env)

    collect.samples = _samples(disk=95)
    await m.poll_once(state)
    await _drain(env.queue)
    assert env.rec.alerts == [], "an unresolved alert already announced must not repeat"
    assert triggers.events == [], "no edge fired, so no trigger edge either"

    # The all-clear still goes out once the metric recovers.
    collect.samples = _samples(disk=50)
    await m.poll_once(state)
    await _drain(env.queue)
    assert len(env.rec.alerts) == 1
    assert env.rec.alerts[0].data["event_type"] == "host_disk_ok"
    assert store.load_alert_states()[("nas", "disk")]["state"] == "cleared"


# ── 3. maintenance windows ────────────────────────────────────────────────────


async def test_maintenance_suppresses_the_emit_then_delivers_after_the_window(
    env, monkeypatch
):
    cfg = dict(CFG, maintenance=[{"start": "22:00", "end": "23:00"}])
    collect = _Collect()
    monkeypatch.setattr(m, "collect_with_errors", collect)
    state, _triggers = _state(env, cfg)

    # Inside the window: sampling and tracker updates continue, but nothing emits.
    monkeypatch.setattr(m, "_now", lambda: datetime(2026, 10, 14, 22, 30))
    collect.samples = _samples(disk=95)
    await m.poll_once(state)
    await _drain(env.queue)
    assert env.rec.alerts == [], "no dispatch inside a maintenance window"
    assert state.trackers[("nas", "disk_pct")].alerted is True, "the tracker still latches"
    assert state.pending.get(("nas", "disk_pct")) == "alert", "the edge is remembered"
    # NOT written to alert_state: a row here would make the bridge swallow the
    # post-window emit this flag exists to deliver.
    assert ("nas", "disk") not in store.load_alert_states()

    # Window over, condition still bad: the first post-window poll emits it.
    monkeypatch.setattr(m, "_now", lambda: datetime(2026, 10, 14, 12, 0))
    await m.poll_once(state)
    await _drain(env.queue)
    assert len(env.rec.alerts) == 1
    assert env.rec.alerts[0].data["event_type"] == "host_disk_warning"
    assert state.pending == {}

    # …and the paired clear still works after the window-delivered alert.
    collect.samples = _samples(disk=50)
    await m.poll_once(state)
    await _drain(env.queue)
    assert len(env.rec.alerts) == 2
    assert env.rec.alerts[1].data["event_type"] == "host_disk_ok"


def test_maintenance_parsing_overnight_days_and_hosts():
    windows = m.parse_maintenance(
        [
            {"start": "23:00", "end": "07:00"},  # spans midnight
            {"start": "02:00", "end": "04:00", "days": [6], "hosts": ["nas"]},
            {"start": "nonsense", "end": "04:00"},  # dropped loudly
            "not-a-mapping",  # dropped loudly
        ]
    )
    assert len(windows) == 2

    sat = datetime(2026, 10, 17, 23, 30)  # Saturday, weekday() == 5
    assert m.in_maintenance(sat, windows[:1], "any") is True
    assert m.in_maintenance(datetime(2026, 10, 18, 6, 0), windows[:1], "any") is True
    assert m.in_maintenance(datetime(2026, 10, 18, 12, 0), windows[:1], "any") is False

    sunday_3am = datetime(2026, 10, 18, 3, 0)  # weekday() == 6
    assert m.in_maintenance(sunday_3am, windows[1:], "nas") is True
    assert m.in_maintenance(sunday_3am, windows[1:], "other") is False, "hosts filter"
    assert m.in_maintenance(datetime(2026, 10, 17, 3, 0), windows[1:], "nas") is False, (
        "days filter: weekday 5 is not in [6]"
    )


# ── 4. hard failure: unreachable once, recover once, never crash ───────────────


async def test_hard_failure_emits_host_down_once_then_host_up(env, monkeypatch):
    collect = _Collect()
    monkeypatch.setattr(m, "collect_with_errors", collect)
    state, _triggers = _state(env)

    collect.failures = [CheckFailure("nas", "resources", "remote command exited 1")]
    await m.poll_once(state)  # must not raise on failures-only
    await _drain(env.queue)
    assert len(env.rec.alerts) == 1
    assert env.rec.alerts[0].title.startswith("Host down")
    assert env.rec.alerts[0].priority == "high"
    assert "reason: remote command exited 1" in env.rec.alerts[0].body

    # Still unreachable: the latch holds (no second host_down).
    await m.poll_once(state)
    await _drain(env.queue)
    assert len(env.rec.alerts) == 1

    # Back: host_up with tidy status, latch cleared.
    collect.failures = []
    collect.samples = _samples(disk=50)
    await m.poll_once(state)
    await _drain(env.queue)
    assert len(env.rec.alerts) == 2
    assert env.rec.alerts[1].data["event_type"] == "host_up"
    assert env.rec.alerts[1].data["tidy"] == "status"


async def test_a_psutil_gap_is_not_a_host_down(env, monkeypatch):
    """A local box without psutil must idle, not cry host_down every poll."""
    collect = _Collect()
    monkeypatch.setattr(m, "collect_with_errors", collect)
    state, _triggers = _state(env)

    collect.failures = [
        CheckFailure("nas", "resources", "psutil is not installed"),
        CheckFailure("nas", "services", "no local service data (psutil/systemctl unavailable?)"),
    ]
    await m.poll_once(state)
    await _drain(env.queue)
    assert env.rec.alerts == []


# ── services: down once, up once ──────────────────────────────────────────────


async def test_service_down_then_service_up_pair(env, monkeypatch):
    collect = _Collect()
    monkeypatch.setattr(m, "collect_with_errors", collect)
    state, _triggers = _state(env)

    # Healthy baseline: an active service announces nothing.
    collect.samples = _samples(disk=50, services={"nginx": "active"})
    await m.poll_once(state)
    await _drain(env.queue)
    assert env.rec.alerts == []

    # Watched unit stops: one service_down (high priority), latched.
    collect.samples = _samples(disk=50, services={"nginx": "inactive"})
    await m.poll_once(state)
    await _drain(env.queue)
    assert len(env.rec.alerts) == 1
    assert env.rec.alerts[0].title == "Service down: nginx"
    assert env.rec.alerts[0].priority == "high"

    await m.poll_once(state)
    await _drain(env.queue)
    assert len(env.rec.alerts) == 1, "a still-down service must not re-alert"

    # Back up: the paired service_up clears the latch.
    collect.samples = _samples(disk=50, services={"nginx": "active"})
    await m.poll_once(state)
    await _drain(env.queue)
    assert len(env.rec.alerts) == 2
    assert env.rec.alerts[1].data["event_type"] == "service_up"
    assert env.rec.alerts[1].data["tidy"] == "status"
    assert store.load_alert_states()[("nas", "service")]["state"] == "cleared"


# ── 5. the three surfaces agree about this monitor ────────────────────────────


def test_host_checks_is_known_and_opt_in_on_every_surface():
    from navig.gateway.deck.routes.notify import (
        _DEFAULT_ON,
        _MONITOR_KEYS,
        _monitor_availability,
    )
    from navig.gateway.server import NavigGateway
    from navig.notify.producers.samples import _SAMPLES, monitor_sample

    assert "host_checks" in NavigGateway.MONITOR_KEYS
    assert "host_checks" not in NavigGateway.MONITORS_DEFAULT_ON, "default OFF (opt-in)"
    assert "host_checks" in _MONITOR_KEYS
    assert "host_checks" not in _DEFAULT_ON, "deck default must match the gateway"
    assert "host_checks" in _SAMPLES, "the deck Test button needs a sample"
    assert monitor_sample("host_checks")[0] == "system_alert"
    # Deck availability mirrors resources: local sampling needs psutil.
    assert _monitor_availability("host_checks", mode="", is_win=True, has_psutil=False) == (
        False,
        "Needs psutil",
    )
    assert _monitor_availability("host_checks", mode="", is_win=True, has_psutil=True) == (
        True,
        None,
    )
    # The monitor-defaults contract must hold with the new key present.
    assert set(NavigGateway.MONITORS_DEFAULT_ON) == set(_DEFAULT_ON)
    assert set(_DEFAULT_ON) <= set(NavigGateway.MONITOR_KEYS)


# ── 6. the gateway boots the event→notify bridge ──────────────────────────────


async def test_gateway_monitor_init_installs_the_bridge_on_its_queue(env, monkeypatch):
    """_init_notify_monitors must subscribe the 1c bridge: nothing else does."""
    from navig.gateway.server import NavigGateway

    bare_queue = se.SystemEventQueue(env.queue.storage_path.parent / "queue2")

    class _FakeGateway:
        MONITOR_KEYS = ("host_checks",)
        MONITORS_DEFAULT_ON = frozenset()

        def __init__(self, queue):
            self.event_queue = queue
            self.config_manager = SimpleNamespace(global_config={})
            self._monitor_tasks = {}

    gateway = _FakeGateway(bare_queue)
    NavigGateway._init_notify_monitors(gateway)

    handle = getattr(gateway, "_event_bridge_handle", None)
    assert handle is not None and handle.installed, "boot must install the bridge"

    # A subsequent system event on that queue actually reaches notify.
    await bare_queue.emit("host_disk_warning", {"host": "nas", "value": 91})
    await _drain(bare_queue)
    assert len(env.rec.alerts) == 1
    assert env.rec.alerts[0].data["event_type"] == "host_disk_warning"

    # Idempotent: a second init returns the same live handle (no double subscriber).
    NavigGateway._init_notify_monitors(gateway)
    assert gateway._event_bridge_handle is handle
    handle()


# ── 7. EventTypes ↔ SYSTEM_EVENT_SPECS drift ──────────────────────────────────


def test_every_emittable_alert_family_event_type_has_a_bridge_spec():
    """The host/cert/service/cron/site event families are exactly what the monitor
    and its peers can raise — every one of them needs a SYSTEM_EVENT_SPECS entry
    or the bridge silently drops it (subscription and map are keyed off the specs).

    ``host_check``, ``service_restarted`` and ``cron_job_start`` are
    defined-but-never-emitted placeholders (no emitter anywhere in core — the
    wire values appear only in their own definition and docstrings), so they
    have no notify mapping by design; everything else must be mapped.
    """
    from navig.gateway.system_events import EventTypes

    excluded = {"host_check", "service_restarted", "cron_job_start"}
    members = {
        value
        for key, value in vars(EventTypes).items()
        if not key.startswith("_")
        and isinstance(value, str)
        and value.startswith(("host_", "cert_", "service_", "cron_", "site_"))
    } - excluded
    missing = members - set(SYSTEM_EVENT_SPECS)
    assert not missing, f"EventTypes without a SYSTEM_EVENT_SPECS entry: {sorted(missing)}"

    # The two recoveries this branch exists to add are wired, in the right family.
    assert SYSTEM_EVENT_SPECS[EventTypes.HOST_DISK_OK].family == "disk"
    assert SYSTEM_EVENT_SPECS[EventTypes.HOST_DISK_OK].recovery is True
    assert SYSTEM_EVENT_SPECS[EventTypes.HOST_MEMORY_OK].family == "memory"
    assert SYSTEM_EVENT_SPECS[EventTypes.HOST_MEMORY_OK].recovery is True

    # The site-probe family (site_down → site_up, cert ladder → cert_ok) is wired.
    assert SYSTEM_EVENT_SPECS[EventTypes.SITE_DOWN].family == "site"
    assert SYSTEM_EVENT_SPECS[EventTypes.SITE_DOWN].priority == "high"
    assert SYSTEM_EVENT_SPECS[EventTypes.SITE_UP].recovery is True
    assert SYSTEM_EVENT_SPECS[EventTypes.CERT_OK].recovery is True
    assert SYSTEM_EVENT_SPECS[EventTypes.CERT_OK].family == "cert"


# ── the loop survives a bad poll ──────────────────────────────────────────────


async def test_one_bad_poll_never_kills_the_loop(env, monkeypatch):
    monkeypatch.setattr(m, "_MIN_POLL_S", 0.001)
    polls: list[int] = []

    async def _boom(state):
        polls.append(1)
        if len(polls) <= 2:
            raise RuntimeError("collect path broke")

    monkeypatch.setattr(m, "poll_once", _boom)
    task = asyncio.create_task(
        m.run_host_check_monitor({"hosts": ["nas"], "poll_seconds": 0.001})
    )
    for _ in range(400):
        if len(polls) >= 4:
            break
        await asyncio.sleep(0.005)
    survived = len(polls)
    task.cancel()
    try:
        await task
    except BaseException:  # noqa: BLE001 — swallow cancel; survival already measured
        pass
    assert survived >= 4, f"monitor died after {survived} poll(s) — a raise killed the loop"


# ── 6. CPU evidence: the monitor owns its window; designed load is not an anomaly ──


def _bare_state() -> m.MonitorState:
    """A state for pure-function tests — no store, no queue, no real collect."""
    return m.MonitorState(targets=[], thresholds={}, crit={}, trackers={}, pending={})


def test_busy_pct_mirrors_psutil_math() -> None:
    ct = namedtuple("ct", "user system idle")
    # +30 user, +30 system, +140 idle → 60 busy of 200 total
    assert m._busy_pct(ct(100, 100, 800), ct(130, 130, 940)) == 30.0
    # Windows shape: interrupt/dpc are busy time, only idle is not
    win = namedtuple("ct", "user system idle interrupt dpc")
    assert m._busy_pct(win(50, 50, 400, 0, 0), win(60, 60, 460, 5, 5)) == 33.3
    # Linux shape: guest counts inside user, so it leaves the total (psutil's rule)
    lin = namedtuple("ct", "user system idle guest guest_nice")
    assert m._busy_pct(lin(100, 100, 700, 50, 0), lin(150, 150, 1150, 60, 0)) == 18.2
    # No elapsed CPU time → no window → None, never a fabricated 0 %
    assert m._busy_pct(ct(1, 1, 1), ct(1, 1, 1)) is None


def test_local_cpu_samples_take_the_monitors_own_window(monkeypatch) -> None:
    psutil = pytest.importorskip("psutil")
    state = _bare_state()
    state.cpu_prev = psutil.cpu_times()._replace(user=1000.0, system=1000.0, idle=8000.0)
    monkeypatch.setattr(
        m,
        "_cpu_times_sample",
        lambda: psutil.cpu_times()._replace(user=1030.0, system=1030.0, idle=8140.0),
    )
    got = m._apply_own_cpu_window(state, _samples("h", cpu=11.1, disk=50))
    cpu = [s for s in got if s.metric == "cpu_pct"][0]
    assert cpu.value == 30.0, "the shared sampler's 11.1 must yield to OUR window"
    assert next(s for s in got if s.metric == "disk_pct").value == 50, "others untouched"


def test_windowing_is_inert_before_the_first_baseline_and_behind_the_seam() -> None:
    samples = _samples("h", cpu=42.5)
    state = _bare_state()
    assert state.cpu_prev is None
    assert m._apply_own_cpu_window(state, samples) is samples  # first poll: shared reading passes through
    state.cpu_prev = "a-baseline"
    state.window_cpu = False
    assert m._apply_own_cpu_window(state, samples) is samples  # the fake-collect seam


async def test_poll_once_closes_the_window_and_remote_targets_never_read_lanes(
    env, monkeypatch
) -> None:
    snap = object()
    monkeypatch.setattr(m, "_cpu_times_sample", lambda: snap)

    def _no_lanes() -> bool:
        raise AssertionError("a remote target must not consult this machine's lanes")

    monkeypatch.setattr(m, "_heavy_lanes_busy", _no_lanes)
    collect = _Collect()
    monkeypatch.setattr(m, "collect_with_errors", collect)
    state, _ = _state(env)
    state.window_cpu = True  # the fixture seam is off — this test IS about the window
    collect.samples = _samples("nas", disk=50)
    await m.poll_once(state)
    assert state.cpu_prev is snap, "one baseline per poll, closed after the sweep"


async def test_cpu_edges_wait_out_the_lane_queue(env, monkeypatch) -> None:
    """A held lane makes local CPU load *designed*: park the edge, settle on drain."""
    collect = _Collect()
    monkeypatch.setattr(m, "collect_with_errors", collect)
    state, _ = _state(env, {"hosts": []})  # local target: this machine
    label = state.targets[0][1]
    busy = True
    monkeypatch.setattr(m, "_heavy_lanes_busy", lambda: busy)

    # sustain=3 for CPU: three armed polls park the edge instead of paging
    collect.samples = _samples(label, cpu=95, disk=50)
    for _ in range(3):
        await m.poll_once(state)
        await _drain(env.queue)
    assert env.rec.alerts == [], "a lane holds the box — designed load must not page"
    assert state.pending.get((label, "cpu_pct")) == "alert", "the edge is remembered"
    assert not any(key[1] == "cpu" for key in store.load_alert_states()), (
        "no latch row before dispatch — it would swallow the post-CI emit"
    )

    # Still hot, still busy: no repeat, the parked edge stays put.
    await m.poll_once(state)
    await _drain(env.queue)
    assert env.rec.alerts == [] and state.pending.get((label, "cpu_pct")) == "alert"

    # Queue drains with the box STILL hot → the first free poll announces it.
    busy = False
    await m.poll_once(state)
    await _drain(env.queue)
    cpu_alerts = [a for a in env.rec.alerts if a.data.get("event_type") == "host_cpu_warning"]
    assert len(cpu_alerts) == 1, "still hot after CI is a real anomaly — announce it then"
    assert cpu_alerts[0].data["host"] == label
    assert cpu_alerts[0].data["value"] == 95 and cpu_alerts[0].data["level"] == "crit"
    assert state.pending == {}
    assert store.load_alert_states()[(label, "cpu")]["state"] == "firing"


async def test_a_cpu_spike_that_resolves_inside_the_lane_window_stays_silent(
    env, monkeypatch
) -> None:
    """Begun and ended during CI: the alert never went out, so neither does its clear."""
    collect = _Collect()
    monkeypatch.setattr(m, "collect_with_errors", collect)
    state, _ = _state(env, {"hosts": []})
    label = state.targets[0][1]
    busy = True
    monkeypatch.setattr(m, "_heavy_lanes_busy", lambda: busy)

    collect.samples = _samples(label, cpu=95)
    for _ in range(3):
        await m.poll_once(state)
        await _drain(env.queue)
    assert env.rec.alerts == [] and state.pending.get((label, "cpu_pct")) == "alert"

    # Cools down while lanes still hold the box → the CLEAR parks too (last edge wins).
    collect.samples = _samples(label, cpu=50)
    await m.poll_once(state)
    await _drain(env.queue)
    assert env.rec.alerts == [] and state.pending.get((label, "cpu_pct")) == "clear"

    busy = False
    await m.poll_once(state)
    await _drain(env.queue)
    assert env.rec.alerts == [], "resolved inside the CI window: total silence"
    assert state.pending == {}
    assert not any(key[1] == "cpu" for key in store.load_alert_states())


def test_heavy_lanes_busy_reads_the_slot_protocol(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NAVIG_SLOT_DIR", str(tmp_path))
    live = {12345}

    def is_alive(pid: int) -> bool:
        return pid in live

    now = datetime.now(timezone.utc)

    def write(name: str, pid: int, started: datetime) -> None:
        (tmp_path / name).write_text(
            json.dumps(
                {"pid": pid, "repo": "navig", "label": "gate", "started": started.isoformat()}
            ),
            encoding="utf-8",
        )

    # A fresh live holder is busy — express/push lanes run gates too, they count.
    write("slot-0.json", 12345, now - timedelta(minutes=5))
    assert m._heavy_lanes_busy(is_alive=is_alive) is True
    (tmp_path / "slot-0.json").unlink()
    write("push-0.json", 12345, now - timedelta(minutes=5))
    assert m._heavy_lanes_busy(is_alive=is_alive) is True
    (tmp_path / "push-0.json").unlink()

    # A dead holder and a stale file are free — a crashed gate must never silence alerts.
    write("slot-0.json", 999, now - timedelta(minutes=5))
    assert m._heavy_lanes_busy(is_alive=is_alive) is False
    write("slot-0.json", 12345, now - timedelta(hours=4))
    assert m._heavy_lanes_busy(is_alive=is_alive) is False
    (tmp_path / "slot-0.json").unlink()

    # A waiter's ticket holds no lane; malformed JSON is free; a missing dir is free.
    (tmp_path / "ticket-123-999.json").write_text("{}", encoding="utf-8")
    assert m._heavy_lanes_busy(is_alive=is_alive) is False
    (tmp_path / "slot-0.json").write_text("not json", encoding="utf-8")
    assert m._heavy_lanes_busy(is_alive=is_alive) is False
    monkeypatch.setenv("NAVIG_SLOT_DIR", str(tmp_path / "gone"))
    assert m._heavy_lanes_busy(is_alive=is_alive) is False
