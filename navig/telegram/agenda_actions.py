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

from navig.telegram.card_doc import Bullets, Card, Details, Heading, Para, Raw, Table

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


def _counts(confirmed: int, proposals: int) -> str:
    """The one line under the title: what is booked, and what is waiting on a tap.

    Both numbers, because they answer two different questions — "how full is the
    day" and "is anything stuck" — and neither belongs in the heading.
    """
    if not confirmed and not proposals:
        return ""
    booked = _t("pim.agenda.count_booked", n=confirmed) or f"{confirmed} booked"
    if not proposals:
        return booked
    waiting = _t("pim.agenda.count_waiting", n=proposals) or f"{proposals} waiting"
    return f"{booked} · {waiting}"


def _details(item: dict[str, Any]) -> str:
    """The tail of one row: WHERE, never who.

    A listing row is scanned for time and place; the person matters once you are
    deciding about that one appointment, which is what the detail card is for. Two
    tails per row turned the day groups into a wall of emoji.
    """
    if not item.get("location"):
        return ""
    return f"📍 {html.escape(str(item['location']))}"


def _agenda_keyboard(proposals: list[dict[str, Any]], confirmed: list[dict[str, Any]]) -> dict[str, Any]:
    """The buttons under the agenda — identical for both renders."""
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
    return {"inline_keyboard": rows}


def _agenda_structure(now: datetime) -> tuple[Card, dict[str, Any], str]:
    """The agenda as structure: (card, keyboard, banner). Both renders project this."""
    from navig.pim import service  # noqa: PLC0415
    from navig.telegram.todo_actions import extension_banner  # noqa: PLC0415

    upcoming = service.agenda(start=now - timedelta(hours=2), end=now + timedelta(days=_HORIZON_DAYS))
    proposals = [a for a in upcoming if a.get("origin") in ("ai", "agent")]
    confirmed = [a for a in upcoming if a.get("origin") not in ("ai", "agent")]

    title = _t("pim.agenda.title") or "📅 AGENDA"
    # The counts ride in the meta line, not the title: the title is a section
    # heading now, and "AGENDA · 3" both shouts and answers the wrong question —
    # what waits for an answer is the PROPOSAL count, not how many are booked.
    counts = _counts(len(confirmed), len(proposals))
    card = Card(title, meta=counts)

    if proposals:
        card.add(Heading(_t("pim.agenda.proposals") or "✨ To confirm"))
        card.add(Bullets([
            Raw(f"<code>{html.escape(str(p['when_label']))}</code> {html.escape(str(p['title']))}")
            for p in proposals
        ]))

    if not confirmed and not proposals:
        card.add(Para(
            _t("pim.agenda.empty")
            or "No appointments coming up. Send one with a date and a time — "
            "“Dentist Oct 26 10:30”.",
            style="italic",
        ))

    day: str | None = None
    items: list[str | Raw] = []
    for a in confirmed:
        if a["day"] != day:
            if items:
                card.add(Bullets(items))
                items = []
            day = a["day"]
            card.add(Heading(_day_label(a["day"], now)))
        clock = html.escape(a["due_local"][11:16]) if a.get("due_local") else ""
        body = f"<code>{clock}</code> " if clock else ""
        body += f"<b>{html.escape(str(a['title']))}</b>"
        extra = _details(a)
        if extra:
            body += f" · {extra}"
        items.append(Raw(body))
    if items:
        card.add(Bullets(items))

    return card, _agenda_keyboard(proposals, confirmed), extension_banner().strip()


def agenda_card(now: datetime | None = None) -> tuple[Card, dict[str, Any]]:
    """The agenda as a card — the transport renders it rich or classic."""
    from navig.pim.clock import local_now  # noqa: PLC0415

    now = now or local_now()
    card, keyboard, banner = _agenda_structure(now)
    if banner:
        card.footer = banner
    return card, keyboard


def build_view(now: datetime | None = None) -> tuple[str, dict[str, Any]]:
    """Proposals to confirm, then every upcoming appointment grouped by day."""
    card, keyboard = agenda_card(now)
    return card.classic_html(), keyboard


def build_view_rich(now: datetime | None = None) -> tuple[str, dict[str, Any]]:
    """The same agenda as a rich message — real day headings and a bulleted list."""
    card, keyboard = agenda_card(now)
    return card.rich_html(), keyboard


def _detail_keyboard(item: dict[str, Any]) -> dict[str, Any]:
    from navig.pim.dates import describe_lead  # noqa: PLC0415

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
    return {"inline_keyboard": rows}


def _detail_structure(item: dict[str, Any]) -> tuple[Card, dict[str, Any], str]:
    """One appointment as structure: (card, keyboard, banner)."""
    from navig.pim.dates import describe_lead  # noqa: PLC0415
    from navig.telegram.todo_actions import extension_banner  # noqa: PLC0415

    when = html.escape(str(item["when_label"]))
    if item.get("end_local"):
        when += f" → {html.escape(str(item['end_local'])[11:16])}"
    sheet: list[list[str | Raw]] = [[Raw("🕐"), Raw(when)]]
    if item.get("location"):
        sheet.append([Raw("📍"), Raw(html.escape(str(item["location"])))])
    if item.get("with_whom"):
        sheet.append([Raw("👤"), Raw(html.escape(str(item["with_whom"])))])
    active = [describe_lead(lead).replace(" before", "") for lead in item.get("remind_before") or []]
    sheet.append([
        Raw("🔔"),
        Raw(", ".join(html.escape(lead) for lead in active) or html.escape(_t("pim.detail.no_reminder") or "—")),
    ])

    card = Card(str(item["title"]))
    card.add(Table(rows=sheet, bordered=False, striped=False))
    notes = item.get("notes")
    if notes and notes != item["title"]:
        text = str(notes)
        card.add(Details(
            _t("pim.detail.notes") or "Notes",
            [Para(text[:400] + ("…" if len(text) > 400 else ""))],
        ))
    return card, _detail_keyboard(item), extension_banner().strip()


def detail_card(item_id: str, now: datetime | None = None) -> tuple[Card, dict[str, Any]] | None:
    """One appointment as a card — the transport renders it rich or classic."""
    from navig.pim import service  # noqa: PLC0415
    from navig.pim.clock import local_now  # noqa: PLC0415

    now = now or local_now()
    try:
        item = service.present(service.get(item_id), now)
    except service.NotFound:
        return None
    card, keyboard, banner = _detail_structure(item)
    if banner:
        card.footer = banner
    return card, keyboard


def build_detail(item_id: str, now: datetime | None = None) -> tuple[str, dict[str, Any]] | None:
    made = detail_card(item_id, now)
    return (made[0].classic_html(), made[1]) if made else None


def build_detail_rich(item_id: str, now: datetime | None = None) -> tuple[str, dict[str, Any]] | None:
    made = detail_card(item_id, now)
    return (made[0].rich_html(), made[1]) if made else None


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
    """Open the agenda in the operator's chat — rich where the chat renders it."""
    from navig.telegram import card_transport  # noqa: PLC0415

    card, keyboard = agenda_card()
    await card_transport.send_card(channel, chat_id, card, keyboard=keyboard)


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
