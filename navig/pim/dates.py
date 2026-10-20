"""When something is due, said the way a person says it.

`/remindme` understands `in <n> m|h|d` and `at HH:MM` and nothing else — no dates at
all — and there is no natural-language date parser anywhere in core. A task list you
have to feed ISO timestamps is a task list you stop using, so this is that parser.

Three jobs, all pure functions of their arguments:

* :func:`split_due` — pull a date phrase off the END of a line, leaving the title.
  ``"Dentist tomorrow 10:30"`` → ``("Dentist", <tomorrow 10:30 local>)``.
* :func:`split_recurrence` — the same for ``every day`` / ``every monday`` / ``weekly``.
* :func:`advance` / :func:`humanize_delta` — move a due date on by its recurrence,
  and render "in 3h" / "2d ago" for the countdown.

**Why the phrase must be a SUFFIX.** Scanning anywhere in the line makes
``"Call Sep about the invoice"`` due in September. Anchoring at the end and requiring
the WHOLE suffix to parse means that line has no date, which is the right answer — a
missed date the operator can add with one tap beats a wrong one they never notice.

**Longest match wins.** ``"Dentist tomorrow 10:30"`` must not stop at ``10:30`` and
leave the title as "Dentist tomorrow". Candidate suffixes are tried longest-first.

**Timezone.** Everything here works in whatever zone ``now`` carries, and ``now`` is
always passed in — never read from the clock. The caller supplies an AWARE local
datetime; the store converts to UTC. Reminders compare as strings against a UTC
``…Z`` value, so a naive local time stored as-if-UTC fires off by the server's offset.
"""

from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta

__all__ = [
    "LEAD_TIMES",
    "RECURRENCES",
    "RecurrenceError",
    "advance",
    "describe_lead",
    "extract_when",
    "find_date",
    "find_when",
    "names_a_calendar_date",
    "parse_at",
    "parse_when",
    "format_local",
    "humanize_delta",
    "parse_lead",
    "split_due",
    "split_recurrence",
]


class RecurrenceError(ValueError):
    """An unknown recurrence token reached :func:`advance`."""


# Recurrences the card offers. Kept as a tuple so the keyboard, the validator and
# `advance()` can never disagree about what exists.
RECURRENCES: tuple[str, ...] = ("daily", "weekly", "monthly", "yearly")

# Lead times the "remind me before" picker offers, longest first (how they read in
# a list). The value is the stored token; `parse_lead` accepts any `<n><unit>`.
LEAD_TIMES: tuple[str, ...] = ("1w", "3d", "1d", "4h", "1h", "15m")

_WEEKDAYS: dict[str, int] = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
    # French and Russian, because the operator writes in both and an appointment
    # pasted from a French email is still an appointment. Full names only: a
    # French abbreviation like "mar" (mardi) collides with English "mar" (March).
    "lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3, "vendredi": 4,
    "samedi": 5, "dimanche": 6,
    "понедельник": 0, "вторник": 1, "среда": 2, "среду": 2, "четверг": 3,
    "пятница": 4, "пятницу": 4, "суббота": 5, "субботу": 5, "воскресенье": 6,
}

#: English fallbacks for the weekday abbreviations, matching `_MONTHS` below. The
#: locale files carry `weekday.0` … `weekday.6`; these are what renders if one is gone.
_WEEKDAYS_SHORT: tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

_MONTHS: dict[str, int] = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5,
    "juin": 6, "juillet": 7, "août": 8, "aout": 8, "septembre": 9, "octobre": 10,
    "novembre": 11, "décembre": 12, "decembre": 12,
    # Russian: nominative AND genitive — "26 октября" is how a date is written.
    "январь": 1, "января": 1, "февраль": 2, "февраля": 2, "март": 3, "марта": 3,
    "апрель": 4, "апреля": 4, "май": 5, "мая": 5, "июнь": 6, "июня": 6,
    "июль": 7, "июля": 7, "август": 8, "августа": 8, "сентябрь": 9, "сентября": 9,
    "октябрь": 10, "октября": 10, "ноябрь": 11, "ноября": 11,
    "декабрь": 12, "декабря": 12,
}

_UNIT_SECONDS: dict[str, int] = {
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "week": 604800, "weeks": 604800,
    # French and Russian, for "dans 3 jours" / "через 3 дня".
    "heure": 3600, "heures": 3600, "jour": 86400, "jours": 86400,
    "semaine": 604800, "semaines": 604800,
    "мин": 60, "минуту": 60, "минуты": 60, "минут": 60,
    "час": 3600, "часа": 3600, "часов": 3600,
    "день": 86400, "дня": 86400, "дней": 86400,
    "неделю": 604800, "недели": 604800, "недель": 604800,
}

