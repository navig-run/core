"""Reminders set from inside a business chat, delivered back INTO that chat.

Asked 2026-09-30, from a real chat: the other person typed "Напомнить через 16 часов
про схему" and "!напомнить", the owner typed "remember in 15 hours schema" — and
nothing happened, because no business-chat command understood a reminder. Every
existing reminder lands in the owner's DM with the bot, which is the wrong place
for one two people agreed on in their own conversation.

    remind in 15 hours schema          → "⏰ I'll remind you in 15 h — schema (07:26)"
    напомни через 16 часов про схему   → at the time, "⏰ Напоминание: про схему"
    rappelle dans 2 heures le devis    → in the chat, as the owner
    remind list · remind cancel

Durable: a reminder is written to ``<data_dir>/business_reminders.json`` before it
is confirmed, re-armed when the channel starts, and claimed across processes
(the supervisor runs TWO processes holding the bot) so it goes out exactly once.
If Telegram refuses the post — business bots may only write into chats with recent
activity — the owner gets it in their DM instead: a reminder is never just lost.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_DELAY = timedelta(days=365)  # "remind me in 90 days" was asked twice
MAX_PER_CHAT = 20
#: Delivered late (the daemon was down) up to this much; older ones are dropped
#: with a note to the owner rather than surfacing a day-old surprise in a chat.
LATE_GRACE = timedelta(hours=12)
CLAIM_TTL_SEC = 3600

# ── parsing ──────────────────────────────────────────────────────────────────

_UNITS: tuple[tuple[str, int], ...] = (
    # (regex alternative, seconds). Longest spellings first inside each group.
    (r"weeks?|w|недел[яиюь]|нед|semaines?", 7 * 86400),
    (r"days?|d|дн(?:я|ей)|день|сут(?:ки|ок)|jours?|j", 86400),
    (r"hours?|hrs?|h|час(?:а|ов)?|ч|heures?", 3600),
    (r"minutes?|mins?|m|минут[аыу]?|мин|minutes?", 60),
    (r"seconds?|secs?|s|секунд[аыу]?|сек|secondes?", 1),
)
_UNIT_RE = "|".join(f"(?P<u{i}>{alt})" for i, (alt, _s) in enumerate(_UNITS))
_AMOUNT = rf"(?P<n>\d+(?:[.,]\d+)?)\s*(?:{_UNIT_RE})(?![\w])"
_AMOUNT_RE = re.compile(_AMOUNT, re.IGNORECASE)
_LEAD = r"(?:in|after|через|dans|me|мне|нам|moi|nous|us|and|и|et|,)"
_ARTICLE = r"(?:an?|one|une?|один|одну)"
#: A duration at the START: "in 15 hours …", "через 2 часа и 30 минут …", "2h …".
_HEAD_RE = re.compile(
    rf"^\s*(?:{_LEAD}\s+)*(?P<span>(?:(?:\d+(?:[.,]\d+)?\s*|{_ARTICLE}\s+)?(?:{_UNIT_RE})(?![\w])"
    rf"(?:\s+(?:and|и|et)\s+|\s+|$))+)",
    re.IGNORECASE,
)
#: …or announced anywhere: "call the bank in 2 hours", "позвонить через час".
_TAIL_RE = re.compile(
    rf"(?<![\w])(?:in|через|dans|after)\s+(?P<span>(?:(?:\d+(?:[.,]\d+)?\s*|{_ARTICLE}\s+)?(?:{_UNIT_RE})(?![\w])"
    rf"(?:\s+(?:and|и|et)\s+)?)+)",
    re.IGNORECASE,
)
# (?<![\w]): without it the "s" closing "is" read as one second ("Timer is broken").
_ONE_UNIT_RE = re.compile(rf"(?<![\w])(?:{_ARTICLE}\s+)?(?:{_UNIT_RE})(?![\w])", re.IGNORECASE)
#: Nothing but durations: "5 min", "1h 30m", "2 часа и 30 минут".
_ONLY_DURATIONS_RE = re.compile(
    rf"^\s*(?:\d+(?:[.,]\d+)?\s*(?:{_UNIT_RE})(?![\w])\s*(?:(?:and|и|et|,)\s*)?)+$", re.IGNORECASE
)
_HALF_HOUR_RE = re.compile(r"полчаса|half an hour|une demi-heure", re.IGNORECASE)
#: …or as the very last words: "Remember me about of Scheme 4 hours" (a real
#: message that got the usage line).
_END_RE = re.compile(
    rf"(?<![\w])(?P<span>(?:\d+(?:[.,]\d+)?\s*(?:{_UNIT_RE})(?![\w])\s*(?:(?:and|и|et)\s+)?)+)$",
    re.IGNORECASE,
)
_CONNECTORS = re.compile(
    r"^(?:(?:in|after|через|dans|me|мне|нам|moi|nous|us|to|that|что|about|of|:|-|—|,)\s+)+",
    re.IGNORECASE,
)


def _seconds(span: str) -> int:
    """Seconds in a duration phrase; a unit with no number counts once.

    Each unit counts on its own: "an hour and 30 minutes" is 90 minutes. Counting
    the implicit one only when nothing else matched read it as 30."""
    total = 0.0
    counted: list[tuple[int, int]] = []
    for m in _AMOUNT_RE.finditer(span):
        unit = next(i for i in range(len(_UNITS)) if m.group(f"u{i}"))
        total += float(m.group("n").replace(",", ".")) * _UNITS[unit][1]
        counted.append(m.span())
    for m in _ONE_UNIT_RE.finditer(span):
        if any(m.start() < end and start < m.end() for start, end in counted):
            continue  # the unit of a "<n> <unit>" already counted above
        unit = next(i for i in range(len(_UNITS)) if m.group(f"u{i}"))
        total += _UNITS[unit][1]
    return int(total)


# ── a day and/or a clock time ────────────────────────────────────────────────
#
# Asked 2026-10-01: "must work in Russian and French too" — "завтра в 10" and
# "demain à 9h" did not work in any language. Asked 2026-10-03: weekdays, dates
# and repeats too ("в пятницу в 10", "15.10 в 12:00", "каждый день в 9").

_DAYS: dict[str, int] = {
    "today": 0,
    "tonight": 0,
    "сегодня": 0,
    "вечером": 0,
    "aujourd'hui": 0,
    "aujourd’hui": 0,
    "ce soir": 0,
    "tomorrow": 1,
    "завтра": 1,
    "demain": 1,
    "послезавтра": 2,
    "après-demain": 2,
    "apres-demain": 2,
    "the day after tomorrow": 2,
}
_EVENING_DAYS = {"tonight", "вечером", "ce soir"}
_DAY_RE = "|".join(re.escape(d) for d in sorted(_DAYS, key=len, reverse=True))

#: Weekday spellings → Monday=0. Singular and the plural a repeat uses.
_WEEKDAY_FORMS: tuple[tuple[int, str], ...] = (
    (0, r"monday|mondays|понедельник(?:а|ам)?|lundis?"),
    (1, r"tuesday|tuesdays|вторник(?:а|ам)?|mardis?"),
    (2, r"wednesday|wednesdays|сред(?:а|у|ам)|mercredis?"),
    (3, r"thursday|thursdays|четверг(?:а|ам)?|jeudis?"),
    (4, r"friday|fridays|пятниц(?:а|у|ам)|vendredis?"),
    (5, r"saturday|saturdays|суббот(?:а|у|ам)|samedis?"),
    (6, r"sunday|sundays|воскресень(?:е|ям)|dimanches?"),
)
_WD_RE = "|".join(f"(?P<wd{i}>{alt})" for i, alt in _WEEKDAY_FORMS)

_MONTH_FORMS: tuple[tuple[int, str], ...] = (
    (1, r"january|jan|январ[ья]|janvier"),
    (2, r"february|feb|феврал[ья]|février|fevrier"),
    (3, r"march|mar|март[а]?|mars"),
    (4, r"april|apr|апрел[ья]|avril"),
    (5, r"may|ма[яй]|mai"),
    (6, r"june|jun|июн[ья]|juin"),
    (7, r"july|jul|июл[ья]|juillet"),
    (8, r"august|aug|август[а]?|août|aout"),
    (9, r"september|sept?|сентябр[ья]|septembre"),
    (10, r"october|oct|октябр[ья]|octobre"),
    (11, r"november|nov|ноябр[ья]|novembre"),
    (12, r"december|dec|декабр[ья]|décembre|decembre"),
)
_MON_RE = "|".join(f"(?P<mo{i}>{alt})" for i, alt in _MONTH_FORMS)

#: One "day" phrase: a relative day, a weekday, or a date.
_DAYSPEC_RE = re.compile(
    r"(?<![\w])(?:(?:on|next|this|в|во|ce|le|cette|по)\s+)?"
    rf"(?:(?P<rel>{_DAY_RE})"
    rf"|(?P<wday>{_WD_RE})(?:\s+(?:prochain|prochaine))?"
    r"|(?P<dd>\d{1,2})[./](?P<mm>\d{1,2})(?:[./](?P<yy>\d{2,4}))?"
    rf"|(?P<dd2>\d{{1,2}})(?:st|nd|rd|th|er)?\s+(?P<mon>{_MON_RE})"
    rf"|(?P<mon2>{_MONTH_FORMS[0][1]}|{'|'.join(a for _i, a in _MONTH_FORMS[1:])})\s+(?P<dd3>\d{{1,2}})(?:st|nd|rd|th)?"
    r")(?![\w])",
    re.IGNORECASE,
)
_AMPM = r"am|pm|a\.m\.|p\.m\.|утра|вечера|дня|ночи|du matin|du soir|de l'après-midi"
_HUNIT = r"(?:\s*(?:h|ч|час(?:а|ов)?|heures?)(?![\w]))?"
# A bare "a" (an unaccented "à") is also the English article: "buy a 2 litre bottle".
# It announces a clock only when what follows is written as one: "a 9h", "a 10:30".
_AT = r"(?:at|в|во|à|a(?=\s+\d{1,2}(?:[:hH]\d{2}|\s*(?:h|heures?))(?![\w]))|vers|к)"
#: A clock time that cannot be mistaken for any other number: announced by
#: at/в/à, or written as a time (10:30, 9h, 14h30, 5pm).
_CLOCK_RE = re.compile(
    rf"(?<![\w])(?:{_AT}\s+(?P<h>\d{{1,2}})(?:[:.hHч](?P<m>\d{{2}}))?\s*(?P<ap>{_AMPM})?{_HUNIT}"
    rf"|(?P<h2>\d{{1,2}})(?::(?P<m2>\d{{2}})\s*(?P<ap2>{_AMPM})?|\s*(?P<ap3>{_AMPM})"
    rf"|[hHч](?P<m3>\d{{2}})?))(?![\w])",
    re.IGNORECASE,
)
#: Right after a day phrase a bare hour is a time: "friday 10", "завтра 9".
_BARE_HOUR_RE = re.compile(
    rf"^\s*(?P<h>\d{{1,2}})\s*(?P<ap>{_AMPM})?{_HUNIT}(?![\w\d.:/])", re.IGNORECASE
)
_DEFAULT_HOUR = 9


def _group_index(m: re.Match, prefix: str, n: int) -> int | None:
    return next((i for i in range(n) if m.group(f"{prefix}{i}")), None)


def _clock_from(m: re.Match) -> tuple[int, int, str]:
    h = m.group("h") or m.group("h2")
    mins = m.group("m") or m.group("m2") or m.group("m3")
    ap = (m.group("ap") or m.group("ap2") or m.group("ap3") or "").lower()
    return int(h), int(mins) if mins else 0, ap


def _apply_ampm(hour: int, ap: str, evening: bool) -> int:
    if ap in ("pm", "p.m.", "вечера", "du soir", "de l'après-midi", "дня") and hour < 12:
        return hour + 12
    if ap in ("am", "a.m.", "утра", "du matin", "ночи") and hour == 12:
        return 0
    if evening and not ap and hour < 12:
        return hour + 12
    return hour


def _when(raw: str, now: datetime) -> tuple[datetime, str, dict[str, Any]] | None:
    """The local moment a day/clock phrase names, the text without it, and what was
    understood (``weekday`` for a weekly repeat, ``clock`` for its hour)."""
    rest = raw
    day: datetime | None = None
    weekday: int | None = None
    evening = False
    hour = minute = None
    ap = ""
    d = _DAYSPEC_RE.search(rest)
    if d is not None:
        if d.group("rel"):
            word = d.group("rel").lower()
            day = now + timedelta(days=_DAYS[word])
            evening = word in _EVENING_DAYS
        elif d.group("wday"):
            weekday = _group_index(d, "wd", 7)
            day = now + timedelta(days=(weekday - now.weekday()) % 7)
        else:
            if d.group("dd"):
                dd, mm, yy = int(d.group("dd")), int(d.group("mm")), d.group("yy")
            elif d.group("dd2"):
                mm = next(i for i, _alt in _MONTH_FORMS if d.group(f"mo{i}"))
                dd, yy = int(d.group("dd2")), None
            else:
                mon_word = d.group("mon2").lower()
                mm = next(
                    i for i, alt in _MONTH_FORMS if re.fullmatch(alt, mon_word, re.IGNORECASE)
                )
                dd, yy = int(d.group("dd3")), None
            year = now.year if yy is None else (int(yy) + 2000 if len(yy) == 2 else int(yy))
            try:
                day = now.replace(year=year, month=mm, day=dd)
            except ValueError:
                return None
            if yy is None and day.date() < now.date():
                day = day.replace(year=year + 1)
        after = rest[d.end() :]
        if (b := _BARE_HOUR_RE.match(after)) is not None:
            hour, minute, ap = int(b.group("h")), 0, (b.group("ap") or "").lower()
            after = after[b.end() :]
        rest = rest[: d.start()] + " " + after
    if hour is None and (c := _CLOCK_RE.search(rest)) is not None:
        hour, minute, ap = _clock_from(c)
        rest = rest[: c.start()] + " " + rest[c.end() :]
    if day is None and hour is None:
        return None
    clock_given = hour is not None
    hour = _apply_ampm(hour if hour is not None else _DEFAULT_HOUR, ap, evening)
    minute = minute or 0
    if hour > 23 or minute > 59:
        return None
    due = (day or now).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due <= now:
        if weekday is not None:
            due += timedelta(days=7)  # "в пятницу" said on Friday evening: next Friday
        elif day is None:
            due += timedelta(days=1)  # "в 10" said at 22:00 means tomorrow at 10
        else:
            return None
    return due, rest, {"weekday": weekday, "clock": (hour, minute), "clock_given": clock_given}


# ── repeats: "каждый день в 9", "по понедельникам", "every weekday at 8" ─────

_REPEAT_RE = re.compile(
    r"(?<![\w])(?:"
    r"(?P<daily>every\s*day|daily|each\s+day|каждый\s+день|ежедневно|tous\s+les\s+jours|chaque\s+jour)"
    r"|(?P<workdays>every\s+weekday|weekdays|on\s+weekdays|по\s+будням|каждый\s+будний\s+день|en\s+semaine|du\s+lundi\s+au\s+vendredi)"
    rf"|(?:every|each|каждый|каждую|каждое|по|tous\s+les|chaque)\s+(?P<wday>{_WD_RE})"
    r"|(?P<weekly>every\s+week|weekly|каждую\s+неделю|еженедельно|toutes\s+les\s+semaines|chaque\s+semaine)"
    r")(?![\w])",
    re.IGNORECASE,
)


def _repeat(raw: str) -> tuple[dict[str, Any], str] | None:
    m = _REPEAT_RE.search(raw)
    if m is None:
        return None
    rest = raw[: m.start()] + " " + raw[m.end() :]
    if m.group("daily"):
        return {"every": "day"}, rest
    if m.group("workdays"):
        return {"every": "workday"}, rest
    if m.group("wday"):
        return {"every": "week", "weekday": _group_index(m, "wd", 7)}, rest
    return {"every": "week"}, rest


def _on_the_wall(due: datetime) -> datetime:
    """*due*'s wall-clock time with the offset the local zone will REALLY have then.

    `datetime.now().astimezone()` carries a FIXED offset — today's. Calendar maths in
    it keeps that offset across a DST change, so "tomorrow at 10" said the day before
    the clocks move came out an hour off. A naive time re-localised by
    `astimezone()` takes the system zone's rule for that date."""
    return due.replace(tzinfo=None).astimezone()


