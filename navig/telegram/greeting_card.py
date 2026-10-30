"""The ``/start`` greeting as a card.

The greeting itself stays what it always was: one or two warm sentences from the
model, never a command list. What changes is the envelope around it. A greeting card
can fold the "last time we talked" recap away instead of sending a second bubble, and
it carries one button — the hub — so the way into everything else is visible without
turning a hello into a dashboard.

Pure: the deck URL and the text come in as arguments, so a test renders the card
without a config read or a network call.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from navig.telegram.card_doc import Button, Card, Details, Para, Raw, esc, link

__all__ = ["build", "greeting_card"]

_LABELS: dict[str, str] = {
    "start.card.title": "NAVIG",
    "start.card.last_time": "Last time we talked",
    "start.card.deck": "Open the Deck",
    "start.card.menu": "🧭 Menu",
    "start.card.hint": "Everything else opens from the Menu — tasks, agenda, reminders and habits stay in one message.",
    "start.card.tod.morning": "Good morning",
    "start.card.tod.afternoon": "Good afternoon",
    "start.card.tod.evening": "Good evening",
    "start.card.tod.late": "Late night",
}


def _label(key: str, fallback: str = "") -> str:
    """Locale text for *key*, falling back to this module's English default."""
    default = _LABELS.get(key, fallback)
    try:
        from navig.core.i18n import t

        text = str(t(key) or "").strip()
    except Exception:  # noqa: BLE001 — a greeting must never fail on a locale file
        return default
    return default if text in (key, "") else text


def _tod_label(tod: str) -> str:
    """'morning' → the localized 'Good morning'; an unknown value stays unsaid."""
    key = f"start.card.tod.{tod}" if tod in ("morning", "afternoon", "evening", "late") else ""
    return _label(key, "") if key else ""


def _paragraphs(text: str) -> list[Para]:
    """A greeting split on blank lines, so a model-authored pause survives."""
    blocks = [block.strip() for block in (text or "").replace("\r\n", "\n").split("\n\n")]
    return [Para(block) for block in blocks if block]


def greeting_card(
    greeting: str,
    *,
    name: str = "",
    tod: str = "morning",
    away: str = "",
    deck_url: str = "",
    now: datetime | None = None,
) -> Card:
    """The card a ``/start`` answers with: greeting, folded recap, one way in."""
    del now  # kept in the signature so a caller can pin a clock without a second path
    card = Card(title=_label("start.card.title", "NAVIG"), glyph="🧭")

    meta = " · ".join(part for part in (_tod_label(tod), f"@{name}" if name else "") if part)
    if meta:
        card.meta = meta

    for para in _paragraphs(greeting):
        card.add(para)

    if away:
        card.add(
            Details(
                summary=_label("start.card.last_time", "Last time we talked"),
                body=(Para(away),),
            )
        )

    hint = _label("start.card.hint", "")
    parts = [esc(hint)] if hint else []
    if deck_url:
        # A footer is escaped wholesale, so the one real link lives in the body,
        # where Raw carries it into both projections.
        parts.append(link(deck_url, _label("start.card.deck", "Open the Deck")).html)
    if parts:
        card.add(Para(Raw(" ".join(parts))))

    card.buttons(Button(_label("start.card.menu", "🧭 Menu"), data="hm:home", style="primary"))
    return card


def build(
    greeting: str,
    *,
    name: str = "",
    tod: str = "morning",
    away: str = "",
    deck_url: str = "",
    now: datetime | None = None,
) -> tuple[str, str, list[list[dict[str, Any]]] | None]:
    """``(classic_html, rich_html, keyboard)`` for the ``/start`` answer."""
    card = greeting_card(greeting, name=name, tod=tod, away=away, deck_url=deck_url, now=now)
    return card.classic_html(), card.rich_html(), card.keyboard()
