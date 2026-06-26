"""First-party event helpers other in-daemon code calls to emit notifications
into the NAVIG category.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("navig.notify")


async def report_deploy(
    service: str, version: str = "", status: str = "ok", note: str = ""
) -> dict[str, Any]:
    """Announce a deploy (edge / Mini App / your CI) as a ``deploy`` notification."""
    from navig.notify import dispatch

    label = f"{service} {version}".strip()
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
    try:
        asyncio.run(report_deploy(service, version=version, status=status, note=note))
    except RuntimeError:
        # Already inside a running loop — schedule instead of asyncio.run.
        try:
            asyncio.ensure_future(report_deploy(service, version=version, status=status, note=note))
        except Exception:
            logger.debug("deploy notify (scheduled) failed", exc_info=True)
    except Exception:
        logger.debug("deploy notify failed", exc_info=True)
