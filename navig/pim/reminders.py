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

__all__ = [
    "cancel_for",
    "cancel_for_operator",
    "delivery_suppressed",
    "operator_delivery",
    "planned_fires",
    "reminder_text",
    "reschedule",
    "sync_for_operator",
]


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

    The links are read first and dropped LAST, and not at all when the reminder table
    could not be opened. A link is the only record that a reminder row belongs to a
    todo, so deleting it before the row is gone turns a retryable failure into a ping
    nobody can trace back to anything. `board_todo_reminder.card_id` cascades on
    delete, so links kept here still disappear with the card itself.
    """
    try:
        ids = store.todo_reminder_ids(card_id)
    except Exception:  # noqa: BLE001 — cancellation must never break the calling action
        logger.exception("todo reminders: could not read links for %s", card_id)
        return 0
    if not ids:
        return 0

    runtime = _runtime_store()
    if runtime is None:
        # Keep the links: the rows are still out there, and the links are how a later
        # edit or delete finds them. Unlinking here would strand them permanently.
        logger.error(
            "todo reminders: %d row(s) for %s could not be cancelled — the runtime "
            "store is unavailable, so they will still fire",
            len(ids),
            card_id,
        )
        return 0

    cancelled = 0
    survivors: list[int] = []
    for rid in ids:
        try:
            if runtime.cancel_reminder(int(rid), int(user_id)):
                cancelled += 1
            else:
                survivors.append(int(rid))
        except Exception:  # noqa: BLE001
            logger.exception("todo reminders: could not cancel %s", rid)
            survivors.append(int(rid))

    if survivors:
        # `cancel_reminder` is scoped by user_id, so "no such row" and "that row is
        # someone else's" look identical from here. Either way these will fire for a
        # task that has moved or gone, and a silent 0 is how that becomes unfindable.
        logger.warning(
            "todo reminders: %d of %d row(s) for %s did not cancel under user %s "
            "(ids: %s) — they may still fire",
            len(survivors),
            len(ids),
            card_id,
            user_id,
            ", ".join(str(r) for r in survivors),
        )

    try:
        store.unlink_reminders(card_id)
    except Exception:  # noqa: BLE001
        logger.exception("todo reminders: could not drop links for %s", card_id)
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

    if _extension_is_off():
        # The same rule habits follow (`cron_service` skips writing the row while the
        # habits extension is off): an extension you switched off goes quiet. Doing it
        # HERE rather than at delivery means no row is created at all, so there is
        # nothing to leak — and the cancel above has already cleared what was there.
        logger.info(
            "todo reminders: the Todo extension is off — %s keeps its date but schedules nothing",
            card_id,
        )
        return 0

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


def operator_delivery() -> tuple[int, int] | None:
    """``(user_id, chat_id)`` for the operator's own Telegram chat, or None.

    The CLI and the agent tools have no chat of their own, but the reminders they
    must cancel were created with one — and `RuntimeStore.cancel_reminder` is scoped
    by `user_id`, so a surface that cannot name the operator cannot cancel anything.

    Resolved through :func:`navig.messaging.notify_operator.resolve_operator_chat_id`
    — the same `telegram.allowed_users[0]` rule the gateway's own boot message and the
    habit check-ins use, so every process agrees on who the operator is. In a private
    chat the user id and the chat id are the same number, which is exactly what the
    `/todo` command assumes when it schedules.
    """
    try:
        from navig.messaging.notify_operator import resolve_operator_chat_id  # noqa: PLC0415

        raw = resolve_operator_chat_id()
    except Exception:  # noqa: BLE001 — no Telegram is a normal state, not an error
        logger.debug("todo reminders: operator chat could not be resolved", exc_info=True)
        return None
    if not raw:
        return None
    try:
        ident = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("todo reminders: operator chat id %r is not a number", raw)
        return None
    return ident, ident


def sync_for_operator(store: Any, todo: dict[str, Any] | None, *, now: datetime | None = None) -> int:
    """:func:`reschedule` for a surface with no chat of its own (CLI, agent tools).

    Returns how many reminders are now scheduled; 0 when no Telegram is configured,
    which is an ordinary state on a machine that only uses the CLI — there is nowhere
    to deliver a ping, so there is nothing to schedule and nothing that can be ghosted.

    Never raises. A reminder is a side effect of the edit, and losing the edit because
    the reminder table hiccupped would be a strictly worse outcome than a missed ping.
    """
    if todo is None:
        return 0
    delivery = operator_delivery()
    if delivery is None:
        logger.debug(
            "todo reminders: no Telegram configured — %s keeps its schedule but pings nowhere",
            todo.get("id"),
        )
        return 0
    user_id, chat_id = delivery
    try:
        from navig.pim.clock import local_now  # noqa: PLC0415

        return reschedule(
            store, todo, user_id=user_id, chat_id=chat_id, now=now or local_now()
        )
    except Exception:  # noqa: BLE001
        logger.exception("todo reminders: could not resync %s", todo.get("id"))
        return 0


def cancel_for_operator(store: Any, card_id: str) -> int:
    """:func:`cancel_for` for a surface with no chat of its own. Never raises.

    Call it BEFORE deleting the todo: the links cascade away with the card, so a
    delete that runs first takes the only record of what to cancel with it.
    """
    delivery = operator_delivery()
    if delivery is None:
        return 0
    try:
        return cancel_for(store, card_id, user_id=delivery[0])
    except Exception:  # noqa: BLE001
        logger.exception("todo reminders: could not cancel for %s", card_id)
        return 0


def delivery_suppressed(reminder_id: int) -> bool:
    """Should this due reminder be held back because the Todo extension is off?

    True only for a reminder that belongs to a TODO, and only while the extension is
    off. :func:`reschedule` already refuses to write new rows in that state; this
    covers the ones written BEFORE the switch was flipped, which would otherwise keep
    arriving from an extension the operator had just turned off.

    Held, not discarded: the row stays due, so switching the extension back on the
    same day still delivers it (the poller's own 24-hour staleness rule retires
    anything older). Fails OPEN — a reminder whose provenance cannot be read is
    delivered, because a missed ping is worse than one from a disabled surface.
    """
    if not _extension_is_off():
        return False
    try:
        from navig.store.board import get_board_store  # noqa: PLC0415

        return bool(get_board_store().reminder_is_todo(int(reminder_id)))
    except Exception:  # noqa: BLE001
        logger.debug("todo reminders: could not classify reminder %s", reminder_id, exc_info=True)
        return False


def _extension_is_off() -> bool:
    """True when the Todo extension is switched off. Fails OPEN (False).

    A gate that cannot be read must not silently stop the operator's reminders — the
    failure mode of guessing "off" here is the exact ghost this module exists to
    prevent, in reverse: a task that was supposed to ping and never did.
    """
    try:
        from navig.telegram.todo_actions import extension_is_off  # noqa: PLC0415

        return bool(extension_is_off())
    except Exception:  # noqa: BLE001
        logger.debug("todo reminders: extension gate unreadable — scheduling anyway", exc_info=True)
        return False


def _runtime_store() -> Any | None:
    """The reminder table. Returns None rather than raising — a PIM action must still
    complete when reminders are unavailable, with the failure recorded."""
    try:
        from navig.store.runtime import get_runtime_store  # noqa: PLC0415

        return get_runtime_store()
    except Exception:  # noqa: BLE001
        logger.exception("todo reminders: runtime store could not be opened")
        return None
