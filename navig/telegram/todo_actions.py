"""The `/todo` card — capture, schedule, remind, tick off, from a phone.

One editable message that switches between views rather than a stream of new ones: a
task list you scroll back through is a task list you lose. Every button edits the card
in place, exactly like `/habit` (`hb:`) and `/extensions` (`xt:`), the two modules this
one is shaped after.

Layout and date grammar live in :mod:`navig.pim` (pure, unit-tested without a bot);
persistence in :mod:`navig.store.board`; reminders in :mod:`navig.pim.reminders`. This
module is the wiring: parse a callback, call one of those, re-render.

**Callback budget.** Telegram caps `callback_data` at 64 bytes. Card ids are 12 hex
chars, so `td:d:<id>` is 17 — but a CATEGORY is free-form text the operator invents and
could be any length in any script, so category buttons carry the category's ORDINAL in
the current list rather than its name. The ordinal is resolved against a freshly-read
list on every tap, so it cannot drift into pointing at the wrong row.

**Everything is gated.** The `todo` extension owns `/todo`, `/t`, `/task` and the `td:`
prefix; switching it off in `/extensions` removes the commands from autocomplete and
makes the buttons answer "switched off" — the behaviour the operator asked for.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from navig.pim.clock import local_now, to_utc_iso
from navig.pim.dates import LEAD_TIMES, RECURRENCES, describe_lead, split_due, split_recurrence
from navig.pim.render import render_agenda, render_category_summary, render_detail

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "td:"

#: Quick-date buttons on the detail card, as (payload slot, label, resolver).
#: Deliberately relative rather than a calendar picker: a calendar is five taps and a
#: scroll on a phone, and "tomorrow" covers most of what a person actually schedules.
_QUICK_SLOTS: tuple[tuple[str, str], ...] = (
    ("today", "Today"),
    ("tmrw", "Tomorrow"),
    ("wknd", "Weekend"),
    ("nextwk", "Next week"),
    ("clear", "↩ To inbox"),
)

_VIEWS = ("all", "inbox", "today", "cats")


def extension_is_off() -> bool:
    """True when the Todo extension is switched off. Never raises."""
    try:
        from navig.gateway.channels.telegram_extensions import is_enabled  # noqa: PLC0415

        return not is_enabled("todo")
    except Exception:  # noqa: BLE001 — a gate that cannot be read must not hide the list
        return False


def extension_banner(*, as_html: bool = True) -> str:
    """One-line warning above any surface that reports todo state.

    Switching the extension off stops DELIVERY, not scheduling: the reminders are
    still in the table and the tasks are still due. Left unexplained that is the "is
    it me or is it broken" ambiguity, so `navig todo` and the agent tools print this
    above their own output. Returns "" when the extension is on, so a caller can
    prepend unconditionally.
    """
    if not extension_is_off():
        return ""
    if as_html:
        return (
            "⚠️ <b>The Todo extension is off</b> — your tasks are still here, but "
            "reminders are not delivered.\nTurn it on: /extensions\n"
        )
    return "The Todo extension is off - tasks are kept, reminders are not delivered. Turn it on: /extensions"


# ── the store ────────────────────────────────────────────────────────────────

def _store() -> Any:
    from navig.store.board import get_board_store  # noqa: PLC0415

    return get_board_store()


# ── keyboards ────────────────────────────────────────────────────────────────

def _nav_row(active: str) -> list[dict[str, str]]:
    """The view switcher. The active view is marked rather than removed — a button
    that disappears when you are on its view makes the row jump under your thumb."""
    labels = (("all", "📋 All"), ("inbox", "📥 Inbox"), ("today", "📅 Today"), ("cats", "🗂 Category"))
    return [
        {
            "text": f"• {label} •" if key == active else label,
            "callback_data": f"td:v:{key}",
        }
        for key, label in labels
    ]


def build_list_keyboard(
    todos: list[dict[str, Any]], *, view: str = "all", limit: int = 8
) -> dict[str, Any]:
    """Tick buttons for the visible rows, plus the view switcher.

    Capped at `limit` rows: Telegram rejects an over-large keyboard outright, so an
    unbounded list would make the card fail to render exactly when it has the most in
    it — the moment the operator most needs it.
    """
    rows: list[list[dict[str, str]]] = []
    for todo in todos[:limit]:
        title = str(todo.get("title") or "")
        short = title if len(title) <= 22 else title[:21] + "…"
        rows.append([
            {"text": f"✓ {short}", "callback_data": f"td:d:{todo['id']}"},
            {"text": "⋯", "callback_data": f"td:o:{todo['id']}"},
        ])
    if len(todos) > limit:
        rows.append([{"text": f"… and {len(todos) - limit} more", "callback_data": "td:v:all"}])
    rows.append(_nav_row(view))
    rows.append([{"text": "＋ Add a task", "callback_data": "td:add"}])
    return {"inline_keyboard": rows}


def build_category_keyboard(categories: list[dict[str, Any]]) -> dict[str, Any]:
    """One button per category, addressed by ORDINAL.

    A category is free-form text the operator invents, so its NAME could be any length
    in any script — and `callback_data` is capped at 64 bytes. The ordinal is resolved
    against a freshly-read list on every tap, so it cannot point at a stale row.
    """
    rows: list[list[dict[str, str]]] = []
    pair: list[dict[str, str]] = []
    for index, row in enumerate(categories):
        name = str(row.get("name") or "") or "uncategorised"
        count = int(row.get("open") or 0)
        pair.append({
            "text": f"🗂 {name}" + (f" ({count})" if count else ""),
            "callback_data": f"td:c:{index}",
        })
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append(_nav_row("cats"))
    return {"inline_keyboard": rows}


def build_detail_keyboard(todo: dict[str, Any]) -> dict[str, Any]:
    """Everything you can do to one task, on one screen."""
    tid = todo["id"]
    done = bool(todo.get("completed_at"))
    rows: list[list[dict[str, str]]] = []

    rows.append([
        {"text": "↩ Reopen" if done else "✓ Done", "callback_data": f"td:{'u' if done else 'd'}:{tid}"},
        {"text": "🗑 Delete", "callback_data": f"td:x:{tid}"},
    ])

    quick = [
        {"text": label, "callback_data": f"td:s:{tid}:{slot}"}
        for slot, label in _QUICK_SLOTS
    ]
    rows.extend([quick[:3], quick[3:]])

    leads = set(todo.get("remind_before") or [])
    rows.append([
        {
            "text": ("🔔 " if token in leads else "") + describe_lead(token).replace(" before", ""),
            "callback_data": f"td:r:{tid}:{token}",
        }
        for token in LEAD_TIMES[:3]
    ])

    current = todo.get("recur")
    rows.append([
        {
            "text": ("🔁 " if current == name else "") + name,
            "callback_data": f"td:p:{tid}:{'off' if current == name else name}",
        }
        for name in RECURRENCES
    ])

    rows.append([{"text": "‹ Back to the list", "callback_data": "td:v:all"}])
    return {"inline_keyboard": rows}


# ── views ────────────────────────────────────────────────────────────────────

def build_view(view: str, *, now: datetime | None = None) -> tuple[str, dict[str, Any]]:
    """Render one of the four top-level views. Pure apart from the store read."""
    now = now or local_now()
    store = _store()

    if view == "cats":
        categories = store.todo_categories()
        return render_category_summary(categories, now), build_category_keyboard(categories)

    todos = store.list_todos()
    if view == "inbox":
        todos = [t for t in todos if not t.get("due_at")]
        text = render_agenda(
            todos, now, title="📥 INBOX",
            empty_hint="The inbox is empty. Send /todo &lt;something&gt; to capture one.",
        )
    elif view == "today":
        from navig.pim.render import bucket_of  # noqa: PLC0415

        todos = [t for t in todos if bucket_of(t, now) in ("overdue", "today")]
        text = render_agenda(
            todos, now, title="📅 TODAY",
            empty_hint="Nothing due today. 🎉",
        )
    else:
        view = "all"
        text = render_agenda(todos, now)

    return extension_banner() + text, build_list_keyboard(todos, view=view)


def build_category_view(index: int, *, now: datetime | None = None) -> tuple[str, dict[str, Any]]:
    """One category's tasks, addressed by ordinal (see `build_category_keyboard`)."""
    now = now or local_now()
    store = _store()
    categories = store.todo_categories()
    if not 0 <= index < len(categories):
        return build_view("cats", now=now)

    name = str(categories[index].get("name") or "")
    todos = store.list_todos(category=name)
    label = name or "uncategorised"
    text = render_agenda(
        todos, now, title=f"🗂 {label.upper()}",
        empty_hint=f"Nothing in {label} yet.",
    )
    return extension_banner() + text, build_list_keyboard(todos, view="cats")


