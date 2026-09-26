"""Space journal — what the evening check-in card collects about the day.

The tracker records *whether* a day happened; the journal records *what*
happened in it. Both belong to the same space, so the journal lives next to the
tracker: ``<space>/journal/YYYY-MM-DD.md``, the layout spaces already use.

Why this module exists at all: the card has always ended by asking for the day
in writing, and nothing anywhere captured it. Taps landed in habits.csv, the
writing landed nowhere, and a plan whose own rule is "failure is when you stop
recording" was recording exactly half of itself.

The card used to ask three fixed questions and this module labelled an entry of
exactly three lines with them. It now invites free-form writing, and entries are
stored as written — see :func:`format_block`.

Written to by ``navig habit journal`` (CLI) and by the gateway when the owner
replies to the closing card's prompt. One implementation for both, for the same
reason ``habit_tracker`` is shared: two writers of one file must agree on its
shape.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

JOURNAL_DIR = "journal"

#: The three questions the evening card used to ask. Nothing WRITES them any
#: more — free-form entries are stored verbatim (see :func:`format_block`) — but
#: this is a live constant, not a memento: :func:`setback_for` PARSES this exact
#: wording out of entries already on disk, so every journal written before the
#: card changed still yields its setback line to the weekly review. Change the
#: wording and you silently stop reading the operator's own history.
PROMPTS = (
    "Proud of",
    "What knocked me off",
    "Tomorrow's number one",
)

_HEADING = "## Evening check-in"
_HEADING_AGAIN = "## Evening check-in (added later)"
_HEADING_DICTATED = "## Evening check-in (dictated)"

_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
_WEEKDAYS = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)


def journal_dir(tracker: Path) -> Path:
    """The journal folder of the space that owns *tracker* (its habits.csv)."""
    return tracker.parent / JOURNAL_DIR


def entry_path(tracker: Path, day: str) -> Path:
    """``<space>/journal/2026-08-26.md`` — one file per day, as the space already does."""
    return journal_dir(tracker) / f"{day}.md"


def _title(day: str) -> str:
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return f"# Journal — {day}"
    return f"# Journal — {_WEEKDAYS[d.weekday()]}, {d.day} {_MONTHS[d.month - 1]} {d.year}"


def clean_lines(text: str) -> list[str]:
    """The reply as lines: blanks dropped, list markers the owner typed stripped.

    ⚠ This is NO LONGER how an entry is rendered — :func:`format_block` writes
    the text through verbatim, markers and all, because nothing is prefixed to
    the writer's lines any more. It survives as the *comparison* form: whether a
    reply is empty, and whether a redelivered Telegram update is text this file
    already holds. Stripping "1." and "-" for that check is what lets a retry
    match an entry the writer formatted slightly differently the second time.
    """
    out: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        for marker in ("1.", "2.", "3.", "1)", "2)", "3)", "-", "*", "•"):
            if line.startswith(marker):
                line = line[len(marker) :].strip()
                break
        if line:
            out.append(line)
    return out


def format_block(text: str, *, again: bool = False, dictated: bool = False) -> str:
    """Render one entry as the writer wrote it.

    Two things used to happen here and neither survives the move to free-form
    writing:

    **The three labels are gone.** An entry of exactly three lines was rendered
    as "1. **Proud of:** …" / "2. **What knocked me off:** …" / "3. **Tomorrow's
    number one:** …". That was faithful while the card ASKED those three
    questions in that order. The card now invites you to write about your day,
    where three paragraphs is an ordinary shape — so the branch could only
    assert three answers nobody gave. The old docstring already stated the rule
    ("a journal that invents content is worse than an empty one") and applied it
    to every line count *except* three; that exception was a property of the
    questions, and it died with them.

    **Prose is no longer bulletised.** Every line became "- line", which is the
    right shape for three terse answers and the wrong one for a paragraph about
    a day. The text is written through verbatim, so the writer's own paragraph
    breaks and their own numbering survive — nothing is prefixed to their lines
    any more, so nothing can collide with them.

    The heading is deliberately unchanged: ``has_entry`` recognises an existing
    entry by ``_HEADING``, so renaming it would make every journal already on
    disk read as empty.
    """
    heading = _HEADING_DICTATED if dictated else (_HEADING_AGAIN if again else _HEADING)
    # Trailing whitespace goes (it is invisible and churns diffs); the line and
    # paragraph structure stays exactly as typed or transcribed.
    body = "\n".join(line.rstrip() for line in text.strip().splitlines())
    return f"{heading}\n\n{body}\n"


def append_entry(
    tracker: Path, day: str, text: str, *, dictated: bool = False
) -> tuple[Path, bool]:
    """Append the evening entry for *day*. Returns ``(path, written)``.

    ``written=False`` means the identical text was already in the file and
    nothing was added — Telegram redelivers updates on its own, and a redelivered
    reply must not write the same entry twice.

    ``dictated=True`` marks an entry that arrived as a voice note, and keeps it
    verbatim.
    """
    path = entry_path(tracker, day)
    lines = clean_lines(text)
    if not lines:
        return path, False

    existing = path.read_text(encoding="utf-8") if path.exists() else ""

    block = format_block(text, again=_HEADING in existing, dictated=dictated)
    # Compare on the lines themselves: the heading differs between the first and
    # a later entry, so comparing whole blocks would never match a retry.
    if existing and all(line in existing for line in lines):
        return path, False

    return _write(path, day, existing, block), True


def append_block(tracker: Path, day: str, block: str) -> Path:
    """Append an arbitrary markdown block to *day*'s entry, creating the file.

    Used by the weekly review, which is not a day's own entry but belongs in the
    same file: the review is written *into* the day it reviews from, so a week
    later there is one place to read rather than two.
    """
    path = entry_path(tracker, day)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    return _write(path, day, existing, block if block.endswith("\n") else block + "\n")


def _write(path: Path, day: str, existing: str, block: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if existing:
        separator = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
        path.write_text(existing + separator + block, encoding="utf-8")
    else:
        path.write_text(f"{_title(day)}\n\n{block}", encoding="utf-8")
    return path


def entry_bodies(tracker: Path, day: str) -> list[str]:
    """The evening entries written for *day*, as the writer wrote them.

    A day's file can hold more than the day's own entry: ``append_block`` writes
    the weekly review into the same file, under its own ``## Review`` heading. A
    reader that took the whole file would hand a reflection its own previous
    output as if the operator had written it. So only sections headed
    ``## Evening check-in`` (first, added-later, dictated) are returned, each
    body stripped, in file order.
    """
    path = entry_path(tracker, day)
    if not path.exists():
        return []
    bodies: list[str] = []
    current: list[str] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            if current is not None:
                bodies.append("\n".join(current).strip())
            current = [] if line.startswith(_HEADING) else None
            continue
        if current is not None:
            current.append(line)
    if current is not None:
        bodies.append("\n".join(current).strip())
    return [b for b in bodies if b]


def entries_between(tracker: Path, start: date, end: date) -> list[tuple[str, str]]:
    """``[(day, text), …]`` for every day in *start*..*end* that has an entry.

    Several entries on one day are joined with a blank line, so a day reads as
    one piece of writing. Days without an entry are simply absent — the caller
    decides what an absence means, because for a weekly read-back the honest
    answer to "what did the week hold" must not be padded with empty days.
    """
    out: list[tuple[str, str]] = []
    d = start
    while d <= end:
        bodies = entry_bodies(tracker, d.isoformat())
        if bodies:
            out.append((d.isoformat(), "\n\n".join(bodies)))
        d += timedelta(days=1)
    return out


def has_entry(tracker: Path, day: str) -> bool:
    """Whether *day* has an evening entry — not merely a file.

    The distinction matters because ``navig habit review --write`` creates the
    day's file to hold the review. Testing for the file would make the review
    announce "no journal entry" for the very day it is being written into.
    """
    path = entry_path(tracker, day)
    if not path.exists():
        return False
    return _HEADING in path.read_text(encoding="utf-8")


def setback_for(tracker: Path, day: str) -> str | None:
    """The "what knocked me off" line of *day*, if the entry carries one.

    Only a labelled entry answers this. A verbatim or dictated entry is not
    searched for a setback: picking a sentence out of free text and presenting it
    to the owner as "what broke this week" would be the system inventing a
    finding. Silence is the honest answer when the shape isn't there.
    """
    path = entry_path(tracker, day)
    if not path.exists():
        return None

    marker = f"**{PROMPTS[1]}:**"
    for raw in path.read_text(encoding="utf-8").splitlines():
        if marker in raw:
            text = raw.split(marker, 1)[1].strip()
            if text:
                return text
    return None
