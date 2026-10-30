"""One page of a provider's models as a card: assign with one tap, page in place.

The list was already paginated and already edited its own message, but it printed a bold
header, a "Page 3/6" line and then a column of one-per-row buttons — eight rows of
keyboard for eight models, with the current pick marked only by an emoji, and every
button the same grey.

The card keeps the callbacks it had (`mdl_sel_…`, `mdl_page_…`, `mdl_back_…`, `mdl_close`)
so the router is untouched, and makes three things visible that the flat list hid: the
tier's current model in the subtitle, the current pick styled and folded into the button
row it belongs to, and a sentence when assigning here would switch the tier's provider.
"""

from __future__ import annotations

from typing import Any

from navig.core.i18n import t
from navig.telegram.card_doc import Button, Card, Details, Para, PullQuote, Raw, esc

_FALLBACK = {
    "mdl.card.title": "Pick a model",
    "mdl.card.hint": "Tap a model to assign it to this tier.",
    "mdl.card.current": "Current",
    "mdl.card.switch": "This tier runs {provider} today — a model here switches it.",
    "mdl.card.none": "No models available for this provider.",
    "mdl.card.failed": "Could not load models for this provider right now.",
    "mdl.card.page": "Page {page}/{pages}",
    "mdl.card.count": "{count} models",
    "mdl.prev": "◀ Prev",
    "mdl.next": "Next ▶",
    "mdl.back": "← Back to tiers",
    "mdl.close": "✖ Close",
}

PER_ROW = 2


def _label(key: str, fallback: str = "") -> str:
    """Locale string when the install has it; `t` echoes the key when it does not."""
    try:
        value = t(key)
    except Exception:  # noqa: BLE001 — a missing catalogue is not a failed list
        value = ""
    if not value or value == key:
        return fallback or _FALLBACK.get(key, key)
    return value


def short_name(model: str) -> str:
    """`openrouter/xiaomi/mimo-v2.6-flash` -> `mimo-v2.6-flash`.

    Only the path is shortened: a `:7b` tag is part of the model's name, and dropping it
    turns `qwen-vl:7b` into a meaningless `7b`.
    """
    return (model or "").split("/")[-1] or "—"


def is_current_model(model: str, current: str) -> bool:
    """The router stores the tier's model bare (`mimo-v2.6-flash`) while the provider's
    catalogue lists it qualified (`openrouter/xiaomi/mimo-v2.6-flash`), so the match is on
    the name, not the whole string. A `:tag` still has to agree."""
    if not current or not model:
        return False
    return model == current or short_name(model) == short_name(current)


def model_buttons(
    models: list[str],
    *,
    provider_id: str,
    tier_code: str,
    page: int,
    start: int,
    current_model: str = "",
    per_row: int = PER_ROW,
) -> list[list[Button]]:
    """One button per model, paired, with the assigned model marked and styled."""
    buttons: list[Button] = []
    for offset, model in enumerate(models):
        index = start + offset
        marked = is_current_model(model, current_model)
        label = f"✅ {short_name(model)}" if marked else short_name(model)
        buttons.append(
            Button(
                label,
                data=f"mdl_sel_{provider_id}_{index}_{tier_code}_{page}",
                style="success" if marked else "",
            )
        )
    return [buttons[i : i + per_row] for i in range(0, len(buttons), per_row)]


def nav_row(*, provider_id: str, tier_code: str, page: int, total_pages: int) -> list[Button]:
    """Prev/Next only when there is somewhere to go; both disabled states are absent."""
    row: list[Button] = []
    if page > 0:
        row.append(
            Button(
                _label("mdl.prev", "◀ Prev"),
                data=f"mdl_page_{provider_id}_{tier_code}_{page - 1}",
            )
        )
    if page < total_pages - 1:
        row.append(
            Button(
                _label("mdl.next", "Next ▶"),
                data=f"mdl_page_{provider_id}_{tier_code}_{page + 1}",
            )
        )
    return row


def empty_card(
    *,
    tier_label: str,
    provider_emoji: str,
    provider_name: str,
    provider_id: str,
    failed: bool = False,
) -> Card:
    # The glyph supplies the 📝; repeating it in the title renders it twice.
    card = Card(f"{esc(tier_label)} — {provider_emoji} {esc(provider_name)}", glyph="📝")
    reason = _label(
        "mdl.card.failed" if failed else "mdl.card.none",
        "Could not load models for this provider right now."
        if failed
        else "No models available for this provider.",
    )
    card.add(PullQuote(reason))
    card.buttons(Button(_label("mdl.back", "← Back to tiers"), data=f"mdl_back_tiers_{provider_id}"))
    card.buttons(Button(_label("mdl.close", "✖ Close"), data="mdl_close"))
    return card


def model_list_card(
    *,
    tier_label: str,
    provider_emoji: str,
    provider_name: str,
    provider_id: str,
    tier_code: str,
    models: list[str],
    page: int,
    page_size: int,
    current_model: str = "",
    current_provider_name: str = "",
) -> Card:
    """The card for one page: the pick as buttons, the context as text."""
    total_pages = max(1, (len(models) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    page_models = list(models[start : start + page_size])

    meta = " · ".join(
        [
            _label("mdl.card.page", "Page {page}/{pages}")
            .replace("{page}", str(page + 1))
            .replace("{pages}", str(total_pages)),
            _label("mdl.card.count", "{count} models").replace("{count}", str(len(models))),
        ]
    )
    card = Card(
        f"{esc(tier_label)} — {provider_emoji} {esc(provider_name)}",
        glyph="📝",
        meta=meta,
    )
    card.add(Para(_label("mdl.card.hint", "Tap a model to assign it to this tier.")))

    if current_provider_name and not current_model:
        card.add(
            Para(
                Raw(
                    _label(
                        "mdl.card.switch",
                        "This tier runs {provider} today — a model here switches it.",
                    ).replace("{provider}", esc(current_provider_name))
                )
            )
        )

    for row in model_buttons(
        page_models,
        provider_id=provider_id,
        tier_code=tier_code,
        page=page,
        start=start,
        current_model=current_model,
    ):
        card.buttons(*row)

    if current_model:
        card.add(
            Details(
                _label("mdl.card.current", "Current"),
                body=(Para(Raw(f"<code>{esc(current_model)}</code>")),),
            )
        )

    row = nav_row(
        provider_id=provider_id, tier_code=tier_code, page=page, total_pages=total_pages
    )
    if row:
        card.buttons(*row)
    card.buttons(Button(_label("mdl.back", "← Back to tiers"), data=f"mdl_back_tiers_{provider_id}"))
    card.buttons(Button(_label("mdl.close", "✖ Close"), data="mdl_close"))
    return card


def build(*, models: list[str], page_size: int = 8, **kwargs: Any) -> tuple[str, str, list]:
    card = model_list_card(models=models, page_size=page_size, **kwargs)
    return card.classic_html(), card.rich_html(), card.keyboard()


__all__ = [
    "PER_ROW",
    "build",
    "empty_card",
    "is_current_model",
    "model_buttons",
    "model_list_card",
    "nav_row",
    "short_name",
]