#: Days named relative to today, in the operator's languages. A value of None means
#: "in the past" — a due date nobody means.
_RELATIVE_DAYS: dict[str, int | None] = {
    "today": 0, "tonight": 0, "tomorrow": 1, "day after tomorrow": 2, "overmorrow": 2,
    "yesterday": None,
    "aujourd'hui": 0, "ce soir": 0, "demain": 1, "demain soir": 1,
    "après-demain": 2, "apres-demain": 2, "après demain": 2, "apres demain": 2,
    "hier": None,
    "сегодня": 0, "сегодня вечером": 0, "завтра": 1, "завтра вечером": 1,
    "послезавтра": 2, "вчера": None,
}

# "next monday" / "lundi prochain" / "следующий понедельник"; "this friday" / "эту пятницу".
_NEXT_PREFIX = frozenset({"next", "следующий", "следующую", "следующее", "следующая"})
_NEXT_SUFFIX = frozenset({"prochain", "prochaine"})
_THIS_PREFIX = frozenset({"this", "ce", "cette", "этот", "эту", "это"})
_NEXT_WEEK = frozenset({
    "next week", "semaine prochaine", "la semaine prochaine",
    "следующей неделе", "на следующей неделе",
})

# The longest date phrase worth considering, in words: "next tuesday at half past" is
# already beyond this grammar, and a larger window only costs false positives.
_MAX_PHRASE_WORDS = 7  # "le lundi 26 octobre 2026 à 10h30"

# Words that may sit in front of a phrase without changing it. They are stripped from
# the CANDIDATE, not from the title, so "Pay the bill on friday" keeps "Pay the bill".
_LEADERS = frozenset({"at", "on", "by", "@", "le", "à", "в", "во"})

# Words that turn the number after them into a DURATION ("in 1h30", "dans 2h").
_DURATION_PREPS = frozenset({"in", "dans", "через"})

# Default clock time when a day is named without one. 9am is the "sometime that day"
# convention; a task due "friday" that fires at 00:00 is a task you see on Thursday
# night and forget by morning.
_DEFAULT_HOUR = 9
_DEFAULT_MINUTE = 0


def _t(key: str, **fields: object) -> str:
    """One localized string. Returns "" when the key is absent, so a caller can test
    it — the locale files ship every key this module reads, and a missing one means a
    file was edited, not that English should be silently substituted."""
    try:
        from navig.core import i18n  # noqa: PLC0415

        text = i18n.t(key, **fields)
    except Exception:  # noqa: BLE001 — a render must never fail on a locale read
        return ""
    return "" if text == key else text


#: English fallbacks for the abbreviated month names.
_MONTHS_SHORT: tuple[str, ...] = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def _localized_month(month: int, *, short: bool = False) -> str:
    """The month name, in the operator's language.

    Two forms, because they do two jobs. A card TITLE says "6 сентября" — the full
    genitive is how a person reads a date. A task ROW already carries a mark, a title,
    a category, a time and a countdown, so it takes the short form or the title falls
    off a phone screen.
    """
    if short:
        return _t(f"month_short.{month}") or _MONTHS_SHORT[month - 1]
    return _t(f"month.{month}") or _MONTHS_SHORT[month - 1]


def _localized_weekday(index: int) -> str:
    """The weekday abbreviation, in the operator's language."""
    return _t(f"weekday.{index}") or _WEEKDAYS_SHORT[index]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _words(text: str) -> list[str]:
    return _norm(text).split(" ") if _norm(text) else []


def _at(base: datetime, *, hour: int, minute: int) -> datetime:
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


# ── time-of-day ──────────────────────────────────────────────────────────────

_TIME_RE = re.compile(
    r"^(?P<h>\d{1,2})"
    # "10h30" is how French writes a time. The minutes are REQUIRED after an "h":
    # a bare "3h" is a duration ("in 3h"), and reading it as 03:00 would turn
    # "call mom in 3h" into a task due at three in the morning.
    r"(?:[:.hH](?P<m>\d{2})|(?P<bare_h>[hH]))?"
    r"\s*(?P<mer>am|pm|a\.m\.|p\.m\.)?$",
    re.IGNORECASE,
)


def _parse_time(token: str) -> tuple[int, int] | None:
    """``9`` · ``9am`` · ``9:30`` · ``18:00`` · ``9.30pm`` → (hour, minute).

    A bare number is ONLY a time when it carries a meridiem or a minute part — "3"
    on its own is far more often a quantity than 3 o'clock, and guessing wrong puts
    a task on the board at the wrong hour with nothing to notice it by.
    """
    m = _TIME_RE.match(token.strip())
    if not m:
        return None
    hour = int(m.group("h"))
    minute = int(m.group("m") or 0)
    mer = (m.group("mer") or "").replace(".", "").lower()
    if not mer and m.group("m") is None and not m.group("bare_h"):
        return None
    if minute > 59:
        return None
    if mer:
        if not 1 <= hour <= 12:
            return None
        if mer == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
    elif hour > 23:
        return None
    return hour, minute


