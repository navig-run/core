"""
plugins/timer.py — Live countdown timer (edits its own message).
Command : /timer <duration>   e.g. /timer 30s  /timer 5m  /timer 2h
Alias   : /countdown
Passive : "set timer for 5 minutes" / "timer 30 seconds"
Limits  : 5 s min, 24 h max, 3 concurrent per user.
"""

from __future__ import annotations

import asyncio
import re
from typing import Dict, Tuple

from telegram import Update
from telegram.ext import ContextTypes

try:
    from plugin_base import BotPlugin, PluginMeta
except ImportError:
    from ..plugin_base import BotPlugin, PluginMeta  # type: ignore

_TIMERS: Dict[Tuple[int, int], asyncio.Task] = {}


def _parse(text: str) -> int | None:
    text = text.strip().lower()
    total, found = 0, False
    for val, unit in re.findall(r"(\d+)\s*([smh]?)", text):
        v = int(val)
        if unit == "h":
            total += v * 3600
            found = True
        elif unit == "m":
            total += v * 60
            found = True
        elif unit == "s":
            total += v
            found = True
        elif not found:
            total += v
            found = True  # bare number = seconds
    return total if found and total > 0 else None


def _fmt(s: int) -> str:
    if s >= 3600:
        h, r = divmod(s, 3600)
        m, sec = divmod(r, 60)
        return f"{h}h {m:02d}m {sec:02d}s"
    return f"{s//60}m {s%60:02d}s" if s >= 60 else f"{s}s"


class TimerPlugin(BotPlugin):
    """Live countdown timer — edits its own message every tick."""

    @property
    def meta(self):
        return PluginMeta(
            "timer", "Countdown timer: /timer 5m · /timer 30s · /timer 2h", "1.0.0"
        )

    @property
    def command(self):
        return "timer"

    @property
    def passive_patterns(self):
        return [
            r"\bset\s+(?:a\s+)?timer\s+(?:for\s+)?\d+",
            r"\btimer\s+\d+\s*[smh]",
            r"\bcountdown\s+\d+",
        ]

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        raw = " ".join(context.args or [])
        if not raw:
            await update.message.reply_text(
                "⏱ <b>Timer</b>\n\nUsage: <code>/timer &lt;duration&gt;</code>\n\n"
                "• <code>/timer 30s</code> · <code>/timer 5m</code> · <code>/timer 2h</code> · <code>/timer 1h30m</code>\n\n"
                "Or say: <i>set a timer for 10 minutes</i>",
                parse_mode="HTML",
            )
            return
        await self._start(update, raw)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text or ""
        m = re.search(
            r"(\d+)\s*(h(?:ours?)?|m(?:in(?:utes?)?)?|s(?:ec(?:onds?)?)?)(?:\s+(\d+)\s*(m(?:in(?:utes?)?)?|s(?:ec(?:onds?)?)?))?",
            text,
            re.IGNORECASE,
        )
        if m:
            await self._start(update, "".join(filter(None, m.groups())))

    async def _start(self, update, dur_str):
        uid = update.effective_user.id
        secs = _parse(dur_str)
        if secs is None:
            await update.message.reply_text(
                "⚠️ Can't parse duration. Try <code>/timer 5m</code>.", parse_mode="HTML"
            )
            return
        if secs < 5:
            await update.message.reply_text("⚠️ Minimum timer is 5 seconds.")
            return
        if secs > 86400:
            await update.message.reply_text("⚠️ Maximum timer is 24 hours.")
            return
        used = [k for k in _TIMERS if k[0] == uid]
        if len(used) >= 3:
            await update.message.reply_text("⚠️ You already have 3 active timers.")
            return
        slot = next(i for i in range(1, 4) if (uid, i) not in _TIMERS)
        msg = await update.message.reply_text(
            f"⏱ Timer #{slot}: <b>{_fmt(secs)}</b> remaining…", parse_mode="HTML"
        )
        _TIMERS[(uid, slot)] = asyncio.create_task(
            _run(
                uid,
                slot,
                secs,
                update.effective_chat.id,
                msg.message_id,
                update.get_bot(),
            )
        )


async def _run(uid, slot, total, chat_id, msg_id, bot):
    rem = total
    interval = 1 if total <= 60 else 30 if total <= 3600 else 60
    try:
        while rem > 0:
            tick = min(interval, rem)
            await asyncio.sleep(tick)
            rem -= tick
            if rem > 0:
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=f"⏱ Timer #{slot}: <b>{_fmt(rem)}</b> remaining…",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=f"⏰ <b>Timer #{slot} complete!</b>",
            parse_mode="HTML",
        )
    except asyncio.CancelledError:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=f"❌ Timer #{slot} cancelled.",
                parse_mode="HTML",
            )
        except Exception:
            pass
    finally:
        _TIMERS.pop((uid, slot), None)


def create():
    return TimerPlugin()
