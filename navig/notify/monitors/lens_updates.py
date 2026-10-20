"""Lens updates — once a day, fetch each Lens's install link and stage what changed.

Tampermonkey checks a script's update URL daily; this is the same loop, with one
difference that is the point of it: **nothing is applied**. A changed source is staged
beside the running copy (:meth:`navig.lenses.store.LensStore.check_updates`) and a
``lens_review`` notification says it is waiting, with the diff and what it would newly be
allowed to do one click away. An update that adds ``@match <all_urls>`` must not reach
every site the operator visits because a stranger pushed to a gist.

Default ON (``monitors.lens_updates.enabled``): it is idle unless a Lens was installed from
a link, it only GETs links the operator chose, and an update nobody hears about is a
security fix nobody applies. Paced from the store's ``last_update_check`` rather than from
boot, so a daemon restarted ten times a day still checks once a day.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

logger = logging.getLogger("navig.notify")

INTERVAL_S = 24 * 3600.0
#: First check after boot: let the daemon settle, and never fetch during a restart storm.
BOOT_DELAY_S = 600.0


def seconds_until_due(last_check: str | None, *, now: dt.datetime | None = None) -> float:
    """How long to wait before the next check (0 = due now)."""
    if not last_check:
        return 0.0
    try:
        last = dt.datetime.fromisoformat(last_check)
    except ValueError:
        return 0.0
    if last.tzinfo is None:
        last = last.replace(tzinfo=dt.timezone.utc)
    now = now or dt.datetime.now(dt.timezone.utc)
    return max(0.0, INTERVAL_S - (now - last).total_seconds())


async def check_once() -> list[dict] | None:
    """One pass: check every linked Lens, notify about what was staged. Never raises.

    ``None`` when the pass itself failed (an unreadable store) — the loop then backs off an
    hour instead of retrying every minute.
    """
    from navig.lenses import LensStore
    from navig.lenses.notify import notify_review, updates_message

    try:
        rows = await asyncio.to_thread(LensStore().check_updates)
    except Exception as exc:  # noqa: BLE001 — a bad pass must not end the loop
        logger.warning("lens update check failed: %s", exc)
        return None
    message = updates_message(rows)
    if message:
        staged = [r["id"] for r in rows if r.get("status") == "staged"]
        await notify_review(*message, lens_ids=staged)
    for row in rows:
        if row.get("status") == "error":
            logger.info("lens update check: %s — %s", row.get("id"), row.get("error"))
    return rows


async def run_lens_update_monitor() -> None:
    from navig.lenses import LensStore

    await asyncio.sleep(BOOT_DELAY_S)
    while True:
        try:
            wait = seconds_until_due(await asyncio.to_thread(LensStore().last_update_check))
        except Exception:  # noqa: BLE001 — an unreadable index: check now, it reports why
            wait = 0.0
        if wait > 0:
            await asyncio.sleep(wait)
            continue
        rows = await check_once()
        # The next loop re-reads last_update_check (now fresh); a failed pass did not
        # record one, so back off rather than hammering every minute.
        await asyncio.sleep(60.0 if rows is not None else 3600.0)
