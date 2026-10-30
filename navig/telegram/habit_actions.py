"""
Telegram check-in card — one tap per habit, no typing.

The evening check-in only survives if it costs seconds. Typing
``navig habit log wake`` on a phone at 22:15 does not qualify; a card with
buttons does. Tapping toggles the row in the space's habits.csv and edits the
card in place, so the message itself is always the current state of the day.

Card is SENT by ``navig habit checkin --send`` (CLI, knows the space) and its
taps are handled here, inside the gateway. See
``habit_tracker.remember_target`` for how the two processes agree on which
habits.csv a card belongs to.

Callback data (Telegram caps it at 64 bytes):
    hb:t:<label>:<yyyymmdd>[:m]   toggle a habit (``:m`` = tapped on the morning card)
    hb:s:<score>:<yyyymmdd>[:m]   set the day's score
    hb:x:<yyyymmdd>           close the day
"""

from __future__ import annotations

import html
import logging
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from navig.spaces import habit_tracker
from navig.telegram import card_nav
from navig.telegram.card_doc import Card, Heading, Para, PullQuote, Raw, Table, Tasks, esc

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "hb:"
#: Appended to a toggle/score callback from the MORNING card, so a tap keeps it one.
MORNING_SUFFIX = ":m"


def extension_is_off() -> bool:
    """True when the Habits extension is switched off. Never raises."""
    try:
        from navig.gateway.channels.telegram_extensions import is_enabled

        return not is_enabled("habits")
    except Exception:  # noqa: BLE001
        return False


def extension_banner(*, as_html: bool = True) -> str:
    """The one-line warning shown above ANY surface that reports habit state.

    Suppressing delivery leaves the schedule intact, so ``cron_jobs.json`` still
    reads ``enabled: true`` while nothing arrives. Left unexplained that is the
    "is it me or is it broken" ambiguity that gets a bot muted at the OS. Every
    surface that lists reminders renders this ABOVE its own content, so the
    extension state always outranks the per-habit state.

    Returns "" when the extension is on, so a caller can prepend unconditionally.
    """
    if not extension_is_off():
        return ""
    if as_html:
        return _t("habit.ext.off_html")
    return "\n".join(extension_banner_cli() or ())


def extension_banner_cli() -> tuple[str, str] | None:
    """The same warning as ``(message, detail)`` for ``ch.warning``.

    A tuple rather than one string with a separator: the CLI renders the two
    halves differently, and splitting a formatted string back apart on
    whitespace is the kind of thing that breaks silently on a reflow.
    """
    if not extension_is_off():
        return None
    return (_t("habit.ext.off_cli"), _t("habit.ext.off_cli_detail"))

#: Button state markers. An empty checkbox plus an icon read as "white square and
#: icon" — no word, no state, nothing to act on. A word and an unmistakable mark
#: are what make a button answerable at 22:15 without thinking.
MARK_DONE = "✓"
MARK_OPEN = ""

#: Toggle rows: (tracker label, button word). First row is the floor.
#: The tracker label stays as it is on disk — only the button reads in English.
CHECKIN_ROWS: list[list[tuple[str, str]]] = [
    [("wake", "Wake"), ("out", "Walk"), ("ship", "Ship")],
    [("train", "Train"), ("caffeine_cutoff", "No coffee"), ("glow", "Self-care")],
]

#: A score you pick by feeling, not by arithmetic — one tap, no keyboard.
SCORE_FACES: list[tuple[str, str]] = [
    ("2", "😞"),
    ("4", "😐"),
    ("6", "🙂"),
    ("8", "😀"),
    ("10", "🔥"),
]

#: Tracker label → the word a human reads. Labels stay machine-stable on disk.
#: Only six of these reach a button; the rest exist so a card never prints a raw
#: label like ``caffeine_cutoff`` or ``outside_work`` at someone.
LABEL_WORDS: dict[str, str] = {
    **{label: word for row in CHECKIN_ROWS for label, word in row},
    "deep": "Deep work",
    "steps": "Steps",
    "eat_window": "Eating window",
    "protein": "Protein",
    "friend": "Friend",
    "mom": "Mother",
    "outside_work": "Worked outside",
    "screens_off": "Screens off",
    "sleep_time": "Bedtime",
    "nightclose": "Night closed",
    "mobility": "Mobility",
    "score": "Day score",
    # Reminder-job keys. They are not tracker labels, but the pause menu lists
    # jobs — and a menu row reading `ship_weekend` is the raw-label defect again,
    # just on a different screen.
    "checkin": "Evening card",
    "close": "Day close",
    "review": "Weekly review",
    "people": "People",
    "out_weekend": "Weekend walk",
    "ship_weekend": "Weekend block",
}

#: Grid marks for the stats tables — TEXT, not emoji.
#:
#: ✅/❌ are emoji: double-width, rendered oversized, and they wrap. In a table
#: they guarantee ragged columns no matter how carefully the row is padded. These
#: two are ordinary characters, so a monospace block lines up exactly. The cards
#: keep the emoji, where a big obvious mark is the point and there is no grid.
MARK_ON = "✓"
MARK_OFF = "·"


def _pre(rows: list[str]) -> str:
    """Wrap table rows in Telegram's only alignment primitive.

    Telegram has NO table markup — the Bot API's HTML subset is
    b/i/u/s/code/pre/a/blockquote and nothing else. ``<pre>`` renders monospace,
    which is what makes columns hold; outside it a proportional font drifts every
    row out of line. Rows are escaped: a habit word is owner-supplied text.
    """
    body = "\n".join(html.escape(r) for r in rows)
    return f"<pre>{body}</pre>"