_BARE_H_RE = re.compile(r"^\d{1,2}[hH]$")


def _is_bare_h(token: str) -> bool:
    """``10h`` — French for ten o'clock, and also how a DURATION is written ("2h").
    Read as a time only when a day is named alongside it (see `_try_phrase`)."""
    return bool(_BARE_H_RE.match(token.strip()))


def _split_trailing_time(words: list[str]) -> tuple[list[str], tuple[int, int] | None]:
    """Peel an optional trailing time (and its `at`) off a candidate phrase."""
    if not words:
        return words, None
    # "9 pm" arrives as two tokens; try the pair before the single.
    if len(words) >= 2:
        pair = _parse_time(words[-2] + words[-1])
        if pair is not None:
            rest = words[:-2]
            if rest and rest[-1].lower() in _LEADERS:
                rest = rest[:-1]
            return rest, pair
    single = _parse_time(words[-1])
    if single is not None:
        rest = words[:-1]
        # "in 1h30" is a duration, not half past one.
        if "h" in words[-1].lower() and rest and rest[-1].lower() in _DURATION_PREPS:
            return words, None
        if rest and rest[-1].lower() in _LEADERS:
            rest = rest[:-1]
        return rest, single
    return words, None


# ── day anchors ──────────────────────────────────────────────────────────────

_ISO_RE = re.compile(r"^(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})$")
_SLASH_RE = re.compile(r"^(?P<d>\d{1,2})[/.](?P<m>\d{1,2})(?:[/.](?P<y>\d{2,4}))?$")
_ORDINAL_RE = re.compile(r"^(?P<d>\d{1,2})(?:st|nd|rd|th)?$", re.IGNORECASE)


def _next_weekday(now: datetime, target: int, *, force_next_week: bool) -> datetime:
    """The next occurrence of a weekday.

    Plain ``friday`` means "the coming friday", and TODAY counts only if the time is
    still ahead — that is decided by the caller, which knows the hour. ``next friday``
    always skips a same-day match, because someone who says "next" does not mean today.
    """
    delta = (target - now.weekday()) % 7
    if delta == 0 and force_next_week:
        delta = 7
    return now + timedelta(days=delta)


def _with_year(now: datetime, month: int, day: int) -> datetime | None:
    """A month/day in the nearest sensible year.

    A bare ``Sep 12`` that has already passed means NEXT September — a task list
    cannot hold a date in the past, and silently creating one is how a list starts
    lying about what is overdue.

    Four years, not two: ``feb 29`` needs up to four to reach a leap year, and the
    extra iterations are unreachable for every other date because the first candidate
    on or after today always wins.
    """
    for year in range(now.year, now.year + 4):
        last = calendar.monthrange(year, month)[1]
        if day > last:
            continue  # 29 Feb in a non-leap year: try the next year rather than clamp
        candidate = now.replace(
            year=year, month=month, day=day, hour=0, minute=0, second=0, microsecond=0
        )
        if candidate.date() >= now.date():
            return candidate
    return None


