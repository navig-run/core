"""The weekly review, built from the tracker instead of from memory.

Filling a review by hand means reconstructing seven days from memory, and memory
reports the two bad days as the whole week. The rows are already on disk; this
renders them.

What it deliberately does NOT do is draw a conclusion. It prints the week and
leaves "one decision for next week" blank — deciding is the part that only works
when the owner does it, and a generated decision would be obeyed for a week and
then quietly ignored, like every other generated intention.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from navig.spaces import habit_tracker, journal

#: Rendered separately from the yes/no labels: these carry values, not marks.
VALUE_LABELS = ("sleep_time", "score", "steps", "deep")

_MARK_DONE = "✅"
_MARK_OPEN = "❌"

_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def _word(label: str) -> str:
    from navig.telegram.habit_actions import word  # noqa: PLC0415

    return word(label)


def _span(start: date, end: date) -> str:
    if start.month == end.month:
        return f"{start.day}–{end.day} {_MONTHS[end.month - 1]}"
    return f"{start.day} {_MONTHS[start.month - 1]} – {end.day} {_MONTHS[end.month - 1]}"


def _days(start: date, end: date) -> list[date]:
    out, cursor = [], start
    while cursor <= end:
        out.append(cursor)
        cursor += timedelta(days=1)
    return out


def average_bedtime(values: list[str]) -> str | None:
    """Mean of ``HH:MM`` bedtimes, treating anything before noon as after midnight.

    Without that wrap, a 23:40 and a 00:20 average to 12:00 — the middle of the
    following day — and the one number the sleep rail is steered by would be
    nonsense in exactly the weeks it matters most.
    """
    minutes: list[int] = []
    for raw in values:
        text = raw.strip()
        if ":" not in text:
            continue
        hh, _, mm = text.partition(":")
        try:
            h, m = int(hh), int(mm[:2])
        except ValueError:
            continue
        if not (0 <= h <= 23 and 0 <= m <= 59):
            continue
        minutes.append((h + 24 if h < 12 else h) * 60 + m)

    if not minutes:
        return None
    mean = round(sum(minutes) / len(minutes))
    return f"{(mean // 60) % 24:02d}:{mean % 60:02d}"


def _average_number(values: list[str]) -> str | None:
    numbers: list[float] = []
    for raw in values:
        try:
            numbers.append(float(raw.strip()))
        except ValueError:
            continue
    if not numbers:
        return None
    mean = sum(numbers) / len(numbers)
    return f"{mean:.1f}".rstrip("0").rstrip(".")


def _body_block(start: date, end: date) -> list[str] | None:
    """The week's body numbers, or ``None`` when there is nothing to show.

    The review reminder this renders for already says "streaks, **тело**, money,
    what broke, one decision" — it has been asking about the body for months
    against a metrics.csv nothing wrote to. This is the answer to its own
    question.

    It reads the BODY record, which lives in a different space from the tracker
    (the growth space keeps the discipline; the health space keeps the numbers
    and the medicine), so it resolves its own path rather than deriving one from
    *tracker*. Never raises: a missing or unreadable body record must not take
    the whole review down — the habit half is still worth rendering.
    """
    try:
        from navig.spaces import body_metrics as bm  # noqa: PLC0415

        path = bm.default_path()
        points = [(d, v) for d, v in bm.series(path) if start <= d <= end]
        if not points:
            return None

        span = (end - start).days + 1
        average = bm.moving_average(path, days=span, ending=end)
        change = bm.trend(path, days=span, ending=end)
    except Exception:  # noqa: BLE001 — the review is worth more than this section
        return None

    listed = " · ".join(f"{v:g}" for _, v in points)
    line = f"**Weight:** {listed}  (average **{average:g} kg**"
    if change is not None:
        line += f", **{change:+.1f} kg** vs the week before"
    line += ")"

    out = ["", line]
    if len(points) < span:
        # Same rule as the tracker's blank days: a gap is invisible in a summary
        # of what WAS recorded, and the average of two readings is not a week.
        out.append(f"_Recorded on {len(points)} of {span} days._")
    return out


def build(tracker: Path, start: date, end: date) -> str:
    """Render the review for the window as a markdown block."""
    rows = habit_tracker.read_tracker(tracker)
    days = _days(start, end)
    by_day = {d.isoformat(): habit_tracker.day_map(tracker, d.isoformat()) for d in days}

    out: list[str] = [f"## Review — {_span(start, end)}", ""]

    # ── The floor, day by day ────────────────────────────────────────────────
    out.append("| | " + " | ".join(d.strftime("%a %d") for d in days) + " | Done |")
    out.append("|---|" + "---|" * (len(days) + 1))
    for label in habit_tracker.NON_NEGOTIABLES:
        marks, hit = [], 0
        for d in days:
            done = habit_tracker.is_done(by_day[d.isoformat()].get(label, ""))
            marks.append(_MARK_DONE if done else _MARK_OPEN)
            hit += done
        out.append(f"| **{_word(label)}** | " + " | ".join(marks) + f" | {hit}/{len(days)} |")

    complete = sum(
        1
        for d in days
        if all(
            habit_tracker.is_done(by_day[d.isoformat()].get(h, ""))
            for h in habit_tracker.NON_NEGOTIABLES
        )
    )
    out += ["", f"**Complete days: {complete} of {len(days)}** — all three done."]

    # ── Everything else that was actually logged this week ───────────────────
    others: dict[str, int] = {}
    for d in days:
        for label, value in by_day[d.isoformat()].items():
            if label in habit_tracker.NON_NEGOTIABLES or label in VALUE_LABELS:
                continue
            if habit_tracker.is_done(value):
                others[label] = others.get(label, 0) + 1
    if others:
        parts = [f"{_word(k)} {v}/{len(days)}" for k, v in sorted(others.items())]
        out += ["", "**The rest:** " + " · ".join(parts)]

    # ── Values, not marks ────────────────────────────────────────────────────
    bedtimes = [by_day[d.isoformat()].get("sleep_time", "") for d in days]
    average = average_bedtime([b for b in bedtimes if b])
    if average:
        listed = " · ".join(b or "—" for b in bedtimes)
        out += ["", f"**Bedtime:** {listed}  (average **{average}**)"]

    scores = [by_day[d.isoformat()].get("score", "") for d in days]
    score_avg = _average_number([s for s in scores if s])
    if score_avg:
        out += ["", f"**Day score:** {' · '.join(s or '—' for s in scores)}  (average **{score_avg}**)"]

    body = _body_block(start, end)
    if body:
        out += body

    # ── What the journal already said ────────────────────────────────────────
    setbacks = [(d, journal.setback_for(tracker, d.isoformat())) for d in days]
    found = [(d, s) for d, s in setbacks if s]
    out += ["", "**What knocked me off — from the journal**"]
    if found:
        out += [f"- {d.strftime('%a %d')}: {s}" for d, s in found]
    else:
        out.append("- _nothing recorded this week_")

    # ── The silence, named ───────────────────────────────────────────────────
    #
    # A week of missing entries is invisible in a summary of the entries that do
    # exist, and "failure is when you stop recording" is the rule this whole
    # tracker is built on. So the gaps get their own line.
    blank = [d for d in days if not by_day[d.isoformat()]]
    if blank:
        out += ["", "**No tracker row at all:** " + ", ".join(d.strftime("%a %d") for d in blank)]
    no_entry = [d for d in days if not journal.has_entry(tracker, d.isoformat())]
    if no_entry:
        # "No journal entry" was the first wording and it read as a contradiction
        # inside the very file --write had just created. Naming the missing thing
        # — the three lines — is both accurate and answerable.
        out += ["", "**No three lines:** " + ", ".join(d.strftime("%a %d") for d in no_entry)]

    out += [
        "",
        "**One decision for next week:**",
        "",
        "_______________________________________________",
        "",
        "_One decision. Five improvements at once is how you make none of them._",
    ]

    if not rows:
        out += ["", "_The tracker is empty for this window — nothing above was measured._"]

    return "\n".join(out) + "\n"
