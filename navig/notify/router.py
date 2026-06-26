"""NotificationRouter — the fan-out dispatcher.

A single `dispatch(type, title, body, ...)` reads the per-type×channel matrix
and sends to every enabled channel using the senders that already exist:

    deck     → feed store + system_events.emit("notification")  (bell/Inbox/toast)
    telegram → NotificationManager telegram channel
    matrix   → NotificationManager matrix channel
    sms      → messaging SMS adapter
    discord  → messaging Discord adapter
    whatsapp → messaging WhatsApp Cloud adapter
    email    → Gmail connector (gmail.send)

Master toggle + quiet-hours gating are applied first. Every channel send is
best-effort and isolated — one failing channel never blocks the others. Returns
a per-channel summary so the UI / test button can show what happened.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from navig.notify import feed, prefs
from navig.notify.types import emoji_for_type

logger = logging.getLogger("navig.notify")

# channel key → messaging adapter registry name(s) to try
_ADAPTER_NAMES = {
    "sms": ["sms"],
    "discord": ["discord"],
    "whatsapp": ["whatsapp_cloud", "whatsapp"],
}


def _priority_enum(priority: str):
    from navig.gateway.notifications import NotificationPriority

    return {
        "low": NotificationPriority.LOW,
        "normal": NotificationPriority.NORMAL,
        "high": NotificationPriority.HIGH,
        "critical": NotificationPriority.CRITICAL,
    }.get(priority, NotificationPriority.NORMAL)


def _in_quiet_hours(hour: int, start: int, end: int) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # wraps midnight


class NotificationRouter:
    _instance: "NotificationRouter | None" = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._gateway = None
        return cls._instance

    def bind_gateway(self, gateway) -> None:
        """Give the router the gateway handle (for SSE emit). Called at boot."""
        self._gateway = gateway

    # ── Dispatch ─────────────────────────────────────────────────────────────

    async def dispatch(
        self,
        type_key: str,
        title: str,
        body: str = "",
        *,
        priority: str = "normal",
        data: dict[str, Any] | None = None,
        only_channels: list[str] | None = None,
    ) -> dict[str, Any]:
        settings = prefs.get_settings()
        if not settings["master_enabled"]:
            return {"type": type_key, "skipped": "master_off", "channels": []}

        channels = prefs.enabled_channels(type_key)
        if only_channels is not None:
            channels = [c for c in channels if c in only_channels]

        # Quiet hours: keep the silent in-app deck channel, mute the noisy
        # outbound channels for anything below CRITICAL.
        if (
            settings["quiet_hours_enabled"]
            and priority != "critical"
            and _in_quiet_hours(datetime.now().hour, settings["quiet_hours_start"], settings["quiet_hours_end"])
        ):
            channels = [c for c in channels if c == "deck"]

        text = body or ""
        results: list[dict[str, Any]] = []
        for ch in channels:
            try:
                ok, detail = await self._send_one(ch, type_key, title, text, priority, data or {})
            except Exception as exc:  # noqa: BLE001 — isolate per-channel failures
                logger.debug("notify channel %s failed: %s", ch, exc)
                ok, detail = False, str(exc)
            results.append({"channel": ch, "ok": ok, "detail": detail})

        return {"type": type_key, "priority": priority, "channels": results}

    async def _send_one(
        self, channel: str, type_key: str, title: str, body: str, priority: str, data: dict
    ) -> tuple[bool, str]:
        if channel == "deck":
            item = feed.append(type_key, title, body, priority=priority, data=data)
            if self._gateway is not None and getattr(self._gateway, "system_events", None):
                try:
                    await self._gateway.system_events.emit("notification", item)
                except Exception:  # noqa: BLE001
                    logger.debug("notification SSE emit failed", exc_info=True)
            return True, item["id"]

        if channel in ("telegram", "matrix"):
            from navig.gateway.notifications import get_notification_manager

            nm = get_notification_manager()
            ch_obj = nm.get_channel(channel) if hasattr(nm, "get_channel") else nm._channels.get(channel)
            if ch_obj is None:
                return False, f"{channel} not configured"
            await ch_obj.send_alert(title, body, _priority_enum(priority))
            return True, "sent"

        if channel == "email":
            from navig.notify.email import send_email

            to = prefs.get_target("email")
            return await send_email(to, title, body)

        if channel in _ADAPTER_NAMES:
            from navig.messaging.adapter_registry import get_adapter_registry

            target = prefs.get_target(channel)
            if not target:
                return False, "no target configured"
            reg = get_adapter_registry()
            adapter = next((reg.get(n) for n in _ADAPTER_NAMES[channel] if reg.get(n)), None)
            if adapter is None:
                return False, f"{channel} adapter not enabled"
            msg = f"{emoji_for_type(type_key)} {title}" + (f"\n\n{body}" if body else "")
            receipt = await adapter.send_message(target, msg)
            status = getattr(getattr(receipt, "status", None), "value", "sent")
            err = getattr(receipt, "error", None)
            return (not err), (err or status)

        return False, "unknown channel"


_router: NotificationRouter | None = None


def get_notification_router() -> NotificationRouter:
    global _router
    if _router is None:
        _router = NotificationRouter()
    return _router


async def dispatch(type_key: str, title: str, body: str = "", **kwargs) -> dict[str, Any]:
    """Module-level convenience used by producers."""
    return await get_notification_router().dispatch(type_key, title, body, **kwargs)
