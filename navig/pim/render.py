"""How the task list LOOKS. Pure functions of (todos, now) — no store, no bot.

Keeping the layout out of the callback handler is what makes the card testable: every
line, badge and countdown here is asserted without a Telegram token, a database or a
fixed system clock. The Telegram module (`navig.telegram.todo_actions`) turns these
strings into an API call and does nothing else with them.

The shape the operator asked for, and why it is this and not a Kanban:

    📋 TODO · 12 open

    ⏰ OVERDUE (1)
      ○ Renew the domain          🗂 business   2d ago

    📅 TODAY · Fri 4 Sep
      ○ Dentist                   🗂 life       10:30 · in 3h

    📥 INBOX (4) · no date yet
      ○ Call the accountant

A board has columns you drag things between. This has one question per row — is it
late, is it today, or has it not been scheduled yet — which is the question a person
actually asks a task list, and it needs no dragging on a phone.
"""

from __future__ import annotations

import html
from datetime import datetime, timedelta
from typing import Any

from navig.pim.clock import from_utc_iso, to_local
from navig.pim.dates import format_local, humanize_delta
from navig.telegram.card_doc import Bullets, Card, Details, Heading, Para, Raw, Table

__all__ = [
    "BUCKETS",
    "agenda_card",
    "bucket_label",
    "bucket_of",
    "category_card",
    "category_label",
    "detail_card",
    "group_by_bucket",
    "render_agenda",
    "render_category_summary",
    "render_detail",
    "todo_line",
]

# Ordered as they render. The order IS the priority: what is late first, what is
# happening today next, and the unscheduled pile last, where it reads as something to
# sort rather than a backlog to feel bad about.
#: (bucket key, emoji). The WORDS come from the locale files at render time — a
#: constant here would freeze them in English the way `navig habit add` froze its
#: reminder text into a cron command.
BUCKETS: tuple[tuple[str, str], ...] = (
    ("overdue", "⏰"),
    ("today", "📅"),
    ("soon", "🔜"),
    ("later", "🗓"),
    ("inbox", "📥"),
)

_MARK_OPEN = "○"
_MARK_DONE = "●"


def _esc(text: Any) -> str:
    return html.escape(str(text or ""))


def _t(key: str, **fields: Any) -> str:
    """One localized string, or "" when the key is absent so a caller can test it."""
    try:
        from navig.core import i18n  # noqa: PLC0415

        text = i18n.t(key, **fields)
    except Exception:  # noqa: BLE001 — a render must never fail on a locale read
        return ""
    return "" if text == key else text


def _proposed_origins() -> tuple[str, ...]:
    """Which origins mean "proposed". Imported lazily: `render` is pure display and
    must not pull the store in at import time."""
    try:
        from navig.store.board import PROPOSED_ORIGINS  # noqa: PLC0415

        return PROPOSED_ORIGINS
    except Exception:  # noqa: BLE001 — a render must never fail on an import
        return ("ai", "agent")


def category_label(name: str) -> str:
    """How a category READS. The stored value is unchanged.

    Only the four SEEDS are translated — they are words we ship, so on a fresh install
    they are the whole picker. A category the operator invented is their own word and
    is returned untouched; translating it would be rewriting their data.

    The stored value stays the English key on purpose: translating it would break
    `--category life` after a `/lang` and orphan every row written before the change.
    """
    if not name:
        return _t("pim.category.uncategorised") or "uncategorised"
    return _t(f"pim.category.{name}") or name


def bucket_label(key: str) -> str:
    """The heading for one bucket, emoji plus the localized word."""
    emoji = dict(BUCKETS).get(key, "")
    word = _t(f"pim.bucket.{key}") or key.upper()
    return f"{emoji} {word}".strip()


def bucket_of(todo: dict[str, Any], now: datetime) -> str:
    """Which section a todo belongs in.

    Derived from `completed_at` / `due_at` on every render rather than stored, so a
    task cannot sit in the wrong section because the clock moved and nothing wrote
    to it. That is the whole reason there is no `bucket` column.
    """
    if todo.get("completed_at"):
        return "done"
    due = to_local(from_utc_iso(todo.get("due_at")))
    if due is None:
        return "inbox"
    if due < now:
        return "overdue"
    if due.date() == now.date():
        return "today"
    if due <= now + timedelta(days=7):
        return "soon"
    return "later"


