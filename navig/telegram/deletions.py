"""Deletion watching: what is recorded, who hears about it, and how loudly.

Split out of :mod:`navig.telegram.business` because three separate decisions were
tangled into one boolean (``telegram.business.deletion_alert``):

  1. **record** — should a deletion be written down at all? The catalog row is the
     ONLY surviving record that a message existed, so this is a data decision, not
     a notification one. An operator who wants no trace of deletions has to be able
     to say so, and an operator who wants the record but no pings must not have to
     give up the record to get quiet.
  2. **mode** — ``instant`` (a DM per deletion event), ``digest`` (one "N messages
     deleted · Show" card per window, the default) or ``off`` (record silently).
     Instant was the only behaviour, and on a busy account it floods: every deleted
     message was its own DM *plus* an immediate re-send of its photo/voice.
  3. **where** — the owner's DM, or a separate log chat.

On the "log bot" question: a second bot is the wrong shape for this. It needs its
own token, its own webhook and its own tenant on the edge, and it cannot see the
business connection at all — a bot only receives ``deleted_business_messages`` for
the account IT is connected to. A separate *chat* gets the same separation for
free: point ``target`` at a channel (add the bot as an admin) and every deletion
lands there instead of the DM, with Telegram's own log-channel semantics.

The digest deliberately holds **no content in memory**. It stores a watermark and
re-reads the catalog at flush time, so a restart mid-window loses nothing and the
"Show" button still works on a card sent by a process that has since died.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ── Config keys ──────────────────────────────────────────────────────────────
CFG_RECORD = "telegram.business.deletions.record"        # bool, default on
CFG_MODE = "telegram.business.deletions.mode"            # instant | digest | off
CFG_WINDOW = "telegram.business.deletions.window_sec"    # int, default 900
CFG_TARGET = "telegram.business.deletions.target"        # chat id, default owner DM
CFG_MUTED = "telegram.business.deletions.mute_chats"     # list[int]
CFG_QUIET_EXPIRED = "telegram.business.deletions.quiet_expired"  # bool, default on
CFG_QUIET_WIPES = "telegram.business.deletions.quiet_wipes"      # bool, default on
#: The original single toggle. Still honoured so an operator who turned alerts off
#: stays off after upgrading: an explicit ``false`` means mode ``off``.
CFG_LEGACY_ALERT = "telegram.business.deletion_alert"

MODES = ("instant", "digest", "off")
DEFAULT_MODE = "digest"
DEFAULT_WINDOW_SEC = 900          # 15 minutes
MIN_WINDOW_SEC = 30
MAX_WINDOW_SEC = 86_400
CB_PREFIX = "bizdel:"             # gated by the `business` extension

#: Where the digest resumes from after a restart. Config, not memory: a card can
#: outlive the process that sent it, and its button must still resolve.
CFG_WATERMARK = "telegram.business.deletions.watermark"


def _t(key: str, **fields: Any) -> str:
    """One localized string, or "" when the key is absent so a caller can fall back.

    Buttons and cards reach the operator in THEIR language — this account runs in
    Russian, and a hardcoded "Show"/"Quiet" is exactly what
    ``test_no_english_button_reaches_the_operator`` exists to catch."""
    try:
        from navig.core import i18n

        text = i18n.t(key, **fields)
    except Exception:  # noqa: BLE001 — a card must never fail on a locale read
        return ""
    return "" if text == key else text


def _cfg():
    from navig.core import Config

    return Config()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    """The window edge, in the SAME shape a row is stamped with.

    It used to truncate to milliseconds and the comment claimed that matched the store.
    It did not: ``navig.store.base.utcnow()`` writes six fractional digits, and the
    windows are compared as strings, so an edge of ``…:00.123Z`` sorted AFTER every row
    stamped ``…:00.123456Z`` — a deletion recorded in the boundary millisecond was
    outside the window that ended there and behind the one that started there, i.e.
    never reported. Six digits make the comparison agree with the data.
    """
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _canonical_window(value: str) -> str:
    """Pad a millisecond-precision watermark written by an older build to microseconds.

    Watermarks stored before 2026-10-30 end in ``.123Z``. Padding to ``.123000Z`` keeps
    every later row (``.123456Z``) inside the next window instead of behind it; the one
    row that could re-report is the microunique stamp ``.123000000`` itself.
    """
    text = str(value)
    if not text.endswith("Z"):
        return text
    head, _, frac = text[:-1].rpartition(".")
    if not head or not frac.isdigit():
        return text
    if len(frac) >= 6:
        return text
    return f"{head}.{frac.ljust(6, '0')}Z"


# ── Policy ───────────────────────────────────────────────────────────────────


def record_enabled() -> bool:
    """Whether a deletion is written down at all (the catalog's only record)."""
    from navig.core.coerce import coerce_bool

    try:
        return coerce_bool(_cfg().get(CFG_RECORD, True), default=True)
    except Exception:  # noqa: BLE001
        return True


def set_record_enabled(value: bool) -> None:
    cfg = _cfg()
    cfg.set(CFG_RECORD, bool(value), scope="global")
    cfg.save(scope="global")


def mode() -> str:
    """``instant`` | ``digest`` | ``off`` — how the owner is told."""
    from navig.core.coerce import coerce_bool

    try:
        cfg = _cfg()
        raw = cfg.get(CFG_MODE, None)
        if raw:
            v = str(raw).strip().lower()
            if v in MODES:
                return v
            logger.warning("unknown %s=%r — falling back to %r", CFG_MODE, raw, DEFAULT_MODE)
        # No explicit mode: honour the original boolean so an operator who had
        # alerts OFF is not surprised by a digest after upgrading.
        legacy = cfg.get(CFG_LEGACY_ALERT, None)
        if legacy is not None and not coerce_bool(legacy, default=True):
            return "off"
        return DEFAULT_MODE
    except Exception:  # noqa: BLE001
        return DEFAULT_MODE


def set_mode(value: str) -> None:
    v = str(value).strip().lower()
    if v not in MODES:
        raise ValueError(f"mode must be one of {' | '.join(MODES)}")
    cfg = _cfg()
    cfg.set(CFG_MODE, v, scope="global")
    # Keep the legacy boolean consistent, so anything still reading it (the deck
    # toggle, `business status`) agrees with the mode instead of contradicting it.
    cfg.set(CFG_LEGACY_ALERT, v != "off", scope="global")
    cfg.save(scope="global")


def window_sec() -> int:
    from navig.core.coerce import coerce_int

    try:
        raw = coerce_int(_cfg().get(CFG_WINDOW, DEFAULT_WINDOW_SEC), default=DEFAULT_WINDOW_SEC)
    except Exception:  # noqa: BLE001
        return DEFAULT_WINDOW_SEC
    return max(MIN_WINDOW_SEC, min(MAX_WINDOW_SEC, raw))


def set_window_sec(value: int) -> None:
    v = int(value)
    if not (MIN_WINDOW_SEC <= v <= MAX_WINDOW_SEC):
        raise ValueError(f"window must be between {MIN_WINDOW_SEC} and {MAX_WINDOW_SEC} seconds")
    cfg = _cfg()
    cfg.set(CFG_WINDOW, v, scope="global")
    cfg.save(scope="global")


def target_chat() -> str | None:
    """A separate log chat for deletion alerts, or None for the owner's DM."""
    try:
        raw = _cfg().get(CFG_TARGET, None)
    except Exception:  # noqa: BLE001
        return None
    v = str(raw).strip() if raw is not None else ""
    return v or None


def set_target_chat(chat_id: str | int | None) -> None:
    cfg = _cfg()
    cfg.set(CFG_TARGET, str(chat_id).strip() if chat_id not in (None, "") else "", scope="global")
    cfg.save(scope="global")


def muted_chats() -> set[int]:
    try:
        raw = _cfg().get(CFG_MUTED, []) or []
    except Exception:  # noqa: BLE001
        return set()
    out: set[int] = set()
    # `navig config set` stores a raw string, so a list can arrive as
    # "-100123,456" as well as a real list. Accept both rather than silently
    # muting nothing.
    items = raw if isinstance(raw, (list, tuple, set)) else str(raw).split(",")
    for item in items:
        try:
            out.add(int(str(item).strip()))
        except (TypeError, ValueError):
            continue
    return out


def is_muted(chat_id: Any) -> bool:
    try:
        return int(chat_id) in muted_chats()
    except (TypeError, ValueError):
        return False


def set_muted(chat_id: Any, muted: bool) -> bool:
    """Mute/unmute one chat. Returns the new state."""
    try:
        cid = int(chat_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("chat id must be numeric") from exc
    chats = muted_chats()
    chats.add(cid) if muted else chats.discard(cid)
    cfg = _cfg()
    cfg.set(CFG_MUTED, sorted(chats), scope="global")
    cfg.save(scope="global")
    return muted


# ── Deletions nobody chose: auto-delete timers and whole-history wipes ──────────
#
# Measured on the operator's own catalog (2026-10-09): 604 of 716 business
# deletions landed within ten minutes of an exact multiple of 24 h after the
# message was sent — 538 of them to the minute. That is a chat's auto-delete timer
# expiring, not a person deciding, and it was most of every digest. A
# whole-history clear arrives as ONE update carrying every id (34 and 13 in one
# second, same catalog). Both are still RECORDED — the catalog row is the only
# trace the message existed — they are just not announced.

REASON_EXPIRED = "expired"
REASON_WIPED = "wiped"
REASONS = (REASON_EXPIRED, REASON_WIPED)

#: Telegram's auto-delete timers are whole days (1 d · 1 w · 1 month · custom).
#: Measured lateness of the expiry update: -1 to +4 minutes. A small negative
#: margin covers clock skew between Telegram's `date` and our receive time.
EXPIRY_EARLY_SEC = 3 * 60
EXPIRY_LATE_SEC = 10 * 60
DAY_SEC = 86_400
#: One deletion event this large is a clear, not a tidy-up.
WIPE_MIN_IDS = 10
#: A smaller batch is still a clear when it empties the chat's whole record.
WIPE_MIN_IDS_WHEN_EMPTIED = 3


def _bool_cfg(key: str, default: bool) -> bool:
    from navig.core.coerce import coerce_bool

    try:
        return coerce_bool(_cfg().get(key, default), default=default)
    except Exception:  # noqa: BLE001
        return default


def quiet_reasons() -> set[str]:
    """Deletion reasons that are recorded but never announced."""
    out: set[str] = set()
    if _bool_cfg(CFG_QUIET_EXPIRED, True):
        out.add(REASON_EXPIRED)
    if _bool_cfg(CFG_QUIET_WIPES, True):
        out.add(REASON_WIPED)
    return out


def set_quiet(reason: str, quiet: bool) -> None:
    key = {REASON_EXPIRED: CFG_QUIET_EXPIRED, REASON_WIPED: CFG_QUIET_WIPES}.get(reason)
    if key is None:
        raise ValueError(f"reason must be one of {' | '.join(REASONS)}")
    cfg = _cfg()
    cfg.set(key, bool(quiet), scope="global")
    cfg.save(scope="global")


def looks_expired(sent_date: Any, deleted_at: float) -> bool:
    """True when a message vanished a whole number of days after it was sent.

    *sent_date* is Telegram's ``date`` as the catalog stores it (epoch seconds, as
    a string). Anything unparseable is "not known to be a timer" — an ordinary
    deletion, which is the loud and therefore safe answer."""
    try:
        sent = float(str(sent_date).strip())
    except (TypeError, ValueError):
        return False
    if sent <= 0:
        return False
    life = deleted_at - sent
    days = round(life / DAY_SEC)
    if days < 1:
        return False
    off = life - days * DAY_SEC
    return -EXPIRY_EARLY_SEC <= off <= EXPIRY_LATE_SEC


def classify(store: Any, chat_id: Any, ids: list[Any], *,
             now: float | None = None) -> dict[Any, str | None]:
    """``{message_id: reason | None}`` for one deletion event, BEFORE it is recorded.

    Per message, the timer wins: an expired message in a large batch is still an
    expiry. The wipe rule then covers the rest of a batch that is either large on
    its own, or that leaves the chat with nothing live. Never raises — a lookup
    failure means "ordinary deletion", which errs towards telling the operator."""
    import time

    ts = time.time() if now is None else now
    out: dict[Any, str | None] = {}
    known = 0
    for mid in ids:
        reason = None
        try:
            row = store.get_message_by_ref(chat_id, mid)
        except Exception:  # noqa: BLE001
            row = None
        if row and not row.get("deleted"):
            known += 1
            if looks_expired(row.get("date"), ts):
                reason = REASON_EXPIRED
        out[mid] = reason
    wiped = len(ids) >= WIPE_MIN_IDS
    if not wiped and len(ids) >= WIPE_MIN_IDS_WHEN_EMPTIED and known:
        try:
            # Every live row of this chat is in this one event: the chat was cleared.
            wiped = store.live_message_count(chat_id, kind="business") <= known
        except Exception:  # noqa: BLE001
            wiped = False
    if wiped:
        for mid, reason in out.items():
            if reason is None:
                out[mid] = REASON_WIPED
    return out


def status() -> dict[str, Any]:
    """Everything a status surface needs, in one read."""
    quiet = quiet_reasons()
    return {
        "record": record_enabled(),
        "mode": mode(),
        "window_sec": window_sec(),
        "target": target_chat(),
        "muted_chats": sorted(muted_chats()),
        "quiet_expired": REASON_EXPIRED in quiet,
        "quiet_wipes": REASON_WIPED in quiet,
    }


# ── Watermark ────────────────────────────────────────────────────────────────


def _watermark() -> str:
    """Start of the un-reported window. Defaults to one window ago, so a first
    run reports what just happened rather than the entire history."""
    try:
        raw = _cfg().get(CFG_WATERMARK, None)
    except Exception:  # noqa: BLE001
        raw = None
    if raw:
        return _canonical_window(str(raw))
    return _iso(_now() - timedelta(seconds=window_sec()))


def _seed_watermark() -> None:
    """Persist the window start when none is stored yet.

    Called when a deletion arms the timer. Deriving the default at FLUSH time put
    it a full window after the deletion that armed it, so a first, isolated
    deletion fell before its own window and was reported as nothing pending."""
    try:
        raw = _cfg().get(CFG_WATERMARK, None)
    except Exception:  # noqa: BLE001
        raw = None
    if not raw:
        _set_watermark(_watermark())


def _set_watermark(value: str) -> None:
    try:
        cfg = _cfg()
        cfg.set(CFG_WATERMARK, value, scope="global")
        cfg.save(scope="global")
    except Exception:  # noqa: BLE001
        # A lost watermark re-reports a window at worst; it must never take down
        # the deletion path that just recorded the rows.
        logger.debug("deletion watermark not persisted", exc_info=True)


# ── Digest scheduling ────────────────────────────────────────────────────────

#: How often a digest held by quiet hours is re-checked until it can go out.
QUIET_RECHECK_SEC = 900

#: One pending flush per process. The flush reads the catalog, so two processes
#: arming a timer would send the same digest twice — see `_flush_lock`.
_flush_task: asyncio.Task | None = None
_flush_lock = asyncio.Lock()


def _digest_scope() -> dict[str, Any]:
    """What a digest reports: BUSINESS deletions in chats the operator has not muted.

    Every digest read goes through this — the card's count, resume's check, and
    the Show button's list — so the number on the card and the rows under Show
    cannot disagree. Without it, a muted chat still landed in the count and under
    Show, and a message the operator deleted in a group via the deck was counted
    as a business deletion."""
    # Timer expiries and whole-chat wipes are recorded but not news (see
    # `classify`), so they leave the count, the per-chat lines and Show together.
    return {"kind": "business", "exclude_chats": muted_chats(),
            "exclude_reasons": quiet_reasons()}


def _store():
    from navig.store.telegram_catalog import TelegramCatalogStore

    return TelegramCatalogStore()


def arm_digest(channel: Any, *, delay: int | None = None) -> None:
    """Ensure a flush is scheduled. Idempotent while one is pending, so a burst of
    deletions produces ONE card rather than one per event.

    ``delay`` overrides the window — resume uses a short one, because the
    deletions it finds already waited out (part of) a window before the restart."""
    global _flush_task
    _seed_watermark()
    if _flush_task is not None and not _flush_task.done():
        return
    delay = window_sec() if delay is None else max(0, int(delay))

    async def _later() -> None:
        global _flush_task
        try:
            await asyncio.sleep(delay)
            res = await flush_digest(channel)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("deletion digest flush failed", exc_info=True)
            return
        if not res.get("sent"):
            if res.get("reason") == "quiet hours":
                # Quiet hours lift on their own; nothing else re-arms this window
                # until the next deletion, so check again later rather than hold
                # the promised digest indefinitely.
                _flush_task = None
                arm_digest(channel, delay=QUIET_RECHECK_SEC)
            return
        # A deletion recorded while this card was sending found this task still
        # running and armed nothing; it is past the card's bound, so give it its own.
        try:
            pending = _store().count_deleted_since(_watermark(), **_digest_scope())["messages"]
        except Exception:  # noqa: BLE001
            logger.debug("deletion digest: re-arm check failed", exc_info=True)
            return
        if pending:
            _flush_task = None
            arm_digest(channel)

    from navig.core.background import spawn

    _flush_task = spawn(_later())


#: How soon a digest left pending by a restart is sent once the channel is back.
#: Not a full window: those deletions already waited before the process died.
RESUME_DELAY_SEC = 60


async def resume_pending(channel: Any) -> dict[str, Any]:
    """Re-arm a digest that a restart left pending.

    ``arm_digest`` is only ever called when a deletion ARRIVES, so a window that
    was pending when the process stopped used to sit unsent until the next
    deletion — hours, on a quiet account. The operator restarts often (five times
    in two days, measured), so this was not an edge case.

    Runs in every process that starts a Telegram channel — the supervisor starts
    two — which is safe because :func:`flush_digest` claims its window
    cross-process before sending."""
    try:
        from navig.telegram import permissions

        if not permissions.business_enabled():
            return {"armed": False, "reason": "business inbox off"}
    except Exception:  # noqa: BLE001
        pass
    if not record_enabled():
        return {"armed": False, "reason": "recording off"}
    if mode() != "digest":
        return {"armed": False, "reason": f"mode={mode()}"}
    try:
        counts = _store().count_deleted_since(_watermark(), **_digest_scope())
    except Exception:  # noqa: BLE001
        logger.debug("deletion digest resume: count failed", exc_info=True)
        return {"armed": False, "reason": "count_failed"}
    if not counts["messages"]:
        return {"armed": False, "reason": "nothing_pending"}
    arm_digest(channel, delay=RESUME_DELAY_SEC)
    logger.info("deletion digest resumed: %d pending across %d chat(s)",
                counts["messages"], counts["chats"])
    return {"armed": True, "pending": counts["messages"], "chats": counts["chats"]}


# ── Cross-process window claim ───────────────────────────────────────────────
#
# The supervisor runs a gateway AND a telegram_worker, each with its own channel.
# A per-process lock is honoured by both — which is exactly how a per-process
# "announce once" flag produced two boot greetings per restart. Resume runs in
# both, so two processes can flush the SAME window. The claim is an atomic file
# create, so whichever gets there first sends and the other stands down.

#: A send takes seconds. A claim older than this belongs to a process that died
#: mid-send, and holding it would strand that window forever.
CLAIM_TTL_SEC = 120


def _claim_path(since: str):
    from navig.platform import paths

    return paths.cache_dir() / "deletion_digest" / f"{_token_for(since)}.claim"


def _claim_key(since: str, latest: str | None) -> str:
    """The window identity two processes agree on — see :func:`flush_digest`."""
    try:
        stored = _cfg().get(CFG_WATERMARK, None)
    except Exception:  # noqa: BLE001
        stored = None
    if stored and str(stored) == since:
        return since
    return latest or since


def _claim_window(since: str, *, now: float | None = None) -> bool:
    """True if this process owns the card for the window starting at *since*.

    Atomic ``open(..., "x")``, never read-then-write: the two processes resume in
    the same second. Fails OPEN — if the claim cannot be recorded, sending one
    card too many beats a digest that is never sent."""
    import os
    import time
    import uuid

    ts = time.time() if now is None else now
    path = _claim_path(since)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "x", encoding="utf-8") as fh:
                fh.write(str(ts))
            return True
        except FileExistsError:
            try:
                age = ts - float(path.read_text(encoding="utf-8").strip() or 0)
            except (OSError, ValueError):
                age = float("inf")
            if age < CLAIM_TTL_SEC:
                return False
            # Take over atomically. Two processes can both read the same stale
            # claim; a read-then-replace let both win. A rename to a name only
            # this caller uses succeeds for exactly one of them.
            grave = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.stale")
            try:
                os.rename(path, grave)
            except FileNotFoundError:
                return False                   # a sibling took it over first
            try:
                moved_age = ts - float(grave.read_text(encoding="utf-8").strip() or 0)
            except (OSError, ValueError):
                moved_age = float("inf")
            if moved_age < CLAIM_TTL_SEC:
                # What we moved was a sibling's FRESH takeover, written after our
                # read: hand it back and stand down.
                try:
                    os.rename(grave, path)
                except OSError:
                    grave.unlink(missing_ok=True)
                return False
            grave.unlink(missing_ok=True)
            try:
                with open(path, "x", encoding="utf-8") as fh:
                    fh.write(str(ts))
            except FileExistsError:
                return False
            return True
    except OSError:
        logger.debug("deletion digest claim not recorded; sending anyway", exc_info=True)
        return True


def _release_window(since: str) -> None:
    """Give a window back after a FAILED send, so a retry can take it. Without this
    a rejected card would leave the window claimed and never reported."""
    try:
        _claim_path(since).unlink(missing_ok=True)
    except OSError:
        logger.debug("deletion digest claim not released", exc_info=True)


def _prune_claims(*, older_than_sec: int = 86_400, now: float | None = None) -> int:
    """Delete claims for windows long since reported. One file per sent card would
    otherwise accumulate forever in the cache dir."""
    import time

    ts = time.time() if now is None else now
    removed = 0
    try:
        from navig.platform import paths

        folder = paths.cache_dir() / "deletion_digest"
        if not folder.is_dir():
            return 0
        for f in folder.glob("*.claim"):
            try:
                if ts - f.stat().st_mtime > older_than_sec:
                    f.unlink()
                    removed += 1
            except OSError:
                continue
    except Exception:  # noqa: BLE001
        logger.debug("deletion digest claim prune failed", exc_info=True)
    return removed


def _consume_unannounced(since: str, until: str) -> None:
    """Advance past a window holding only rows the digest never announces.

    A window of muted-chat (or quiet-reason) deletions counts zero, and leaving the
    watermark behind it kept those rows pending: unmuting the chat later announced
    deletions that happened while it was muted — the opposite of what mute
    promised, and of instant mode."""
    try:
        if not _store().count_deleted_since(since, until=until)["messages"]:
            return
    except Exception:  # noqa: BLE001
        logger.debug("deletion digest: unscoped count failed", exc_info=True)
        return
    # Never move the watermark backwards over a card a sibling just sent.
    if until > _watermark():
        _set_watermark(until)


async def flush_digest(channel: Any, *, force: bool = False) -> dict[str, Any]:
    """Send one digest card for everything deleted since the watermark.

    Returns what it did, so a caller (a test, the CLI) can assert rather than
    infer. ``force`` sends even in ``instant``/``off`` mode — that is how the CLI
    offers "show me now"."""
    async with _flush_lock:
        since = _watermark()
        # The window's upper bound, fixed BEFORE counting. A deletion recorded while
        # the card is being sent is not in it, so the watermark must stop here —
        # advancing to the time the send finished would skip that deletion for good.
        until = _iso(_now())
        try:
            counts = _store().count_deleted_since(since, until=until, **_digest_scope())
        except Exception:  # noqa: BLE001
            logger.warning("deletion digest count failed", exc_info=True)
            return {"sent": False, "reason": "count_failed"}
        if not counts["messages"]:
            _consume_unannounced(since, until)
            return {"sent": False, "reason": "nothing_pending", "since": since}
        if not force and mode() != "digest":
            return {"sent": False, "reason": f"mode={mode()}", "since": since}
        allowed, why = should_notify()
        if not allowed and not force:
            # Leave the watermark alone so the window is reported once quiet hours
            # lift, instead of being consumed in silence.
            return {"sent": False, "reason": why, "since": since}

        # Claimed AFTER every "don't send" check and BEFORE sending: a claim taken
        # for a window we then decline would block the sibling from sending it.
        # Honoured even under `force` — a duplicate card is never what anyone
        # asked for, and the TTL keeps a crashed sender from stranding it.
        #
        # Keyed on the STORED watermark when there is one: both processes read the
        # same persisted window start, while the newest row each one counts can
        # differ when a deletion lands between their two counts — which gave them
        # different keys and two cards. Only with no watermark stored (each
        # process then computes since = now - window itself, ms apart) is the
        # batch's newest deleted_at the better shared identity.
        batch_key = _claim_key(since, counts.get("latest"))
        if not _claim_window(batch_key):
            return {"sent": False, "reason": "claimed_by_sibling", "since": since}

        n, chats = counts["messages"], counts["chats"]
        head = _t("deletions.digest.head", n=n) or f"{n} message{'s' if n != 1 else ''} deleted"
        if chats > 1:
            head += _t("deletions.digest.chats", n=chats) or f" in {chats} chats"
        tail = _t("deletions.digest.tail") or "tap to see what they were"
        # Local time, like every other deletion surface (`format_when`). The card
        # printed the raw UTC window start ("19:31 UTC"), so an operator at UTC+2
        # read a time two hours off their own clock — on the one message whose job
        # is to say WHEN. Lazy import: `business` already imports this module.
        from navig.telegram.business import format_when

        when = format_when(since) or f"{since[:16].replace('T', ' ')} UTC"
        since_line = _t("deletions.digest.since", when=when) or f"since {when}"
        who = _who_lines(since, until=until)
        body = f"🗑 {head}\n" + (f"{who}\n" if who else "") + f"{since_line} · {tail}."
        # Start AND end of the window: an older card's Show must reveal what that
        # card counted, not every row since its start (~27 bytes, well under 64).
        token = f"{_token_for(since)}-{_token_for(until)}"
        show = _t("deletions.button.show", n=n) or f"Show {n}"
        delete = _t("deletions.button.delete") or "Delete"
        quiet = _t("deletions.button.quiet") or "Stop these alerts"
        # Delete is THIS card; the off switch is EVERY future card. They sat side by
        # side as "Show · Тихо", and the operator asked what Тихо did and for "just a
        # button to delete it" — so delete is its own button, and the switch says
        # what it switches, on its own row where it is not tapped by accident.
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": f"👁 {show}", "callback_data": f"{CB_PREFIX}show:{token}"},
                    {"text": f"🗑 {delete}", "callback_data": f"{CB_PREFIX}del"},
                ],
                [{"text": f"🔕 {quiet}", "callback_data": f"{CB_PREFIX}off"}],
            ]
        }
        ok = await _send(channel, body, keyboard=keyboard)
        if ok:
            # Advance ONLY on a delivered card. A failed send that moved the
            # watermark would silently drop the window it was reporting.
            _set_watermark(until)
            _prune_claims()
        else:
            # Hand the batch back so the next attempt — ours or the sibling's —
            # can take it. Keeping the claim would strand it until the TTL.
            _release_window(batch_key)
        return {"sent": bool(ok), "count": n, "chats": chats, "since": since}


