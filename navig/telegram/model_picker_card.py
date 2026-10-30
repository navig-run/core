"""The provider model picker as one card: what is assigned, what is on offer.

Before this module the picker was a wall of rows: a stack of ``<code>`` tier lines
in the text and one single-model button per row underneath. Twenty models meant
twenty rows of keyboard — a scroll, not a choice — and the tier assignment (the
thing the screen exists for) was buried in markup the reader had to decode.

Here the two facts get the two shapes that read fastest: the tier assignment is a
small key/value sheet, the models are a numbered catalogue. Numbering is not
decoration — every model button carries the same index, so "3" on the button is
"3" in the table and the reader can name the model they picked.

Everything else is unchanged on purpose: the callback payloads are the ones the
dispatcher already understands (``pmv_`` tier tab, ``pms_`` assign, ``prov_page_``
page turn, ``prov_activate_``/``prov_deactivate_``), so a card sent yesterday is
still pressable today.
"""

from __future__ import annotations

from typing import Any

from navig.telegram.card_doc import Button, Card, Para, Raw, Table

__all__ = ["build", "model_rows", "model_buttons", "picker_card", "tier_rows"]

_FALLBACK = {
    "picker.assign": "Assign a model to a tier",
    "picker.tiers": "Tiers",
    "picker.models": "Models",
    "picker.hint": "Tap a model to assign it to {tier}.",
    "picker.none": "—",
    "picker.prev": "⬅️ Prev",
    "picker.next": "Next ➡️",
    "picker.back": "🔙 Back",
    "picker.home": "🏠 Home",
    "picker.close": "✖ Close",
    "picker.activate": "🚀 Activate {name}",
    "picker.deactivate": "🔴 Deactivate {name}",
    "picker.page": "📄 {page}/{total}",
}

_TIER_EMOJI = {"small": "⚡", "big": "🧠", "coder_big": "💻"}
_TIER_LABEL = {"small": "Small", "big": "Big", "coder_big": "Code"}
_TIER_CODE = {"small": "s", "big": "b", "coder_big": "c"}


def _label(key: str, fallback: str = "") -> str:
    try:
        from navig.core.i18n import t

        value = t(key)
    except Exception:  # noqa: BLE001 — a missing locale must not blank a card
        value = ""
    if value == key:  # i18n echoes the key when it has no translation
        value = ""
    return value or _FALLBACK.get(key) or fallback


def short_model(model: str) -> str:
    """The human part of a model id: vendor path dropped, ``:tag`` kept."""
    return model.split("/")[-1] or "—"


def tier_rows(current: dict[str, str | None], *, selected: str = "small") -> list[list[Any]]:
    """One row per tier: its label, the model assigned there, and a marker for the tab."""
    rows: list[list[Any]] = []
    for tier in ("small", "big", "coder_big"):
        label = f"{_TIER_EMOJI[tier]} {_TIER_LABEL[tier]}"
        cell = f"<b>{label}</b> ▸" if tier == selected else label
        model = current.get(tier)
        value = Raw(f"<code>{short_model(model)}</code>") if model else _label("picker.none", "—")
        rows.append([Raw(cell), value])
    return rows


def model_rows(models: list[str], *, start: int = 0) -> list[list[Any]]:
    """The page as a numbered catalogue: ``1 | <code>model-name</code>``."""
    return [[str(start + offset + 1), Raw(f"<code>{short_model(model)}</code>")] for offset, model in enumerate(models)]


def model_buttons(
    prov_id: str,
    models: list[str],
    *,
    start: int = 0,
    selected_tier: str = "s",
    page: int = 0,
    per_row: int = 2,
) -> list[list[dict[str, Any]]]:
    """Model buttons, two per row, each labelled with its catalogue number."""
    buttons: list[dict[str, Any]] = []
    for offset, model in enumerate(models):
        index = start + offset
        label = f"{index + 1}. {short_model(model)}"
        if len(label) > 44:
            label = label[:41] + "…"
        buttons.append(
            {
                "text": label,
                "callback_data": f"pms_{prov_id}_{index}_{selected_tier}_{page}",
            }
        )
    return [buttons[i : i + per_row] for i in range(0, len(buttons), per_row)]


