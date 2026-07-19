"""Business-chat commands — owner & counterparty triggers that post their result
INTO the conversation AS the owner (via the business connection). The owner's
trigger message is deleted so the chat shows only the clean result.

  ping              anyone   → a short playful pong
  time              anyone   → the current time, posted as the owner
  timer <duration>  anyone   → a live countdown (edits one message in place)
  timer cancel      anyone   → stop the running countdown  (also: timer_cancel)

``role …`` is owner-only and lives in :mod:`navig.telegram.autoreply`.

Permission per command is ``"all"`` (anyone in the chat) or ``"owner"``. Output
always goes into the conversation as the owner; the owner's trigger is removed.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# Playful pong variants (shared shape with business._PONGS).
_PONGS = (
    "🏓 pong", "🏓 king pong", "🏓 pong gong", "🏓 gnop", "🏓 ponguuuuuuuuuuuuuuuuuuuuuuw",
    "🏓 pong pong", "🏓 p0ng", "🏓 pongo", "🏓 pongggg", "🏓 ping? pong.", "🏓 pôńg",
)

# One live countdown per chat (asyncio.Task), so a new timer / cancel supersedes it.
_TIMERS: dict[int, asyncio.Task] = {}
_MAX_TIMER_S = 24 * 3600

_DUR_RE = re.compile(r"(\d+)\s*(h|hours?|m|min|mins?|minutes?|s|sec|secs?|seconds?)", re.IGNORECASE)


# ── low-level send/edit/delete as the owner ─────────────────────────────────


async def _send(channel: Any, chat_id: int, bcid: str | None, text: str,
                parse_mode: str = "HTML") -> int | None:
    data: dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if bcid:
        data["business_connection_id"] = bcid
    try:
        res = await channel._api_call("sendMessage", data)
        return res.get("message_id") if isinstance(res, dict) else None
    except Exception:  # noqa: BLE001
        logger.debug("biz_commands send failed", exc_info=True)
        return None


async def _chat_action(channel: Any, chat_id: int, bcid: str | None) -> None:
    """Show 'typing…' (used while a slow lookup command fetches data)."""
    try:
        data: dict[str, Any] = {"chat_id": chat_id, "action": "typing"}
        if bcid:
            data["business_connection_id"] = bcid
        await channel._api_call("sendChatAction", data)
    except Exception:  # noqa: BLE001
        pass


async def _edit(channel: Any, chat_id: int, bcid: str | None, message_id: int, text: str) -> None:
    data: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if bcid:
        data["business_connection_id"] = bcid
    try:
        await channel._api_call("editMessageText", data)
    except Exception:  # noqa: BLE001
        pass  # edit can fail near the end (message identical / deleted) — non-fatal


async def _delete(channel: Any, chat_id: int | None, bcid: str | None, message_id: int | None) -> None:
    if message_id is None:
        return
    try:
        if bcid:
            # Business chats require deleteBusinessMessages (deleteMessage +
            # business_connection_id fails "message to delete not found").
            await channel._api_call(
                "deleteBusinessMessages",
                {"business_connection_id": bcid, "message_ids": [message_id]},
            )
        elif chat_id is not None:
            await channel._api_call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
    except Exception:  # noqa: BLE001
        pass


# ── helpers ─────────────────────────────────────────────────────────────────


def parse_duration(text: str) -> int:
    """Sum every ``<n> <unit>`` token → seconds. "1h 30m"→5400, "30 seconds"→30."""
    total = 0
    for n, unit in _DUR_RE.findall(text or ""):
        u = unit.lower()
        mult = 3600 if u.startswith("h") else 60 if u.startswith("m") else 1
        total += int(n) * mult
    return total


def _fmt(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# ── command handlers ─────────────────────────────────────────────────────────


async def _cmd_ping(channel, chat_id, bcid, arg, **_):
    await _send(channel, chat_id, bcid, random.choice(_PONGS))


async def _cmd_time(channel, chat_id, bcid, arg, **_):
    now = datetime.now()
    await _send(channel, chat_id, bcid, "🕐 " + now.strftime("%H:%M") + " · " + now.strftime("%A %d %B"))


_OR_SPLIT = re.compile(r"\s+(?:or|ou|или|o|oder)\s+|\s*[,|/]\s*", re.IGNORECASE)


async def _cmd_choice(channel, chat_id, bcid, arg, **_):
    """`choice a or b` / `choice x, y, z` → pick one at random (ported from the lab)."""
    opts = [o.strip() for o in _OR_SPLIT.split(arg or "") if o.strip()]
    if len(opts) < 2:
        await _send(channel, chat_id, bcid, "🤔 Give me options: <code>choice pizza or sushi</code>")
        return
    await _send(channel, chat_id, bcid, "🎯 " + random.choice(opts))


async def _cmd_flip(channel, chat_id, bcid, arg, **_):
    await _send(channel, chat_id, bcid, "🪙 " + random.choice(("Heads", "Tails")))


async def _cmd_dice(channel, chat_id, bcid, arg, **_):
    m = re.search(r"\d+", arg or "")
    sides = max(2, min(1000, int(m.group()))) if m else 6
    await _send(channel, chat_id, bcid, f"🎲 {random.randint(1, sides)}  (d{sides})")


_8BALL = (
    "Yes.", "No.", "Definitely.", "Absolutely not.", "Ask again later.", "Most likely.",
    "Don't count on it.", "Without a doubt.", "Very doubtful.", "Signs point to yes.",
    "Better not tell you now.", "It is certain.", "My sources say no.", "Outlook good.",
)


async def _cmd_8ball(channel, chat_id, bcid, arg, **_):
    if not (arg or "").strip():
        await _send(channel, chat_id, bcid, "🎱 Ask me a question: <code>8ball will it rain?</code>")
        return
    await _send(channel, chat_id, bcid, "🎱 " + random.choice(_8BALL))


# ── external-data lookups (weather / crypto / currency / whois) ──────────────


async def _cmd_weather(channel, chat_id, bcid, arg, **_):
    from navig.telegram import biz_lookups

    await _send(channel, chat_id, bcid, await biz_lookups.weather(arg))


async def _cmd_crypto(channel, chat_id, bcid, arg, **_):
    from navig.telegram import biz_lookups

    parts = (arg or "").split()
    sym = parts[0] if parts else "btc"
    vs = parts[1] if len(parts) > 1 else "usd"
    await _send(channel, chat_id, bcid, await biz_lookups.crypto(sym, vs))


async def _cmd_currency(channel, chat_id, bcid, arg, **_):
    from navig.telegram import biz_lookups

    await _send(channel, chat_id, bcid, await biz_lookups.currency(arg))


async def _cmd_whois(channel, chat_id, bcid, arg, **_):
    from navig.telegram import biz_lookups

    await _send(channel, chat_id, bcid, await biz_lookups.whois(arg))


async def _cmd_timer(channel, chat_id, bcid, arg, **_):
    if (arg or "").strip().lower() in ("cancel", "stop", "off"):
        await _cmd_timer_cancel(channel, chat_id, bcid, "")
        return
    secs = parse_duration(arg)
    if secs <= 0:
        await _send(channel, chat_id, bcid, "⏱ Usage: <code>timer 30 seconds</code> · <code>timer 5 min</code>")
        return
    secs = min(secs, _MAX_TIMER_S)
    # Supersede any existing timer in this chat.
    await _cancel_task(chat_id)
    mid = await _send(channel, chat_id, bcid, f"⏳ {_fmt(secs)}")
    if mid is None:
        return
    _TIMERS[chat_id] = asyncio.create_task(_run_countdown(channel, chat_id, bcid, mid, secs))


async def _cmd_timer_cancel(channel, chat_id, bcid, arg, **_):
    if await _cancel_task(chat_id):
        await _send(channel, chat_id, bcid, "⏹ Timer cancelled.")
    else:
        await _send(channel, chat_id, bcid, "No timer running.")


async def _cancel_task(chat_id: int) -> bool:
    task = _TIMERS.pop(chat_id, None)
    if task and not task.done():
        task.cancel()
        return True
    return False


async def _run_countdown(channel, chat_id, bcid, message_id: int, total: int) -> None:
    """Edit ``message_id`` in place every few seconds until the timer elapses."""
    try:
        remaining = total
        while remaining > 0:
            # Tighter cadence near the end so it feels live; gentler for long timers.
            step = 1 if remaining <= 10 else 5 if total <= 120 else 15 if total <= 1800 else 30
            step = min(step, remaining)
            await asyncio.sleep(step)
            remaining -= step
            await _edit(channel, chat_id, bcid, message_id, f"⏳ {_fmt(remaining)}")
        await _edit(channel, chat_id, bcid, message_id, "⏰ Time's up!")
    except asyncio.CancelledError:
        await _edit(channel, chat_id, bcid, message_id, "⏹ Timer cancelled.")
    finally:
        _TIMERS.pop(chat_id, None)


# ── registry + dispatch ──────────────────────────────────────────────────────

_COMMANDS: dict[str, dict[str, Any]] = {
    "ping": {"perm": "all", "fn": _cmd_ping},
    "time": {"perm": "all", "fn": _cmd_time},
    "timer": {"perm": "all", "fn": _cmd_timer},
    "timer_cancel": {"perm": "all", "fn": _cmd_timer_cancel},
    # fun / utility (ported from the lab's SecondBot command handlers)
    "choice": {"perm": "all", "fn": _cmd_choice},
    "flip": {"perm": "all", "fn": _cmd_flip},
    "coin": {"perm": "all", "fn": _cmd_flip},
    "dice": {"perm": "all", "fn": _cmd_dice},
    "roll": {"perm": "all", "fn": _cmd_dice},
    "8ball": {"perm": "all", "fn": _cmd_8ball},
    # external-data lookups — slow (network), so show "typing…" while they fetch
    "weather": {"perm": "all", "fn": _cmd_weather, "typing": True},
    "crypto": {"perm": "all", "fn": _cmd_crypto, "typing": True},
    "currency": {"perm": "all", "fn": _cmd_currency, "typing": True},
    "convert": {"perm": "all", "fn": _cmd_currency, "typing": True},
    "whois": {"perm": "all", "fn": _cmd_whois, "typing": True},
}


async def dispatch(channel: Any, msg: dict, *, is_owner: bool, owner_id: int | None) -> bool:
    """Match + run a business-chat command. Returns True if one handled the message.

    Owner-issued commands have their trigger message deleted first so the chat
    shows only the result (posted as the owner). Counterparty commands just reply.
    """
    text = (msg.get("text") or "").strip()
    if not text:
        return False
    head = text.lower().lstrip("/").strip().split(None, 1)
    if not head:
        return False
    name = head[0]
    cmd = _COMMANDS.get(name)
    if not cmd:
        return False
    if cmd["perm"] == "owner" and not is_owner:
        return False

    chat_id = (msg.get("chat") or {}).get("id")
    bcid = msg.get("business_connection_id")
    arg = head[1] if len(head) > 1 else ""

    if is_owner:
        await _delete(channel, chat_id, bcid, msg.get("message_id"))
    if cmd.get("typing"):
        await _chat_action(channel, chat_id, bcid)  # 'typing…' while the lookup fetches
    try:
        await cmd["fn"](channel, chat_id, bcid, arg, is_owner=is_owner, owner_id=owner_id)
    except Exception:  # noqa: BLE001
        logger.warning("business command %r failed", name, exc_info=True)
    return True