#: What the three actually are, in words that need no glossary.
#:
#: They used to be called "non-negotiables" on the card. That word reported a
#: score — "2/3 non-negotiables" — and named neither what the three were nor
#: which one was missed, so the banner was unreadable to the only person who
#: reads it. Every surface now spells the three out with their marks.
FLOOR_TITLE = "The 3 that decide the day"


def _t(key: str, **fields: object) -> str:
    """A shared-locale string. Separate from this module's own ``t`` (body/habit
    strings live in different roots) but resolved against the same language."""
    from navig.core import i18n  # noqa: PLC0415

    return i18n.t(key, **fields)


def _floor_title() -> str:
    """FLOOR_TITLE, localized. The constant stays as the English fallback and is
    still exported — other modules import it."""
    return _t("habit.card.floor_title") if _t(
        "habit.card.floor_title"
    ) != "habit.card.floor_title" else FLOOR_TITLE



_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def _compact(day: str) -> str:
    """2026-08-10 -> 20260810 (callback data is byte-budgeted)."""
    return day.replace("-", "")


def _expand(compact: str) -> str:
    return f"{compact[0:4]}-{compact[4:6]}-{compact[6:8]}"


def _month_name(month: int) -> str:
    """The month, in the operator's language. `_MONTHS` is the English fallback."""
    try:
        from navig.core import i18n  # noqa: PLC0415

        key = f"month.{month}"
        translated = i18n.t(key)
        return _MONTHS[month - 1] if translated == key else translated
    except Exception:  # noqa: BLE001
        return _MONTHS[month - 1]


def _weekday_name(index: int) -> str:
    """Mon/Tue/… in the operator's language. `_WEEKDAY_NAMES` is the fallback."""
    try:
        from navig.core import i18n  # noqa: PLC0415

        key = f"weekday.{index}"
        translated = i18n.t(key)
        return _WEEKDAY_NAMES[index] if translated == key else translated
    except Exception:  # noqa: BLE001
        return _WEEKDAY_NAMES[index]


def _col_width(header: str, cells: Iterable[str]) -> int:
    """How wide a `<pre>` column has to be: the widest thing in it, plus a space.

    The +1 is the separator. Without it a header exactly as wide as its column
    touches the next one — which is how the translated totals header rendered as
    `готовосейчасрекорд` while every data row underneath stayed correctly aligned.
    """
    return max([len(header), *(len(c) for c in cells)]) + 1


def _range_label(days: int) -> str:
    """The stats range buttons. 0 means the whole cycle."""
    return _t("habit.stats.range_all") if days <= 0 else _t("habit.stats.range_days", n=days)


def _human_day(day: str) -> str:
    try:
        d = date.fromisoformat(day)
        return f"{d.day} {_month_name(d.month)}"
    except (ValueError, IndexError):
        return day


def word(label: str) -> str:
    """The human word for a tracker label, in the operator's global language.

    Every card, button, stats grid and pause-menu row renders labels through
    here, so localizing this one function localizes all of them. LABEL_WORDS
    stays as the English fallback: a label with no locale entry reads as an
    English word rather than as a raw `caffeine_cutoff`.
    """
    fallback = LABEL_WORDS.get(label, label)
    try:
        from navig.core import i18n  # noqa: PLC0415

        key = f"habit.label.{label}"
        translated = i18n.t(key)
        return fallback if translated == key else translated
    except Exception:  # noqa: BLE001 — a label is never worth an exception
        return fallback


_word = word  # internal shorthand


def floor_line(logged: dict[str, str]) -> str:
    """``✅ Wake · ❌ Walk · ✅ Ship`` — the three, named, with their state.

    One line, one grammar, on every card: morning, evening and closing. A count
    without names is what made the closing banner unanswerable.
    """
    return " · ".join(
        f"{MARK_DONE if habit_tracker.is_done(logged.get(h, '')) else MARK_OPEN} {_word(h)}"
        for h in habit_tracker.NON_NEGOTIABLES
    )


def _all_labels() -> list[str]:
    """Every tappable habit, in button order: the floor first, then the rest."""
    return [label for row in CHECKIN_ROWS for label, _english in row]


def _checkin_keyboard(
    logged: dict[str, str], day: str, morning: bool
) -> list[list[dict[str, str]]]:
    """The tap rows: one per non-negotiable pair, the faces, then close.

    Identical in both renders — the buttons ARE the card's input surface. The frame
    rides in the callback (`:m`), because a tap re-renders the card and the handler
    has nothing else to tell a morning card from an evening one.
    """
    stamp = _compact(day)
    frame = MORNING_SUFFIX if morning else ""

    def done(label: str) -> bool:
        return habit_tracker.is_done(logged.get(label, ""))

    score = logged.get("score", "")
    keyboard: list[list[dict[str, str]]] = []
    for row in CHECKIN_ROWS:
        keyboard.append(
            [
                {
                    # `_word(label)`, NOT the tuple's second element: that is the
                    # English word baked into CHECKIN_ROWS, and binding it to the
                    # name `word` also shadowed the word() function right here —
                    # which is why the card text localized and the buttons under
                    # it stayed in English.
                    "text": f"{MARK_DONE + ' ' if done(label) else ''}{_word(label)}",
                    "callback_data": f"{CALLBACK_PREFIX}t:{label}:{stamp}{frame}",
                    # Colour carries the state on a client that renders button
                    # styles; the mark still says it on one that does not.
                    **({"style": "success"} if done(label) else {}),
                }
                for label, _english in row
            ]
        )
    keyboard.append(
        [
            {
                "text": f"{'▶' if score == value else ''}{face}",
                "callback_data": f"{CALLBACK_PREFIX}s:{value}:{stamp}{frame}",
                **({"style": "primary"} if score == value and score else {}),
            }
            for value, face in SCORE_FACES
        ]
    )
    keyboard.append(
        [{
            "text": _t("habit.card.close_button"),
            "callback_data": f"{CALLBACK_PREFIX}x:{stamp}",
        }]
    )
    return keyboard