def next_occurrence(repeat: dict[str, Any], after: datetime) -> datetime:
    """The first moment strictly after *after* that *repeat* names (local time)."""
    hour, minute = repeat.get("clock") or (_DEFAULT_HOUR, 0)
    due = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
    for _ in range(15):
        ok = due > after
        if ok and repeat["every"] == "workday" and due.weekday() >= 5:
            ok = False
        if ok and repeat["every"] == "week" and repeat.get("weekday") is not None:
            ok = due.weekday() == repeat["weekday"]
        if ok:
            return _on_the_wall(due)
        due += timedelta(days=1)
    return after + timedelta(days=1)


def parse(text: str, *, now: datetime | None = None) -> tuple[int, str] | None:
    """``(seconds, note)`` — see :func:`parse_when`."""
    when = parse_when(text, now=now)
    return None if when is None else (when[0], when[1])


def parse_when(text: str, *, now: datetime | None = None) -> tuple[int, str, bool] | None:
    """``"in 15 hours schema"`` → ``(54000, "schema", False)``; None without a time.

    A duration ("in 15 hours", "через 2 часа", "Scheme 4 hours"), or a day and/or a
    clock time ("завтра в 10", "в пятницу в 10", "15.10 в 12:00", "demain à 9h") —
    the third value says it was a clock time. A number inside the note ("buy 5 m of
    cable") is part of the note: a duration counts only at the start, announced by
    in/через/dans, or as the very last words."""
    full = parse_full(text, now=now)
    return None if full is None else (full["seconds"], full["about"], full["absolute"])


