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


async def _loop(gateway) -> None:
    last_briefing_key: str | None = None
    while True:
        # 1) Keep the inbound SMS webhook in sync with the public URL.
        try:
            from navig.notify.sms_webhook_config import auto_configure

            await auto_configure(gateway)
        except Exception:
            logger.debug("notify: sms webhook auto-config tick failed", exc_info=True)

        # 2) Fire scheduled briefings (once per matching minute).
        try:
            from navig.notify import prefs

            s = prefs.get_settings()
            if s["briefing_enabled"] and s["briefing_times"]:
                now = datetime.now()
                hhmm = now.strftime("%H:%M")
                key = now.strftime("%Y-%m-%d %H:%M")
                if hhmm in s["briefing_times"] and key != last_briefing_key:
                    last_briefing_key = key
                    from navig.notify.briefings import build_and_dispatch_briefing

                    await build_and_dispatch_briefing()
        except Exception:
            logger.debug("notify: briefing tick failed", exc_info=True)

        # 3) Email-ops: filter→notify on new mail + scheduled email briefings.
        try:
            from navig.email_ops.service import get_email_service

            await get_email_service().tick(gateway)
        except Exception:
            logger.debug("notify: email_ops tick failed", exc_info=True)

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
