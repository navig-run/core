"""
MissionScheduler — the autonomous tick + restart-recovery backstop.

v1 responsibility: re-drive missions that are persisted as QUEUED but aren't
being handled by the executor — i.e. missions orphaned by a daemon restart. The
live ``submit`` path already spawns a task for each new mission, so this is a
safety net, not the primary driver.

Pattern-matches HeartbeatRunner (start / stop, a single asyncio.Task loop). Gated
off with the rest of the autonomous layer via ``missions.autonomous_enabled``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class MissionScheduler:
    def __init__(self, gateway: Any, executor: Any, *, interval_secs: int = 300) -> None:
        self.gateway = gateway
        self.executor = executor
        self.interval_secs = max(30, int(interval_secs))
        self._task: asyncio.Task | None = None
        self._running = False
        self._dispatched: set[str] = set()

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await self._sweep()  # immediate recovery of orphaned missions
        self._task = asyncio.create_task(self._loop())
        logger.info("MissionScheduler started (interval=%ss)", self.interval_secs)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.interval_secs)
            except asyncio.CancelledError:
                break
            if not self._running:
                break
            try:
                await self._sweep()
            except Exception as exc:  # noqa: BLE001
                logger.debug("MissionScheduler sweep error: %s", exc)

    async def _sweep(self) -> None:
        """Re-drive any QUEUED mission the executor isn't already handling."""
        from navig.contracts.mission import MissionStatus

        try:
            queued = self.executor.store.list_missions(status=MissionStatus.QUEUED, limit=100)
        except Exception as exc:  # noqa: BLE001
            logger.debug("MissionScheduler list_missions failed: %s", exc)
            return

        for m in queued:
            mid = m.mission_id
            # Skip missions already in-flight (incl. awaiting approval) or ones
            # we've already handed back to the executor this process lifetime.
            if mid in self.executor.active or mid in self._dispatched:
                continue
            self._dispatched.add(mid)
            try:
                await self.executor.submit(m)
                logger.info("MissionScheduler recovered orphaned mission %s", mid[:8])
            except Exception as exc:  # noqa: BLE001
                logger.warning("MissionScheduler failed to resume %s: %s", mid[:8], exc)
