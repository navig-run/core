from __future__ import annotations

import asyncio

from navig.comms.dispatch import send_user_notification
from navig.comms.types import NotificationTarget


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
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_send())
        return

    loop.create_task(_send())
