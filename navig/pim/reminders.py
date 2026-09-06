"""Scheduling and — more importantly — CANCELLING a todo's reminders.

Reuses `RuntimeStore.create_reminder` and the gateway's existing 15-second poller
(`TelegramChannel._poll_due_reminders`). A second delivery path would mean two things
that can each be down, two backoff policies and two places to look when a ping does not
arrive; there is no version of that which is better than one.

**The failure this module exists to prevent is the GHOST PING.** A reminder row knows
nothing about todos — it is a message, a chat and a time. So editing a todo's date,
completing it, or deleting it must explicitly cancel the rows that were scheduled for
the old state. Miss that and the operator is reminded, by name, about a task they
finished yesterday, and the only way to stop it is to find the row in a database.

Every write therefore goes through :func:`reschedule`, which cancels first and
schedules second, in that order — so a crash between the two leaves NOTHING scheduled
rather than a duplicate set. Under-notifying is a missed ping; over-notifying is the
thing that gets a bot muted.

A lead time in the past is skipped rather than fired: "3 days before" on a task due
tomorrow means there is no 3-day warning, not an immediate one.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from navig.pim.clock import from_utc_iso, to_local, to_utc_iso
from navig.pim.dates import describe_lead, format_local, parse_lead

logger = logging.getLogger(__name__)

__all__ = ["cancel_for", "planned_fires", "reminder_text", "reschedule"]


def planned_fires(
    due_at: str | None, leads: list[str], now: datetime
) -> list[tuple[str, datetime]]:
    """(lead token, when it fires) for every lead that is still in the future.

    Pure — no store, no clock read — so the "which of these actually fire" decision is
    testable on its own. That decision is the whole content of the scheduling policy:

    * no due date  → nothing to count back from, so nothing fires;
    * a lead whose moment has passed → skipped, NOT fired now;
    * the due moment itself always fires, provided it is still ahead.
    """
    due = to_local(from_utc_iso(due_at))
    if due is None:
        return []

    fires: list[tuple[str, datetime]] = []
    for token in leads:
        delta = parse_lead(token)
        if delta is None:
            logger.debug("todo reminder: ignoring unparseable lead %r", token)
            continue
        moment = due - delta
        if moment > now:
            fires.append((token, moment))

    if due > now:
        fires.append(("", due))
    return sorted(fires, key=lambda pair: pair[1])


def reminder_text(todo: dict[str, Any], lead: str, fire_at: datetime, now: datetime) -> str:
    """What the operator actually reads when it arrives.

    It names the task AND when it is due, because a ping that says only "Dentist" makes
    you open the app to find out whether that means now or on Friday.
    """
    title = str(todo.get("title") or "task")
    due = to_local(from_utc_iso(todo.get("due_at")))
    when = format_local(due, now) if due else "soon"
    if lead:
        return f"⏰ {title} — {describe_lead(lead)} (due {when})"
    return f"⏰ {title} — due now ({when})"


def cancel_for(store: Any, card_id: str, *, user_id: int) -> int:
    """Cancel and forget every reminder linked to this todo. Returns how many.

    Best-effort per row: one reminder that will not cancel must not strand the others,
    because the alternative is that a single bad row keeps the whole set alive.
    """
    try:
        ids = store.unlink_reminders(card_id)
    except Exception:  # noqa: BLE001 — cancellation must never break the calling action
        logger.exception("todo reminders: could not read links for %s", card_id)
        return 0
    if not ids:
        return 0

    runtime = _runtime_store()
    if runtime is None:
        # The links are already gone, so the caller cannot retry. Say so loudly: this
        # is exactly the state that produces a ping for a task that no longer exists.
        logger.error(
            "todo reminders: unlinked %d row(s) for %s but the runtime store is "
            "unavailable — those reminders will still fire",
            len(ids),
            card_id,
        )
        return 0

    cancelled = 0
    for rid in ids:
        try:
            if runtime.cancel_reminder(int(rid), int(user_id)):
                cancelled += 1
        except Exception:  # noqa: BLE001
            logger.exception("todo reminders: could not cancel %s", rid)
    return cancelled


def reschedule(
    store: Any,
    todo: dict[str, Any],
    *,
    user_id: int,
    chat_id: int,
    now: datetime,
) -> int:
    """Cancel whatever was scheduled, then schedule what the todo says now.

    ORDER MATTERS. Cancelling first means a crash in the middle leaves nothing
    scheduled — a missed ping. Scheduling first would leave BOTH sets alive, which is
    the duplicate-notification failure that gets a bot muted at the OS.

    Returns how many reminders are now scheduled. Zero is a normal answer (an inbox
    item, or a due date already past), not a failure.
    """
    card_id = str(todo.get("id") or "")
    if not card_id:
        return 0

    cancel_for(store, card_id, user_id=user_id)

    if todo.get("completed_at"):
        return 0  # a finished task has nothing to remind anyone about

    fires = planned_fires(todo.get("due_at"), list(todo.get("remind_before") or []), now)
    if not fires:
        return 0

    runtime = _runtime_store()
    if runtime is None:
        logger.error("todo reminders: runtime store unavailable; %s has no reminders", card_id)
        return 0

    scheduled = 0
    for lead, moment in fires:
        try:
            rid = runtime.create_reminder(
                int(user_id), int(chat_id), reminder_text(todo, lead, moment, now), moment
            )
            store.link_reminder(card_id, int(rid), lead=lead, fire_at=to_utc_iso(moment))
            scheduled += 1
        except Exception:  # noqa: BLE001 — one bad lead must not lose the rest
            logger.exception("todo reminders: could not schedule %s for %s", lead or "due", card_id)
    return scheduled


def _runtime_store() -> Any | None:
    """The reminder table. Returns None rather than raising — a PIM action must still
    complete when reminders are unavailable, with the failure recorded."""
    try:
        from navig.store.runtime import get_runtime_store  # noqa: PLC0415

        return get_runtime_store()
    except Exception:  # noqa: BLE001
        logger.exception("todo reminders: runtime store could not be opened")
        return None