#: Chats named on the card before it folds the rest into "+N more". The card is a
#: notification; past a handful of names it stops being glanceable.
WHO_MAX_CHATS = 5


def _who_lines(since: str, *, until: str | None = None) -> str:
    """One line per chat: who the deletions were in, and whose messages they were.

    The card used to say only "удалено: 2 в 2 чатах" — the operator had to tap Show
    to learn even WHICH conversations. It now names them. Names and counts only,
    never message text: this card is a notification preview (it shows on a lock
    screen), and the content deliberately stays behind the Show button.

    "Whose" is the SENDER. Telegram's deletion update does not say who deleted, but
    a deleted message is almost always removed by the person who sent it, so
    "from you" vs them is the honest proxy for "who deleted it"."""
    try:
        from navig.telegram import business

        owner_id = business.primary_owner()
    except Exception:  # noqa: BLE001
        owner_id = None
    try:
        rows = _store().deleted_by_chat_since(since, owner_id=owner_id, until=until,
                                              **_digest_scope())
    except Exception:  # noqa: BLE001
        # The preview is a courtesy on top of a card that already has its count and
        # its Show button; failing to build it must never cost the card.
        logger.debug("deletion digest: per-chat preview unavailable", exc_info=True)
        return ""
    lines: list[str] = []
    for row in rows[:WHO_MAX_CHATS]:
        name = (row.get("title") or "").strip() or str(row["chat_id"])
        n, mine = row["count"], row["mine"]
        line = _t("deletions.digest.row", name=name, n=n) or f"{name} — {n}"
        if mine and mine == n:
            line += " " + (_t("deletions.digest.row_mine_all") or "(from you)")
        elif mine:
            line += " " + (_t("deletions.digest.row_mine_some", k=mine) or f"({mine} from you)")
        lines.append(f"• {line}")
    if len(rows) > WHO_MAX_CHATS:
        extra = len(rows) - WHO_MAX_CHATS
        lines.append("• " + (_t("deletions.digest.more", n=extra) or f"+{extra} more"))
    return "\n".join(lines)


