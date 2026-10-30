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
from navig.pim.render import (
    _proposed_origins,
    agenda_card,
    category_card,
    category_label,
    detail_card,
    render_detail,
)
from navig.telegram.card_doc import Card
from navig.telegram.card_nav import chip, close_row, menu_rows, step

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "td:"

#: Quick-date buttons on the detail card, as (payload slot, label, resolver).
#: Deliberately relative rather than a calendar picker: a calendar is five taps and a
#: scroll on a phone, and "tomorrow" covers most of what a person actually schedules.
#: (payload slot, emoji prefix). The WORDS are looked up per render — a label
#: frozen in a module constant is evaluated once at import and can never follow
#: `/lang`, which is how a localized card keeps showing English buttons.
_QUICK_SLOTS: tuple[tuple[str, str], ...] = (
    ("today", ""),
    ("tmrw", ""),
    ("wknd", ""),
    ("nextwk", ""),
    ("clear", "↩ "),
)


def _quick_label(slot: str, prefix: str) -> str:
    fallback = {
        "today": "Today", "tmrw": "Tomorrow", "wknd": "Weekend",
        "nextwk": "Next week", "clear": "To inbox",
    }[slot]
    return f"{prefix}{_t(f'pim.quick.{slot}') or fallback}"

_VIEWS = ("all", "inbox", "today", "cats")

#: Origins that mean "proposed, not yet agreed to". Rows with these get keep/no
#: buttons instead of done, and render with a sparkle. Read from the store rather
#: than restated: this used to be a third independent copy, and the other two
#: disagreed with it.


def _t(key: str, **fields: Any) -> str:
    """One localized string, or "" when the key is absent so a caller can test it."""
    try:
        from navig.core import i18n  # noqa: PLC0415

        text = i18n.t(key, **fields)
    except Exception:  # noqa: BLE001 — a card must never fail on a locale read
        return ""
    return "" if text == key else text


def extension_is_off() -> bool:
    """True when the Todo extension is switched off. Never raises."""
    try:
        from navig.gateway.channels.telegram_extensions import is_enabled  # noqa: PLC0415

        return not is_enabled("todo")
    except Exception:  # noqa: BLE001 — a gate that cannot be read must not hide the list
        return False


def extension_banner(*, as_html: bool = True) -> str:
    """One-line warning above any surface that reports todo state.

    Switching the extension off keeps the TASKS and stops the REMINDERS — the same
    bargain habits offer. `pim.reminders.reschedule` writes no new row while it is
    off (the habit rule: nothing scheduled can ever be delivered) and the poller
    holds back any row written before the switch was flipped. The tasks are all
    still here and still due; nothing tells you about them.

    Left unexplained that is the "is it me or is it broken" ambiguity, so
    `navig todo` and the agent tools print this above their own output. Returns ""
    when the extension is on, so a caller can prepend unconditionally.
    """
    if not extension_is_off():
        return ""
    if as_html:
        return _t("pim.ext.off_html") or (
            "⚠️ <b>The Todo extension is off</b> — your tasks are still here, but "
            "reminders are not delivered.\nTurn it on: /extensions\n"
        )
    return _t("pim.ext.off_cli") or (
        "The Todo extension is off - tasks are kept, reminders are not delivered. "
        "Turn it on: /extensions"
    )


# ── the store ────────────────────────────────────────────────────────────────

def _store() -> Any:
    from navig.store.board import get_board_store  # noqa: PLC0415

    return get_board_store()


# ── keyboards ────────────────────────────────────────────────────────────────

