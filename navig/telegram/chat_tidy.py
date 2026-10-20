"""Daily chat tidy — yesterday's routine messages leave the bot's DMs overnight.

The bot posts a day of routine into the operator's private chat: the morning
card, reminders, sparks, greetings, the weigh-in receipt, the close-of-day
summary. Telegram keeps all of it, so every morning opened on yesterday's ✗ marks
and quotes. Nothing recorded which messages the bot had sent, so nothing could
take them back later.

Every bot message leaves through one of two choke points —
``TelegramChannel._api_call`` (the gateway) and ``commands.telegram._api_call``
(the cron subprocesses) — and both hand the Message Telegram returned to
:func:`note`. A small JSONL ledger keeps **ids and a timestamp only — never
text**. Only private chats are recorded: never a group, and never a business
chat, which is somebody else's conversation.

What gets recorded depends on ``telegram.daily_clean.scope``:

* ``routine`` (default) — only sends made inside a :func:`routine` block: the
  check-in cards, reminders, sparks, greetings, digests. Downloads, AI answers
  and anything else you might want to keep are left alone.
* ``all`` — every message the bot sends into a private chat.

Once a day, after ``telegram.daily_clean.hour`` local time (default 04:00),
:func:`sweep` deletes every recorded message from an earlier local day. A bot may
delete a message for 48 hours only, so an entry past that is dropped rather than
retried forever.

Off by default: ``navig config set telegram.daily_clean.enabled true``.

SYSTEM CARDS — a second, independent switch (``telegram.system_cleanup``,
**on by default**, owner directive 2026-10-14): approval cards, config-health
incidents, the infrastructure briefing, /status, /plans and OCR bubbles are
tagged kind ``"system"`` and swept the next morning like everything above —
turn it off with ``navig telegram tidy --system-off`` (or ``navig config set
telegram.system_cleanup.enabled false``). The two switches compose: routine
kinds record only when ``daily_clean.enabled`` is on, ``"system"`` only when
``system_cleanup.enabled`` is on, and the sweep fires when either is due.
Neither ever touches the operator's own messages or the agent's replies —
those are conversation, not cards.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Telegram refuses a bot delete once a message is 48 h old; stay a little inside.
DELETE_WINDOW_SEC = 47 * 3600
#: ``deleteMessages`` takes at most 100 ids per call.
BATCH = 100
#: A failed sweep is retried, but not on every 15-second reminder tick.
RETRY_SEC = 30 * 60
#: A lock older than this belongs to a process that died mid-sweep.
LOCK_STALE_SEC = 10 * 60
DEFAULT_HOUR = 4
#: Notification types (``navig.notify`` matrix rows) whose Telegram copy is
#: day-bound routine and is swept tomorrow like the morning card. The notify
#: router's Telegram send runs inside :func:`routine` for these — without it the
#: sale rings, the daily sales summary and the briefing were never recorded, so
#: they piled up forever while everything around them was tidied.
#: Deliberately ABSENT: anything that is a problem to act on (``system_alert``,
#: ``security_alert``, ``sales_check_failed``, ``finance_alert``, ``approval``,
#: ``invoice_due``) or a date you may still need (``appointment``). Override with
#: ``telegram.daily_clean.routine_types`` (a list, or a comma-separated string).
DEFAULT_ROUTINE_NOTIFY_TYPES: frozenset[str] = frozenset(
    # `deploy`: "Deployed Deck (Mini App) · Live at …" is a receipt for something the
    # operator just did, not a problem to act on — and it stayed forever.
    # `free_game`: claimed / deals / ending soon — a daily list, stale by tomorrow.
    {"sale", "sales_digest", "briefing", "goal_digest", "reminder", "deploy", "free_game"}
)

#: What a routine message IS, so the operator can choose per kind what tomorrow's
#: sweep removes (``telegram.daily_clean.keep_kinds``). Recorded with the message
#: id at send time — the only moment the sender knows. Text is never stored.
KINDS: dict[str, str] = {
    "greeting": "Greetings and check-ins (back online, all good)",
    "tip": "Tips and idle nudges",
    "spark": "Motivational lines",
    "reminder": "Reminders",
    "habit_card": "Habit cards (morning, midday, evening, day closed)",
    "body": "Weigh-ins and body receipts",
    "checkin": "Check-in questions (pills, …) once answered",
    "briefing": "Morning and evening briefings",
    "sales": "Sales and the daily sales summary",
    "deploy": "Deploy receipts",
    "digest": "Digests (goals, email stats)",
    "games": "Free games",
    "status": "All-clear notices (back online, back to normal)",
    "deletion": "Deletion alerts",
    "notice": "Other routine notices",
    "system": "System NAVIG cards (approval, config health, briefing, status, spaces, OCR)",
    "other": "Unlabelled (recorded before kinds existed)",
}
#: A notify-router type → the kind its Telegram copy is recorded as.
NOTIFY_TYPE_KINDS: dict[str, str] = {
    "sale": "sales", "sales_digest": "sales", "briefing": "briefing",
    "reminder": "reminder", "deploy": "deploy", "message_deleted": "deletion",
    "goal_digest": "digest", "free_game": "games",
}
#: Notify types whose Telegram copy is a SYSTEM card regardless of priority —
#: recorded as ``"system"`` (governed by telegram.system_cleanup, not
#: daily_clean). Config-health incident notices are the founding member: they
#: fired at any priority and were day-bound noise by the next morning, but
#: never met the routine-types rule (deliberately excluded there — they are
#: "problems to act on" while still fresh).
SYSTEM_NOTIFY_TYPES: frozenset[str] = frozenset({"config_incident"})

_ROUTINE: contextvars.ContextVar[bool] = contextvars.ContextVar("navig_tidy_routine", default=False)
_KIND: contextvars.ContextVar[str] = contextvars.ContextVar("navig_tidy_kind", default="notice")
_settings_cache: tuple[float, dict[str, Any]] | None = None
_last_attempt = 0.0

ApiCall = Callable[[str, dict[str, Any]], Awaitable[Any]]


# ── settings ────────────────────────────────────────────────────────────────


def settings(*, fresh: bool = False) -> dict[str, Any]:
    """``{enabled, hour, scope, routine_types, keep_kinds, system_enabled, system_hour}``
    — cached for a minute; this sits on the send path."""
    global _settings_cache
    now = time.monotonic()
    if not fresh and _settings_cache and now - _settings_cache[0] < 60:
        return _settings_cache[1]
    out: dict[str, Any] = {
        "enabled": False,
        "hour": DEFAULT_HOUR,
        "scope": "routine",
        "routine_types": DEFAULT_ROUTINE_NOTIFY_TYPES,
        "keep_kinds": frozenset(),
        # ON by default (owner directive 2026-10-14: system cards must leave the
        # chat each day); `navig telegram tidy --system-off` turns it off.
        "system_enabled": True,
        "system_hour": DEFAULT_HOUR,
    }
    try:
        from navig.config import get_config_manager
        from navig.core.coerce import coerce_bool

        raw = get_config_manager().get("telegram.daily_clean", {}) or {}
        if isinstance(raw, dict):
            out["enabled"] = coerce_bool(raw.get("enabled"), default=False)
            try:
                out["hour"] = min(23, max(0, int(raw.get("hour", DEFAULT_HOUR))))
            except (TypeError, ValueError):
                pass
            if str(raw.get("scope", "routine")).strip().lower() == "all":
                out["scope"] = "all"
            types = _parse_types(raw.get("routine_types"))
            if types is not None:
                out["routine_types"] = types
            keep = _parse_types(raw.get("keep_kinds"))
            if keep is not None:
                out["keep_kinds"] = keep
        sys_raw = get_config_manager().get("telegram.system_cleanup", {}) or {}
        if isinstance(sys_raw, dict):
            # `or {}` above makes an explicitly-empty block read as "use the
            # default"; coerce_bool still honours an explicit false/"false".
            out["system_enabled"] = coerce_bool(
                sys_raw.get("enabled", True), default=True
            )
            try:
                out["system_hour"] = min(23, max(0, int(sys_raw.get("hour", DEFAULT_HOUR))))
            except (TypeError, ValueError):
                pass
    except Exception:  # noqa: BLE001 — config unreadable: stay off, never break a send
        logger.debug("daily_clean settings unreadable", exc_info=True)
    _settings_cache = (now, out)
    return out


def _parse_types(raw: Any) -> frozenset[str] | None:
    """A configured type list: a real list, or the raw string ``navig config set`` stores."""
    if raw is None:
        return None
    if isinstance(raw, str):
        items = raw.strip().strip("[]").replace(";", ",").split(",")
    elif isinstance(raw, (list, tuple, set, frozenset)):
        items = [str(i) for i in raw]
    else:
        return None
    return frozenset(i.strip().strip("'\"").strip() for i in items if i.strip().strip("'\""))


def is_routine_notification(type_key: str) -> bool:
    """Is a ``navig.notify`` type day-bound routine (tidied away tomorrow)?"""
    types = settings().get("routine_types")
    if types is None:
        types = DEFAULT_ROUTINE_NOTIFY_TYPES
    return str(type_key or "") in types


# ── recording ───────────────────────────────────────────────────────────────


@contextlib.contextmanager
def routine(kind: str = "notice") -> Iterator[None]:
    """Mark every send inside this block as day-bound routine (swept tomorrow).

    *kind* (a :data:`KINDS` key) is what the operator chooses by: a kind listed in
    ``telegram.daily_clean.keep_kinds`` is recorded but never swept."""
    token = _ROUTINE.set(True)
    kind_token = _KIND.set(kind if kind in KINDS else "notice")
    try:
        yield
    finally:
        _KIND.reset(kind_token)
        _ROUTINE.reset(token)


def mark_routine(kind: str = "notice") -> None:
    """Mark the rest of this context as routine — for one-shot CLI commands."""
    _ROUTINE.set(True)
    _KIND.set(kind if kind in KINDS else "notice")


def notify_kind(type_key: str) -> str:
    """The kind a ``navig.notify`` type's Telegram copy is recorded as."""
    return NOTIFY_TYPE_KINDS.get(str(type_key or ""), "notice")


