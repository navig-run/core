"""`/cards` — the review index: every card family, one press away, pageable in place.

The campaign rebuilt twenty-odd surfaces and the only way to see them was to remember each
command. This is the index those surfaces deserved: one card that names each family, says
what it shows, and opens it with the press that runs the real command (`nav:cmd:`), so what
you review is what the command actually renders. Paging rewrites the same message, and the
`‹ Prev` / `Next ›` payloads carry a page number, so the index behaves like the cards it
indexes.
"""

from __future__ import annotations

from typing import Sequence

from navig.telegram.card_doc import Button, Card, Para, Raw, Table

_FALLBACK = {
    "cards.card.title": "Card index",
    "cards.card.meta": "page {page} of {pages} · {n} families",
    "cards.card.family": "Family",
    "cards.card.shows": "What it shows",
    "cards.card.intro": "Every button runs the real command, so what you see is what it renders.",
    "cards.card.prev": "‹ Prev",
    "cards.card.next": "Next ›",
    "cards.card.home": "🏠 Menu",
    "cards.card.footer": (
        "Pages rewrite this message; each family opens as its own card. "
        "Two are not here because they live elsewhere: the business-chat cards answer "
        "in a Business chat, and the location card answers when you share a pin."
    ),
}

#: ``(label, command, what it shows)`` — the order is the review order.
FAMILIES: tuple[tuple[str, str, str], ...] = (
    ("Menu tour", "/menu", "the hub: what is waiting, one press per surface"),
    ("Briefing", "/briefing", "infrastructure meter, daemon, bridge, vault, verdict"),
    ("Status", "/status", "brain / genesis / self, with the detail buttons"),
    ("Plans board", "/plans", "every space as a row: progress bar, active marker"),
    ("Add a goal", "/plan", "the goal sheet, its examples and the duplicate callout"),
    ("Help", "/help", "the encyclopedia: categories, prev/next, copyable usage"),
    ("Providers", "/providers", "route table and provider state"),
    ("Model list", "/models small", "the catalogue as paired presses, pageable"),
    ("Network tools", "/dns", "records table; /ssl, /whois and /trace sit beside it"),
    ("Trace", "/trace", "the hop table and its verdict"),
    ("Routing", "/provider show", "config beside effective; hybrid tiers; vision picker"),
    ("CLI answers", "/cli", "run again / explain / close, edited in place"),
    ("Start", "/start", "the greeting card with the day's first moves"),
    ("Kill", "/kill", "rehearsal → confirm → result, cancel defuses the card"),
    ("Poll", "/poll", "the poll syntax, its flags and the refusal shapes"),
    ("Workout", "/workout", "both forms, the reminder sheet, the habit"),
    ("Health", "/health", "body signals as a sheet, with what needs attention first"),
    ("Language", "/lang", "current value, four presses, read-back receipt"),
    ("Habits", "/habits", "today's habit check-in"),
    ("Todos", "/todo", "the tasks list with its toggle rows"),
    ("Reminders", "/reminders", "upcoming reminders, cancel in place"),
)

PER_PAGE = 6


def _t(key: str, **fields: object) -> str:
    text = _FALLBACK.get(key, "")
    for name, value in fields.items():
        text = text.replace("{" + name + "}", str(value))
    return text


def page_count(per_page: int = PER_PAGE) -> int:
    return max(1, -(-len(FAMILIES) // max(1, per_page)))


def page_families(page: int = 0, per_page: int = PER_PAGE) -> list[tuple[str, str, str]]:
    size = max(1, per_page)
    pages = page_count(size)
    index = min(max(0, page), pages - 1)
    return list(FAMILIES[index * size : index * size + size])


def review_card(page: int = 0, per_page: int = PER_PAGE) -> Card:
    """One page of the index: a table of families and a press for each."""
    size = max(1, per_page)
    pages = page_count(size)
    current = min(max(0, page), pages - 1)
    families: Sequence[tuple[str, str, str]] = page_families(current, size)

    card = Card(
        _t("cards.card.title"),
        glyph="🗂️",
        meta=_t("cards.card.meta", page=current + 1, pages=pages, n=len(FAMILIES)),
    )
    card.add(Para(_t("cards.card.intro"), style="italic"))
    card.add(
        Table(
            header=[_t("cards.card.family"), _t("cards.card.shows")],
            rows=[
                [Raw(f"<b>{label}</b>"), Raw(f"{what} <i>· {command}</i>")]
                for label, command, what in families
            ],
            bordered=False,
        )
    )

    pair: list[tuple[str, str, str | None]] = []
    rows: list[list[tuple[str, str, str | None]]] = []
    for label, command, _what in families:
        pair.append((label, f"nav:cmd:{command}", "primary"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)

    step: list[tuple[str, str, str | None]] = []
    if current > 0:
        step.append((_t("cards.card.prev"), f"nav:cmd:/cards {current - 1}", None))
    if current < pages - 1:
        step.append((_t("cards.card.next"), f"nav:cmd:/cards {current + 1}", None))
    if step:
        rows.append(step)
    rows.append([(_t("cards.card.home"), "nav:home", None)])

    for row in rows:
        buttons = []
        for label, data, style in row:
            buttons.append(Button(label, data=data, style=style) if style else Button(label, data=data))
        card.buttons(*buttons)
    card.footer = _t("cards.card.footer")
    return card


def parse_page(text: str) -> int:
    """The page number in ``/cards 2`` (1-based in chat, 0-based here), or 0."""
    parts = (text or "").strip().split()
    if len(parts) < 2:
        return 0
    try:
        return max(0, int(parts[1]) - 1)
    except ValueError:
        return 0


__all__ = ["FAMILIES", "PER_PAGE", "page_count", "page_families", "parse_page", "review_card"]