def _parse_day(words: list[str], now: datetime) -> tuple[datetime, bool] | None:
    """Resolve a day phrase to a date. Returns (date-at-midnight, day_is_explicit).

    The second element says whether the phrase named a DAY at all: an empty phrase
    (the caller found only a time) resolves to today and reports False, which is how
    ``_roll_forward`` knows it may push a past time to tomorrow.
    """
    lowered = [w.lower().strip(",") for w in words]

    if not lowered:
        return _at(now, hour=0, minute=0), False

    joined = " ".join(lowered)

    if joined in _RELATIVE_DAYS:
        offset = _RELATIVE_DAYS[joined]
        if offset is None:
            return None  # a due date in the past is never what was meant
        return _at(now + timedelta(days=offset), hour=0, minute=0), True

    if joined in _NEXT_WEEK:
        return _at(now + timedelta(days=7), hour=0, minute=0), True

    # "lundi prochain" — French puts "next" after the weekday.
    if len(lowered) == 2 and lowered[0] in _WEEKDAYS and lowered[1] in _NEXT_SUFFIX:
        return (
            _at(_next_weekday(now, _WEEKDAYS[lowered[0]], force_next_week=True), hour=0, minute=0),
            True,
        )

    # "in 3 days" / "in 2 weeks" / "in 90m" / "dans 3 jours" / "через 3 дня"
    if lowered[0] in _DURATION_PREPS and len(lowered) in (2, 3):
        qty_token = lowered[1]
        unit_token = lowered[2] if len(lowered) == 3 else ""
        if not unit_token:
            m = re.match(r"^(\d+)(\D+)$", qty_token)
            if not m:
                return None
            qty_token, unit_token = m.group(1), m.group(2)
        if not qty_token.isdigit():
            return None
        seconds = _UNIT_SECONDS.get(unit_token)
        if seconds is None:
            return None
        return now + timedelta(seconds=int(qty_token) * seconds), True

    # "next monday" / "this friday" / "следующий понедельник" / "эту пятницу"
    if (lowered[0] in _NEXT_PREFIX or lowered[0] in _THIS_PREFIX) and len(lowered) == 2:
        if lowered[1] in _WEEKDAYS:
            return (
                _at(
                    _next_weekday(
                        now, _WEEKDAYS[lowered[1]], force_next_week=lowered[0] in _NEXT_PREFIX
                    ),
                    hour=0,
                    minute=0,
                ),
                True,
            )
        if lowered[1] == "week":
            return _at(now + timedelta(days=7), hour=0, minute=0), True
        if lowered[1] == "month":
            return _at(_add_months(now, 1), hour=0, minute=0), True
        return None

    if len(lowered) == 1:
        token = lowered[0]
        if token in _WEEKDAYS:
            # Plain "friday": today counts, and `_roll_forward` moves it on if the
            # hour has already gone.
            return (
                _at(_next_weekday(now, _WEEKDAYS[token], force_next_week=False), hour=0, minute=0),
                True,
            )
        iso = _ISO_RE.match(token)
        if iso:
            return _safe_date(now, int(iso.group("y")), int(iso.group("m")), int(iso.group("d")))
        slash = _SLASH_RE.match(token)
        if slash:
            day, month = int(slash.group("d")), int(slash.group("m"))
            raw_year = slash.group("y")
            if raw_year:
                year = int(raw_year)
                if year < 100:
                    year += 2000
                return _safe_date(now, year, month, day)
            if not 1 <= month <= 12:
                return None
            found = _with_year(now, month, day)
            return (found, True) if found else None
        return None

    # "lundi 26 octobre" / "monday 12 sep 2026": the weekday is decoration on an
    # explicit date, and the DATE wins — a mismatched weekday is a typo, not a
    # second instruction.
    if len(lowered) >= 3 and lowered[0] in _WEEKDAYS:
        return _parse_day(words[1:], now)

    # "26 octobre 2026" / "october 26 2026" / "26 октября 2026"
    if len(lowered) == 3 and lowered[2].isdigit() and len(lowered[2]) == 4:
        year = int(lowered[2])
        a, b = lowered[0], lowered[1]
        for name, num in ((a, b), (b, a)):
            if name in _MONTHS:
                om = _ORDINAL_RE.match(num)
                if om:
                    return _safe_date(now, year, _MONTHS[name], int(om.group("d")))
        return None

    # "sep 12" / "12 sep" / "september 12th"
    if len(lowered) == 2:
        a, b = lowered
        for name, num in ((a, b), (b, a)):
            if name in _MONTHS:
                om = _ORDINAL_RE.match(num)
                if not om:
                    continue
                found = _with_year(now, _MONTHS[name], int(om.group("d")))
                return (found, True) if found else None
    return None


def _safe_date(now: datetime, year: int, month: int, day: int) -> tuple[datetime, bool] | None:
    if not 1 <= month <= 12:
        return None
    if not 1 <= day <= calendar.monthrange(year, month)[1]:
        return None
    return now.replace(year=year, month=month, day=day, hour=0, minute=0, second=0, microsecond=0), True


def _add_months(base: datetime, months: int) -> datetime:
    """Add whole months, clamping the day to the target month's length.

    31 Jan + 1 month is 28/29 Feb. Any other answer either skips a month or invents
    a date, and for a monthly recurrence that compounds every time it fires.
    """
    total = base.month - 1 + months
    year = base.year + total // 12
    month = total % 12 + 1
    day = min(base.day, calendar.monthrange(year, month)[1])
    return base.replace(year=year, month=month, day=day)


def _roll_forward(when: datetime, now: datetime, *, day_explicit: bool) -> datetime | None:
    """Resolve "9am" typed at 3pm.

    With no day named, a time that has already passed means TOMORROW. With a day
    named, it does not — "friday 9am" typed on a Friday afternoon means next Friday,
    which `_next_weekday` handles, and anything else the operator typed a date for is
    taken at face value.
    """
    if when > now:
        return when
    if not day_explicit:
        return when + timedelta(days=1)
    if when.date() == now.date():
        return when + timedelta(days=7) if _looked_like_weekday(when, now) else None
    return None


