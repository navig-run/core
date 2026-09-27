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
    # Matches BaseStore._utcnow(): "%Y-%m-%dT%H:%M:%S.%fZ" truncated to ms.
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


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


def status() -> dict[str, Any]:
    """Everything a status surface needs, in one read."""
    return {
        "record": record_enabled(),
        "mode": mode(),
        "window_sec": window_sec(),
        "target": target_chat(),
        "muted_chats": sorted(muted_chats()),
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
        return str(raw)
    return _iso(_now() - timedelta(seconds=window_sec()))


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
    return {"kind": "business", "exclude_chats": muted_chats()}


def _store():
    from navig.store.telegram_catalog import TelegramCatalogStore

    return TelegramCatalogStore()


def arm_digest(channel: Any, *, delay: int | None = None) -> None:
    """Ensure a flush is scheduled. Idempotent while one is pending, so a burst of
    deletions produces ONE card rather than one per event.

    ``delay`` overrides the window — resume uses a short one, because the
    deletions it finds already waited out (part of) a window before the restart."""
    global _flush_task
    if _flush_task is not None and not _flush_task.done():
        return
    delay = window_sec() if delay is None else max(0, int(delay))

    async def _later() -> None:
        try:
            await asyncio.sleep(delay)
            await flush_digest(channel)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("deletion digest flush failed", exc_info=True)

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


def _claim_window(since: str, *, now: float | None = None) -> bool:
    """True if this process owns the card for the window starting at *since*.

    Atomic ``open(..., "x")``, never read-then-write: the two processes resume in
    the same second. Fails OPEN — if the claim cannot be recorded, sending one
    card too many beats a digest that is never sent."""
    import os
    import time

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
            tmp = path.with_suffix(".claim.tmp")
            tmp.write_text(str(ts), encoding="utf-8")
            os.replace(tmp, path)
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


async def flush_digest(channel: Any, *, force: bool = False) -> dict[str, Any]:
    """Send one digest card for everything deleted since the watermark.

    Returns what it did, so a caller (a test, the CLI) can assert rather than
    infer. ``force`` sends even in ``instant``/``off`` mode — that is how the CLI
    offers "show me now"."""
    async with _flush_lock:
        since = _watermark()
        try:
            counts = _store().count_deleted_since(since, **_digest_scope())
        except Exception:  # noqa: BLE001
            logger.warning("deletion digest count failed", exc_info=True)
            return {"sent": False, "reason": "count_failed"}
        if not counts["messages"]:
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
        # Keyed on the BATCH (its newest deleted_at), never on `since`. With no
        # watermark stored, each process computes since = now - window itself, a
        # few ms apart, so a since-keyed claim gave the two processes different
        # keys and BOTH sent — caught by the race test, on exactly the first-run
        # path. The rows are the one thing both processes see identically.
        batch_key = counts.get("latest") or since
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
        body = f"🗑 {head}\n{since_line} · {tail}."
        token = _token_for(since)
        show = _t("deletions.button.show", n=n) or f"Show {n}"
        quiet = _t("deletions.button.quiet") or "Quiet"
        keyboard = {
            "inline_keyboard": [[
                {"text": f"👁 {show}", "callback_data": f"{CB_PREFIX}show:{token}"},
                {"text": f"🔕 {quiet}", "callback_data": f"{CB_PREFIX}off"},
            ]]
        }
        ok = await _send(channel, body, keyboard=keyboard)
        if ok:
            # Advance ONLY on a delivered card. A failed send that moved the
            # watermark would silently drop the window it was reporting.
            _set_watermark(_iso(_now()))
            _prune_claims()
        else:
            # Hand the batch back so the next attempt — ours or the sibling's —
            # can take it. Keeping the claim would strand it until the TTL.
            _release_window(batch_key)
        return {"sent": bool(ok), "count": n, "chats": chats, "since": since}


def _token_for(since: str) -> str:
    """Compact, self-contained callback token for a window start.

    Epoch milliseconds, because ``callback_data`` is capped at 64 bytes and an ISO
    timestamp plus the prefix leaves little room. Self-contained on purpose: the
    button keeps working after the process that sent it is gone."""
    try:
        dt = datetime.strptime(since[:23], "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            dt = datetime.strptime(since[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            dt = _now() - timedelta(seconds=window_sec())
    return str(int(dt.timestamp() * 1000))


def since_from_token(token: str) -> str:
    """Inverse of :func:`_token_for`; falls back to one window ago on junk."""
    try:
        dt = datetime.fromtimestamp(int(token) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
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
                owner_id: Any = None, target: str | None = None) -> bool:
    """Deliver one deletion message. Returns whether Telegram accepted it."""
    if target is None:
        target = await resolve_target(owner_id)
    if target is None or channel is None:
        logger.warning("deletion alert not delivered — no target chat configured")
        return False
    data: dict[str, Any] = {"chat_id": target, "text": text}
    if keyboard:
        data["reply_markup"] = keyboard
    try:
        return bool(await channel._api_call("sendMessage", data))
    except Exception:  # noqa: BLE001
        logger.warning("deletion alert send failed", exc_info=True)
        return False


async def send_detail(channel: Any, text: str, *, target: str | None = None,
                      owner_id: Any = None) -> bool:
    """Public delivery for a rendered deletion report (no buttons)."""
    return await _send(channel, text, target=target, owner_id=owner_id)


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
                return body.get("result")


# ── Callback buttons ─────────────────────────────────────────────────────────


async def handle_callback(channel: Any, cb_data: str, chat_id: Any, message_id: Any,
                          user_id: Any) -> str:
    """Route a ``bizdel:`` tap. Returns the toast text.

    Gated by the ``business`` extension (``callback_prefixes``), so switching the
    business inbox off stops these buttons with everything else it owns."""
    payload = cb_data[len(CB_PREFIX):] if cb_data.startswith(CB_PREFIX) else cb_data

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
        since = since_from_token(payload[len("show:"):])
        try:
            rows = _store().list_deleted(since=since, limit=200, **_digest_scope())
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
                channel, chat, ids, target=str(chat_id), check_prefs=False
            ):
                shown += len(ids)
        return f"Showed {shown}" if shown else "⚠️ Could not deliver the detail"

    return ""   # unknown sub-action: the caller answers with a generic toast
