"""The site-probe monitor — scheduled uptime + cert expiry: sampled edges become
system events, each with a paired clear, through a REAL queue + the 1c bridge.

Cloned isolation and recording style from ``test_host_health_monitor.py``:
``NAVIG_DATA_DIR`` + ``NAVIG_CONFIG_DIR`` into tmp (never ``NAVIG_HOME``), a real
``SystemEventQueue`` with ``install_event_notify_bridge``, and a recording
``_send_one`` standing in for the channels — every assertion is about what
actually left the dispatcher.

What lives only here:

* a REAL local ``ThreadingHTTPServer`` for up → down → up (the HTTP boundary is
  the only thing faked — the SSRF guard is patched to allow loopback, the same
  patch the checks tests use); the server stops and rebinds the SAME port, so
  one configured URL flips between live and connection-refused;
* the cert ladder end to end, faking only the checks layer (``collect_http``):
  warning once → escalation to expired (the bridge's ``escalates`` rule — a
  shared latch must NOT swallow the expiry) → one tidy ``cert_ok``;
* restart persistence from ``alert_state`` (a firing latch never re-announces);
* maintenance windows: emit suppressed, edge remembered, delivered post-window;
* the three wiring surfaces + a config-less gateway never starting the loop;
* the EventTypes ↔ SYSTEM_EVENT_SPECS drift guard for the new site/cert members.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from navig.gateway import system_events as se
from navig.monitoring.checks import CheckFailure, Sample
from navig.notify import prefs, store
from navig.notify.monitors import site_probe as m
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


# ── the local site (real HTTP) ─────────────────────────────────────────────────


class _SiteHandler(BaseHTTPRequestHandler):
    """Minimal 200-OK site — stdlib, not aiohttp (the repo avoids aiohttp in
    tests: its import hangs on Windows / Python 3.14 — see
    fixtures/mock_llm_server.py)."""

    def log_message(self, fmt: str, *args) -> None:  # noqa: A002
        pass

    def do_HEAD(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", "2")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self.do_HEAD()
        self.wfile.write(b"ok")


class _LocalSite:
    """A local site that can stop and rebind the SAME port (up → down → up).

    A fixed port is the point: the monitor probes ONE configured URL across
    polls, so "the server went away" has to be a state of that URL, not a new
    URL. ``HTTPServer.allow_reuse_address`` makes the rebind legal.
    """

    def __init__(self) -> None:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        self._server: ThreadingHTTPServer | None = None
        self.start()

    def start(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", self.port), _SiteHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self._server = server

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"


def _allow_local_urls(monkeypatch) -> None:
    """SiteCheckTool's SSRF guard blocks loopback by default (secure default).

    These tests probe a local server on purpose — the same patch the checks
    tests use; the guard itself is covered by tests/tools/test_tools_site_check.py
    and the monitor keeps the real guard on for configured (non-test) URLs.
    """
    monkeypatch.setattr("navig.tools.site_check.check_url", lambda *_a, **_k: None)


# ── the checks-layer fake (cert cases: a real TLS handshake needs real certs) ──


class _FakeCollect:
    """Controllable stand-in for ``collect_http`` (only the HTTP boundary is faked)."""

    def __init__(self) -> None:
        self.samples: list[Sample] = []
        self.failures: list[CheckFailure] = []

    async def __call__(self, urls, *, host=None, checks=None):
        return list(self.samples), list(self.failures)


def _online_samples(label: str) -> list[Sample]:
    ts = time.time()
    return [
        Sample(label, "http.online", 1, "", ts, "http"),
        Sample(label, "http.status_code", 200, "", ts, "http"),
    ]


def _cert_samples(label: str, days_left: int) -> list[Sample]:
    """Healthy HTTP + a certificate *days_left* from today (negative = past expiry)."""
    ts = time.time()
    expiry = date.today() + timedelta(days=days_left)
    return [
        Sample(label, "http.online", 1, "", ts, "http"),
        Sample(label, "http.status_code", 200, "", ts, "http"),
        Sample(label, "http.cert_expiry", expiry.isoformat(), "date", ts, "http"),
    ]


CFG = {"urls": [{"url": "https://example.com", "name": "ex"}]}


# ── 1. uptime: up → down once → up once, all through the real HTTP path ───────


async def test_local_site_up_then_down_then_up_announces_each_edge_once(
    env, monkeypatch
):
    _allow_local_urls(monkeypatch)
    site = _LocalSite()
    try:
        state = m.build_state(
            {"urls": [{"url": site.url, "name": "web"}]}, event_queue=env.queue
        )

        # Healthy: a real 200 from the real server must not notify.
        await m.poll_once(state)
        await _drain(env.queue)
        assert env.rec.alerts == [], "a healthy site must not notify"

        # Server stopped: connection refused = down → exactly one site_down (high).
        site.stop()
        await m.poll_once(state)
        await _drain(env.queue)
        assert len(env.rec.alerts) == 1, "the outage must reach the channel"
        alert = env.rec.alerts[0]
        assert alert.title.startswith("Site down")
        assert alert.priority == "high"
        assert alert.data["event_type"] == "site_down"
        assert alert.data["host"] == "web" and alert.data["url"] == site.url
        assert "reason" in alert.data, "the failure reason rides in the payload"
        assert "tidy" not in alert.data, "an alert is never chat-tidied"

        # Still down: no re-announce (tracker latch + bridge latch + cooldown).
        await m.poll_once(state)
        await _drain(env.queue)
        assert len(env.rec.alerts) == 1

        # Server back on the same port: the paired site_up clears the latch.
        site.start()
        await m.poll_once(state)
        await _drain(env.queue)
        assert len(env.rec.alerts) == 2
        recovery = env.rec.alerts[1]
        assert recovery.data["event_type"] == "site_up"
        assert recovery.title.startswith("Site up")
        assert recovery.data["tidy"] == "status"
        assert store.load_alert_states()[("web", "site")]["state"] == "cleared"

        # Healthy again: silence (a routine success never chirps).
        await m.poll_once(state)
        await _drain(env.queue)
        assert len(env.rec.alerts) == 2
    finally:
        site.stop()


async def test_a_5xx_answer_counts_as_down(env, monkeypatch):
    """online is false (or status ≥ 500) → down, without a connection failure."""
    fake = _FakeCollect()
    monkeypatch.setattr(m, "collect_http", fake)
    state = m.build_state(CFG, event_queue=env.queue)

    ts = time.time()
    fake.samples = [
        Sample("ex", "http.online", 0, "", ts, "http"),
        Sample("ex", "http.status_code", 503, "", ts, "http"),
    ]
    await m.poll_once(state)
    await _drain(env.queue)
    assert len(env.rec.alerts) == 1
    assert env.rec.alerts[0].data["event_type"] == "site_down"
    assert env.rec.alerts[0].data["reason"] == "HTTP 503"


# ── 2. the cert ladder: warn once, ESCALATE to expired, one tidy clear ────────


async def test_cert_ladder_warns_once_then_escalates_to_expired_then_one_clear(
    env, monkeypatch
):
    fake = _FakeCollect()
    monkeypatch.setattr(m, "collect_http", fake)
    state = m.build_state(
        {"urls": [{"url": "https://example.com", "name": "ex",
                   "cert_warn_days": 14, "cert_crit_days": 3}]},
        event_queue=env.queue,
    )

    # 10 days out, warn band 14 → one warning (normal), once.
    fake.samples = _cert_samples("ex", 10)
    await m.poll_once(state)
    await _drain(env.queue)
    assert len(env.rec.alerts) == 1
    warn = env.rec.alerts[0]
    assert warn.data["event_type"] == "cert_expiry_warning"
    assert warn.priority == "normal"
    assert warn.title.startswith("Certificate expiring")
    assert "days: 10" in warn.body

    # Still inside the band: no second warning (tracker latch + bridge latch).
    await m.poll_once(state)
    await _drain(env.queue)
    assert len(env.rec.alerts) == 1

    # Expiry date jumps past now: the escalation must DISPATCH even though the
    # warning latched the shared "cert" bucket — the bridge's escalates rule.
    fake.samples = _cert_samples("ex", -1)
    await m.poll_once(state)
    await _drain(env.queue)
    assert len(env.rec.alerts) == 2, "a cert that has expired is new information"
    expired = env.rec.alerts[1]
    assert expired.data["event_type"] == "cert_expired"
    assert expired.priority == "high"
    assert expired.title.startswith("Certificate expired")
    assert store.load_alert_states()[("ex", "cert")]["event_type"] == "cert_expired"

    # Renewed: ONE tidy cert_ok clears the shared bucket (both trackers' clears
    # collapse to a single dispatched recovery — the second is latched out).
    fake.samples = _cert_samples("ex", 90)
    await m.poll_once(state)
    await _drain(env.queue)
    assert len(env.rec.alerts) == 3
    ok = env.rec.alerts[2]
    assert ok.data["event_type"] == "cert_ok"
    assert ok.data["tidy"] == "status"
    assert store.load_alert_states()[("ex", "cert")]["state"] == "cleared"


async def test_a_restart_does_not_re_fire_a_still_firing_cert_warning(env, monkeypatch):
    # What a previous run of the daemon left behind: we already said "expiring".
    store.save_alert_state(
        "ex", "cert", "firing", since=time.time() - 3600,
        event_type="cert_expiry_warning",
    )

    fake = _FakeCollect()
    monkeypatch.setattr(m, "collect_http", fake)
    state = m.build_state(CFG, event_queue=env.queue)

    fake.samples = _cert_samples("ex", 10)
    await m.poll_once(state)
    await _drain(env.queue)
    assert env.rec.alerts == [], "an unresolved cert alert already announced must not repeat"
    assert state.trackers[("ex", "cert_warn")].alerted is True, "the tracker was seeded"

    # The all-clear still goes out once the certificate is renewed.
    fake.samples = _cert_samples("ex", 90)
    await m.poll_once(state)
    await _drain(env.queue)
    assert len(env.rec.alerts) == 1
    assert env.rec.alerts[0].data["event_type"] == "cert_ok"
    assert store.load_alert_states()[("ex", "cert")]["state"] == "cleared"


# ── 3. maintenance windows ────────────────────────────────────────────────────


async def test_maintenance_suppresses_the_emit_then_delivers_after_the_window(
    env, monkeypatch
):
    cfg = dict(CFG, maintenance=[{"start": "22:00", "end": "23:00"}])
    fake = _FakeCollect()
    monkeypatch.setattr(m, "collect_http", fake)
    state = m.build_state(cfg, event_queue=env.queue)

    # Inside the window: sampling and tracker updates continue, but nothing emits.
    monkeypatch.setattr(m, "_now", lambda: datetime(2026, 10, 14, 22, 30))
    fake.failures = [CheckFailure("ex", "http", "connection failed: refused")]
    await m.poll_once(state)
    await _drain(env.queue)
    assert env.rec.alerts == [], "no dispatch inside a maintenance window"
    assert state.trackers[("ex", "site")].alerted is True, "the tracker still latches"
    assert state.pending.get(("ex", "site")) == "alert", "the edge is remembered"
    # NOT written to alert_state: a row here would make the bridge swallow the
    # post-window emit this flag exists to deliver.
    assert ("ex", "site") not in store.load_alert_states()

    # Window over, condition still bad: the first post-window poll emits it.
    monkeypatch.setattr(m, "_now", lambda: datetime(2026, 10, 14, 12, 0))
    await m.poll_once(state)
    await _drain(env.queue)
    assert len(env.rec.alerts) == 1
    assert env.rec.alerts[0].data["event_type"] == "site_down"
    assert state.pending == {}

    # …and the paired clear still works after the window-delivered alert.
    fake.failures = []
    fake.samples = _online_samples("ex")
    await m.poll_once(state)
    await _drain(env.queue)
    assert len(env.rec.alerts) == 2
    assert env.rec.alerts[1].data["event_type"] == "site_up"


def test_maintenance_parsing_is_shared_with_host_health():
    """Same windows, same semantics: the parser itself is imported, not copied."""
    windows = m.parse_maintenance(
        [
            {"start": "23:00", "end": "07:00"},  # spans midnight
            {"start": "02:00", "end": "04:00", "days": [6], "hosts": ["ex"]},
            {"start": "nonsense", "end": "04:00"},  # dropped loudly
            "not-a-mapping",  # dropped loudly
        ]
    )
    assert len(windows) == 2

    sat = datetime(2026, 10, 17, 23, 30)  # Saturday, weekday() == 5
    assert m.in_maintenance(sat, windows[:1], "any") is True
    assert m.in_maintenance(datetime(2026, 10, 18, 12, 0), windows[:1], "any") is False

    sunday_3am = datetime(2026, 10, 18, 3, 0)  # weekday() == 6
    assert m.in_maintenance(sunday_3am, windows[1:], "ex") is True
    assert m.in_maintenance(sunday_3am, windows[1:], "other") is False, "hosts filter"


# ── 4. config parsing: labels, defaults, and loud drops ───────────────────────


def test_targets_accept_bare_strings_and_apply_the_cert_defaults():
    targets = m._targets_from(
        [
            "https://bare.example",  # a bare string entry
            {"url": "https://two.example", "name": "two",
             "cert_warn_days": 7, "cert_crit_days": 2},
            {"name": "no-url"},  # dropped loudly
            {"url": "https://three.example", "name": "two"},  # duplicate label
        ]
    )
    # 1st and 2nd kept; the no-url and duplicate-label entries dropped.
    assert [t.label for t in targets] == ["bare.example", "two"]
    assert targets[0].url == "https://bare.example"
    assert targets[0].warn_days == m.DEFAULT_WARN_DAYS == 14
    assert targets[0].crit_days == m.DEFAULT_CRIT_DAYS == 3
    assert (targets[1].warn_days, targets[1].crit_days) == (7.0, 2.0)


def test_a_crit_band_above_the_warn_band_is_clamped(caplog):
    (target,) = m._targets_from(
        [{"url": "https://x.example", "cert_warn_days": 3, "cert_crit_days": 14}]
    )
    assert target.crit_days == target.warn_days == 3.0
    assert "clamped" in caplog.text


# ── 5. the three surfaces agree about this monitor, and boot stays opt-in ─────


def test_site_probes_is_known_and_opt_in_on_every_surface():
    from navig.gateway.deck.routes.notify import (
        _DEFAULT_ON,
        _MONITOR_KEYS,
        _monitor_availability,
    )
    from navig.gateway.server import NavigGateway
    from navig.notify.producers.samples import _SAMPLES, monitor_sample

    assert "site_probes" in NavigGateway.MONITOR_KEYS
    assert "site_probes" not in NavigGateway.MONITORS_DEFAULT_ON, "default OFF (opt-in)"
    assert "site_probes" in _MONITOR_KEYS
    assert "site_probes" not in _DEFAULT_ON, "deck default must match the gateway"
    assert "site_probes" in _SAMPLES, "the deck Test button needs a sample"
    assert monitor_sample("site_probes")[0] == "system_alert"
    # URL probing needs no platform/psutil prerequisite.
    assert _monitor_availability(
        "site_probes", mode="", is_win=False, has_psutil=False
    ) == (True, None)
    # The monitor-defaults contract must hold with the new key present.
    assert set(NavigGateway.MONITORS_DEFAULT_ON) == set(_DEFAULT_ON)
    assert set(_DEFAULT_ON) <= set(NavigGateway.MONITOR_KEYS)
    assert set(_DEFAULT_ON) <= _MONITOR_KEYS


def test_a_configless_gateway_never_starts_the_site_probe_loop():
    from navig.gateway.server import NavigGateway

    started: list[str] = []

    class _Fake:
        MONITOR_KEYS = NavigGateway.MONITOR_KEYS
        MONITORS_DEFAULT_ON = NavigGateway.MONITORS_DEFAULT_ON

        def __init__(self, monitors_cfg):
            self.config_manager = type(
                "CM", (), {"global_config": {"monitors": monitors_cfg}}
            )()
            self._monitor_tasks = {}

        def _start_monitor(self, name):
            started.append(name)

    # Unset → nothing opt-in starts (site_probes included).
    NavigGateway._init_notify_monitors(_Fake({}))
    assert "site_probes" not in started, "default OFF: config-less boot must not start it"

    # Explicit enable → it starts.
    started.clear()
    NavigGateway._init_notify_monitors(_Fake({"site_probes": {"enabled": True}}))
    assert "site_probes" in started

    # …and the string "false" `navig config set` stores keeps it off.
    started.clear()
    NavigGateway._init_notify_monitors(_Fake({"site_probes": {"enabled": "false"}}))
    assert "site_probes" not in started


def test_gateway_start_monitor_spawns_the_probe_loop_with_the_configured_dict(
    monkeypatch,
):
    """The `_start_monitor` branch must actually wire config + queue into the loop
    (a written-but-unwired branch is the defect this assertion exists to catch)."""
    from navig.gateway.server import NavigGateway
    from navig.notify.monitors import site_probe as sp

    seen: dict = {}

    def _fake_run(config=None, *, event_queue=None):
        # Record at CALL time: an async def's body would only run if awaited,
        # and the gateway hands the coroutine straight to the spawner.
        seen["config"] = config
        seen["queue"] = event_queue

        async def _noop():
            return None

        return _noop()

    monkeypatch.setattr(sp, "run_site_probe_monitor", _fake_run)

    class _Fake:
        def __init__(self):
            self.config_manager = type(
                "CM",
                (),
                {
                    "global_config": {
                        "monitors": {"site_probes": {"enabled": True, "poll_seconds": 123}}
                    }
                },
            )()
            self._monitor_tasks = {}
            self.event_queue = object()  # sentinel: the loop must receive THE queue

        def _spawn_monitor_task(self, name, coro):
            seen["spawned"] = name
            coro.close()  # a real coroutine — close it, never await it
            return "handle"

    NavigGateway._start_monitor(_Fake(), "site_probes")
    assert seen["spawned"] == "site_probes"
    assert seen["config"]["poll_seconds"] == 123, "the monitor must get its config dict"
    assert seen["queue"] is not None, "the monitor must emit through the gateway queue"


# ── 6. EventTypes ↔ SYSTEM_EVENT_SPECS drift for the new members ──────────────


def test_every_new_event_type_member_has_a_bridge_spec():
    """SITE_DOWN / SITE_UP / CERT_OK are new wire values — without a spec the
    bridge silently drops them (subscription and map are keyed off the specs)."""
    from navig.gateway.system_events import EventTypes

    for name in ("SITE_DOWN", "SITE_UP", "CERT_OK"):
        value = getattr(EventTypes, name)
        assert value in SYSTEM_EVENT_SPECS, f"EventTypes.{name} has no SYSTEM_EVENT_SPECS entry"

    assert SYSTEM_EVENT_SPECS[EventTypes.SITE_DOWN].priority == "high"
    assert SYSTEM_EVENT_SPECS[EventTypes.SITE_DOWN].recovery is False
    assert SYSTEM_EVENT_SPECS[EventTypes.SITE_DOWN].family == "site"
    assert SYSTEM_EVENT_SPECS[EventTypes.SITE_UP].recovery is True
    assert SYSTEM_EVENT_SPECS[EventTypes.SITE_UP].family == "site"
    assert SYSTEM_EVENT_SPECS[EventTypes.CERT_OK].recovery is True
    assert SYSTEM_EVENT_SPECS[EventTypes.CERT_OK].family == "cert"
    # The escalation rule is wired the direction it must be: warning → expired
    # may dispatch, and the reverse (a repeat) never does.
    warning = SYSTEM_EVENT_SPECS[EventTypes.CERT_EXPIRY_WARNING]
    assert EventTypes.CERT_EXPIRED in warning.escalates
    assert EventTypes.CERT_EXPIRY_WARNING not in warning.escalates
    assert SYSTEM_EVENT_SPECS[EventTypes.CERT_EXPIRED].escalates == ()


# ── the loop survives a bad poll ──────────────────────────────────────────────


async def test_one_bad_poll_never_kills_the_loop(env, monkeypatch):
    monkeypatch.setattr(m, "_MIN_POLL_S", 0.001)
    polls: list[int] = []

    async def _boom(state):
        polls.append(1)
        if len(polls) <= 2:
            raise RuntimeError("probe path broke")

    monkeypatch.setattr(m, "poll_once", _boom)
    task = asyncio.create_task(
        m.run_site_probe_monitor(
            {"urls": ["https://example.com"], "poll_seconds": 0.001},
            event_queue=env.queue,
        )
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


async def test_no_urls_configured_means_the_loop_never_starts(env):
    """Empty config exits instead of idling a task the deck would call running."""
    await m.run_site_probe_monitor({}, event_queue=env.queue)  # returns, does not hang


# ── the trigger feed: one outage drives BOTH the alert and the action ───────


def _register_rescue_trigger(monkeypatch, ran: list) -> None:
    """A THRESHOLD trigger whose command action appends to *ran*.

    Registration persists through the isolated NAVIG_CONFIG_DIR, so the
    monitor's lazily-built TriggerManager reads the same triggers.yaml.
    The action seam is patched on the CLASS — the feed constructs its
    manager after the patch.
    """
    from navig.commands.triggers import (
        ActionType,
        Trigger,
        TriggerAction,
        TriggerManager,
        TriggerType,
    )

    mgr = TriggerManager()
    trigger = Trigger(
        id="t-site-rescue",
        name="Site down runs a rescue",
        type=TriggerType.THRESHOLD,
        actions=[
            TriggerAction(type=ActionType.COMMAND, target="navig cron run rescue")
        ],
    )
    assert mgr.add_trigger(trigger) is True

    def _fake_run(self, command, params):
        ran.append(command)
        return True, ""

    monkeypatch.setattr(TriggerManager, "_run_command", _fake_run)


async def test_one_outage_fires_both_the_alert_and_a_registered_trigger(env, monkeypatch):
    """The acceptance path: a local server dies → channel alert AND trigger action."""
    _allow_local_urls(monkeypatch)
    ran: list = []
    _register_rescue_trigger(monkeypatch, ran)
    site = _LocalSite()
    try:
        state = m.build_state(
            {"urls": [{"url": site.url, "name": "web"}]}, event_queue=env.queue
        )

        # Healthy: neither the channel nor the trigger speaks.
        await m.poll_once(state)
        await _drain(env.queue)
        assert env.rec.alerts == [] and ran == []

        # ONE outage → the alert reaches the channel AND the trigger runs.
        site.stop()
        await m.poll_once(state)
        await _drain(env.queue)
        assert len(env.rec.alerts) == 1, "the outage must reach the channel"
        assert env.rec.alerts[0].data["event_type"] == "site_down"
        assert ran == ["navig cron run rescue"], (
            "the same outage must run the registered command action"
        )

        # Still down: neither side repeats (tracker latch + trigger cooldown).
        await m.poll_once(state)
        await _drain(env.queue)
        assert len(env.rec.alerts) == 1 and ran == ["navig cron run rescue"]
    finally:
        site.stop()


async def test_a_raising_trigger_feed_never_stops_the_monitor(env, monkeypatch):
    """A broken trigger store (or exploding action) must not kill probe or alerting."""
    _allow_local_urls(monkeypatch)
    from navig.commands.triggers import TriggerManager

    def _boom(self, event):
        raise RuntimeError("trigger store exploded")

    monkeypatch.setattr(TriggerManager, "process_event", _boom)

    site = _LocalSite()
    try:
        state = m.build_state(
            {"urls": [{"url": site.url, "name": "web"}]}, event_queue=env.queue
        )
        site.stop()
        await m.poll_once(state)
        await _drain(env.queue)
        assert len(env.rec.alerts) == 1, "the alert path is unaffected by the feed"

        # The loop is still alive: a second poll runs and latches without re-announcing.
        await m.poll_once(state)
        await _drain(env.queue)
        assert len(env.rec.alerts) == 1

        # ...and recovery still lands.
        site.start()
        await m.poll_once(state)
        await _drain(env.queue)
        assert len(env.rec.alerts) == 2
        assert env.rec.alerts[1].data["event_type"] == "site_up"
    finally:
        site.stop()


async def test_no_registered_triggers_the_feed_is_a_silent_noop(env, monkeypatch):
    """Empty store: the outage alerts exactly as before, nothing raises."""
    _allow_local_urls(monkeypatch)
    site = _LocalSite()
    try:
        state = m.build_state(
            {"urls": [{"url": site.url, "name": "web"}]}, event_queue=env.queue
        )
        site.stop()
        await m.poll_once(state)
        await _drain(env.queue)
        assert len(env.rec.alerts) == 1
        assert env.rec.alerts[0].data["event_type"] == "site_down"
    finally:
        site.stop()