def parse_full(text: str, *, now: datetime | None = None) -> dict[str, Any] | None:
    """Everything a reminder request says: when, what, and whether it repeats."""
    raw = " ".join((text or "").split())
    local_now = (now or datetime.now()).astimezone()
    repeat = None
    if (rep := _repeat(raw)) is not None:
        repeat, raw = rep[0], " ".join(rep[1].split())
    seconds, rest, absolute = 0, raw, False
    if repeat is None and (half := _HALF_HOUR_RE.search(raw)) is not None:
        seconds, rest = 1800, raw[: half.start()] + " " + raw[half.end() :]
    elif (
        repeat is None
        and (head := _HEAD_RE.match(raw)) is not None
        and (n := _seconds(head.group("span")))
    ):
        seconds, rest = n, raw[head.end() :]
    elif (
        repeat is None
        and (tail := _TAIL_RE.search(raw)) is not None
        and (n := _seconds(tail.group("span")))
    ):
        seconds, rest = n, raw[: tail.start()] + " " + raw[tail.end() :]
    elif (
        repeat is None
        and (end := _END_RE.search(raw)) is not None
        and _end_is_duration(raw, end)
        and (n := _seconds(end.group("span")))
    ):
        seconds, rest = n, raw[: end.start()]
    elif (when := _when(raw, local_now)) is not None:
        due, rest, seen = when
        if repeat is not None:
            repeat["clock"] = seen["clock"]
            if (
                repeat["every"] == "week"
                and repeat.get("weekday") is None
                and seen["weekday"] is not None
            ):
                repeat["weekday"] = seen["weekday"]
            if repeat["every"] == "week" and repeat.get("weekday") is None:
                # "every week at 9": weekly from today, as the no-clock branch does —
                # a weekly rule without a weekday fires every day.
                repeat["weekday"] = local_now.weekday()
            due = next_occurrence(repeat, local_now)
        # Rounded UP: truncating made "завтра в 10" fire — and read — at 09:59.
        seconds, absolute = math.ceil((_on_the_wall(due) - local_now).total_seconds()), True
    elif repeat is not None:
        if repeat["every"] == "week" and repeat.get("weekday") is None:
            repeat["weekday"] = local_now.weekday()
        repeat["clock"] = (_DEFAULT_HOUR, 0)
        due = next_occurrence(repeat, local_now)
        seconds, absolute = math.ceil((due - local_now).total_seconds()), True
    if seconds <= 0:
        return None
    about = _CONNECTORS.sub("", " ".join(rest.split()) + " ").strip()
    return {"seconds": seconds, "about": about, "absolute": absolute, "repeat": repeat}