def button_rows(
    *,
    prov_id: str,
    name: str,
    models: list[str] | None = None,
    start: int = 0,
    page: int = 0,
    total_pages: int = 1,
    selected_tier: str = "small",
    show_models: bool = True,
    active: bool = False,
) -> list[list[dict[str, Any]]]:
    """The action rows as raw callback dicts — the same rows for body and keyboard."""
    rows: list[list[dict[str, Any]]] = [
        [
            {
                "text": f"{_TIER_EMOJI[tier]} {_TIER_LABEL[tier]}",
                "callback_data": f"pmv_{prov_id}_{_TIER_CODE[tier]}_{page}",
                **({"style": "primary"} if tier == selected_tier else {}),
            }
            for tier in ("small", "big", "coder_big")
        ]
    ]
    tier_code = _TIER_CODE.get(selected_tier, "s")
    if show_models:
        rows.extend(
            model_buttons(
                prov_id, models or [], start=start, selected_tier=tier_code, page=page
            )
        )

    if show_models and total_pages > 1:
        nav: list[dict[str, Any]] = []
        if page > 0:
            nav.append(
                {
                    "text": _label("picker.prev", "⬅️ Prev"),
                    "callback_data": f"prov_page_{prov_id}_{tier_code}_{page - 1}",
                }
            )
        nav.append(
            {
                "text": _label("picker.page", "📄 {page}/{total}")
                .replace("{page}", str(page + 1))
                .replace("{total}", str(total_pages)),
                "callback_data": "prov_noop",
            }
        )
        if page < total_pages - 1:
            nav.append(
                {
                    "text": _label("picker.next", "Next ➡️"),
                    "callback_data": f"prov_page_{prov_id}_{tier_code}_{page + 1}",
                }
            )
        rows.append(nav)

    if active:
        rows.append(
            [
                {
                    "text": _label("picker.deactivate", "🔴 Deactivate {name}").replace(
                        "{name}", name
                    ),
                    "callback_data": f"prov_deactivate_{prov_id}",
                    "style": "danger",
                }
            ]
        )
    else:
        rows.append(
            [
                {
                    "text": _label("picker.activate", "🚀 Activate {name}").replace(
                        "{name}", name
                    ),
                    "callback_data": f"prov_activate_{prov_id}",
                    "style": "success",
                }
            ]
        )
    rows.append(
        [
            {"text": _label("picker.back", "🔙 Back"), "callback_data": "nav:providers"},
            {"text": _label("picker.home", "🏠 Home"), "callback_data": "nav:home"},
        ]
    )
    rows.append([{"text": _label("picker.close", "✖ Close"), "callback_data": "prov_close"}])
    return rows


def picker_card(
    *,
    prov_id: str,
    name: str,
    emoji: str = "",
    models: list[str] | None = None,
    start: int = 0,
    page: int = 0,
    total_pages: int = 1,
    current: dict[str, str | None] | None = None,
    selected_tier: str = "small",
    show_models: bool = True,
    key_hint: str = "",
    active: bool = False,
) -> Card:
    """The whole screen: tier sheet, model catalogue, actions in the bubble."""
    current = current or {}
    page_models = models or []
    title = f"{emoji} {name}".strip() if emoji else name
    card = Card(title, meta=_label("picker.assign", "Assign a model to a tier"))

    if key_hint:
        card.add(Para(f"⚠️ {key_hint}", style="italic"))

    card.add(
        Table(
            header=["Tier", "Model"],
            rows=tier_rows(current, selected=selected_tier),
            caption=_label("picker.tiers", "Tiers"),
            bordered=False,
        )
    )

    if show_models and page_models:
        caption = _label("picker.models", "Models")
        if total_pages > 1:
            caption = f"{caption} · {page + 1}/{total_pages}"
        card.add(
            Table(header=["#", "Model"], rows=model_rows(page_models, start=start), caption=caption)
        )

    tier_name = _TIER_LABEL.get(selected_tier, selected_tier)
    hint = _label("picker.hint", "Tap a model to assign it to {tier}.").replace("{tier}", tier_name)
    card.add(Para(hint, style="italic"))

    # The rich body draws the same rows the classic reply keyboard carries, so a
    # downgrade never loses an action and a press never lands on a missing button.
    for row in button_rows(
        prov_id=prov_id,
        name=name,
        models=page_models,
        start=start,
        page=page,
        total_pages=total_pages,
        selected_tier=selected_tier,
        show_models=show_models,
        active=active,
    ):
        card.buttons(
            *[
                Button(item["text"], data=item["callback_data"], style=str(item.get("style") or ""))
                for item in row
            ]
        )
    return card


def build(
    *,
    prov_id: str,
    name: str,
    emoji: str = "",
    models: list[str] | None = None,
    start: int = 0,
    page: int = 0,
    total_pages: int = 1,
    current: dict[str, str | None] | None = None,
    selected_tier: str = "s",
    show_models: bool = True,
    key_hint: str = "",
    active: bool = False,
) -> tuple[str, str, list[list[dict[str, Any]]]]:
    """Return ``(classic_html, rich_html, keyboard)`` for the picker screen."""
    tier_name = {"s": "small", "b": "big", "c": "coder_big"}.get(selected_tier, "small")
    card = picker_card(
        prov_id=prov_id,
        name=name,
        emoji=emoji,
        models=models,
        start=start,
        page=page,
        total_pages=total_pages,
        current=current,
        selected_tier=tier_name,
        show_models=show_models,
        key_hint=key_hint,
        active=active,
    )
    rows = button_rows(
        prov_id=prov_id,
        name=name,
        models=models,
        start=start,
        page=page,
        total_pages=total_pages,
        selected_tier=tier_name,
        show_models=show_models,
        active=active,
    )
    return card.classic_html(), card.rich_html(), rows