def group_by_bucket(
    todos: list[dict[str, Any]], now: datetime
) -> dict[str, list[dict[str, Any]]]:
    """Bucket → rows, in `BUCKETS` order, empty buckets omitted."""
    out: dict[str, list[dict[str, Any]]] = {}
    for todo in todos:
        out.setdefault(bucket_of(todo, now), []).append(todo)
    return {key: out[key] for key, _ in BUCKETS if out.get(key)}


def todo_line(todo: dict[str, Any], now: datetime, *, show_date: bool = True) -> str:
    """One row: mark, title, category, and the countdown.

    The countdown is the thing the operator asked for by name ("timer with how much
    time is left"), so it is on every dated row rather than hidden behind a tap.
    """
    mark = _MARK_DONE if todo.get("completed_at") else _MARK_OPEN
    parts = [f"  {mark} <b>{_esc(todo.get('title'))}</b>"]

    category = str(todo.get("category") or "")
    if category:
        parts.append(f"🗂 {_esc(category_label(category))}")

    due = to_local(from_utc_iso(todo.get("due_at")))
    if due is not None and show_date:
        stamp = f"{due:%H:%M}" if due.date() == now.date() else format_local(due, now)
        parts.append(f"{_esc(stamp)} · <i>{_esc(humanize_delta(due, now))}</i>")

    if todo.get("recur"):
        recur = todo["recur"]
        parts.append(f"🔁 {_esc(_t(f'pim.recur.{recur}') or recur)}")
    if todo.get("origin") in _proposed_origins():
        # An AI guess must be visibly a guess. Without this the operator cannot tell
        # what they wrote from what was proposed, which is the difference between a
        # suggestion they can dismiss and a task they think they made a commitment to.
        parts.append("✨")
    return "   ".join(parts)


def _row_html(todo: dict[str, Any], now: datetime, *, show_date: bool = True) -> str:
    """One task as inline HTML for a list bullet — no marker, no indent.

    ``todo_line`` keeps the flat-text marker for the plain render; a rich list has its
    own bullets, so marking the row twice would read as a broken list.
    """
    parts = [f"<b>{_esc(todo.get('title'))}</b>"]
    category = str(todo.get("category") or "")
    if category:
        parts.append(f"🗂 {_esc(category_label(category))}")
    due = to_local(from_utc_iso(todo.get("due_at")))
    if due is not None and show_date:
        stamp = f"{due:%H:%M}" if due.date() == now.date() else format_local(due, now)
        parts.append(f"{_esc(stamp)} · <i>{_esc(humanize_delta(due, now))}</i>")
    if todo.get("recur"):
        recur = todo["recur"]
        parts.append(f"🔁 {_esc(_t(f'pim.recur.{recur}') or recur)}")
    if todo.get("origin") in _proposed_origins():
        parts.append("✨")
    return "  ·  ".join(parts)


def agenda_card(
    todos: list[dict[str, Any]],
    now: datetime,
    *,
    title: str | None = None,
    empty_hint: str | None = None,
) -> Card:
    """The task list as structure — one definition, rendered rich or flat.

    A section heading per bucket and a bullet per task: the rich render gets real
    ``<h3>`` sections and a list that reads as a list, the classic render keeps the
    bold headings and ``•`` rows the operator already knows.
    """
    open_todos = [t for t in todos if not t.get("completed_at")]
    heading = title or f"📋 {_t('pim.title.todo') or 'TODO'}"
    count = _t("pim.count.open", n=len(open_todos)) or f"{len(open_todos)} open"
    card = Card(title=heading, meta=count)

    if not open_todos:
        hint = empty_hint or _t("pim.empty.default") or (
            "Nothing here. Send /todo &lt;something&gt; to capture one."
        )
        # Locale hints ship with their own &lt; &gt; entities — escaping again would
        # print "&amp;lt;something&amp;gt;" on the card.
        card.add(Para(Raw(hint), style="italic"))
        return card

    grouped = group_by_bucket(open_todos, now)
    for key, _emoji in BUCKETS:
        rows = grouped.get(key)
        if not rows:
            continue
        suffix = ""
        if key == "today":
            suffix = f" · {format_local(now.replace(hour=0, minute=0), now)}"
        elif key == "inbox":
            suffix = f" · {_t('pim.inbox.no_date') or 'no date yet'}"
        card.add(Heading(f"{bucket_label(key)} ({len(rows)}){suffix}"))
        card.add(Bullets([
            Raw(_row_html(t, now, show_date=key != "inbox")) for t in rows
        ]))
    return card


