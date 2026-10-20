"""Business-chat commands — owner & counterparty triggers that post their result
INTO the conversation AS the owner (via the business connection). The owner's
trigger message is deleted so the chat shows only the clean result.

  ping              anyone   → a short playful pong
  time · время · heure       → the current time, in the language that asked
  timer <duration>  anyone   → a live countdown (edits one message in place)
                    (also: таймер · minuteur; units in en/ru/fr)
  timer cancel      anyone   → stop the running countdown  (also: timer_cancel)
  remind <when> <what>  anyone → posted back into this chat when it is due
                    (also: remember · напомни · напомнить · rappelle(-moi); days,
                    dates and repeats; see :mod:`navig.telegram.biz_reminders`)
  flip · coin · монетка, dice · roll · кубик, choice · выбери · choisis, 8ball
  weather · погода · météo <city>, crypto, currency · convert, whois <domain>

``role …`` is owner-only and lives in :mod:`navig.telegram.autoreply`.

Permission per command is ``"all"`` (anyone in the chat) or ``"owner"``. Output
always goes into the conversation as the owner; the owner's trigger is removed.

A bare command word acts only when the REST of the message is a command
(`_BARE_OK`): "Time to go" or "Напомни 😁" is conversation, left untouched — not
answered, and for the owner not deleted. "/" or "!" in front always means "the bot".
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
                parse_mode: str = "HTML", reply_markup: dict | None = None) -> int | None:
    data: dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        data["reply_markup"] = reply_markup
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
            from navig.telegram.deletions import note_own_deletion  # noqa: PLC0415

            note_own_deletion(bcid, [message_id])  # our trigger — keep it out of the digest
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
    """Sum every ``<n> <unit>`` token → seconds. "1h 30m"→5400, "30 seconds"→30,
    "5 минут"→300, "2 heures"→7200 — the same units a reminder understands; this
    read English only, so "таймер 5 минут" answered with the usage line."""
    from navig.telegram.biz_reminders import duration_seconds  # noqa: PLC0415

    return duration_seconds(text or "")


def _fmt(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# ── command handlers ─────────────────────────────────────────────────────────


async def _cmd_ping(channel, chat_id, bcid, arg, **_):
    await _send(channel, chat_id, bcid, random.choice(_PONGS))


_RU_WORDS = {"время", "монетка", "монета", "кубик", "выбери", "погода", "таймер"}
_FR_WORDS = {"heure", "pièce", "piece", "dé", "choisis", "météo", "meteo", "minuteur"}
_WEEKDAYS = {
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    "ru": ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"),
    "fr": ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"),
}
_MONTHS = {
    "en": ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"),
    "ru": ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
           "сентября", "октября", "ноября", "декабря"),
    "fr": ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
           "septembre", "octobre", "novembre", "décembre"),
}


def _lang_of(word: str) -> str:
    """Reply in the language of the word that asked: "время" → Russian."""
    w = (word or "").lower()
    return "ru" if w in _RU_WORDS else "fr" if w in _FR_WORDS else "en"


async def _cmd_time(channel, chat_id, bcid, arg, *, word: str = "", **_):
    # It printed the server locale's English ("Wednesday 01 October") whatever
    # language the chat was in; "время" and "heure" now answer in theirs.
    now = datetime.now()
    lang = _lang_of(word)
    day = _WEEKDAYS[lang][now.weekday()]
    month = _MONTHS[lang][now.month - 1]
    await _send(channel, chat_id, bcid, f"🕐 {now:%H:%M} · {day} {now.day} {month}")


_OR_SPLIT = re.compile(r"\s+(?:or|ou|или|o|oder)\s+|\s*[,|/]\s*", re.IGNORECASE)


async def _cmd_choice(channel, chat_id, bcid, arg, **_):
    """`choice a or b` / `choice x, y, z` → pick one at random (ported from the lab)."""
    opts = [o.strip() for o in _OR_SPLIT.split(arg or "") if o.strip()]
    if len(opts) < 2:
        await _send(channel, chat_id, bcid, "🤔 Give me options: <code>choice pizza or sushi</code>")
        return
    await _send(channel, chat_id, bcid, "🎯 " + random.choice(opts))


_COIN = {"en": ("Heads", "Tails"), "ru": ("Орёл", "Решка"), "fr": ("Pile", "Face")}


async def _cmd_flip(channel, chat_id, bcid, arg, *, word: str = "", **_):
    await _send(channel, chat_id, bcid, "🪙 " + random.choice(_COIN[_lang_of(word)]))


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


async def _cmd_weather(channel, chat_id, bcid, arg, *, word: str = "", **_):
    from navig.telegram import biz_lookups

    await _send(channel, chat_id, bcid, await biz_lookups.weather(arg, _lang_of(word)))


async def _cmd_crypto(channel, chat_id, bcid, arg, **_):
    from navig.telegram import biz_lookups

    parts = (arg or "").split()
    sym = parts[0] if parts else "btc"
    vs = parts[1] if len(parts) > 1 else "usd"
    await _send(channel, chat_id, bcid, await biz_lookups.crypto(sym, vs))


async def _cmd_currency(channel, chat_id, bcid, arg, **_):
    from navig.telegram import biz_lookups

    await _send(channel, chat_id, bcid, await biz_lookups.currency(arg))


async def _cmd_smart_convert(channel, chat_id, bcid, arg, **_):
    """Amount-aware conversion across fiat + crypto (arg = "AMOUNT FROM TO"). Used by
    the natural-language crypto path, e.g. "0.5 btc to usd"."""
    from navig.telegram import biz_lookups

    parts = (arg or "").split()
    if len(parts) < 3:
        return
    try:
        amount = float(parts[0])
    except ValueError:
        return
    await _send(channel, chat_id, bcid, await biz_lookups.smart_convert(amount, parts[1], parts[2]))


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


# ── natural conversion phrasing ("10 eur to usd", "0.5 btc to usd") — no command ─
# Business chats never route text to the AI agent, so a bare conversion phrase has to
# be recognised here or it does nothing. Guarded hard: an amount, two KNOWN assets
# (fiat currency/symbol OR crypto symbol), and a conversion word/symbol between them —
# so ordinary chat ("see you in 10 min", "10 cats and dogs") is never hijacked. Any
# exotic fiat code is still reachable via the explicit `convert <from> <to>` command.

_CUR_SYMBOLS = {
    "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₽": "RUB",
    "₹": "INR", "₩": "KRW", "₺": "TRY", "₴": "UAH", "₪": "ILS", "฿": "THB",
}

_KNOWN_CURRENCIES = frozenset({
    "AED", "ARS", "AUD", "BGN", "BRL", "CAD", "CHF", "CLP", "CNY", "COP", "CZK",
    "DKK", "EGP", "EUR", "GBP", "HKD", "HUF", "IDR", "ILS", "INR", "ISK", "JPY",
    "KRW", "MXN", "MYR", "NOK", "NZD", "PHP", "PLN", "RON", "RUB", "SAR", "SEK",
    "SGD", "THB", "TRY", "TWD", "UAH", "USD", "VND", "ZAR",
})

# 3–5 letters covers fiat (exactly 3) AND crypto symbols (btc…shib, matic). The
# both-sides-must-be-known check below rejects any 4–5 letter word that isn't an asset.
_CUR_TOKEN = r"[$€£¥₽₹₩₺₴₪฿]|[A-Za-z]{3,5}"
_NL_CONVERT_RE = re.compile(
    rf"^\s*(?P<fsym>[$€£¥₽₹₩₺₴₪฿])?\s*(?P<amt>\d+(?:[.,]\d+)?)\s*(?P<from>{_CUR_TOKEN})?\s*"
    rf"(?:to|in|into|as|=|->|→|>)\s*(?P<to>{_CUR_TOKEN})\s*[?!.]*$",
    re.IGNORECASE,
)


def _nl_convert(text: str) -> tuple[str, str] | None:
    """Detect a bare "N XXX to YYY" phrase → ``(kind, "AMOUNT FROM TO")`` where *kind*
    is ``"fiat"`` (both sides fiat) or ``"crypto"`` (a coin on either side), else None.
    Both sides must be a KNOWN fiat currency/symbol or a known crypto symbol, so
    ordinary chat is never hijacked."""
    m = _NL_CONVERT_RE.match(text or "")
    if not m:
        return None
    from navig.telegram import biz_lookups  # lazy: cheap module, no aiohttp at import

    cryptos = biz_lookups.CRYPTO_SYMBOLS

    def _norm(tok: str | None) -> tuple[str, str] | None:
        if not tok:
            return None
        if tok in _CUR_SYMBOLS:
            return ("fiat", _CUR_SYMBOLS[tok])
        up, low = tok.upper(), tok.lower()
        if up in _KNOWN_CURRENCIES:
            return ("fiat", up)
        if low in cryptos:
            return ("crypto", up)
        return None

    a = _norm(m.group("from") or m.group("fsym"))
    b = _norm(m.group("to"))
    if not a or not b or a[1] == b[1]:
        return None
    kind = "crypto" if "crypto" in (a[0], b[0]) else "fiat"
    return kind, f"{m.group('amt').replace(',', '.')} {a[1]} {b[1]}"


async def _cmd_remind(channel, chat_id, bcid, arg, **kw):
    from navig.telegram import biz_reminders  # noqa: PLC0415

    # `arg` arrives lowercased by dispatch; the note keeps the case it was typed in.
    raw = ((kw.get("trigger") or {}).get("text") or "").strip().lstrip("/!").strip()
    parts = raw.split(None, 1)
    arg = parts[1] if len(parts) > 1 else ""
    text, markup = await biz_reminders.command(channel, chat_id, bcid, arg, **kw)
    await _send(channel, chat_id, bcid, text, reply_markup=markup)


async def _cmd_todo(channel, chat_id, bcid, arg, **kw):
    """The chat's shared to-do list — see `biz_todo`."""
    from navig.telegram import biz_todo  # noqa: PLC0415

    await biz_todo.command(channel, chat_id, bcid, arg, **kw)


