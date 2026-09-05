"""Telegram body check-in — the weigh-in prompt and the weekly card.

The sibling of :mod:`navig.telegram.habit_actions`, and shaped like it, because
the same thing is true of both: a check-in only survives if it costs seconds.

**Where it differs, and why.** A habit is a tap — done or not done. A weight is a
number you read off a scale, and no arrangement of buttons can express 87.4. So
the weigh-in borrows the *journal* pattern instead: ``force_reply`` plus a
disk-backed record of which question is outstanding, so a reply can be
identified as an answer rather than guessed at. Everything in the weekly card
that CAN be a tap (mood, sleep, "all fine") is one.

Cards are SENT by ``navig body checkin --send`` (the CLI, which knows the space)
and answered here, inside the gateway. See ``body_metrics.remember_target`` for
how the two processes agree on which metrics.csv an answer belongs to.

All strings resolve through the operator's ONE global language preference
(``user.language``, changed from the chat with ``/lang``) — this module has no
language switch of its own.

Callback data (Telegram caps it at 64 bytes):
    bm:m:<1-10>:<yyyymmdd>    mood for the week
    bm:sl:<hours>:<yyyymmdd>  typical night's sleep
    bm:tx:ok:<yyyymmdd>       treatment: nothing to report
    bm:tx:no:<yyyymmdd>       treatment: ask for a note

Never diagnoses, never prescribes, never suggests a dose. What the operator
writes about the treatment is recorded verbatim and not interpreted.
"""

from __future__ import annotations

import html
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from navig.spaces import body_metrics as bm

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "bm:"

#: Extension id gating every surface in this module.
EXTENSION_ID = "health"

_LOCALES_ROOT = Path(__file__).parent / "body_locales"

#: Mood faces, reused from the habit card so one scale looks like one scale.
_MOOD_FACES = [(2, "😞"), (4, "🙁"), (6, "😐"), (8, "🙂"), (10, "😄")]

#: Sleep taps. Round numbers on purpose — the point is a usable estimate in one
#: tap, not a precision nobody has at 19:00 on a Sunday.
_SLEEP_HOURS = [5, 6, 7, 8, 9]

_MONTHS_KEY = "%d.%m"


# ── language ──────────────────────────────────────────────────────────────────

_store: Any = None


def _lang() -> str:
    """The ISO code for the operator's global output language, or "en".

    ``user.language`` holds a NAME ("Russian") because a human wrote it;
    ``language_code`` is the one bridge to the code a lookup needs. Read through
    ``ConfigManager`` — ``_load_global_config()`` returns a validated view that
    DROPS the whole ``user`` subtree, so this would silently always be English.
    """
    try:
        from navig.core.language import language_code, resolve_language  # noqa: PLC0415

        return language_code(resolve_language()) or "en"
    except Exception:  # noqa: BLE001 — a language we cannot resolve is English
        return "en"


def t(key: str, **fields: object) -> str:
    """A localized string, falling back to English and then to the key itself."""
    global _store
    if _store is None:
        from navig.agent.conv.localization import LocalizationStore  # noqa: PLC0415

        _store = LocalizationStore(locales_root=_LOCALES_ROOT)
    text = _store.get(key, _lang())
    if not fields:
        return text
    try:
        return text.format(**fields)
    except (KeyError, IndexError, ValueError):
        # A locale file with a broken placeholder must not take the check-in down.
        logger.debug("locale placeholder mismatch for %r", key)
        return text


# ── extension gate ────────────────────────────────────────────────────────────


def extension_is_off() -> bool:
    """True when the Health extension is switched off. Never raises."""
    try:
        from navig.gateway.channels.telegram_extensions import is_enabled  # noqa: PLC0415

        return not is_enabled(EXTENSION_ID)
    except Exception:  # noqa: BLE001
        return False


def extension_banner() -> str:
    """The warning shown above any surface reporting check-in state.

    Suppressing delivery leaves the schedule intact, so the cron job still reads
    ``enabled: true`` while nothing arrives. Returns "" when the extension is on,
    so a caller can prepend it unconditionally.
    """
    return t("ext.off") + "\n" if extension_is_off() else ""


# ── callback data packing ─────────────────────────────────────────────────────


def _compact(day: str) -> str:
    """2026-09-05 -> 20260905 (callback data is byte-budgeted)."""
    return day.replace("-", "")


def _expand(compact: str) -> str:
    return f"{compact[0:4]}-{compact[4:6]}-{compact[6:8]}"


def _short_date(day: str) -> str:
    try:
        return date.fromisoformat(day).strftime(_MONTHS_KEY)
    except ValueError:
        return day


# ── the daily weigh-in ────────────────────────────────────────────────────────