def build_detail(card_id: str, *, now: datetime | None = None) -> tuple[str, dict[str, Any]] | None:
    now = now or local_now()
    todo = _store().get_todo(card_id)
    if todo is None:
        return None
    return extension_banner() + render_detail(todo, now), build_detail_keyboard(todo)


# ── capture ──────────────────────────────────────────────────────────────────

def capture(text: str, *, now: datetime | None = None, origin: str = "manual") -> dict[str, Any] | None:
    """Turn one typed line into a todo. Returns None for an empty line.

    Order matters: recurrence comes off FIRST, because "every monday 9:30" has to lose
    the "every monday" before the date parser sees it — otherwise the suffix rule
    matches "monday 9:30" and the recurrence stays in the title.
    """
    now = now or local_now()
    rest, recur = split_recurrence(text or "")
    title, due = split_due(rest, now)
    title = title.strip()
    if not title:
        return None

    return _store().create_todo(
        title,
        due_at=to_utc_iso(due) if due else None,
        recur=recur,
        origin=origin,
    )


def _quick_date(slot: str, now: datetime) -> datetime | None:
    """Resolve a quick-date button. `None` means "back to the inbox"."""
    if slot == "clear":
        return None
    base = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if slot == "today":
        # Today at 9am has usually gone by the time you tap it; keep it today but an
        # hour out, so it is still a real moment a reminder can fire at.
        return base if base > now else now + timedelta(hours=1)
    if slot == "tmrw":
        return base + timedelta(days=1)
    if slot == "wknd":
        # Saturday. `(5 - weekday) % 7` is 0 on a Saturday, which would mean "today" —
        # so a Saturday tap means NEXT Saturday, matching "next" everywhere else.
        ahead = (5 - now.weekday()) % 7 or 7
        return base + timedelta(days=ahead)
    if slot == "nextwk":
        return base + timedelta(days=7)
    return None