def _token_for(since: str) -> str:
    """Compact, self-contained callback token for a window start.

    Epoch MICROSECONDS, because ``callback_data`` is capped at 64 bytes and an ISO
    timestamp plus the prefix leaves little room. Self-contained on purpose: the
    button keeps working after the process that sent it is gone.

    It used to encode milliseconds while the window edge carried six fractional
    digits, so a Show decoded its ``until`` 1 ms EARLY: a card counted a row in that
    millisecond and then answered "Nothing left to show" for it (measured 2026-10-30
    in `test_deletion_policy` / `test_deletion_digest_scope`)."""
    try:
        dt = datetime.strptime(since[:26], "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            dt = datetime.strptime(since[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            dt = _now() - timedelta(seconds=window_sec())
    return str(int(dt.timestamp() * 1_000_000))


def since_from_token(token: str) -> str:
    """Inverse of :func:`_token_for`; falls back to one window ago on junk.

    Read by magnitude: a 16-digit number cannot be epoch milliseconds until the year
    33658, so a shorter token is one an older card minted in milliseconds. Cards
    already sitting in a chat keep their window instead of pointing at 1970."""
    text = str(token).strip()
    try:
        stamp = int(text)
    except (TypeError, ValueError):
        dt = _now() - timedelta(seconds=window_sec())
        return _iso(dt)
    scale = 1_000_000 if len(text) >= 16 else 1_000
    try:
        dt = datetime.fromtimestamp(stamp / scale, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        dt = _now() - timedelta(seconds=window_sec())
    return _iso(dt)


# ── Delivery ─────────────────────────────────────────────────────────────────


def should_notify() -> tuple[bool, str]:
    """Whether a deletion message may go out right now, and why not.

    Deletion output does NOT go through :class:`NotificationRouter` — the digest
    card carries an inline button and the router's telegram sink cannot attach
    one, and a log chat is not the router's target by definition. So the two
    router decisions that must still hold are consulted directly here rather than
    re-implemented: the master switch and the ``message_deleted`` type's channels
    (the operator muting that type has to mean something), plus quiet hours via
    the router's own helper so the two cannot drift apart."""
    try:
        from navig.notify import prefs
        from navig.notify.router import _in_quiet_hours

        settings = prefs.get_settings()
        if not settings["master_enabled"]:
            return False, "notifications master switch off"
        if "telegram" not in prefs.enabled_channels("message_deleted"):
            return False, "message_deleted muted for telegram"
        if settings["quiet_hours_enabled"] and _in_quiet_hours(
            datetime.now().hour, settings["quiet_hours_start"], settings["quiet_hours_end"]
        ):
            return False, "quiet hours"
    except Exception:  # noqa: BLE001
        # Prefs unavailable (a CLI, a test): deliver rather than silently drop —
        # the operator asked to be told about deletions.
        logger.debug("deletion notify prefs unavailable; delivering", exc_info=True)
    return True, ""


async def resolve_target(owner_id: Any = None) -> str | None:
    """Chat to deliver deletion alerts to: the configured log chat, else the
    owner's DM."""
    if t := target_chat():
        return t
    try:
        from navig.messaging.notify_operator import resolve_operator_chat_id

        if dm := resolve_operator_chat_id():
            return dm
    except Exception:  # noqa: BLE001
        pass
    return str(owner_id) if owner_id is not None else None


async def _send(channel: Any, text: str, *, keyboard: dict | None = None,
                owner_id: Any = None, target: str | None = None,
                sent: list[int] | None = None) -> bool:
    """Deliver one deletion message. Returns whether Telegram accepted it.

    ``sent`` collects the new message's id, so a caller can offer to remove what
    it just posted (the Show button's "clear these")."""
    if target is None:
        target = await resolve_target(owner_id)
    if target is None or channel is None:
        logger.warning("deletion alert not delivered — no target chat configured")
        return False
    data: dict[str, Any] = {"chat_id": target, "text": text}
    if keyboard:
        data["reply_markup"] = keyboard
    from navig.telegram import chat_tidy  # noqa: PLC0415

    try:
        with chat_tidy.routine("deletion"):
            result = await channel._api_call("sendMessage", data)
    except Exception:  # noqa: BLE001
        logger.warning("deletion alert send failed", exc_info=True)
        return False
    _collect(sent, result)
    return bool(result)


def _collect(sent: list[int] | None, result: Any) -> None:
    """Append a sent message's id to *sent*, when there is one to append."""
    if sent is None or not isinstance(result, dict):
        return
    mid = result.get("message_id")
    if isinstance(mid, int):
        sent.append(mid)


async def send_detail(channel: Any, text: str, *, target: str | None = None,
                      owner_id: Any = None, sent: list[int] | None = None) -> bool:
    """Public delivery for a rendered deletion report (no buttons)."""
    return await _send(channel, text, target=target, owner_id=owner_id, sent=sent)


class DirectBotChannel:
    """A minimal stand-in for the gateway's channel, for code that runs OUTSIDE it.

    Everything here needs exactly one thing from a channel — ``_api_call`` — and
    the CLI has no access to the live gateway object (different process). Rather
    than reimplement sending, or pretend a CLI flush cannot work, this speaks the
    Bot API with the same token the gateway uses, so `navig telegram business
    deletions flush` behaves identically whether or not the daemon is running."""

    def __init__(self, token: str) -> None:
        self.bot_token = token

    @classmethod
    def resolve(cls) -> "DirectBotChannel | None":
        try:
            from navig.messaging.secrets import resolve_telegram_bot_token

            token = (resolve_telegram_bot_token() or "").strip()
        except Exception:  # noqa: BLE001
            token = ""
        return cls(token) if token else None

    async def _api_call(self, method: str, data: dict | None = None) -> Any:
        import aiohttp

        url = f"https://api.telegram.org/bot{self.bot_token}/{method}"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=data or {}) as resp:
                body = await resp.json()
                if not body.get("ok"):
                    logger.warning("telegram %s rejected: %s", method, body.get("description"))
                    return None
                if method.startswith("send"):
                    from navig.telegram import chat_tidy  # noqa: PLC0415

                    chat_tidy.note(body.get("result"))
                return body.get("result")


# ── Deletions NAVIG made itself ──────────────────────────────────────────────
#
# The owner's trigger words — `tyler`, `translate`, `ping` — are deleted by the bot
# so the other person never sees them. Telegram then reports those deletions like
# any other, and they were recorded and put in the digest: "1 deleted in X (от
# тебя)", for a word the owner typed to the bot a second earlier. Measured in the
# gateway log 2026-09-29: `business deletion: chat=429106811 ids=1` right after a
# `tyler`. Each bot-made deletion is noted just BEFORE the call, so Telegram's
# report can never outrun it, and is skipped when the report arrives.

#: Telegram reports a deletion within seconds; anything older is not ours.
OWN_DELETION_TTL_SEC = 10 * 60


def _own_deletions_path():
    from navig.platform import paths  # noqa: PLC0415

    return paths.cache_dir() / "own_business_deletions.json"


def _own_key(bcid: Any, chat_id: Any, message_id: Any) -> str:
    # Scoped by chat: a message id is unique only within one chat, and one business
    # connection spans many — the reveal registry below keys by chat for the same reason.
    return f"{bcid}:{chat_id}:{message_id}"


def note_own_deletion(bcid: Any, message_ids: list[Any], *, chat_id: Any = None,
                      now: float | None = None) -> None:
    """Record that NAVIG is about to delete these business messages. Never raises."""
    import json  # noqa: PLC0415
    import time  # noqa: PLC0415

    if not bcid or not message_ids:
        return
    ts = time.time() if now is None else now
    path = _own_deletions_path()
    try:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = {k: v for k, v in data.items() if ts - float(v) < OWN_DELETION_TTL_SEC}
        for mid in message_ids:
            data[_own_key(bcid, chat_id, mid)] = ts
        from navig.core.yaml_io import atomic_write_text  # noqa: PLC0415

        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(data), encoding="utf-8")
    except Exception:  # noqa: BLE001 — at worst the trigger shows up in a digest
        logger.debug("could not note an own deletion", exc_info=True)