def checkin_card(path: Path, day: str, morning: bool = False) -> tuple[Card, dict[str, Any]]:
    """The check-in card for *day*, as one definition both renders read.

    ``morning=True`` frames the same card as the start of the day. The buttons are
    identical on purpose: waking up is the one thing that can only be marked at the
    moment it happens, and by 22:15 it is a memory. One card in the morning and one
    in the evening beats a single card that scrolls out of reach by lunchtime.

    The three non-negotiables are a task list in the rich render: a checkbox says
    "done" or "open" at a glance, and the row under it is still the thing that
    changes it (a rich checkbox is state, not input).
    """
    logged = habit_tracker.day_map(path, day)
    rows = habit_tracker.read_tracker(path)

    def done(label: str) -> bool:
        return habit_tracker.is_done(logged.get(label, ""))

    open_floor = [_word(h) for h in habit_tracker.NON_NEGOTIABLES if not done(h)]

    floor = list(habit_tracker.NON_NEGOTIABLES)
    extras = [h for h in _all_labels() if h not in floor]
    done_floor = sum(1 for h in floor if done(h))

    blocks: list[Any] = []
    if morning:
        blocks.append(Para(_t("habit.card.morning_line")))
        # The locale string carries <b>{wake}</b> for the classic parse_mode, so it
        # rides as Raw with the word escaped — a plain Para would print the tags.
        blocks.append(
            Para(Raw(_t("habit.card.morning_tap", wake=esc(word("wake")))), style="italic")
        )

    # The floor is what the day is judged on, so it is named as "required" rather than
    # as "the ones that decide the day" — the other three are tappable habits too, and
    # a card that hides them answers a question nobody asked.
    blocks.append(Heading(_t("habit.card.floor_title")))
    blocks.append(Tasks([(_word(h), done(h)) for h in floor]))
    if extras:
        blocks.append(Heading(_t("habit.card.extras_title")))
        blocks.append(Tasks([(_word(h), done(h)) for h in extras]))

    if not morning:
        open_floor = [_word(h) for h in floor if not done(h)]
        if open_floor:
            blocks.append(
                Para(Raw(_t("habit.card.still_open", names=esc(", ".join(open_floor)))))
            )
        else:
            blocks.append(Para(Raw(f"<b>{esc(_t('habit.card.all_three'))}</b>")))

    try:
        today = date.fromisoformat(day)
        streaks = " · ".join(
            f"{_word(h)} " + _t("habit.card.streak_day", n=habit_tracker.streak_for(rows, h, today))
            for h in floor
        )
        blocks.append(Para(_t("habit.card.streaks", value=streaks), style="italic"))
    except ValueError:
        pass

    score = logged.get("score", "")
    if score and habit_tracker.is_done(score):
        blocks.append(Para(_t("habit.card.day_score", value=score), style="italic"))

    meta = f"{_human_day(day)} · {done_floor}/{len(floor)}"

    title = _t("habit.card.morning_title") if morning else _t("habit.card.evening_title")
    card = Card(
        title,
        glyph="☀️" if morning else "✍️",
        meta=meta,
        blocks=blocks,
    )
    return card, {"inline_keyboard": _checkin_keyboard(logged, day, morning)}


def build_card(path: Path, day: str, morning: bool = False) -> tuple[str, dict[str, Any]]:
    """The plain render of :func:`checkin_card` — what a message without rich support gets."""
    card, keyboard = checkin_card(path, day, morning)
    return card.classic_html(), keyboard


def build_card_rich(path: Path, day: str, morning: bool = False) -> tuple[str, dict[str, Any]]:
    """The rich render: a task list instead of a dot-joined line."""
    card, keyboard = checkin_card(path, day, morning)
    return card.rich_html(), keyboard


