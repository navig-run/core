"""Creative scan — every 6 hours, look for grounded improvement proposals.

Thin scheduled loop (the ``lens_updates`` pattern): paced from the proposal
store's ``last_scan_at`` rather than from boot, so a daemon restarted ten times
a day still scans once every 6 h. Default ON but **idle unless Creative Agent
Mode is on** — an opportunity nobody hears about is an improvement nobody gets,
and Standard mode must never proactively read history (``run_scan`` enforces
that gate and still records the pacing heartbeat).

Nothing here applies anything: a scan only proposes; building, publishing and
dismissing stay operator actions.
"""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger("navig.notify")

INTERVAL_S = 6 * 3600.0
#: First scan after boot: let the daemon settle (and any startup gate finish).
BOOT_DELAY_S = 600.0

__all__ = ["INTERVAL_S", "BOOT_DELAY_S", "seconds_until_due", "scan_once", "run_creative_scan_monitor"]


def seconds_until_due(last_scan_epoch: float | None, *, now: float | None = None) -> float:
    """Seconds until the next scan (0 = due now). ``None`` last scan → due."""
    if not last_scan_epoch:
        return 0.0
    now = time.time() if now is None else now
    return max(0.0, INTERVAL_S - (now - float(last_scan_epoch)))


async def scan_once() -> dict | None:
    """One scan pass. ``None`` when the pass itself failed (loop backs off).

    ``run_scan`` always records the pacing stamp on success (even for a Standard
    skip or a no-op), so a returned summary means the stamp advanced; ``None``
    means the pass raised before that and the loop must back off.
    """
    from navig.creative.signals import run_scan

    try:
        # File/SQLite work — never block the event loop.
        summary = await asyncio.to_thread(run_scan)
    except Exception as exc:  # noqa: BLE001 — a bad pass must not end the loop
        logger.warning("creative scan failed: %s", exc)
        return None
    if summary.get("created"):
        logger.info(
            "creative scan: %d proposal(s), %d dispatched",
            len(summary["created"]),
            len(summary["dispatched"]),
        )
    return summary


async def run_creative_scan_monitor() -> None:
    await asyncio.sleep(BOOT_DELAY_S)
    while True:
        from navig.creative.proposals import last_scan_at

        try:
            wait = seconds_until_due(last_scan_at())
        except Exception:  # noqa: BLE001 — unreadable store: scan now, it reports why
            wait = 0.0
        if wait > 0:
            await asyncio.sleep(wait)
            continue
        summary = await scan_once()
        # A pass that returned None did not advance the stamp — back off an hour
        # rather than hammer every minute; a healthy pass waits out the interval
        # on the next loop's due-check.
        await asyncio.sleep(60.0 if summary is not None else 3600.0)
