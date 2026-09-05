"""Space journal — the three lines the check-in card asks for every evening.

The tracker records *whether* a day happened; the journal records *what*
happened in it. Both belong to the same space, so the journal lives next to the
tracker: ``<space>/journal/YYYY-MM-DD.md``, the layout spaces already use.

Why this module exists at all: the card has always ended with "three lines in
the journal and you're done", and nothing anywhere captured them. Taps landed in
habits.csv, the three lines landed nowhere, and a plan whose own rule is
"failure is when you stop recording" was recording exactly half of itself.

Written to by ``navig habit journal`` (CLI) and by the gateway when the owner
replies to the closing card's prompt. One implementation for both, for the same
reason ``habit_tracker`` is shared: two writers of one file must agree on its
shape.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

JOURNAL_DIR = "journal"

#: The card's three questions, in the order it asks them.
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

    People number their own lines. Keeping "1." and then adding our own label
    would render "1. **Proud of:** 1. finished the release".
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
    """Render one entry.

    Exactly three lines get the card's labels — that is what was asked for, in
    that order. Any other count is written verbatim as bullets: labelling two
    lines "Proud of" and "What knocked me off" would put words in someone's
    mouth, and a journal that invents content is worse than an empty one.
    """
    lines = clean_lines(text)
    heading = _HEADING_DICTATED if dictated else (_HEADING_AGAIN if again else _HEADING)
    body: list[str] = [heading, ""]

    # A transcript has no line breaks — speech does not come with a line 1, 2 and
    # 3. Labelling it would attach the card's three questions to whatever the
    # speech-to-text happened to produce, so dictation is always kept verbatim.
    if len(lines) == len(PROMPTS) and not dictated:
        body += [f"{i}. **{PROMPTS[i - 1]}:** {line}" for i, line in enumerate(lines, start=1)]
    else:
        body += [f"- {line}" for line in lines]

    return "\n".join(body) + "\n"


def append_entry(
    tracker: Path, day: str, text: str, *, dictated: bool = False
) -> tuple[Path, bool]:
    """Append the evening entry for *day*. Returns ``(path, written)``.

    ``written=False`` means the identical text was already in the file and
    nothing was added — Telegram redelivers updates on its own, and a redelivered
    reply must not print the same three lines twice.

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

    Used by the weekly review, which is not a three-line entry but belongs in the
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
