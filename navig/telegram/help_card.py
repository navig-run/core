"""The Help Encyclopedia as cards: the front door, a category, and one command.

`/help` is the one screen every operator opens first and it was plain text with the commands
listed as `• /name — description` lines under a grey keyboard that repeated the same names.
A category is a **table** now (`Command | What it does`), and the same rows carry their
detail presses in the bubble. The categories are neighbours, so a category card gets
`‹ Prev` / `Next ›` and rewrites itself in place instead of walking back to the hub each
time — the "next and previous that stay put" the whole campaign is chasing.

The builders take plain data (the handler gathers it; this module only arranges it), so the
help catalogue stays the single source of truth and no callback payload changes: every press
still carries ``help:c:<key>``, ``help:s:<cat>:<sub>``, ``help:d:<cmd>:<cat>``, ``help:t``,
``help:home``, ``help:close`` or ``xt:r``.
"""

from __future__ import annotations

from typing import Any, Sequence

from navig.telegram.card_doc import Button, Card, Code, Para, Raw, Table

_FALLBACK = {
    "help.card.title": "Help",
    "help.card.meta": "{n} commands · {m} categories",
    "help.card.intro": "Tap a category, or type /help <topic> to search it.",
    "help.card.keywords": "Reply to any message with a keyword to transform it.",
    "help.card.command": "Command",
    "help.card.what": "What it does",
    "help.card.none": "—",
    "help.card.category_meta": "{n} commands · {i} of {total}",
    "help.card.prev": "‹ Prev",
    "help.card.next": "Next ›",
    "help.card.categories": "🗂 Categories",
    "help.card.close": "✕ Close",
    "help.card.keyword_actions": "✨ Reply keywords",
    "help.card.extensions": "🔌 Extensions",
    "help.card.usage": "Usage",
    "help.card.copy_usage": "📋 Copy usage",
    "help.card.back": "🔙 Back to category",
    "help.card.no_description": "no description yet",
}


def _t(key: str, **fields: Any) -> str:
    """Translated label, or the English default (a help screen must never fail to render)."""
    text = _FALLBACK.get(key, "")
    for name, value in fields.items():
        text = text.replace("{" + name + "}", str(value))
    return text


def _buttons(card: Card, rows: Sequence[Sequence[tuple[str, str, str | None]]]) -> None:
    for row in rows:
        if not row:
            continue
        built = []
        for label, data, style in row:
            if style:
                built.append(Button(label, data=data, style=style))
            else:
                built.append(Button(label, data=data))
        card.buttons(*built)


def home_card(data: dict[str, Any]) -> Card:
    """The hub: what this is for, then one press per category."""
    commands = int(data.get("command_count") or 0)
    categories = list(data.get("categories") or [])
    card = Card(
        _t("help.card.title"),
        glyph="❓",
        meta=_t("help.card.meta", n=commands, m=len(categories)),
    )
    card.add(Para(_t("help.card.intro")))
    card.add(Para(_t("help.card.keywords"), style="italic"))

    rows: list[list[tuple[str, str, str | None]]] = []
    pair: list[tuple[str, str, str | None]] = []
    for item in categories:
        label = f"{item.get('emoji', '')} {item.get('label', '')}".strip()
        pair.append((label, f"help:c:{item.get('key', '')}", None))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append(
        [
            (data.get("keywords_label") or _t("help.card.keyword_actions"), "help:t", None),
            (data.get("extensions_label") or _t("help.card.extensions"), "xt:r", None),
        ]
    )
    rows.append([(data.get("close_label") or _t("help.card.close"), "help:close", None)])
    _buttons(card, rows)
    return card


def category_card(data: dict[str, Any]) -> Card:
    """One category: the commands as a table, their detail presses, and its neighbours."""
    emoji = str(data.get("emoji") or "")
    label = str(data.get("label") or "")
    rows_data = list(data.get("commands") or [])
    index = int(data.get("index") or 0)
    total = int(data.get("total") or 0)

    card = Card(
        f"{emoji} {label}".strip(),
        glyph="",
        meta=_t("help.card.category_meta", n=len(rows_data), i=index, total=total),
    )

    table_rows = []
    for item in rows_data:
        name = str(item.get("name") or "")
        desc = str(item.get("description") or "").strip() or _t("help.card.no_description")
        table_rows.append([Raw(f"<code>/{name}</code>"), Raw(desc)])
    if table_rows:
        card.add(
            Table(
                header=[_t("help.card.command"), _t("help.card.what")],
                rows=table_rows,
                bordered=False,
            )
        )
    else:
        card.add(Para(_t("help.card.none"), style="italic"))

    buttons: list[list[tuple[str, str, str | None]]] = []
    pair: list[tuple[str, str, str | None]] = []
    for item in rows_data:
        callback = str(item.get("callback") or "")
        if not callback:
            continue
        pair.append((f"/{item.get('name', '')}", callback, None))
        if len(pair) == 2:
            buttons.append(pair)
            pair = []
    if pair:
        buttons.append(pair)

    step: list[tuple[str, str, str | None]] = []
    if data.get("prev"):
        step.append((_t("help.card.prev"), f"help:c:{data['prev']}", None))
    if data.get("next"):
        step.append((_t("help.card.next"), f"help:c:{data['next']}", None))
    if step:
        buttons.append(step)
    buttons.append(
        [
            (data.get("categories_label") or _t("help.card.categories"), "help:home", None),
            (data.get("close_label") or _t("help.card.close"), "help:close", None),
        ]
    )
    _buttons(card, buttons)
    return card


def command_card(data: dict[str, Any]) -> Card:
    """A single command: what it does, how it is spelled, and the way back."""
    name = str(data.get("name") or "")
    usage = str(data.get("usage") or "").strip() or f"/{name}"
    description = str(data.get("description") or "").strip() or _t("help.card.no_description")
    category_label = str(data.get("category_label") or "")

    card = Card(f"/{name}", glyph="", meta=category_label)
    card.add(Para(Raw(description)))
    card.add(Para(Raw(f"<b>{_t('help.card.usage')}</b>")))
    card.add(Code(usage, language="text"))

    card.buttons(Button(_t("help.card.copy_usage"), copy=usage, style="primary"))
    card.buttons(
        Button(
            data.get("back_label") or _t("help.card.back"),
            data=str(data.get("back_callback") or "help:home"),
        ),
        Button(data.get("categories_label") or _t("help.card.categories"), data="help:home"),
    )
    card.buttons(Button(data.get("close_label") or _t("help.card.close"), data="help:close"))
    return card


def card_from(data: dict[str, Any] | None) -> Card | None:
    """Build the card for one help screen, or ``None`` when the data names no screen."""
    if not data:
        return None
    kind = str(data.get("kind") or "")
    if kind == "home":
        return home_card(data)
    if kind == "category":
        return category_card(data)
    if kind == "command":
        return command_card(data)
    return None


__all__ = ["card_from", "category_card", "command_card", "home_card"]