def _looked_like_weekday(when: datetime, now: datetime) -> bool:
    return when.date() == now.date()


def _try_phrase(phrase: str, now: datetime) -> datetime | None:
    words = _words(phrase)
    if not words:
        return None
    if words[0].lower() in _LEADERS:
        words = words[1:]
        if not words:
            return None

    rest, time_part = _split_trailing_time(words)
    if rest and rest[0].lower() in _LEADERS:
        rest = rest[1:]
    if not rest and time_part is None:
        return None
    if not rest and _is_bare_h(words[-1]):
        return None  # "Workout 2h" is a duration, not two in the morning

    day = _parse_day(rest, now)
    if day is None:
        return None
    base, day_explicit = day

    if time_part is None:
        # "in 90 minutes" already carries a time; a named day does not.
        if base.hour or base.minute:
            when = base
        else:
            when = _at(base, hour=_DEFAULT_HOUR, minute=_DEFAULT_MINUTE)
    else:
        when = _at(base, hour=time_part[0], minute=time_part[1])

    return _roll_forward(when, now, day_explicit=day_explicit)


def _continues_a_date(word: str) -> bool:
    w = word.strip(_EDGE_PUNCT).lower()
    return bool(
        (w.isdigit() and len(w) == 4)
        or w in _MONTHS
        or _ISO_RE.match(w)
        or _SLASH_RE.match(w)
    )


def split_due(text: str, now: datetime) -> tuple[str, datetime | None]:
    """Split a line into (title, due date), taking the date off the END.

    ``now`` must be an AWARE datetime in the operator's zone; the result carries the
    same zone. Returns the input unchanged with ``None`` when no suffix parses — which
    is the common case and must never be an error.
    """
    words = _words(text)
    if not words:
        return _norm(text), None

    for size in range(min(_MAX_PHRASE_WORDS, len(words)), 0, -1):
        # A phrase may not swallow the whole line: "tomorrow" alone is a title, not a
        # dateless task called "".
        if size == len(words):
            continue
        # "in 1h30" is a duration: its "1h30" alone must not read as half past one.
        if words[-size - 1].lower() in _DURATION_PREPS and re.match(
            r"^\d+h", words[-size], re.IGNORECASE
        ):
            continue
        # A suffix that starts in the MIDDLE of a date ("… 5 janvier 2025 10h00" →
        # just "10h00") is not that date: the longer phrase failed because it is in
        # the past, and keeping only its tail would silently move it to tomorrow.
        if _continues_a_date(words[-size - 1]):
            continue
        when = _try_phrase(" ".join(words[-size:]), now)
        if when is not None:
            title = " ".join(words[:-size]).rstrip(" ,-–—")
            if title:
                return title, when
    return " ".join(words), None


_EDGE_PUNCT = "\"'«»“”()[]{},;:!?."


def find_when(text: str, now: datetime) -> tuple[datetime, int, int] | None:
    """Find an appointment's moment ANYWHERE in a message.

    Returns ``(when, start, end)`` where ``start:end`` is the word span the date
    occupied in ``text.split()``, or ``None``.

    This is deliberately stricter than :func:`split_due`, because it scans the whole
    message rather than an anchored suffix — the "Call Sep about the invoice" problem
    the module docstring describes. A hit needs ALL of:

    * a day that can only mean the FUTURE — a calendar date ("26 octobre", "12/10",
      "2026-10-26"), a relative day ("demain", "après-demain", "завтра"), "next"
      plus a weekday ("lundi prochain", "следующий понедельник"), or a delay ("dans
      3 jours"). A BARE weekday is not enough: "we met on monday at 10:30" is a
      story, not an appointment;
    * an explicit clock time ("10:30", "10h30", "10h", "9am");
    * a moment still in the future.

    Longest span wins, then the earliest one.
    """
    raw = text.split()
    words = [w.strip(_EDGE_PUNCT) for w in raw]
    if len(words) < 2:
        return None

    for size in range(min(_MAX_PHRASE_WORDS, len(words)), 1, -1):
        for start in range(0, len(words) - size + 1):
            window = [w for w in words[start : start + size] if w]
            if len(window) < 2:
                continue
            if window[0].lower() in _LEADERS:
                continue  # the same window without the leader is tried; prefer it trimmed
            rest, time_part = _split_trailing_time(window)
            if time_part is None or not rest:
                continue
            if rest[-1].lower() in _LEADERS:
                rest = rest[:-1]
            if not (_names_a_calendar_date(rest) or _names_a_future_day(rest)):
                continue
            day = _parse_day(rest, now)
            if day is None or not day[1]:
                continue
            when = _at(day[0], hour=time_part[0], minute=time_part[1])
            if when <= now:
                continue
            # Swallow a leader just before the span ("le", "on", "в") so the title
            # left behind does not end in a dangling preposition.
            begin = start
            if begin > 0 and words[begin - 1].lower() in _LEADERS:
                begin -= 1
            return when, begin, start + size
    return None


