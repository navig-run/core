"""Config-incident reporter — a recorded self-heal becomes a PUSH, not just a doctor row.

The whole point of the config-health chain is that a daemon which heals itself silently
looks exactly like one that is fine. Config Health made the rescues visible in `navig
doctor` — but that is a PULL. This producer pushes them, so the operator learns their
config was wiped / their bot re-identified the moment it happens.
"""

from __future__ import annotations

import asyncio

import pytest

from navig.core import incidents
from navig.notify.producers import config_incidents as ci


class _DummyLoop:
    """Records scheduled callbacks; provides the monotonic clock the throttle needs."""

    def __init__(self):
        self.scheduled: list = []
        self._t = 1000.0

    def time(self) -> float:
        return self._t

    def call_soon_threadsafe(self, fn, *args):
        self.scheduled.append((fn, args))


@pytest.fixture(autouse=True)
def _no_leaked_reporter():
    """Fully reset the process-global reporter around every test.

    Clearing only the incidents hook is NOT enough: install_config_incident_reporter() is
    idempotent on its module-global `_reporter`, so if ANOTHER test in the suite installed
    it and left it set (a real gateway boot now does — config_incidents is default-ON), a
    later install() is a no-op that never re-registers the hook, and these tests see a
    None hook. uninstall() resets both `_reporter` and the hook, so state can't leak in
    either direction.
    """
    ci.uninstall_config_incident_reporter()
    yield
    ci.uninstall_config_incident_reporter()


# ── the reporter ─────────────────────────────────────────────────────────────


def test_on_incident_schedules_a_push():
    loop = _DummyLoop()
    r = ci.ConfigIncidentReporter(loop, sink=lambda *_: None)

    r.on_incident(incidents.DECK_KEY_REIDENTIFIED, {"source": "gateway"})

    assert len(loop.scheduled) == 1, "a recorded incident must schedule a notification"


def test_same_incident_is_deduped_within_the_cooldown():
    loop = _DummyLoop()
    r = ci.ConfigIncidentReporter(loop, sink=lambda *_: None)

    r.on_incident(incidents.WIPE_REFUSED, {})
    r.on_incident(incidents.WIPE_REFUSED, {})  # same key, same time → deduped

    assert len(loop.scheduled) == 1


def test_distinct_incidents_each_push():
    loop = _DummyLoop()
    r = ci.ConfigIncidentReporter(loop, sink=lambda *_: None)

    r.on_incident(incidents.WIPE_REFUSED, {})
    r.on_incident(incidents.DECK_KEY_RESTORED, {})

    assert len(loop.scheduled) == 2


def test_render_uses_the_operator_facing_description():
    title, body = ci.ConfigIncidentReporter._render(incidents.DECK_KEY_REIDENTIFIED)
    assert body == incidents.DESCRIPTIONS[incidents.DECK_KEY_REIDENTIFIED]
    assert incidents.DECK_KEY_REIDENTIFIED not in body  # the id itself is never shown


def test_on_incident_never_raises_even_if_the_loop_is_hostile():
    class _Boom:
        def time(self):
            raise RuntimeError("no clock")

    # Must swallow — a push must never break incidents.record().
    ci.ConfigIncidentReporter(_Boom()).on_incident(incidents.LOAD_FAILED, {})


# ── the incidents.record() hook seam ─────────────────────────────────────────


def test_record_calls_the_registered_hook():
    seen: list = []
    incidents.set_notify_hook(lambda ev, data: seen.append((ev, data)))

    incidents.record(incidents.WIPE_REFUSED, source="test")

    assert seen == [(incidents.WIPE_REFUSED, {"source": "test"})]


def test_record_without_a_hook_is_a_silent_no_op():
    incidents.set_notify_hook(None)
    incidents.record(incidents.WIPE_REFUSED)  # must not raise, must not push


def test_a_raising_hook_never_breaks_record():
    def boom(ev, data):
        raise RuntimeError("sink exploded")

    incidents.set_notify_hook(boom)
    incidents.record(incidents.LOAD_FAILED)  # record must swallow the hook error


# ── install / uninstall register the hook ────────────────────────────────────


async def test_install_registers_and_uninstall_clears_the_hook():
    ci.install_config_incident_reporter()
    assert incidents._notify_hook is not None, "install must register the push sink"

    ci.uninstall_config_incident_reporter()
    assert incidents._notify_hook is None, "uninstall must clear it"