def _todo_words() -> tuple[str, ...]:
    from navig.telegram import biz_todo  # noqa: PLC0415

    return (
        *biz_todo.ADD_WORDS, *biz_todo.LIST_WORDS, *biz_todo.DONE_WORDS,
        *biz_todo.UNDO_WORDS, *biz_todo.DEL_WORDS, *biz_todo.CLEAR_WORDS,
    )


def _bare_todo(arg: str, text: str) -> bool:
    from navig.telegram import biz_todo  # noqa: PLC0415

    head = text.lower().lstrip("/!").strip().split(None, 1)
    return bool(head) and biz_todo.bare_ok(head[0], arg, text)


# ── registry + dispatch ──────────────────────────────────────────────────────

_COMMANDS: dict[str, dict[str, Any]] = {
    "ping": {"perm": "all", "fn": _cmd_ping},
    "time": {"perm": "all", "fn": _cmd_time},
    "время": {"perm": "all", "fn": _cmd_time},
    "heure": {"perm": "all", "fn": _cmd_time},
    "timer": {"perm": "all", "fn": _cmd_timer},
    "таймер": {"perm": "all", "fn": _cmd_timer},
    "minuteur": {"perm": "all", "fn": _cmd_timer},
    "timer_cancel": {"perm": "all", "fn": _cmd_timer_cancel},
    # fun / utility (ported from the lab's SecondBot command handlers)
    "choice": {"perm": "all", "fn": _cmd_choice},
    "выбери": {"perm": "all", "fn": _cmd_choice},
    "choisis": {"perm": "all", "fn": _cmd_choice},
    "flip": {"perm": "all", "fn": _cmd_flip},
    "coin": {"perm": "all", "fn": _cmd_flip},
    "монетка": {"perm": "all", "fn": _cmd_flip},
    "dice": {"perm": "all", "fn": _cmd_dice},
    "roll": {"perm": "all", "fn": _cmd_dice},
    "кубик": {"perm": "all", "fn": _cmd_dice},
    "8ball": {"perm": "all", "fn": _cmd_8ball},
    # external-data lookups — slow (network), so show "typing…" while they fetch
    "weather": {"perm": "all", "fn": _cmd_weather, "typing": True},
    "погода": {"perm": "all", "fn": _cmd_weather, "typing": True},
    "météo": {"perm": "all", "fn": _cmd_weather, "typing": True},
    "meteo": {"perm": "all", "fn": _cmd_weather, "typing": True},
    "crypto": {"perm": "all", "fn": _cmd_crypto, "typing": True},
    "currency": {"perm": "all", "fn": _cmd_currency, "typing": True},
    "convert": {"perm": "all", "fn": _cmd_currency, "typing": True},
    "whois": {"perm": "all", "fn": _cmd_whois, "typing": True},
    # A reminder set in the chat comes back INTO the chat (asked 2026-09-30, after
    # "Напомнить через 16 часов про схему" and "remember in 15 hours schema" did
    # nothing). Every spelling the two people in that chat reached for.
    **{
        w: {"perm": "all", "fn": _cmd_remind}
        for w in (
            "remind",
            "reminder",
            "remember",
            "remindme",
            "напомни",
            "напомните",
            "напомнить",
            "напоминание",
            "rappelle",
            "rappelle-moi",
            "rappelle-nous",
            "rappelez",
            "rappelez-moi",
            "rappelez-nous",
            "rappel",
            "rappeler",
        )
    },
    # The chat's shared to-do list: both people add and tick, the owner deletes.
    **{w: {"perm": "all", "fn": _cmd_todo} for w in _todo_words()},
}


