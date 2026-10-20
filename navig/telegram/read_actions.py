"""``/read`` — be read to, like a book.

You are in bed. Send NAVIG a text and it reads the whole thing to you, in numbered parts of
about ten minutes that arrive as Telegram's own audio player — the first within about a
minute, the rest as they are made, so you can fall asleep to part 1 while part 4 is still
being generated.

Three ways in, because Telegram caps a message at 4096 characters and a chapter is longer:

* ``/read <text>``                     — a short text, typed or pasted;
* a ``.txt`` / ``.md`` file with the caption ``/read``  — the way to send a long one;
* the agent ("read this to me") — see ``navig.agent.tools.read_aloud_tool``.

And ``/read stop`` (cancel), ``/read go`` (say yes to a quoted big reading), ``/read status``.

**No surprise bills.** Reading costs one ElevenLabs credit per character, so a chapter can
be most of a month. The quote is shown before anything is spent when it is large, a reading
the account cannot afford is refused, and "go" is required above the threshold — see
``navig_audio.reading_job``. The engine is the ``navig-audio`` plugin; this module is only
the Telegram door (and degrades to a one-line explanation when the plugin is absent).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("navig.telegram.read")

READ_COMMANDS = ("/read", "/book")
TEXT_SUFFIXES = (".txt", ".md", ".markdown", ".text")
#: A text file bigger than this is not a bedtime story, and decoding it is pointless:
#: at one credit per character it would also not be affordable.
FILE_LIMIT = 1024 * 1024

# Words people type as the whole argument. Input vocabulary (like skill trigger keywords),
# not user-facing copy — the owner writes Russian and French.
_STOP_WORDS = frozenset({"stop", "cancel", "стоп", "отмена", "отмени", "arrête", "arrete", "annuler"})
_GO_WORDS = frozenset({"go", "yes", "ok", "start", "да", "давай", "поехали", "oui", "vas-y"})


def _t(key: str, default: str, **fields: Any) -> str:
    """i18n lookup with an English default — a missing locale never blocks a reply."""
    try:
        from navig.core import i18n

        text = i18n.t(key, **fields)
        if text and text != key:
            return text
    except Exception:  # noqa: BLE001
        logger.debug("i18n lookup failed for %s", key, exc_info=True)
    try:
        return default.format(**fields)
    except (KeyError, IndexError, ValueError):
        return default


def job_key(chat_id: int | str) -> str:
    """One reading per chat. The agent tool derives the SAME key from its session, so
    ``/read stop`` stops a reading the agent started, and the reverse."""
    return f"telegram:{chat_id}"


# ── intake ────────────────────────────────────────────────────────────────────


def is_read_caption(text: str) -> bool:
    head = (text or "").strip().lower().split(None, 1)[0].split("@", 1)[0] if (text or "").strip() else ""
    return head in READ_COMMANDS


def wants_read(message: dict[str, Any]) -> bool:
    """Is this a document sent with the caption ``/read``?"""
    return bool(message.get("document")) and is_read_caption(message.get("caption") or "")


async def _download_bytes(channel: Any, file_id: str, limit: int) -> bytes:
    import aiohttp

    remote = await channel._get_file_path(file_id)
    url = channel._build_file_url(remote)
    async with aiohttp.ClientSession() as session, session.get(url) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Telegram file download HTTP {resp.status}")
        data = await resp.read()
    if len(data) > limit:
        raise RuntimeError("file too large")
    return data


# ── what to say ───────────────────────────────────────────────────────────────


def _money(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def describe_event(event: Any) -> str | None:
    """The message for a job event, or ``None`` when the audio itself is the message."""
    kind = event.kind
    summary = event.summary
    done = len(summary.parts) if summary is not None else 0
    total = summary.parts[-1].total if summary is not None and summary.parts else done
    if kind == "done":
        return _t("reader.tg.done", "✅ That's all — {parts} part(s).", parts=done)
    if kind == "cancelled":
        return _t("reader.tg.cancelled", "⏹ Stopped after {done} of {total} part(s).", done=done, total=total)
    if kind == "partial":
        return _t(
            "reader.tg.partial",
            "⚠️ Stopped early after {done} of {total} part(s): {error}\n"
            "Send /read again — what was already made is cached and free.",
            done=done, total=total, error=event.detail,
        )
    if kind == "failed":
        return _t("reader.tg.failed", "⚠️ Could not read that: {error}", error=event.detail)
    return None


def describe_result(result: Any) -> str:
    """The reply to a ``begin``/``confirm`` outcome."""
    from navig_audio.reading_job import Outcome

    plan, o = result.plan, result.outcome
    if o is Outcome.STARTED:
        return _t(
            "reader.tg.started",
            "📖 Reading to you: {parts} part(s), about {minutes} min, {chars} characters. "
            "The first part is on its way. /read stop to cancel.",
            parts=plan.part_count, minutes=round(plan.minutes), chars=_money(plan.chars),
        )
    if o is Outcome.NEEDS_CONFIRM:
        return _t(
            "reader.tg.needs_confirm",
            "📖 That is long: {chars} characters, about {minutes} min, about {credits} credits.\n"
            "Send /read go to start, or /read stop to drop it.",
            chars=_money(plan.chars), minutes=round(plan.minutes), credits=_money(result.spend),
        )
    if o is Outcome.INSUFFICIENT:
        return _t(
            "reader.tg.insufficient",
            "That needs about {credits} credits and the account has {remaining} left. "
            "Send a shorter text — what was already read is cached and free.",
            credits=_money(result.spend), remaining=_money(result.remaining or 0),
        )
    if o is Outcome.BUSY:
        return _t("reader.tg.busy", "I am already reading to you. /read stop to cancel it first.")
    if o is Outcome.EMPTY:
        return _t(
            "reader.tg.empty",
            "Nothing to read. Send /read followed by the text, or a .txt / .md file with the caption /read.",
        )
    if o is Outcome.NOTHING_PENDING:
        return _t("reader.tg.nothing_pending", "Nothing is waiting for a yes.")
    return _t("reader.tg.failed", "⚠️ Could not read that: {error}", error=result.detail or "unavailable")


# ── actions ───────────────────────────────────────────────────────────────────


async def _say(channel: Any, chat_id: int, text: str) -> None:
    await channel.send_message(chat_id, text, parse_mode=None)


def _deliver_to(channel: Any, chat_id: int):
    async def deliver(part: Any) -> bool:
        data = await asyncio.to_thread(part.path.read_bytes)
        sent = await channel.send_audio(
            chat_id,
            data,
            caption=f"📖 {part.caption}",
            filename=part.path.name,
            title=f"{part.index}/{part.total}",
            performer="NAVIG",
        )
        return sent is not None  # a rejected send returns None WITHOUT raising

    return deliver


def _notify_to(channel: Any, chat_id: int):
    async def notify(event: Any) -> None:
        text = describe_event(event)
        if text:
            await _say(channel, chat_id, text)

    return notify


def _engine():
    """The navig-audio engine, or ``None`` when the plugin is not installed."""
    try:
        from navig_audio import reader, reading_job
    except ImportError:
        return None
    return reader, reading_job


async def _unavailable(channel: Any, chat_id: int) -> None:
    await _say(
        channel,
        chat_id,
        _t("reader.tg.unavailable", "Reading aloud needs the navig-audio plugin (pip install navig-audio)."),
    )


async def start_reading(channel: Any, chat_id: int, text: str, *, confirmed: bool = False) -> None:
    """Plan a reading of *text* and, if it is safe to, start it in the background."""
    engine = _engine()
    if engine is None:
        await _unavailable(channel, chat_id)
        return
    reader, reading_job = engine
    try:
        options = reader.options_from_config()
        result = await reading_job.begin(
            job_key(chat_id),
            text,
            options,
            deliver=_deliver_to(channel, chat_id),
            notify=_notify_to(channel, chat_id),
            confirmed=confirmed,
        )
    except reader.ReadError as exc:
        await _say(channel, chat_id, _t("reader.tg.failed", "⚠️ Could not read that: {error}", error=str(exc)))
        return
    await _say(channel, chat_id, describe_result(result))


async def handle_command(channel: Any, chat_id: int, argument: str) -> None:
    """``/read [stop | go | status | <text>]``."""
    engine = _engine()
    if engine is None:
        await _unavailable(channel, chat_id)
        return
    _, reading_job = engine
    key = job_key(chat_id)
    arg = (argument or "").strip()
    first = arg.split(None, 1)[0].lower() if arg else ""
    single = len(arg.split()) == 1

    if not arg:
        await _say(
            channel,
            chat_id,
            _t(
                "reader.tg.help",
                "📖 I read texts aloud, like a book.\n\n"
                "• /read <text> — a short text\n"
                "• send a .txt or .md file with the caption /read — for a long one\n"
                "• /read stop — cancel · /read go — confirm a long one · /read status\n\n"
                "Parts of about ten minutes arrive as audio, quiet and unhurried.",
            ),
        )
    elif single and first in _STOP_WORDS:
        if reading_job.stop(key):
            await _say(channel, chat_id, _t("reader.tg.stopping", "⏹ Stopping after the part in progress."))
        elif reading_job.discard_pending(key):
            await _say(channel, chat_id, _t("reader.tg.dropped", "Dropped. Nothing was spent."))
        else:
            await _say(channel, chat_id, _t("reader.tg.not_running", "Nothing is being read right now."))
    elif single and first in _GO_WORDS:
        result = await reading_job.confirm(
            key, deliver=_deliver_to(channel, chat_id), notify=_notify_to(channel, chat_id)
        )
        await _say(channel, chat_id, describe_result(result))
    elif single and first == "status":
        job = reading_job.current(key)
        if job is None:
            pending = reading_job.pending_plan(key)
            text = (
                _t("reader.tg.status_pending", "Waiting for your yes: /read go to start, /read stop to drop.")
                if pending
                else _t("reader.tg.not_running", "Nothing is being read right now.")
            )
        else:
            text = _t(
                "reader.tg.status_running",
                "📖 Reading: {done} of {total} part(s) delivered.",
                done=job.delivered, total=job.plan.part_count,
            )
        await _say(channel, chat_id, text)
    else:
        await start_reading(channel, chat_id, arg)


async def handle_document(channel: Any, chat_id: int, user_id: int, message: dict[str, Any]) -> None:
    """A ``.txt`` / ``.md`` sent with the caption ``/read`` (``/read go`` confirms a big one)."""
    engine = _engine()
    if engine is None:
        await _unavailable(channel, chat_id)
        return
    reader, _ = engine
    doc = message.get("document") or {}
    name = str(doc.get("file_name") or "").lower()
    if not name.endswith(TEXT_SUFFIXES):
        await _say(channel, chat_id, _t("reader.tg.file_type", "Send a .txt or .md file — other formats are not supported yet."))
        return
    size = int(doc.get("file_size") or 0)
    if size > FILE_LIMIT:
        await _say(
            channel,
            chat_id,
            _t("reader.tg.file_too_big", "That file is {size} MB; the limit is 1 MB of text.", size=round(size / (1024 * 1024), 1)),
        )
        return
    try:
        data = await _download_bytes(channel, str(doc.get("file_id") or ""), FILE_LIMIT)
    except Exception as exc:  # noqa: BLE001 — say so; never go silent on a sent file
        logger.warning("read: download failed: %s", exc)
        await _say(channel, chat_id, _t("reader.tg.file_unreadable", "I could not download that file."))
        return
    words = (message.get("caption") or "").split()[1:]
    confirmed = bool(words) and words[0].lower() in _GO_WORDS
    await start_reading(channel, chat_id, reader.decode_text_file(data), confirmed=confirmed)