def _nav_rows(active: str) -> list[list[dict[str, str]]]:
    """The view switcher, as TWO rows of two.

    Four buttons on one row left each a quarter of a phone's width, and Telegram cut
    every Russian label to its first letters ("• 📋 В…", "📥 Вхо…", "📅 Сег…") — the
    switcher became four icons and an ellipsis. Two per row fits "📥 Входящие".

    The active view is marked rather than removed — a button that disappears when you
    are on its view makes the row jump under your thumb. The mark is a single leading
    "•", because a trailing one costs width on exactly the labels that need it.
    """
    views = (
        ("all", "📋", "All"),
        ("inbox", "📥", "Inbox"),
        ("today", "📅", "Today"),
        ("cats", "🗂", "Category"),
    )
    out: list[dict[str, str]] = []
    for key, emoji, fallback in views:
        # `cats` is the view name; the locale key reads `category`, which is what the
        # button says. Keeping them separate means renaming one cannot silently
        # repoint the other.
        word = _t(f"pim.nav.{'category' if key == 'cats' else key}") or fallback
        label = f"{emoji} {word}"
        out.append(
            {
                "text": f"• {label}" if key == active else label,
                "callback_data": f"td:v:{key}",
            }
        )
    return [out[:2], out[2:]]


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
        if todo.get("origin") in _proposed_origins():
            # A suggestion is not a task yet, so "done" would be a lie: it would mark
            # work the operator never agreed to as finished. Keep or no, one tap each.
            keep = _t("pim.button.keep") or "keep"
            rows.append([
                {"text": f"✓ {keep} {short}", "callback_data": f"td:ok:{todo['id']}"},
                {"text": "✕", "callback_data": f"td:no:{todo['id']}"},
            ])
            continue
        rows.append([
            {"text": f"✓ {short}", "callback_data": f"td:d:{todo['id']}"},
            {"text": "⋯", "callback_data": f"td:o:{todo['id']}"},
        ])
    if len(todos) > limit:
        extra = len(todos) - limit
        more = _t("pim.button.more", n=extra) or f"… and {extra} more"
        rows.append([{"text": more, "callback_data": "td:v:all"}])
    rows.extend(_nav_rows(view))
    # Appointments and pings are their own views now — one tap away, never mixed in.
    agenda = _t("pim.agenda.nav") or "Agenda"
    pings = _t("pim.nav.reminders") or "Reminders"
    rows.append([
        {"text": f"📅 {agenda}", "callback_data": "ag:v"},
        {"text": f"🔔 {pings}", "callback_data": "rv:v"},
    ])
    add = _t("pim.button.add") or "Add a task"
    rows.append([{"text": f"＋ {add}", "callback_data": "td:add"}])
    return {"inline_keyboard": rows}


def _category_chip(cat: dict[str, Any]) -> str:
    from navig.pim.render import category_label  # noqa: PLC0415

    emoji = str(cat.get("emoji") or "🗂")
    return f"{emoji} {cat.get('label') or category_label(str(cat['name']))}"


def build_added(todo: dict[str, Any], *, now: datetime | None = None) -> tuple[str, dict[str, Any]]:
    """The reply to a quick capture: what was saved, and ONE tap to file it.

    Capture is the shortest path on purpose ("/todo buy paper"), so the category is
    asked AFTER, as buttons, instead of being a field you must fill first. Typing
    `#work` in the line skips the question.
    """
    import html  # noqa: PLC0415

    now = now or local_now()
    title = html.escape(str(todo.get("title") or ""))
    added = _t("pim.added", title=title) or f"✓ <b>Added</b>: {title}"
    lines = [added]
    if todo.get("due_at"):
        lines.append(f"🕐 {_short_date(todo.get('due_at'), now)}")
    rows: list[list[dict[str, str]]] = []
    category = str(todo.get("category") or "")
    if category:
        cat = _store().get_category(category) or {"name": category}
        lines.append(_category_chip(cat))
    else:
        lines.append(f"<i>{_t('pim.pick_category') or 'Pick a category:'}</i>")
        chips = [
            {"text": _category_chip(c), "callback_data": f"td:k:{todo['id']}:{i}"}
            for i, c in enumerate(_store().list_categories())
        ][:8]
        rows.extend(chips[i : i + 2] for i in range(0, len(chips), 2))
    rows.append([
        {"text": f"⋯ {_t('pim.button.details') or 'Details'}", "callback_data": f"td:o:{todo['id']}"},
        {"text": f"📋 {_t('pim.rv.tasks') or 'Tasks'}", "callback_data": "td:v:all"},
    ])
    return "\n".join(lines), {"inline_keyboard": rows}


