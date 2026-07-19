"""Notify scheduler — a single lightweight daemon loop that:

  1. keeps Twilio's inbound-SMS webhook pointed at the current public URL
     (handles quick-tunnel rotation automatically), and
  2. fires the AI briefing at the user-configured times.

Cron-style command jobs can't run an async Python dispatch cleanly, so this is
an in-process asyncio loop started at gateway boot (mirrors the TelegramNotifier
scheduler). Ticks every 45s — cheap, and only PATCHes Twilio when the URL changes.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

logger = logging.getLogger("navig.notify")

_task: asyncio.Task | None = None
_TICK_SECONDS = 45


def _due_briefings(last_check: datetime, now: datetime, times: list[str]) -> list[str]:
    """Briefing ``"HH:MM"`` times whose scheduled instant (for *now*'s date) falls
    in the half-open window ``(last_check, now]``.

    Window-based, NOT an exact-minute string match: a slow tick (the per-tick SMS
    PATCH + network email scan can overrun the 45s interval) then cannot skip the
    briefing's minute — it fires on the next tick instead of being lost for the
    day. Contiguous half-open windows (each tick sets ``last_check = now``) mean a
    given instant lands in exactly one window, so it fires exactly once; seeding
    ``last_check = now`` at startup keeps a restart from replaying times already
    past. Malformed / out-of-range entries are skipped.
    """
    due: list[str] = []
    for t in times:
        try:
            hh, mm = (int(x) for x in str(t).split(":", 1))
            inst = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        except (ValueError, TypeError):
            continue
        if last_check < inst <= now:
            due.append(t)
    return due


async def _loop(gateway) -> None:
    # Anchor for the briefing window; seeded to now so a restart doesn't replay
    # briefing times that already passed today. Advanced every tick below.
    last_check = datetime.now()
    while True:
        # 1) Keep the inbound SMS webhook in sync with the public URL.
        try:
            from navig.notify.sms_webhook_config import auto_configure

            await auto_configure(gateway)
        except Exception:
            logger.debug("notify: sms webhook auto-config tick failed", exc_info=True)

        # 2) Fire scheduled briefings — window-based so a slow tick can't skip a
        #    minute (see _due_briefings). One dispatch per tick even if several
        #    times fall in the same window (e.g. after a long stall).
        now = datetime.now()
        try:
            from navig.notify import prefs

            s = prefs.get_settings()
            if s["briefing_enabled"] and _due_briefings(last_check, now, s.get("briefing_times") or []):
                from navig.notify.briefings import build_and_dispatch_briefing

                await build_and_dispatch_briefing()
        except Exception:
            logger.debug("notify: briefing tick failed", exc_info=True)
        last_check = now  # advance the window every tick, even on error

        # 3) Email-ops: filter→notify on new mail + scheduled email briefings.
        # Lives in the optional navig-email plugin; the import is soft (skipped
        # when the plugin isn't installed) and only ticks when the "email" module
        # is enabled.
        try:
            from navig.modules.registry import get_registry

            if get_registry().is_enabled("email"):
                from navig_email.service import get_email_service

                await get_email_service().tick(gateway)
        except Exception:
            logger.debug("notify: email tick skipped", exc_info=True)

        await asyncio.sleep(_TICK_SECONDS)


def start(gateway) -> "asyncio.Task | None":
    """Start the scheduler loop (idempotent). Requires a running event loop."""
    global _task
    if _task is not None and not _task.done():
        return _task
    try:
        _task = asyncio.ensure_future(_loop(gateway))
        logger.info("Notify scheduler started")
    except Exception as exc:  # noqa: BLE001
        logger.debug("notify scheduler start skipped: %s", exc)
        _task = None
    return _task
