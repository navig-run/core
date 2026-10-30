"""The Planner service — the ONE write path for tasks, appointments and reminders.

Telegram (DM and Business chats), the deck API (NAVIG OS + the Mini App), the CLI and
the agent tools all call this module. That is the point of it: before, every surface
wrote the board store directly and remembered — or forgot — to reschedule reminders
afterwards, and the one that forgot left a ghost ping for a task that had moved or
gone. Here every mutation ends in ``_sync`` (reminders) and ``_emit`` (live refresh).

Three things, kept apart on purpose:

* **Task** (``kind='todo'``) — something to DO. A list, a category, maybe a due date.
* **Appointment** (``kind='appointment'``) — something to ATTEND. A start time, maybe
  an end, a place or link, who with. Never shown in a to-do list.
* **Reminder** — a ping at a moment. A task or appointment schedules its own (one per
  lead time + one at the moment); a one-off ``/remindme`` has none behind it; a
  Business chat's free-text reminders live in ``telegram/biz_reminders.py``.

Lists: ``'me'`` is the operator's own; ``'tg:<chat_id>'`` is a Telegram Business
chat's shared list. A shared list's dated items ping THAT chat; everything else pings
the operator's DM.

Synchronous on purpose — SQLite underneath. Async callers (deck routes, Telegram
handlers) run it as-is; the work is a few small queries.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from navig.pim.clock import LOCAL, from_utc_iso, local_now, to_local, to_utc_iso

logger = logging.getLogger(__name__)

PERSONAL = "me"
TITLE_MAX = 200
MAX_OPEN_PER_LIST = 100
MAX_DATED_PER_BIZ_LIST = 20

#: Leads an appointment starts with when the caller names none. An appointment captured
#: from chat keeps the stronger 3d/1d/1h (see `telegram.appointments`).
APPOINTMENT_LEADS: tuple[str, ...] = ("1d", "1h")

_CATEGORY_TOKEN = re.compile(r"(?<!\w)#([\w\-]{1,32})", re.UNICODE)


class PlannerError(ValueError):
    """A request the Planner refuses — the message is safe to show the person."""


class NotFound(PlannerError):
    pass


class Forbidden(PlannerError):
    pass


# ── plumbing ─────────────────────────────────────────────────────────────────


def _store() -> Any:
    from navig.store.board import get_board_store  # noqa: PLC0415

    return get_board_store()


def _runtime() -> Any:
    from navig.store.runtime import get_runtime_store  # noqa: PLC0415

    return get_runtime_store()


_SINK: tuple[asyncio.AbstractEventLoop, Any] | None = None


def set_event_sink(loop: asyncio.AbstractEventLoop, queue: Any) -> None:
    """Register the gateway's event queue so every mutation refreshes open UIs.

    Only the gateway process registers one. Elsewhere (the worker, the CLI) ``_emit``
    is a no-op — those UIs also refresh when focused, so a missed push costs a second
    of staleness, never a wrong answer.
    """
    global _SINK
    _SINK = (loop, queue)


def _emit(kind: str, item_id: str | None = None, list_id: str | None = None) -> None:
    sink = _SINK
    if sink is None:
        return
    loop, queue = sink
    payload = {"kind": kind, "id": item_id, "list_id": list_id}
    try:
        loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(queue.emit("planner_update", payload))
        )
    except Exception:  # noqa: BLE001 — a refresh hint is never worth failing a write
        logger.debug("planner_update emit failed", exc_info=True)


def _norm_list(list_id: str | None) -> str:
    return PERSONAL if list_id in (None, "", PERSONAL) else str(list_id)


def _delivery(item: dict[str, Any]) -> tuple[int, int, str | None] | None:
    """(user_id, chat_id, bcid) a row's reminders go to, or None when nowhere.

    A shared list's TASK pings its Business chat. Everything else — personal tasks and
    every appointment — pings the operator's DM.
    """
    from navig.pim.reminders import operator_delivery  # noqa: PLC0415

    list_id = _norm_list(item.get("list_id"))
    if item.get("kind") == "todo" and list_id != PERSONAL:
        lst = _store().get_list(list_id)
        if lst and lst.get("chat_id") and lst.get("owner_id") and lst.get("bcid"):
            return int(lst["owner_id"]), int(lst["chat_id"]), str(lst["bcid"])
    delivery = operator_delivery()
    if delivery is None:
        return None
    return delivery[0], delivery[1], None


def _sync(item: dict[str, Any] | None, *, now: datetime | None = None) -> int:
    """Re-derive this row's reminders. Never raises — the edit is already saved, and
    losing it over a reminder-table hiccup would be strictly worse than a missed ping."""
    if item is None:
        return 0
    from navig.store.board import PROPOSED_ORIGINS  # noqa: PLC0415

    target = _delivery(item)
    if target is None:
        return 0
    user_id, chat_id, bcid = target
    try:
        from navig.pim.reminders import cancel_for, reschedule  # noqa: PLC0415

        if item.get("kind") == "appointment" and item.get("origin") in PROPOSED_ORIGINS:
            # A PROPOSED appointment pings no one until it is accepted. (An agent-added
            # TASK is also "proposed", but its reminders have always been scheduled —
            # it was asked for — so that rule stays.)
            cancel_for(_store(), str(item["id"]), user_id=user_id)
            return 0
        return reschedule(
            _store(), item, user_id=user_id, chat_id=chat_id, now=now or local_now(), bcid=bcid
        )
    except Exception:  # noqa: BLE001
        logger.exception("planner: could not sync reminders for %s", item.get("id"))
        return 0


def _cancel_all(item: dict[str, Any]) -> None:
    target = _delivery(item)
    if target is None:
        return
    try:
        from navig.pim.reminders import cancel_for  # noqa: PLC0415

        cancel_for(_store(), str(item["id"]), user_id=target[0])
    except Exception:  # noqa: BLE001
        logger.exception("planner: could not cancel reminders for %s", item.get("id"))


def _clean_title(title: str) -> str:
    text = re.sub(r"\s+", " ", (title or "").strip())
    if not text:
        raise PlannerError("a title is required")
    if len(text) > TITLE_MAX:
        text = text[: TITLE_MAX - 1].rstrip() + "…"
    return text


def _iso(value: Any) -> str | None:
    """Accept a datetime, an ISO string, or None; return the stored UTC ``…Z`` form."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return to_utc_iso(value if value.tzinfo else value.replace(tzinfo=LOCAL))
    raw = str(value)
    parsed = from_utc_iso(raw)
    if parsed is None:
        raise PlannerError(f"unreadable time: {raw!r}")
    if "T" in raw and not raw.endswith("Z") and "+" not in raw[10:] and "-" not in raw[10:]:
        # A naive ISO string from a form is the operator's LOCAL wall clock.
        parsed = datetime.fromisoformat(raw).replace(tzinfo=LOCAL)
    return to_utc_iso(parsed)