def build_category_keyboard(categories: list[dict[str, Any]]) -> dict[str, Any]:
    """One button per category, addressed by ORDINAL.

    A category is free-form text the operator invents, so its NAME could be any length
    in any script — and `callback_data` is capped at 64 bytes. The ordinal is resolved
    against a freshly-read list on every tap, so it cannot point at a stale row.
    """
    rows: list[list[dict[str, str]]] = []
    pair: list[dict[str, str]] = []
    for index, row in enumerate(categories):
        name = category_label(str(row.get("name") or ""))
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
    rows.extend(_nav_rows("cats"))
    return {"inline_keyboard": rows}


def build_detail_keyboard(todo: dict[str, Any]) -> dict[str, Any]:
    """Everything you can do to one task, on one screen."""
    tid = todo["id"]
    done = bool(todo.get("completed_at"))
    rows: list[list[dict[str, str]]] = []

    primary = (
        f"↩ {_t('pim.button.reopen') or 'Reopen'}"
        if done
        else f"✓ {_t('pim.button.done') or 'Done'}"
    )
    rows.append([
        {"text": primary, "callback_data": f"td:{'u' if done else 'd'}:{tid}"},
        {"text": f"🗑 {_t('pim.button.delete') or 'Delete'}", "callback_data": f"td:x:{tid}"},
    ])

    quick = [
        {"text": _quick_label(slot, prefix), "callback_data": f"td:s:{tid}:{slot}"}
        for slot, prefix in _QUICK_SLOTS
    ]
    rows.extend([quick[:3], quick[3:]])

    leads = set(todo.get("remind_before") or [])
    # The first three picker leads, plus any OTHER lead that is on — a chat-captured
    # appointment starts with "1h", and a reminder you cannot see is one you cannot
    # switch off.
    shown = [*LEAD_TIMES[:3], *(t for t in LEAD_TIMES[3:] if t in leads)]
    rows.append([
        {
            "text": ("🔔 " if token in leads else "") + describe_lead(token).replace(" before", ""),
            "callback_data": f"td:r:{tid}:{token}",
        }
        for token in shown
    ])

    current = todo.get("recur")
    rows.append([
        {
            "text": ("🔁 " if current == name else "") + (_t(f"pim.recur.{name}") or name),
            "callback_data": f"td:p:{tid}:{'off' if current == name else name}",
        }
        for name in RECURRENCES
    ])

    back = _t("pim.button.back") or "Back to the list"
    rows.append([{"text": f"‹ {back}", "callback_data": "td:v:all"}])
    return {"inline_keyboard": rows}


# ── views ────────────────────────────────────────────────────────────────────

def _view_card(view: str, now: datetime) -> tuple[Card, dict[str, Any], str]:
    """One view as structure: (card, keyboard, banner). Both renders project this.

    The selection rules (which rows belong to which view, which title, which empty
    hint) live here once — a rich render that re-derived them would be the classic
    drift this refactor exists to remove.
    """
    store = _store()

    if view == "cats":
        categories = store.todo_categories()
        return (
            category_card(categories, now),
            build_category_keyboard(categories),
            extension_banner().strip(),
        )

    todos = store.list_todos()
    if view == "inbox":
        todos = [t for t in todos if not t.get("due_at")]
        title = f"📥 {_t('pim.title.inbox') or 'INBOX'}"
        empty_hint = _t("pim.empty.inbox") or "The inbox is empty. Send /todo &lt;something&gt; to capture one."
    elif view == "today":
        from navig.pim.render import bucket_of  # noqa: PLC0415

        todos = [t for t in todos if bucket_of(t, now) in ("overdue", "today")]
        title = f"📅 {_t('pim.title.today') or 'TODAY'}"
        empty_hint = _t("pim.empty.today") or "Nothing due today. 🎉"
    else:
        view, title, empty_hint = "all", None, None

    card = agenda_card(todos, now, title=title, empty_hint=empty_hint)
    return card, build_list_keyboard(todos, view=view), extension_banner().strip()


