"""`/reminders` — every ping the bot is going to send, with its exact time.

There are two ways a ping gets scheduled, and one queue underneath (the `reminders`
table in `store/runtime.py`):

* a TODO with a date writes one row per lead ("3 days before", "1 hour before") plus
  one at the moment itself — `pim.reminders.reschedule`, linked back to its task in
  `board_todo_reminder`;
* `/remindme in 30 min …` writes a one-off row with no task behind it.

The old `/reminders` listed raw rows in English, with no idea which task a row
belonged to — so "what will I be reminded of this week?" meant reading timestamps
and guessing. This view groups every upcoming ping by day, names the task and the
lead, and offers the right action for each kind: a todo's ping opens its task (where
its reminders are switched on and off), a one-off ping can be cancelled here.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "rv:"

#: How many pings the card lists, and how many get a button. Telegram rejects an
#: over-large keyboard outright, so the list is capped rather than left to fail.
_MAX_ROWS = 25
_MAX_BUTTONS = 8
_SHORT = 20


def _t(key: str, **fields: Any) -> str:
    from navig.telegram.todo_actions import _t as todo_t  # noqa: PLC0415

    return todo_t(key, **fields)


def _runtime() -> Any:
    from navig.store.runtime import get_runtime_store  # noqa: PLC0415

    return get_runtime_store()


def _board() -> Any:
    from navig.store.board import get_board_store  # noqa: PLC0415

    return get_board_store()


def _short(text: str) -> str:
    return text if len(text) <= _SHORT else text[: _SHORT - 1] + "…"


def upcoming(user_id: int, now: datetime) -> list[dict[str, Any]]:
    """Every pending ping for this user, soonest first, each annotated with its task.

    Returns dicts with ``id``, ``at`` (aware, local), ``title``, ``lead`` and
    ``card_id`` (None for a one-off `/remindme`).
    """
    from navig.pim.clock import from_utc_iso, to_local  # noqa: PLC0415

    rows = _runtime().get_user_reminders(int(user_id), limit=_MAX_ROWS)
    links: dict[int, dict[str, Any]] = {}
    try:
        links = _board().reminder_links([int(r["id"]) for r in rows])
    except Exception:  # noqa: BLE001 — without links every row still shows, as a one-off
        logger.exception("reminders view: could not read todo links")

    out: list[dict[str, Any]] = []
    for row in rows:
        at = to_local(from_utc_iso(str(row.get("remind_at") or "")))
        if at is None:
            continue
        rid = int(row["id"])
        link = links.get(rid)
        message = str(row.get("message") or "").strip().removeprefix("⏰").strip()
        title = message
        if link:
            title = str(link.get("title") or "") or message
        out.append(
            {
                "id": rid,
                "at": at,
                "title": title,
                "lead": (link or {}).get("lead") or "",
                "card_id": (link or {}).get("card_id"),
            }
        )
    out.sort(key=lambda r: r["at"])
    return out


def _day_label(at: datetime, now: datetime) -> str:
    from navig.pim.dates import format_local  # noqa: PLC0415

    if at.date() == now.date():
        return _t("pim.quick.today") or "Today"
    if at.date() == (now + timedelta(days=1)).date():
        return _t("pim.quick.tmrw") or "Tomorrow"
    return format_local(at.replace(hour=0, minute=0, second=0, microsecond=0), now)


def build(user_id: int, now: datetime | None = None) -> tuple[str, dict[str, Any]]:
    """The card and its keyboard. Pure apart from the two store reads."""
    from navig.pim.clock import local_now  # noqa: PLC0415
    from navig.pim.dates import describe_lead  # noqa: PLC0415
    from navig.telegram.todo_actions import extension_banner  # noqa: PLC0415

    now = now or local_now()
    pings = upcoming(user_id, now)

    title = _t("pim.rv.title") or "🔔 REMINDERS"
    lines = [f"<b>{title}</b> · {len(pings)}", ""]
    if not pings:
        lines.append(
            "<i>"
            + (
                _t("pim.rv.empty")
                or "Nothing scheduled. Send an appointment with a date and a time, "
                "or /remindme in 30 min &lt;text&gt;."
            )
            + "</i>"
        )

    day = None
    for ping in pings:
        label = _day_label(ping["at"], now)
        if label != day:
            if day is not None:
                lines.append("")
            lines.append(f"<b>{html.escape(label)}</b>")
            day = label
        what = html.escape(ping["title"])
        if ping["lead"]:
            what += f" <i>— {html.escape(describe_lead(ping['lead']))}</i>"
        mark = "📋" if ping["card_id"] else "⏰"
        lines.append(f"{mark} <code>{ping['at']:%H:%M}</code> {what}")

    if pings:
        lines.append("")
        lines.append(
            "<i>"
            + (_t("pim.rv.hint") or "📋 opens the task · ✕ cancels a one-off reminder")
            + "</i>"
        )

    rows: list[list[dict[str, str]]] = []
    seen_cards: set[str] = set()
    for ping in pings:
        if len(rows) >= _MAX_BUTTONS:
            break
        when = f"{ping['at']:%d.%m %H:%M}"
        if ping["card_id"]:
            # One button per TASK: a todo with four pings is still one thing to open.
            if ping["card_id"] in seen_cards:
                continue
            seen_cards.add(ping["card_id"])
            rows.append([{
                "text": f"📋 {when} {_short(ping['title'])}",
                "callback_data": f"td:o:{ping['card_id']}",
            }])
        else:
            rows.append([{
                "text": f"✕ {when} {_short(ping['title'])}",
                "callback_data": f"{CALLBACK_PREFIX}x:{ping['id']}",
            }])

    rows.append([
        {"text": f"🔄 {_t('pim.rv.refresh') or 'Refresh'}", "callback_data": f"{CALLBACK_PREFIX}v"},
        {"text": f"📋 {_t('pim.rv.tasks') or 'Tasks'}", "callback_data": "td:v:all"},
    ])
    return extension_banner() + "\n".join(lines), {"inline_keyboard": rows}


async def handle_callback(
    channel: Any, cb_data: str, chat_id: int, message_id: int, user_id: int | None = None
) -> str:
    """Apply one button press and re-render. Returns the toast. Never raises."""
    body = cb_data[len(CALLBACK_PREFIX):]
    parts = body.split(":")
    action = parts[0] if parts else ""
    owner = int(user_id if user_id is not None else chat_id)
    toast = ""
    try:
        if action == "x" and len(parts) >= 2 and parts[1].isdigit():
            rid = int(parts[1])
            if _board().reminder_is_todo(rid):
                # A todo's pings are owned by its task: cancelling one here would come
                # back on the next edit. Its card is where they are switched off.
                return _t("pim.rv.owned") or "That one belongs to a task — open the task"
            ok = _runtime().cancel_reminder(rid, owner)
            toast = (_t("pim.rv.cancelled") or "Cancelled") if ok else (
                _t("pim.toast.gone") or "Already gone"
            )
        text, keyboard = build(owner)
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
    except Exception as exc:  # noqa: BLE001
        if "not modified" in str(exc).lower():
            return toast
        logger.exception("reminders callback %r failed", cb_data)
        return _t("pim.toast.error") or "Something went wrong"
    return toast


async def send(channel: Any, chat_id: int, user_id: int) -> None:
    text, keyboard = build(user_id)
    await channel._api_call(
        "sendMessage",
        {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "reply_markup": keyboard},
    )
