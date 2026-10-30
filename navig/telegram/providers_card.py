"""How ``/providers`` reads as a card.

The old answer was a stack of one-button rows under a handful of bold lines: every
provider was a button and nothing told you which ones were ready, locked or offline
without reading the label. This module states the routing facts as a sheet — what each
mode actually runs, and the state of every provider — and keeps the buttons for the one
thing a tap means: activate it.

Pure: the handler gathers the facts, this decides the shape.
"""

from __future__ import annotations

from typing import Any

from navig.telegram.card_doc import Button, Card, Para, Raw, Table, esc

_FALLBACK = {
    "providers.card.title": "AI Providers",
    "providers.card.caption": "Route",
    "providers.card.providers": "Providers",
    "providers.card.hint": "Tap a provider to activate it.",
    "providers.card.noai": "🚫 Next message: raw / no AI (one-shot)",
    "providers.card.state.active": "✅ active",
    "providers.card.state.ready": "ready",
    "providers.card.state.locked": "🔒 needs key",
    "providers.card.state.offline": "⏸ offline",
    "providers.card.bridge": "Bridge",
    "providers.card.bridge.online": "online",
    "providers.card.bridge.offline": "offline",
}

_STATE_KEY = {
    "active": "providers.card.state.active",
    "ready": "providers.card.state.ready",
    "locked": "providers.card.state.locked",
    "offline": "providers.card.state.offline",
}
_STATE_STYLE = {"active": "success", "ready": "primary", "locked": None, "offline": None}


def _label(key: str, fallback: str = "") -> str:
    text = ""
    try:
        from navig.core.i18n import t

        text = str(t(key) or "")
    except Exception:  # noqa: BLE001 - a missing catalog must not cost the card
        text = ""
    default = fallback or _FALLBACK.get(key, key)
    return default if text in ("", key) else text


def state_label(state: str) -> str:
    key = _STATE_KEY.get(state, "providers.card.state.ready")
    return _label(key, _FALLBACK.get(key, state))


def _short(model: str) -> str:
    """The model name without its vendor path — the mode sheet has no room for it."""
    name = (model or "").split("/")[-1]
    return name or "—"


def route_rows(routes: list[dict[str, str]], bridge: dict[str, Any] | None) -> list[list[Any]]:
    """``[label, model]`` rows for the sheet, plus the bridge line when it runs."""
    rows: list[list[Any]] = []
    for route in routes:
        model = str(route.get("model") or "")
        rows.append([str(route.get("label") or ""), Raw(f"<code>{esc(_short(model))}</code>")])
    if bridge and (bridge.get("online") or bridge.get("active")):
        state = (
            _label("providers.card.bridge.online", "online")
            if bridge.get("online")
            else _label("providers.card.bridge.offline", "offline")
        )
        rows.append(
            [
                _label("providers.card.bridge", "Bridge"),
                Raw(f"<i>{esc(state)}</i>"),
            ]
        )
    return rows


def provider_rows(providers: list[dict[str, str]]) -> list[list[Any]]:
    """``[name, state]`` rows — the table that replaces eight unexplained buttons."""
    return [
        [
            Raw(f"{esc(str(item.get('emoji') or ''))} {esc(str(item.get('name') or ''))}".strip()),
            Raw(f"<i>{esc(state_label(str(item.get('state') or 'ready')))}</i>"),
        ]
        for item in providers
    ]


def provider_buttons(providers: list[dict[str, str]], *, per_row: int = 2) -> list[list[Button]]:
    """Activation buttons, paired — a provider that cannot run still opens its config."""
    buttons: list[Button] = []
    for item in providers:
        state = str(item.get("state") or "ready")
        ident = str(item.get("id") or "")
        label = f"{item.get('emoji') or ''} {item.get('name') or ident}".strip()
        suffix = {"active": " ✅", "locked": " 🔒", "offline": " ⏸"}.get(state, "")
        data = str(item.get("data") or "")
        if not data:
            data = f"prov_cfg_{ident}" if state in ("locked", "offline") else f"prov_{ident}"
        buttons.append(Button(f"{label}{suffix}", data=data, style=_STATE_STYLE.get(state)))
    return [buttons[i : i + per_row] for i in range(0, len(buttons), per_row)]


def _action_rows(*, session: bool, message_id: bool, noai: bool = False) -> list[list[Button]]:
    noai_prefix = "✅ " if noai else ""
    rows: list[list[Button]] = [
        [
            Button("🔀 Hybrid", data="pu_hybrid"),
            Button(f"{noai_prefix}🚫 No AI — raw mode", data="prov_noai"),
        ],
        [
            Button("👁 Vision", data="pu_vision"),
            Button("🎙 Voice", data="pu_voice"),
            Button("📲 Messengers", data="open_messengers"),
        ],
    ]
    if session:
        rows.append([Button("🔄 Reset session", data="pu_reset_session")])
    if message_id:
        rows.append([Button("🔙 Back", data="nav:back"), Button("🏠 Home", data="nav:home")])
    rows.append([Button("✖ Close", data="prov_close")])
    return rows


def provider_hub_card(
    *,
    providers: list[dict[str, str]],
    routes: list[dict[str, str]],
    bridge: dict[str, Any] | None = None,
    active_name: str = "",
    session_parts: list[str] | None = None,
    vision: dict[str, str] | None = None,
    noai: bool = False,
    note: str = "",
    message_id: int | None = None,
) -> Card:
    """The ``/providers`` card: the routing facts as a sheet, the providers as rows."""
    session_parts = session_parts or []
    card = Card(
        _label("providers.card.title", "AI Providers"),
        glyph="🎛️",
        meta=(f"Current: {active_name}" if active_name else ""),
    )
    if session_parts:
        card.add(Para(f"🔶 Session: {', '.join(session_parts)}", style="italic"))

    sheet = route_rows(routes, bridge)
    if vision and vision.get("model"):
        reason = str(vision.get("reason") or "")
        sheet.append(
            [
                "Vision",
                Raw(
                    f"<code>{esc(_short(vision['model']))}</code>"
                    + (f" <i>{esc(reason)}</i>" if reason else "")
                ),
            ]
        )
    if sheet:
        card.add(Table(rows=sheet, caption=_label("providers.card.caption", "Route"), bordered=False))

    if providers:
        card.add(
            Table(
                header=["Provider", "State"],
                rows=provider_rows(providers),
                caption=_label("providers.card.providers", "Providers"),
                bordered=False,
            )
        )

    if noai:
        card.add(Para(_label("providers.card.noai", ""), style="italic"))
    if note:
        card.add(Para(esc(note), style="italic"))

    for row in provider_buttons(providers) + _action_rows(
        session=bool(session_parts), message_id=bool(message_id), noai=noai
    ):
        card.buttons(*row)
    card.footer = _label("providers.card.hint", "Tap a provider to activate it.")
    return card


def build(
    *,
    providers: list[dict[str, str]],
    routes: list[dict[str, str]],
    bridge: dict[str, Any] | None = None,
    active_name: str = "",
    session_parts: list[str] | None = None,
    vision: dict[str, str] | None = None,
    noai: bool = False,
    note: str = "",
    message_id: int | None = None,
) -> tuple[str, str, list[list[dict]] | None]:
    """``(classic_html, rich_html, keyboard)`` for the ``/providers`` answer."""
    card = provider_hub_card(
        providers=providers,
        routes=routes,
        bridge=bridge,
        active_name=active_name,
        session_parts=session_parts,
        vision=vision,
        noai=noai,
        note=note,
        message_id=message_id,
    )
    return card.classic_html(), card.rich_html(), card.keyboard()
