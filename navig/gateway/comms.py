from __future__ import annotations

import asyncio

from navig.comms.dispatch import send_user_notification
from navig.comms.types import NotificationTarget
from navig.core.background import spawn


def dispatch_message(message: str) -> None:
    """Compatibility shim for legacy gateway comms dispatch API."""

    async def _send() -> None:
        result = await send_user_notification(
            channel="auto",
            target=NotificationTarget.auto("task-bridge"),
            message=message,
        )
        if hasattr(result, "ok") and not result.ok:
            raise RuntimeError(getattr(result, "error", "delivery failed"))
        if hasattr(result, "all_ok") and not result.all_ok:
            raise RuntimeError("fanout delivery failed")

    try:
        asyncio.get_running_loop()  # require a running loop; else run to completion
    except RuntimeError:
        asyncio.run(_send())
        return

    spawn(_send())
