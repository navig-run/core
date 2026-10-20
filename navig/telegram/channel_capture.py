"""Post in your Reminders channel → it becomes a NAVIG task with a reminder.

The ``reminder`` notification type can be routed to its own Telegram channel
(``navig notify route set reminder <chat>`` / Mini App → Notifications →
Telegram chats). That channel is then also an *input*: a post like
``tomorrow 9:00 dentist #health`` is parsed with the same grammar as ``/todo``
and the Mini App quick-add, saved as a task, and its reminder is scheduled. The
reminder rings back in the same channel when it is due, because reminders follow
the route.

Trust model: only posts in the chat that is the **current reminder route** are
read, and only ``channel_post`` updates — in a channel only its admins can post,
and the operator chose that channel. Everything else is ignored here and flows
on unchanged. A re-delivered post (webhook retry, two processes) is a no-op:
the task is keyed on ``tgchan:<chat>:<message>``.
"""

from __future__ import annotations

import asyncio
import html
import logging
from typing import Any

logger = logging.getLogger("navig.telegram.channel_capture")

#: Our own cards start with these; never re-capture something NAVIG posted.
_OWN_PREFIXES = ("⏰", "✅", "⚠️", "📝")


def capture_post(chat_id: int, message_id: int, text: str) -> dict[str, Any]:
    """Save *text* as a task. Returns ``{status, title, when}``.

    ``status``: ``scheduled`` (has a time → reminder set) · ``added`` (no time
    found — saved without a reminder) · ``duplicate`` (already captured).
    Raises ``navig.pim.service.PlannerError`` for a refused add (time passed,
    list full).
    """
    from navig.pim import service
    from navig.pim.clock import from_utc_iso, local_now, to_local
    from navig.pim.dates import format_local

    item = service.create_task(text, origin="manual", origin_ref=f"tgchan:{chat_id}:{message_id}")
    if item is None:
        return {"status": "duplicate", "title": "", "when": ""}
    # Remember this post's own id so a later reply to IT ("done", "snooze 1h")
    # finds the task (manage_reply).
    _remember(chat_id, message_id, str(item["id"]))
    due = to_local(from_utc_iso(item.get("due_at")))
    if due is None:
        return {"status": "added", "title": str(item.get("title") or ""), "when": ""}
    now = local_now()
    if due <= now:
        # Saved, but a past time would never ring — say so instead of pretending.
        return {"status": "past", "title": str(item.get("title") or ""), "when": format_local(due, now)}
    return {"status": "scheduled", "title": str(item.get("title") or ""), "when": format_local(due, now)}


def _reply_text(result: dict[str, Any]) -> str | None:
    title = html.escape(result.get("title") or "")
    when = html.escape(result.get("when") or "")
    status = result.get("status")
    if status == "scheduled":
        return f"✅ Reminder set — <b>{when}</b>\n{title}"
    if status == "added":
        return (
            f"📝 Added to your tasks: {title}\n"
            "<i>No time found, so no reminder — post e.g. “tomorrow 9:00 dentist”.</i>"
        )
    if status == "past":
        return f"⚠️ Saved “{title}”, but {when} has already passed — no reminder will ring."
    return None  # duplicate: say nothing a second time


# ── Reply-to-manage: answer a confirmation to run the action ────────────────

_TARGETS_SCHEMA = """
CREATE TABLE IF NOT EXISTS notify_tg_chan_targets (
  chat_id INTEGER NOT NULL,
  message_id INTEGER NOT NULL,
  task_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (chat_id, message_id)
)
"""

#: Message→task mappings kept per chat. Old confirmations are pruned; answering
#: one from months ago is not a thing anyone does.
_PRUNE_KEEP = 500

#: The operator replies "done" / "cancel" / "snooze 1h" to a card in this channel.
_DONE_WORDS = frozenset({"done", "complete", "completed", "ok", "ready", "✓", "✅"})
_CANCEL_WORDS = frozenset({"cancel", "delete", "del", "remove", "stop", "abort"})
_SNOOZE_WORDS = frozenset({"snooze", "later", "remind", "remindme"})

