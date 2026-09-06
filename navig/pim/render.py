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

__all__ = [
    "BUCKETS",
    "bucket_of",
    "group_by_bucket",
    "render_agenda",
    "render_category_summary",
    "render_detail",
    "todo_line",
]

# Ordered as they render. The order IS the priority: what is late first, what is
# happening today next, and the unscheduled pile last, where it reads as something to
# sort rather than a backlog to feel bad about.
BUCKETS: tuple[tuple[str, str], ...] = (
    ("overdue", "⏰ OVERDUE"),
    ("today", "📅 TODAY"),
    ("soon", "🔜 NEXT 7 DAYS"),
    ("later", "🗓 LATER"),
    ("inbox", "📥 INBOX"),
)

_MARK_OPEN = "○"
_MARK_DONE = "●"


def _esc(text: Any) -> str:
    return html.escape(str(text or ""))


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
        parts.append(f"🗂 {_esc(category)}")

    due = to_local(from_utc_iso(todo.get("due_at")))
    if due is not None and show_date:
        stamp = f"{due:%H:%M}" if due.date() == now.date() else format_local(due, now)
        parts.append(f"{_esc(stamp)} · <i>{_esc(humanize_delta(due, now))}</i>")

    if todo.get("recur"):
        parts.append(f"🔁 {_esc(todo['recur'])}")
    if todo.get("origin") == "ai":
        # An AI guess must be visibly a guess. Without this the operator cannot tell
        # what they wrote from what was proposed, which is the difference between a
        # suggestion they can dismiss and a task they think they made a commitment to.
        parts.append("✨")
    return "   ".join(parts)


def render_agenda(
    todos: list[dict[str, Any]],
    now: datetime,
    *,
    title: str = "📋 TODO",
    empty_hint: str = "Nothing here. Send /todo &lt;something&gt; to capture one.",
) -> str:
    """The default view: everything open, grouped, soonest first."""
    open_todos = [t for t in todos if not t.get("completed_at")]
    header = f"<b>{_esc(title)}</b> · {len(open_todos)} open"
    if not open_todos:
        return f"{header}\n\n<i>{empty_hint}</i>"

    lines = [header]
    grouped = group_by_bucket(open_todos, now)
    for key, label in BUCKETS:
        rows = grouped.get(key)
        if not rows:
            continue
        suffix = ""
        if key == "today":
            suffix = f" · {now:%a %d %b}".replace(" 0", " ")
        elif key == "inbox":
            suffix = " · no date yet"
        lines.append(f"\n<b>{label}</b> ({len(rows)}){suffix}")
        lines.extend(todo_line(t, now, show_date=key != "inbox") for t in rows)
    return "\n".join(lines)


def render_category_summary(categories: list[dict[str, Any]], now: datetime) -> str:
    """The category picker, with open counts.

    Categories with nothing in them still appear: an empty category you can file INTO
    is useful, while one that only shows up once it already has something is a picker
    that is empty exactly when you first need it.
    """
    del now  # signature parity with the other renderers; nothing here is time-dependent
    lines = ["<b>🗂 BY CATEGORY</b>"]
    if not categories:
        return "<b>🗂 BY CATEGORY</b>\n\n<i>No categories yet.</i>"
    for row in categories:
        name = str(row.get("name") or "") or "uncategorised"
        count = int(row.get("open") or 0)
        marker = f"{count} open" if count else "<i>empty</i>"
        lines.append(f"  🗂 <b>{_esc(name)}</b> — {marker}")
    return "\n".join(lines)


def render_detail(todo: dict[str, Any], now: datetime) -> str:
    """One task, everything about it.

    Reached by tapping a row. Shows the state that the list has no room for —
    reminders, recurrence, which space it came from — because a setting the operator
    cannot SEE is one they cannot tell is wrong.
    """
    lines = [f"<b>{_esc(todo.get('title'))}</b>"]

    due = to_local(from_utc_iso(todo.get("due_at")))
    if due is not None:
        lines.append(f"📅 {_esc(format_local(due, now))} · <i>{_esc(humanize_delta(due, now))}</i>")
    else:
        lines.append("📥 <i>in the inbox — no date yet</i>")

    if todo.get("category"):
        lines.append(f"🗂 {_esc(todo['category'])}")
    if todo.get("recur"):
        lines.append(f"🔁 repeats {_esc(todo['recur'])}")

    leads = todo.get("remind_before") or []
    if leads:
        from navig.pim.dates import describe_lead  # noqa: PLC0415 — keeps `navig help` fast

        lines.append("🔔 " + _esc(", ".join(describe_lead(x) for x in leads)))
    elif due is not None:
        lines.append("🔔 <i>no reminder set</i>")

    if todo.get("space"):
        lines.append(f"🧩 space: {_esc(todo['space'])}")
    if todo.get("origin") == "ai":
        lines.append("✨ <i>suggested from your spaces</i>")
    if todo.get("notes"):
        lines.append(f"\n{_esc(todo['notes'])}")
    if todo.get("completed_at"):
        lines.append("\n✅ <i>done</i>")
    return "\n".join(lines)