def view_card(view: str, *, now: datetime | None = None) -> tuple[Card, dict[str, Any]]:
    """One view as structure — :mod:`navig.telegram.card_transport` picks the render.

    The extension banner rides as the card footer here instead of being prepended
    to the text: on a rich card a "switched off" notice belongs under the content,
    not above the heading where it reads like part of the title.
    """
    now = now or local_now()
    card, keyboard, banner = _view_card(view, now)
    if banner:
        card.footer = banner
    return card, keyboard


def build_view(view: str, *, now: datetime | None = None) -> tuple[str, dict[str, Any]]:
    """Render one of the four top-level views. Pure apart from the store read."""
    now = now or local_now()
    card, keyboard, banner = _view_card(view, now)
    return (banner + card.classic_html() if banner else card.classic_html()), keyboard


def build_view_rich(view: str, *, now: datetime | None = None) -> tuple[str, dict[str, Any]]:
    """The same view as a rich message — headings and a real list."""
    card, keyboard = view_card(view, now=now)
    return card.rich_html(), keyboard


def _category_card(index: int, now: datetime) -> tuple[Card, dict[str, Any], str]:
    """One category as structure: (card, keyboard, banner). See :func:`_view_card`."""
    store = _store()
    categories = store.todo_categories()
    if not 0 <= index < len(categories):
        return _view_card("cats", now)

    name = str(categories[index].get("name") or "")
    todos = store.list_todos(category=name)
    label = category_label(name)
    card = agenda_card(
        todos, now, title=f"🗂 {label.upper()}",
        empty_hint=_t("pim.empty.category", name=label) or f"Nothing in {label} yet.",
    )
    return card, build_list_keyboard(todos, view="cats"), extension_banner().strip()


def build_category_view(index: int, *, now: datetime | None = None) -> tuple[str, dict[str, Any]]:
    """One category's tasks, addressed by ordinal (see `build_category_keyboard`)."""
    now = now or local_now()
    card, keyboard, banner = _category_card(index, now)
    return (banner + card.classic_html() if banner else card.classic_html()), keyboard


def build_category_view_rich(
    index: int, *, now: datetime | None = None
) -> tuple[str, dict[str, Any]]:
    """One category's tasks as a rich message."""
    now = now or local_now()
    card, keyboard, banner = _category_card(index, now)
    if banner:
        card.footer = banner
    return card.rich_html(), keyboard


def build_detail(card_id: str, *, now: datetime | None = None) -> tuple[str, dict[str, Any]] | None:
    now = now or local_now()
    todo = _store().get_todo(card_id)
    if todo is None:
        return None
    return extension_banner() + render_detail(todo, now), build_detail_keyboard(todo)


def build_detail_rich(
    card_id: str, *, now: datetime | None = None
) -> tuple[str, dict[str, Any]] | None:
    """One task as a rich sheet: a key/value table plus foldable notes."""
    now = now or local_now()
    todo = _store().get_todo(card_id)
    if todo is None:
        return None
    card = detail_card(todo, now)
    banner = extension_banner().strip()
    if banner:
        card.footer = banner
    return card.rich_html(), build_detail_keyboard(todo)


def open_ids(store: Any | None = None) -> list[str]:
    """The open tasks in the order the list shows them — the sequence the pager walks.

    Derived from the store on every press rather than frozen into the keyboard: a task
    completed on another screen must not leave the arrows pointing at a row that is no
    longer there.
    """
    store = store if store is not None else _store()
    return [t["id"] for t in store.list_todos() if not t.get("completed_at")]


