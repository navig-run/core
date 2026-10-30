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

#: The hub's prefix: one button on this card opens the menu in the same message.
MENU_PREFIX = "hm:"

#: How many pings the card lists, and how many get a button. Telegram rejects an
#: over-large keyboard outright, so the list is capped rather than left to fail.
_MAX_ROWS = 25
_MAX_BUTTONS = 8
_SHORT = 20
#: A body row is edited in place, and an edit past Telegram's 4096-unit limit
#: fails outright — the title is cut so one long appointment cannot stale the card.
_TITLE = 80

#: Entries per page. A list longer than this pages in place (‹ / ›) instead of
#: growing into a wall the operator has to scroll past every morning.
PAGE_SIZE = 6


def _t(key: str, **fields: Any) -> str:
    from navig.telegram.todo_actions import _t as todo_t  # noqa: PLC0415

    return todo_t(key, **fields)


def _runtime() -> Any:
    from navig.store.runtime import get_runtime_store  # noqa: PLC0415

    return get_runtime_store()


def _board() -> Any:
    from navig.store.board import get_board_store  # noqa: PLC0415

    return get_board_store()


def _short(text: str, limit: int = _SHORT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


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


def _lines(pings: list[dict[str, Any]], now: datetime) -> list[tuple[str, Any, dict[str, Any]]]:
    """One entry per thing to show — ``(day label, html row, ping)``, duplicates folded.

    A ping's title can be a whole appointment: "Стоматолог — за 1 день (Пн 26 окт ·
    14:00)" then the clinic, the doctor, what to bring, the file. Bolding all of it
    produced one enormous bold paragraph — the operator's "not organised" verdict on
    2026-10-26. The first line names the thing; the rest is the detail behind it.
    """
    from navig.pim.dates import describe_lead  # noqa: PLC0415
    from navig.telegram.card_doc import Raw  # noqa: PLC0415

    out: list[tuple[str, Any, dict[str, Any]]] = []
    shown: set[tuple[str, str]] = set()
    for ping in pings:
        headline, _, detail = str(ping["title"]).partition("\n")
        headline = headline.strip() or "—"
        stamp = f"{ping['at']:%Y-%m-%d %H:%M}"
        if (stamp, headline) in shown:  # two rows for one appointment is one line
            continue
        shown.add((stamp, headline))
        what = f"<b>{html.escape(_short(headline, _TITLE))}</b>"
        if ping["lead"]:
            what += f" <i>— {html.escape(describe_lead(ping['lead']))}</i>"
        detail = " · ".join(line.strip() for line in detail.splitlines() if line.strip())
        if detail:
            what += f" <i>· {html.escape(detail)}</i>"
        mark = "📋" if ping["card_id"] else "⏰"
        row = Raw(f"{mark} <code>{ping['at']:%H:%M}</code> {what}")
        out.append((_day_label(ping["at"], now), row, ping))
    return out


def reminder_list_card(
    user_id: int, now: datetime | None = None, *, page: int = 0
) -> tuple[Any, dict[str, Any]]:
    """The pings as a card. Both renders project this, so the two never disagree.

    A long list pages in place: the same message is edited to the next page, so the
    chat keeps one reminders card instead of a scroll. ``page`` is zero-based and
    clamped, which is what the ``‹``/``›`` buttons carry in their callback data.
    """
    from navig.pim.clock import local_now  # noqa: PLC0415
    from navig.telegram.card_doc import Bullets, Card, Heading, Para  # noqa: PLC0415
    from navig.telegram.todo_actions import extension_banner  # noqa: PLC0415

    now = now or local_now()
    pings = upcoming(user_id, now)
    entries = _lines(pings, now)
    pages = max(1, -(-len(entries) // PAGE_SIZE))
    page = min(max(int(page), 0), pages - 1)
    visible = entries[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    title = _t("pim.rv.title") or "🔔 REMINDERS"
    card = Card(f"{title} · {len(pings)}")
    if not pings:
        card.add(
            Para(
                _t("pim.rv.empty")
                or "Nothing scheduled. Send an appointment with a date and a time, "
                "or /remindme in 30 min &lt;text&gt;.",
                style="italic",
            )
        )

    day = None
    items: list[str | Any] = []
    for label, row, _ping in visible:
        if label != day:
            if items:
                card.add(Bullets(items))
                items = []
            day = label
            card.add(Heading(label))
        items.append(row)
    if items:
        card.add(Bullets(items))

    if pings:
        card.add(
            Para(
                _t("pim.rv.hint") or "📋 opens the task · ✕ cancels a one-off reminder",
                style="italic",
            )
        )

    rows: list[list[dict[str, str]]] = []
    seen_cards: set[str] = set()
    seen_buttons: set[tuple[str, str]] = set()
    for _label, _row, ping in visible:
        if len(rows) >= _MAX_BUTTONS:
            break
        when = f"{ping['at']:%d.%m %H:%M}"
        label = _short(str(ping["title"]).partition("\n")[0].strip())
        if (when, label) in seen_buttons:  # one button per appointment, not per row
            continue
        if ping["card_id"]:
            # One button per TASK: a todo with four pings is still one thing to open.
            if ping["card_id"] in seen_cards:
                continue
            seen_cards.add(ping["card_id"])
            seen_buttons.add((when, label))
            rows.append(
                [
                    {
                        "text": f"📋 {when} {label}",
                        "callback_data": f"td:o:{ping['card_id']}",
                    }
                ]
            )
        else:
            seen_buttons.add((when, label))
            # The page rides along only when there is more than one, so a short list
            # keeps the payloads (and the history of this card) unchanged.
            target = f"{CALLBACK_PREFIX}x:{ping['id']}"
            if pages > 1:
                target += f":{page}"
            rows.append([{"text": f"✕ {when} {label}", "callback_data": target}])

    if pages > 1:
        nav: list[dict[str, str]] = []
        if page > 0:
            nav.append({"text": "‹", "callback_data": f"{CALLBACK_PREFIX}p:{page - 1}"})
        nav.append({"text": f"{page + 1}/{pages}", "callback_data": f"{CALLBACK_PREFIX}noop"})
        if page + 1 < pages:
            nav.append({"text": "›", "callback_data": f"{CALLBACK_PREFIX}p:{page + 1}"})
        rows.append(nav)

    rows.append(
        [
            {
                "text": f"🔄 {_t('pim.rv.refresh') or 'Refresh'}",
                "callback_data": f"{CALLBACK_PREFIX}v",
            },
            {"text": f"📋 {_t('pim.rv.tasks') or 'Tasks'}", "callback_data": "td:v:all"},
            {"text": "🏠", "callback_data": f"{MENU_PREFIX}home"},
        ]
    )
    banner = extension_banner().strip()
    if banner:
        card.footer = banner
    return card, {"inline_keyboard": rows}


def build(user_id: int, now: datetime | None = None) -> tuple[str, dict[str, Any]]:
    """The card and its keyboard. Pure apart from the two store reads."""
    card, keyboard = reminder_list_card(user_id, now)
    return card.classic_html(), keyboard


def build_rich(user_id: int, now: datetime | None = None) -> tuple[str, dict[str, Any]]:
    """The same list as a rich message — day headings instead of blank lines."""
    card, keyboard = reminder_list_card(user_id, now)
    return card.rich_html(), keyboard


async def _edit_in_place(
    channel: Any, chat_id: int, message_id: int, card: Any, keyboard: dict[str, Any]
) -> None:
    """Redraw the tapped message, rich when the channel can, classic when it cannot.

    A press used to *downgrade* the card: the list was re-rendered as the classic HTML
    and edited with ``editMessageText``, so day headings and the live countdown vanished
    on the first tap. The rich edit keeps the card the operator already has.
    """
    from navig.telegram import card_transport  # noqa: PLC0415

    rows = keyboard.get("inline_keyboard") if keyboard else None
    await card_transport.edit_card(channel, chat_id, message_id, card, keyboard=rows)


async def handle_callback(
    channel: Any, cb_data: str, chat_id: int, message_id: int, user_id: int | None = None
) -> str:
    """Apply one button press and re-render. Returns the toast. Never raises."""
    body = cb_data[len(CALLBACK_PREFIX) :]
    parts = body.split(":")
    action = parts[0] if parts else ""
    owner = int(user_id if user_id is not None else chat_id)
    toast = ""
    page = 0
    try:
        if action == "noop":  # the page counter, which is a label rather than a button
            return ""
        if action == "p" and len(parts) >= 2 and parts[1].isdigit():
            page = int(parts[1])
        if action == "x" and len(parts) >= 2 and parts[1].isdigit():
            rid = int(parts[1])
            if len(parts) >= 3 and parts[2].isdigit():
                page = int(parts[2])  # cancelling must not throw the page away
            if _board().reminder_is_todo(rid):
                # A todo's pings are owned by its task: cancelling one here would come
                # back on the next edit. Its card is where they are switched off.
                return _t("pim.rv.owned") or "That one belongs to a task — open the task"
            ok = _runtime().cancel_reminder(rid, owner)
            toast = (
                (_t("pim.rv.cancelled") or "Cancelled")
                if ok
                else (_t("pim.toast.gone") or "Already gone")
            )
        card, keyboard = reminder_list_card(owner, page=page)
        await _edit_in_place(channel, chat_id, message_id, card, keyboard)
    except Exception as exc:  # noqa: BLE001
        if "not modified" in str(exc).lower():
            return toast
        logger.exception("reminders callback %r failed", cb_data)
        return _t("pim.toast.error") or "Something went wrong"
    return toast


async def send(channel: Any, chat_id: int, user_id: int) -> None:
    from navig.telegram import card_transport  # noqa: PLC0415

    card, keyboard = reminder_list_card(user_id)
    await card_transport.send_card(channel, chat_id, card, keyboard=keyboard)