def category_card(categories: list[dict[str, Any]], now: datetime) -> Card:
    """The category picker with open counts, as structure."""
    del now  # signature parity with the other renderers; nothing here is time-dependent
    card = Card(title=f"🗂 {_t('pim.title.categories') or 'BY CATEGORY'}")
    if not categories:
        card.add(Para(_t("pim.empty.categories") or "No categories yet.", style="italic"))
        return card
    rows: list[list[str | Raw]] = []
    for row in categories:
        name = category_label(str(row.get("name") or ""))
        count = int(row.get("open") or 0)
        marker: str | Raw = (
            (_t("pim.count.open", n=count) or f"{count} open")
            if count
            else Raw(f"<i>{_esc(_t('pim.category.empty') or 'empty')}</i>")
        )
        rows.append([Raw(f"🗂 <b>{_esc(name)}</b>"), marker])
    card.add(Table(rows=rows))
    return card


def detail_card(todo: dict[str, Any], now: datetime) -> Card:
    """One task, everything about it, as a two-column sheet.

    The list has room for a title and a countdown; a task's settings — category,
    recurrence, lead time, which space proposed it — do not fit there. A key/value
    table is the shape that keeps them scannable instead of a stack of emoji lines.
    """
    card = Card(title=str(todo.get("title") or ""))
    rows: list[list[str | Raw]] = []

    due = to_local(from_utc_iso(todo.get("due_at")))
    if due is not None:
        rows.append(["📅", Raw(
            f"{_esc(format_local(due, now))} · <i>{_esc(humanize_delta(due, now))}</i>"
        )])
    else:
        rows.append(["📥", Raw(
            f"<i>{_esc(_t('pim.detail.inbox') or 'in the inbox — no date yet')}</i>"
        )])

    if todo.get("category"):
        rows.append(["🗂", Raw(_esc(category_label(str(todo["category"]))))])
    if todo.get("recur"):
        recur = todo["recur"]
        word = _t(f"pim.recur.{recur}") or recur
        rows.append(["🔁", Raw(_esc(_t("pim.detail.repeats", recur=word) or f"repeats {word}"))])

    leads = todo.get("remind_before") or []
    if leads:
        from navig.pim.dates import describe_lead  # noqa: PLC0415 — keeps `navig help` fast

        rows.append(["🔔", Raw(_esc(", ".join(describe_lead(x) for x in leads)))])
    elif due is not None:
        rows.append(["🔔", Raw(f"<i>{_esc(_t('pim.detail.no_reminder') or 'no reminder set')}</i>")])

    if todo.get("space"):
        space = _t("pim.detail.space", space=todo["space"]) or f"space: {todo['space']}"
        rows.append(["🧩", Raw(_esc(space))])

    # A key/value sheet, not a data grid: no rules, no stripes.
    card.add(Table(rows=rows, bordered=False, striped=False))

    if todo.get("origin") in _proposed_origins():
        suggested = _t("pim.detail.suggested") or "suggested from your spaces"
        card.add(Para(f"✨ {suggested}", style="italic"))
    if todo.get("notes"):
        card.add(Details(_t("pim.detail.notes") or "Notes", [Para(str(todo["notes"]))]))
    if todo.get("completed_at"):
        card.add(Para(f"✅ {_t('pim.detail.done') or 'done'}", style="italic"))
    return card


def render_agenda(
    todos: list[dict[str, Any]],
    now: datetime,
    *,
    title: str | None = None,
    empty_hint: str | None = None,
) -> str:
    """The task list as classic ``sendMessage`` HTML (see :func:`agenda_card`)."""
    return agenda_card(todos, now, title=title, empty_hint=empty_hint).classic_html()


def render_category_summary(categories: list[dict[str, Any]], now: datetime) -> str:
    """The category picker, classic render."""
    return category_card(categories, now).classic_html()


def render_detail(todo: dict[str, Any], now: datetime) -> str:
    """One task, classic render."""
    return detail_card(todo, now).classic_html()

