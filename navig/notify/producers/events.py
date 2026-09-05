"""First-party event helpers other in-daemon code calls to emit notifications
into the NAVIG category.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from navig.notify.producers import spawn

logger = logging.getLogger("navig.notify")


async def report_deploy(
    service: str, version: str = "", status: str = "ok", note: str = ""
) -> dict[str, Any]:
    """Announce a deploy (edge / Mini App / your CI) as a ``deploy`` notification."""
    from navig.notify import dispatch

    label = f"{service} {version}".strip()
    # Strip before comparing: callers pass this through from CLI args and CI
    # variables, and an unnoticed trailing newline or space turned a successful
    # deploy into a high-priority "Deploy ok : edge" failure alert.
    status = (status or "").strip()
    ok = status.lower() in ("ok", "success", "succeeded", "done")
    title = f"Deployed {label}" if ok else f"Deploy {status}: {label}"
    return await dispatch(
        "deploy",
        title,
        note or status,
        priority="normal" if ok else "high",
        data={"service": service, "version": version, "status": status},
    )


def report_deploy_sync(service: str, version: str = "", status: str = "ok", note: str = "") -> None:
    """Best-effort sync wrapper for CLI deploy commands.

    Always records to the deck feed (shared notify.db); Telegram/other channels
    fire when this runs inside the daemon (where the channel senders live).
    """
    # Decide by ASKING for the loop, not by letting asyncio.run raise. Passing a
    # freshly-built coroutine to asyncio.run inside a running loop raises before it
    # is ever awaited, leaving it to be collected with a "coroutine 'report_deploy'
    # was never awaited" RuntimeWarning — noise in the daemon log on every deploy
    # reported from inside the loop. Each branch now builds the coroutine once.
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            asyncio.run(report_deploy(service, version=version, status=status, note=note))
        except Exception:
            logger.debug("deploy notify failed", exc_info=True)
        return

    # Inside the daemon's loop — schedule it. spawn() keeps a strong ref so the
    # scheduled deploy push can't be GC'd before it runs.
    try:
        spawn(report_deploy(service, version=version, status=status, note=note))
    except Exception:
        logger.debug("deploy notify (scheduled) failed", exc_info=True)