def _detail_pager(ids: list[str], card_id: str) -> list[dict[str, Any]]:
    """``◀  3 / 12  ▶`` for the task currently on screen, or no row at all.

    The arrows carry the card id they were drawn for, so a press always resolves
    against that exact neighbour even if three tasks were finished in between.
    """
    if len(ids) <= 1 or card_id not in ids:
        return []
    index = ids.index(card_id)
    return [
        step("◀", f"td:b:{card_id}", enabled=index > 0),
        chip(f"{index + 1} / {len(ids)}"),
        step("▶", f"td:n:{card_id}", enabled=index < len(ids) - 1),
    ]


def detail_view(card_id: str, *, now: datetime | None = None) -> tuple[Card, dict[str, Any]] | None:
    """One task as structure, with the pager on top and one way out at the bottom.

    The pager is the first row because it is the row that turns a list into a
    sequence: from here the operator walks the tasks without going back to the
    list first, and ``✕`` settles the card instead of leaving its buttons live.
    """
    now = now or local_now()
    store = _store()
    todo = store.get_todo(card_id)
    if todo is None:
        return None
    card = detail_card(todo, now)
    banner = extension_banner().strip()
    if banner:
        card.footer = banner
    rows = menu_rows(
        _detail_pager(open_ids(store), card_id),
        *build_detail_keyboard(todo)["inline_keyboard"],
        close_row("td"),
    )
    return card, {"inline_keyboard": rows}


def _neighbour(ids: list[str], card_id: str, delta: int) -> str | None:
    """The id ``delta`` places away, clamped to the ends. None if nothing moves."""
    if card_id not in ids:
        return None
    index = ids.index(card_id)
    target = max(0, min(index + delta, len(ids) - 1))
    return ids[target] if target != index else None


# ── capture ──────────────────────────────────────────────────────────────────

def capture(text: str, *, now: datetime | None = None, origin: str = "manual") -> dict[str, Any] | None:
    """Turn one typed line into a todo. Returns None for an empty line.

    Order matters: recurrence comes off FIRST, because "every monday 9:30" has to lose
    the "every monday" before the date parser sees it — otherwise the suffix rule
    matches "monday 9:30" and the recurrence stays in the title.
    """
    now = now or local_now()
    raw, category = _take_category(text or "")
    rest, recur = split_recurrence(raw)
    title, due = split_due(rest, now)
    title = title.strip()
    if not title:
        return None

    return _store().create_todo(
        title,
        due_at=to_utc_iso(due) if due else None,
        recur=recur,
        origin=origin,
        category=category,
    )


