"""
Unified notification dispatcher.

Routes ``send_user_notification()`` to the correct channel backend.
Reads ``comms.default_notification_channel`` from NAVIG config to decide
which channel to use when the caller passes ``channel="auto"``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, Union

from navig.comms.types import (
    CommsChannel,
    DeliveryPriority,
    DeliveryResult,
    FanoutResult,
    NotificationOptions,
    NotificationTarget,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global registry — populated by gateway startup or tests
# ---------------------------------------------------------------------------

_telegram_notifier = None  # TelegramNotifier | None
_matrix_notifier = None  # NavigMatrixBot | None   (Prompt 4)
_default_channel: CommsChannel = "telegram"


def configure(
    *,
    telegram_notifier=None,
    matrix_notifier=None,
    default_channel: CommsChannel = "telegram",
) -> None:
    """Wire concrete notifiers at gateway boot time."""
    global _telegram_notifier, _matrix_notifier, _default_channel
    _telegram_notifier = telegram_notifier
    _matrix_notifier = matrix_notifier
    _default_channel = default_channel
    logger.info(
        "Comms configured: default=%s telegram=%s matrix=%s",
        default_channel,
        "yes" if telegram_notifier else "no",
        "yes" if matrix_notifier else "no",
    )


def get_default_channel() -> CommsChannel:
    """Return the configured default channel."""
    return _default_channel


# ---------------------------------------------------------------------------
# Public dispatch function
# ---------------------------------------------------------------------------


async def send_user_notification(
    channel: CommsChannel,
    target: NotificationTarget,
    message: str,
    options: Optional[NotificationOptions] = None,
) -> Union[DeliveryResult, FanoutResult]:
    """Send a notification via the selected channel(s).

    Parameters
    ----------
    channel : CommsChannel
        One of "telegram", "matrix", "both", "none", "auto".
    target : NotificationTarget
        Concrete IDs (telegram_chat_id, matrix_room_id) or a user_id for auto.
    message : str
        The message body (Markdown supported).
    options : NotificationOptions, optional
        Priority, silent flag, etc.

    Returns
    -------
    DeliveryResult or FanoutResult
    """
    options = options or NotificationOptions()

    # Resolve "auto"
    resolved = _resolve_channel(channel, target)

    if resolved == "none":
        return DeliveryResult.success(channel="none", message_id="noop")

    if resolved == "both":
        return await _fanout(target, message, options)

    if resolved == "telegram":
        return await _send_telegram(target, message, options)

    if resolved == "matrix":
        return await _send_matrix(target, message, options)

    return DeliveryResult.failure(
        channel=resolved, error=f"Unknown channel: {resolved}"
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_channel(channel: CommsChannel, target: NotificationTarget) -> str:
    """Resolve "auto" to a concrete channel name."""
    if channel != "auto":
        return channel

    # If the identity module is available, try user preference
    if target.user_id:
        try:
            from navig.identity.store import get_user_preferred_channel

            return get_user_preferred_channel(target.user_id) or _default_channel
        except ImportError:
            pass  # optional dependency not installed; feature disabled

    return _default_channel


async def _send_telegram(
    target: NotificationTarget,
    message: str,
    options: NotificationOptions,
) -> DeliveryResult:
    """Deliver via TelegramNotifier."""
    if not _telegram_notifier:
        return DeliveryResult.failure("telegram", "Telegram notifier not configured")

    chat_id = target.telegram_chat_id
    if not chat_id:
        return DeliveryResult.failure("telegram", "No telegram_chat_id in target")

    try:
        from navig.gateway.notifications import Notification, NotificationPriority

        # Map DeliveryPriority → NotificationPriority
        prio_map = {
            DeliveryPriority.LOW: NotificationPriority.LOW,
            DeliveryPriority.NORMAL: NotificationPriority.NORMAL,
            DeliveryPriority.HIGH: NotificationPriority.HIGH,
            DeliveryPriority.CRITICAL: NotificationPriority.CRITICAL,
        }

        notif = Notification(
            type="alert",
            title="",
            message=message,
            priority=prio_map.get(options.priority, NotificationPriority.NORMAL),
        )
        await _telegram_notifier.send(notif)
        return DeliveryResult.success("telegram")
    except Exception as exc:
        logger.exception("Telegram send failed")
        return DeliveryResult.failure("telegram", str(exc))


async def _send_matrix(
    target: NotificationTarget,
    message: str,
    options: NotificationOptions,
) -> DeliveryResult:
    """Deliver via Matrix.  Placeholder until Prompt 4 is implemented."""
    if not _matrix_notifier:
        return DeliveryResult.failure(
            "matrix", "Matrix notifier not configured (install matrix module)"
        )

    room_id = target.matrix_room_id
    if not room_id:
        return DeliveryResult.failure("matrix", "No matrix_room_id in target")

    try:
        await _matrix_notifier.send_message(room_id, message)
        return DeliveryResult.success("matrix")
    except Exception as exc:
        logger.exception("Matrix send failed")
        return DeliveryResult.failure("matrix", str(exc))


async def _fanout(
    target: NotificationTarget,
    message: str,
    options: NotificationOptions,
) -> FanoutResult:
    """Send to all available channels in parallel."""
    tasks = []
    if _telegram_notifier and target.telegram_chat_id:
        tasks.append(_send_telegram(target, message, options))
    if _matrix_notifier and target.matrix_room_id:
        tasks.append(_send_matrix(target, message, options))

    if not tasks:
        return FanoutResult(
            results=[DeliveryResult.failure("both", "No channels available for fanout")]
        )

    results = await asyncio.gather(*tasks, return_exceptions=True)
    delivery_results = []
    for r in results:
        if isinstance(r, DeliveryResult):
            delivery_results.append(r)
        elif isinstance(r, Exception):
            delivery_results.append(DeliveryResult.failure("unknown", str(r)))
    return FanoutResult(results=delivery_results)
