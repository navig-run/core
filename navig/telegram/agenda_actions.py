"""`/agenda` — appointments, by day. Never mixed into the to-do list.

An appointment is something to ATTEND (a start time, maybe a place, someone), a task
is something to DO. They used to share one list under a "rendezvous" category, so a
dentist visit sat between "buy paper" and "renew the domain" and ticking it "done"
made no sense. Here they are their own view, with their own buttons.

Callback prefix ``ag:`` (owned by the `todo` extension):

    ag:v            the agenda
    ag:o:<id>       one appointment
    ag:r:<id>:<ld>  switch one reminder lead on/off (3d / 1d / 1h)
    ag:x:<id>       cancel (delete) it and its reminders
    ag:ok:<id>      accept a proposal — its reminders begin
    ag:no:<id>      dismiss a proposal — it is never proposed again
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "ag:"

#: How far ahead the agenda looks, and how many get their own button.
_HORIZON_DAYS = 60
_MAX_BUTTONS = 8
_SHORT = 24
LEADS: tuple[str, ...] = ("3d", "1d", "1h")


def _t(key: str, **fields: Any) -> str:
    from navig.telegram.todo_actions import _t as todo_t  # noqa: PLC0415

    return todo_t(key, **fields)


def _short(text: str) -> str:
    return text if len(text) <= _SHORT else text[: _SHORT - 1] + "…"


def _day_label(day_iso: str, now: datetime) -> str:
    from datetime import date  # noqa: PLC0415

    from navig.pim.dates import format_local  # noqa: PLC0415

    day = date.fromisoformat(day_iso)
    if day == now.date():
        return _t("pim.quick.today") or "Today"
    if day == (now + timedelta(days=1)).date():
        return _t("pim.quick.tmrw") or "Tomorrow"
    as_dt = now.replace(year=day.year, month=day.month, day=day.day, hour=0, minute=0, second=0, microsecond=0)
    return format_local(as_dt, now)


def _details(item: dict[str, Any]) -> str:
    bits = []
    if item.get("location"):
        bits.append(f"📍 {html.escape(str(item['location']))}")
    if item.get("with_whom"):
        bits.append(f"👤 {html.escape(str(item['with_whom']))}")
    return " · ".join(bits)


def build_view(now: datetime | None = None) -> tuple[str, dict[str, Any]]:
    """Proposals to confirm, then every upcoming appointment grouped by day."""
    from navig.pim import service  # noqa: PLC0415
    from navig.pim.clock import local_now  # noqa: PLC0415
    from navig.telegram.todo_actions import extension_banner  # noqa: PLC0415

    now = now or local_now()
    upcoming = service.agenda(start=now - timedelta(hours=2), end=now + timedelta(days=_HORIZON_DAYS))
    proposals = [a for a in upcoming if a.get("origin") in ("ai", "agent")]
    confirmed = [a for a in upcoming if a.get("origin") not in ("ai", "agent")]

    title = _t("pim.agenda.title") or "📅 AGENDA"
    lines = [f"<b>{title}</b> · {len(confirmed)}", ""]

    if proposals:
        lines.append(f"<b>{_t('pim.agenda.proposals') or '✨ To confirm'}</b>")
        for p in proposals:
            lines.append(f"• <code>{html.escape(p['when_label'])}</code> {html.escape(p['title'])}")
        lines.append("")

    if not confirmed and not proposals:
        lines.append(
            "<i>"
            + (
                _t("pim.agenda.empty")
                or "No appointments coming up. Send one with a date and a time — "
                "“Dentist Oct 26 10:30”."
            )
            + "</i>"
        )

    day = None
    for a in confirmed:
        if a["day"] != day:
            if day is not None:
                lines.append("")
            lines.append(f"<b>{html.escape(_day_label(a['day'], now))}</b>")
            day = a["day"]
        clock = a["due_local"][11:16] if a.get("due_local") else ""
        line = f"• <code>{clock}</code> {html.escape(a['title'])}"
        extra = _details(a)
        if extra:
            line += f"\n   {extra}"
        lines.append(line)

    rows: list[list[dict[str, str]]] = []
    for p in proposals[:3]:
        rows.append([
            {"text": f"✓ {_short(p['title'])}", "callback_data": f"{CALLBACK_PREFIX}ok:{p['id']}"},
            {"text": "✕", "callback_data": f"{CALLBACK_PREFIX}no:{p['id']}"},
        ])
    for a in confirmed[:_MAX_BUTTONS]:
        clock = a["due_local"][5:16].replace("T", " ") if a.get("due_local") else ""
        when = f"{clock[3:5]}.{clock[0:2]} {clock[6:]}" if clock else ""
        rows.append([{
            "text": f"📅 {when} {_short(a['title'])}",
            "callback_data": f"{CALLBACK_PREFIX}o:{a['id']}",
        }])
    rows.append([
        {"text": f"📋 {_t('pim.rv.tasks') or 'Tasks'}", "callback_data": "td:v:all"},
        {"text": f"🔔 {_t('pim.nav.reminders') or 'Reminders'}", "callback_data": "rv:v"},
    ])
    return extension_banner() + "\n".join(lines), {"inline_keyboard": rows}


def build_detail(item_id: str, now: datetime | None = None) -> tuple[str, dict[str, Any]] | None:
    from navig.pim import service  # noqa: PLC0415
    from navig.pim.clock import local_now  # noqa: PLC0415
    from navig.pim.dates import describe_lead  # noqa: PLC0415

    now = now or local_now()
    try:
        item = service.present(service.get(item_id), now)
    except service.NotFound:
        return None
    lines = [f"📅 <b>{html.escape(item['title'])}</b>", f"🕐 {html.escape(item['when_label'])}"]
    if item.get("end_local"):
        lines[-1] += f" → {item['end_local'][11:16]}"
    extra = _details(item)
    if extra:
        lines.append(extra)
    if item.get("notes") and item["notes"] != item["title"]:
        notes = str(item["notes"])
        lines.append(f"<i>{html.escape(notes[:400] + ('…' if len(notes) > 400 else ''))}</i>")
    active = set(item.get("remind_before") or [])
    rows = [
        [
            {
                "text": ("🔔 " if lead in active else "🔕 ") + describe_lead(lead).replace(" before", ""),
                "callback_data": f"{CALLBACK_PREFIX}r:{item['id']}:{lead}",
            }
            for lead in LEADS
        ],
    ]
    if item.get("origin") in ("ai", "agent"):
        rows.append([
            {"text": f"✓ {_t('pim.appt.save') or 'Save'}", "callback_data": f"{CALLBACK_PREFIX}ok:{item['id']}"},
            {"text": f"✕ {_t('pim.appt.dismiss') or 'No'}", "callback_data": f"{CALLBACK_PREFIX}no:{item['id']}"},
        ])
    else:
        rows.append([
            {"text": f"🗑 {_t('pim.agenda.cancel') or 'Cancel appointment'}", "callback_data": f"{CALLBACK_PREFIX}x:{item['id']}"},
        ])
    rows.append([{"text": f"‹ {_t('pim.agenda.back') or 'Agenda'}", "callback_data": f"{CALLBACK_PREFIX}v"}])
    return "\n".join(lines), {"inline_keyboard": rows}


async def _edit(channel: Any, chat_id: int, message_id: int, text: str, keyboard: dict[str, Any]) -> None:
    await channel._api_call(
        "editMessageText",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": keyboard,
        },
    )


async def send(channel: Any, chat_id: int) -> None:
    text, keyboard = build_view()
    await channel._api_call(
        "sendMessage",
        {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "reply_markup": keyboard},
    )


async def handle_callback(
    channel: Any, cb_data: str, chat_id: int, message_id: int, user_id: int | None = None
) -> str:
    """Apply one press and re-render. Returns the toast. Never raises."""
    from navig.pim import service  # noqa: PLC0415

    parts = cb_data[len(CALLBACK_PREFIX):].split(":")
    action = parts[0] if parts else ""
    item_id = parts[1] if len(parts) >= 2 else ""
    gone = _t("pim.toast.gone") or "That one is gone"
    try:
        if action == "v":
            await _edit(channel, chat_id, message_id, *build_view())
            return ""
        if action == "o":
            rendered = build_detail(item_id)
            if rendered is None:
                return gone
            await _edit(channel, chat_id, message_id, *rendered)
            return ""
        if action == "r" and len(parts) >= 3:
            item = service.get(item_id)
            lead = parts[2]
            leads = list(item.get("remind_before") or [])
            on = lead not in leads
            service.update(item_id, {"remind_before": [*leads, lead] if on else [x for x in leads if x != lead]})
            rendered = build_detail(item_id)
            if rendered:
                await _edit(channel, chat_id, message_id, *rendered)
            from navig.pim.dates import describe_lead  # noqa: PLC0415

            key = "pim.toast.lead_on" if on else "pim.toast.lead_off"
            return _t(key, lead=describe_lead(lead)) or ("On" if on else "Off")
        if action == "x":
            service.delete(item_id)
            await _edit(channel, chat_id, message_id, *build_view())
            return _t("pim.agenda.cancelled") or "Cancelled"
        if action == "ok":
            service.accept(item_id)
            await _edit(channel, chat_id, message_id, *build_view())
            return _t("pim.agenda.accepted") or "Added — reminders on"
        if action == "no":
            service.delete(item_id)  # records the dismissal: never proposed again
            await _edit(channel, chat_id, message_id, *build_view())
            return _t("pim.toast.dismissed") or "Dismissed"
    except service.NotFound:
        return gone
    except Exception as exc:  # noqa: BLE001 — a raise here leaves the card stale
        if "not modified" in str(exc).lower():
            return ""
        logger.exception("agenda callback %r failed", cb_data)
        return _t("pim.toast.error") or "Something went wrong"
    return ""
