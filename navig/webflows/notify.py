"""Tell the person when a Webflow run is WAITING on them — never pretend to continue.

Phase E. A headless run that needs the human (a secret nobody may fill, a vault login the
person must approve or add, a challenge the flow cannot complete alone) must pause: the run
records its checkpoint as ``needs_user``, exits with its own status, and — when the ordinary
notify fan-out is configured — sends ONE message so the pause is not invisible. The message
follows the Lens-review precedent (``navig.lenses.notify``): a registered type routed in
Settings, dispatched best-effort through ``navig.notify.dispatch``.

A failed push never fails the pause, and the text carries the flow, the reason and how to
resume — never a parameter value.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

NOTIFY_TYPE = "webflow_needs_user"


def needs_user_message(flow_id: str, flow_name: str, reason: str, run_id: str | None = None) -> tuple[str, str]:
    """(title, body) for one paused run. *reason* must already be secret-free."""
    where = f"NAVIG OS → Webflows, or `navig webflow run {flow_id}`"
    if run_id:
        where = f"`navig webflow run {flow_id} --resume {run_id}` (or finish it in the Dock)"
    return (
        "Webflow needs you",
        f"“{flow_name}” paused: {reason}.\n{where}",
    )


async def notify_needs_user(*, flow_id: str, flow_name: str, reason: str, run_id: str | None = None) -> Any:
    """Best-effort pause notification. Returns the dispatch result, or ``None``."""
    try:
        from navig.notify import dispatch

        title, body = needs_user_message(flow_id, flow_name, reason, run_id)
        return await dispatch(NOTIFY_TYPE, title, body, priority="normal",
                              data={"flow": flow_id, "run_id": run_id, "reason": reason})
    except Exception:  # noqa: BLE001 — a push must never fail the pause itself
        logger.debug("webflow needs_user notify failed", exc_info=True)
        return None
