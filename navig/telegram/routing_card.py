"""Cards for the routing family: the whole picture, hybrid tiers, and the vision picker.

Three surfaces answer "who is going to answer me": ``/provider show`` (config vs session
vs effective), ``/provider hybrid`` (a provider per tier), and ``/provider vision`` (which
model reads images). They were three column-padded messages with grey keyboards under
them; here they are three cards whose buttons are the only controls, and whose facts sit
in tables you can compare down a column instead of reading twice.

Callbacks stay exactly as the dispatcher already routes them (``hyb_*``, ``vis_*``,
``pu_*``, ``nav:*``, ``prov_close``), so these builders take ready ``(label, data,
style)`` rows instead of knowing about payload encoding — the vision picker has to
truncate its 64-byte payload, and that stays with the handler.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from navig.core.i18n import t as _t
from navig.telegram.card_doc import Button, Card, Para, PullQuote, Raw, Table

_FALLBACK = {
    "routing.card.title": "Routing State",
    "routing.card.config": "Config",
    "routing.card.effective": "Effective",
    "routing.card.overrides": "Session overrides",
    "routing.card.active": "Active provider",
    "routing.card.vision": "Vision",
    "routing.card.none": "none",
    "routing.card.unavailable": "not available",
    "routing.card.overridden": (
        "Session overrides are live — reset them to fall back to the saved config."
    ),
    "routing.card.clean": "Nothing is overridden — the saved config is what answers.",
    "routing.card.hint": "Everything here changes where your next message goes.",
    "hybrid.card.title": "Hybrid Routing",
    "hybrid.card.tier": "Tier",
    "hybrid.card.assigned": "Assigned",
    "hybrid.card.enabled": "enabled",
    "hybrid.card.disabled": "disabled",
    "hybrid.card.off": (
        "Hybrid routing is off — the single active provider answers every tier."
    ),
    "hybrid.card.hint": "Tap a tier to assign a provider to it.",
    "hybrid.card.overridden": "🔶 marks a session override (lost on restart).",
    "vision.card.title": "Vision Model",
    "vision.card.active": "Active",
    "vision.card.source": "Source",
    "vision.card.available": "Available",
    "vision.card.none": "No vision model — connect a provider that can read images.",
    "vision.card.pick": "Tap a model to use it for image analysis:",
    "vision.card.hint": "Images you send go to whichever model is marked ✅.",
    "vision.card.only": "only one connected provider offers vision.",
    "vision.card.clear": "Clear vision override",
}

_SOURCE_FALLBACK = {
    "session_override": "session override",
    "global_config": "saved in config",
    "active_provider": "from the active provider",
    "fallback": "auto-detected fallback",
}

TIERS: tuple[tuple[str, str, str], ...] = (
    ("small", "⚡", "Small"),
    ("big", "🧠", "Big"),
    ("coder_big", "💻", "Code"),
)


def _label(key: str, fallback: str = "") -> str:
    """Translated label, or the English default when i18n has nothing.

    ``i18n.t`` answers the key itself when a locale lacks it, so a value equal to the key
    counts as missing — otherwise every card would print ``routing.card.title``.
    """
    try:
        value = _t(key)
    except Exception:  # noqa: BLE001 - a missing i18n must never break a card
        value = ""
    if not value or value == key:
        return _FALLBACK.get(key) or fallback
    return value


label = _label  # public alias: handlers pass translated labels to their action rows


def _short(model: str) -> str:
    """Last path segment of a model id, tags kept: ``x/y/qwen-vl:7b`` -> ``qwen-vl:7b``."""
    return (model or "").split("/")[-1] or "—"


def button_rows(
    items: Iterable[tuple[str, str] | tuple[str, str, str | None]],
    *,
    per_row: int = 2,
) -> list[list[tuple[str, str, str | None]]]:
    """Normalise ``(label, data[, style])`` items into button rows of *per_row*."""
    flat: list[tuple[str, str, str | None]] = []
    for item in items:
        if len(item) == 2:
            item_label, data = item  # type: ignore[misc]
            flat.append((item_label, data, None))
        else:
            item_label, data, style = item  # type: ignore[misc]
            flat.append((item_label, data, style))
    size = max(1, per_row)
    return [flat[i : i + size] for i in range(0, len(flat), size)]


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


def _tier_cell(tier: str) -> str:
    for key, emoji, label in TIERS:
        if key == tier:
            return f"{emoji} {label}"
    return tier


def routing_card(
    *,
    active_provider: str = "",
    config: dict[str, tuple[str, str]] | None = None,
    effective: dict[str, tuple[str, str]] | None = None,
    overrides: dict[str, str] | None = None,
    vision: tuple[str, str, str] | None = None,
    buttons: Sequence[Sequence[tuple[str, str, str | None]]] = (),
) -> Card:
    """The whole routing picture: config beside what is actually in force.

    Two readings of the same three tiers used to be two separate lists; side by side they
    answer the only question worth asking — which rows changed.
    """
    config = config or {}
    effective = effective or {}
    overrides = overrides or {}

    active = active_provider or _label("routing.card.none", "none")
    card = Card(_label("routing.card.title", "Routing State"), glyph="📊", meta=f"{active}")

    facts: list[list[Any]] = [
        [Raw(f"🎯 {_label('routing.card.active', 'Active provider')}"), Raw(f"<code>{active}</code>")]
    ]
    if vision:
        v_prov, v_model, v_reason = vision
        reason = _SOURCE_FALLBACK.get(v_reason, v_reason)
        facts.append(
            [
                Raw(f"👁 {_label('routing.card.vision', 'Vision')}"),
                Raw(f"<code>{_short(v_model)}</code> <i>{v_prov} · {reason}</i>"),
            ]
        )
    else:
        facts.append(
            [
                Raw(f"👁 {_label('routing.card.vision', 'Vision')}"),
                Raw(f"<i>{_label('routing.card.unavailable', 'not available')}</i>"),
            ]
        )
    card.add(Table(rows=facts, bordered=False))

    tiers: list[list[Any]] = []
    session_tiers = {
        key[len("tier_") : -len("_model")] for key in overrides if key.endswith("_model")
    }
    for key, _emoji, _label_name in TIERS:
        cfg = config.get(key)
        eff = effective.get(key) or cfg
        if cfg:
            cfg_cell = f"<code>{_short(cfg[1])}</code> <i>{cfg[0]}</i>"
        else:
            cfg_cell = "—"
        if eff:
            mark = " 🔶" if key in session_tiers else ""
            eff_cell = f"<code>{_short(eff[1])}</code> <i>{eff[0]}</i>{mark}"
        else:
            eff_cell = "—"
        tiers.append(
            [
                Raw(_tier_cell(key)),
                Raw(cfg_cell),
                Raw(eff_cell),
            ]
        )
    card.add(
        Table(
            header=[
                _label("hybrid.card.tier", "Tier"),
                _label("routing.card.config", "Config"),
                _label("routing.card.effective", "Effective"),
            ],
            rows=tiers,
            bordered=False,
        )
    )

    if overrides:
        rows = [
            [Raw(f"<code>{key}</code>"), Raw(f"<code>{value}</code>")]
            for key, value in sorted(overrides.items())
        ]
        card.add(
            Table(
                rows=rows,
                caption=_label("routing.card.overrides", "Session overrides"),
                bordered=False,
            )
        )
        card.add(PullQuote(_label("routing.card.overridden", "")))
    else:
        card.add(Para(_label("routing.card.clean", ""), style="italic"))

    _add_buttons(card, buttons)
    card.footer = _label("routing.card.hint", "")
    return card


def hybrid_card(
    *,
    tiers: dict[str, tuple[str, str, bool]] | None = None,
    hybrid_active: bool = False,
    buttons: Sequence[Sequence[tuple[str, str, str | None]]] = (),
) -> Card:
    """One row per tier, with the session marker where a row is temporary."""
    tiers = tiers or {}
    meta = _label("hybrid.card.enabled", "enabled") if hybrid_active else _label(
        "hybrid.card.disabled", "disabled"
    )
    card = Card(_label("hybrid.card.title", "Hybrid Routing"), glyph="🔀", meta=meta)

    if not hybrid_active:
        card.add(PullQuote(_label("hybrid.card.off", "")))

    rows: list[list[Any]] = []
    for key, _emoji, _name in TIERS:
        assigned = tiers.get(key)
        if assigned:
            provider, model, is_session = assigned
            mark = " 🔶" if is_session else ""
            rows.append(
                [
                    Raw(_tier_cell(key)),
                    Raw(f"<code>{_short(model)}</code> <i>{provider}</i>{mark}"),
                ]
            )
        else:
            rows.append([Raw(_tier_cell(key)), Raw("<i>not assigned</i>")])
    card.add(
        Table(
            header=[_label("hybrid.card.tier", "Tier"), _label("hybrid.card.assigned", "Assigned")],
            rows=rows,
            bordered=False,
        )
    )

    if any(bool(t[2]) for t in tiers.values() if t):
        card.add(Para(_label("hybrid.card.overridden", ""), style="italic"))

    _add_buttons(card, buttons)
    card.footer = _label("hybrid.card.hint", "")
    return card


def vision_card(
    *,
    current: tuple[str, str, str] | None = None,
    models: Sequence[dict[str, str]] = (),
    has_override: bool = False,
    buttons: Sequence[Sequence[tuple[str, str, str | None]]] = (),
    model_buttons: Sequence[Sequence[tuple[str, str, str | None]]] = (),
) -> Card:
    """What reads images now, and the short list of what could."""
    meta = ""
    if current:
        provider, model, reason = current
        meta = f"{_short(model)} · {provider}"

    card = Card(_label("vision.card.title", "Vision Model"), glyph="👁", meta=meta)

    if current:
        provider, model, reason = current
        card.add(
            Table(
                rows=[
                    [
                        Raw(_label("vision.card.active", "Active")),
                        Raw(f"<code>{_short(model)}</code> <i>{provider}</i>"),
                    ],
                    [
                        Raw(_label("vision.card.source", "Source")),
                        Raw(f"<i>{_SOURCE_FALLBACK.get(reason, reason)}</i>"),
                    ],
                    [
                        Raw(_label("vision.card.available", "Available")),
                        Raw(f"<b>{len(models)}</b>"),
                    ],
                ],
                bordered=False,
            )
        )
    else:
        card.add(PullQuote(_label("vision.card.none", "")))

    if model_buttons:
        card.add(Para(_label("vision.card.pick", ""), style="italic"))
        _add_buttons(card, model_buttons)

    _add_buttons(card, buttons)
    card.footer = _label("vision.card.hint", "")
    return card


def build(
    card: Card,
) -> tuple[str, str, list[list[dict]] | None]:
    """``(classic_html, rich_html, keyboard)`` for any card built above."""
    return card.classic_html(), card.rich_html(), card.keyboard()


__all__ = [
    "TIERS",
    "build",
    "button_rows",
    "hybrid_card",
    "routing_card",
    "vision_card",
]