_AT_WORDS = frozenset({"at", "в", "во", "à", "a", "vers", "к"})


def _end_is_duration(raw: str, end: re.Match) -> bool:
    """ "Scheme 4 hours" is a duration; "demain à 9h" is a clock time — "9h" reads
    as both, and it is the "à" (or the day word) that says which."""
    before = raw[: end.start()].split()
    if before and before[-1].lower() in _AT_WORDS:
        return False
    return _DAYSPEC_RE.search(raw) is None


def duration_seconds(text: str) -> int:
    """Every ``<n> <unit>`` in *text*, summed — the timer's reading, in en/ru/fr.
    A number is required: a timer is never "one of a unit" by accident."""
    total = 0.0
    for m in _AMOUNT_RE.finditer(text or ""):
        unit = next(i for i in range(len(_UNITS)) if m.group(f"u{i}"))
        total += float(m.group("n").replace(",", ".")) * _UNITS[unit][1]
    return int(total)


def only_durations(text: str) -> bool:
    """True when *text* is nothing but durations — what a bare "timer …" must be."""
    return bool(_ONLY_DURATIONS_RE.match(text or ""))


def is_request(arg: str) -> bool:
    """Whether a bare "remind …" / "напомни …" is a reminder request rather than
    conversation. "Напомни пожалуйста во сколько у тебя дислокация" is a real
    message that is not one: no time in it."""
    words = (arg or "").strip()
    first = words.split(None, 1)[0].lower() if words else ""
    if first in _LIST_WORDS or first in _CANCEL_WORDS:
        return True
    return parse_full(words) is not None or names_a_past_time(words)


def names_a_past_time(text: str) -> bool:
    """A clock time that has already gone ("сегодня в 7 вечера" at 23:00)."""
    raw = " ".join((text or "").split())
    if _DAYSPEC_RE.search(raw) is None and _CLOCK_RE.search(raw) is None:
        return False
    return parse_full(raw) is None


def language(text: str) -> str:
    """The language to answer in, from the words that set the reminder."""
    if re.search(r"[а-яё]", text or "", re.IGNORECASE):
        return "ru"
    if re.search(
        r"\b(?:dans|heures?|jours?|rappel\w*|minutes? de|demain|ce soir|lundis?|mardis?|"
        r"mercredis?|jeudis?|vendredis?|samedis?|dimanches?|chaque|tous les|semaine)\b",
        text or "",
        re.IGNORECASE,
    ):
        return "fr"
    return "en"