def find_date(text: str, now: datetime) -> tuple[datetime, int, int] | None:
    """Find a parseable FUTURE date ANYWHERE in the message.

    Returns ``(when, start, end)`` like :func:`find_when`, minus its precision
    gates: no explicit clock time is required (the day gets the default hour,
    mirroring :func:`_try_phrase`), a single-word window is allowed, and the
    whole-message skip does not apply.

    This is for OFFER/DETECT decisions only — "does this message carry a date,
    so should the task tools be offered?". Never use it to silently create an
    appointment or task: :func:`find_when` stays the strict detector its
    docstring promises (a date alone is not an appointment — "renew on October
    28" is a deadline the model must route through ``task_add``, not a meeting
    to book behind the operator's back).
    """
    raw = text.split()
    words = [w.strip(_EDGE_PUNCT) for w in raw]
    if not words:
        return None

    for size in range(min(_MAX_PHRASE_WORDS, len(words)), 0, -1):
        for start in range(0, len(words) - size + 1):
            window = [w for w in words[start : start + size] if w]
            if not window:
                continue
            if window[0].lower() in _LEADERS:
                continue  # the same window without the leader is tried; prefer it trimmed
            rest, time_part = _split_trailing_time(window)
            if not rest:
                continue
            if rest[-1].lower() in _LEADERS:
                rest = rest[:-1]
            if not (_names_a_calendar_date(rest) or _names_a_future_day(rest)):
                continue
            day = _parse_day(rest, now)
            if day is None or not day[1]:
                continue
            if time_part is None:
                # A named day carries no clock time — same rule as _try_phrase:
                # a duration already timed keeps its time, otherwise default.
                base = day[0]
                when = (
                    base
                    if (base.hour or base.minute)
                    else _at(base, hour=_DEFAULT_HOUR, minute=_DEFAULT_MINUTE)
                )
            else:
                when = _at(day[0], hour=time_part[0], minute=time_part[1])
            if when <= now:
                continue
            begin = start
            if begin > 0 and words[begin - 1].lower() in _LEADERS:
                begin -= 1
            return when, begin, start + size
    return None


def _names_a_calendar_date(words: list[str]) -> bool:
    lowered = [w.lower() for w in words]
    if any(w in _MONTHS for w in lowered):
        return any(_ORDINAL_RE.match(w) for w in lowered)
    return any(_ISO_RE.match(w) or _SLASH_RE.match(w) for w in lowered)


def _names_a_future_day(words: list[str]) -> bool:
    lowered = [w.lower() for w in words]
    joined = " ".join(lowered)
    if _RELATIVE_DAYS.get(joined) is not None or joined in _NEXT_WEEK:
        return True
    if len(lowered) == 2:
        if lowered[0] in _NEXT_PREFIX and lowered[1] in _WEEKDAYS:
            return True
        if lowered[0] in _WEEKDAYS and lowered[1] in _NEXT_SUFFIX:
            return True
    return bool(lowered) and lowered[0] in _DURATION_PREPS and len(lowered) in (2, 3)


def names_a_calendar_date(words: list[str]) -> bool:
    """Does this phrase name a CALENDAR date ("26 octobre", "12/10", "2026-10-26"),
    as opposed to a relative one ("demain", "lundi prochain")? Public so a caller can
    treat the two with different confidence."""
    return _names_a_calendar_date([w.strip(_EDGE_PUNCT) for w in words])


# Words that only connect a reminder's TIME to its TEXT: "remind me TO call mom",
# "rappelle-moi DE rappeler", "напомни ПРО встречу". Dropped from the front of the text.
_TEXT_CONNECTORS = frozenset({
    "to", "about", "that", "de", "d'", "du", "que", "qu'",
    "что", "о", "об", "про", "чтобы",
})


def _clean_text(words: list[str]) -> str:
    out = list(words)
    while out:
        head = out[0].lower()
        if head in _TEXT_CONNECTORS or head in _LEADERS:
            out = out[1:]
            continue
        for glued in ("d'", "qu'"):
            if head.startswith(glued) and len(head) > len(glued):
                out[0] = out[0][len(glued):]
        break
    return " ".join(out).strip(" ,;:-–—.")