_HINT = "Reply <code>done</code>, <code>cancel</code> or <code>snooze 1h</code> to manage it."
_MANAGE_HEADS = _DONE_WORDS | _CANCEL_WORDS | _SNOOZE_WORDS


def _targets():
    from navig.notify import store

    store.init_db()
    c = store.conn()
    c.execute(_TARGETS_SCHEMA)
    return c


def _remember(chat_id: int, message_id: int, task_id: str, *, now: str | None = None) -> None:
    """Map a message this channel sent (or received) to the task it stands for."""
    try:
        from navig.notify import store

        c = _targets()
        with c:
            c.execute(
                "INSERT OR REPLACE INTO notify_tg_chan_targets "
                "(chat_id, message_id, task_id, created_at) VALUES (?, ?, ?, ?)",
                (chat_id, message_id, task_id, now or store.now_iso()),
            )
        # Keep it bounded: one row per confirmation adds up over a year, and nobody
        # answers one from months ago. Keep the newest 500 per chat.
        # (An earlier version compared created_at against MIN(created_at) — a condition
        # that is never true, so it pruned nothing.)
        with c:
            c.execute(
                "DELETE FROM notify_tg_chan_targets WHERE chat_id = ? AND rowid NOT IN "
                "(SELECT rowid FROM notify_tg_chan_targets WHERE chat_id = ? "
                "ORDER BY rowid DESC LIMIT ?)",
                (chat_id, chat_id, _PRUNE_KEEP),
            )
    except Exception:  # noqa: BLE001 — mapping is a convenience, never a delivery
        logger.warning("reminder reply mapping failed", exc_info=True)


def _target_task(chat_id: int, message_id: int) -> str | None:
    if not message_id:
        return None
    try:
        r = _targets().execute(
            "SELECT task_id FROM notify_tg_chan_targets WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        ).fetchone()
        return str(r["task_id"]) if r else None
    except Exception:  # noqa: BLE001
        logger.debug("reminder reply lookup failed", exc_info=True)
        return None


def _parse_duration(arg: str) -> int | None:
    """``"1h"`` / ``"30m"`` / ``"2d"`` / ``"90"`` → seconds; ``None`` when unreadable."""
    import re

    raw = (arg or "").strip().lower().replace(" ", "")
    if not raw:
        return 60 * 60  # "snooze" alone → an hour, the default everyone expects
    m = re.fullmatch(r"(\d+)\s*([smhd]?)", raw)
    if not m:
        return None
    n = int(m.group(1))
    mult = {"": 60, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2) or "m"]
    secs = n * mult
    return secs if 60 <= secs <= 7 * 86400 else None


def apply_manage(task_id: str, text: str) -> str:
    """Act on ``done``/``cancel``/``snooze <n>h`` for one task. Never raises.

    Returns the reply the operator sees; an unrecognized reply gets the hint
    rather than being stored as a new task ("done" is not a to-do).
    """
    from datetime import timedelta

    from navig.pim import service
    from navig.pim.clock import from_utc_iso, local_now, to_local
    from navig.pim.dates import format_local

    w = (text or "").strip().lower().lstrip("/").strip(" 	.!?:,")
    head, _, arg = w.partition(" ")
    try:
        item = service.get(task_id)
    except Exception:  # noqa: BLE001 — NotFound, or the store moved on since
        return "That task is gone — it was already done or removed."

    title = html.escape(str(item.get("title") or "").strip() or "task")
    try:
        if head in _DONE_WORDS:
            service.complete(task_id)
            return f"✅ Done — {title}"
        if head in _CANCEL_WORDS:
            service.delete(task_id, actor_is_owner=True)
            return f"🗑 Removed — {title}"
        if head in _SNOOZE_WORDS:
            # get_todo reports completion as `completed_at`; `done` is only a
            # subtask column, so reading `item["done"]` here never fired.
            if item.get("completed_at"):
                return f"Already done: {title}"
            secs = _parse_duration(arg)
            if secs is None:
                return "That snooze length didn't read. Try <code>snooze 30m</code>, <code>snooze 2h</code> or <code>snooze 1d</code>."
            due = from_utc_iso(item.get("due_at"))
            if due is None:
                # Never moved before: start counting from now.
                from navig.pim.clock import to_utc_iso

                due = to_utc_iso(local_now())
                due = from_utc_iso(due)
            new_due = due + timedelta(seconds=secs)  # type: ignore[operator]
            service.update(task_id, {"due_at": new_due})
            return f"⏰ Moved to <b>{html.escape(format_local(to_local(new_due), local_now()))}</b> — {title}"
    except Exception as exc:  # noqa: BLE001
        logger.warning("reminder channel manage %s failed: %s", head, exc)
        return f"⚠️ Couldn't do that: {html.escape(str(exc))}"
    return _HINT