# ── the callback router ──────────────────────────────────────────────────────

async def handle_callback(
    channel: Any,
    cb_data: str,
    chat_id: int,
    message_id: int,
    user_id: int | None = None,
) -> str:
    """Apply one button press and re-render the card. Returns the toast text.

    Never raises: an exception here reaches the callback router, which answers with a
    generic failure and leaves the card showing state that no longer matches the
    database — the operator's only clue being that nothing happened.
    """
    now = local_now()
    body = cb_data[len(CALLBACK_PREFIX) :]
    parts = body.split(":")
    action = parts[0] if parts else ""

    try:
        store = _store()

        if action == "v" and len(parts) >= 2:
            view = parts[1] if parts[1] in _VIEWS else "all"
            text, keyboard = build_view(view, now=now)
            await _edit(channel, chat_id, message_id, text, keyboard)
            return ""

        if action == "c" and len(parts) >= 2:
            index = int(parts[1]) if parts[1].isdigit() else 0
            text, keyboard = build_category_view(index, now=now)
            await _edit(channel, chat_id, message_id, text, keyboard)
            return ""

        if action == "o" and len(parts) >= 2:
            rendered = build_detail(parts[1], now=now)
            if rendered is None:
                return "That task is gone"
            await _edit(channel, chat_id, message_id, *rendered)
            return ""

        if action in ("d", "u") and len(parts) >= 2:
            card_id = parts[1]
            todo = store.complete_todo(card_id) if action == "d" else store.reopen_todo(card_id)
            if todo is None:
                return "That task is gone"
            _resync_reminders(store, todo, user_id=user_id, chat_id=chat_id, now=now)
            await _rerender_list(channel, chat_id, message_id, now)
            if action == "u":
                return "Reopened"
            # A recurring todo comes back OPEN with a new date — say which, or the
            # operator taps "done" and watches the row refuse to disappear.
            if todo.get("recur") and not todo.get("completed_at"):
                return f"Done · next {_short_date(todo.get('due_at'), now)}"
            return "Done ✓"

        if action == "x" and len(parts) >= 2:
            card_id = parts[1]
            from navig.pim.reminders import cancel_for  # noqa: PLC0415

            if user_id is not None:
                cancel_for(store, card_id, user_id=int(user_id))
            if not store.delete_todo(card_id):
                return "That task is gone"
            await _rerender_list(channel, chat_id, message_id, now)
            return "Deleted"

        if action == "s" and len(parts) >= 3:
            card_id, slot = parts[1], parts[2]
            when = _quick_date(slot, now)
            todo = store.update_todo(card_id, {"due_at": to_utc_iso(when) if when else None})
            if todo is None:
                return "That task is gone"
            _resync_reminders(store, todo, user_id=user_id, chat_id=chat_id, now=now)
            rendered = build_detail(card_id, now=now)
            if rendered:
                await _edit(channel, chat_id, message_id, *rendered)
            return "Back in the inbox" if when is None else f"Due {_short_date(todo.get('due_at'), now)}"

        if action == "r" and len(parts) >= 3:
            card_id, token = parts[1], parts[2]
            todo = store.get_todo(card_id)
            if todo is None:
                return "That task is gone"
            leads = list(todo.get("remind_before") or [])
            on = token not in leads
            leads = [*leads, token] if on else [x for x in leads if x != token]
            todo = store.update_todo(card_id, {"remind_before": leads})
            _resync_reminders(store, todo, user_id=user_id, chat_id=chat_id, now=now)
            rendered = build_detail(card_id, now=now)
            if rendered:
                await _edit(channel, chat_id, message_id, *rendered)
            return f"{'On' if on else 'Off'}: {describe_lead(token)}"

        if action == "p" and len(parts) >= 3:
            card_id, name = parts[1], parts[2]
            recur = None if name == "off" or name not in RECURRENCES else name
            todo = store.update_todo(card_id, {"recur": recur})
            if todo is None:
                return "That task is gone"
            rendered = build_detail(card_id, now=now)
            if rendered:
                await _edit(channel, chat_id, message_id, *rendered)
            return "Repeat off" if recur is None else f"Repeats {recur}"

        if action == "add":
            return "Send: /todo <task> [when]"

    except Exception:  # noqa: BLE001 — a raise here leaves the card showing stale state
        logger.exception("todo callback %r failed", cb_data)
        return "Something went wrong"

    return ""