# ── when a bare word is a command ────────────────────────────────────────────

_ALONE = re.compile(r"^[\s?!.…]*$")
_NOT_A_PLACE = frozenset(
    "is was will be today tomorrow now here there so too "
    "сегодня завтра сейчас тут там хорошая плохая отличная такая "
    "aujourd'hui demain est sera fait ici trop".split()
)


def _bare_weather(arg: str, _text: str) -> bool:
    """A city, not a sentence: "weather Paris" yes, "weather is nice today" no —
    the weather service resolves almost any words to SOME place."""
    words = arg.split()
    return (
        len(words) <= 2
        and not any(w.lower() in _NOT_A_PLACE for w in words)
        and not any(ch.isdigit() for ch in arg)
    )


def _timer_args(arg: str) -> bool:
    from navig.telegram.biz_reminders import only_durations  # noqa: PLC0415

    return only_durations(arg) or arg.strip().lower() in ("cancel", "stop", "off")


def _bare_remind(arg: str, _text: str) -> bool:
    from navig.telegram.biz_reminders import is_request  # noqa: PLC0415

    return is_request(arg)


_BARE_OK: dict[Any, Any] = {
    _cmd_ping: lambda a, _t: bool(_ALONE.match(a)),
    _cmd_time: lambda a, _t: bool(_ALONE.match(a)),
    _cmd_flip: lambda a, _t: bool(_ALONE.match(a)),
    _cmd_dice: lambda a, _t: bool(re.fullmatch(r"\s*(?:d?\d{1,4})?\s*", a, re.IGNORECASE)),
    _cmd_8ball: lambda a, _t: a.strip().endswith("?") and len(a.split()) >= 2,
    _cmd_choice: lambda a, _t: len([o for o in _OR_SPLIT.split(a) if o.strip()]) >= 2,
    _cmd_weather: _bare_weather,
    _cmd_crypto: lambda a, _t: bool(
        re.fullmatch(r"\s*(?:[a-z]{2,6}(?:\s+[a-z]{2,6})?)?\s*", a, re.IGNORECASE)
    ),
    _cmd_currency: lambda a, _t: bool(
        re.fullmatch(r"\s*[\d.,]*\s*[a-z]{3}\s+(?:to\s+|in\s+)?[a-z]{3}\s*", a, re.IGNORECASE)
    ),
    _cmd_whois: lambda a, _t: bool(re.fullmatch(r"\s*\S+\.\S+\s*", a)),
    _cmd_timer: lambda a, _t: _timer_args(a),
    _cmd_timer_cancel: lambda a, _t: bool(_ALONE.match(a)),
    _cmd_remind: _bare_remind,
    _cmd_todo: _bare_todo,
}