def notify_tidy_kind(type_key: str, priority: str = "normal",
                     data: dict[str, Any] | None = None) -> str | None:
    """The kind to record a router send under, or None to leave it in the chat.

    One type often carries both the alarm and its all-clear ("brain offline" /
    "back online" are both ``connectivity``), so a producer may decide per send:
    ``data["tidy"]`` is a kind (record it, whatever the type) or ``False`` (never —
    a renewal or an appointment still ahead is not yesterday's news). Otherwise a
    HIGH/CRITICAL send is never recorded, even of a routine type: "Deploy failed"
    and "sign-in expired" share their type with a receipt and a game list.

    :data:`SYSTEM_NOTIFY_TYPES` win over the priority rule — a config-health
    notice is day-bound at ANY priority (it is recorded only while
    telegram.system_cleanup is on, which note() enforces per kind)."""
    choice = (data or {}).get("tidy")
    if choice is False:
        return None
    if isinstance(choice, str) and choice in KINDS:
        return choice
    if str(type_key or "") in SYSTEM_NOTIFY_TYPES:
        return "system"
    if str(priority or "").lower() in ("high", "critical"):
        return None
    return notify_kind(type_key) if is_routine_notification(type_key) else None


def keep_kinds() -> frozenset[str]:
    return frozenset(settings().get("keep_kinds") or ())


