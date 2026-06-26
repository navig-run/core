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
from typing import Awaitable, Callable

logger = logging.getLogger("navig.notify")

OFFLINE_GRACE_S = 30.0


class ConnectivityReporter:
    def __init__(
        self,
        *,
        offline_grace_s: float = OFFLINE_GRACE_S,
        enabled_check: Callable[[], bool] | None = None,
        sink: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.offline_grace_s = offline_grace_s
        self._enabled_check = enabled_check or (lambda: True)
        self._sink = sink or self._dispatch
        self._announced_offline = False
        self._pending: asyncio.Task | None = None

    def on_status(self, status: str) -> None:
        """Receive a raw transition from the uplink (runs in the loop thread)."""
        if status == "offline":
            if not self._announced_offline and self._pending is None:
                self._pending = asyncio.create_task(self._confirm_offline())
        elif status == "online":
            if self._pending is not None:
                self._pending.cancel()
                self._pending = None
            if self._announced_offline:
                self._announced_offline = False
                asyncio.create_task(self._emit("online"))

    async def _confirm_offline(self) -> None:
        try:
            await asyncio.sleep(self.offline_grace_s)
        except asyncio.CancelledError:
            return
        self._pending = None
        self._announced_offline = True
        await self._emit("offline")

    async def _emit(self, kind: str) -> None:
        try:
            if not self._enabled_check():
                return
            await self._sink(kind)
        except Exception:  # noqa: BLE001
            logger.debug("connectivity notify failed", exc_info=True)

    async def _dispatch(self, kind: str) -> None:
        from navig.notify import dispatch

        if kind == "offline":
            await dispatch(
                "connectivity",
                "Brain offline",
                "Lost the Lighthouse uplink — your deck/Telegram may be unreachable until it reconnects.",
                priority="high",
                data={"state": "offline"},
            )
        else:
            await dispatch(
                "connectivity",
                "Brain back online",
                "Lighthouse uplink restored — you're reachable again.",
                priority="normal",
                data={"state": "online"},
            )
