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
}

_MONTHS: dict[str, int] = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

_UNIT_SECONDS: dict[str, int] = {
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "week": 604800, "weeks": 604800,
}

# The longest date phrase worth considering, in words: "next tuesday at half past" is
# already beyond this grammar, and a larger window only costs false positives.
_MAX_PHRASE_WORDS = 5

# Words that may sit in front of a phrase without changing it. They are stripped from
# the CANDIDATE, not from the title, so "Pay the bill on friday" keeps "Pay the bill".
_LEADERS = frozenset({"at", "on", "by", "@"})

# Default clock time when a day is named without one. 9am is the "sometime that day"
# convention; a task due "friday" that fires at 00:00 is a task you see on Thursday
# night and forget by morning.
_DEFAULT_HOUR = 9
_DEFAULT_MINUTE = 0


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _words(text: str) -> list[str]:
    return _norm(text).split(" ") if _norm(text) else []


def _at(base: datetime, *, hour: int, minute: int) -> datetime:
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


# ── time-of-day ──────────────────────────────────────────────────────────────

_TIME_RE = re.compile(
    r"^(?P<h>\d{1,2})"
    r"(?:[:.](?P<m>\d{2}))?"
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
    if not mer and m.group("m") is None:
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

    if joined in ("today", "tonight"):
        return _at(now, hour=0, minute=0), True
    if joined == "tomorrow":
        return _at(now + timedelta(days=1), hour=0, minute=0), True
    if joined in ("day after tomorrow", "overmorrow"):
        return _at(now + timedelta(days=2), hour=0, minute=0), True
    if joined == "yesterday":
        return None  # a due date in the past is never what was meant

    # "in 3 days" / "in 2 weeks" / "in 90m"
    if lowered[0] == "in" and len(lowered) in (2, 3):
        qty_token = lowered[1]
        unit_token = lowered[2] if len(lowered) == 3 else ""
        if not unit_token:
            m = re.match(r"^(\d+)([a-z]+)$", qty_token)
            if not m:
                return None
            qty_token, unit_token = m.group(1), m.group(2)
        if not qty_token.isdigit():
            return None
        seconds = _UNIT_SECONDS.get(unit_token)
        if seconds is None:
            return None
        return now + timedelta(seconds=int(qty_token) * seconds), True

    # "next monday" / "this friday"
    if lowered[0] in ("next", "this") and len(lowered) == 2:
        if lowered[1] in _WEEKDAYS:
            return (
                _at(
                    _next_weekday(
                        now, _WEEKDAYS[lowered[1]], force_next_week=lowered[0] == "next"
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
        when = _try_phrase(" ".join(words[-size:]), now)
        if when is not None:
            title = " ".join(words[:-size]).rstrip(" ,-–—")
            if title:
                return title, when
    return " ".join(words), None


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
    still readable rather than blank."""
    delta = parse_lead(token)
    if delta is None:
        return token
    return f"{_plain_delta(delta)} before"


def _plain_delta(delta: timedelta) -> str:
    seconds = int(abs(delta).total_seconds())
    for unit, size, label in (
        ("w", 604800, "week"),
        ("d", 86400, "day"),
        ("h", 3600, "hour"),
        ("m", 60, "minute"),
    ):
        if seconds >= size:
            count = seconds // size
            return f"{count} {label}{'s' if count != 1 else ''}"
    return "moments"


# ── rendering ────────────────────────────────────────────────────────────────

def humanize_delta(when: datetime, now: datetime) -> str:
    """The countdown: ``in 3h`` · ``2d ago`` · ``now``.

    Compact on purpose — it sits at the end of a task row on a phone, where a full
    sentence pushes the title off the screen.
    """
    seconds = int((when - now).total_seconds())
    if -60 < seconds < 60:
        return "now"
    ahead = seconds > 0
    seconds = abs(seconds)
    for size, suffix in ((604800, "w"), (86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size:
            count = seconds // size
            return f"in {count}{suffix}" if ahead else f"{count}{suffix} ago"
    return "now"


def format_local(when: datetime, now: datetime) -> str:
    """A date the way a person reads it, with the year only when it is not this one.

    ``Fri 5 Sep · 19:00`` · ``Mon 3 Feb 2027 · 09:00``. Midnight prints without a
    time, because a task due "on Friday" should not claim to be due at 00:00.
    """
    day = when.strftime("%a %-d %b") if _supports_dash(when) else when.strftime("%a %d %b").replace(" 0", " ")
    if when.year != now.year:
        day = f"{day} {when.year}"
    if when.hour == 0 and when.minute == 0:
        return day
    return f"{day} · {when:%H:%M}"


def _supports_dash(when: datetime) -> bool:
    """``%-d`` is glibc; Windows uses ``%#d`` and raises on ``%-d``.

    Detected rather than assumed: this runs on the operator's Windows box and on a
    Linux server, and a ValueError here would take down the whole card render.
    """
    try:
        when.strftime("%-d")
    except ValueError:
        return False
    return True
