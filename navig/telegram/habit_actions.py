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
    hb:t:<label>:<yyyymmdd>   toggle a habit
    hb:s:<score>:<yyyymmdd>   set the day's score
    hb:x:<yyyymmdd>           close the day
"""

from __future__ import annotations

import html
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from navig.spaces import habit_tracker

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "hb:"


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
        return (
            "⚠️ <b>The Habits extension is off</b> — these reminders are scheduled "
            "but not delivered.\nTurn it on: /extensions\n"
        )
    return "\n".join(extension_banner_cli() or ())


def extension_banner_cli() -> tuple[str, str] | None:
    """The same warning as ``(message, detail)`` for ``ch.warning``.

    A tuple rather than one string with a separator: the CLI renders the two
    halves differently, and splitting a formatted string back apart on
    whitespace is the kind of thing that breaks silently on a reflow.
    """
    if not extension_is_off():
        return None
    return (
        "The Habits extension is off - these reminders are scheduled but not delivered.",
        "Turn it on: navig telegram extensions enable habits",
    )

#: Button state markers. An empty checkbox plus an icon read as "white square and
#: icon" — no word, no state, nothing to act on. A word and an unmistakable mark
#: are what make a button answerable at 22:15 without thinking.
MARK_DONE = "✅"
MARK_OPEN = "❌"

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


def build_card(path: Path, day: str, morning: bool = False) -> tuple[str, dict[str, Any]]:
    """Render the check-in card for *day* from the tracker at *path*.

    ``morning=True`` frames the same card as the start of the day. The buttons are
    identical on purpose: waking up is the one thing that can only be marked at the
    moment it happens, and by 22:15 it is a memory. One card in the morning and one
    in the evening beats a single card that scrolls out of reach by lunchtime.
    """
    logged = habit_tracker.day_map(path, day)
    rows = habit_tracker.read_tracker(path)

    def done(label: str) -> bool:
        return habit_tracker.is_done(logged.get(label, ""))

    open_floor = [_word(h) for h in habit_tracker.NON_NEGOTIABLES if not done(h)]

    if morning:
        lines = [
            f"☀️ <b>{html.escape(_t('habit.card.morning_title'))}</b>"
            f" · {html.escape(_human_day(day))}",
            "",
        ]
        lines.append(html.escape(_t("habit.card.morning_line")))
        # `{wake}` is the localized label, so the sentence names the button the
        # operator can actually see rather than the English word "Wake".
        lines.append(_t("habit.card.morning_tap", wake=html.escape(word("wake"))))
    else:
        lines = [
            f"✍️ <b>{html.escape(_t('habit.card.evening_title'))}</b>"
            f" · {html.escape(_human_day(day))}"
        ]

    lines += ["", f"<b>{html.escape(_floor_title())}</b>", floor_line(logged)]

    if not morning:
        if open_floor:
            lines.append(
                _t(
                    "habit.card.still_open",
                    names=html.escape(", ".join(open_floor)),
                )
            )
        else:
            lines.append(f"✅ <b>{html.escape(_t('habit.card.all_three'))}</b>")

    lines += [
        "",
        "<i>"
        + html.escape(_t("habit.card.tap_hint", open=MARK_OPEN, done=MARK_DONE))
        + "</i>",
    ]

    try:
        today = date.fromisoformat(day)
        streaks = " · ".join(
            f"{_word(h)} "
            + _t("habit.card.streak_day", n=habit_tracker.streak_for(rows, h, today))
            for h in habit_tracker.NON_NEGOTIABLES
        )
        lines.append(
            f"<i>{html.escape(_t('habit.card.streaks', value=streaks))}</i>"
        )
    except ValueError:
        pass

    score = logged.get("score", "")
    if score and habit_tracker.is_done(score):
        lines.append(f"<i>{html.escape(_t('habit.card.day_score', value=score))}</i>")

    keyboard: list[list[dict[str, str]]] = []
    stamp = _compact(day)
    for row in CHECKIN_ROWS:
        keyboard.append(
            [
                {
                    # `_word(label)`, NOT the tuple's second element: that is the
                    # English word baked into CHECKIN_ROWS, and binding it to the
                    # name `word` also shadowed the word() function right here —
                    # which is why the card text localized and the buttons under
                    # it stayed in English.
                    "text": f"{MARK_DONE if done(label) else MARK_OPEN} {_word(label)}",
                    "callback_data": f"{CALLBACK_PREFIX}t:{label}:{stamp}",
                }
                for label, _english in row
            ]
        )
    keyboard.append(
        [
            {
                "text": f"{'▶' if score == value else ''}{face}",
                "callback_data": f"{CALLBACK_PREFIX}s:{value}:{stamp}",
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

    return "\n".join(lines), {"inline_keyboard": keyboard}


def build_closing_text(path: Path, day: str) -> str:
    """What the card becomes once the day is closed — buttons removed.

    Names the three and says which one was missed. The previous version reported
    "2/3 non-negotiables", which is a score for a machine: it never said what the
    three were, which one broke, or what to do about it tomorrow.
    """
    logged = habit_tracker.day_map(path, day)
    missed = [
        _word(h) for h in habit_tracker.NON_NEGOTIABLES if not habit_tracker.is_done(logged.get(h, ""))
    ]

    lines = [
        f"🌙 <b>{html.escape(_human_day(day))} closed</b>",
        "",
        f"<b>{html.escape(_floor_title())}</b>",
        floor_line(logged),
        "",
    ]

    if missed:
        names = html.escape(", ".join(missed))
        lines.append(f"⚠️ <b>Missed: {names}.</b>")
        lines.append(
            "One miss is noise. Two in a row is a new habit — "
            f"tomorrow <b>{names}</b> is not optional. NEVER MISS TWICE."
        )
    else:
        lines.append(f"✅ <b>{html.escape(_t('habit.card.all_three'))}</b>")

    # Everything outside the three, minus the score — which gets its own line
    # rather than sitting in a list of habits as if "score" were something you do.
    extra = sorted(
        _word(k)
        for k, v in logged.items()
        if habit_tracker.is_done(v) and k not in habit_tracker.NON_NEGOTIABLES and k != "score"
    )
    if extra:
        lines += ["", f"<i>Also done: {html.escape(', '.join(extra))}</i>"]

    score = logged.get("score", "")
    if score and habit_tracker.is_done(score):
        lines.append(f"<i>{html.escape(_t('habit.card.day_score', value=score))}</i>")

    lines += ["", "Three lines in the journal and you're done."]
    return "\n".join(lines)


# ── /stats — the cycle, on the phone ──────────────────────────────────────────
#
# The numbers only change behaviour if they are readable where the behaviour
# happens. `navig habit stats` in a terminal is a report you have to go and fetch;
# the same thing in the chat that already interrupts you is one you actually read.

#: Ranges the stats panel offers. 0 = the whole cycle.
STATS_RANGES: list[tuple[int, str]] = [(7, "7 days"), (14, "14 days"), (0, "All")]

_WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def build_stats(path: Path, today: date, days: int = 7) -> tuple[str, dict[str, Any]]:
    """The whole cycle as one message: totals, day-of-week, and a day list.

    ``days=0`` shows every day since the first row. The day-of-week block is the
    part worth the screen space — a total says "some days were missed", a weekday
    column says which day of the week you lose and therefore what to change.
    """
    rows = habit_tracker.read_tracker(path)
    if not rows:
        return (
            "📊 <b>Progress</b>\n\nNothing logged yet — tap a card and this fills in.",
            {"inline_keyboard": []},
        )

    s = habit_tracker.summarize(rows, today)
    pct = round(100 * s["complete"] / s["days"]) if s["days"] else 0

    lines = [
        f"📊 <b>Progress</b> · day {s['days']}"
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
    table = [f"{'':<6}{'done':>7}{'now':>6}{'best':>6}"]
    for label in habit_tracker.NON_NEGOTIABLES:
        st = s["habits"][label]
        done = f"{st['done']}/{s['days']}"
        now = f"{st['streak']}d"
        best = f"{st['best']}d"
        table.append(f"{_word(label):<6}{done:>7}{now:>6}{best:>6}")
    lines += [_pre(table), ""]

    # Table 2 — the weekday column, the part actually worth reading. A total says
    # "some days were missed"; this says which day of the week you lose.
    week = [f"{'':<5}{'counted':>9}"]
    for i, name in enumerate(_WEEKDAY_NAMES):
        bucket = s["by_weekday"].get(i, {"days": 0, "complete": 0})
        if not bucket["days"]:
            continue
        hit, total = bucket["complete"], bucket["days"]
        bar = MARK_ON * hit + MARK_OFF * (total - hit)
        week.append(f"{name:<5}{f'{hit}/{total}':>5}  {bar}")
    lines += ["<b>By day of week</b>", _pre(week), ""]

    # Table 3 — the day list, blanks included. A day with no row at all is the
    # failure a list of only-logged-days renders invisible.
    start = s["start"] if days <= 0 else max(s["start"], today - timedelta(days=days - 1))
    # Full words in the header, not truncations: "Wake" and "Walk" both cut to "Wa".
    # The month goes in its own separator row instead of a column on every line —
    # it changes twice a cycle and costs four characters of width every day.
    head = "".join(f"{_word(h):>5}" for h in habit_tracker.NON_NEGOTIABLES)
    grid = [f"{'':<7}{head}"]
    month: int | None = None
    blanks = full = 0
    for day, marks in habit_tracker.day_rows(rows, start, today):
        if day.month != month:
            month = day.month
            grid.append(f"{_MONTHS[month - 1]}")
        cells = "".join(
            f"{MARK_ON if marks[h] else MARK_OFF:>5}" for h in habit_tracker.NON_NEGOTIABLES
        )
        hit = sum(marks.values())
        tail = ""
        if hit == 0:
            tail, blanks = "  ←", blanks + 1
        elif hit == 3:
            tail, full = "  ★", full + 1
        grid.append(f"{day.strftime('%a %d'):<7}{cells}{tail}")
    lines += ["<b>Day by day</b>", _pre(grid)]

    # A bare symbol is a symbol you have to ask about — the same defect as a
    # button reading "Groom". The legend only appears when the symbol does.
    legend = []
    if full:
        legend.append("★ all three")
    if blanks:
        legend.append("← nothing logged")
    if legend:
        lines.append(f"<i>{' · '.join(legend)}</i>")

    keyboard = [
        [
            {
                "text": f"{'▶ ' if n == days else ''}{label}",
                "callback_data": f"{CALLBACK_PREFIX}st:{n}",
            }
            for n, label in STATS_RANGES
        ]
    ]
    return "\n".join(lines), {"inline_keyboard": keyboard}


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
        return datetime.fromisoformat(str(job.get("next_run") or "")).strftime("%a %H:%M")
    except ValueError:
        return "—"


def build_pause_menu() -> tuple[str, dict[str, Any]]:
    """Every reminder with its state and next fire; tapping a row flips that one.

    Deliberately lists them rather than offering a bare on/off: a switch you cannot
    verify is how you end up unsure whether the silence is you or a failure. The
    next-run column is the same reason — a reminder that is "on" but never fires
    looks identical to one that works until you can see when it is due.
    """
    jobs = _habit_jobs()
    # The extension state OUTRANKS every per-habit switch below: with Habits off
    # these rows still read "on" and still show a next-run time, and nothing
    # arrives. Say so first, or the screen is a lie.
    banner = extension_banner()
    if not jobs:
        return (
            banner + "⏰ <b>Reminders</b>\n\nNo habit reminders are configured.",
            {"inline_keyboard": []},
        )

    on = [j for j in jobs if j.get("enabled", True)]
    lines = ([banner] if banner else []) + [
        f"⏰ <b>Reminders</b> — {len(on)} of {len(jobs)} on",
        "",
    ]
    for j in jobs:
        mark = "🔔" if j.get("enabled", True) else "🔕"
        when = _next_run(j) if j.get("enabled", True) else "paused"
        lines.append(
            f"{mark} <b>{html.escape(_word(_job_key(j)))}</b> — "
            f"<code>{html.escape(str(j.get('schedule', '—')))}</code> · {when}"
        )
    lines += [
        "",
        "<i>Nothing is deleted when you pause — the schedule and every logged row",
        "stay exactly as they are, and resuming picks up where you left off.</i>",
        "",
        "<i>Tap one to flip it.</i>",
    ]

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
            {"text": "🔕 Pause all", "callback_data": f"{CALLBACK_PREFIX}p:*"},
            {"text": "🔔 Resume all", "callback_data": f"{CALLBACK_PREFIX}r:*"},
        ]
    )
    return "\n".join(lines), {"inline_keyboard": keyboard}


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
    path = habit_tracker.resolve_target(chat_id)
    body = cb_data[len(CALLBACK_PREFIX) :]
    parts = body.split(":")
    action = parts[0] if parts else ""

    try:
        if action == "t" and len(parts) >= 3:
            label, day = parts[1], _expand(parts[2])
            current = habit_tracker.day_map(path, day).get(label, "")
            now_done = not habit_tracker.is_done(current)
            habit_tracker.upsert(path, day, label, "yes" if now_done else "no")
            toast = f"{MARK_DONE if now_done else MARK_OPEN} {_word(label)}"

        elif action == "s" and len(parts) >= 3:
            value, day = parts[1], _expand(parts[2])
            habit_tracker.upsert(path, day, "score", value)
            toast = f"Day score {value}/10"

        elif action == "x" and len(parts) >= 2:
            day = _expand(parts[1])
            await _edit(channel, chat_id, message_id, build_closing_text(path, day), None)
            habit_tracker.mark_day_closed(chat_id, day)
            await ask_for_journal(channel, chat_id, day)
            return "Day closed"

        elif action == "st" and len(parts) >= 2:
            days = int(parts[1]) if parts[1].lstrip("-").isdigit() else 7
            text, keyboard = build_stats(path, date.today(), days)
            await _edit(channel, chat_id, message_id, text, keyboard)
            return "All days" if days <= 0 else f"Last {days} days"

        elif action in ("p", "r") and len(parts) >= 2:
            enabled = action == "r"
            target = None if parts[1] == "*" else parts[1]
            changed = _set_reminders(target, enabled=enabled)
            text, keyboard = build_pause_menu()
            await _edit(channel, chat_id, message_id, text, keyboard)
            if not changed:
                return "⚠️ Nothing changed"
            names = ", ".join(sorted(_word(k) for k in changed))
            return f"{'🔔 On' if enabled else '🔕 Paused'}: {names}"[:200]

        else:
            return "⚠️ Unknown button"
    except OSError as exc:
        logger.warning("habit check-in write failed (chat=%s): %s", chat_id, exc)
        return "⚠️ Could not save"

    text, keyboard = build_card(path, day)
    await _edit(channel, chat_id, message_id, text, keyboard)
    return toast


def journal_prompt_text(day: str) -> str:
    """What the card asks for once the day is closed.

    The three questions are quoted from the daily card rather than paraphrased:
    the point is that the answer is short and always the same shape, so it can be
    given at 22:15 without composing anything.
    """
    return "\n".join(
        [
            f"✍️ <b>Three lines · {html.escape(_human_day(day))}</b>",
            "",
            "1. What I did today, what I'm proud of.",
            "2. What specifically knocked me off.",
            "3. The number-one task for tomorrow.",
            "",
            "<i>Reply to this message — three lines, one per line.</i>",
            "<i>A voice note works too: reply with one and it is written down for you.</i>",
            f"<i>They land in journal/{html.escape(day)}.md. Send</i> <code>skip</code> "
            "<i>to leave the day without them.</i>",
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

    prompt_id = ((result or {}).get("result") or {}).get("message_id")
    habit_tracker.set_journal_prompt(chat_id, day, prompt_id)


async def _edit(
    channel: Any,
    chat_id: int,
    message_id: int,
    text: str,
    keyboard: dict[str, Any] | None,
) -> None:
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
        await channel._api_call("editMessageText", payload)
    except Exception as exc:  # noqa: BLE001
        # "message is not modified" is Telegram's answer to a tap that changed
        # nothing visible; it is not a failure worth surfacing to the user.
        logger.debug("check-in card edit failed: %s", exc)