_COPY = {
    "en": {
        "set": "⏰ I'll remind you in {when}{about} · {at}",
        "set_at": "⏰ I'll remind you {at}{about}",
        "past": "⏰ That time has already passed today.",
        "at_labels": ("at {t}", "tomorrow at {t}", "on {d} at {t}"),
        "due": "⏰ Reminder{about}",
        "usage": "⏰ <code>remind in 15 hours call the bank</code> · <code>remind list</code> · <code>remind cancel</code>",
        "too_far": "⏰ Up to a year ahead.",
        "too_many": "⏰ This chat already has 20 reminders waiting.",
        "none": "⏰ No reminders waiting here.",
        "list": "⏰ Waiting here:",
        "cancelled": "⏰ Cancelled {n} reminder(s).",
        "units": ("d", "h", "min"),
        "today": "",
        "other_day": "%d.%m ",
        "set_repeat": "⏰ I'll remind you {rep} at {t}{about}",
        "rep": {"day": "every day", "workday": "every weekday", "week": "every {wd}"},
        "wd": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
        "b_cancel": "✖ Cancel",
        "b_stop": "✖ Stop",
        "b_hour": "+1 h",
        "b_day": "+1 day",
        "cancelled_one": "⏰ Cancelled{about}",
        "snoozed": "{text}\n<i>→ again {at}</i>",
        "t_cancelled": "Cancelled",
        "t_snoozed": "Snoozed",
        "t_gone": "Already done",
    },
    "ru": {
        "set": "⏰ Напомню через {when}{about} · {at}",
        "set_at": "⏰ Напомню {at}{about}",
        "past": "⏰ Это время сегодня уже прошло.",
        "at_labels": ("в {t}", "завтра в {t}", "{d} в {t}"),
        "due": "⏰ Напоминание{about}",
        "usage": "⏰ <code>напомни через 2 часа позвонить</code> · <code>напомни список</code> · <code>напомни отмена</code>",
        "too_far": "⏰ Не дальше чем на год вперёд.",
        "too_many": "⏰ В этом чате уже ждут 20 напоминаний.",
        "none": "⏰ Здесь нет напоминаний.",
        "list": "⏰ Ждут здесь:",
        "cancelled": "⏰ Отменено напоминаний: {n}.",
        "units": ("дн", "ч", "мин"),
        "today": "",
        "other_day": "%d.%m ",
        "set_repeat": "⏰ Буду напоминать {rep} в {t}{about}",
        "rep": {"day": "каждый день", "workday": "по будням", "week": "{wd}"},
        "wd": (
            "по понедельникам",
            "по вторникам",
            "по средам",
            "по четвергам",
            "по пятницам",
            "по субботам",
            "по воскресеньям",
        ),
        "b_cancel": "✖ Отменить",
        "b_stop": "✖ Остановить",
        "b_hour": "+1 ч",
        "b_day": "+1 день",
        "cancelled_one": "⏰ Отменено{about}",
        "snoozed": "{text}\n<i>→ ещё раз {at}</i>",
        "t_cancelled": "Отменено",
        "t_snoozed": "Отложено",
        "t_gone": "Уже неактуально",
    },
    "fr": {
        "set": "⏰ Je vous le rappelle dans {when}{about} · {at}",
        "set_at": "⏰ Je vous le rappelle {at}{about}",
        "past": "⏰ Cette heure est déjà passée aujourd'hui.",
        "at_labels": ("à {t}", "demain à {t}", "le {d} à {t}"),
        "due": "⏰ Rappel{about}",
        "usage": "⏰ <code>rappelle dans 2 heures le devis</code> · <code>rappelle liste</code> · <code>rappelle annule</code>",
        "too_far": "⏰ Pas plus d'un an à l'avance.",
        "too_many": "⏰ Ce chat a déjà 20 rappels en attente.",
        "none": "⏰ Aucun rappel en attente ici.",
        "list": "⏰ En attente ici :",
        "cancelled": "⏰ {n} rappel(s) annulé(s).",
        "units": ("j", "h", "min"),
        "today": "",
        "other_day": "%d.%m ",
        "set_repeat": "⏰ Je vous le rappellerai {rep} à {t}{about}",
        "rep": {"day": "tous les jours", "workday": "en semaine", "week": "tous les {wd}"},
        "wd": ("lundis", "mardis", "mercredis", "jeudis", "vendredis", "samedis", "dimanches"),
        "b_cancel": "✖ Annuler",
        "b_stop": "✖ Arrêter",
        "b_hour": "+1 h",
        "b_day": "+1 jour",
        "cancelled_one": "⏰ Annulé{about}",
        "snoozed": "{text}\n<i>→ de nouveau {at}</i>",
        "t_cancelled": "Annulé",
        "t_snoozed": "Reporté",
        "t_gone": "Déjà fait",
    },
}
_LIST_WORDS = {"list", "ls", "список", "liste"}
_CANCEL_WORDS = {"cancel", "stop", "clear", "отмена", "отменить", "annule", "annuler"}


def _span(seconds: int, lang: str) -> str:
    d_u, h_u, m_u = _COPY[lang]["units"]
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    mins = max(1, round(rem / 60)) if not (days or hours) else round(rem / 60)
    parts = [
        f"{days} {d_u}" if days else "",
        f"{hours} {h_u}" if hours else "",
        f"{mins} {m_u}" if mins else "",
    ]
    return " ".join(p for p in parts if p)


def _clock(due: datetime, lang: str) -> str:
    local = due.astimezone()
    now = datetime.now().astimezone()
    day = "" if local.date() == now.date() else local.strftime(_COPY[lang]["other_day"])
    return f"{day}{local:%H:%M}"


def _at_label(due: datetime, lang: str) -> str:
    """ "завтра в 10:00" / "tomorrow at 10:00" / "le 03.10 à 10:00"."""
    local = due.astimezone()
    today = datetime.now().astimezone().date()
    same_day, next_day, other = _COPY[lang]["at_labels"]
    t = f"{local:%H:%M}"
    if local.date() == today:
        return same_day.format(t=t)
    if local.date() == today + timedelta(days=1):
        return next_day.format(t=t)
    return other.format(t=t, d=f"{local:%d.%m}")


def _rep_label(repeat: dict[str, Any], lang: str) -> str:
    c = _COPY[lang]
    if repeat["every"] == "week":
        return c["rep"]["week"].format(wd=c["wd"][repeat.get("weekday") or 0])
    return c["rep"][repeat["every"]]


def _keyboard(*buttons: tuple[str, str]) -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in buttons]]}


def _about(about: str) -> str:
    return f" — {_escape(about)}" if about else ""


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── storage ──────────────────────────────────────────────────────────────────


def _path() -> Path:
    from navig.platform import paths  # noqa: PLC0415

    return paths.data_dir() / "business_reminders.json"


def _load() -> list[dict[str, Any]]:
    path = _path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "[]")
    except (OSError, ValueError):
        # Never treat an unreadable file as empty: the next save would erase every
        # pending reminder. Keep it aside and start a fresh list.
        logger.warning("business reminders unreadable — kept aside as .corrupt", exc_info=True)
        try:
            path.replace(path.with_suffix(".json.corrupt"))
        except OSError:
            pass
        return []
    return (
        [r for r in data if isinstance(r, dict) and r.get("id")] if isinstance(data, list) else []
    )


def _save(items: list[dict[str, Any]]) -> None:
    from navig.core.yaml_io import atomic_write_text  # noqa: PLC0415

    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(items, ensure_ascii=False, indent=1))