def build_weigh_prompt(path: Path, day: str) -> tuple[str, dict[str, Any]]:
    """``(text, reply_markup)`` for the morning weigh-in.

    ``force_reply``, and deliberately NO inline keyboard: Telegram will not carry
    both on one message, and the overwhelmingly common action here is typing a
    number. force_reply opens the reply box pre-addressed to this message, which
    makes typing 87.4 a single interaction — and makes the answer *identifiable*,
    so a bare number sent at any other moment is never swallowed into the record.

    Skip and same-as-last therefore become typed words rather than buttons. That
    is the right trade: they are the rare cases, and a button that cost the
    common case its one-tap path would be a bad bargain.
    """
    last = bm.latest(path)
    lines = [extension_banner() + f"<b>{html.escape(t('weigh.title'))}</b>", ""]
    if last:
        lines.append(
            f"<i>{html.escape(t('weigh.last', value=f'{last[1]:g}', date=_short_date(last[0].isoformat())))}</i>"
        )
    else:
        lines.append(f"<i>{html.escape(t('weigh.first'))}</i>")
    lines += [
        "",
        f"<i>{html.escape(t('weigh.hint'))}</i>",
        f"<i>{html.escape(t('weigh.options'))}</i>",
    ]
    return "\n".join(lines), {"force_reply": True, "selective": True}


# ── the weekly card ───────────────────────────────────────────────────────────


def _trend_line(summary: dict) -> str:
    avg, trend = summary.get("average"), summary.get("trend")
    if avg is None:
        return f"<i>{html.escape(t('week.nodata'))}</i>"
    kg = t("unit.kg")
    if trend is None:
        change = t("week.no_prior")
    else:
        change = f"{trend:+.1f} {kg}"
    spark = summary.get("sparkline") or ""
    logged = t("week.logged", n=summary.get("recorded"), total=summary.get("days"))
    return (
        f"{html.escape(t('week.average'))}: <b>{avg:.1f} {html.escape(kg)}</b>  "
        f"({html.escape(change)})\n"
        f"<code>{html.escape(spark)}</code>  <i>{html.escape(logged)}</i>"
    )


def build_weekly_card(path: Path, day: str) -> tuple[str, dict[str, Any]]:
    """``(text, reply_markup)`` for the Sunday check-in.

    Leads with the 7-day average rather than the latest reading: day-to-day
    weight is mostly water, and a single pair of readings a week apart cannot
    tell a real change from noise.
    """
    summary = bm.summarize(path, ending=date.fromisoformat(day))
    compact = _compact(day)

    lines = [
        extension_banner() + f"<b>{html.escape(t('week.title'))}</b>",
        "",
        _trend_line(summary),
    ]

    clinician = _clinician_line(summary.get("trend"))
    if clinician:
        lines.append(f"\n⚠️ <i>{html.escape(clinician)}</i>")

    # The three questions, in the same order as the three button rows below —
    # Telegram renders the keyboard under the whole message, so the only thing
    # tying a question to its row is that order.
    lines += ["", f"1. {html.escape(t('week.mood_q'))}"]

    keyboard = [
        [
            {"text": face, "callback_data": f"bm:m:{value}:{compact}"}
            for value, face in _MOOD_FACES
        ],
        [
            {"text": f"{h}{t('unit.h')}", "callback_data": f"bm:sl:{h}:{compact}"}
            for h in _SLEEP_HOURS
        ],
        [
            {"text": t("btn.fine"), "callback_data": f"bm:tx:ok:{compact}"},
            {"text": t("btn.note"), "callback_data": f"bm:tx:no:{compact}"},
        ],
    ]

    lines += [
        f"2. {html.escape(t('week.sleep_q'))}",
        f"3. {html.escape(t('week.treatment_q'))}",
        "",
        f"<i>{html.escape(t('week.safety'))}</i>",
    ]

    return "\n".join(lines), {"inline_keyboard": keyboard}


def _clinician_line(trend: float | None) -> str | None:
    """One neutral sentence when the trend is fast. Never an interpretation.

    The threshold comes from body_metrics, not from the CLI module: this runs
    inside the gateway, which must not import a command module — and the two
    surfaces must not be able to disagree about when the line appears.
    """
    if trend is None or trend > -bm.FAST_LOSS_KG_PER_WEEK:
        return None
    return t("clinician", delta=f"{abs(trend):.1f}")


# ── callbacks ─────────────────────────────────────────────────────────────────


