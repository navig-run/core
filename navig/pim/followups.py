"""Follow-ups — forgotten tasks suggested through the existing reminder/notify path.

Step 5 of the work-system mission, implemented as a thin read/deliver pass over
records that already exist:

* the task is an ordinary ``BoardStore`` todo (kind='todo'), with ``with_whom``
  naming the person and ``due_at``/``remind_before`` doing the scheduling;
* "fulfilled" is the todo being completed (it leaves ``list_todos``), so the
  existing completion path is the suppression;
* "snoozed" is a cooldown claim in ``navig.notify.dedupe`` — the same announce-once
  store the heartbeat uses, NOT a second reminder scheduler;
* delivery is ``navig.notify.dispatch("reminder", …)`` — the one dispatcher.

There is deliberately no external check here. Whether a reply was actually sent to
*with_whom* is not knowable from this process, so every suggestion says
**unverified** and offers to help; it never claims "you did not reply". Sending a
draft is a separate, authorized action owned by the messaging surface.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Callable

logger = logging.getLogger(__name__)

__all__ = [
    "COOLDOWN_SECONDS",
    "candidates",
    "check",
    "snooze",
    "suggest",
    "suggestion",
    "verification",
]

#: One suggestion per follow-up task per day-ish window. A snooze writes a claim
#: further into the future, so it extends the silence without a new store.
COOLDOWN_SECONDS = 20 * 3600

_UNVERIFIED = (
    "I can't verify whether you replied — no conversation source is connected to "
    "this check. If you haven't, I can help draft a reply."
)


def _now() -> datetime:
    from navig.pim.clock import local_now  # noqa: PLC0415

    return local_now()


def _store():  # noqa: ANN202
    from navig.store.board import get_board_store  # noqa: PLC0415

    return get_board_store()


def _due_todo(todo: dict[str, Any], now: datetime) -> bool:
    """True when the follow-up moment has arrived (no date = inbox, still due)."""
    raw = todo.get("due_at")
    if not raw:
        return True
    from navig.pim.clock import from_utc_iso  # noqa: PLC0415

    due = from_utc_iso(str(raw))
    return due is None or due <= now


def check(todo: dict[str, Any]) -> dict[str, Any]:
    """Verify the follow-up's real state. No trusted source exists → unverified.

    Kept as a function (not an inlined string) so a future messaging adapter can
    answer ``fulfilled``/``pending`` here without changing the suggestion contract.
    """
    return {"state": "unverified", "source": None, "detail": _UNVERIFIED}


def verification(todo: dict[str, Any]) -> str:
    return str(check(todo).get("state") or "unverified")


def candidates(*, store: Any = None, now: datetime | None = None) -> list[dict[str, Any]]:
    """Open todos that need a follow-up now, soonest due first.

    A follow-up is a task with a person on it (``with_whom``) whose moment has
    arrived. Completed tasks are already excluded by ``list_todos``; deleted ones
    no longer exist; there is no second table to consult.
    """
    store = store or _store()
    now = now or _now()
    out: list[dict[str, Any]] = []
    for todo in store.list_todos():
        whom = str(todo.get("with_whom") or "").strip()
        if not whom:
            continue
        if not _due_todo(todo, now):
            continue
        item = dict(todo)
        item["verification"] = check(todo)
        out.append(item)
    return out


def _when_label(todo: dict[str, Any], now: datetime) -> str:
    raw = todo.get("due_at")
    if not raw:
        return "no date"
    from navig.pim.clock import from_utc_iso, to_local  # noqa: PLC0415
    from navig.pim.dates import format_local  # noqa: PLC0415

    due = to_local(from_utc_iso(str(raw)))
    return format_local(due, now) if due else "no date"


def suggestion(todo: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """The one message this follow-up would deliver — no side effects."""
    now = now or _now()
    whom = str(todo.get("with_whom") or "").strip() or "someone"
    when = _when_label(todo, now)
    title = f"Follow up: {todo.get('title') or 'task'}"
    body = (
        f"{when} · {whom}\n\n{_UNVERIFIED}\n\n"
        "Open the task to mark it done, snooze this suggestion, or ask for a draft."
    )
    return {
        "todo_id": str(todo.get("id") or ""),
        "title": title,
        "body": body,
        "due_at": todo.get("due_at"),
        "with_whom": whom,
        "verification": check(todo),
    }


def _claim(key: str, cooldown_s: float, *, now: float | None = None) -> bool:
    from navig.notify import dedupe  # noqa: PLC0415

    return dedupe.claim(key, cooldown_s, now=now)


def _release(key: str) -> None:
    from navig.notify import dedupe  # noqa: PLC0415

    dedupe.release(key)


async def suggest(
    *,
    store: Any = None,
    now: datetime | None = None,
    limit: int = 10,
    only: str | None = None,
    dispatch: Callable[..., Any] | None = None,
    claim: Callable[..., bool] | None = None,
    release: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """Deliver at most one suggestion per follow-up, once per cooldown.

    ``dispatch``/``claim``/``release`` are injectable only so the delivery policy is
    testable; production always uses the notify router and its dedupe store.
    Returns one row per considered task with a status, never raises for a delivery
    failure (a quiet follow-up is better than a crashed tick).
    """
    store = store or _store()
    now = now or _now()
    dispatch = dispatch or _dispatch
    claim = claim or _claim
    release = release or _release

    results: list[dict[str, Any]] = []
    for todo in candidates(store=store, now=now):
        tid = str(todo.get("id") or "")
        if not tid or (only is not None and tid != only):
            continue
        if len(results) >= max(1, int(limit)):
            break
        key = f"followup:{tid}"
        if not claim(key, COOLDOWN_SECONDS):
            results.append({"todo_id": tid, "status": "suppressed", "reason": "cooldown"})
            continue
        msg = suggestion(todo, now=now)
        try:
            sent = await dispatch("reminder", msg["title"], msg["body"], data={
                "todo_id": tid,
                "kind": "followup",
                "verification": msg["verification"].get("state"),
            })
        except Exception:  # noqa: BLE001 — one failed channel must not stop the tick
            logger.exception("follow-up: dispatch failed for %s", tid)
            release(key)
            results.append({"todo_id": tid, "status": "not-delivered", "reason": "dispatch-error"})
            continue
        channels = (sent or {}).get("channels") or []
        if not channels:
            release(key)  # nothing shipped, so a later tick may try again
            results.append({"todo_id": tid, "status": "not-delivered", "reason": "no-channel"})
            continue
        results.append({
            "todo_id": tid,
            "status": "delivered",
            "channels": list(channels),
            "verification": msg["verification"],
        })
    return results


async def _dispatch(*args: Any, **kwargs: Any) -> Any:
    from navig.notify.router import dispatch as notify_dispatch  # noqa: PLC0415

    return await notify_dispatch(*args, **kwargs)


def snooze(todo_id: str, hours: float = 8, *, claim: Callable[..., bool] | None = None) -> bool:
    """Silence this follow-up for *hours* using the same cooldown row.

    Claiming with a FUTURE "now" pushes the row past the current check, so the
    regular ``suggest`` pass skips it without any task edit or reminder churn.
    """
    claim = claim or _claim
    hours = max(0.0, float(hours))
    return bool(claim(f"followup:{todo_id}", 0, now=time.time() + hours * 3600))
