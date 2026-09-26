"""The gateway must close the browser it launched when it shuts down.

`POST /browser/navigate` launches Chromium lazily inside the daemon
(`BrowserController._ensure_started()`), and the only thing that ever closed it was an
explicit `POST /browser/stop`. A daemon restart therefore orphaned a live Chromium — its
window, its profile dir and its page still up — which is the same user-visible symptom as
the leaked CDP browsers, arriving by a different route.

The idle reaper cannot cover this one. It sweeps `cdp-launched.json`, and that registry is
written by `launch_with_cdp`; this browser is launched by **Playwright**, so it never
appears there. Shutdown is the only layer that can own it.
"""

from __future__ import annotations

from navig.gateway.server import NavigGateway


class _Controller:
    def __init__(self, raises: BaseException | None = None) -> None:
        self.stopped = 0
        self._raises = raises

    async def stop(self) -> None:
        self.stopped += 1
        if self._raises is not None:
            raise self._raises


def _gateway_with(controller) -> NavigGateway:
    """A gateway object with only what `stop()` touches — no real subsystems."""
    gw = NavigGateway.__new__(NavigGateway)
    gw.running = True
    gw.browser_controller = controller
    # Everything else `stop()` consults, in its "nothing to do" shape.
    gw.system_events = None
    gw._queue_task = None
    gw._background_tasks = set()
    gw.mission_scheduler = None
    gw.mission_executor = None
    gw.heartbeat_runner = None
    gw.cron_service = None
    gw.cloud_manager = None
    gw.config_watcher = None
    return gw


async def _run_stop(gw) -> None:
    """Drive `stop()` far enough to reach the browser teardown.

    The method touches many optional subsystems; each is guarded, and the ones that are
    not tolerate None poorly — so anything that raises past our line is swallowed here.
    The assertion is about the browser, not about the rest of shutdown.
    """
    try:
        await gw.stop()
    except Exception:  # noqa: BLE001 — later subsystems are not under test
        pass


async def test_shutdown_stops_the_browser_it_launched():
    """The regression: a daemon restart used to orphan a live Chromium."""
    ctrl = _Controller()
    await _run_stop(_gateway_with(ctrl))
    assert ctrl.stopped == 1, "the gateway shut down without closing its browser"


async def test_shutdown_survives_a_browser_that_refuses_to_close():
    """Cleanup must never be the thing that fails a shutdown."""
    ctrl = _Controller(raises=RuntimeError("playwright is wedged"))
    await _run_stop(_gateway_with(ctrl))
    assert ctrl.stopped == 1  # attempted, and the exception did not escape


async def test_shutdown_is_fine_when_no_browser_was_ever_created():
    """`browser_controller` is None when the import failed; absent on a partial init."""
    await _run_stop(_gateway_with(None))  # must not raise

    gw = _gateway_with(None)
    del gw.browser_controller  # never assigned at all
    await _run_stop(gw)  # getattr default must cover this


class _Exploding:
    """A subsystem whose teardown raises."""

    def __init__(self, what: str) -> None:
        self._what = what

    async def stop(self):
        raise RuntimeError(f"{self._what} is wedged")

    # cron/heartbeat runners are also asked to aclose() in some shapes.
    async def aclose(self):
        raise RuntimeError(f"{self._what} is wedged")


async def test_a_started_browser_is_stopped_even_if_an_earlier_subsystem_failed():
    """Shutdown is a sequence of guarded teardowns; ours must not be skipped by a
    neighbour's failure. The browser is the one that leaves a WINDOW behind."""
    ctrl = _Controller()
    gw = _gateway_with(ctrl)
    gw.system_events = _Exploding("system events")
    await _run_stop(gw)
    assert ctrl.stopped == 1


async def test_every_teardown_between_start_and_the_browser_is_guarded():
    """The teeth for the line above — and the reason it needed sharpening.

    `stop()` runs a sequence of teardowns and the browser's is near the END, so an
    UNGUARDED raise anywhere before it skips the browser entirely and the window leaks
    again. `heartbeat_runner` and `cron_service` were the only two in the sequence with
    no try/except: the first version of this file exploded `system_events`, which was
    already guarded, so it passed while the real hole stayed open.

    Explode every intervening subsystem at once — the browser must still be closed.
    """
    ctrl = _Controller()
    gw = _gateway_with(ctrl)
    for attr in (
        "system_events",
        "mission_scheduler",
        "mission_executor",
        "heartbeat_runner",
        "cron_service",
    ):
        setattr(gw, attr, _Exploding(attr))

    await _run_stop(gw)
    assert ctrl.stopped == 1, (
        "an earlier teardown raised and the browser was never closed — its window is "
        "still on the operator's screen. Guard the teardown that raised."
    )