_STORE_LOCK = threading.RLock()
_lock_state = threading.local()


@contextmanager
def _locked(path: Path | None = None) -> Iterator[None]:
    """Serialise a read-modify-write of the store (or a claim) across processes.

    The supervisor runs two processes holding the bot: two reminders settling at once
    both read ``[A, B]``, write ``[B]`` and ``[A]``, and the last write resurrected a
    delivered one. Re-entrant within a thread; a lock that cannot be taken degrades to
    the old unlocked behaviour (see `daily_once._locked`), never a failed delivery.
    """
    if getattr(_lock_state, "depth", 0):
        _lock_state.depth += 1
        try:
            yield
        finally:
            _lock_state.depth -= 1
        return
    from navig.telegram.daily_once import _locked as _file_lock  # noqa: PLC0415

    with _STORE_LOCK, _file_lock(path or _path()):
        _lock_state.depth = 1
        try:
            yield
        finally:
            _lock_state.depth = 0


#: A delivered reminder is kept this long, so its "+1 h" / "+1 day" still works.
DELIVERED_KEEP = timedelta(days=2)


def _remove(rid: str) -> None:
    with _locked():
        _save([r for r in _load() if r.get("id") != rid])


def _find(rid: str) -> dict[str, Any] | None:
    return next((r for r in _load() if r.get("id") == rid), None)


