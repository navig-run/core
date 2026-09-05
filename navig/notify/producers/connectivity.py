"""Connectivity reporter — tell you when your brain loses (and regains) its
Lighthouse edge, so you know when you've gone unreachable.

The ``UplinkClient`` fires raw ``online``/``offline`` transitions at us (from the
loop thread). We debounce: an ``offline`` is only announced after it persists for
``offline_grace_s`` (uplinks reconnect constantly — a 2-second blip isn't news),
and ``online`` is announced only to clear a previously-announced outage. Gated by
``monitors.connectivity.enabled`` via an injected check, evaluated live.

The debounce state machine is pure-ish and unit-tested with a fake clock/sink.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from navig.notify.delivery import all_channels_failed
from navig.notify.producers import spawn

logger = logging.getLogger("navig.notify")

OFFLINE_GRACE_S = 30.0


class ConnectivityReporter:
    def __init__(
        self,
        *,
        offline_grace_s: float = OFFLINE_GRACE_S,
        enabled_check: Callable[[], bool] | None = None,
        sink: Callable[[str], Awaitable[Any]] | None = None,
    ) -> None:
        self.offline_grace_s = offline_grace_s
        self._enabled_check = enabled_check or (lambda: True)
        self._sink = sink or self._dispatch
        self._announced_offline = False
        self._pending: asyncio.Task | None = None
        # Serialises emissions. A dispatch fans out over the network and takes real
        # time — and the "offline" one is the slow case by definition, the link just
        # died. Without this, an "online" raised mid-dispatch overtook the "offline"
        # still in flight and the operator's LAST message read "Brain offline" while
        # the brain was up, with the clear already spent. asyncio.Lock wakes waiters
        # FIFO, so acquisition order is emission order.
        self._emit_lock = asyncio.Lock()

    def on_status(self, status: str) -> None:
        """Receive a raw transition from the uplink (runs in the loop thread)."""
        if status == "offline":
            if not self._announced_offline and self._pending is None:
                # spawn(), not a bare create_task: `_confirm_offline` clears
                # `self._pending` before it dispatches (deliberately — so an "online"
                # arriving mid-dispatch cannot cancel the send), which would leave the
                # running task with NO strong reference. asyncio holds only a weak one,
                # so the alert this producer exists to deliver could be collected
                # mid-flight. spawn() owns the reference until the task finishes;
                # `_pending` stays purely the cancellable grace-timer handle.
                self._pending = spawn(self._confirm_offline())
        elif status == "online":
            if self._pending is not None:
                self._pending.cancel()
                self._pending = None
            if self._announced_offline:
                self._announced_offline = False
                spawn(self._emit("online"))  # GC-safe ref so the "back online" push runs

    async def _confirm_offline(self) -> None:
        try:
            await asyncio.sleep(self.offline_grace_s)
        except asyncio.CancelledError:
            return
        # Release the cancellation handle before dispatching: past the grace window
        # the outage is real, so an "online" arriving mid-dispatch must NOT cancel the
        # send (it queues its own clear behind ours on `_emit_lock`). spawn() is what
        # keeps this task alive once this line drops the attribute holding it.
        self._pending = None
        self._announced_offline = True
        if not await self._emit("offline"):
            # Nobody was actually told. Drop the latch so we never later announce a
            # recovery for an outage the operator never saw — and so a subsequent
            # offline transition is free to try again. Mirrors how the resources and
            # webcam monitors settle their own latches on verified delivery.
            self._announced_offline = False

    async def _emit(self, kind: str) -> bool:
        """Deliver one transition. Returns True when it actually reached someone.

        Serialised on ``_emit_lock`` so a later transition can never overtake an
        earlier one still in flight.
        """
        async with self._emit_lock:
            try:
                if not self._enabled_check():
                    return False
                outcome = await self._sink(kind)
                # A sink that reports nothing (the test//custom-sink shape) counts as
                # delivered; only an explicit all-channels-failed fan-out is a miss.
                return not all_channels_failed(outcome)
            except Exception:  # noqa: BLE001
                logger.debug("connectivity notify failed", exc_info=True)
                return False

    async def _dispatch(self, kind: str) -> Any:
        from navig.notify import dispatch

        if kind == "offline":
            return await dispatch(
                "connectivity",
                "Brain offline",
                "Lost the Lighthouse uplink — your deck/Telegram may be unreachable until it reconnects.",
                priority="high",
                data={"state": "offline"},
            )
        else:
            return await dispatch(
                "connectivity",
                "Brain back online",
                "Lighthouse uplink restored — you're reachable again.",
                priority="normal",
                data={"state": "online"},
            )
