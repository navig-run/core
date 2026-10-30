"""
Habit tracker store — the habits.csv a space owns.

Split out of ``navig.commands.habit`` so that both the CLI and the Telegram
check-in card read and write through one implementation. The gateway must not
import a CLI command module to log a button press.

The file shape is the one spaces already had — ``date,habit,completed,notes`` —
so rows written by hand stay valid and nothing needs migrating.
"""

from __future__ import annotations

import csv
import os
from datetime import date, timedelta
from pathlib import Path

TRACKER_FILE = "habits.csv"
TRACKER_FIELDS = ["date", "habit", "completed", "notes"]

#: The three that decide whether a day counts at all.
NON_NEGOTIABLES = ("wake", "out", "ship")

#: ``completed`` values that mean "this did not happen (yet)".
NOT_DONE = {"", "no", "n", "false", "0", "pending", "skip", "skipped"}


def tracker_path(space: str | None = None) -> Path:
    """Locate the habits.csv this invocation means.

    ``space`` wins, then the directory the command was invoked from **if it
    already holds a habits.csv**, then the active space.

    The invocation rule is not a convenience: ``get_active_working_dir()`` prefers
    the pin written by ``navig space switch`` over the directory you are standing
    in, so logging from inside a space whose tracker you can see would silently
    write into a different space's file — and the row would look lost. A directory
    that already owns a tracker is unambiguous about which one is meant.

    ``NAVIG_INVOCATION_CWD`` rather than ``Path.cwd()``: main.py chdir's to the
    active space before any command runs, so by now cwd is the pin, not the shell's
    directory. That env var is the pre-chdir launch dir (main.py:746). A relative
    ``space`` is anchored there too, for the same reason.

    A space directory that declares ``habits.log_file`` in its ``.navig/config.yaml``
    (the bundled human-space keeps it at ``spaces/growth/habits.csv``) is honoured;
    the root-level ``habits.csv`` is only the fallback for spaces that declare none.
    """
    if space:
        from navig.platform.paths import resolve_user_path  # noqa: PLC0415

        p = resolve_user_path(space)
        return p if p.suffix.lower() == ".csv" else _space_tracker(p)

    launched_from = os.environ.get("NAVIG_INVOCATION_CWD") or ""
    here = (Path(launched_from) if launched_from else Path.cwd()) / TRACKER_FILE
    if here.exists():
        return here

    from navig.spaces.active import get_active_working_dir  # noqa: PLC0415

    return _space_tracker(get_active_working_dir())


def _space_tracker(root: Path) -> Path:
    """The habits.csv a space directory declares, else ``<root>/habits.csv``.

    A root-level tracker that already holds history keeps winning while the
    declared file does not exist yet, so nothing logged before this rule
    appears lost.
    """
    from navig.core.yaml_io import safe_load_yaml  # noqa: PLC0415

    fallback = root / TRACKER_FILE
    config = safe_load_yaml(root / ".navig" / "config.yaml")
    habits = config.get("habits") if isinstance(config, dict) else None
    log_file = habits.get("log_file") if isinstance(habits, dict) else None
    if isinstance(log_file, str) and log_file.strip():
        declared = root / Path(log_file.strip()).expanduser()
        if declared.exists() or not fallback.exists():
            return declared
    return fallback