def parse_when(phrase: str, now: datetime) -> datetime | None:
    """A phrase that is ONLY a time — "demain 10h", "in 30 min", "26 октября 10:30" —
    for a caller that already has the text separately (the deck's reminder form)."""
    words = [w.strip(_EDGE_PUNCT) for w in (phrase or "").split()]
    words = [w for w in words if w]
    if not words or len(words) > _MAX_PHRASE_WORDS:
        return None
    return _try_phrase(" ".join(words), now)


def parse_at(phrase: str, now: datetime) -> datetime | None:
    """An EXPLICIT moment for ``--at``: ISO first (``2026-10-12T09:00`` / ``2026-10-12 09:00``),
    then the same short phrases :func:`parse_when` reads (``demain 10h``, ``next monday 9am``).

    Exists because :func:`split_due` reads the date out of the SENTENCE, and a reminder's
    text often mentions other dates: ``"RDV 11h45 - apporter l'IRM du 30/09"`` came back due
    on 30 September of the NEXT year with ``30/09`` cut out of the text. A caller that
    knows the moment (an agent, a script) passes it here and keeps its text verbatim.

    A naive ISO value is read in ``now``'s zone — the operator's local time.
    """
    raw = (phrase or "").strip()
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        return parse_when(raw, now)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        # A bare date means the morning, as it does in split_due — never a midnight ping.
        when = when.replace(hour=9)
    if when.tzinfo is None and now.tzinfo is not None:
        when = when.replace(tzinfo=now.tzinfo)
    return when


def extract_when(text: str, now: datetime) -> tuple[datetime, str] | None:
    """THE reader for an explicit reminder request: ``(when, what)`` or None.

    `/remindme …` and "rappelle-moi … / напомни … / remind me …" both come here, so
    every way a reminder is added understands the same dates. Unlike
    :func:`find_when` it is LENIENT — the operator has already said "remind me", so a
    bare time ("18:45"), a weekday ("friday 9am") or a delay ("in 30 min") is
    exactly what they meant. Tried in order:

    1. a strict date anywhere — "appeler Sarah le lundi 26 octobre à 10h30";
    2. a date at the START — "in 30 min check logs", "demain 10h appeler", "18:45 deploy";
    3. a date at the END — "check logs in 30 min", "call mom tomorrow 9am".

    The text must not be empty: a reminder that says nothing is not a reminder.
    """
    words = (text or "").split()
    if not words:
        return None

    strict = find_when(text, now)
    if strict is not None:
        when, start, end = strict
        what = _clean_text(words[:start] + words[end:])
        if what:
            return when, what
    else:
        # An explicit calendar date that is already PAST must come back as past, so
        # the caller can say so. Left to the lenient passes below, "5 janvier 2025
        # 10h00" lost its year and its time and became 5 January NEXT year at 09:00 —
        # a reminder silently moved two years from what was typed.
        past = find_when(text, now.replace(year=now.year - 50))
        if past is not None and names_a_calendar_date(words[past[1] : past[2]]):
            when, start, end = past
            what = _clean_text(words[:start] + words[end:])
            if when <= now and what:
                return when, what

    bare = [w.strip(_EDGE_PUNCT) for w in words]
    for size in range(min(_MAX_PHRASE_WORDS, len(words) - 1), 0, -1):
        when = _try_phrase(" ".join(w for w in bare[:size] if w), now)
        if when is not None:
            what = _clean_text(words[size:])
            if what:
                return when, what

    title, due = split_due(text, now)
    if due is not None:
        what = _clean_text(title.split())
        if what:
            return due, what
    return None


# ── recurrence ───────────────────────────────────────────────────────────────

_EVERY_UNIT: dict[str, str] = {
    "day": "daily", "days": "daily", "daily": "daily",
    "week": "weekly", "weeks": "weekly", "weekly": "weekly",
    "month": "monthly", "months": "monthly", "monthly": "monthly",
    "year": "yearly", "years": "yearly", "yearly": "yearly",
    "morning": "daily", "night": "daily",
}


def split_recurrence(text: str) -> tuple[str, str | None]:
    """Pull ``every day`` / ``every monday`` / ``weekly`` off a line.

    Returns (remaining text, recurrence or None). A weekday (``every monday``) becomes
    ``weekly`` — the DUE DATE carries which day it is, so storing the weekday twice
    would be two facts that can disagree.
    """
    words = _words(text)
    if not words:
        return _norm(text), None

    lowered = [w.lower().strip(",") for w in words]

    for size in (2, 1):
        if len(words) < size:
            continue
        for start in range(len(words) - size + 1):
            chunk = lowered[start : start + size]
            recur = _recurrence_of(chunk)
            if recur is None:
                continue
            remaining = words[:start] + words[start + size :]
            return " ".join(remaining).strip(" ,-–—"), recur
    return " ".join(words), None


