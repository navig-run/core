"""A notification body that is already laid out as sections becomes a card.

The daily brief is dispatched by a producer as one plain-text body::

    Brief 2026-10-26
    OUTPOST  all green (15-min probes)
    AUTOMATION  20 task(s) ok (+2 by design)
    NEXT
      1. Reply to Rakib - send GAZE invite (4d late)
    HABITS   ship 1/7 · out 0/7

Those columns were held together by spaces, so on a phone nothing lined up and the
whole message read as a paragraph. :func:`render` reads that shape back — a label, two
or more spaces, a value, plus optional labelled lists — and returns the same message
as a card: section headings, a two-column table, a real list. The producer keeps
working in plain text; Telegram gets structure.

It returns ``None`` when the body has no structure worth showing. A two-line alert
("The scheduled job did not finish successfully." / "error: …") must keep its plain
shape — forcing every alert into a table would be noise, not structure.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from html import escape

from navig.telegram.card_doc import Bullets, Card, Heading, Para, Raw, Table

logger = logging.getLogger("navig.notify")

#: ``LABEL`` + two or more spaces + a value. Labels are upper case by convention
#: (OUTPOST, TASKS, EMPIRE-2); a line with a single space is prose, not a section.
_SECTION = re.compile(r"^([A-Z][A-Z0-9 &/'()._-]{2,}?)\s{2,}(\S.*)$")
#: A label alone on its line opens a group whose rows follow (``NEXT``).
_HEADING = re.compile(r"^([A-Z][A-Z0-9 &/'()._-]{2,})$")
#: ``1. text`` / ``- text`` / ``• text``, with any indent.
_ITEM = re.compile(r"^\s{0,6}(?:(\d+)[.)]|[-•*])\s+(\S.*)$")
#: ``(4d late)`` — the one fact on a brief's next-list that must not be skimmed past.
_LATE = re.compile(r"^(.*?)(\((?:\d+d )?(?:late|overdue)\))\s*$", re.IGNORECASE)
#: A producer's own inline markup (``<b>NOTICE</b>``), dropped before a cell is drawn.
_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9]{0,12}(?:\s[^<>]{0,40})?/?>")
#: Markdown emphasis (``**LE MOTIF:**``, `` `a_b` ``) in a body that was written for a
#: markdown renderer. A card draws its own emphasis, so the markers are dropped rather
#: than shown as asterisks — ``**LE MOTIF:** плановая чистка`` arrived literally.
_MD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__|`([^`]+)`")
#: ``(1 row(s))`` — a producer's own accounting, said the way a person says it.
_ROWS = re.compile(r"\((\d+)\s+rows?\(s\)\)")
#: ``20 task(s)`` — same.
_COUNT = re.compile(r"(\d+)\s+task\(s\)")
#: ``2,000.00 EUR`` — the currency sign in front, where a reader looks for it.
_MONEY = re.compile(r"(?<![\w.])(\d{1,3}(?:[ ,]\d{3})*(?:\.\d{1,2})?)\s*(EUR|USD|GBP)\b")
_SIGN = {"EUR": "€", "USD": "$", "GBP": "£"}

#: A cell longer than this stops being skimmable; the rest of its facts move below.
CELL_WIDTH = 56

#: Below this many sections a body is a message, not a report.
MIN_SECTIONS = 3

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _section_label(raw: str) -> str:
    """``OUTPOST`` → ``Outpost``; ``FOLLOW-UP`` keeps its hyphen, ``AI 2`` its digit."""
    return " ".join(word.capitalize() for word in raw.strip().split())


def _meta(title: str) -> str:
    """The readable date behind a ``Brief 2026-10-26`` title, else empty.

    The date is already in the title, so it stays there; the meta line adds the
    weekday, which is the part a person has to work out from an ISO date.
    """
    tail = (title or "").strip().rsplit(" ", 1)
    if len(tail) != 2:
        return ""
    try:
        day = date.fromisoformat(tail[1])
    except ValueError:
        return ""
    return f"{_DAYS[day.weekday()]} {day.day} {_MONTHS[day.month - 1]}"


def parse(
    body: str,
) -> tuple[list[tuple[str, str]], list[tuple[str, list[str]]], list[str]]:
    """``([(label, value)], [(group_title, [rows])], [lead])`` from a plain body.

    Lines before the first label are the lead: a headline the producer wanted read
    first ("TX tx 009 · day 2026-10-26"). The card keeps them as one paragraph
    unless they only repeat the title.

    A line that follows a ``LABEL  value`` line and is not itself a label or an
    item continues that value: a caption or an error message wraps, and dropping
    its second line would send half a sentence.
    """
    sections: list[tuple[str, str]] = []
    groups: list[tuple[str, list[str]]] = []
    lead: list[str] = []
    current: list[str] | None = None

    for line in (body or "").splitlines():
        if not line.strip():
            continue
        item = _ITEM.match(line)
        if item is not None and current is not None:
            text = item.group(2).strip()
            current.append(text)
            continue
        section = _SECTION.match(line)
        if section is not None:
            sections.append((_section_label(section.group(1)), section.group(2).strip()))
            current = None
            continue
        heading = _HEADING.match(line.strip())
        if heading is not None:
            current = []
            groups.append((_section_label(heading.group(1)), current))
            continue
        if not sections and not groups:
            lead.append(line.strip())
        elif current is None and sections:
            # A wrapped value, not a new field: the caption's second line.
            label, value = sections[-1]
            sections[-1] = (label, f"{value} {line.strip()}".strip())
        current = None

    return sections, [(t, rows) for t, rows in groups if rows], lead


def _item_node(text: str) -> str | Raw:
    """An item, with its overdue marker bold — the one thing worth seeing first."""
    text = _MD.sub(lambda m: m.group(1) or m.group(2) or m.group(3), text)
    late = _LATE.match(text)
    if late is None:
        return text

    return Raw(f"{escape(late.group(1).rstrip())} <b>{escape(late.group(2))}</b>")


def _value(text: str) -> str:
    """One cell: markup flattened, ``a | b | c`` read as ``a · b · c``.

    A vertical bar is how these bodies mark a boundary between facts, and inside a
    table column that character is drawn as a cell edge — two borders for one fact.
    A producer's own ``<b>`` is dropped rather than escaped into the cell: the card
    draws its own emphasis, and a literal tag in a table reads as a bug.
    """
    plain = _MD.sub(lambda m: m.group(1) or m.group(2) or m.group(3), " ".join(str(text).split()))
    plain = _TAG.sub("", plain)
    return " · ".join(plain.split(" | "))


def _readable(text: str) -> str:
    """The producer's accounting said the way a reader counts.

    ``(1 row(s))`` and ``20 task(s)`` are a template's plural guess and ``2,000.00
    EUR`` puts the unit after a number people scan for; a brief is read in three
    seconds, so each of those is rewritten into its ordinary form.
    """
    text = _ROWS.sub(lambda m: "(1 row)" if m.group(1) == "1" else f"({m.group(1)} rows)", text)
    text = _COUNT.sub(r"\1 tasks", text)
    return _MONEY.sub(lambda m: f"{_SIGN[m.group(2)]}{m.group(1)}", text)


def _split(value: str) -> tuple[str, str]:
    """``(cell, rest)`` — the facts that fit a table cell, and the ones that don't.

    A cell is read at a glance, so it holds one or two facts; a brief's caveat
    ("directionally-positive (keep; not significant at this n)") is real content
    that would bury the headline inside its own row, so it moves to the notes under
    the table instead of being dropped.
    """
    parts = value.split(" · ")
    cell = parts[0]
    for index, part in enumerate(parts[1:], start=1):
        if len(cell) + 3 + len(part) <= CELL_WIDTH:
            cell = f"{cell} · {part}"
        else:
            return cell, " · ".join(parts[index:])
    return cell, ""


def build(type_key: str, title: str, body: str) -> Card | None:
    """The card for *body*, or ``None`` when it has no structure worth showing."""
    sections, groups, lead = parse(body)
    if len(sections) < MIN_SECTIONS:
        return None

    from navig.notify.router import emoji_for_type

    title = (title or "").strip()
    glyph = "" if title[:1] and not title[:1].isalnum() else emoji_for_type(type_key)
    card = Card(title=title, glyph=glyph, meta=_meta(title))
    lead_text = _MD.sub(lambda m: m.group(1) or m.group(2) or m.group(3), " ".join(lead))
    if lead_text and lead_text.casefold() not in title.casefold():
        card.add(Para(lead_text))

    rows: list[tuple[str, str]] = []
    notes: list[str] = []
    for label, value in sections:
        cell, rest = _split(_readable(_value(value)))
        rows.append((label, cell))
        if rest:
            notes.append(f"<b>{escape(label)}</b> — {escape(rest)}")
    card.add(Table(rows=rows))
    if notes:
        card.add(Heading("What the numbers don't show"))
        card.add(Bullets([Raw(note) for note in notes]))
    for group_title, group_rows in groups:
        card.add(Heading(group_title))
        card.add(Bullets([_item_node(row) for row in group_rows]))
    return card


#: The one action a briefing's reader actually takes: the work behind the numbers.
#: ``cd:`` callbacks are core's card registry; ``hb:`` is the habit handler's own
#: form, so both buttons land on a live handler and never on a dead spinner.
BUTTONS: dict[str, list[list[dict[str, str]]]] = {
    "briefing": [
        [
            {"text": "☑ Tasks", "callback_data": "cd:tsk:review:review"},
            {"text": "◔ Habits", "callback_data": "hb:st:7"},
        ]
    ],
}


#: ``19 due`` — the number that makes a Tasks button worth pressing.
_DUE = re.compile(r"(\d+)\s+due\b")

#: A type whose message is a condition to be cleared, not news to be read: it earns a
#: Resolve button, and once resolved the same condition is not announced again.
_RESOLVABLE_SUFFIX = "_alert"
_RESOLVABLE_TYPES = frozenset({"agent_failed", "node_status", "mail_send_problem"})

#: The card action that records the resolution (registered in `navig.telegram.notice_cards`).
RESOLVE_PREFIX = "nak"


def resolvable(type_key: str) -> bool:
    """True when a message of this type is a condition the operator can clear."""
    return type_key.endswith(_RESOLVABLE_SUFFIX) or type_key in _RESOLVABLE_TYPES


def keyboard(type_key: str, body: str = "", *, key: str = "") -> list[list[dict[str, str]]] | None:
    """The inline keyboard a card of this type carries, or ``None``.

    The body of a brief is a report; the buttons are how a reader gets from the
    report to the thing it talks about. Only types whose handlers exist get a
    keyboard — a button that answers nothing is worse than no button. The Tasks
    button says how much work is waiting, because that is the decision ("open it
    now or after coffee") and the body already counted it.

    An alert gets a Resolve button keyed on its own words (*key*): the press records
    that a person has seen this condition, and the monitor's next run stays quiet.
    """
    rows = [list(row) for row in BUTTONS.get(type_key, [])]
    due = _DUE.search(body or "")
    label = f"☑ Tasks · {due.group(1)} due" if due else "☑ Tasks"
    rows = [
        [
            {**button, "text": label} if button["text"].startswith("☑") else dict(button)
            for button in row
        ]
        for row in rows
    ]
    if key and resolvable(type_key):
        rows.append([{"text": "✓ Resolve", "callback_data": f"cd:{RESOLVE_PREFIX}:done:{key}"}])
    return rows or None


def render(type_key: str, title: str, body: str) -> str | None:
    """The card's rich HTML, or ``None``. Only the sender needs the string form."""
    card = build(type_key, title, body)
    if card is None:
        return None
    try:
        return card.rich_html()
    except Exception:  # noqa: BLE001 — a card that cannot render must not lose the message
        logger.debug("notify card render failed", exc_info=True)
        return None
