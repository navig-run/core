"""The browser_reaper MONITOR — the wiring around `reap_idle_browsers`, not the sweep.

`targets.reap_idle_browsers` (what to close, and everything it refuses) is covered by
tests/browser/test_reap_idle_browsers.py. This file covers the layer above it, which had
no test at all despite being registered in `NavigGateway.MONITOR_KEYS` and rendered on the
deck's Monitors card: **wired but unexercised** — the inverse of the "written · tested ·
never wired" class, and just as invisible.

What lives only here:

* the ``browser.reap_idle_minutes`` read, including the `navig config set` string form
  and the documented `0` disable;
* ``sweep_once``'s promise that a monitor survives a bad pass;
* the notification TYPE, which decides where the operator sees a cleanup.
"""

from __future__ import annotations

import asyncio

import pytest

from navig.notify.monitors import browser_reaper as m


@pytest.fixture
def cfg(monkeypatch):
    """Control what `browser.reap_idle_minutes` resolves to."""
    box: dict = {"value": None}

    class _CM:
        def get(self, key, default=None):
            assert key == "browser.reap_idle_minutes", f"unexpected key {key!r}"
            return default if box["value"] is None else box["value"]

    monkeypatch.setattr("navig.config.get_config_manager", lambda: _CM())
    return box


# ── the idle threshold ────────────────────────────────────────────────────────


def test_default_is_thirty_minutes(cfg):
    assert m._idle_seconds() == m.DEFAULT_IDLE_MINUTES * 60.0


def test_a_configured_value_is_honoured(cfg):
    cfg["value"] = 5
    assert m._idle_seconds() == 300.0


def test_the_string_form_navig_config_set_stores_is_honoured(cfg):
    """`navig config set` writes its argument verbatim, so this arrives as a STRING.

    A raw `int()` would raise and silently fall back to the default, so an operator who
    set 5 minutes would keep getting 30 with nothing to show for it.
    """
    cfg["value"] = "5"
    assert m._idle_seconds() == 300.0


def test_zero_disables_the_sweep_and_is_not_overridden_by_the_default(cfg):
    """`0` is documented as "disable". It must survive as 0, not be rescued to 30.

    The except-branch deliberately falls back to the DEFAULT rather than 0 (a config
    hiccup must not silently disable the cleanup); this pins that the reverse is also
    true — an explicit 0 is honoured.
    """
    cfg["value"] = 0
    assert m._idle_seconds() == 0.0
    cfg["value"] = "0"
    assert m._idle_seconds() == 0.0


def test_garbage_falls_back_to_the_default_rather_than_disabling(cfg):
    """An unparseable value must not resolve to 0 — that would silently stop cleanup."""
    for junk in ("abc", "", None if False else "  "):
        cfg["value"] = junk
        assert m._idle_seconds() == m.DEFAULT_IDLE_MINUTES * 60.0, junk


def test_a_negative_value_cannot_produce_a_negative_threshold(cfg):
    """A negative idle window would make every browser instantly "idle"."""
    cfg["value"] = -10
    assert m._idle_seconds() == 0.0  # clamped, i.e. disabled — never negative


def test_an_exploding_config_read_does_not_stop_the_sweep(monkeypatch):
    def _boom():
        raise RuntimeError("config.yaml is locked")

    monkeypatch.setattr("navig.config.get_config_manager", _boom)
    assert m._idle_seconds() == m.DEFAULT_IDLE_MINUTES * 60.0


# ── one sweep ─────────────────────────────────────────────────────────────────


def test_sweep_once_passes_the_resolved_threshold_through(cfg, monkeypatch):
    cfg["value"] = 2
    seen: dict = {}

    # A real function, not `seen.setdefault(...) or {...}`: setdefault RETURNS the value,
    # so a truthy threshold short-circuits the `or` and the fake returns 120.0 instead of
    # the result dict. The fake would then be testing itself.
    def _fake_reap(idle):
        seen["idle"] = idle
        return {"reaped": [1]}

    monkeypatch.setattr("navig.browser.targets.reap_idle_browsers", _fake_reap)
    assert m.sweep_once() == {"reaped": [1]}
    assert seen["idle"] == 120.0


def test_sweep_once_never_raises(cfg, monkeypatch):
    """A monitor that dies on one bad pass stops cleaning up forever, silently."""
    def _boom(idle):
        raise RuntimeError("psutil exploded")

    monkeypatch.setattr("navig.browser.targets.reap_idle_browsers", _boom)
    res = m.sweep_once()  # must not raise
    assert res["reaped"] == []
    assert res["errors"] and "psutil exploded" in res["errors"][0]


