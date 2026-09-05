"""Group members → your Telegram contact list, marked so a cohort stays findable.

Two jobs:

* **Read** the participant list of any group you can see (``list_members``) and
  your own saved contacts (``list_contacts``).
* **Write** members into your contacts carrying a marker (``tag_members``), and
  undo it again (``undo_run``).

The marker can go in either of two places, chosen with ``mark``:

``surname``
    Appends the marker to the saved surname — ``Popov`` → ``Popov MTP``. Ugly,
    and **the only form Telegram can search**: typing "MTP" into contact search
    matches names, so this is what makes a cohort findable in the app.
``note``
    Telegram's own private per-contact note — ``contacts.addContact`` takes a
    ``note`` field and ``contacts.updateContactNote`` edits it. It holds a
    templated line like ``MTP · vibe_sud_france · 2026-08-21``, is capped at
    :data:`NOTE_LENGTH_LIMIT` characters, and iOS labels it "notes only visible
    to you". It leaves the real name untouched and carries the source group and
    date, which a one-word suffix cannot — but **it is not searchable**.
``both`` (default)
    The surname marker for finding people, the note for knowing where they came
    from. Default because either one alone loses something that matters.

Why ``note`` alone is not the default, despite being the tidier field: in
tdesktop, ``UserData::note()`` is read in four places and every one of them is
display or edit UI. It never reaches ``_nameWords``, the index contact search
actually queries. A marker only in the note is invisible exactly where you look
for it — verified the hard way on a real account.

What shapes the write side:

1. It is a live-account mutation, so it is **dry-run by default**; nothing is
   written without ``confirm=True``.
2. Every write is journalled *before the next one is attempted*, so a crash,
   a Ctrl-C or an abort still leaves an exact undo record.
3. ``PEER_FLOOD`` aborts the run rather than retrying. Reading Telegram's own
   clients suggests this is belt-and-braces rather than the live wire —
   ``PeerFloodType`` is ``{Send, InviteGroup, InviteChannel}`` and tdesktop's
   ``contacts.addContact`` call has no failure handler at all — so the policed
   operations are messaging strangers and mass group-inviting, not filing
   someone in your address book. Generic ``FLOOD_WAIT`` can still hit any
   method, which is what :data:`DEFAULT_DELAY` is actually for.

``add_phone_privacy_exception`` is hard-wired to ``False``: adding someone to
your contacts must never hand them your phone number.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .user_client import UserClient

logger = logging.getLogger(__name__)

DEFAULT_TAG = "MTP"

#: Where the marker is written. ``both`` is the default because Telegram's
#: contact search indexes **names only** — see :data:`NOTE_LENGTH_LIMIT`'s
#: neighbours and the module docstring. A marker written only to the note is
#: invisible in the one place people actually look for it.
MARKS = ("note", "surname", "both")
DEFAULT_MARK = "both"

#: Placeholders: ``{tag}`` ``{group}`` ``{date}``.
DEFAULT_NOTE_TEMPLATE = "{tag} · {group} · {date}"

#: ``contact_note_length_limit`` — the value tdesktop falls back to when the
#: server does not ship the key (which it does not, for this account).
NOTE_LENGTH_LIMIT = 128

#: Seconds between two contact writes. Not a ``PEER_FLOOD`` defence — see the
#: module docstring; that error belongs to messaging and inviting. This is
#: ordinary politeness against generic ``FLOOD_WAIT`` rate limiting.
DEFAULT_DELAY = 5.0

#: A ``FLOOD_WAIT`` longer than this is treated as "stop", not "sleep".
MAX_FLOOD_WAIT = 300


# ── journal ──────────────────────────────────────────────────────────────────


def journal_path() -> Path:
    from navig.memory.paths import navig_home
    return navig_home() / "telegram" / "contact_runs.jsonl"


def _append_journal(record: dict) -> None:
    p = journal_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        fh.flush()


def read_journal(run_id: str | None = None) -> list[dict]:
    """Journal records, newest run last. ``run_id`` filters to one run."""
    p = journal_path()
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue  # a torn last line must not hide the rest of the history
        if run_id and rec.get("run_id") != run_id:
            continue
        out.append(rec)
    return out


def list_runs() -> list[dict]:
    """Summarise each run in the journal → ``[{run_id, started, chat, written}]``."""
    runs: dict[str, dict] = {}
    for rec in read_journal():
        rid = rec.get("run_id")
        if not rid:
            continue
        r = runs.setdefault(rid, {
            "run_id": rid, "started": rec.get("at"), "chat": rec.get("chat"),
            "tag": rec.get("tag"), "written": 0, "pruned": 0, "undone": 0,
        })
        if rec.get("kind") == "write":
            r["written"] += 1
        elif rec.get("kind") == "prune":
            r["pruned"] = r.get("pruned", 0) + 1
        elif rec.get("kind") == "undo":
            r["undone"] += 1
        r["chat"] = r["chat"] or rec.get("chat")
    return list(runs.values())


# ── tag helpers (pure) ───────────────────────────────────────────────────────


def _has_tag(value: str | None, tag: str) -> bool:
    if not value:
        return False
    return re.search(rf"(?:^|\s){re.escape(tag)}(?:\s|$)", value.strip()) is not None


def is_tagged(first: str | None, last: str | None, tag: str = DEFAULT_TAG) -> bool:
    """True when the marker already appears as a whole word in either name."""
    return _has_tag(last, tag) or _has_tag(first, tag)


def apply_tag(last: str | None, tag: str = DEFAULT_TAG) -> str:
    """Append the marker to a surname, idempotently.

    ``"Popov" → "Popov MTP"`` · ``"" → "MTP"`` · ``"Popov MTP" → "Popov MTP"``.
    """
    last = (last or "").strip()
    if not last:
        return tag
    if _has_tag(last, tag):
        return last
    return f"{last} {tag}"


def strip_tag(last: str | None, tag: str = DEFAULT_TAG) -> str:
    """Remove the marker from a surname (used by undo on pre-existing contacts)."""
    last = (last or "").strip()
    if not last:
        return ""
    out = re.sub(rf"(?:^|\s){re.escape(tag)}(?=\s|$)", " ", last)
    return re.sub(r"\s+", " ", out).strip()


def render_note(
    template: str = DEFAULT_NOTE_TEMPLATE,
    *,
    tag: str = DEFAULT_TAG,
    group: str = "",
    date: str = "",
    limit: int = NOTE_LENGTH_LIMIT,
) -> str:
    """Fill a note template and clamp it to Telegram's length limit.

    An over-long note is truncated rather than sent: the server rejects the
    whole ``addContact`` call for an oversized note, which would fail the write
    instead of just the annotation.
    """
    text = template.format(tag=tag, group=str(group).lstrip("@"), date=date).strip()
    if len(text) > limit:
        text = text[: max(0, limit - 1)].rstrip() + "…"
    return text


# ── planning (pure — no network, so it is testable) ──────────────────────────

#: Every member lands in exactly one bucket.
ACTIONS = ("add", "update", "already-tagged", "existing-untagged", "bot",
           "deleted", "self")


def plan_tagging(
    members: Iterable[dict],
    contacts_by_id: dict[int, dict],
    *,
    me_id: int,
    tag: str = DEFAULT_TAG,
    mark: str = DEFAULT_MARK,
    note: str = "",
    notes_by_id: dict[int, str] | None = None,
    update_existing: bool = False,
    include_bots: bool = False,
) -> list[dict]:
    """Decide what would happen to each member. Pure; no side effects.

    ``members`` are participant dicts from :func:`list_members`; ``contacts_by_id``
    maps user id → your *saved* contact record (saved names, not profile names);
    ``notes_by_id`` maps user id → their existing contact note, and is what makes
    "already marked" detectable in ``note`` mode. Without it, a person whose note
    already carries the marker is reported as ``existing-untagged`` rather than
    ``already-tagged`` — a needless rewrite, never a wrong name.

    Returns one row per member with an ``action`` from :data:`ACTIONS`.
    """
    if mark not in MARKS:
        raise ValueError(f"mark must be one of {MARKS}, got {mark!r}")
    notes_by_id = notes_by_id or {}
    writes_name = mark in ("surname", "both")
    writes_note = mark in ("note", "both")

    plan: list[dict] = []
    for m in members:
        uid = m["user_id"]
        row = {
            "user_id": uid,
            "username": m.get("username"),
            "profile_first": m.get("first_name"),
            "profile_last": m.get("last_name"),
        }
        if uid == me_id:
            plan.append({**row, "action": "self"})
            continue
        if m.get("deleted"):
            plan.append({**row, "action": "deleted"})
            continue
        if m.get("is_bot") and not include_bots:
            plan.append({**row, "action": "bot"})
            continue

        saved = contacts_by_id.get(uid)
        if saved is None:
            profile_last = (m.get("last_name") or "").strip()
            plan.append({
                **row, "action": "add",
                "before_first": None, "before_last": None, "before_note": None,
                "new_first": (m.get("first_name") or "").strip() or (m.get("username") or str(uid)),
                "new_last": apply_tag(profile_last, tag) if writes_name else profile_last,
                "new_note": note if writes_note else None,
            })
            continue

        # Already a contact: the saved name wins over the profile name, so a
        # name you curated is never overwritten by whatever they call themselves
        # today.
        s_first = (saved.get("first_name") or "").strip()
        s_last = (saved.get("last_name") or "").strip()
        s_note = (notes_by_id.get(uid) or "").strip()
        name_marked = is_tagged(s_first, s_last, tag)
        note_marked = _has_tag(s_note, tag)
        marked = ((name_marked and note_marked) if mark == "both"
                  else note_marked if mark == "note" else name_marked)
        if marked:
            plan.append({**row, "action": "already-tagged", "saved_first": s_first,
                         "saved_last": s_last, "saved_note": s_note or None})
            continue
        if not update_existing:
            plan.append({**row, "action": "existing-untagged", "saved_first": s_first,
                         "saved_last": s_last, "saved_note": s_note or None})
            continue
        plan.append({
            **row, "action": "update",
            "before_first": s_first, "before_last": s_last, "before_note": s_note or None,
            "new_first": s_first or (m.get("first_name") or "").strip() or str(uid),
            "new_last": apply_tag(s_last, tag) if writes_name else s_last,
            "new_note": note if writes_note else None,
        })
    return plan


def summarize_plan(plan: list[dict]) -> dict[str, int]:
    counts = dict.fromkeys(ACTIONS, 0)
    for row in plan:
        counts[row["action"]] = counts.get(row["action"], 0) + 1
    return counts


# ── reads ────────────────────────────────────────────────────────────────────


def _user_row(u: Any) -> dict:
    return {
        "user_id": int(u.id),
        "username": getattr(u, "username", None),
        "first_name": getattr(u, "first_name", None),
        "last_name": getattr(u, "last_name", None),
        "phone": getattr(u, "phone", None),
        "is_bot": bool(getattr(u, "bot", False)),
        "deleted": bool(getattr(u, "deleted", False)),
        "is_contact": bool(getattr(u, "contact", False)),
        "premium": bool(getattr(u, "premium", False)),
    }


async def _members(client, chat: str | int, *, limit: int | None = None) -> list[dict]:
    from .media import resolve_entity
    entity = await resolve_entity(client, chat)
    rows: list[dict] = []
    async for u in client.iter_participants(entity, limit=limit):
        rows.append(_user_row(u))
    return rows


async def list_members(chat: str | int, *, limit: int | None = None) -> list[dict]:
    """Every participant of ``chat`` you are allowed to see.

    Raises whatever Telegram raises when the list is hidden — a broadcast channel
    or a group with participant-hiding on returns ``ChatAdminRequiredError``, and
    that is the honest answer rather than an empty list.
    """
    async with UserClient() as c:
        return await _members(c, chat, limit=limit)


async def chat_info(chat: str | int) -> dict:
    """``{chat_id, title, username, kind, members, can_view_participants}``."""
    from .dialogs import _kind
    from .media import resolve_entity
    async with UserClient() as c:
        ent = await resolve_entity(c, chat)
        info = {
            "chat_id": int(getattr(ent, "id", 0)),
            "title": getattr(ent, "title", None) or getattr(ent, "first_name", "") or "",
            "username": getattr(ent, "username", None),
            "kind": _kind(ent),
            "members": getattr(ent, "participants_count", None),
            "can_view_participants": None,
        }
        try:
            from telethon.tl.functions.channels import GetFullChannelRequest
            full = await c(GetFullChannelRequest(ent))
            info["members"] = getattr(full.full_chat, "participants_count", info["members"])
            info["can_view_participants"] = bool(
                getattr(full.full_chat, "can_view_participants", False))
        except Exception:  # noqa: BLE001 — basic groups have no full-channel call
            logger.debug("GetFullChannel unavailable for %s", chat, exc_info=True)
        return info


async def _contacts(client) -> list[dict]:
    from telethon.tl.functions.contacts import GetContactsRequest
    res = await client(GetContactsRequest(hash=0))
    return [_user_row(u) for u in getattr(res, "users", [])]


async def list_contacts(*, tag: str | None = None,
                        with_notes: bool = False) -> list[dict]:
    """Your saved contacts. ``tag`` keeps only those carrying the marker.

    ``with_notes`` also fetches each contact's private note — one request per
    contact, so it is slow over a large list, and it is the only way to see a
    marker written in ``note`` mode. Without it, only surname markers match.
    """
    async with UserClient() as c:
        rows = await _contacts(c)
        if with_notes:
            notes = await _notes_for(c, [r["user_id"] for r in rows])
            for r in rows:
                r["note"] = notes.get(r["user_id"], "")
    if tag:
        rows = [r for r in rows
                if is_tagged(r.get("first_name"), r.get("last_name"), tag)
                or _has_tag(r.get("note"), tag)]
    return rows


# ── the write path ───────────────────────────────────────────────────────────


class PeerFlooded(RuntimeError):
    """Telegram limited the account mid-run. Not retryable — stop."""


def _run_id(chat: str | int) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{str(chat).lstrip('@')[:24]}"


def _note_obj(text: str | None):
    """``TextWithEntities`` for a plain note, or None to leave the note alone."""
    if text is None:
        return None
    from telethon.tl import types
    return types.TextWithEntities(text=text, entities=[])


async def _write_contact(client, user, first: str, last: str,
                         note: str | None = None) -> None:
    """One ``contacts.addContact``. ``note=None`` omits the field entirely.

    The note rides along in the same request as the names, so marking a contact
    this way costs no extra round trip.
    """
    from telethon.tl.functions.contacts import AddContactRequest
    await client(AddContactRequest(
        id=user,
        first_name=first,
        last_name=last,
        phone="",
        # Never hand out your phone number as a side effect of filing someone.
        add_phone_privacy_exception=False,
        note=_note_obj(note),
    ))


async def _read_note(client, user) -> str:
    """The private contact note for one user, or "" when unset/unsupported."""
    from telethon.tl.functions.users import GetFullUserRequest
    try:
        full = await client(GetFullUserRequest(user))
        note = getattr(getattr(full, "full_user", None), "note", None)
        return (getattr(note, "text", "") or "") if note is not None else ""
    except Exception:  # noqa: BLE001 — an unreadable note must not fail the run
        logger.debug("could not read contact note", exc_info=True)
        return ""


def unpersisted_ids(written: list[dict], contact_ids: set[int]) -> list[dict]:
    """Written rows whose person is not in the contact list afterwards.

    ``contacts.addContact`` can return success without the contact sticking —
    observed on a real account for someone who had blocked the owner. The API
    not erroring is therefore *not* proof the person was filed, and a run that
    counts sent requests over-reports what actually landed.
    """
    return [r for r in written if r.get("user_id") not in contact_ids]


async def _unpersisted(client, written: list[dict]) -> list[dict]:
    """Re-read the contact list once and report writes that did not stick."""
    if not written:
        return []
    try:
        after = {r["user_id"] for r in await _contacts(client)}
    except Exception:  # noqa: BLE001 — a failed check must not fail the run
        logger.debug("post-write contact re-read failed", exc_info=True)
        return []
    return unpersisted_ids(written, after)


async def _notes_for(client, user_ids: list[int]) -> dict[int, str]:
    """Existing notes for a handful of users — one request each, so keep it small.

    Only ever called for members who are *already* contacts, which is a fraction
    of a group; fetching notes for every member would be one request per person.
    """
    out: dict[int, str] = {}
    for uid in user_ids:
        try:
            user = await client.get_input_entity(uid)
        except Exception:  # noqa: BLE001
            continue
        out[uid] = await _read_note(client, user)
    return out


async def tag_members(
    chat: str | int,
    *,
    tag: str = DEFAULT_TAG,
    mark: str = DEFAULT_MARK,
    note_template: str = DEFAULT_NOTE_TEMPLATE,
    confirm: bool = False,
    limit: int | None = None,
    delay: float = DEFAULT_DELAY,
    update_existing: bool = False,
    include_bots: bool = False,
    progress: Callable[[dict], None] | None = None,
) -> dict:
    """Plan (and with ``confirm=True``, perform) the marking of a group's members.

    Returns ``{run_id, chat, mark, note, plan, counts, written, failed, aborted}``.
    With ``confirm=False`` nothing is sent and ``written`` is empty — that is the
    default, deliberately.

    ``limit`` caps how many *writes* happen (the plan always covers everyone), so
    a pilot batch is ``limit=3`` and a run in pieces is repeated ``limit=N``:
    people written by an earlier batch are contacts by the next one, so they fall
    out of ``todo`` on their own.
    """
    from telethon.errors import FloodWaitError, PeerFloodError, RPCError

    run_id = _run_id(chat)
    written: list[dict] = []
    failed: list[dict] = []
    not_persisted: list[dict] = []
    aborted: str | None = None
    note = render_note(
        note_template, tag=tag, group=str(chat),
        date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    )

    async with UserClient() as c:
        me = await c.get_me()
        members = await _members(c, chat)
        saved = await _contacts(c)
        by_id = {r["user_id"]: r for r in saved}

        # Notes are one request per person, so only fetch them for members who
        # are already contacts — the only ones whose note could already exist.
        notes_by_id: dict[int, str] = {}
        if mark in ("note", "both"):
            already = [m["user_id"] for m in members if m["user_id"] in by_id]
            notes_by_id = await _notes_for(c, already)

        plan = plan_tagging(
            members, by_id, me_id=int(me.id), tag=tag, mark=mark, note=note,
            notes_by_id=notes_by_id, update_existing=update_existing,
            include_bots=include_bots,
        )
        counts = summarize_plan(plan)
        todo = [r for r in plan if r["action"] in ("add", "update")]
        if limit is not None:
            todo = todo[:limit]

        if not confirm:
            return {"run_id": None, "chat": str(chat), "tag": tag, "mark": mark,
                    "note": note, "plan": plan, "counts": counts, "todo": todo,
                    "written": [], "failed": [], "aborted": None,
                    "not_persisted": [], "dry_run": True}

        for i, row in enumerate(todo):
            try:
                user = await c.get_input_entity(row["user_id"])
                await _write_contact(c, user, row["new_first"], row["new_last"],
                                     row.get("new_note"))
            except PeerFloodError as exc:
                # The account is already rate-limited. Retrying makes it worse.
                aborted = ("PEER_FLOOD — Telegram has limited this account for adding "
                           "too many contacts. Stopped; wait a day before resuming.")
                logger.warning("peer flood during contact tagging: %s", exc)
                break
            except FloodWaitError as exc:
                wait = int(getattr(exc, "seconds", 0) or 0)
                if wait > MAX_FLOOD_WAIT:
                    aborted = (f"FLOOD_WAIT {wait}s — longer than the {MAX_FLOOD_WAIT}s "
                               f"ceiling. Stopped; resume later.")
                    break
                await asyncio.sleep(wait + 1)
                try:
                    user = await c.get_input_entity(row["user_id"])
                    await _write_contact(c, user, row["new_first"], row["new_last"],
                                         row.get("new_note"))
                except RPCError as exc2:
                    failed.append({**row, "error": type(exc2).__name__, "detail": str(exc2)})
                    continue
            except RPCError as exc:
                failed.append({**row, "error": type(exc).__name__, "detail": str(exc)})
                continue

            rec = {
                "run_id": run_id, "kind": "write", "at": datetime.now(timezone.utc).isoformat(),
                "chat": str(chat), "tag": tag, "mark": mark, "action": row["action"],
                "user_id": row["user_id"], "username": row.get("username"),
                "before_first": row.get("before_first"), "before_last": row.get("before_last"),
                "before_note": row.get("before_note"),
                "new_first": row["new_first"], "new_last": row["new_last"],
                "new_note": row.get("new_note"),
            }
            # Journalled before the next write is attempted: an abort here still
            # leaves an undo record for everything already written.
            _append_journal(rec)
            written.append(rec)
            if progress:
                progress({"done": len(written), "total": len(todo), **rec})
            if delay and i < len(todo) - 1:
                await asyncio.sleep(delay)

        not_persisted = await _unpersisted(c, written)

    return {"run_id": run_id, "chat": str(chat), "tag": tag, "mark": mark,
            "note": note, "plan": plan, "counts": counts, "todo": todo,
            "written": written, "failed": failed, "aborted": aborted,
            "not_persisted": not_persisted, "dry_run": False}


#: Telethon status class → the bucket Telegram shows in the UI.
STATUS_BUCKETS = {
    "UserStatusOnline": "online",
    "UserStatusOffline": "offline",
    "UserStatusRecently": "recently",
    "UserStatusLastWeek": "last-week",
    "UserStatusLastMonth": "last-month",
    "UserStatusEmpty": "gone",
}
PRUNE_STATUSES = ("gone", "last-month", "last-week")


def status_bucket(user) -> str:
    """Which last-seen bucket a user falls in; ``gone`` when there is no status.

    An absent status is what Telegram reports as *"last seen a long time ago"*.
    It is what you see when someone has **blocked you** — and equally what you
    see when someone sets last-seen privacy to Nobody and has been away a while.
    The API does not distinguish the two, so anything acting on this bucket must
    say so rather than call it "blocked".
    """
    st = getattr(user, "status", None)
    if st is None:
        return "gone"
    return STATUS_BUCKETS.get(st.__class__.__name__, "unknown")


def taggable_reason(entity) -> str | None:
    """``None`` when ``entity`` can be a contact, else why it cannot.

    A public handle is not necessarily a person: ``t.me/foo`` may be a channel or
    a group. Passing one to ``contacts.addContact`` raises a *TypeError* out of
    telethon's casting layer, not an ``RPCError`` — which is how one bad handle
    used to abort an entire run.
    """
    cls = entity.__class__.__name__
    if cls != "User":
        return f"not a person ({cls})"
    if getattr(entity, "deleted", False):
        return "account is deleted"
    if getattr(entity, "bot", False):
        return "is a bot"
    return None


async def tag_handles(
    entries: list[dict],
    *,
    tag: str = DEFAULT_TAG,
    mark: str = DEFAULT_MARK,
    confirm: bool = False,
    limit: int | None = None,
    delay: float = DEFAULT_DELAY,
    resolve_delay: float = 1.0,
    label: str = "handles",
    progress: Callable[[dict], None] | None = None,
) -> dict:
    """Mark an explicit list of people instead of a whole group.

    ``entries`` is ``[{"handle": "@someone", "note": "…", "first_name": "…"}]``.
    Only ``handle`` is required; ``note`` overrides the marker text for that one
    person (this is how per-person facts like a city travel), and ``first_name``
    is a fallback used only when the account itself has no name.

    Resolving a username is a *separate* rate-limited call from adding a
    contact, so it gets its own ``resolve_delay``. A handle that no longer
    resolves — deleted, banned, renamed — is reported in ``unresolved`` rather
    than dropped, because a silently shorter run looks exactly like a
    successful one.
    """
    from telethon.errors import FloodWaitError, PeerFloodError, RPCError

    run_id = _run_id(label)
    written: list[dict] = []
    failed: list[dict] = []
    unresolved: list[dict] = []
    not_persisted: list[dict] = []
    aborted: str | None = None
    writes_name = mark in ("surname", "both")
    writes_note = mark in ("note", "both")

    if mark not in MARKS:
        raise ValueError(f"mark must be one of {MARKS}, got {mark!r}")

    async with UserClient() as c:
        saved = {r["user_id"]: r for r in await _contacts(c)}

        todo: list[dict] = []
        for e in entries:
            handle = str(e.get("handle") or "").strip().lstrip("@")
            if not handle:
                continue
            try:
                ent = await c.get_entity(handle)
            except FloodWaitError as exc:
                # NOT an unresolved handle. contacts.resolveUsername is far more
                # tightly limited than addContact, and lumping a flood in with
                # "does not exist" is how a live person gets recorded as dead —
                # permanently, if the caller feeds `unresolved` to a flagger.
                wait = int(getattr(exc, "seconds", 0) or 0)
                if wait > MAX_FLOOD_WAIT:
                    aborted = (f"FLOOD_WAIT {wait}s while resolving @{handle} — over the "
                               f"{MAX_FLOOD_WAIT}s ceiling. Stopped after "
                               f"{len(todo)} resolved; nothing here is 'dead'.")
                    break
                await asyncio.sleep(wait + 1)
                try:
                    ent = await c.get_entity(handle)
                except (ValueError, RPCError) as exc2:
                    unresolved.append({"handle": handle, "error": type(exc2).__name__,
                                       "detail": str(exc2)})
                    continue
            except (ValueError, RPCError) as exc:
                unresolved.append({"handle": handle, "error": type(exc).__name__,
                                   "detail": str(exc)})
                continue
            reason = taggable_reason(ent)
            if reason:
                unresolved.append({"handle": handle, "error": "NotTaggable",
                                   "detail": reason})
                continue
            uid = int(ent.id)
            cur = saved.get(uid)
            first = ((cur or {}).get("first_name") if cur else None) \
                or getattr(ent, "first_name", None) or e.get("first_name") or handle
            if "last_name" in e:
                # An explicit surname base from the file wins over both the saved
                # and the profile surname. This is how a caller that knows more
                # than Telegram does — a city, a cohort — puts it in the name,
                # e.g. {"last_name": "Lyon"} with tag DAVI → "Lyon DAVI".
                last = str(e.get("last_name") or "").strip()
            else:
                last = ((cur or {}).get("last_name") if cur else None) \
                    or getattr(ent, "last_name", None) or ""
            note = e.get("note") or tag
            # Check the SAVED name, not the computed one: with an override the
            # computed surname is the bare city, which never looks tagged, so
            # comparing it would rewrite an already-marked contact every run.
            if cur and is_tagged(cur.get("first_name"), cur.get("last_name"), tag):
                continue                      # already carries the marker
            todo.append({
                "user_id": uid, "handle": handle, "username": getattr(ent, "username", None),
                "action": "update" if cur else "add",
                "before_first": (cur or {}).get("first_name") if cur else None,
                "before_last": (cur or {}).get("last_name") if cur else None,
                "before_note": None,
                "new_first": str(first).strip(),
                "new_last": apply_tag(last, tag) if writes_name else str(last).strip(),
                "new_note": (note[:NOTE_LENGTH_LIMIT] if writes_note else None),
            })
            if resolve_delay:
                await asyncio.sleep(resolve_delay)

        if limit is not None:
            todo = todo[:limit]

        if not confirm:
            return {"run_id": None, "label": label, "tag": tag, "mark": mark,
                    "todo": todo, "unresolved": unresolved, "written": [],
                    # A dry run that hit a flood ceiling resolved only part of the
                    # list; reporting aborted=None would present a truncated plan
                    # as the whole plan.
                    "failed": [], "aborted": aborted, "not_persisted": [],
                    "dry_run": True}

        for i, row in enumerate(todo):
            try:
                user = await c.get_input_entity(row["user_id"])
                await _write_contact(c, user, row["new_first"], row["new_last"],
                                     row.get("new_note"))
            except PeerFloodError as exc:
                aborted = ("PEER_FLOOD — Telegram limited this account mid-run. "
                           "Stopped; wait before resuming.")
                logger.warning("peer flood during handle tagging: %s", exc)
                break
            except FloodWaitError as exc:
                wait = int(getattr(exc, "seconds", 0) or 0)
                if wait > MAX_FLOOD_WAIT:
                    aborted = f"FLOOD_WAIT {wait}s — over the {MAX_FLOOD_WAIT}s ceiling."
                    break
                await asyncio.sleep(wait + 1)
                try:
                    user = await c.get_input_entity(row["user_id"])
                    await _write_contact(c, user, row["new_first"], row["new_last"],
                                         row.get("new_note"))
                except Exception as exc2:  # noqa: BLE001 — see below
                    failed.append({**row, "error": type(exc2).__name__, "detail": str(exc2)})
                    continue
            except Exception as exc:  # noqa: BLE001
                # Deliberately broad. telethon raises TypeError (not RPCError)
                # when an id cannot be cast to an InputUser, and one such row
                # used to kill the whole batch. A row that fails is recorded in
                # `failed` and reported — never silently skipped.
                failed.append({**row, "error": type(exc).__name__, "detail": str(exc)})
                continue

            rec = {"run_id": run_id, "kind": "write", "mark": mark,
                   "at": datetime.now(timezone.utc).isoformat(),
                   "chat": label, "tag": tag, **row}
            _append_journal(rec)
            written.append(rec)
            if progress:
                progress({"done": len(written), "total": len(todo), **rec})
            if delay and i < len(todo) - 1:
                await asyncio.sleep(delay)

        not_persisted = await _unpersisted(c, written)

    return {"run_id": run_id, "label": label, "tag": tag, "mark": mark, "todo": todo,
            "unresolved": unresolved, "written": written, "failed": failed,
            "aborted": aborted, "not_persisted": not_persisted, "dry_run": False}


async def prune_contacts(
    *,
    status: str = "gone",
    tag: str | None = None,
    confirm: bool = False,
    limit: int | None = None,
    delay: float = 1.5,
    progress: Callable[[dict], None] | None = None,
) -> dict:
    """Remove contacts sitting in a given last-seen bucket.

    ``status="gone"`` is the common case: contacts whose last-seen reads *"a long
    time ago"*. See :func:`status_bucket` — that bucket contains people who
    blocked you **and** people who merely hid their last-seen, so this is a
    heuristic, not a blocked-list. Every candidate is printed before removal and
    journalled after, and :func:`undo_run` can put them back.

    ``tag`` scopes the sweep to one cohort, so pruning an imported list cannot
    touch contacts you added by hand.
    """
    from telethon.errors import RPCError
    from telethon.tl.functions.contacts import DeleteContactsRequest, GetContactsRequest

    run_id = _run_id(f"prune-{status}")
    removed: list[dict] = []
    failed: list[dict] = []

    async with UserClient() as c:
        res = await c(GetContactsRequest(hash=0))
        todo: list[dict] = []
        for u in getattr(res, "users", []):
            if status_bucket(u) != status:
                continue
            if tag and not is_tagged(getattr(u, "first_name", None),
                                     getattr(u, "last_name", None), tag):
                continue
            todo.append({
                "user_id": int(u.id), "username": getattr(u, "username", None),
                "removed_first": getattr(u, "first_name", None),
                "removed_last": getattr(u, "last_name", None),
                "status": status,
            })
        if limit is not None:
            todo = todo[:limit]

        if not confirm:
            return {"run_id": None, "status": status, "tag": tag, "todo": todo,
                    "removed": [], "failed": [], "dry_run": True}

        for i, row in enumerate(todo):
            try:
                user = await c.get_input_entity(row["user_id"])
                await c(DeleteContactsRequest(id=[user]))
            except (RPCError, TypeError, ValueError) as exc:
                failed.append({**row, "error": type(exc).__name__, "detail": str(exc)})
                continue
            rec = {"run_id": run_id, "kind": "prune",
                   "at": datetime.now(timezone.utc).isoformat(),
                   "chat": f"prune-{status}", "tag": tag,
                   "reason": f"last seen bucket: {status}", **row}
            _append_journal(rec)
            removed.append(rec)
            if progress:
                progress({"done": len(removed), "total": len(todo), **rec})
            if delay and i < len(todo) - 1:
                await asyncio.sleep(delay)

    return {"run_id": run_id, "status": status, "tag": tag, "todo": todo,
            "removed": removed, "failed": failed, "dry_run": False}


async def undo_run(run_id: str | None = None, *, confirm: bool = False) -> dict:
    """Reverse a marking or prune run.

    A contact this run *created* is deleted outright, which takes its note with
    it; one it merely *edited* is restored to the exact name and note it had
    before; one it *pruned* is added back under the name it was removed with.
    Undo is itself journalled, and an entry already undone is skipped, so running
    it twice is safe.
    """
    from telethon.errors import RPCError

    records = read_journal(run_id)
    if run_id is None:
        runs = [r["run_id"] for r in list_runs()]
        if not runs:
            return {"run_id": None, "reverted": [], "failed": [], "dry_run": not confirm}
        run_id = runs[-1]
        records = read_journal(run_id)

    undone = {r.get("user_id") for r in records if r.get("kind") == "undo"}
    writes = [r for r in records
              if r.get("kind") in ("write", "prune") and r.get("user_id") not in undone]

    if not confirm:
        return {"run_id": run_id, "todo": writes, "reverted": [], "failed": [],
                "dry_run": True}

    reverted: list[dict] = []
    failed: list[dict] = []
    async with UserClient() as c:
        from telethon.tl.functions.contacts import DeleteContactsRequest
        for rec in writes:
            try:
                if rec.get("kind") == "prune":
                    # The contact is gone, so the session may no longer hold an
                    # access hash for the id — the username is the reliable way
                    # back. Without one there is nothing to resolve.
                    handle = rec.get("username")
                    if not handle:
                        failed.append({**rec, "error": "NoUsername",
                                       "detail": "pruned contact had no username to re-add by"})
                        continue
                    ent = await c.get_entity(handle)
                    await _write_contact(c, await c.get_input_entity(ent.id),
                                         rec.get("removed_first") or handle,
                                         rec.get("removed_last") or "", None)
                    entry = {"run_id": run_id, "kind": "undo",
                             "at": datetime.now(timezone.utc).isoformat(),
                             "user_id": rec["user_id"], "action": "prune"}
                    _append_journal(entry)
                    reverted.append(rec)
                    continue

                user = await c.get_input_entity(rec["user_id"])
                if rec.get("action") == "add":
                    # Deleting the contact takes its note with it.
                    await c(DeleteContactsRequest(id=[user]))
                else:
                    # Restore name *and* note. A note this run wrote over an empty
                    # one restores to "" rather than being left behind — passing
                    # None here would keep the marker on a contact we just undid.
                    await _write_contact(
                        c, user,
                        rec.get("before_first") or "",
                        rec.get("before_last") or "",
                        (rec.get("before_note") or "") if rec.get("new_note") is not None else None,
                    )
            except (RPCError, TypeError, ValueError) as exc:
                failed.append({**rec, "error": type(exc).__name__, "detail": str(exc)})
                continue
            entry = {"run_id": run_id, "kind": "undo",
                     "at": datetime.now(timezone.utc).isoformat(),
                     "user_id": rec["user_id"], "action": rec.get("action")}
            _append_journal(entry)
            reverted.append(rec)

    return {"run_id": run_id, "reverted": reverted, "failed": failed, "dry_run": False}
