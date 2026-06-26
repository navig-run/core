"""navig.notify — unified cross-channel notification system.

A per-type×channel routing matrix (`prefs`) drives a fan-out `router.dispatch()`
that reuses every existing sender (Telegram/Matrix via NotificationManager,
SMS/Discord/WhatsApp via the messaging adapters, Email via the Gmail connector)
and adds a `deck` channel (bell feed + Inbox + toast) backed by `feed`.

Producers call `navig.notify.dispatch("reminder", title, body, priority=...)`.
"""

from __future__ import annotations

from navig.notify.router import dispatch, get_notification_router

__all__ = ["dispatch", "get_notification_router"]