# ── how the operator hears about it ───────────────────────────────────────────


async def test_announce_dispatches_the_system_alert_type(monkeypatch):
    """NOT `config_incident`: that type means the config/identity layer rescued itself,
    and filing a browser cleanup there would dilute the one signal that means the bot is
    about to go deaf."""
    sent: dict = {}

    async def _dispatch(notify_type, title, body, **kw):
        sent.update(type=notify_type, title=title, body=body, **kw)
        return {"ok": True}

    monkeypatch.setattr("navig.notify.dispatch", _dispatch)
    await m._announce([9222, 9333])

    assert sent["type"] == m.NOTIFY_TYPE == "system_alert"
    assert "9222" in sent["body"] and "9333" in sent["body"]
    assert sent["data"]["ports"] == [9222, 9333]


async def test_announce_never_breaks_the_loop(monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("no channels configured")

    monkeypatch.setattr("navig.notify.dispatch", _boom)
    await m._announce([9222])  # must not raise


async def test_announce_says_one_browser_not_1_browsers(monkeypatch):
    """Plural agreement — the operator reads this line, and "1 browsers" is sloppy."""
    sent: dict = {}

    async def _dispatch(notify_type, title, body, **kw):
        sent["body"] = body
        return {}

    monkeypatch.setattr("navig.notify.dispatch", _dispatch)
    await m._announce([9222])
    assert "1 idle automation browser " in sent["body"]
    assert "browsers" not in sent["body"]


# ── the monitor is actually registered ────────────────────────────────────────


def test_the_monitor_is_wired_into_the_gateway_and_the_deck():
    """A monitor nothing registers is documentation. Both surfaces or neither."""
    from navig.gateway.deck.routes.notify import _MONITOR_KEYS
    from navig.gateway.server import NavigGateway

    assert "browser_reaper" in NavigGateway.MONITOR_KEYS
    assert "browser_reaper" in _MONITOR_KEYS


# ── the loop is never blocked by a sweep ──────────────────────────────────────


def test_the_sweep_runs_off_the_event_loop():
    """`sweep_once` is synchronous and slow: it probes ports with 1s HTTP timeouts, asks
    each idle browser to close and waits up to `GRACEFUL_CLOSE_S` for it, then kills and
    re-probes. Called inline from the monitor coroutine it froze the whole daemon for the
    duration — and `tests/gateway/test_no_loop_blocking.py` cannot see it, because that
    guard flags blocking calls in a coroutine's OWN body, not inside a sync callee.

    Pinned on the AST: the coroutine may only reach `sweep_once` through `to_thread` /
    `run_in_executor`.
    """
    import ast
    import inspect

    from navig.notify.monitors import browser_reaper as m

    tree = ast.parse(inspect.getsource(m.run_browser_reaper))
    inline, offloaded = [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        # sweep_once() called directly
        if isinstance(f, ast.Name) and f.id == "sweep_once":
            inline.append(node)
        # asyncio.to_thread(sweep_once) / loop.run_in_executor(None, sweep_once)
        if isinstance(f, ast.Attribute) and f.attr in {"to_thread", "run_in_executor"}:
            if any(isinstance(a, ast.Name) and a.id == "sweep_once" for a in node.args):
                offloaded.append(node)
    assert not inline, "sweep_once() is called inline on the event loop — it blocks the daemon"
    assert offloaded, "the sweep is not handed to a worker at all"


async def test_the_reaper_loop_yields_while_a_slow_sweep_runs(monkeypatch):
    """Behavioural twin of the AST pin: while a sweep takes 300ms of REAL blocking time,
    another coroutine on the same loop must still get scheduled."""
    import time

    from navig.notify.monitors import browser_reaper as m

    ticks: list[float] = []

    def _slow_sweep():
        time.sleep(0.3)  # a genuinely blocking sweep
        return {"reaped": [], "errors": []}

    async def _ticker():
        for _ in range(6):
            ticks.append(time.monotonic())
            await asyncio.sleep(0.05)

    monkeypatch.setattr(m, "sweep_once", _slow_sweep)
    reaper = asyncio.create_task(m.run_browser_reaper(poll_s=10))
    try:
        await asyncio.wait_for(_ticker(), timeout=2.0)
    finally:
        reaper.cancel()
        try:
            await reaper
        except asyncio.CancelledError:
            pass
    # Six ticks 50ms apart span ~300ms. If the sweep blocked the loop, the ticker could not
    # have run until it finished, and the gaps would show one ≥300ms hole.
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert len(ticks) == 6
    assert max(gaps) < 0.25, f"the loop was frozen by the sweep: gaps={[f'{g:.2f}' for g in gaps]}"
