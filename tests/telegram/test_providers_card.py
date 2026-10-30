"""How ``/providers`` reads: a routing sheet, a state table, one action per provider.

The old answer stacked one button per provider under a few bold lines, so "is Ollama
running?" and "does Anthropic have a key?" were only answerable by reading an emoji in a
button label. These tests pin the card: two tables that state the facts, buttons that
still activate with one tap, and a press that edits the same message.
"""

from __future__ import annotations

import asyncio
from typing import Any

from navig.telegram import providers_card as pc

PROVIDERS = [
    {"id": "openrouter", "emoji": "🟢", "name": "OpenRouter", "state": "active"},
    {"id": "ollama", "emoji": "🦙", "name": "Ollama", "state": "offline"},
    {"id": "anthropic", "emoji": "🅰️", "name": "Anthropic", "state": "locked"},
    {
        "id": "bridge",
        "emoji": "⚡",
        "name": "Bridge",
        "state": "offline",
        "data": "prov_bridge_offline",
    },
]
ROUTES = [
    {"label": "⚡ Small talk", "model": "openrouter/xiaomi/mimo-v2.6-flash"},
    {"label": "🧠 Big tasks", "model": "openrouter/mimo-v2.6-flash"},
    {"label": "💻 Coding", "model": ""},
]


def _build(**kwargs: Any) -> tuple[str, str, list[list[dict]] | None]:
    kw: dict[str, Any] = {
        "providers": PROVIDERS,
        "routes": ROUTES,
        "active_name": "🟢 OpenRouter",
    }
    kw.update(kwargs)
    return pc.build(**kw)


def test_the_routing_sheet_states_the_model_without_its_vendor_path() -> None:
    _, rich, _ = _build()
    assert "<caption>Route</caption>" in rich
    assert "<td>⚡ Small talk</td><td><code>mimo-v2.6-flash</code></td>" in rich
    assert "xiaomi/" not in rich
    # An unset mode is a dash, never a blank cell that looks like a rendering bug.
    assert "<td>💻 Coding</td><td><code>—</code></td>" in rich


def test_every_provider_state_is_a_row_not_a_riddle_in_a_button() -> None:
    _, rich, _ = _build()
    assert "<caption>Providers</caption>" in rich
    assert "<th>Provider</th><th>State</th>" in rich
    assert "<td>🟢 OpenRouter</td><td><i>✅ active</i></td>" in rich
    assert "<td>🦙 Ollama</td><td><i>⏸ offline</i></td>" in rich
    assert "<td>🅰️ Anthropic</td><td><i>🔒 needs key</i></td>" in rich


def test_a_provider_that_cannot_run_opens_its_config_instead_of_activating() -> None:
    _, _, keyboard = _build()
    flat = {btn["text"]: btn["callback_data"] for row in keyboard or [] for btn in row}
    assert flat["🟢 OpenRouter ✅"] == "prov_openrouter"
    assert flat["🅰️ Anthropic 🔒"] == "prov_cfg_anthropic"
    assert flat["🦙 Ollama ⏸"] == "prov_cfg_ollama"
    assert flat["⚡ Bridge ⏸"] == "prov_bridge_offline"


def test_the_active_provider_is_the_only_coloured_button() -> None:
    rows = pc.provider_buttons(PROVIDERS)
    styles = [btn.style for row in rows for btn in row]
    assert styles.count("success") == 1
    assert styles[0] == "success"


def test_buttons_are_paired_so_the_card_stays_one_screen() -> None:
    _, _, keyboard = _build()
    widths = [len(row) for row in keyboard or []]
    assert widths[0] == 2, "providers pair up instead of one row each"


def test_vision_and_session_overrides_ride_the_sheet() -> None:
    _, rich, _ = _build(
        vision={"model": "openrouter/qwen-vl:7b", "reason": "cloud"},
        session_parts=["tiers=big", "vision=openrouter"],
    )
    assert "<td>Vision</td><td><code>qwen-vl:7b</code> <i>cloud</i></td>" in rich
    assert "<i>🔶 Session: tiers=big, vision=openrouter</i>" in rich


def test_raw_mode_says_so_on_the_card_and_on_its_button() -> None:
    _, rich, keyboard = _build(noai=True)
    assert "Next message: raw / no AI (one-shot)" in rich
    labels = [btn["text"] for row in keyboard or [] for btn in row]
    assert "✅ 🚫 No AI — raw mode" in labels


def test_the_classic_projection_carries_no_rich_only_tag() -> None:
    classic, rich, _ = _build(message_id=42)
    assert "<table" in rich and "<tg-button-row" in rich
    for tag in ("<table", "<caption", "<tg-button", "<footer", "<h2"):
        assert tag not in classic
    assert "🟢 OpenRouter" in classic and "⏸ offline" in classic
    # Back/Home only make sense when the card replaced another one.
    assert "Back" not in [b["text"] for row in _build()[2] or [] for b in row]


class _RichChannel:
    """A channel that can edit and send rich messages, like the real one can."""

    def __init__(self) -> None:
        self.rich: list[dict] = []
        self.edits: list[dict] = []
        self.plain: list[dict] = []

    def _rich_messages_enabled(self) -> bool:
        return True

    async def send_rich_message(self, chat_id, **kwargs):
        self.rich.append({"chat": chat_id, **kwargs})
        return {"ok": True, "message_id": 11, "rich_message": {"html": kwargs.get("html")}}

    async def edit_rich_message(self, chat_id, message_id, **kwargs):
        self.edits.append({"chat": chat_id, "message_id": message_id, **kwargs})
        return {"ok": True}

    async def send_message(self, chat_id, text, parse_mode="HTML", **kwargs):
        self.plain.append({"chat": chat_id, "text": text, **kwargs})
        return {"ok": True}

    async def _api_call(self, method, data):
        return {"ok": True}


def test_the_channel_sends_the_card_rich_and_keeps_the_words_as_fallback() -> None:
    from navig.telegram import card_transport

    channel = _RichChannel()
    card = pc.provider_hub_card(providers=PROVIDERS, routes=ROUTES, active_name="🟢 OpenRouter")
    asyncio.run(card_transport.send_card(channel, 7, card))  # type: ignore[arg-type]
    assert len(channel.rich) == 1
    sent = channel.rich[0]
    assert "<caption>Route</caption>" in sent["html"]
    assert "🟢 OpenRouter" in sent["fallback_html"]
    assert channel.plain == []


def test_a_press_edits_the_same_message_instead_of_sending_a_second() -> None:
    from navig.telegram import card_transport

    channel = _RichChannel()
    card = pc.provider_hub_card(
        providers=PROVIDERS, routes=ROUTES, active_name="🟢 OpenRouter", message_id=42
    )
    changed = asyncio.run(
        card_transport.edit_card(channel, 7, 42, card, keyboard=card.keyboard())  # type: ignore[arg-type]
    )
    assert changed is True
    assert channel.edits and channel.edits[0]["message_id"] == 42
    assert "<caption>Providers</caption>" in channel.edits[0]["html"]