def closing_card(path: Path, day: str) -> Card:
    """The day once it is closed: what happened, and what to do tomorrow.

    Names every habit, split the same way the check-in splits them, because the
    closing message is where the operator compares the day to the intention. The
    "never miss twice" line is a pull quote: it is the one sentence worth reading
    twice, and it was buried in body text before.
    """
    logged = habit_tracker.day_map(path, day)
    floor = list(habit_tracker.NON_NEGOTIABLES)
    known = set(_all_labels())
    extras = [h for h in _all_labels() if h not in floor]
    # A label written by an older build (or a hand edit) still gets a row instead of
    # vanishing from the closing summary.
    extras += sorted(k for k in logged if k not in known and k != "score")

    def done(label: str) -> bool:
        return habit_tracker.is_done(logged.get(label, ""))

    missed = [_word(h) for h in floor if not done(h)]
    names = esc(", ".join(missed))

    blocks: list[Any] = [
        Heading(_t("habit.card.floor_title")),
        Tasks([(_word(h), done(h)) for h in floor]),
    ]
    if extras:
        blocks += [
            Heading(_t("habit.card.extras_title")),
            Tasks([(_word(h), done(h)) for h in extras]),
        ]

    if missed:
        blocks.append(Para(Raw(f"<b>{_t('habit.card.missed', names=names)}</b>")))
        blocks.append(PullQuote(Raw(_t("habit.card.never_miss_twice", names=names))))
    else:
        blocks.append(Para(Raw(f"<b>{esc(_t('habit.card.all_three'))}</b>")))

    score = logged.get("score", "")
    if score and habit_tracker.is_done(score):
        blocks.append(Para(_t("habit.card.day_score", value=score), style="italic"))

    return Card(
        _t("habit.card.closed", day=_human_day(day)),
        glyph="🌙",
        meta="",
        blocks=blocks,
        footer=_t("habit.card.journal_nudge"),
    )


def build_closing_text(path: Path, day: str) -> str:
    """The plain render of :func:`closing_card` — buttons already removed."""
    return closing_card(path, day).classic_html()


def build_closing_rich(path: Path, day: str) -> str:
    """The rich render: checklist, pull quote, footer."""
    return closing_card(path, day).rich_html()


# ── /stats — the cycle, on the phone ──────────────────────────────────────────
#
# The numbers only change behaviour if they are readable where the behaviour
# happens. `navig habit stats` in a terminal is a report you have to go and fetch;
# the same thing in the chat that already interrupts you is one you actually read.

#: Ranges the stats panel offers. 0 = the whole cycle.
STATS_RANGES: list[tuple[int, str]] = [(7, "7 days"), (14, "14 days"), (0, "All")]

_WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


