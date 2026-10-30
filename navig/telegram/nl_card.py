"""The natural-language dispatcher's own answers, as cards.

Bare text arrives here and one of three things can happen before the LLM ever sees it: a
space intent is detected and auto-starts in three seconds, a command match is ambiguous, or
the command needs arguments it did not get. Each of those used to be a paragraph with the
actionable part buried in `<code>`, and the argument case had no button at all — the usage
line was output to be re-read and retyped by hand.

The callbacks are the ones the router already answers (`nl_yes`, `nl_cancel`,
`nl_pick:<command>`), so nothing downstream changed.
"""

from __future__ import annotations

from typing import Any, Iterable

from navig.core.i18n import t
from navig.telegram.card_doc import Button, Card, Code, Para, Raw, Table, esc

_FALLBACK = {
    "nl.card.intent_title": "Space intent",
    "nl.card.ambiguous_title": "Which command?",
    "nl.card.missing_title": "Missing arguments",
    "nl.card.detected": "Detected",
    "nl.card.intent": "Intent",
    "nl.card.space": "Space",
    "nl.card.starts": "Auto-starts",
    "nl.card.in_seconds": "in {seconds}s",
    "nl.card.hint": "Reply cancel to stop, or yes to run it now.",
    "nl.card.ambiguous_hint": "Nothing ran — one of these matches what you typed.",
    "nl.card.missing_hint": "This one needs arguments before it can run.",
    "nl.card.usage": "Usage",
    "nl.card.yes": "✅ Yes now",
    "nl.card.cancel": "🛑 Cancel",
    "nl.card.copy_usage": "📋 Copy usage",
    "nl.card.command": "Command",
    "nl.card.why": "Why",
}


def _label(key: str, fallback: str = "") -> str:
    try:
        value = t(key)
    except Exception:  # noqa: BLE001 — a missing catalogue is not a failed answer
        value = ""
    if not value or value == key:
        return fallback or _FALLBACK.get(key, key)
    return value


def intent_card(
    *,
    intent: str,
    space: str,
    delay: int = 3,
    window: str = "",
) -> Card:
    """A detected space intent, with the two answers it accepts as buttons."""
    card = Card(
        _label("nl.card.intent_title", "Space intent"),
        glyph="🧭",
        meta=f"{_label('nl.card.detected', 'Detected')} · {intent}",
    )
    card.add(
        Table(
            rows=[
                [_label("nl.card.intent", "Intent"), Raw(f"<code>{esc(intent)}</code>")],
                [_label("nl.card.space", "Space"), Raw(f"<code>{esc(space)}</code>")],
                [
                    _label("nl.card.starts", "Auto-starts"),
                    Raw(
                        _label("nl.card.in_seconds", "in {seconds}s").replace(
                            "{seconds}", str(delay)
                        )
                    ),
                ],
            ],
            bordered=False,
        )
    )
    if window:
        card.add(Para(Raw(esc(window))))
    card.add(Para(_label("nl.card.hint", "Reply cancel to stop, or yes to run it now.")))
    card.buttons(
        Button(_label("nl.card.yes", "✅ Yes now"), data="nl_yes", style="success"),
        Button(_label("nl.card.cancel", "🛑 Cancel"), data="nl_cancel", style="danger"),
    )
    return card


def _candidate_rows(candidates: Iterable[dict[str, Any]], limit: int = 3) -> list[list[Button]]:
    """One press per candidate, two per row so the picker does not grow tall."""
    picks: list[Button] = []
    for item in list(candidates)[:limit]:
        command = str(item.get("command") or "").strip().lower()
        if not command:
            continue
        usage = str(item.get("usage") or f"/{command}").strip()
        picks.append(Button(f"▶ {usage}", data=f"nl_pick:{command}", style="primary"))
    rows = [picks[i : i + 2] for i in range(0, len(picks), 2)]
    if picks:
        rows.append([Button(_label("nl.card.cancel", "🛑 Cancel"), data="nl_cancel", style="danger")])
    return rows


def ambiguous_card(
    *,
    candidates: list[dict[str, Any]],
    query: str = "",
    limit: int = 3,
) -> Card:
    """More than one command matched: show them as a table and let a press decide."""
    card = Card(
        _label("nl.card.ambiguous_title", "Which command?"),
        glyph="🧭",
        meta=f"“{query}”" if query else "",
    )
    card.add(Para(_label("nl.card.ambiguous_hint", "Nothing ran — one of these matches what you typed.")))
    rows = []
    for item in candidates[:limit]:
        command = str(item.get("command") or "").strip().lower()
        if not command:
            continue
        usage = str(item.get("usage") or f"/{command}").strip()
        why = str(item.get("description") or item.get("reason") or "").strip()
        rows.append(
            [
                Raw(f"<code>{esc(usage)}</code>"),
                Raw(esc(why)) if why else Raw("<i>—</i>"),
            ]
        )
    if rows:
        card.add(
            Table(
                header=[_label("nl.card.command", "Command"), _label("nl.card.why", "Why")],
                rows=rows,
                bordered=False,
            )
        )
    for row in _candidate_rows(candidates, limit=limit):
        card.buttons(*row)
    return card


def missing_args_card(*, command: str, usage: str, description: str = "") -> Card:
    """The command matched but it cannot run without arguments — hand back the usage."""
    card = Card(
        _label("nl.card.missing_title", "Missing arguments"),
        glyph="🧭",
        meta=f"/{command}",
    )
    card.add(Para(_label("nl.card.missing_hint", "This one needs arguments before it can run.")))
    if description:
        card.add(Para(Raw(esc(description))))
    card.add(Para(Raw(f"<b>{esc(_label('nl.card.usage', 'Usage'))}</b>")))
    card.add(Code(usage, language="text"))
    card.buttons(Button(_label("nl.card.copy_usage", "📋 Copy usage"), copy=usage, style="primary"))
    card.buttons(Button(_label("nl.card.cancel", "🛑 Cancel"), data="nl_cancel", style="danger"))
    return card


def keyboard_of(card: Card) -> list[list[dict[str, Any]]] | None:
    return card.keyboard()


def build_intent(*, intent: str, space: str, delay: int = 3, window: str = "") -> tuple[str, str, list]:
    card = intent_card(intent=intent, space=space, delay=delay, window=window)
    return card.classic_html(), card.rich_html(), card.keyboard()


def build_ambiguous(*, candidates: list[dict[str, Any]], query: str = "") -> tuple[str, str, list]:
    card = ambiguous_card(candidates=candidates, query=query)
    return card.classic_html(), card.rich_html(), card.keyboard()


def build_missing_args(*, command: str, usage: str, description: str = "") -> tuple[str, str, list]:
    card = missing_args_card(command=command, usage=usage, description=description)
    return card.classic_html(), card.rich_html(), card.keyboard()


__all__ = [
    "ambiguous_card",
    "build_ambiguous",
    "build_intent",
    "build_missing_args",
    "intent_card",
    "keyboard_of",
    "missing_args_card",
]
