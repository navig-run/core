"""NAVIG PIM — the personal task list behind ``/todo``, ``navig todo`` and the agent tools.

Pure logic only. Everything here is a function of its arguments — no clock reads, no
config, no database — so the card layout, the date grammar and the recurrence maths
are unit-testable without a bot, a store or a fixed system time.

Persistence lives in :mod:`navig.store.board` (the PIM is a ``kind='todo'`` card on
the existing board, not a fifth task system); the Telegram card lives in
:mod:`navig.telegram.todo_actions`.
"""

from __future__ import annotations

from navig.pim.clock import from_utc_iso, local_now, to_local, to_utc_iso
from navig.pim.dates import (
    LEAD_TIMES,
    RECURRENCES,
    RecurrenceError,
    advance,
    describe_lead,
    format_local,
    humanize_delta,
    parse_lead,
    split_due,
    split_recurrence,
)
from navig.pim.render import (
    bucket_of,
    group_by_bucket,
    render_agenda,
    render_category_summary,
    render_detail,
    todo_line,
)

__all__ = [
    "LEAD_TIMES",
    "RECURRENCES",
    "RecurrenceError",
    "advance",
    "bucket_of",
    "describe_lead",
    "format_local",
    "from_utc_iso",
    "group_by_bucket",
    "humanize_delta",
    "local_now",
    "parse_lead",
    "render_agenda",
    "render_category_summary",
    "render_detail",
    "split_due",
    "split_recurrence",
    "to_local",
    "to_utc_iso",
    "todo_line",
]