#: Telegram's ceiling for one message's text, counted in UTF-16 code units. The raw
#: HTML is measured, which over-counts the tags — erring on the side that fits.
STATS_TEXT_LIMIT = 4096


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def build_stats(path: Path, today: date, days: int = 7) -> tuple[str, dict[str, Any]]:
    """The whole cycle as one message: totals, day-of-week, and a day list.

    ``days=0`` shows every day since the first row. The day-of-week block is the
    part worth the screen space — a total says "some days were missed", a weekday
    column says which day of the week you lose and therefore what to change.
    """
    rows = habit_tracker.read_tracker(path)
    # Empty is judged on the days that PARSE: a hand-edited file whose every date is
    # malformed has rows but no cycle, and summarize() then has no start to show.
    s = habit_tracker.summarize(rows, today) if rows else None
    if not s or not s["start"]:
        return (
            f"📊 <b>{_t('habit.stats.title')}</b>\n\n{_t('habit.stats.empty')}",
            {"inline_keyboard": []},
        )

    pct = round(100 * s["complete"] / s["days"]) if s["days"] else 0

    lines = [
        f"📊 <b>{_t('habit.stats.title')}</b> · {_t('habit.stats.day_n', n=s['days'])}"
        f"  <i>{_human_day(s['start'].isoformat())} → {_human_day(s['end'].isoformat())}</i>",
        "",
        f"<b>{html.escape(_t('habit.card.days_counted', done=s['complete'], total=s['days']))}</b>"
        f"  ({pct}%)",
        "",
    ]

    # Table 1 — the three. `<pre>` is the only alignment Telegram has: it has no
    # table markup at all, and outside a monospace block a proportional font makes
    # every column drift. The marks are TEXT (✓ ·), never emoji: an emoji is
    # double-width and rendered oversized, which is what turned the previous
    # version into a ragged wall.
    col_done, col_now, col_best = (
        _t("habit.stats.col_done"),
        _t("habit.stats.col_now"),
        _t("habit.stats.col_best"),
    )
    # `<pre>` is the only alignment Telegram has, so every width is measured from
    # the strings that actually go in the column — header AND cells — plus one for
    # the separator. Numbers chosen against English words collapse the moment a
    # translation is as long as its column: "готовосейчасрекорд".
    cells = [
        (
            _word(label),
            f"{s['habits'][label]['done']}/{s['days']}",
            _t("habit.card.streak_day", n=s["habits"][label]["streak"]),
            _t("habit.card.streak_day", n=s["habits"][label]["best"]),
        )
        for label in habit_tracker.NON_NEGOTIABLES
    ]
    name_w = max(len(row[0]) for row in cells)
    done_w = _col_width(col_done, (row[1] for row in cells))
    now_w = _col_width(col_now, (row[2] for row in cells))
    best_w = _col_width(col_best, (row[3] for row in cells))
    table = [f"{'':<{name_w}}{col_done:>{done_w}}{col_now:>{now_w}}{col_best:>{best_w}}"]
    for name, done, now, best in cells:
        table.append(
            f"{name:<{name_w}}{done:>{done_w}}{now:>{now_w}}{best:>{best_w}}"
        )
    lines += [_pre(table), ""]

    # Table 2 — the weekday column, the part actually worth reading. A total says
    # "some days were missed"; this says which day of the week you lose.
    col_counted = _t("habit.stats.col_counted")
    day_names = [_weekday_name(i) for i in range(7)]
    present = [
        (day_names[i], s["by_weekday"].get(i, {"days": 0, "complete": 0}))
        for i in range(7)
        if s["by_weekday"].get(i, {"days": 0})["days"]
    ]
    weekday_w = max((len(name) for name, _ in present), default=3)
    counted_w = _col_width(
        col_counted, (f"{b['complete']}/{b['days']}" for _, b in present)
    )
    week = [f"{'':<{weekday_w}}{col_counted:>{counted_w}}"]
    for name, bucket in present:
        hit, total = bucket["complete"], bucket["days"]
        bar = MARK_ON * hit + MARK_OFF * (total - hit)
        week.append(f"{name:<{weekday_w}}{f'{hit}/{total}':>{counted_w}}  {bar}")
    lines += [f"<b>{_t('habit.stats.by_weekday')}</b>", _pre(week), ""]

    # Table 3 — the day list, blanks included. A day with no row at all is the
    # failure a list of only-logged-days renders invisible.
    start = s["start"] if days <= 0 else max(s["start"], today - timedelta(days=days - 1))
    # Full words in the header, not truncations: "Wake" and "Walk" both cut to "Wa".
    # The month goes in its own separator row instead of a column on every line —
    # it changes twice a cycle and costs four characters of width every day.
    # One width per habit column, sized to its own label: a single shared width
    # would be set by the longest word and waste that many characters on every
    # other column, on a screen that has none to spare.
    habit_w = {h: len(_word(h)) + 1 for h in habit_tracker.NON_NEGOTIABLES}
    stamp_w = max(len(f"{_weekday_name(i)} 00") for i in range(7)) + 1
    head = "".join(f"{_word(h):>{habit_w[h]}}" for h in habit_tracker.NON_NEGOTIABLES)
    while True:
        grid = [f"{'':<{stamp_w}}{head}"]
        month: int | None = None
        blanks = full = 0
        for day, marks in habit_tracker.day_rows(rows, start, today):
            if day.month != month:
                month = day.month
                grid.append(_month_name(month))
            cells = "".join(
                f"{MARK_ON if marks[h] else MARK_OFF:>{habit_w[h]}}"
                for h in habit_tracker.NON_NEGOTIABLES
            )
            hit = sum(marks.values())
            tail = ""
            if hit == 0:
                tail, blanks = "  ←", blanks + 1
            elif hit == 3:
                tail, full = "  ★", full + 1
            stamp = f"{_weekday_name(day.weekday())} {day.day:02d}"
            grid.append(f"{stamp:<{stamp_w}}{cells}{tail}")
        tail_lines = [f"<b>{_t('habit.stats.day_by_day')}</b>", _pre(grid)]
        omitted = (start - s["start"]).days
        if omitted > 0:
            tail_lines.append(f"<i>{html.escape(_t('habit.stats.earlier_omitted', n=omitted))}</i>")

        # A bare symbol is a symbol you have to ask about — the same defect as a
        # button reading "Groom". The legend only appears when the symbol does.
        legend = []
        if full:
            legend.append(_t("habit.stats.legend_all"))
        if blanks:
            legend.append(_t("habit.stats.legend_none"))
        if legend:
            tail_lines.append(f"<i>{' · '.join(legend)}</i>")

        # "All" grows one line a day, and the panel is sent as ONE editMessageText —
        # past Telegram's limit the edit is refused outright. Keep the most recent
        # days that fit and say how many earlier ones were left out.
        over = _utf16_len("\n".join(lines + tail_lines)) - STATS_TEXT_LIMIT
        if over <= 0 or start >= today:
            break
        per_day = max(1, _utf16_len(grid[-1]) + 1)
        start = min(today, start + timedelta(days=max(1, -(-over // per_day))))
    lines += tail_lines

    keyboard = [
        [
            {
                "text": f"{'▶ ' if n == days else ''}{_range_label(n)}",
                "callback_data": f"{CALLBACK_PREFIX}st:{n}",
            }
            for n, _ in STATS_RANGES
        ]
    ]
    return "\n".join(lines), {"inline_keyboard": keyboard}


def stats_card(path: Path, today: date, days: int = 7) -> tuple[Card, dict[str, Any]]:
    """The cycle as a rich card: two real tables instead of three `<pre>` blocks.

    The plain render still needs monospace (Telegram has no table markup there), but a
    rich message has one, with borders, a header row and cells that reflow on a phone.
    The data is the same `habit_tracker.summarize`; only the presentation changes.

    NOTE: `build_stats` below still computes its own rows. The shapes drifted apart once
    already — when the next edit touches either, fold both onto this one definition.
    """
    rows = habit_tracker.read_tracker(path)
    keys = [
        {"text": f"{'▶ ' if n == days else ''}{_range_label(n)}",
         **({"style": "primary"} if n == days else {}),
         "callback_data": f"{CALLBACK_PREFIX}st:{n}"}
        for n, _ in STATS_RANGES
    ]
    keyboard = {"inline_keyboard": [keys]}

    if not rows:
        return Card(_t("habit.stats.title"), glyph="📊", meta=_range_label(days),
                    blocks=[Para(_t("habit.stats.empty"), style="italic")]), keyboard

    s = habit_tracker.summarize(rows, today)
    pct = round(100 * s["complete"] / s["days"]) if s["days"] else 0

    by_habit = Table(
        header=[_t("habit.stats.col_habit"), _t("habit.stats.col_done"),
                _t("habit.stats.col_now"), _t("habit.stats.col_best")],
        rows=[
            [
                Raw(f"<b>{esc(_word(label))}</b>"),
                f"{s['habits'][label]['done']}/{s['days']}",
                _t("habit.card.streak_day", n=s["habits"][label]["streak"]),
                _t("habit.card.streak_day", n=s["habits"][label]["best"]),
            ]
            for label in habit_tracker.NON_NEGOTIABLES
        ],
        striped=True,
    )

    weekday_rows = []
    for i in range(7):
        bucket = s["by_weekday"].get(i, {"days": 0})
        if not bucket["days"]:
            continue
        hit, total = bucket["complete"], bucket["days"]
        weekday_rows.append([
            _weekday_name(i),
            f"{hit}/{total}",
            Raw(f"{MARK_ON * hit}{MARK_OFF * (total - hit)}"),
        ])
    by_weekday = Table(
        header=[_t("habit.stats.col_day"), _t("habit.stats.col_counted"), ""],
        rows=weekday_rows,
    )

    blocks: list[Any] = [
        Para(Raw(
            f"<b>{esc(_t('habit.card.days_counted', done=s['complete'], total=s['days']))}</b>"
            f" · {pct}%"
        )),
        Heading(_t("habit.stats.by_habit")),
        by_habit,
        Heading(_t("habit.stats.by_weekday")),
        by_weekday,
    ]

    return Card(
        _t("habit.stats.title"),
        glyph="📊",
        meta=f"{_range_label(days)} · {_human_day(s['start'].isoformat())} → {_human_day(s['end'].isoformat())}",
        blocks=blocks,
    ), keyboard


def build_stats_rich(path: Path, today: date, days: int = 7) -> tuple[str, dict[str, Any]]:
    card, keyboard = stats_card(path, today, days)
    return card.rich_html(), keyboard


# ── /pause — switching the reminders off, from the phone ──────────────────────


def _habit_jobs() -> list[dict]:
    from navig.scheduler import habit_store  # noqa: PLC0415 — gateway-only import

    return sorted(habit_store.list_habit_jobs(), key=lambda j: str(j.get("name", "")))


def _job_key(job: dict) -> str:
    from navig.scheduler.habit_store import HABIT_NAME_PREFIX  # noqa: PLC0415

    return str(job.get("name", "")).removeprefix(HABIT_NAME_PREFIX)


def _next_run(job: dict) -> str:
    from datetime import datetime  # noqa: PLC0415

    try:
        when = datetime.fromisoformat(str(job.get("next_run") or ""))
    except ValueError:
        return "—"
    # `%a` is the C locale's weekday, so this column printed "Sat 07:00" inside an
    # otherwise-Russian menu. The weekday comes from the locale files like every
    # other one on the card.
    return f"{_weekday_name(when.weekday())} {when:%H:%M}"


def _schedule_word(schedule: str) -> str:
    """``0 8 * * *`` → ``08:00 · daily``; anything unusual keeps its own text.

    A cron expression is a machine setting and a phone-sized table cell is not the
    place to read one. Only the two shapes a habit reminder actually uses are
    translated — a daily time and a weekly one — and every other expression is
    passed through untouched rather than guessed at.
    """
    parts = str(schedule or "").split()
    if len(parts) != 5:
        return str(schedule or "—")
    minute, hour, dom, month, dow = parts
    if not (minute.isdigit() and hour.isdigit()):
        return str(schedule)
    clock = f"{int(hour):02d}:{int(minute):02d}"
    if (dom, month, dow) == ("*", "*", "*"):
        return f"{clock} · {_t('habit.pause.every_day')}"
    if dom == "*" and month == "*" and dow.isdigit():
        # Cron counts Sunday as 0; `_weekday_name` counts Monday as 0.
        return f"{clock} · {_weekday_name((int(dow) + 6) % 7)}"
    return str(schedule)


def pause_card() -> tuple[Card, dict[str, Any]]:
    """Every reminder with its state and next fire; tapping a row flips that one.

    Deliberately lists them rather than offering a bare on/off: a switch you cannot
    verify is how you end up unsure whether the silence is you or a failure. The
    next-run column is the same reason — a reminder that is "on" but never fires
    looks identical to one that works until you can see when it is due.

    The three columns are a real table now instead of ``—``-joined prose: habit,
    schedule and next fire line up, and the state glyph rides on the habit rather
    than on a bullet the reader has to decode.
    """
    jobs = _habit_jobs()
    # The extension state OUTRANKS every per-habit switch below: with Habits off
    # these rows still read "on" and still show a next-run time, and nothing
    # arrives. Say so first, or the screen is a lie.
    banner = extension_banner()
    if not jobs:
        card = Card(_t("habit.pause.title"), glyph="⏰")
        if banner:
            card.add(Para(Raw(banner)))
        card.add(Para(_t("habit.pause.none"), style="italic"))
        return card, {"inline_keyboard": []}

    on = [j for j in jobs if j.get("enabled", True)]
    rows: list[list[str | Raw]] = []
    for j in jobs:
        live = j.get("enabled", True)
        when = _next_run(j) if live else _t("habit.pause.paused")
        rows.append(
            [
                Raw(f"{'🔔' if live else '🔕'} <b>{esc(_word(_job_key(j)))}</b>"),
                Raw(f"<code>{esc(_schedule_word(j.get('schedule', '—')))}</code>"),
                Raw(f"<i>{esc(when)}</i>"),
            ]
        )

    card = Card(
        _t("habit.pause.title"),
        glyph="⏰",
        meta=_t("habit.pause.count", on=len(on), total=len(jobs)),
    )
    if banner:
        card.add(Para(Raw(banner)))
    card.add(
        Table(
            header=[
                _t("habit.pause.col.habit"),
                _t("habit.pause.col.schedule"),
                _t("habit.pause.col.next"),
            ],
            rows=rows,
            striped=True,
        ),
        Para(_t("habit.pause.explain"), style="italic"),
    )
    card.footer = _t("habit.pause.tap_hint")

    keyboard = [
        [
            {
                "text": f"{'🔔' if j.get('enabled', True) else '🔕'} {_word(_job_key(j))}",
                "callback_data": (
                    f"{CALLBACK_PREFIX}{'p' if j.get('enabled', True) else 'r'}:{_job_key(j)}"
                ),
            }
        ]
        for j in jobs
    ]
    keyboard.append(
        [
            {
                "text": f"🔕 {_t('habit.pause.pause_all')}",
                "callback_data": f"{CALLBACK_PREFIX}p:*",
            },
            {
                "text": f"🔔 {_t('habit.pause.resume_all')}",
                "callback_data": f"{CALLBACK_PREFIX}r:*",
            },
        ]
    )
    keyboard.append(card_nav.close_row(CALLBACK_PREFIX.rstrip(":")))
    return card, {"inline_keyboard": keyboard}


def build_pause_menu() -> tuple[str, dict[str, Any]]:
    """The reminder switchboard as the plain body (the classic transport)."""
    card, keyboard = pause_card()
    return card.classic_html(), keyboard


def build_pause_menu_rich() -> tuple[str, dict[str, Any]]:
    """The same switchboard with its table, for a chat that draws rich messages."""
    card, keyboard = pause_card()
    return card.rich_html(), keyboard


def _set_reminders(key: str | None, *, enabled: bool) -> list[str]:
    from navig.scheduler import habit_store  # noqa: PLC0415

    return habit_store.set_jobs_enabled(key, enabled=enabled)


async def handle_callback(
    channel: Any,
    cb_data: str,
    chat_id: int,
    message_id: int,
    user_id: int | None = None,
) -> str:
    """Apply one button press and re-render the card. Returns the toast text."""
    path = habit_tracker.resolve_target(chat_id, message_id)
    body = cb_data[len(CALLBACK_PREFIX) :]
    parts = body.split(":")
    action = parts[0] if parts else ""
    # `hb:t:<label>:<day>:m` — the card being tapped is the morning one.
    morning = action in ("t", "s") and len(parts) >= 4 and parts[3] == "m"

    try:
        if action in ("t", "s") and len(parts) >= 3:
            # An older card can outlive the close (its edit or delete failed, or it
            # is past Telegram's 48 h): a tap on it must not rewrite a finished day.
            if habit_tracker.is_day_closed(chat_id, _expand(parts[2])):
                return f"🔒 {_t('habit.toast.day_is_closed')}"

        if action == "t" and len(parts) >= 3:
            label, day = parts[1], _expand(parts[2])
            current = habit_tracker.day_map(path, day).get(label, "")
            now_done = not habit_tracker.is_done(current)
            habit_tracker.upsert(path, day, label, "yes" if now_done else "no")
            toast = f"{MARK_DONE if now_done else MARK_OPEN} {_word(label)}"

        elif action == "s" and len(parts) >= 3:
            value, day = parts[1], _expand(parts[2])
            habit_tracker.upsert(path, day, "score", value)
            toast = _t("habit.card.day_score", value=value)

        elif action == "x" and len(parts) >= 2:
            day = _expand(parts[1])
            # Close in place: the card the operator is looking at BECOMES the
            # verdict. Buttons are cleared explicitly — leaving them would keep a
            # "close the day" control live on a day that is already closed.
            if not await _edit_card(
                channel, chat_id, message_id, closing_card(path, day), {"inline_keyboard": []}
            ):
                # The card still has live buttons: recording the close now would make
                # the scheduled close skip its fallback and leave nothing to retry.
                return f"⚠️ {_t('habit.toast.close_failed')}"
            habit_tracker.remember_closing(chat_id, message_id, day)
            habit_tracker.mark_day_closed(chat_id, day)
            await ask_for_journal(channel, chat_id, day)
            return _t("habit.toast.day_closed")

        elif action == "st" and len(parts) >= 2:
            days = int(parts[1]) if parts[1].lstrip("-").isdigit() else 7
            text, keyboard = build_stats(path, date.today(), days)
            if not await _edit(channel, chat_id, message_id, text, keyboard):
                return f"⚠️ {_t('habit.toast.card_not_updated')}"
            return (
                _t("habit.toast.all_days")
                if days <= 0
                else _t("habit.toast.last_days", n=days)
            )

        elif action in ("p", "r") and len(parts) >= 2:
            enabled = action == "r"
            target = None if parts[1] == "*" else parts[1]
            changed = _set_reminders(target, enabled=enabled)
            card, keyboard = pause_card()
            await _edit_card(channel, chat_id, message_id, card, keyboard)
            if not changed:
                return f"⚠️ {_t('habit.toast.nothing_changed')}"
            names = ", ".join(sorted(_word(k) for k in changed))
            key = "habit.pause.on_toast" if enabled else "habit.pause.paused_toast"
            return f"{'🔔' if enabled else '🔕'} {_t(key, names=names)}"[:200]

        elif action == card_nav.VERB_CLOSE:
            # A settings screen is done when the operator says so; closing it must
            # not leave six live toggles under a card that claims to be finished.
            await card_nav.settle(
                channel, chat_id, message_id, title=_t("habit.pause.title")
            )
            return ""

        else:
            return f"⚠️ {_t('habit.toast.unknown_button')}"
    except OSError as exc:
        logger.warning("habit check-in write failed (chat=%s): %s", chat_id, exc)
        return f"⚠️ {_t('habit.toast.save_failed')}"

    card, keyboard = checkin_card(path, day, morning=morning)
    if not await _edit_card(channel, chat_id, message_id, card, keyboard):
        # Saved, but the card still shows the old marks — saying only "✅ Wake"
        # would make the next tap look like it undid the habit.
        return f"⚠️ {_t('habit.toast.saved_card_stale')}"
    return toast


def journal_prompt_text(day: str) -> str:
    """What the card asks for once the day is closed.

    It used to ask three numbered questions — proud of / knocked me off / tomorrow's
    first task — chosen so the answer was short and always the same shape. The
    operator asked for the opposite: "free form of writing of all my day", because
    a fixed shape collects three sentences and a day is not three sentences.

    So this is an invitation, not a form. The only structural hints that survive
    are the ones that are *mechanical* rather than editorial: reply to THIS
    message (that is what makes the answer identifiable), a voice note works, and
    here is the file it lands in.
    """
    title = _t("habit.journal.title", day=_human_day(day))
    # `skip` is a COMMAND, so it is substituted rather than translated: a locale
    # that rendered it as "пропустить" would document a word the bot does not
    # accept. The <code> markup travels with it for the same reason.
    where = _t("habit.journal.where", day=html.escape(day), skip="</i><code>skip</code><i>")
    return "\n".join(
        [
            f"✍️ <b>{html.escape(title)}</b>",
            "",
            _t("habit.journal.invite"),
            "",
            f"<i>{_t('habit.journal.reply_hint')}</i>",
            f"<i>{_t('habit.journal.voice_hint')}</i>",
            f"<i>{where}</i>",
        ]
    )


async def ask_for_journal(channel: Any, chat_id: int, day: str) -> None:
    """Prompt for the three lines and remember that the answer is owed.

    ``force_reply`` is what makes the answer identifiable: the gateway consumes a
    message as a journal entry only when it is a reply to THIS prompt, so an
    ordinary question sent to the bot at 22:20 can never be swallowed into the
    journal instead of being answered.

    Only the tap path asks. An automatic end-of-day close happens when nobody is
    holding the phone, and a question asked into an empty room is just a
    notification.
    """
    try:
        result = await channel._api_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": journal_prompt_text(day),
                "parse_mode": "HTML",
                "reply_markup": {"force_reply": True, "selective": True},
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("journal prompt failed (chat=%s): %s", chat_id, exc)
        return

    # `channel._api_call` returns the Message itself, not the {ok, result} envelope:
    # reading ["result"]["message_id"] gave None, so the reply check — which needs
    # the prompt's id — never accepted a journal entry. Same fix as body_actions.
    from navig.checkins.state import message_id_of  # noqa: PLC0415

    prompt_id = message_id_of(result)
    habit_tracker.set_journal_prompt(chat_id, day, prompt_id)


async def _edit_card(
    channel: Any,
    chat_id: int,
    message_id: int,
    card: Card,
    keyboard: dict[str, Any] | None,
) -> bool:
    """Re-render the check-in card in place, rich where the chat supports it."""
    from navig.telegram import card_transport  # noqa: PLC0415 — avoids a cycle

    if card_transport.rich_available(channel) and hasattr(channel, "edit_rich_message"):
        try:
            await channel.edit_rich_message(
                chat_id,
                message_id,
                html=card.rich_html(),
                fallback_html=card.classic_html(),
                keyboard=keyboard,
            )
            return True
        except Exception as exc:  # noqa: BLE001 — a failed edit is not worth a tap
            logger.debug("rich check-in edit failed, retrying plain: %s", exc)
    return await _edit(channel, chat_id, message_id, card.classic_html(), keyboard)


async def _edit(
    channel: Any,
    chat_id: int,
    message_id: int,
    text: str,
    keyboard: dict[str, Any] | None,
) -> bool:
    """Edit the card. True when Telegram took the edit (or had nothing to change).

    ``channel._api_call`` answers a refusal with None rather than raising, so the
    result is what tells a stale card from an updated one."""
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if keyboard is not None:
        payload["reply_markup"] = keyboard
    else:
        # Explicitly clear the keyboard — omitting reply_markup leaves the old
        # buttons live on a message that says the day is closed.
        payload["reply_markup"] = {"inline_keyboard": []}
    try:
        result = await channel._api_call("editMessageText", payload)
    except Exception as exc:  # noqa: BLE001
        # "message is not modified" is Telegram's answer to a tap that changed
        # nothing visible; it is not a failure worth surfacing to the user.
        if "not modified" in str(exc).lower():
            return True
        logger.warning("check-in card edit failed: %s", exc)
        return False
    if result is not None:
        return True
    if "not modified" in _last_telegram_error().lower():
        return True
    logger.warning("check-in card edit refused (chat=%s, message=%s)", chat_id, message_id)
    return False


def _last_telegram_error() -> str:
    """The description of the gateway channel's last refused call, when known."""
    try:
        from navig.gateway.channels.telegram import _LAST_API_ERROR  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return ""
    return _LAST_API_ERROR.get() or ""