def _recurrence_of(chunk: list[str]) -> str | None:
    if len(chunk) == 1:
        return _EVERY_UNIT.get(chunk[0]) if chunk[0] in ("daily", "weekly", "monthly", "yearly") else None
    if len(chunk) == 2 and chunk[0] == "every":
        if chunk[1] in _WEEKDAYS:
            return "weekly"
        return _EVERY_UNIT.get(chunk[1])
    return None


def advance(when: datetime, recur: str) -> datetime:
    """Move a due date on by one period.

    Raises :class:`RecurrenceError` for an unknown token rather than returning the
    input: a recurrence that silently does not advance produces a task that is due
    forever and re-fires its reminder on every poll.
    """
    if recur == "daily":
        return when + timedelta(days=1)
    if recur == "weekly":
        return when + timedelta(days=7)
    if recur == "monthly":
        return _add_months(when, 1)
    if recur == "yearly":
        return _add_months(when, 12)
    raise RecurrenceError(f"unknown recurrence {recur!r}; expected one of {RECURRENCES}")


# ── lead times ───────────────────────────────────────────────────────────────

_LEAD_RE = re.compile(r"^(?P<n>\d+)(?P<unit>[mhdw])$", re.IGNORECASE)


def parse_lead(token: str) -> timedelta | None:
    """``3d`` → 3 days. Returns None for anything not in the ``<n><m|h|d|w>`` shape."""
    m = _LEAD_RE.match(token.strip())
    if not m:
        return None
    return timedelta(seconds=int(m.group("n")) * _UNIT_SECONDS[m.group("unit").lower()])


def describe_lead(token: str) -> str:
    """``3d`` → "3 days before". Falls back to the raw token so an unknown lead is
    still readable rather than blank.

    The DETAIL card has room for words, so this one is spelled out rather than using
    the compact units the row countdown uses.
    """
    delta = parse_lead(token)
    if delta is None:
        return token
    amount = _plain_delta(delta)
    return _t("pim.lead.before", amount=amount) or f"{amount} before"


def _plain_delta(delta: timedelta) -> str:
    """"3 days" / "1 hour", in the operator's language.

    English is the only locale here that needs a singular form — the Russian and
    French abbreviations read correctly at every count — so the `_one` keys exist
    rather than a plural framework nothing else would use.
    """
    seconds = int(abs(delta).total_seconds())
    for unit, size, label in (
        ("w", 604800, "week"),
        ("d", 86400, "day"),
        ("h", 3600, "hour"),
        ("m", 60, "minute"),
    ):
        if seconds < size:
            continue
        count = seconds // size
        key = f"pim.lead.{unit}_one" if count == 1 else f"pim.lead.{unit}"
        return _t(key, n=count) or f"{count} {label}{'s' if count != 1 else ''}"
    return "moments"


# ── rendering ────────────────────────────────────────────────────────────────

def humanize_delta(when: datetime, now: datetime) -> str:
    """The countdown: ``in 3h`` · ``2d ago`` · ``now``.

    Compact on purpose — it sits at the end of a task row on a phone, where a full
    sentence pushes the title off the screen.
    """
    seconds = int((when - now).total_seconds())
    if -60 < seconds < 60:
        return _t("pim.delta.now") or "now"
    ahead = seconds > 0
    seconds = abs(seconds)
    for size, unit in ((604800, "w"), (86400, "d"), (3600, "h"), (60, "m")):
        if seconds < size:
            continue
        count = seconds // size
        # SHORT units in every language: Russian inflects a counted noun three ways
        # (1 день · 2 дня · 5 дней) and a task row has no room for a plural rule.
        amount = _t(f"pim.unit.{unit}", n=count) or f"{count}{unit}"
        key = "pim.delta.in" if ahead else "pim.delta.ago"
        rendered = _t(key, amount=amount)
        if rendered:
            return rendered
        return f"in {amount}" if ahead else f"{amount} ago"
    return _t("pim.delta.now") or "now"


def format_local(when: datetime, now: datetime) -> str:
    """A date the way a person reads it, with the year only when it is not this one.

    ``Fri 5 Sep · 19:00`` · ``Mon 3 Feb 2027 · 09:00``. Midnight prints without a
    time, because a task due "on Friday" should not claim to be due at 00:00.
    """
    # Built from the locale files rather than strftime: `%a` and `%b` are the C
    # locale's names, so this rendered "Fri 11 Sep" inside an otherwise-Russian card.
    day = f"{_localized_weekday(when.weekday())} {when.day} {_localized_month(when.month, short=True)}"
    if when.year != now.year:
        day = f"{day} {when.year}"
    if when.hour == 0 and when.minute == 0:
        return day
    return f"{day} · {when:%H:%M}"