async def _say(channel: Any, chat_id: int, reply_to: int, text: str) -> None:
    """Send a silent reply card in the channel; never raises."""
    try:
        await channel._api_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "reply_to_message_id": reply_to,
                # The sound in this channel means "due now" — a receipt must not ring.
                "disable_notification": True,
            },
        )
    except Exception:  # noqa: BLE001
        logger.warning("reminder channel reply failed", exc_info=True)


def _is_manage_word(text: str) -> bool:
    """Is *text* a bare management command? (used to route an unknown-target reply)"""
    w = (text or "").strip().lower().lstrip("/").strip(" 	.!?:,")
    return w.split(" ", 1)[0] in _MANAGE_HEADS


async def handle_channel_post(channel: Any, post: dict[str, Any]) -> bool:
    """Capture *post* if it was made in the reminder channel. True when consumed."""
    chat_id = int((post.get("chat") or {}).get("id") or 0)
    if not chat_id:
        return False
    from navig.notify import telegram_routes

    route = await asyncio.to_thread(telegram_routes.get_route, "reminder")
    if route is None or route.chat_id != chat_id:
        return False

    text = str(post.get("text") or post.get("caption") or "").strip()
    if not text or text.startswith(_OWN_PREFIXES) or text.startswith("/"):
        return True  # it's the reminder channel — just nothing to capture

    message_id = int(post.get("message_id") or 0)

    # A reply to a message this channel already stands for is MANAGEMENT, not a new
    # task — "done" must not be stored as a to-do called "done". A reply to something
    # we do not recognise, typed as a management verb, gets an explanation instead of
    # becoming a junk task.
    reply_to = int(post.get("reply_to_message_id") or 0)
    if reply_to:
        task_id = await asyncio.to_thread(_target_task, chat_id, reply_to)
        if task_id is not None:
            await _say(channel, chat_id, message_id, await asyncio.to_thread(apply_manage, task_id, text))
            return True
        if _is_manage_word(text):
            await _say(channel, chat_id, message_id, _HINT + " (Reply to one of my ✅ confirmations.)")
            return True

    try:
        result = await asyncio.to_thread(capture_post, chat_id, message_id, text)
        reply = _reply_text(result)
    except Exception as exc:  # noqa: BLE001 — PlannerError and store errors both reach the user
        logger.warning("reminder channel capture failed: %s", exc)
        reply = f"⚠️ Couldn't add that: {html.escape(str(exc))}"

    if reply:
        try:
            sent = await channel._api_call(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": reply,
                    "parse_mode": "HTML",
                    "reply_to_message_id": message_id,
                    # The confirmation must not ring: the sound in this channel means "due now".
                    "disable_notification": True,
                },
            )
            # Map the card we just sent too, so the operator can answer THAT one
            # ("done" to the receipt, not to their own post).
            new_id = int((sent or {}).get("message_id") or 0) if isinstance(sent, dict) else 0
            task_id = await asyncio.to_thread(_target_task, chat_id, message_id)
            if new_id and task_id:
                await asyncio.to_thread(_remember, chat_id, new_id, task_id)
        except Exception:  # noqa: BLE001 — the task is saved; a missing confirmation is cosmetic
            logger.warning("reminder channel: confirmation reply failed", exc_info=True)
    return True