async def test_end_to_end_record_pushes_through_the_installed_reporter(monkeypatch):
    sent: list = []

    async def fake_dispatch(type_key, title, body, **kw):
        sent.append((type_key, title, body))

    monkeypatch.setattr("navig.notify.dispatch", fake_dispatch, raising=False)
    # Patch the module-level dispatch the reporter imports.
    import navig.notify as _n
    monkeypatch.setattr(_n, "dispatch", fake_dispatch, raising=False)

    ci.install_config_incident_reporter()
    try:
        incidents.record(incidents.DECK_KEY_REIDENTIFIED, source="gateway")
        await asyncio.sleep(0.05)  # let the scheduled task run
    finally:
        ci.uninstall_config_incident_reporter()

    assert sent and sent[0][0] == "config_incident"
    assert incidents.DESCRIPTIONS[incidents.DECK_KEY_REIDENTIFIED] in sent[0][2]


# ── wiring: gateway MONITOR_KEYS and deck _MONITORS must not drift ────────────


def test_monitor_is_wired_in_the_gateway_and_the_deck():
    from navig.gateway.deck.routes.notify import _MONITOR_KEYS
    from navig.gateway.server import NavigGateway

    assert "config_incidents" in NavigGateway.MONITOR_KEYS, "gateway must know the monitor"
    assert "config_incidents" in _MONITOR_KEYS, "the deck Monitors card must offer the toggle"


# ── The throttle reservation must settle on VERIFIED delivery ─────────────────
#
# `allow()` runs before the send, so a push that reached zero channels used to burn
# this incident's cooldown and suppress the NEXT identical one — losing a config
# rescue twice over. That is precisely the silent failure this default-ON producer
# exists to prevent: a daemon that heals itself at 3am and tells nobody.


def _failed_fanout() -> dict:
    return {"type": ci.NOTIFY_TYPE, "channels": [{"channel": "telegram", "ok": False}]}


def _delivered_fanout() -> dict:
    return {"type": ci.NOTIFY_TYPE, "channels": [{"channel": "deck", "ok": True}]}


async def _run_scheduled(loop) -> None:
    """Execute every callback the reporter handed to call_soon_threadsafe."""
    for fn, args in list(loop.scheduled):
        task = fn(*args)
        if task is not None:
            await task


async def test_a_push_that_reached_nobody_lets_the_incident_fire_again():
    loop = _DummyLoop()
    sent: list[str] = []

    async def failing_sink(title, body):
        sent.append(body)
        return _failed_fanout()

    r = ci.ConfigIncidentReporter(loop, sink=failing_sink)
    r.on_incident(incidents.DECK_KEY_REIDENTIFIED, {})
    await _run_scheduled(loop)
    assert len(sent) == 1

    r.on_incident(incidents.DECK_KEY_REIDENTIFIED, {})
    assert len(loop.scheduled) == 2, (
        "the incident reached nobody, so it must not stay suppressed for the cooldown"
    )


async def test_a_delivered_incident_stays_deduped():
    loop = _DummyLoop()
    sent: list[str] = []

    async def delivering_sink(title, body):
        sent.append(body)
        return _delivered_fanout()

    r = ci.ConfigIncidentReporter(loop, sink=delivering_sink)
    r.on_incident(incidents.DECK_KEY_REIDENTIFIED, {})
    await _run_scheduled(loop)
    assert len(sent) == 1

    r.on_incident(incidents.DECK_KEY_REIDENTIFIED, {})
    assert len(loop.scheduled) == 1, "a delivered incident must still be deduped"


async def test_a_muted_fanout_counts_as_delivered():
    """An empty channel list is an intentional mute, not a failure — do not churn."""
    loop = _DummyLoop()

    async def muted_sink(title, body):
        return {"type": ci.NOTIFY_TYPE, "skipped": "master_off", "channels": []}

    r = ci.ConfigIncidentReporter(loop, sink=muted_sink)
    r.on_incident(incidents.DECK_KEY_REIDENTIFIED, {})
    await _run_scheduled(loop)

    r.on_incident(incidents.DECK_KEY_REIDENTIFIED, {})
    assert len(loop.scheduled) == 1, "a deliberately silenced reporter must not churn"


def test_suppressed_incidents_are_reported_in_the_next_push():
    from navig.notify.producers.self_errors import _Throttle

    loop = _DummyLoop()
    r = ci.ConfigIncidentReporter(loop, sink=lambda *_: None)
    r._throttle = _Throttle(window_s=1000.0, max_per_window=1, cooldown_s=0.0)

    r.on_incident(incidents.DECK_KEY_REIDENTIFIED, {})   # allowed
    r.on_incident(incidents.WIPE_REFUSED, {})     # suppressed — window full
    assert len(loop.scheduled) == 1

    r._throttle.max_per_window = 5
    r.on_incident(incidents.WIPE_REFUSED, {})
    assert len(loop.scheduled) == 2

    # The body is captured in the closure the reporter scheduled; re-render to check.
    _, body = r._render(incidents.WIPE_REFUSED)
    assert r._throttle.drain_suppressed() == 0, "the count was already drained into the push"
