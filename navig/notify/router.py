"""NotificationRouter — the fan-out dispatcher.

A single `dispatch(type, title, body, ...)` reads the per-type×channel matrix
and sends to every enabled channel using the senders that already exist:

    deck     → feed store + system_events.emit("notification")  (bell/Inbox/toast)
    telegram → NotificationManager telegram channel
    matrix   → NotificationManager matrix channel
    sms      → messaging SMS adapter
    discord  → messaging Discord adapter
    whatsapp → messaging WhatsApp Cloud adapter
    toast    → Windows desktop toast (local, opt-in per type)
    pushover → Pushover API (emergency = rings on silent, repeats until acknowledged)
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


def _starts_with_symbol(text: str) -> bool:
    """True when *text* already opens with an emoji/pictograph (``💰 New sale``)."""
    import unicodedata

    return bool(text) and unicodedata.category(text[0]) in ("So", "Sk")


def format_telegram_html(type_key: str, title: str, body: str = "") -> str:
    """One notification as Telegram HTML: ``<emoji> <b>title</b>`` + the body as markdown.

    The direct-to-Bot-API path used to send ``<b>escape(title)</b>\\n escape(body)``:
    no glyph, and every bit of markdown a producer wrote (``**bold**``, `` `code` ``,
    bullet lists) arrived as literal asterisks and backticks. This matches
    ``Notification.to_telegram_message`` — escaped bold title, body through the
    shared ``md_to_html`` (which escapes first, so it is safe on any text). A title
    that already starts with its own emoji keeps it instead of getting two.
    """
    from navig.gateway.channels.telegram_html import html_escape, md_to_html

    title = (title or "").strip()
    body_html = md_to_html(body) if body else ""
    if title:
        glyph = "" if _starts_with_symbol(title) else f"{emoji_for_type(type_key)} "
        header = f"{glyph}<b>{html_escape(title)}</b>"
    else:
        header = emoji_for_type(type_key)
    return f"{header}\n\n{body_html}" if body_html else header


def _in_quiet_hours(hour: int, start: int, end: int) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # wraps midnight


def _alert_resolution(type_key: str, title: str, body: str) -> tuple[str, bool]:
    """``(key, already resolved)`` for an alert, or ``("", False)`` for anything else.

    An alert is a condition, and a condition a person has already cleared must not be
    announced again on the monitor's next run — the Resolve button on the card is what
    records that. The key is the alert's own words, so a *different* alert still
    arrives while the same one stays quiet.
    """
    from navig.notify import acks, notify_card

    if not notify_card.resolvable(type_key):
        return "", False
    key = acks.key_for(title, body)
    return key, acks.acknowledged(key)


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
        keyboard: list[list[dict[str, str]]] | None = None,
        dedupe_key: str | None = None,
    ) -> dict[str, Any]:
        """Fan a notification out to its enabled channels.

        ``keyboard`` (inline-button rows) is Telegram-only and optional; every
        other channel ignores it, so a producer can offer a one-tap action without
        caring which channels the operator has on.

        ``dedupe_key`` names the EVENT when the producer knows it (a signed webhook's
        event id). Two genuinely different payments can render identical words — the
        same bag sold twice at $19.99 — so keying those on the text alone would swallow
        the second one. Producers without an identity keep the text key, which is what
        stops five taps on a test button from ringing five times.
        """
        settings = prefs.get_settings()
        if not settings["master_enabled"]:
            return {"type": type_key, "skipped": "master_off", "channels": []}

        channels = prefs.enabled_channels(type_key)

        # Fail loud + fail safe on an UNREGISTERED type. A type nobody registered in
        # navig.notify.types has no matrix rows, so enabled_channels() returns [] and the
        # notification is SILENTLY DROPPED — exactly how the config-incident push (#282)
        # and the business-chat deletion alert both vanished. Deliver it to the always-on
        # deck feed so it is never lost, and warn so the type gets registered.
        #   • Only an UNKNOWN type falls back — a REGISTERED type the user muted everywhere
        #     legitimately resolves to [] and must stay silent (that is their choice).
        #   • Deck-only (never external channels) so a typo can't spam telegram; the WARN
        #     is what makes it loud.
        if not channels and not prefs._is_known_type(type_key):
            logger.warning(
                "notify: dispatched UNREGISTERED type %r — it resolves to no channels and "
                "would be dropped. Falling back to the deck feed; register it in "
                "navig.notify.types.NOTIFICATION_TYPES so it routes properly.",
                type_key,
            )
            channels = ["deck"]

        if only_channels is not None:
            channels = [c for c in channels if c in only_channels]

        # Quiet hours: keep the silent in-app deck channel, mute the noisy
        # outbound channels for anything below CRITICAL — except a Pushover
        # EMERGENCY: the operator chose that type + priority as "must wake me", and
        # quiet hours silencing it would make "never miss" mean "miss at night".
        if (
            settings["quiet_hours_enabled"]
            and priority != "critical"
            and _in_quiet_hours(
                datetime.now().hour, settings["quiet_hours_start"], settings["quiet_hours_end"]
            )
        ):
            keep = {"deck"}
            if "pushover" in channels:
                from navig.notify import pushover

                if pushover.pushover_priority(priority) == 2:
                    keep.add("pushover")
            channels = [c for c in channels if c in keep]

        text = body or ""

        # One event, one arrival — the same wall telegram_routes.deliver raises, one
        # layer up. The matrix is the OTHER way a Telegram card reaches the operator
        # (Signals "Send test", monitor producers, briefings), so a producer that
        # fires twice in a row, or five taps on a test button, must not ring five
        # times for one event. Keyed on the rendered words, so a second sale at a
        # different amount, a re-fired reminder and a new brief all still arrive.
        from navig.notify import dedupe

        collapse = (
            dedupe.payload_key("dispatch", type_key, dedupe_key)
            if dedupe_key
            else dedupe.payload_key("dispatch", type_key, title, text)
        )
        if not dedupe.claim(collapse, dedupe.COLLAPSE_WINDOW_S):
            logger.info(
                "notify %r collapsed — identical words went out within %ss",
                type_key,
                dedupe.COLLAPSE_WINDOW_S,
            )
            return {
                "type": type_key,
                "priority": priority,
                "skipped": "duplicate",
                "channels": [
                    {"channel": ch, "ok": True, "detail": "duplicate collapsed"} for ch in channels
                ],
            }

        results: list[dict[str, Any]] = []
        for ch in channels:
            try:
                ok, detail = await self._send_one(
                    ch,
                    type_key,
                    title,
                    text,
                    priority,
                    data or {},
                    keyboard=keyboard,
                    dedupe_key=dedupe_key,
                )
            except Exception as exc:  # noqa: BLE001 — isolate per-channel failures
                logger.debug("notify channel %s failed: %s", ch, exc)
                ok, detail = False, str(exc)
            results.append({"channel": ch, "ok": ok, "detail": detail})
            # Remember the outcome so a channel that stopped working says so
            # (Notifications → Channels), instead of failing invisibly for weeks.
            # Self-limiting: a write only on a state change, or hourly while healthy.
            from navig.notify import health

            health.record(ch, ok, detail)

        # Nothing reached any channel: hand the claim back so the producer's honest
        # retry is not swallowed by the copy that failed.
        if results and not any(r["ok"] for r in results):
            dedupe.release(collapse)

        return {"type": type_key, "priority": priority, "channels": results}

    async def send_test(self, channel: str) -> dict[str, Any]:
        """Deliver ONE test message to *channel*, bypassing the routing matrix.

        "Send test" proves a channel can deliver; it must not depend on which
        notification types happen to be routed there. It used to go through
        ``dispatch("custom", only_channels=[channel])`` — and ``custom`` routes to the
        deck only by default, so a Telegram test resolved to zero channels and was
        reported as a failure for a perfectly working Telegram. Quiet hours are
        bypassed too (the operator pressed the button); the master switch is not —
        with every notification off, "skipped: master_off" is the honest answer.
        """
        from navig.notify.types import CHANNEL_KEYS

        if channel not in CHANNEL_KEYS:
            raise ValueError(f"unknown channel: {channel}")
        if not prefs.get_settings()["master_enabled"]:
            return {"type": "custom", "skipped": "master_off", "channels": []}
        title = "Test notification"
        body = f"This is a test to {channel} from navig."
        try:
            ok, detail = await self._send_one(
                channel, "custom", title, body, "normal", {"_test": True}
            )
        except Exception as exc:  # noqa: BLE001 — report, never raise into the route
            logger.debug("notify test to %s failed: %s", channel, exc)
            ok, detail = False, str(exc)
        from navig.notify import health

        health.record(channel, ok, detail)
        return {
            "type": "custom",
            "priority": "normal",
            "channels": [{"channel": channel, "ok": ok, "detail": detail}],
        }

    async def _send_one(
        self,
        channel: str,
        type_key: str,
        title: str,
        body: str,
        priority: str,
        data: dict,
        *,
        keyboard: list[list[dict[str, str]]] | None = None,
        dedupe_key: str | None = None,
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
            ch_obj = (
                nm.get_channel(channel) if hasattr(nm, "get_channel") else nm._channels.get(channel)
            )
            if channel == "telegram":
                from navig.notify import telegram_routes

                # No channel object exists outside the gateway (a CLI, a cron subprocess —
                # and, as it turned out, inside the gateway too: configure_telegram() had
                # no caller). The Bot API itself needs none of that. A type with its own
                # chat or sound setting always takes this path too: the channel object
                # only knows the one bound chat.
                if ch_obj is None or telegram_routes.wants_direct_delivery(type_key):
                    import asyncio
                    import contextlib

                    from navig.telegram import chat_tidy

                    text = format_telegram_html(type_key, title, body)
                    # A body already laid out as sections (the daily brief, a
                    # multi-fact report) is worth a card: a table, headings, a real
                    # list. A two-line alert stays one message — forcing it into a
                    # table is noise, not structure. The plain text is the fallback
                    # either way, so a Bot API server without rich messages still
                    # gets the whole notification.
                    from navig.notify import notify_card

                    card = notify_card.build(type_key, title, body)
                    ack_key, resolved = _alert_resolution(type_key, title, body)
                    if resolved:
                        return True, "resolved"
                    # The card is a report; its buttons are the way from the report
                    # to the work it talks about (the task list, the habit week).
                    if keyboard is None:
                        keyboard = notify_card.keyboard(type_key, body, key=ack_key)
                    # Day-bound routine (a sale ring, the daily summary, a briefing)
                    # is recorded for tomorrow's chat tidy; an alert never is (and
                    # the tidy only ever records private chats, so a routed channel
                    # keeps its history). `asyncio.to_thread` runs the send in a COPY
                    # of this context, so the flag set here is what `chat_tidy.note()`
                    # reads in the thread.
                    tidy_kind = chat_tidy.notify_tidy_kind(type_key, priority, data)
                    scope = chat_tidy.routine(tidy_kind) if tidy_kind else contextlib.nullcontext()
                    with scope:
                        return await asyncio.to_thread(
                            telegram_routes.deliver,
                            type_key,
                            text,
                            fallback_chat_id=telegram_routes.default_chat_id(),
                            keyboard=keyboard,
                            card=card,
                            dedupe_key=dedupe_key,
                        )
            if ch_obj is None:
                return False, f"{channel} not configured"
            # Use the real result: send_alert now returns False when an immediate
            # (CRITICAL) send is rejected, so a must-deliver alert that Telegram/
            # Matrix refused is reported as failed instead of a phantom "sent".
            if keyboard and channel == "telegram":
                ok = await ch_obj.send_alert(
                    title, body, _priority_enum(priority), keyboard=keyboard
                )
            else:
                ok = await ch_obj.send_alert(title, body, _priority_enum(priority))
            return bool(ok), ("sent" if ok else f"{channel} rejected the send")

        if channel == "pushover":
            import asyncio

            from navig.notify import pushover

            return await asyncio.to_thread(
                pushover.send_notification, type_key, title, body, priority
            )

        if channel == "toast":
            import asyncio

            from navig.notify import toast

            return await asyncio.to_thread(toast.send_toast, title, body)

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
                # Only the gateway process registers adapters at boot. The Telegram
                # worker (which delivers half the due reminders) and any CLI/cron
                # process start empty — build the adapter here from config.
                from navig.messaging import adapter_setup

                for n in _ADAPTER_NAMES[channel]:
                    ok, why = adapter_setup.ensure_registered(n)
                    if ok:
                        adapter = reg.get(n)
                        break
                if adapter is None:
                    return False, why
            msg = f"{emoji_for_type(type_key)} {title}" + (f"\n\n{body}" if body else "")
            receipt = await adapter.send_message(target, msg)
            if receipt is None:
                # Nothing was delivered, so do not report "sent": every getattr below
                # would fall back to its default and yield a phantom success.
                return False, f"{channel} adapter returned no receipt"
            status = getattr(getattr(receipt, "status", None), "value", "sent")
            err = getattr(receipt, "error", None)
            # DeliveryReceipt carries an explicit `ok` — read it rather than
            # re-deriving success from the absence of an error string. Today every
            # adapter builds receipts via DeliveryReceipt.failure(), which always sets
            # `error`, so the two agree; a receipt constructed as
            # DeliveryReceipt(ok=False, status=FAILED) with no message is perfectly
            # legal and would otherwise be reported as delivered.
            ok = bool(getattr(receipt, "ok", not err))
            return ok, (err or status)

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
