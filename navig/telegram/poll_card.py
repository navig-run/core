"""Polls: the syntax card, the posted card, and the checks a poll needs before it goes out.

Telegram's poll API has been in the channel since long before this card family existed and
nothing ever called it — the Bot API will refuse a poll for reasons a chat message never
trips over (fewer than two options, more than ten, a 300-character question, a quiz without
exactly one marked answer). Those rules live here, next to the two cards that explain them,
so the handler parses and posts and nothing else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from navig.core.i18n import t as _t
from navig.telegram.card_doc import Button, Card, Code, Numbered, Para, PullQuote, Raw, Table

MAX_QUESTION = 300
MAX_OPTION = 100
MIN_OPTIONS = 2
MAX_OPTIONS = 10

USAGE = '/poll Question? | Option A | Option B [--multi] [--public] [--quiz=N]'

_FALLBACK = {
    "poll.card.title": "Polls",
    "poll.card.usage": "Usage",
    "poll.card.rules": "How it works",
    "poll.card.flags": "Flags",
    "poll.card.flag": "Flag",
    "poll.card.what": "What it does",
    "poll.card.hint": "Separate the question and each option with a pipe.",
    "poll.card.multi": "anyone can tick several answers",
    "poll.card.public": "answers are shown with names (default is anonymous)",
    "poll.card.quiz": "make it a quiz — N is the 1-based number of the right answer",
    "poll.card.created_title": "Poll posted",
    "poll.card.question": "Question",
    "poll.card.options": "Options",
    "poll.card.mode": "Mode",
    "poll.card.answerers": "Who answers",
    "poll.card.regular": "regular poll",
    "poll.card.quiz_mode": "quiz",
    "poll.card.anonymous": "anonymous",
    "poll.card.named": "visible",
    "poll.card.live": "Results update inside the poll — tap it any time.",
    "poll.card.another": "➕ Another poll",
    "poll.card.help": "🗳️ Poll help",
    "poll.card.close": "✕ Close",
    "poll.card.need_question": "The question comes first, before the first pipe.",
    "poll.card.need_options": "A poll needs at least two options.",
    "poll.card.too_many": "Telegram accepts at most ten options.",
    "poll.card.long_question": "The question must fit in {n} characters.",
    "poll.card.long_option": "An option must fit in {n} characters.",
    "poll.card.bad_quiz": "A quiz needs --quiz=N pointing at one of its options.",
    "poll.card.quiz_needs_one": "A quiz has exactly one right answer, so --multi cannot be used.",
}

FLAGS: tuple[tuple[str, str], ...] = (
    ("--multi", "poll.card.multi"),
    ("--public", "poll.card.public"),
    ("--quiz=N", "poll.card.quiz"),
)


def _label(key: str, fallback: str = "") -> str:
    """Translated label, or the English default when a locale lacks the key."""
    try:
        value = _t(key)
    except Exception:  # noqa: BLE001 - a locale miss must never break a card
        value = ""
    if not value or value == key:
        return _FALLBACK.get(key) or fallback
    return value


@dataclass(frozen=True)
class Poll:
    """A parsed poll, ready for ``send_poll``."""

    question: str
    options: tuple[str, ...]
    anonymous: bool = True
    multi: bool = False
    quiz_index: int | None = None
    error: str = ""

    @property
    def is_quiz(self) -> bool:
        return self.quiz_index is not None


def parse_poll(text: str) -> Poll:
    """Parse ``Question? | A | B [--multi] [--public] [--quiz=N]``.

    Returns a :class:`Poll` whose ``error`` holds a translated reason when the input cannot
    become a poll — the caller shows it on the usage card instead of posting.
    """
    raw = (text or "").strip()
    for prefix in ("/poll", "poll"):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix) :].strip()
            break

    anonymous = True
    multi = False
    quiz_index: int | None = None
    # Flags are picked out of the whole string before the split: an operator writing
    # `| B --multi` puts the flag on the last option, not in a segment of its own.
    for match in re.finditer(r"(?:^|\s)(--multi|--public|--quiz=\d+)\b", raw, flags=re.I):
        flag = match.group(1).lower()
        if flag == "--multi":
            multi = True
        elif flag == "--public":
            anonymous = False
        else:
            quiz_index = int(flag.split("=", 1)[1]) - 1
    stripped = re.sub(r"(?:^|\s)(--multi|--public|--quiz=\d+)\b", "", raw, flags=re.I)
    pieces = [piece.strip() for piece in stripped.split("|")]
    pieces = [piece for piece in pieces if piece]

    if not pieces:
        return Poll(question="", options=(), error=_label("poll.card.need_question"))
    question, *options = pieces
    if not question:
        return Poll(question="", options=(), error=_label("poll.card.need_question"))
    if len(question) > MAX_QUESTION:
        return Poll(
            question=question,
            options=tuple(options),
            error=_label("poll.card.long_question").replace("{n}", str(MAX_QUESTION)),
        )
    if len(options) < MIN_OPTIONS:
        return Poll(question=question, options=tuple(options), error=_label("poll.card.need_options"))
    if len(options) > MAX_OPTIONS:
        return Poll(
            question=question, options=tuple(options), error=_label("poll.card.too_many")
        )
    long_option = next((o for o in options if len(o) > MAX_OPTION), "")
    if long_option:
        return Poll(
            question=question,
            options=tuple(options),
            error=_label("poll.card.long_option").replace("{n}", str(MAX_OPTION)),
        )
    if quiz_index is not None:
        if multi:
            return Poll(
                question=question,
                options=tuple(options),
                error=_label("poll.card.quiz_needs_one"),
            )
        if quiz_index < 0 or quiz_index >= len(options):
            return Poll(
                question=question, options=tuple(options), error=_label("poll.card.bad_quiz")
            )
    return Poll(
        question=question,
        options=tuple(options),
        anonymous=anonymous,
        multi=multi,
        quiz_index=quiz_index,
    )


def usage_card(*, note: str = "", buttons: Sequence[Sequence[tuple[str, str, str | None]]] = ()) -> Card:
    """How to write a poll, with the flags as a table and a warning when one was refused."""
    card = Card(_label("poll.card.title"), glyph="🗳️")
    if note:
        card.add(PullQuote(note))
    card.add(Para(Raw(f"<b>{_label('poll.card.usage', 'Usage')}</b>")))
    card.add(Code(USAGE, language="text"))
    card.add(Para(Raw(f"<b>{_label('poll.card.flags', 'Flags')}</b>")))
    card.add(
        Table(
            header=[_label("poll.card.flag", "Flag"), _label("poll.card.what", "What it does")],
            rows=[[Raw(f"<code>{flag}</code>"), _label(key)] for flag, key in FLAGS],
            bordered=False,
        )
    )
    card.add(Para(_label("poll.card.hint", ""), style="italic"))
    _add_buttons(card, buttons)
    return card


def created_card(
    poll: Poll,
    *,
    buttons: Sequence[Sequence[tuple[str, str, str | None]]] = (),
) -> Card:
    """The record of what was posted: the sheet, the options, and where the results live."""
    mode = _label("poll.card.quiz_mode") if poll.is_quiz else _label("poll.card.regular")
    who = _label("poll.card.anonymous") if poll.anonymous else _label("poll.card.named")
    card = Card(_label("poll.card.created_title"), glyph="🗳️", meta=mode)
    card.add(Para(Raw(f"<b>{poll.question}</b>")))
    card.add(
        Table(
            rows=[
                [Raw(_label("poll.card.options", "Options")), Raw(f"<b>{len(poll.options)}</b>")],
                [Raw(_label("poll.card.mode", "Mode")), Raw(mode)],
                [Raw(_label("poll.card.answerers", "Who answers")), Raw(who)],
            ],
            bordered=False,
        )
    )
    card.add(Numbered([Raw(f"{_mark(poll, i)}{opt}") for i, opt in enumerate(poll.options)]))
    card.add(Para(_label("poll.card.live", ""), style="italic"))
    _add_buttons(card, buttons)
    return card


def _mark(poll: Poll, index: int) -> str:
    """A quiz shows which option the poll was told is right; a poll shows nothing."""
    if poll.is_quiz and poll.quiz_index == index:
        return "✅ "
    return ""


def _add_buttons(card: Card, rows: Sequence[Sequence[tuple[str, str, str | None]]]) -> None:
    for row in rows:
        if not row:
            continue
        buttons = []
        for label, data, style in row:
            if style:
                buttons.append(Button(label, data=data, style=style))
            else:
                buttons.append(Button(label, data=data))
        card.buttons(*buttons)


def default_rows() -> list[list[tuple[str, str, str | None]]]:
    """The rows the usage card offers: ask another, or go home."""
    return [
        [
            (_label("poll.card.another"), "nav:cmd:/poll", "primary"),
            (_label("poll.card.close"), "nav:home", None),
        ]
    ]


def created_rows() -> list[list[tuple[str, str, str | None]]]:
    """After a poll exists: the syntax card is one press away, and the chat can be tidied."""
    return [
        [
            (_label("poll.card.another"), "nav:cmd:/poll", None),
            (_label("poll.card.close"), "nav:home", None),
        ]
    ]


__all__ = [
    "FLAGS",
    "MAX_OPTIONS",
    "MAX_OPTION",
    "MAX_QUESTION",
    "MIN_OPTIONS",
    "Poll",
    "USAGE",
    "created_card",
    "created_rows",
    "default_rows",
    "parse_poll",
    "usage_card",
]