def _prune(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - DELIVERED_KEEP
    return [
        r
        for r in items
        if not r.get("delivered") or datetime.fromisoformat(r["delivered"]) > cutoff
    ]


def pending(chat_id: int) -> list[dict[str, Any]]:
    return sorted(
        (r for r in _load() if r.get("chat_id") == chat_id and not r.get("delivered")),
        key=lambda r: r["due"],
    )


# ── Planner adapter ──────────────────────────────────────────────────────────
# The Planner (OS app, Mini App, /api/deck/planner/reminders) lists, cancels and
# snoozes these alongside every other reminder. The JSON file stays the store: these
# reminders repeat, snooze by copy and run on their own timers, and moving them would
# be risk with no gain for the person reading them.


def list_all() -> list[dict[str, Any]]:
    """Every pending business reminder, in every chat, soonest first."""
    return sorted((r for r in _load() if not r.get("delivered")), key=lambda r: r["due"])


def cancel(rid: str) -> bool:
    """Remove one. An armed timer finds it gone and stays silent (see `_deliver`)."""
    if _find(rid) is None:
        return False
    _remove(rid)
    return True


def reschedule(channel: Any, rid: str, due: datetime) -> dict[str, Any] | None:
    """Move one to ``due`` and arm it in THIS process. The old timer, in this process
    or the sibling, sees a different ``due`` and stands down."""
    with _locked():
        items = _load()
        item = next((r for r in items if r.get("id") == rid), None)
        if item is None:
            return None
        item["due"] = due.astimezone(timezone.utc).isoformat()
        item.pop("delivered", None)
        _save(items)
    if channel is not None:
        _arm(channel, item, item.get("owner_id"))
    return item


# ── cross-process claim ──────────────────────────────────────────────────────


def _claim(rid: str) -> bool:
    """True if this process delivers *rid*. Atomic create, like the deletion digest;
    fails OPEN — one reminder twice beats one never sent.

    An expired claim (a crash between claim and settle) is taken over under the claim
    lock and rewritten with a fresh stamp, so only ONE process inherits it — returning
    True on the stale stamp alone let both supervisor processes post the reminder."""
    import time  # noqa: PLC0415

    from navig.platform import paths  # noqa: PLC0415

    path = paths.cache_dir() / "business_reminder_claims" / rid
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _locked(path.parent / ".claims"):
            try:
                with open(path, "x", encoding="utf-8") as fh:
                    fh.write(str(time.time()))
                return True
            except FileExistsError:
                try:
                    age = time.time() - float(path.read_text(encoding="utf-8").strip() or 0)
                except (OSError, ValueError):
                    age = float("inf")
                if age < CLAIM_TTL_SEC:
                    return False
                path.write_text(str(time.time()), encoding="utf-8")
                return True
    except OSError:
        return True


# ── scheduling + delivery ────────────────────────────────────────────────────

_ARMED: dict[str, asyncio.Task] = {}


def _arm(channel: Any, item: dict[str, Any], owner_id: int | None) -> None:
    rid = item["id"]
    if rid in _ARMED and not _ARMED[rid].done():
        return

    async def _later() -> None:
        due = datetime.fromisoformat(item["due"])
        wait = (due - datetime.now(timezone.utc)).total_seconds()
        if wait > 0:
            await asyncio.sleep(wait)
        await _deliver(channel, item, owner_id)

    from navig.core.background import spawn  # noqa: PLC0415

    _ARMED[rid] = spawn(_later())


#: When neither the chat nor the owner's DM took a reminder (Telegram down, network
#: gone — `_api_call` returns None for both), it is kept and tried again this often,
#: up to this many times, before it is settled as before.
RETRY_SEC = 300.0
RETRY_MAX = 6


def _release(claim: str) -> None:
    from navig.platform import paths  # noqa: PLC0415

    try:
        (paths.cache_dir() / "business_reminder_claims" / claim).unlink()
    except OSError:
        pass


def _retry(
    channel: Any, item: dict[str, Any], owner_id: int | None, attempt: int, claim: str
) -> None:
    """Nothing reached anyone: keep the reminder and deliver it again later."""
    _release(claim)  # this occurrence was not delivered; the retry claims it again

    async def _again() -> None:
        await asyncio.sleep(RETRY_SEC)
        await _deliver(channel, item, owner_id, attempt + 1)

    from navig.core.background import spawn  # noqa: PLC0415

    logger.info(
        "business reminder %s undelivered — retry %d in %.0fs", item["id"], attempt + 1, RETRY_SEC
    )
    _ARMED[item["id"]] = spawn(_again())


async def _send_card(
    channel: Any,
    item: dict[str, Any],
    lang: str,
    text: str,
    buttons: list[tuple[str, str]],
) -> Any:
    """Send the reminder as a card with its original button payloads; None on any failure."""
    try:
        from navig.telegram import biz_card, card_transport  # noqa: PLC0415

        repeat = ""
        if item.get("repeat"):
            repeat = _rep_label(item["repeat"], lang)
        card = biz_card.reminder_card(
            text=text,
            when=_at_label(datetime.fromisoformat(item["due"]), lang),
            repeat=repeat,
        )
        rows = [[{"text": t, "callback_data": d} for t, d in buttons]]
        return await card_transport.send_card(
            channel,
            item["chat_id"],
            card,
            keyboard=rows,
            business_connection_id=item.get("bcid"),
            reply_to_message_id=item.get("reply_to"),
        )
    except Exception:  # noqa: BLE001 — the caller falls back to the plain message
        logger.debug("business reminder card failed", exc_info=True)
        return None


async def _deliver(
    channel: Any, item: dict[str, Any], owner_id: int | None, attempt: int = 0
) -> None:
    rid = item["id"]
    try:
        current = _find(rid)
        if current is None or current.get("delivered") or current.get("due") != item["due"]:
            return  # cancelled, snoozed or rescheduled while it waited
        due_ts = int(datetime.fromisoformat(item["due"]).timestamp())
        claim = f"{rid}-{due_ts}"
        if not _claim(claim):
            return  # the sibling process has it (one claim per occurrence)
        lang = item.get("lang") or "en"
        late = datetime.now(timezone.utc) - datetime.fromisoformat(item["due"])
        text = _COPY[lang]["due"].format(about=_about(item.get("about") or ""))
        if late > LATE_GRACE and not item.get("repeat"):
            told = await _dm(
                channel, owner_id, f"{text}\n<i>(missed — NAVIG was offline when it was due)</i>"
            )
            if not told and attempt < RETRY_MAX:
                _retry(channel, item, owner_id, attempt, claim)
                return
            _remove(rid)
            logger.info(
                "business reminder %s dropped: %.1f h late", rid, late.total_seconds() / 3600
            )
            return
        data: dict[str, Any] = {"chat_id": item["chat_id"], "text": text, "parse_mode": "HTML"}
        if item.get("bcid"):
            data["business_connection_id"] = item["bcid"]
        if item.get("reply_to"):
            data["reply_parameters"] = {
                "message_id": item["reply_to"],
                "allow_sending_without_reply": True,
            }
        c = _COPY[lang]
        buttons = [(c["b_hour"], f"bzr:s:{rid}:3600"), (c["b_day"], f"bzr:s:{rid}:86400")]
        if item.get("repeat"):
            buttons.append((c["b_stop"], f"bzr:x:{rid}"))
        data["reply_markup"] = _keyboard(*buttons)
        sent = None
        if late <= LATE_GRACE:
            sent = await _send_card(channel, item, lang, text, buttons)
            if sent is None:
                # The card could not be drawn (old server, rich switched off, a raise):
                # the plain message below is the same reminder, not a lost one.
                sent = await channel._api_call("sendMessage", data)
        if sent or late > LATE_GRACE:
            # (late here = a repeat that was missed: rescheduled, not dropped into the chat)
            _settle(channel, item, sent, owner_id)
            if sent:
                logger.info("business reminder %s delivered to chat=%s", rid, item["chat_id"])
            return
        logger.info(
            "business reminder %s refused in chat=%s — sent to the owner", rid, item["chat_id"]
        )
        told = await _dm(
            channel,
            owner_id,
            f"{text}\n<i>(Telegram would not post it in "
            f"{_escape(item.get('chat_title') or 'the chat')} — here it is)</i>",
        )
        if not told and attempt < RETRY_MAX:
            # Settled only once someone has it: settling first lost the reminder for
            # good when Telegram was unreachable for both sends.
            _retry(channel, item, owner_id, attempt, claim)
            return
        _settle(channel, item, sent, owner_id)
    except Exception:  # noqa: BLE001
        logger.warning("business reminder %s delivery failed", rid, exc_info=True)
    finally:
        # Only our own entry: a repeat has already been re-armed by `_settle`, and
        # dropping THAT task here left "✖ Stop" unable to cancel the next one.
        if _ARMED.get(rid) is asyncio.current_task():
            _ARMED.pop(rid, None)


def _settle(channel: Any, item: dict[str, Any], sent: Any, owner_id: int | None) -> None:
    """After a delivery: a repeat moves to its next time and is re-armed; a one-off
    is kept (marked delivered) so its snooze buttons still work for a while."""
    with _locked():
        items = _load()
        for r in items:
            if r.get("id") != item["id"]:
                continue
            if r.get("repeat"):
                nxt = next_occurrence(r["repeat"], datetime.now().astimezone())
                r["due"] = nxt.astimezone(timezone.utc).isoformat()
            else:
                r["delivered"] = datetime.now(timezone.utc).isoformat()
                if isinstance(sent, dict) and sent.get("message_id"):
                    r["msg_id"] = sent["message_id"]
            updated = dict(r)
            break
        else:
            return
        _save(_prune(items))
    if updated.get("repeat"):
        _ARMED.pop(updated["id"], None)
        _arm(channel, updated, owner_id)


async def _dm(channel: Any, owner_id: int | None, text: str) -> bool:
    """Send *text* to the owner. True only when Telegram accepted it."""
    if not owner_id:
        return False
    from navig.telegram import chat_tidy  # noqa: PLC0415

    try:
        # A business reminder that fell back to the DM is a reminder like any other:
        # tidied tomorrow, not left there for good.
        with chat_tidy.routine("reminder"):
            return bool(await channel.send_message(owner_id, text, parse_mode="HTML"))
    except Exception:  # noqa: BLE001
        logger.debug("business reminder DM failed", exc_info=True)
        return False


async def resume_pending(channel: Any, owner_id: int | None = None) -> int:
    """Re-arm every saved reminder — a restart must not lose a 16-hour one."""
    items = [r for r in _load() if not r.get("delivered")]
    for item in items:
        _arm(channel, item, owner_id or item.get("owner_id"))
    if items:
        logger.info("business reminders resumed: %d", len(items))
    return len(items)


# ── buttons ──────────────────────────────────────────────────────────────────


async def handle_callback(
    channel: Any, cb_data: str, chat_id: Any, message_id: Any, user_id: Any
) -> str:
    """``bzr:x:<id>`` cancels (or stops a repeat); ``bzr:s:<id>:<secs>`` snoozes a
    delivered reminder. Returns the toast. Gated by the ``business`` extension."""
    parts = cb_data.split(":")
    if len(parts) < 3:
        return ""
    action, rid = parts[1], parts[2]
    item = _find(rid)
    lang = (item or {}).get("lang") or "en"
    c = _COPY[lang]
    if item is None:
        await _edit_markup(channel, chat_id, None, message_id)
        return c["t_gone"]
    bcid = item.get("bcid")
    about = _about(item.get("about") or "")
    if action == "x":
        _remove(rid)
        if (task := _ARMED.pop(rid, None)) is not None:
            task.cancel()
        await _edit(channel, chat_id, bcid, message_id, c["cancelled_one"].format(about=about))
        return c["t_cancelled"]
    if action == "s" and len(parts) == 4 and parts[3].isdigit():
        secs = min(int(parts[3]), 7 * 86400)
        due = datetime.now(timezone.utc) + timedelta(seconds=secs)
        # One snooze per delivered message: a double tap sends two callback queries,
        # and each one used to append and arm its own copy.
        source = f"{rid}:{message_id}"
        new = {k: v for k, v in item.items() if k not in ("delivered", "msg_id", "repeat")}
        new.update(
            id=uuid.uuid4().hex[:12],
            due=due.isoformat(),
            created=datetime.now(timezone.utc).isoformat(),
            snoozed_from=source,
        )
        with _locked():
            items = _load()
            if any(r.get("snoozed_from") == source for r in items):
                return c["t_snoozed"]
            items.append(new)
            _save(_prune(items))
        _arm(channel, new, item.get("owner_id"))
        text = c["snoozed"].format(text=c["due"].format(about=about), at=_at_label(due, lang))
        await _edit(channel, chat_id, bcid, message_id, text)
        return c["t_snoozed"]
    return ""


async def _edit(channel: Any, chat_id: Any, bcid: str | None, message_id: Any, text: str) -> None:
    data: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if bcid:
        data["business_connection_id"] = bcid
    try:
        await channel._api_call("editMessageText", data)
    except Exception:  # noqa: BLE001
        logger.debug("business reminder edit failed", exc_info=True)


async def _edit_markup(channel: Any, chat_id: Any, bcid: str | None, message_id: Any) -> None:
    data: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": {"inline_keyboard": []},
    }
    if bcid:
        data["business_connection_id"] = bcid
    try:
        await channel._api_call("editMessageReplyMarkup", data)
    except Exception:  # noqa: BLE001
        logger.debug("business reminder markup edit failed", exc_info=True)