def read_tracker(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        return [
            {k: (row.get(k) or "") for k in TRACKER_FIELDS}
            for row in csv.DictReader(fh)
            if row.get("date")
        ]


def write_tracker(path: Path, rows: list[dict]) -> None:
    """Replace the tracker ATOMICALLY.

    It used to truncate the file and then write it, so a crash, a full disk or a
    second writer landing mid-write left a torn or empty habits.csv — the whole
    streak history. The CLI, the Telegram check-in and the deck tick all write it.
    """
    import io  # noqa: PLC0415

    from navig.core.yaml_io import atomic_write_text  # noqa: PLC0415

    buf = io.StringIO(newline="")
    # LF, not csv's default CRLF: atomic_write_text writes in TEXT mode, which turns
    # LF into the platform ending — a CRLF handed to it would land as CR CR LF.
    writer = csv.DictWriter(buf, fieldnames=TRACKER_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, buf.getvalue(), encoding="utf-8")


def is_done(value: str) -> bool:
    return value.strip().lower() not in NOT_DONE


def upsert(path: Path, day: str, habit: str, completed: str, note: str = "") -> str:
    """Write one row, replacing that day's row for the same habit.

    Returns the previous ``completed`` value ("" when the row is new) so callers
    can report what changed. Idempotent by (date, habit): logging the same label
    twice on one day can never make that day count twice.
    """
    rows = read_tracker(path)
    for row in rows:
        if row["date"] == day and row["habit"] == habit:
            previous = row["completed"]
            row["completed"] = completed
            if note:
                row["notes"] = note
            write_tracker(path, rows)
            return previous

    rows.append({"date": day, "habit": habit, "completed": completed, "notes": note})
    rows.sort(key=lambda r: (r["date"], r["habit"]))
    write_tracker(path, rows)
    return ""


def day_map(path: Path, day: str) -> dict[str, str]:
    """``{habit: completed}`` for one day — what the check-in card renders from."""
    return {r["habit"]: r["completed"] for r in read_tracker(path) if r["date"] == day}


# ── Which tracker a Telegram card belongs to ──────────────────────────────────
#
# The card is SENT by the CLI (which knows the space) but its buttons are handled
# by the GATEWAY, a different process whose active space is very likely something
# else. Without this the taps would land in the wrong habits.csv — the same class
# of silent mis-write `tracker_path()` exists to prevent. The chat the card went
# to identifies the tracker it belongs to.


#: How many card → tracker entries one chat keeps. Cards older than this resolve
#: through the chat's latest tracker, which is what every card did before.
_MAX_CARDS = 32


def _targets_file() -> Path:
    from navig.platform import paths  # noqa: PLC0415

    return paths.config_dir() / "cache" / "habit_checkin_targets.json"


def _read_state() -> dict:
    import json  # noqa: PLC0415

    try:
        data = json.loads(_targets_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _chat_state(data: dict, chat_id: int) -> dict:
    """One chat's record, tolerating the original format (a bare path string)."""
    entry = data.get(str(chat_id))
    if isinstance(entry, str):
        return {"path": entry}
    return entry if isinstance(entry, dict) else {}


def _write_state(data: dict) -> None:
    import json  # noqa: PLC0415

    f = _targets_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")


def remember_target(
    chat_id: int,
    path: Path,
    message_id: int | None = None,
    day: str | None = None,
) -> None:
    """Record which habits.csv the card sent to *chat_id* writes into.

    The message id and day are kept too so the day can be closed later — by the
    owner tapping, or automatically at the end of the day — by editing that exact
    card rather than posting a second one nobody asked for.

    The path is stored ABSOLUTE: the gateway that resolves it runs from a different
    working directory than the CLI that recorded it. And it is stored per message
    as well as per chat, so two live cards for two spaces in one chat each keep
    writing into their own tracker (see :func:`resolve_target`).
    """
    data = _read_state()
    entry = _chat_state(data, chat_id)
    absolute = str(Path(path).expanduser().resolve())
    entry["path"] = absolute
    if message_id is not None:
        entry["message_id"] = message_id
        cards = entry.get("cards")
        cards = dict(cards) if isinstance(cards, dict) else {}
        cards.pop(str(message_id), None)
        cards[str(message_id)] = absolute
        entry["cards"] = dict(list(cards.items())[-_MAX_CARDS:])
    if day is not None:
        entry["day"] = day
    data[str(chat_id)] = entry
    _write_state(data)


def resolve_target(chat_id: int, message_id: int | None = None) -> Path:
    """The tracker a tap from *chat_id* belongs to; falls back to normal resolution.

    With *message_id*, the card that was tapped wins over the chat's latest card —
    a newer card for a different space must not redirect taps on an older one.
    """
    entry = _chat_state(_read_state(), chat_id)
    if message_id is not None:
        cards = entry.get("cards")
        if isinstance(cards, dict) and isinstance(cards.get(str(message_id)), str):
            return Path(cards[str(message_id)])
    recorded = entry.get("path")
    return Path(recorded) if recorded else tracker_path()


def last_card(chat_id: int) -> tuple[int | None, str | None]:
    """``(message_id, day)`` of the last card sent to *chat_id*."""
    entry = _chat_state(_read_state(), chat_id)
    mid = entry.get("message_id")
    return (int(mid) if mid is not None else None), entry.get("day")


def remember_morning(chat_id: int, message_id: int, day: str) -> None:
    """Record the MORNING card on its own, beside the last-card pointer.

    The last-card pointer is shared: the 22:15 card overwrites it, so by evening
    nothing knew which message the 08:00 card was. The morning card is reposted at
    11:30 and removed at 19:00, and both need exactly that id."""
    data = _read_state()
    entry = _chat_state(data, chat_id)
    entry["morning"] = {"message_id": int(message_id), "day": day}
    data[str(chat_id)] = entry
    _write_state(data)


def morning_card(chat_id: int) -> tuple[int | None, str | None]:
    """``(message_id, day)`` of the morning card still in *chat_id*, if any."""
    slot = _chat_state(_read_state(), chat_id).get("morning")
    if not isinstance(slot, dict) or slot.get("message_id") is None:
        return None, None
    return int(slot["message_id"]), slot.get("day")


def forget_morning(chat_id: int) -> None:
    data = _read_state()
    entry = _chat_state(data, chat_id)
    if entry.pop("morning", None) is not None:
        data[str(chat_id)] = entry
        _write_state(data)


def remember_closing(chat_id: int, message_id: int, day: str) -> None:
    """Record the card that now says the day is closed.

    Closing edits the check-in card in place when its id is still known, so the
    closing card IS the last card. When the edit fails (deleted, too old to edit)
    a fresh message is posted instead, and only this pointer remembers which
    message the verdict landed in. The morning after, exactly one card must
    survive — the new one — so this is what gets deleted."""
    data = _read_state()
    entry = _chat_state(data, chat_id)
    entry["closed_card"] = {"message_id": int(message_id), "day": day}
    data[str(chat_id)] = entry
    _write_state(data)


def closing_card(chat_id: int) -> tuple[int | None, str | None]:
    """``(message_id, day)`` of the card that closed a day, if it was recorded."""
    slot = _chat_state(_read_state(), chat_id).get("closed_card")
    if not isinstance(slot, dict) or slot.get("message_id") is None:
        return None, None
    return int(slot["message_id"]), slot.get("day")


def forget_closing(chat_id: int) -> None:
    data = _read_state()
    entry = _chat_state(data, chat_id)
    if entry.pop("closed_card", None) is not None:
        data[str(chat_id)] = entry
        _write_state(data)


def mark_day_closed(chat_id: int, day: str) -> None:
    data = _read_state()
    entry = _chat_state(data, chat_id)
    entry["closed_day"] = day
    data[str(chat_id)] = entry
    _write_state(data)


def is_day_closed(chat_id: int, day: str) -> bool:
    """True once the day has been closed, so auto-close never closes it twice."""
    return _chat_state(_read_state(), chat_id).get("closed_day") == day


#: How many recently-sent sparks to remember. At two a day this is a fortnight —
#: long enough that a line never feels recycled, short enough that a small pool
#: does not run itself dry and start repeating anyway.
SPARK_MEMORY = 28


def recent_sparks(chat_id: int) -> list[str]:
    """Keys of the lines sent to *chat_id* lately, newest last."""
    got = _chat_state(_read_state(), chat_id).get("sparks")
    return [str(k) for k in got] if isinstance(got, list) else []


def remember_spark(chat_id: int, key: str) -> None:
    """Record a sent line so it is not chosen again for a fortnight.

    A repeated line is worse than no line: it is the moment the thing stops
    reading as written for you and starts reading as a machine emptying a bucket.
    """
    data = _read_state()
    entry = _chat_state(data, chat_id)
    seen = [k for k in entry.get("sparks", []) if isinstance(k, str) and k != key]
    entry["sparks"] = (seen + [key])[-SPARK_MEMORY:]
    data[str(chat_id)] = entry
    _write_state(data)


# ── The journal prompt the closing card leaves open ───────────────────────────
#
# Closing the day asks for the day in writing. The answer arrives as a normal Telegram
# message, possibly minutes later, so the gateway has to know that a reply to
# THAT message is a journal entry and not a question for the agent.
#
# Kept on disk beside the tracker pin rather than in memory on the channel (the
# pattern `_pending_api_key_input` uses): a gateway restart between the prompt
# and the reply would otherwise drop the entry silently, which is the exact
# failure this whole feature exists to fix.


def set_journal_prompt(chat_id: int, day: str, prompt_msg_id: int | None) -> None:
    """Record that *chat_id* was asked to write about *day*."""
    data = _read_state()
    entry = _chat_state(data, chat_id)
    entry["journal_prompt"] = {"day": day, "message_id": prompt_msg_id}
    data[str(chat_id)] = entry
    _write_state(data)


def journal_prompt(chat_id: int) -> tuple[str | None, int | None]:
    """``(day, prompt_message_id)`` of the open journal prompt for *chat_id*."""
    entry = _chat_state(_read_state(), chat_id).get("journal_prompt")
    if not isinstance(entry, dict):
        return None, None
    mid = entry.get("message_id")
    return entry.get("day"), (int(mid) if mid is not None else None)


def clear_journal_prompt(chat_id: int) -> None:
    data = _read_state()
    entry = _chat_state(data, chat_id)
    if entry.pop("journal_prompt", None) is not None:
        data[str(chat_id)] = entry
        _write_state(data)


def best_streak(rows: list[dict], habit: str) -> int:
    """The longest run of consecutive days ever logged for *habit*."""
    days = sorted(
        date.fromisoformat(r["date"])
        for r in rows
        if r["habit"] == habit and is_done(r["completed"]) and _parses(r["date"])
    )
    best = run = 0
    previous: date | None = None
    for day in days:
        run = run + 1 if previous is not None and (day - previous).days == 1 else 1
        previous = day
        best = max(best, run)
    return best


def _parses(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def summarize(rows: list[dict], today: date) -> dict:
    """The whole cycle at a glance: per-habit totals, complete days, weekday shape.

    The day count runs from the FIRST logged day, not from a configured start date —
    the file is the record, so nothing can drift out of sync with it.

    ``by_weekday`` exists because "I missed some days" hides the actual pattern. Days
    are cheap to count and tell you nothing; knowing that every complete day was a
    weekday tells you what to change.
    """
    # A future-dated row (`habit log --date` accepts one) is not part of the period yet:
    # counting it made the span zero or negative and inflated `done` against it.
    days = sorted(
        {r["date"] for r in rows if _parses(r["date"]) and date.fromisoformat(r["date"]) <= today}
    )
    if not days:
        return {"days": 0, "start": None, "end": None, "habits": {}, "complete": 0, "by_weekday": {}}

    start = date.fromisoformat(days[0])
    span = (today - start).days + 1

    done_by_day = {
        d: {r["habit"] for r in rows if r["date"] == d and is_done(r["completed"])} for d in days
    }

    habits: dict[str, dict] = {}
    for habit in NON_NEGOTIABLES:
        hit = sum(1 for d in days if habit in done_by_day[d])
        habits[habit] = {
            "done": hit,
            "streak": streak_for(rows, habit, today),
            "best": best_streak(rows, habit),
            "missed": max(0, span - hit),
        }

    complete = sum(1 for d in days if set(NON_NEGOTIABLES) <= done_by_day[d])

    by_weekday: dict[int, dict[str, int]] = {i: {"days": 0, "complete": 0} for i in range(7)}
    cursor = start
    while cursor <= today:
        bucket = by_weekday[cursor.weekday()]
        bucket["days"] += 1
        if set(NON_NEGOTIABLES) <= done_by_day.get(cursor.isoformat(), set()):
            bucket["complete"] += 1
        cursor += timedelta(days=1)

    return {
        "days": span,
        "start": start,
        "end": today,
        "habits": habits,
        "complete": complete,
        "by_weekday": by_weekday,
    }


def day_rows(rows: list[dict], start: date, end: date) -> list[tuple[date, dict[str, bool]]]:
    """``[(day, {habit: done})]`` for every day in the range — including blank ones.

    Blank days are the point: a day with no row at all is the failure mode that a
    list of logged days renders invisible.
    """
    done_by_day: dict[str, set[str]] = {}
    for r in rows:
        if is_done(r["completed"]):
            done_by_day.setdefault(r["date"], set()).add(r["habit"])

    out: list[tuple[date, dict[str, bool]]] = []
    cursor = start
    while cursor <= end:
        marked = done_by_day.get(cursor.isoformat(), set())
        out.append((cursor, {h: h in marked for h in NON_NEGOTIABLES}))
        cursor += timedelta(days=1)
    return out


def streak_for(rows: list[dict], habit: str, today: date) -> int:
    """Consecutive days ending today (or yesterday, if today isn't logged yet)."""
    done = {r["date"] for r in rows if r["habit"] == habit and is_done(r["completed"])}
    if not done:
        return 0
    cursor = today if today.isoformat() in done else today - timedelta(days=1)
    count = 0
    while cursor.isoformat() in done:
        count += 1
        cursor -= timedelta(days=1)
    return count


def mark_done_today(habit: str, path: Path | None = None, note: str = "") -> bool:
    """Record *habit* as done today unless today already says it is done.

    For one-tap surfaces (the deck tick). A row the operator logged with a value
    (``navig habit log deep --value 2``) is already "done" and is left exactly as it
    is. Returns True when a row was written.
    """
    target = path or tracker_path()
    day = date.today().isoformat()
    if is_done(day_map(target, day).get(habit, "")):
        return False
    upsert(target, day, habit, "yes", note)
    return True