def _leads(leads: Any) -> list[str]:
    from navig.pim.dates import parse_lead  # noqa: PLC0415

    if leads is None:
        return []
    if isinstance(leads, str):
        leads = [leads]
    out = [str(x).strip().lower() for x in leads if str(x).strip()]
    bad = [x for x in out if parse_lead(x) is None]
    if bad:
        raise PlannerError(f"unreadable reminder lead(s) {bad} — use 3d, 1d, 1h, 15m")
    return list(dict.fromkeys(out))


# ── presentation ─────────────────────────────────────────────────────────────


def present(item: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """A row as every client receives it: the stored fields plus the brain's own
    reading of the local day. Computed HERE so a client in another time zone (the
    Mini App on a phone abroad) groups by the operator's day, not its own."""
    from navig.pim.dates import format_local  # noqa: PLC0415

    now = now or local_now()
    out = dict(item)
    due = to_local(from_utc_iso(item.get("due_at")))
    end = to_local(from_utc_iso(item.get("end_at")))
    out["due_local"] = due.isoformat() if due else None
    out["end_local"] = end.isoformat() if end else None
    out["day"] = due.date().isoformat() if due else None
    out["when_label"] = format_local(due, now) if due else ""
    out["done"] = bool(item.get("completed_at"))
    out["overdue"] = bool(due and not out["done"] and due < now)
    return out


# ── capture grammar ──────────────────────────────────────────────────────────


def parse_capture(text: str, now: datetime | None = None) -> dict[str, Any]:
    """One line as typed → ``{title, due_at, recur, category}``.

    ``#work`` anywhere picks the category (matched against names and labels, case
    insensitive; an unknown one is created). Then a recurrence and a due date come off
    the end — the same grammar `/todo`, the Mini App's quick add and a Business chat
    all use.
    """
    from navig.pim.dates import split_due, split_recurrence  # noqa: PLC0415

    now = now or local_now()
    raw = (text or "").strip()
    category = ""
    m = _CATEGORY_TOKEN.search(raw)
    if m:
        category = resolve_category(m.group(1))
        raw = (raw[: m.start()] + raw[m.end():]).strip()
    rest, recur = split_recurrence(raw)
    title, due = split_due(rest, now)
    return {
        "title": title.strip() or raw,
        "due_at": to_utc_iso(due) if due else None,
        "recur": recur,
        "category": category,
    }


def resolve_category(token: str) -> str:
    """A typed category → its stored key. Matches a key or a label; creates it if new."""
    key = (token or "").strip().lower()
    if not key:
        return ""
    store = _store()
    for cat in store.list_categories(include_archived=True):
        if cat["name"] == key or (cat.get("label") or "").strip().lower() == key:
            return str(cat["name"])
    created = store.ensure_category(key)
    return str(created["name"]) if created else key


# ── lists ────────────────────────────────────────────────────────────────────


def lists(*, include_archived: bool = False) -> list[dict[str, Any]]:
    return _store().list_lists(include_archived=include_archived)


def biz_list_id(chat_id: int) -> str:
    return f"tg:{int(chat_id)}"


def ensure_biz_list(
    chat_id: int,
    *,
    bcid: str | None,
    owner_id: int | None,
    peer_name: str = "",
    lang: str = "en",
) -> dict[str, Any]:
    """The shared list of one Business chat — created on first use, refreshed after."""
    list_id = biz_list_id(chat_id)
    item = _store().ensure_list(
        list_id,
        name=peer_name or f"Chat {chat_id}",
        peer_name=peer_name,
        chat_id=int(chat_id),
        bcid=bcid,
        owner_id=owner_id,
        lang=lang,
    )
    return item


def update_list(list_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    out = _store().update_list(list_id, fields)
    if out is None:
        raise NotFound("no such list")
    _emit("list", list_id, list_id)
    return out


# ── categories ───────────────────────────────────────────────────────────────


def categories(*, include_archived: bool = False) -> list[dict[str, Any]]:
    store = _store()
    counts = {c["name"]: c["open"] for c in store.todo_categories()}
    out = []
    for cat in store.list_categories(include_archived=include_archived):
        item = dict(cat)
        item["open"] = counts.get(cat["name"], 0)
        out.append(item)
    return out


def create_category(name: str, *, emoji: str = "", label: str | None = None, color: str = "") -> dict[str, Any]:
    key = (name or "").strip().lower()
    if not key or not re.fullmatch(r"[\w\-]{1,32}", key, re.UNICODE):
        raise PlannerError("a category name is one word of up to 32 letters, digits or '-'")
    store = _store()
    if store.get_category(key) is not None:
        raise PlannerError(f"category {key!r} already exists")
    store.ensure_category(key, emoji=emoji, label=label)
    out = store.update_category(key, {"color": color}) if color else store.get_category(key)
    _emit("category", key)
    return out  # type: ignore[return-value]


def update_category(name: str, fields: dict[str, Any]) -> dict[str, Any]:
    store = _store()
    patch = dict(fields)
    new_name = patch.pop("name", None)
    if new_name and str(new_name).strip().lower() != (name or "").strip().lower():
        if not re.fullmatch(r"[\w\-]{1,32}", str(new_name).strip().lower(), re.UNICODE):
            raise PlannerError("a category name is one word of up to 32 letters, digits or '-'")
        try:
            renamed = store.rename_category(name, str(new_name))
        except ValueError as exc:
            raise PlannerError(str(exc)) from exc
        if renamed is None:
            raise NotFound("no such category")
        name = renamed["name"]
    out = store.update_category(name, patch) if patch else store.get_category(name)
    if out is None:
        raise NotFound("no such category")
    _emit("category", out["name"])
    return out


def delete_category(name: str) -> None:
    try:
        ok = _store().delete_category(name)
    except ValueError as exc:
        raise PlannerError(str(exc)) from exc
    if not ok:
        raise NotFound("no such category")
    _emit("category", name)


def reorder_categories(names: list[str]) -> list[dict[str, Any]]:
    out = _store().reorder_categories(names)
    _emit("category", None)
    return out


# ── tasks ────────────────────────────────────────────────────────────────────


def tasks(
    *, list_id: str | None = PERSONAL, category: str | None = None, include_done: bool = False
) -> list[dict[str, Any]]:
    now = local_now()
    rows = _store().list_todos(
        list_id=None if list_id in (None, "*") else _norm_list(list_id),
        category=category or None,
        include_done=include_done,
    )
    return [present(r, now) for r in rows]


def get(item_id: str) -> dict[str, Any]:
    item = _store().get_todo(item_id)
    if item is None:
        raise NotFound("no such item")
    return item


def by_seq(list_id: str, seq: int) -> dict[str, Any] | None:
    return _store().get_by_seq(_norm_list(list_id), int(seq))


def create_task(
    text: str | None = None,
    *,
    title: str | None = None,
    list_id: str | None = PERSONAL,
    category: str | None = None,
    due_at: Any = None,
    leads: Any = None,
    recur: str | None = None,
    notes: str = "",
    priority: str = "normal",
    created_by: int | None = None,
    origin: str = "manual",
    origin_ref: str | None = None,
    space: str | None = None,
) -> dict[str, Any] | None:
    """Add a task. ``text`` is typed language ("Pay rent friday #life"); ``title`` and
    the keyword fields are structured input. Returns None when ``origin_ref`` was
    already captured (an idempotent re-delivery), else the new task."""
    from navig.pim.dates import RECURRENCES  # noqa: PLC0415

    store = _store()
    if origin_ref and store.todo_exists_for_source(origin_ref):
        return None
    fields: dict[str, Any] = {"title": title or "", "due_at": None, "recur": recur, "category": ""}
    if text:
        fields.update(parse_capture(text))
    if title:
        fields["title"] = title
    if due_at not in (None, ""):
        fields["due_at"] = _iso(due_at)
    if category:
        fields["category"] = resolve_category(category)
    if fields.get("recur") and fields["recur"] not in RECURRENCES:
        raise PlannerError(f"repeat must be one of {', '.join(RECURRENCES)}")
    list_key = _norm_list(list_id)
    if list_key != PERSONAL and store.get_list(list_key) is None:
        raise NotFound("no such list")
    open_now = store.list_todos(list_id=list_key)
    if len(open_now) >= MAX_OPEN_PER_LIST:
        raise PlannerError(f"this list already has {MAX_OPEN_PER_LIST} open items")
    if (
        list_key != PERSONAL
        and fields["due_at"]
        and sum(1 for t in open_now if t.get("due_at")) >= MAX_DATED_PER_BIZ_LIST
    ):
        raise PlannerError(f"this chat already has {MAX_DATED_PER_BIZ_LIST} dated items")
    item = store.create_todo(
        _clean_title(fields["title"]),
        category=fields["category"],
        due_at=fields["due_at"],
        notes=notes or "",
        priority=priority,
        recur=fields.get("recur"),
        remind_before=_leads(leads),
        origin=origin,
        origin_ref=origin_ref,
        list_id=list_key,
        created_by=created_by,
        space=(str(space).strip() or None) if space else None,
    )
    _sync(item)
    _emit("task", item["id"], list_key)
    return item


_TASK_FIELDS = {"title", "notes", "priority", "due_at", "category", "recur", "remind_before", "origin", "space"}


def update(item_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Edit a task OR an appointment. Unknown fields are ignored; ``kind`` and the
    list number can never be changed here."""
    from navig.pim.dates import RECURRENCES  # noqa: PLC0415

    store = _store()
    current = get(item_id)
    allowed = set(_TASK_FIELDS)
    if current.get("kind") == "appointment":
        allowed |= {"end_at", "location", "with_whom"}
    patch = {k: v for k, v in (fields or {}).items() if k in allowed}
    if "title" in patch:
        patch["title"] = _clean_title(str(patch["title"]))
    for key in ("due_at", "end_at"):
        if key in patch:
            patch[key] = _iso(patch[key])
    if "category" in patch:
        patch["category"] = resolve_category(str(patch["category"] or "")) if patch["category"] else ""
    if "remind_before" in patch:
        patch["remind_before"] = _leads(patch["remind_before"])
    if patch.get("recur") and patch["recur"] not in RECURRENCES:
        raise PlannerError(f"repeat must be one of {', '.join(RECURRENCES)}")
    if not patch:
        return current
    item = store.update_todo(item_id, patch)
    _sync(item)
    _emit(str(current.get("kind")), item_id, current.get("list_id"))
    return item  # type: ignore[return-value]


def complete(item_id: str) -> dict[str, Any]:
    item = _store().complete_todo(item_id)
    if item is None:
        raise NotFound("no such item")
    _sync(item)  # a recurring one comes back open with a new date and new pings
    _emit("done", item_id, item.get("list_id"))
    return item


def reopen(item_id: str) -> dict[str, Any]:
    item = _store().reopen_todo(item_id)
    if item is None:
        raise NotFound("no such item")
    _sync(item)
    _emit("reopen", item_id, item.get("list_id"))
    return item


def delete(item_id: str, *, actor_is_owner: bool = True, dismiss: bool = True) -> None:
    """Remove a task or appointment and every reminder it scheduled.

    In a shared list only the owner may delete — the contact can add and tick, which
    is everything they need, and nothing they do can lose the owner's data.
    ``dismiss=False`` rolls back a suggestion that never reached the operator, so
    its source may propose it again.
    """
    item = get(item_id)
    if not actor_is_owner:
        raise Forbidden("only the owner can delete")
    _cancel_all(item)  # BEFORE the delete: the links cascade away with the row
    _store().delete_todo(item_id, dismiss=dismiss)
    _emit("delete", item_id, item.get("list_id"))


def move(item_id: str, list_id: str) -> dict[str, Any]:
    store = _store()
    item = get(item_id)
    target = _norm_list(list_id)
    if target != PERSONAL and store.get_list(target) is None:
        raise NotFound("no such list")
    _cancel_all(item)  # its pings were addressed to the OLD list's chat
    moved = store.move_to_list(item_id, target)
    _sync(moved)
    _emit("move", item_id, target)
    return moved  # type: ignore[return-value]


def clear_done(list_id: str) -> int:
    store = _store()
    done = [t for t in store.list_todos(list_id=_norm_list(list_id), include_done=True) if t.get("completed_at")]
    for t in done:
        store.delete_todo(t["id"])
    if done:
        _emit("clear", None, _norm_list(list_id))
    return len(done)


# ── agenda ───────────────────────────────────────────────────────────────────


def agenda(
    *, start: datetime | None = None, end: datetime | None = None, include_done: bool = False,
    proposed: bool | None = None,
) -> list[dict[str, Any]]:
    from navig.store.board import PROPOSED_ORIGINS  # noqa: PLC0415

    now = local_now()
    rows = _store().list_appointments(
        start=to_utc_iso(start) if start else None,
        end=to_utc_iso(end) if end else None,
        include_done=include_done,
    )
    if proposed is not None:
        rows = [r for r in rows if (r.get("origin") in PROPOSED_ORIGINS) is proposed]
    return [present(r, now) for r in rows]


def create_appointment(
    title: str,
    start_at: Any,
    *,
    end_at: Any = None,
    location: str | None = None,
    with_whom: str | None = None,
    notes: str = "",
    leads: Any = APPOINTMENT_LEADS,
    list_id: str | None = PERSONAL,
    origin: str = "manual",
    origin_ref: str | None = None,
    created_by: int | None = None,
) -> dict[str, Any] | None:
    """Add an appointment. ``origin='ai'`` makes it a PROPOSAL: listed for the operator
    to accept, scheduling nothing until they do. Returns None for a repeat origin_ref."""
    store = _store()
    if origin_ref and store.todo_exists_for_source(origin_ref):
        return None
    start_iso = _iso(start_at)
    if not start_iso:
        raise PlannerError("an appointment needs a start time")
    end_iso = _iso(end_at)
    if end_iso and end_iso < start_iso:
        raise PlannerError("an appointment cannot end before it starts")
    item = store.create_todo(
        _clean_title(title),
        kind="appointment",
        due_at=start_iso,
        end_at=end_iso,
        location=(location or "").strip() or None,
        with_whom=(with_whom or "").strip() or None,
        notes=notes or "",
        remind_before=_leads(leads),
        origin=origin,
        origin_ref=origin_ref,
        list_id=_norm_list(list_id),
        created_by=created_by,
    )
    _sync(item)
    _emit("appointment", item["id"], item.get("list_id"))
    return item


def accept(item_id: str) -> dict[str, Any]:
    """A proposal becomes a real appointment — and its reminders begin."""
    item = _store().update_todo(item_id, {"origin": "manual"})
    if item is None:
        raise NotFound("no such item")
    _sync(item)
    _emit("accept", item_id, item.get("list_id"))
    return item


# ── reminders ────────────────────────────────────────────────────────────────


def reminders(*, limit: int = 100) -> list[dict[str, Any]]:
    """Every upcoming ping, from every source, soonest first.

    ``source='runtime'`` rows are the reminder table — a task's or appointment's leads
    (``card_id`` set) and one-off ``/remindme`` pings. ``source='biz'`` rows are a
    Business chat's free-text reminders.
    """
    from navig.pim.dates import describe_lead, format_local  # noqa: PLC0415

    now = local_now()
    out: list[dict[str, Any]] = []
    try:
        rows = _runtime().list_upcoming(limit)
    except Exception:  # noqa: BLE001
        logger.exception("planner: reminder table unreadable")
        rows = []
    links: dict[int, dict[str, Any]] = {}
    try:
        links = _store().reminder_links([int(r["id"]) for r in rows])
    except Exception:  # noqa: BLE001
        logger.exception("planner: reminder links unreadable")
    for r in rows:
        at = to_local(from_utc_iso(str(r.get("remind_at") or "")))
        if at is None:
            continue
        link = links.get(int(r["id"])) or {}
        lead = str(link.get("lead") or "")
        message = str(r.get("message") or "").strip().removeprefix("⏰").strip()
        out.append({
            "source": "runtime",
            "id": str(r["id"]),
            "at": at.isoformat(),
            "day": at.date().isoformat(),
            "when_label": format_local(at, now),
            "title": str(link.get("title") or "") or message,
            "message": message,
            "card_id": link.get("card_id"),
            "lead": lead,
            "lead_label": describe_lead(lead) if lead and lead != "snooze" else "",
            "chat": "business" if r.get("bcid") else "dm",
        })
    try:
        from navig.telegram import biz_reminders  # noqa: PLC0415

        for b in biz_reminders.list_all():
            at = to_local(datetime.fromisoformat(str(b["due"])))
            if at is None:
                continue
            out.append({
                "source": "biz",
                "id": str(b["id"]),
                "at": at.isoformat(),
                "day": at.date().isoformat(),
                "when_label": format_local(at, now),
                "title": str(b.get("about") or ""),
                "message": str(b.get("about") or ""),
                "card_id": None,
                "lead": "",
                "lead_label": "",
                "chat": "business",
                "chat_title": b.get("chat_title") or "",
            })
    except Exception:  # noqa: BLE001
        logger.debug("planner: business reminders unavailable", exc_info=True)
    out.sort(key=lambda r: r["at"])
    return out[:limit]


def add_reminder(text: str) -> dict[str, Any]:
    """A one-off ping to the operator's DM, from typed text ("in 30 min call mom")."""
    from navig.pim.dates import extract_when  # noqa: PLC0415
    from navig.pim.reminders import operator_delivery  # noqa: PLC0415

    now = local_now()
    found = extract_when(text or "", now)
    if found is None:
        raise PlannerError("no time found — try “in 30 min call mom” or “tomorrow 9:00 dentist”")
    when, message = found
    if when <= now:
        raise PlannerError("that time has already passed")
    delivery = operator_delivery()
    if delivery is None:
        raise PlannerError("no Telegram chat is configured to deliver reminders to")
    rid = _runtime().create_reminder(delivery[0], delivery[1], message, when)
    _emit("reminder", str(rid))
    return {"source": "runtime", "id": str(rid), "at": to_local(when).isoformat(), "message": message}  # type: ignore[union-attr]


def cancel_reminder(source: str, rid: str) -> None:
    """Stop one ping.

    A task's LEAD ping ("3 days before") is switched off on the task itself — cancelling
    only the row would bring it back the next time the task is edited. The ping AT the
    moment, a snooze, or a one-off is simply cancelled.
    """
    if source == "biz":
        from navig.telegram import biz_reminders  # noqa: PLC0415

        if not biz_reminders.cancel(rid):
            raise NotFound("no such reminder")
        _emit("reminder", rid)
        return
    runtime = _runtime()
    row = runtime.get_reminder(int(rid))
    if row is None or row.get("completed"):
        raise NotFound("no such reminder")
    link = _store().reminder_links([int(rid)]).get(int(rid))
    lead = str((link or {}).get("lead") or "")
    if link and lead and lead != "snooze":
        item = _store().get_todo(str(link["card_id"]))
        if item is not None:
            leads = [x for x in (item.get("remind_before") or []) if x != lead]
            update(item["id"], {"remind_before": leads})
            return
    runtime.cancel_reminder(int(rid), int(row["user_id"]))
    _emit("reminder", rid)


_SNOOZE_MAX = 7 * 86400


def snooze_reminder(source: str, rid: str, seconds: int, *, channel: Any = None) -> dict[str, Any]:
    """Push a ping later by ``seconds`` (60 s … 7 days)."""
    seconds = int(seconds)
    if not 60 <= seconds <= _SNOOZE_MAX:
        raise PlannerError("snooze between 1 minute and 7 days")
    when = local_now() + timedelta(seconds=seconds)
    if source == "biz":
        from navig.telegram import biz_reminders  # noqa: PLC0415

        item = biz_reminders.reschedule(channel, rid, when)
        if item is None:
            raise NotFound("no such reminder")
        _emit("reminder", rid)
        return {"source": "biz", "id": rid, "at": when.isoformat()}
    runtime = _runtime()
    row = runtime.get_reminder(int(rid))
    if row is None or row.get("completed"):
        raise NotFound("no such reminder")
    kwargs = {"bcid": row["bcid"]} if row.get("bcid") else {}
    new_id = runtime.create_reminder(
        int(row["user_id"]), int(row["chat_id"]), str(row["message"]), when, **kwargs
    )
    link = _store().reminder_links([int(rid)]).get(int(rid))
    if link:
        _store().link_reminder(str(link["card_id"]), int(new_id), lead="snooze", fire_at=to_utc_iso(when))
    runtime.cancel_reminder(int(rid), int(row["user_id"]))
    _emit("reminder", str(new_id))
    return {"source": "runtime", "id": str(new_id), "at": when.isoformat()}


# ── today ────────────────────────────────────────────────────────────────────


def today(now: datetime | None = None) -> dict[str, Any]:
    """The glance: what is late, what is today, what is coming up."""
    now = now or local_now()
    end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)
    store = _store()
    open_tasks = store.list_todos()
    overdue, due_today = [], []
    inbox = 0
    for t in open_tasks:
        due = to_local(from_utc_iso(t.get("due_at")))
        if due is None:
            if _norm_list(t.get("list_id")) == PERSONAL:
                inbox += 1
            continue
        if due < now:
            overdue.append(present(t, now))
        elif due <= end_of_day:
            due_today.append(present(t, now))
    upcoming = agenda(start=now, end=now + timedelta(days=7))
    from navig.store.board import PROPOSED_ORIGINS  # noqa: PLC0415

    return {
        "now": now.isoformat(),
        "day": now.date().isoformat(),
        "overdue": overdue,
        "today": due_today,
        "inbox_count": inbox,
        "appointments": [a for a in upcoming if a.get("origin") not in PROPOSED_ORIGINS],
        "proposals": [a for a in upcoming if a.get("origin") in PROPOSED_ORIGINS],
        "lists": lists(),
    }


# ── Legacy import ────────────────────────────────────────────────────────────
# The Mini App / OS "Tasks" app wrote a flat ~/.navig/tasks.json before the Planner
# existed. Fold those into the personal list ONCE, keyed by a stable origin_ref so a
# second run adds nothing, then retire the file to <name>.imported so it is read once.
LEGACY_TASKS_FILE = "tasks.json"
_LEGACY_REF = "legacy:tasks:"


def _legacy_tasks_path():
    from navig.platform.paths import config_dir  # noqa: PLC0415

    return config_dir() / LEGACY_TASKS_FILE


def import_legacy_tasks() -> dict[str, Any]:
    """Idempotently import ``~/.navig/tasks.json`` into the personal list.

    Each legacy row becomes a personal task tagged ``legacy:tasks:<id>`` — the same
    idempotency key the shared-chat and proposal paths use — so re-running imports
    nothing new. A done row is created then completed. When the file is fully
    processed it is renamed to ``tasks.json.imported`` (never deleted; the data is
    the operator's), so the next call is a no-op. Returns a small report. Never
    raises on a single malformed row — that row is skipped, the rest still land."""
    import json  # noqa: PLC0415

    path = _legacy_tasks_path()
    report = {"found": 0, "imported": 0, "completed": 0, "skipped": 0, "path": None}
    if not path.exists():
        return report
    try:
        raw = json.loads(path.read_text(encoding="utf-8") or "[]")
    except (OSError, json.JSONDecodeError) as exc:
        # A transient/locked/corrupt read must NOT become "0 tasks, file retired" —
        # that would silently drop the operator's list. Surface it, leave the file.
        raise PlannerError(f"could not read {path.name}: {exc}") from exc
    rows = raw if isinstance(raw, list) else []
    report["found"] = len(rows)
    for row in rows:
        if not isinstance(row, dict):
            report["skipped"] += 1
            continue
        tid = str(row.get("id") or "").strip()
        title = (row.get("title") or "").strip()
        if not tid or not title:
            report["skipped"] += 1
            continue
        ref = f"{_LEGACY_REF}{tid}"
        try:
            item = create_task(title=title, due_at=row.get("due_at") or None, origin_ref=ref)
        except PlannerError:
            # A bad due_at (or any one row's problem) must not sink the whole import:
            # keep the task, just dateless.
            item = create_task(title=title, origin_ref=ref)
        if item is None:
            report["skipped"] += 1  # already imported on an earlier run
            continue
        report["imported"] += 1
        if row.get("done"):
            try:
                complete(item["id"])
                report["completed"] += 1
            except PlannerError:
                pass
    # Retire the file only after a clean pass, so a crash mid-import re-runs safely
    # (every landed row is a no-op the next time via its origin_ref).
    target = path.with_name(path.name + ".imported")
    try:
        path.replace(target)
        report["path"] = str(target)
    except OSError:
        report["path"] = None
    return report
