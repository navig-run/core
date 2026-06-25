"""
ScheduledPostService — fires due Studio posts and reschedules recurring ones.

Always-on background loop (started beside the cron service in the gateway). Every
~20s it pulls due posts from :class:`ScheduledPostStore`, publishes them through
:class:`PublishDispatcher`, writes per-target receipts + a status
(``published``/``partial``/``failed``), and for ``recurring`` posts computes the
next ``run_at`` via the existing :class:`CronParser`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ScheduledPostService:
    def __init__(self, gateway: Any | None = None, *, poll_interval: int = 20) -> None:
        self.gateway = gateway
        self.poll_interval = poll_interval
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Scheduled-post service started (poll=%ss)", self.poll_interval)

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
                await self.tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.error("scheduled-post tick error: %s", exc)
            await asyncio.sleep(self.poll_interval)

    async def tick(self) -> int:
        """Publish all due posts. Returns the number processed."""
        from navig.store.scheduled_posts import get_scheduled_posts

        store = get_scheduled_posts()
        due = store.due(_now_iso())
        for post in due:
            await self.run_post(store, post)
        return len(due)

    async def run_post(self, store: Any, post: dict[str, Any]) -> dict[str, Any]:
        """Publish one post, persist receipts + status, reschedule if recurring."""
        from navig.social.dispatcher import PublishDispatcher
        from navig.social.types import PostContent

        store.update(post["id"], status="publishing")
        content = PostContent.from_dict(post.get("content") or {})
        targets = post.get("targets") or []

        receipts = await self._dispatcher().publish(content, targets)
        rdicts = [r.to_dict() for r in receipts]
        ok = sum(1 for r in receipts if r.ok)
        if receipts and ok == len(receipts):
            status = "published"
        elif ok:
            status = "partial"
        else:
            status = "failed"
        errors = "; ".join(r.error for r in receipts if r.error) or None

        if post.get("schedule_kind") == "recurring" and post.get("cron_expr"):
            next_at = self._next_run(post["cron_expr"])
            store.update(post["id"], status="scheduled", run_at=next_at, receipts=rdicts, last_error=errors)
        else:
            store.update(post["id"], status=status, receipts=rdicts, last_error=errors)

        await self._emit(post["id"], status)
        return {"id": post["id"], "status": status, "receipts": rdicts}

    def _dispatcher(self):
        tg = None
        gw = self.gateway
        if gw is not None and hasattr(gw, "channels"):
            tg = gw.channels.get("telegram")
        from navig.social.dispatcher import PublishDispatcher

        return PublishDispatcher(telegram_channel=tg)

    @staticmethod
    def _next_run(cron_expr: str) -> str:
        try:
            from navig.scheduler.cron_service import CronParser

            base = datetime.now(timezone.utc).replace(tzinfo=None)
            return CronParser.calculate_next(cron_expr, from_time=base).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not compute next run for %r: %s", cron_expr, exc)
            # Fall back to +1h so a broken expression doesn't wedge the queue.
            from datetime import timedelta

            return (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    async def _emit(self, post_id: int, status: str) -> None:
        try:
            from navig.gateway.system_events import get_system_events

            queue = get_system_events()
            if queue is not None:
                await queue.emit("studio_post_update", {"id": post_id, "status": status})
        except Exception:  # noqa: BLE001
            pass
