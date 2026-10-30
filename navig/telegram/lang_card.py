"""`/lang` as a card: the current value, the choices as presses, and a read-back receipt.

The command exists because an unregistered `/lang Russian` fell through to the model, which
answered "Язык переключён" and wrote nothing — a confirmation the operator could not tell
from a real one. So the card shows the value that was **read back from config**, never the
value that was requested, and a mismatch is its own (rare) state.
"""

from __future__ import annotations

from typing import Sequence

from navig.telegram.card_doc import Button, Card, Para, Raw, Table

_FALLBACK = {
    "lang.card.title": "Output language",
    "lang.card.current": "Current",
    "lang.card.auto": "auto (follow the content)",
    "lang.card.auto_short": "Auto",
    "lang.card.saved_title": "Language saved",
    "lang.card.requested": "Requested",
    "lang.card.saved": "Saved",
    "lang.card.mismatch_title": "Language not saved",
    "lang.card.mismatch": (
        "The config kept a different value than the one requested — the write did not persist."
    ),
    "lang.card.applies": "Applies to transcripts, AI briefings and summaries.",
    "lang.card.change": "Change language",
    "lang.card.hint": "A press applies the language and shows the value read back.",
}

#: The languages this install's copy is written in, with the value the command accepts.
CHOICES: tuple[tuple[str, str], ...] = (
    ("Auto", "auto"),
    ("English", "English"),
    ("Русский", "Russian"),
    ("Français", "French"),
)


def _t(key: str, **fields: object) -> str:
    text = _FALLBACK.get(key, "")
    for name, value in fields.items():
        text = text.replace("{" + name + "}", str(value))
    return text


def _esc(value: object) -> str:
    return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def picker_card(
    *,
    current: str = "",
    choices: Sequence[tuple[str, str]] = CHOICES,
) -> Card:
    """The current language, the choices as presses, and what the setting affects."""
    shown = current or _t("lang.card.auto")
    card = Card(_t("lang.card.title"), glyph="🌍", meta=shown)
    card.add(
        Table(
            rows=[[Raw(_t("lang.card.current")), Raw(f"<b>{_esc(shown)}</b>")]],
            bordered=False,
        )
    )
    card.add(Para(_t("lang.card.applies"), style="italic"))

    rows: list[list[tuple[str, str, str | None]]] = []
    pair: list[tuple[str, str, str | None]] = []
    for label, value in choices:
        is_current = (value.lower() == (current or "auto").lower()) or (
            value == "auto" and not current
        )
        text = f"✅ {label}" if is_current else label
        pair.append((text, f"nav:cmd:/lang {value}", "success" if is_current else None))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    for row in rows:
        built = []
        for label, data, style in row:
            built.append(Button(label, data=data, style=style) if style else Button(label, data=data))
        card.buttons(*built)
    card.footer = _t("lang.card.hint")
    return card


def saved_card(*, requested: str, saved: str) -> Card:
    """A receipt: what was asked for beside what the config now holds."""
    shown = saved or _t("lang.card.auto")
    card = Card(_t("lang.card.saved_title"), glyph="🌍", meta=shown)
    card.add(
        Table(
            rows=[
                [Raw(_t("lang.card.requested")), Raw(_esc(requested))],
                [Raw(_t("lang.card.saved")), Raw(f"<b>{_esc(shown)}</b>")],
            ],
            bordered=False,
        )
    )
    card.buttons(Button(_t("lang.card.change"), data="nav:cmd:/lang", style="primary"))
    card.footer = _t("lang.card.applies")
    return card


def mismatch_card(*, requested: str, saved: str) -> Card:
    """The write did not land: say so rather than echo the request back as success."""
    card = Card(_t("lang.card.mismatch_title"), glyph="⚠️")
    card.add(Para(_t("lang.card.mismatch"), style="italic"))
    card.add(
        Table(
            rows=[
                [Raw(_t("lang.card.requested")), Raw(_esc(requested))],
                [Raw(_t("lang.card.saved")), Raw(_esc(saved or _t("lang.card.auto")))],
            ],
            bordered=False,
        )
    )
    card.buttons(Button(_t("lang.card.change"), data="nav:cmd:/lang", style="primary"))
    return card


__all__ = ["CHOICES", "mismatch_card", "picker_card", "saved_card"]