def _take_category(text: str) -> tuple[str, str]:
    """Pull a `#work` token out of a captured line → (rest, category key).

    Matches a category's key or its label, case-insensitively; an unknown word becomes
    a new category on the spot — the same rule as `pim.service.parse_capture`, against
    THIS module's store so a test's isolated board is the one written.
    """
    from navig.pim.service import _CATEGORY_TOKEN  # noqa: PLC0415

    m = _CATEGORY_TOKEN.search(text)
    if not m:
        return text, ""
    token = m.group(1).strip().lower()
    rest = (text[: m.start()] + text[m.end():]).strip()
    store = _store()
    for cat in store.list_categories(include_archived=True):
        if cat["name"] == token or (cat.get("label") or "").strip().lower() == token:
            return rest, str(cat["name"])
    created = store.ensure_category(token)
    return rest, str(created["name"]) if created else token


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
            card, keyboard, _banner = _view_card(view, now)
            await _edit_card(channel, chat_id, message_id, card, keyboard)
            return ""

        if action == "c" and len(parts) >= 2:
            index = int(parts[1]) if parts[1].isdigit() else 0
            card, keyboard, _banner = _category_card(index, now)
            await _edit_card(channel, chat_id, message_id, card, keyboard)
            return ""

        if action == "o" and len(parts) >= 2:
            rendered = detail_view(parts[1], now=now)
            if rendered is None:
                return _t("pim.toast.gone") or "That task is gone"
            await _edit_card(channel, chat_id, message_id, *rendered)
            return ""

        if action in ("n", "b") and len(parts) >= 2:
            # Walk the open tasks without going back to the list: the pager is the
            # row that makes the card a sequence instead of a dead end.
            moved = _neighbour(open_ids(store), parts[1], 1 if action == "n" else -1)
            if moved is None:
                return ""
            rendered = detail_view(moved, now=now)
            if rendered is None:
                return _t("pim.toast.gone") or "That task is gone"
            await _edit_card(channel, chat_id, message_id, *rendered)
            return ""

        if action == "z":
            from navig.telegram import card_nav  # noqa: PLC0415

            todo = store.get_todo(parts[1]) if len(parts) >= 2 else None
            title = (todo or {}).get("title") or (_t("pim.title.todo") or "Tasks")
            await card_nav.settle(channel, chat_id, message_id, title=title)
            return ""

        if action in ("d", "u") and len(parts) >= 2:
            card_id = parts[1]
            todo = store.complete_todo(card_id) if action == "d" else store.reopen_todo(card_id)
            if todo is None:
                return _t("pim.toast.gone") or "That task is gone"
            _resync_reminders(store, todo, user_id=user_id, chat_id=chat_id, now=now)
            await _rerender_list(channel, chat_id, message_id, now)
            if action == "u":
                return _t("pim.toast.reopened") or "Reopened"
            # A recurring todo comes back OPEN with a new date — say which, or the
            # operator taps "done" and watches the row refuse to disappear.
            if todo.get("recur") and not todo.get("completed_at"):
                nxt = _short_date(todo.get("due_at"), now)
                return _t("pim.toast.done_next", when=nxt) or f"Done · next {nxt}"
            return f"{_t('pim.toast.done') or 'Done'} ✓"

        if action == "ok" and len(parts) >= 2:
            # Accepting a suggestion makes it an ordinary task. `origin_ref` is KEPT:
            # it is the dedup key, and clearing it would let the same source be
            # proposed again alongside the copy they just accepted.
            todo = store.update_todo(parts[1], {"origin": "manual"})
            if todo is None:
                return _t("pim.toast.gone") or "That task is gone"
            # A suggestion schedules nothing until it is kept (see
            # `appointments.save`), so keeping it is when its reminders begin.
            _resync_reminders(store, todo, user_id=user_id, chat_id=chat_id, now=now)
            await _rerender_list(channel, chat_id, message_id, now)
            return _t("pim.toast.kept") or "Kept"

        if action == "no" and len(parts) >= 2:
            # Deleting records the dismissal (BoardStore.delete_todo), so this source
            # is never proposed again -- which is what makes "no" mean no.
            card_id = parts[1]
            from navig.pim.reminders import cancel_for  # noqa: PLC0415

            if user_id is not None:
                cancel_for(store, card_id, user_id=int(user_id))
            if not store.delete_todo(card_id):
                return _t("pim.toast.gone") or "That task is gone"
            await _rerender_list(channel, chat_id, message_id, now)
            return _t("pim.toast.dismissed") or "Dismissed — it won't come back"

        if action == "x" and len(parts) >= 2:
            card_id = parts[1]
            from navig.pim.reminders import cancel_for  # noqa: PLC0415

            if user_id is not None:
                cancel_for(store, card_id, user_id=int(user_id))
            if not store.delete_todo(card_id):
                return _t("pim.toast.gone") or "That task is gone"
            await _rerender_list(channel, chat_id, message_id, now)
            return _t("pim.toast.deleted") or "Deleted"

        if action == "s" and len(parts) >= 3:
            card_id, slot = parts[1], parts[2]
            when = _quick_date(slot, now)
            todo = store.update_todo(card_id, {"due_at": to_utc_iso(when) if when else None})
            if todo is None:
                return _t("pim.toast.gone") or "That task is gone"
            _resync_reminders(store, todo, user_id=user_id, chat_id=chat_id, now=now)
            rendered = detail_view(card_id, now=now)
            if rendered:
                await _edit_card(channel, chat_id, message_id, *rendered)
            if when is None:
                return _t("pim.toast.to_inbox") or "Back in the inbox"
            due = _short_date(todo.get("due_at"), now)
            return _t("pim.toast.due", when=due) or f"Due {due}"

        if action == "r" and len(parts) >= 3:
            card_id, token = parts[1], parts[2]
            todo = store.get_todo(card_id)
            if todo is None:
                return _t("pim.toast.gone") or "That task is gone"
            leads = list(todo.get("remind_before") or [])
            on = token not in leads
            leads = [*leads, token] if on else [x for x in leads if x != token]
            todo = store.update_todo(card_id, {"remind_before": leads})
            _resync_reminders(store, todo, user_id=user_id, chat_id=chat_id, now=now)
            rendered = detail_view(card_id, now=now)
            if rendered:
                await _edit_card(channel, chat_id, message_id, *rendered)
            lead = describe_lead(token)
            key = "pim.toast.lead_on" if on else "pim.toast.lead_off"
            return _t(key, lead=lead) or f"{'On' if on else 'Off'}: {lead}"

        if action == "p" and len(parts) >= 3:
            card_id, name = parts[1], parts[2]
            recur = None if name == "off" or name not in RECURRENCES else name
            todo = store.update_todo(card_id, {"recur": recur})
            if todo is None:
                return _t("pim.toast.gone") or "That task is gone"
            rendered = detail_view(card_id, now=now)
            if rendered:
                await _edit_card(channel, chat_id, message_id, *rendered)
            if recur is None:
                return _t("pim.toast.repeat_off") or "Repeat off"
            word = _t(f"pim.recur.{recur}") or recur
            return _t("pim.toast.repeats", recur=word) or f"Repeats {word}"

        if action == "k" and len(parts) >= 3:
            # File a just-captured task. The category is addressed by ORDINAL into the
            # managed list (names never ride in callback_data: 64-byte budget, and a
            # rename must not strand an old button).
            card_id = parts[1]
            cats = store.list_categories()
            index = int(parts[2]) if parts[2].isdigit() else -1
            if not 0 <= index < len(cats):
                return _t("pim.toast.gone") or "That category is gone"
            todo = store.update_todo(card_id, {"category": cats[index]["name"]})
            if todo is None:
                return _t("pim.toast.gone") or "That task is gone"
            await _edit(channel, chat_id, message_id, *build_added(todo, now=now))
            return _category_chip(cats[index])

        if action == "add":
            return _t("pim.toast.add_hint") or "Send: /todo <task> [when]"

    except Exception:  # noqa: BLE001 — a raise here leaves the card showing stale state
        logger.exception("todo callback %r failed", cb_data)
        return _t("pim.toast.error") or "Something went wrong"

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
    card, keyboard, _banner = _view_card("all", now)
    await _edit_card(channel, chat_id, message_id, card, keyboard)


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


async def _edit_card(
    channel: Any,
    chat_id: int,
    message_id: int,
    card: Card,
    keyboard: dict[str, Any] | None,
) -> None:
    """Re-render the card in place, keeping its rich body when the chat renders one.

    ``editMessageText`` accepts a ``rich_message`` (Bot API 10.1), so a press no longer
    has to degrade the card: without this, every task edit silently swapped a heading
    and a key/value sheet for their plain-text projection.
    """
    from navig.telegram import card_transport  # noqa: PLC0415

    rich_edit = getattr(channel, "edit_rich_message", None)
    if rich_edit is not None and card_transport.rich_available(channel):
        await rich_edit(
            chat_id,
            message_id,
            html=card.rich_html(),
            fallback_html=card.classic_html(),
            keyboard=keyboard,
        )
        return
    body = card.classic_html()
    banner = (card.footer or "").strip()
    await _edit(channel, chat_id, message_id, f"{banner}\n{body}" if banner else body, keyboard)