def is_own_deletion(bcid: Any, message_id: Any, *, chat_id: Any = None,
                    now: float | None = None) -> bool:
    """Was this deletion made by NAVIG in the last few minutes? Consumes the note."""
    import json  # noqa: PLC0415
    import time  # noqa: PLC0415

    if not bcid:
        return False
    ts = time.time() if now is None else now
    path = _own_deletions_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    key = _own_key(bcid, chat_id, message_id)
    noted = data.pop(key, None)
    if noted is None:
        return False
    try:
        from navig.core.yaml_io import atomic_write_text  # noqa: PLC0415

        atomic_write_text(path, json.dumps(data), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return ts - float(noted) < OWN_DELETION_TTL_SEC


# ── Clearing what a Show tap revealed ────────────────────────────────────────
#
# "Show" posts a report plus every cached file back into the operator's chat — two
# deleted videos are three messages. Asked for 2026-09-27: *"I want to be able to
# delete the messages when I press Show"*. So each Show remembers exactly which
# messages it posted, and offers one button that removes them all. The digest card
# is left alone: it can be shown again, and the record itself is never touched.

#: Telegram lets a bot delete its own messages for 48 hours; past that a clear
#: cannot succeed, so the entry is not worth keeping.
REVEAL_TTL_SEC = 48 * 3600


def _reveals_path():
    from navig.platform import paths  # noqa: PLC0415

    return paths.cache_dir() / "deletion_reveals.json"


def _read_reveals() -> dict[str, Any]:
    import json  # noqa: PLC0415

    try:
        data = json.loads(_reveals_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_reveals(data: dict[str, Any]) -> None:
    import json  # noqa: PLC0415

    from navig.core.yaml_io import atomic_write_text  # noqa: PLC0415

    path = _reveals_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(data, indent=2), encoding="utf-8")


def _remember_reveal(chat_id: Any, key: str, ids: list[int], *, now: float | None = None) -> None:
    import time  # noqa: PLC0415

    ts = time.time() if now is None else now
    data = {
        k: v
        for k, v in _read_reveals().items()
        if isinstance(v, dict) and ts - float(v.get("at", 0)) < REVEAL_TTL_SEC
    }
    data[f"{chat_id}:{key}"] = {"ids": list(ids), "at": ts}
    _write_reveals(data)


def _peek_reveal(chat_id: Any, key: str) -> list[int] | None:
    """The ids a Show posted in this chat, or None when already cleared.

    Scoped by chat: a key is only a message id, and the same id can exist in
    another chat. Read, not popped: the entry is settled only after the deletes
    ran (:func:`_settle_reveal`), so a delete that failed stays retryable."""
    entry = _read_reveals().get(f"{chat_id}:{key}")
    if not isinstance(entry, dict):
        return None
    return [int(i) for i in entry.get("ids") or []]


def _settle_reveal(chat_id: Any, key: str, failed: list[int]) -> None:
    """Drop the entry when every delete succeeded; otherwise keep only the ids
    that failed, so another tap retries exactly those. The original timestamp is
    kept, so the 48-hour expiry still applies."""
    data = _read_reveals()
    entry = data.get(f"{chat_id}:{key}")
    if not isinstance(entry, dict):
        return  # a concurrent tap already settled it
    still = [int(i) for i in entry.get("ids") or [] if int(i) in set(failed)]
    if still:
        data[f"{chat_id}:{key}"] = {**entry, "ids": still}
    else:
        data.pop(f"{chat_id}:{key}", None)
    _write_reveals(data)


async def _offer_clear(channel: Any, chat_id: Any, ids: list[int]) -> bool:
    """Post one "clear these" button under what a Show just revealed."""
    if not ids:
        return False
    key = str(ids[0])
    n = len(ids)
    label = _t("deletions.button.clear", n=n) or f"Clear these {n}"
    note = _t("deletions.reveal.note", n=n) or f"{n} shown. Clear them when you're done."
    keyboard = {"inline_keyboard": [[
        {"text": f"🧹 {label}", "callback_data": f"{CB_PREFIX}clr:{key}"},
    ]]}
    if not await _send(channel, note, keyboard=keyboard, target=str(chat_id)):
        return False
    _remember_reveal(chat_id, key, ids)
    return True


async def _clear_reveal(channel: Any, chat_id: Any, key: str, control_id: Any) -> str:
    ids = _peek_reveal(chat_id, key)
    if ids is None:
        return _t("deletions.toast.clear_gone") or "Already cleared"
    failed: list[int] = []
    for mid in ids:
        try:
            ok = await channel._api_call("deleteMessage", {"chat_id": chat_id, "message_id": mid})
        except Exception:  # noqa: BLE001
            ok = None
        if not ok:
            failed.append(mid)
    _settle_reveal(chat_id, key, failed)
    removed = len(ids) - len(failed)
    if not failed:
        # The button's own message goes last, and only once everything above is
        # gone: while anything failed it stays, so another tap can retry.
        if control_id is not None:
            try:
                await channel._api_call(
                    "deleteMessage", {"chat_id": chat_id, "message_id": control_id}
                )
            except Exception:  # noqa: BLE001
                logger.debug("could not remove the clear button message", exc_info=True)
        return _t("deletions.toast.cleared", n=removed) or f"Cleared {removed}"
    logger.warning(
        "deletion reveal clear: removed %d of %d in chat %s; the rest stay retryable "
        "(older than 48 h cannot be deleted by a bot)",
        removed, len(ids), chat_id,
    )
    return _t("deletions.toast.cleared_partial", n=removed, total=len(ids)) or (
        f"Cleared {removed} of {len(ids)} — tap again to retry"
    )


# ── Callback buttons ─────────────────────────────────────────────────────────


async def handle_callback(channel: Any, cb_data: str, chat_id: Any, message_id: Any,
                          user_id: Any) -> str:
    """Route a ``bizdel:`` tap. Returns the toast text.

    Gated by the ``business`` extension (``callback_prefixes``), so switching the
    business inbox off stops these buttons with everything else it owns."""
    payload = cb_data[len(CB_PREFIX):] if cb_data.startswith(CB_PREFIX) else cb_data

    if payload == "del":
        # Removes the card only. The record stays (`navig telegram business
        # deleted`), and what a Show posted keeps its own 🧹 button.
        try:
            ok = await channel._api_call(
                "deleteMessage", {"chat_id": chat_id, "message_id": message_id}
            )
        except Exception:  # noqa: BLE001
            ok = None
        if ok:
            return _t("deletions.toast.deleted") or "Deleted"
        # `_api_call` answers None for every refusal — a timeout, a rate limit, a
        # permission, or the 48-hour limit on a bot deleting its own messages — so
        # the toast must not claim the card is too old: that reads as permanent.
        return _t("deletions.toast.delete_failed") or (
            "Couldn't delete — try again (cards older than 48 h can't be removed)"
        )

    if payload == "off":
        try:
            set_mode("off")
        except ValueError:
            return "⚠️ Could not change the mode"
        note = _t("deletions.off.note") or (
            "Deletion alerts off. The record is still kept — see them with "
            "`navig telegram business deleted`, or turn alerts back on with "
            "`navig telegram business deletions mode digest`.")
        await _send(channel, f"🔕 {note}", target=str(chat_id))
        return _t("deletions.toast.off") or "Deletion alerts off"

    if payload.startswith("show:"):
        start, _, end = payload[len("show:"):].partition("-")
        since = since_from_token(start)
        # A card sent before the end bound existed carries only the start.
        until = since_from_token(end) if end else None
        try:
            rows = _store().list_deleted(since=since, until=until, limit=200,
                                         **_digest_scope())
        except Exception:  # noqa: BLE001
            logger.warning("deletion digest detail read failed", exc_info=True)
            return "⚠️ Could not read the deletions"
        if not rows:
            return "Nothing left to show"
        # Group by chat so each report reads like the instant alert it replaces.
        by_chat: dict[Any, list[Any]] = {}
        for r in rows:
            by_chat.setdefault(r["chat_id"], []).append(r["message_id"])
        from navig.telegram import business as biz

        shown = 0
        posted: list[int] = []
        for cid, ids in by_chat.items():
            chat = {"id": cid}
            try:
                room = _store().get_room(cid) or {}
                # A private chat has no title; the renderer falls back to the
                # cached one, which is what keeps a person's name out of the id.
                chat["title"] = room.get("title")
                chat["type"] = room.get("type")
            except Exception:  # noqa: BLE001
                pass
            # check_prefs=False: the operator TAPPED for this. Quiet hours mute the
            # unprompted alert, not an answer to a deliberate request.
            if await biz.deliver_deletion_detail(
                channel, chat, ids, target=str(chat_id), check_prefs=False, sent=posted
            ):
                shown += len(ids)
        if posted:
            await _offer_clear(channel, chat_id, posted)
        return f"Showed {shown}" if shown else "⚠️ Could not deliver the detail"

    if payload.startswith("clr:"):
        return await _clear_reveal(channel, chat_id, payload[len("clr:"):], message_id)

    return ""   # unknown sub-action: the caller answers with a generic toast