async def handle_callback(
    channel: Any,
    cb_data: str,
    chat_id: int,
    message_id: int,
    user_id: int,
) -> str:
    """Apply one tap and return the toast text.

    The write happens before the toast is answered, so what the toast claims is
    what actually landed on disk.
    """
    parts = cb_data.split(":")
    if len(parts) < 3:
        return "?"
    action = parts[1]
    path = bm.resolve_target(chat_id)

    try:
        if action == "m" and len(parts) >= 4:
            day, value = _expand(parts[3]), parts[2]
            bm.upsert(path, day, {"mood_1_10": value})
            return t("week.saved")

        if action == "sl" and len(parts) >= 4:
            day, value = _expand(parts[3]), parts[2]
            bm.upsert(path, day, {"sleep_hours": value})
            return t("week.saved")

        if action == "tx" and len(parts) >= 4:
            day, choice = _expand(parts[3]), parts[2]
            if choice == "ok":
                _append_note(path, day, "treatment: ok")
                return t("week.saved")
            await ask_for_note(channel, chat_id, day)
            return t("week.noted")
    except bm.MetricsReadError as exc:
        logger.warning("body check-in callback could not write: %s", exc)
        return "⚠️"

    return "?"


def _append_note(path: Path, day: str, text: str) -> None:
    """Add to the day's notes without discarding what is already there."""
    _, rows = bm.read_metrics(path)
    existing = next(
        ((r.get("notes") or "") for r in rows if r.get("date") == day), ""
    )
    merged = f"{existing}; {text}" if existing.strip() else text
    bm.upsert(path, day, {"notes": merged})


# ── the two force_reply prompts ───────────────────────────────────────────────


async def ask_for_weight(channel: Any, chat_id: int, day: str, path: Path) -> None:
    """Send the weigh-in prompt and remember that an answer is owed."""
    text, keyboard = build_weigh_prompt(path, day)
    await _send_prompt(channel, chat_id, day, text, "weigh", keyboard)


async def ask_for_note(channel: Any, chat_id: int, day: str) -> None:
    """Ask for the treatment note, recorded verbatim and never interpreted."""
    await _send_prompt(channel, chat_id, day, t("week.note_prompt"), "note", None)


async def _send_prompt(
    channel: Any,
    chat_id: int,
    day: str,
    text: str,
    kind: str,
    keyboard: dict[str, Any] | None,
) -> None:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": keyboard or {"force_reply": True, "selective": True},
    }
    try:
        result = await channel._api_call("sendMessage", payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("body %s prompt failed (chat=%s): %s", kind, chat_id, exc)
        return
    prompt_id = ((result or {}).get("result") or {}).get("message_id")
    bm.set_prompt(chat_id, kind, day, prompt_id)


# ── consuming a typed answer ──────────────────────────────────────────────────


def consume_reply(chat_id: int, text: str) -> str | None:
    """Record a typed answer to the outstanding prompt; return the confirmation.

    ``None`` means "this was not an answer" — the caller must then let the
    message continue down the normal handler chain rather than swallowing it.
    The caller is responsible for having already checked that the message is a
    reply to the prompt's message id.
    """
    pending = bm.pending_prompt(chat_id)
    if pending is None:
        return None
    kind, day, _mid = pending
    path = bm.resolve_target(chat_id)
    body = (text or "").strip()

    if kind == "note":
        if body.lower() in {"skip", "cancel", "stop", "no", "-", "пропустить", "нет"}:
            bm.clear_prompt(chat_id)
            return t("weigh.skipped")
        _append_note(path, day, f"treatment: {body}")
        bm.clear_prompt(chat_id)
        return t("week.saved")

    if kind == "weigh":
        if body.lower() in {"skip", "cancel", "stop", "no", "-", "пропустить", "нет"}:
            bm.clear_prompt(chat_id)
            return t("weigh.skipped")
        if body.lower() in {"same", "как вчера", "то же", "idem", "pareil"}:
            last = bm.latest(path)
            if last is None:
                return t("weigh.no_previous")
            bm.upsert(path, day, {"weight_kg": f"{last[1]:g}"})
            bm.clear_prompt(chat_id)
            return t("weigh.recorded", value=f"{last[1]:g}")
        try:
            value = bm.parse_weight(body)
        except bm.WeightParseError:
            # Deliberately does NOT clear the prompt: the question is still open,
            # so the next attempt is still recognised as an answer to it.
            return t("weigh.unreadable")
        last = bm.latest(path)
        if bm.is_implausible_jump(value, last[1] if last else None):
            if not bm.confirm_value(chat_id, value):
                delta = abs(value - last[1]) if last else 0.0
                return t("weigh.implausible", value=f"{value:g}", delta=f"{delta:.1f}")
        bm.upsert(path, day, {"weight_kg": f"{value:g}"})
        bm.clear_prompt(chat_id)
        return t("weigh.recorded", value=f"{value:g}")

    return None


def next_weekly_day(today: date | None = None) -> date:
    """The next Sunday on or after *today* — the weekly card's natural anchor."""
    day = today or date.today()
    return day + timedelta(days=(6 - day.weekday()) % 7)