def _short_date(due_at: str | None, now: datetime) -> str:
    from navig.pim.clock import from_utc_iso, to_local  # noqa: PLC0415
    from navig.pim.dates import format_local  # noqa: PLC0415

    when = to_local(from_utc_iso(due_at))
    return format_local(when, now) if when else "—"


def _resync_reminders(
    store: Any, todo: dict[str, Any] | None, *, user_id: int | None, chat_id: int, now: datetime
) -> None:
    """Re-derive this todo's reminders after any change to its schedule.

    Called on EVERY mutation rather than only the obviously date-related ones: a
    completion, a recurrence roll and a lead-time toggle all change what should fire,
    and the failure mode of forgetting one is a ping about a task that is finished or
    moved. Best-effort — an unavailable reminder table must not lose the edit itself.
    """
    if todo is None or user_id is None:
        return
    try:
        from navig.pim.reminders import reschedule  # noqa: PLC0415

        reschedule(store, todo, user_id=int(user_id), chat_id=int(chat_id), now=now)
    except Exception:  # noqa: BLE001
        logger.exception("todo reminders: could not resync %s", todo.get("id"))


async def _rerender_list(channel: Any, chat_id: int, message_id: int, now: datetime) -> None:
    text, keyboard = build_view("all", now=now)
    await _edit(channel, chat_id, message_id, text, keyboard)


async def _edit(
    channel: Any, chat_id: int, message_id: int, text: str, keyboard: dict[str, Any] | None
) -> None:
    await channel._api_call(
        "editMessageText",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": keyboard or {"inline_keyboard": []},
        },
    )
