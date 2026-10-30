"""Cards for the plans family: the spaces progress board and the goal answers.

`/plans` printed a `████░░░░░░` bar inside a text line, with the space name bold, the bar
padded by hand and the scope, goal and timestamp glued onto a second line — a table drawn
with spaces. It is a table here, and the bar lives in its own column so the boards line up
whatever the names are.

`/plan <goal>` answered three ways (usage, added, duplicate) in the same padded style. Those
are three cards now, and the usage card lists its examples as an ordered list rather than
three bullet lines that read like a paragraph.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from navig.core.i18n import t as _t
from navig.telegram.card_doc import Button, Card, Code, Numbered, Para, PullQuote, Raw, Table

_FALLBACK = {
    "plans.card.title": "Spaces Progress",
    "plans.card.space": "Space",
    "plans.card.progress": "Progress",
    "plans.card.updated": "Updated",
    "plans.card.active": "Active",
    "plans.card.hint": "▸ marks the active space.",
    "plans.card.empty": "No spaces yet — run /intake to set up the first one.",
    "plans.card.empty_hint": "An intake asks a few questions and writes the space for you.",
    "plans.card.meta": "{n} spaces",
    "plan.card.usage_title": "Add a Plan Goal",
    "plan.card.usage": "Usage",
    "plan.card.usage_hint": "The goal is filed under the active space; repeat the same text and nothing is written twice.",
    "plan.card.examples": "Examples",
    "plan.card.added_title": "Goal added",
    "plan.card.duplicate_title": "Duplicate goal",
    "plan.card.duplicate": "This goal is already in your plan.",
    "plan.card.space": "Space",
    "plan.card.goal": "Goal",
    "plan.card.copy_goal": "📋 Copy the command",
    "plan.card.view": "📋 View Plans",
    "plan.card.another": "➕ Add Another",
    "plan.card.intake": "🧭 Start Intake",
    "plan.card.plans": "🗺️ Plans",
    "plan.card.briefing": "📋 Briefing",
    "plan.card.spaces": "🗂️ Spaces",
    "plan.card.back": "🔙 Back",
    "plan.card.home": "🏠 Home",
}

USAGE = "/plan <goal text>"

EXAMPLES = (
    "/plan Launch MVP by end of May",
    "/plan Read 2 books this month",
    "/plan Reduce cloud costs by 20%",
)


def _label(key: str, fallback: str = "") -> str:
    """Translated label, or the English default when the locale has no key."""
    try:
        value = _t(key)
    except Exception:  # noqa: BLE001 - a locale miss must never break a card
        value = ""
    if not value or value == key:
        return _FALLBACK.get(key) or fallback
    return value


def _f(key: str, **kwargs: Any) -> str:
    text = _label(key)
    for name, value in kwargs.items():
        text = text.replace("{" + name + "}", str(value))
    return text


def progress_bar(pct: float, *, width: int = 10) -> str:
    """A ten-cell bar for *pct* (0-100), clamped so a wild value cannot draw an 80-cell line."""
    try:
        value = float(pct or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    value = min(100.0, max(0.0, value))
    filled = int(round(value / 100 * width))
    return "█" * filled + "░" * (width - filled)


def space_rows(spaces: Iterable[Any], active: str = "") -> list[list[Any]]:
    """One row per space: name (with scope and goal underneath), bar + percent, updated."""
    rows: list[list[Any]] = []
    for sp in spaces:
        name = str(getattr(sp, "name", "") or "").strip() or "—"
        marker = "▸ " if name == active else ""
        bits: list[str] = []
        scope = str(getattr(sp, "scope", "") or "")
        if scope and scope != "global":
            bits.append(f"<code>{scope}</code>")
        goal = str(getattr(sp, "goal", "") or "").strip()
        if goal:
            bits.append(f"<i>{goal}</i>")
        first = f"{marker}<b>{name}</b>"
        if bits:
            first += "<br/>" + " · ".join(bits)
        pct = float(getattr(sp, "completion_pct", 0.0) or 0.0)
        rows.append(
            [
                Raw(first),
                Raw(f"<code>{progress_bar(pct)}</code> <b>{pct:.0f}%</b>"),
                Raw(f"<i>{getattr(sp, 'last_updated', '—') or '—'}</i>"),
            ]
        )
    return rows


def spaces_card(
    *,
    spaces: Sequence[Any] = (),
    active: str = "",
    buttons: Sequence[Sequence[tuple[str, str, str | None]]] = (),
) -> Card:
    """The board: every space, its progress bar in its own column, and what it last moved."""
    meta = _f("plans.card.meta", n=len(spaces)) if spaces else ""
    card = Card(_label("plans.card.title"), glyph="🗺️", meta=meta)
    if spaces:
        card.add(
            Table(
                header=[
                    _label("plans.card.space", "Space"),
                    _label("plans.card.progress", "Progress"),
                    _label("plans.card.updated", "Updated"),
                ],
                rows=space_rows(spaces, active),
                bordered=False,
            )
        )
        card.add(
            Table(
                rows=[
                    [
                        Raw(_label("plans.card.active", "Active")),
                        Raw(f"<code>{active}</code>" if active else "<i>—</i>"),
                    ]
                ],
                bordered=False,
            )
        )
    else:
        card.add(PullQuote(_label("plans.card.empty", "")))
        card.add(Para(_label("plans.card.empty_hint", ""), style="italic"))

    _add_buttons(card, buttons)
    card.footer = _label("plans.card.hint", "") if spaces else ""
    return card


def goal_usage_card(
    *,
    buttons: Sequence[Sequence[tuple[str, str, str | None]]] = (),
    examples: Sequence[str] = EXAMPLES,
) -> Card:
    """No argument: the syntax in a copyable block, the examples as an ordered list."""
    card = Card(_label("plan.card.usage_title"), glyph="📝")
    card.add(Para(Raw(f"<b>{_label('plan.card.usage', 'Usage')}</b>")))
    card.add(Code(USAGE, language="text"))
    card.add(Para(Raw(f"<b>{_label('plan.card.examples', 'Examples')}</b>")))
    card.add(Numbered([Raw(f"<code>{example}</code>") for example in examples]))
    card.add(Para(_label("plan.card.usage_hint", ""), style="italic"))
    _add_buttons(card, buttons)
    return card


def goal_added_card(
    *,
    space: str,
    goal: str,
    buttons: Sequence[Sequence[tuple[str, str, str | None]]] = (),
) -> Card:
    """The goal landed: one sheet, and the two things an operator does next."""
    card = Card(_label("plan.card.added_title"), glyph="✅", meta=f"[{space}]")
    card.add(
        Table(
            rows=[
                [Raw(_label("plan.card.space", "Space")), Raw(f"<code>{space}</code>")],
                [_label("plan.card.goal", "Goal"), Raw(f"<b>{goal}</b>")],
            ],
            bordered=False,
        )
    )
    _add_buttons(card, buttons)
    return card


def goal_duplicate_card(
    *,
    goal: str,
    buttons: Sequence[Sequence[tuple[str, str, str | None]]] = (),
) -> Card:
    """It was already there — say so once, and offer the way to look."""
    card = Card(_label("plan.card.duplicate_title"), glyph="⚠️")
    card.add(PullQuote(_label("plan.card.duplicate", "")))
    card.add(Para(Raw(f"<code>{goal}</code>")))
    _add_buttons(card, buttons)
    return card


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


def nav_rows(*, message_id: bool) -> list[list[tuple[str, str, str | None]]]:
    """The button rows the plans family shares across its three cards."""
    rows: list[list[tuple[str, str, str | None]]] = [
        [
            (_label("plan.card.intake"), "nav:open:intake", "primary"),
            (_label("plan.card.plans"), "nav:cmd:/plans", None),
        ]
    ]
    if message_id:
        rows.append(
            [
                (_label("plan.card.back"), "nav:back", None),
                (_label("plan.card.home"), "nav:home", None),
            ]
        )
    return rows


__all__ = [
    "EXAMPLES",
    "USAGE",
    "goal_added_card",
    "goal_duplicate_card",
    "goal_usage_card",
    "nav_rows",
    "progress_bar",
    "space_rows",
    "spaces_card",
]