def set_keep(kind: str, keep: bool) -> frozenset[str]:
    """Keep (never sweep) or stop keeping one kind. Returns the new keep set."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of: {', '.join(KINDS)}")
    current = set(settings(fresh=True).get("keep_kinds") or ())
    if keep:
        current.add(kind)
    else:
        current.discard(kind)
    from navig.config import get_config_manager

    get_config_manager().set_global("telegram.daily_clean.keep_kinds", sorted(current))
    _invalidate()
    return frozenset(current)


def set_hour(hour: int) -> None:
    if not 0 <= int(hour) <= 23:
        raise ValueError("hour must be 0-23")
    from navig.config import get_config_manager

    get_config_manager().set_global("telegram.daily_clean.hour", int(hour))
    _invalidate()


def set_enabled(on: bool) -> None:
    from navig.config import get_config_manager

    get_config_manager().set_global("telegram.daily_clean.enabled", bool(on))
    _invalidate()


def set_system_enabled(on: bool) -> None:
    """Turn the daily SYSTEM-card sweep (approval cards, briefings, OCR, …) on/off."""
    from navig.config import get_config_manager

    get_config_manager().set_global("telegram.system_cleanup.enabled", bool(on))
    _invalidate()


def set_system_hour(hour: int) -> None:
    if not 0 <= int(hour) <= 23:
        raise ValueError("hour must be 0-23")
    from navig.config import get_config_manager

    get_config_manager().set_global("telegram.system_cleanup.hour", int(hour))
    _invalidate()


def _invalidate() -> None:
    global _settings_cache
    _settings_cache = None


def pending_by_kind(entries: list[dict[str, Any]] | None = None) -> dict[str, int]:
    """How many recorded messages wait in the ledger, per kind."""
    from collections import Counter

    rows = read_ledger(ledger_path()) if entries is None else entries
    return dict(Counter(str(e.get("k") or "other") for e in rows))


def _dir() -> Path:
    from navig.platform import paths

    return paths.config_dir() / "cache"


def ledger_path() -> Path:
    return _dir() / "telegram_sent.jsonl"


def _state_path() -> Path:
    return _dir() / "telegram_tidy_state.json"


def note(result: Any, *, routine: bool | None = None, kind: str | None = None) -> None:
    """Record a Message (or a list of them, from sendMediaGroup). Never raises.

    Two switches govern recording: routine kinds need ``daily_clean.enabled``,
    kind ``"system"`` needs ``system_cleanup.enabled`` — a send is recorded when
    the switch that owns its kind is on (neither on → record nothing)."""
    try:
        cfg = settings()
        is_routine = _ROUTINE.get() if routine is None else routine
        # `scope: all` records non-routine sends too; those are "other".
        label = kind or (_KIND.get() if is_routine else "other")
        if label == "system":
            if not cfg["system_enabled"]:
                return
        else:
            if not cfg["enabled"]:
                return
            if cfg["scope"] == "routine" and not is_routine:
                return
        lines = []
        for msg in result if isinstance(result, list) else [result]:
            if not isinstance(msg, dict) or msg.get("business_connection_id"):
                continue
            chat = msg.get("chat") or {}
            cid, mid = chat.get("id"), msg.get("message_id")
            if chat.get("type") != "private" or not isinstance(cid, int) or not isinstance(mid, int):
                continue
            ts = msg.get("date") if isinstance(msg.get("date"), int) else int(time.time())
            lines.append(json.dumps({"c": cid, "m": mid, "t": ts, "k": label}))
        if not lines:
            return
        path = ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except Exception:  # noqa: BLE001 — bookkeeping must never cost a delivery
        logger.debug("chat_tidy.note failed", exc_info=True)


# ── sweeping ────────────────────────────────────────────────────────────────


@dataclass
class SweepResult:
    deleted: int = 0
    expired: int = 0
    failed: int = 0
    kept: int = 0
    busy: bool = False
    #: Open check-in questions skipped this sweep (see :func:`_protected`).
    protected: int = 0
    #: Left in the chat because the operator keeps their kind (``keep_kinds``).
    spared: int = 0


def _protected() -> set[tuple[int, int]]:
    """``(chat, message_id)`` of every open check-in question. Never raises.

    If the check-in state cannot be read the sweep still runs, protecting nothing
    extra — exactly the behaviour before check-ins existed — rather than taking
    the whole tidy down.
    """
    try:
        from navig.checkins.state import protected_messages  # noqa: PLC0415

        return protected_messages()
    except Exception:  # noqa: BLE001
        logger.debug("check-in protection unavailable", exc_info=True)
        return set()


def read_ledger(path: Path) -> list[dict[str, int]]:
    out: list[dict[str, int]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return out
    for line in text.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and all(isinstance(e.get(k), int) for k in ("c", "m", "t")):
            # A line written before kinds existed has no "k": it is "other".
            if not isinstance(e.get("k"), str):
                e["k"] = "other"
            out.append(e)
    return out


@contextlib.contextmanager
def _lock() -> Iterator[bool]:
    """Exclusive across processes — the gateway and the worker both tick."""
    path = _dir() / "telegram_tidy.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if time.time() - path.stat().st_mtime > LOCK_STALE_SEC:
            path.unlink(missing_ok=True)
    except FileNotFoundError:
        pass
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        yield False
        return
    os.close(fd)
    try:
        yield True
    finally:
        path.unlink(missing_ok=True)


def _take() -> list[dict[str, int]] | None:
    """Move the live ledger aside and read it, so appends during the sweep land
    in a fresh file instead of being lost. ``None`` = a writer holds it; later."""
    live, aside = ledger_path(), ledger_path().with_suffix(".sweeping")
    leftover = read_ledger(aside)  # a sweep that died before putting entries back
    try:
        os.replace(live, aside)
    except FileNotFoundError:
        return leftover
    except PermissionError:
        return None
    return leftover + read_ledger(aside)


def _put_back(entries: list[dict[str, int]]) -> None:
    if entries:
        with open(ledger_path(), "a", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(e) + "\n" for e in entries))
    ledger_path().with_suffix(".sweeping").unlink(missing_ok=True)


async def sweep(api: ApiCall, *, now: datetime | None = None, dry_run: bool = False) -> SweepResult:
    """Delete every recorded message from before today. Safe to call repeatedly."""
    now = now or datetime.now()
    today = now.date()
    res = SweepResult()
    with _lock() as held:
        if not held:
            res.busy = True
            return res
        entries = _take() if not dry_run else read_ledger(ledger_path())
        if entries is None:
            res.busy = True
            return res
        keep: list[dict[str, int]] = []
        due: dict[int, dict[int, dict[str, int]]] = {}
        protected = _protected()
        spare = keep_kinds()
        for e in entries:
            if e.get("k") in spare and datetime.fromtimestamp(e["t"]).date() < today:
                # The operator keeps this kind: it stays in the chat for good. Dropped
                # from the ledger rather than retried; the choice is applied when the
                # message falls due, so un-keeping a kind still sweeps today's.
                res.spared += 1
            elif (e["c"], e["m"]) in protected:
                # An OPEN check-in question (a pill card, the weigh-in prompt) is
                # not yesterday's routine until it is answered. Deleting it left the
                # pending record pointing at a message that no longer existed — and
                # for a dose, an unanswered question silently vanishing is the
                # unsafe direction. Kept for a later sweep; past Telegram's delete
                # window it could not be deleted anyway, so it is dropped.
                if now.timestamp() - e["t"] > DELETE_WINDOW_SEC:
                    res.expired += 1
                else:
                    keep.append(e)
                    res.protected += 1
            elif datetime.fromtimestamp(e["t"]).date() >= today:
                keep.append(e)
            elif now.timestamp() - e["t"] > DELETE_WINDOW_SEC:
                res.expired += 1
            else:
                due.setdefault(e["c"], {})[e["m"]] = e
        for chat, by_id in due.items():
            ids = sorted(by_id)
            for i in range(0, len(ids), BATCH):
                batch = ids[i : i + BATCH]
                if dry_run:
                    res.deleted += len(batch)
                    continue
                try:
                    ok = await api("deleteMessages", {"chat_id": chat, "message_ids": batch})
                except Exception:  # noqa: BLE001
                    logger.warning("daily chat tidy: deleteMessages raised", exc_info=True)
                    ok = None
                if ok:
                    res.deleted += len(batch)
                else:
                    res.failed += len(batch)
                    keep.extend(by_id[m] for m in batch)
        res.kept = len(keep) - res.failed - res.protected
        if not dry_run:
            _put_back(keep)
    return res


def last_sweep_day() -> str | None:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8")).get("last_day")
    except (OSError, ValueError, AttributeError):
        return None


def mark_swept(day: str) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"last_day": day}), encoding="utf-8")
    os.replace(tmp, path)


async def maybe_sweep(api: ApiCall, *, now: datetime | None = None) -> SweepResult | None:
    """The once-a-day trigger, called from a frequent tick. ``None`` = not due.

    Fires when EVERY enabled switch's hour has passed — the latest one wins.
    An OR here would let the earlier hour silently override the later one
    (daily_clean set to 06:00 swept at 04:00 by system_cleanup). The ledger
    only ever holds what the recording gates allowed, so with daily_clean off
    the sweep touches nothing but ``"system"`` entries."""
    global _last_attempt
    now = now or datetime.now()
    cfg = settings()
    day = now.date().isoformat()
    hours = [
        h
        for h, on in ((cfg["hour"], cfg["enabled"]), (cfg["system_hour"], cfg["system_enabled"]))
        if on
    ]
    due = bool(hours) and now.hour >= max(hours)
    if not due or last_sweep_day() == day:
        return None
    if time.monotonic() - _last_attempt < RETRY_SEC and _last_attempt:
        return None
    res = await sweep(api, now=now)
    if res.busy:
        return res  # someone else is on it, or a writer held the file — next tick
    _last_attempt = time.monotonic()
    if not res.failed:
        mark_swept(day)
    logger.info(
        "daily chat tidy: deleted %d, expired %d, failed %d, kept %d",
        res.deleted, res.expired, res.failed, res.kept,
    )
    return res