# ── the command ──────────────────────────────────────────────────────────────


async def command(
    channel: Any,
    chat_id: int,
    bcid: str | None,
    arg: str,
    *,
    is_owner: bool = False,
    owner_id: int | None = None,
    trigger: dict | None = None,
    word: str = "",
    **_: Any,
) -> tuple[str, dict[str, Any] | None]:
    """Handle ``remind …``; returns the text to post in the chat and its buttons."""
    lang = language(f"{word} {arg}")
    copy = _COPY[lang]
    words = (arg or "").strip()
    first = words.split(None, 1)[0].lower() if words else ""
    if first in _LIST_WORDS:
        items = pending(chat_id)
        if not items:
            return copy["none"], None
        lines = [copy["list"]]
        for r in items:
            when = _clock(datetime.fromisoformat(r["due"]), lang)
            if r.get("repeat"):
                when = f"{_rep_label(r['repeat'], lang)} · {when}"
            lines.append(f"• {when}{_about(r.get('about') or '')}")
        return "\n".join(lines), None
    if first in _CANCEL_WORDS:
        if not is_owner:
            return copy["usage"], None
        with _locked():
            before = _load()
            keep = [r for r in before if r.get("chat_id") != chat_id or r.get("delivered")]
            _save(keep)
        for r in before:
            if r.get("chat_id") == chat_id and (t := _ARMED.pop(r["id"], None)):
                t.cancel()
        return copy["cancelled"].format(n=len(before) - len(keep)), None
    parsed = parse_full(words)
    if parsed is None:
        return (copy["past"] if names_a_past_time(words) else copy["usage"]), None
    seconds, about, absolute, repeat = (
        parsed["seconds"],
        parsed["about"],
        parsed["absolute"],
        parsed["repeat"],
    )
    if timedelta(seconds=seconds) > MAX_DELAY:
        return copy["too_far"], None
    with _locked():
        items = _prune(_load())
        if (
            sum(1 for r in items if r.get("chat_id") == chat_id and not r.get("delivered"))
            >= MAX_PER_CHAT
        ):
            return copy["too_many"], None
        due = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        chat = (trigger or {}).get("chat") or {}
        item = {
            "id": uuid.uuid4().hex[:12],
            "chat_id": chat_id,
            "bcid": bcid,
            "owner_id": owner_id,
            "due": due.isoformat(),
            "about": about,
            "lang": lang,
            "chat_title": chat.get("title") or chat.get("first_name") or "",
            # The owner's trigger is deleted; the other person's stays, so the reminder
            # can point back at the message that asked for it.
            "reply_to": None if is_owner else (trigger or {}).get("message_id"),
            "created": datetime.now(timezone.utc).isoformat(),
            "repeat": repeat,
        }
        items.append(item)
        _save(items)
    _arm(channel, item, owner_id)
    logger.info("business reminder %s set: chat=%s in=%ss", item["id"], chat_id, seconds)
    markup = _keyboard((copy["b_stop"] if repeat else copy["b_cancel"], f"bzr:x:{item['id']}"))
    if repeat:
        h, m = repeat["clock"]
        text = copy["set_repeat"].format(
            rep=_rep_label(repeat, lang), t=f"{h:02d}:{m:02d}", about=_about(about)
        )
    elif absolute:
        text = copy["set_at"].format(at=_at_label(due, lang), about=_about(about))
    else:
        text = copy["set"].format(
            when=_span(seconds, lang), about=_about(about), at=_clock(due, lang)
        )
    return text, markup