async def dispatch(channel: Any, msg: dict, *, is_owner: bool, owner_id: int | None) -> bool:
    """Match + run a business-chat command. Returns True if one handled the message.

    Owner-issued commands have their trigger message deleted first so the chat
    shows only the result (posted as the owner). Counterparty commands just reply.
    """
    text = (msg.get("text") or "").strip()
    if not text:
        return False
    # "/" and "!" both mark a command — "!напомнить" was typed in a real chat.
    head = text.lower().lstrip("/!").strip().split(None, 1)
    if not head:
        return False
    name = head[0]
    cmd = _COMMANDS.get(name)
    arg = head[1] if len(head) > 1 else ""
    if cmd is None:
        # No command word — recognise a natural conversion phrase ("10 eur to usd",
        # "0.5 btc to usd"). None → genuinely not a command.
        conv = _nl_convert(text)
        if conv is None:
            return False
        kind, conv_arg = conv
        if kind == "crypto":
            cmd = {"perm": "all", "fn": _cmd_smart_convert, "typing": True}
            name, arg = "convert", conv_arg
        else:
            cmd = _COMMANDS["currency"]
            name, arg = "currency", conv_arg
    if cmd["perm"] == "owner" and not is_owner:
        return False
    # A bare word ("Time to go", "Напомни 😁", "Roll with it") is a command only when
    # the rest of the message is one. It used to fire on the first word alone — and
    # for the OWNER that deletes their message first: "Напомни пожалуйста во сколько
    # у тебя дислокация" (a real message) would have been replaced by the usage line.
    # "/" or "!" still means "I am talking to the bot", usage line and all.
    explicit = text.startswith(("/", "!"))
    check = _BARE_OK.get(cmd["fn"])
    if not explicit and check is not None and not check(arg, text):
        return False

    chat_id = (msg.get("chat") or {}).get("id")
    bcid = msg.get("business_connection_id")

    if is_owner:
        await _delete(channel, chat_id, bcid, msg.get("message_id"))
    if cmd.get("typing"):
        await _chat_action(channel, chat_id, bcid)  # 'typing…' while the lookup fetches
    try:
        await cmd["fn"](
            channel,
            chat_id,
            bcid,
            arg,
            is_owner=is_owner,
            owner_id=owner_id,
            trigger=msg,
            word=name,
        )
    except Exception:  # noqa: BLE001
        logger.warning("business command %r failed", name, exc_info=True)
    return True
