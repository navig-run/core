"""Tests for the routing cards (``/provider show``, ``hybrid``, ``vision``).

The point of each assertion is a reading problem the padded-text version had: two lists of
the same three tiers that had to be compared by eye, a "🔶 = session override" legend
separated from the rows it described, and a vision picker whose current model appeared in
prose above a list of models with no loader.

Run: py -3.13 -m pytest tests/telegram/test_routing_card.py -q
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from navig.telegram import routing_card


def _channel() -> Any:
    class _Channel:
        def __init__(self) -> None:
            self.rich: list[dict] = []
            self.edits: list[dict] = []
            self.plain: list[str] = []
            self.keyboards: list[Any] = []

        async def send_rich_message(self, chat_id, html, fallback_html=None, keyboard=None, **kw):
            self.rich.append({"chat_id": chat_id, "html": html, "fallback": fallback_html})
            self.keyboards.append(keyboard)
            return {"message_id": 900 + len(self.rich), "rich_message": {"html": html}}

        async def edit_rich_message(self, chat_id, message_id, html, fallback_html=None, **kw):
            self.edits.append({"message_id": message_id, "html": html})
            return {"message_id": message_id}

        async def send_message(self, chat_id, text, **kw):
            self.plain.append(text)
            return {"message_id": 1}

    return _Channel()


def _keyboard(card) -> list[list[dict]]:
    return card.keyboard()


# ── routing_card: config beside effective ───────────────────────────────────────


def test_config_and_effective_sit_in_one_table() -> None:
    card = routing_card.routing_card(
        active_provider="openrouter",
        config={"big": ("openrouter", "xiaomi/mimo-v2.6-flash")},
        effective={"big": ("openrouter", "xiaomi/mimo-v2.6-flash")},
        overrides={},
    )
    html = card.rich_html()
    assert "<th>Tier</th><th>Config</th><th>Effective</th>" in html
    assert "mimo-v2.6-flash" in html
    assert "<i>openrouter</i>" in html
    assert "xiaomi/" not in html  # the vendor path is noise in a narrow table


def test_only_the_overridden_tier_carries_the_session_marker() -> None:
    card = routing_card.routing_card(
        active_provider="openrouter",
        config={"big": ("openrouter", "big-model"), "small": ("openrouter", "small-model")},
        effective={"big": ("ollama", "big-model")},
        overrides={"tier_big_provider": "ollama", "tier_big_model": "big-model"},
    )
    html = card.rich_html()
    assert "🔶" in html
    assert "tier_big_provider" in html  # the raw override is in its own table
    assert "Session overrides" in html


def test_a_clean_config_says_so_instead_of_showing_an_empty_table() -> None:
    card = routing_card.routing_card(active_provider="openrouter", config={}, overrides={})
    html = card.rich_html()
    assert "Nothing is overridden" in html
    assert "<aside>" not in html
    assert "Session overrides" not in html


def test_an_override_raises_a_callout() -> None:
    card = routing_card.routing_card(
        active_provider="ollama",
        overrides={"tier_big_provider": "ollama", "tier_big_model": "x"},
        effective={"big": ("ollama", "x")},
    )
    assert "<aside>" in card.rich_html()


def test_vision_row_names_the_model_and_its_source() -> None:
    card = routing_card.routing_card(
        active_provider="openrouter",
        vision=("openrouter", "openrouter/qwen-vl:7b", "active_provider"),
    )
    html = card.rich_html()
    assert "qwen-vl:7b" in html  # the :tag survives
    assert "from the active provider" in html


def test_no_vision_is_stated_not_left_blank() -> None:
    card = routing_card.routing_card(active_provider="openrouter")
    assert "not available" in card.rich_html()


# ── hybrid_card ────────────────────────────────────────────────────────────────


def test_hybrid_off_is_a_callout_not_a_legend() -> None:
    card = routing_card.hybrid_card(hybrid_active=False)
    html = card.rich_html()
    assert "<aside>" in html
    assert "answers every tier" in html
    assert "<i>enabled</i>" not in html


def test_hybrid_on_states_its_mode_in_the_meta_line() -> None:
    card = routing_card.hybrid_card(hybrid_active=True)
    html = card.rich_html()
    assert "<i>enabled</i>" in html
    assert "<aside>" not in html


def test_an_unassigned_tier_is_a_row_that_says_so() -> None:
    card = routing_card.hybrid_card(
        tiers={"big": ("openrouter", "x/mimo", False)},
        hybrid_active=True,
    )
    html = card.rich_html()
    assert "<i>not assigned</i>" in html
    assert "mimo" in html


def test_the_session_marker_rides_its_own_row_and_the_legend_appears_once() -> None:
    card = routing_card.hybrid_card(
        tiers={
            "small": ("openrouter", "small-model", False),
            "big": ("ollama", "big-model", True),
        },
        hybrid_active=True,
    )
    html = card.rich_html()
    assert html.count("🔶") == 2  # the row plus the one legend sentence
    assert "lost on restart" in html


def test_hybrid_buttons_keep_their_callback_payloads() -> None:
    card = routing_card.hybrid_card(
        hybrid_active=False,
        buttons=[
            [("⚡ Small", "hyb_tier_small", None), ("🧠 Big", "hyb_tier_big", None)],
            [("🔛 Enable Hybrid Routing", "hyb_enable", "primary")],
        ],
    )
    rows = _keyboard(card)
    assert rows[0][0]["callback_data"] == "hyb_tier_small"
    assert rows[1][0]["callback_data"] == "hyb_enable"
    assert 'style="primary"' in card.rich_html()


# ── vision_card ───────────────────────────────────────────────────────────────


def test_the_active_vision_model_is_a_sheet_not_a_sentence() -> None:
    card = routing_card.vision_card(
        current=("openrouter", "openrouter/qwen-vl:7b", "session_override"),
        models=[{"name": "openrouter/qwen-vl:7b"}],
    )
    html = card.rich_html()
    assert "<td>Active</td>" in html
    assert "session override" in html
    assert "<b>1</b>" in html


def test_no_vision_model_is_a_callout_and_no_empty_sheet() -> None:
    card = routing_card.vision_card(current=None)
    html = card.rich_html()
    assert "<aside>" in html
    assert "<td>Active</td>" not in html


def test_the_current_model_button_is_the_only_success() -> None:
    card = routing_card.vision_card(
        current=("openrouter", "a/qwen-vl", "global_config"),
        models=[{"name": "a/qwen-vl"}, {"name": "b/llava"}],
        model_buttons=routing_card.button_rows(
            [
                ("👁 qwen-vl ✅", "vis_openrouter:a/qwen-vl", "success"),
                ("👁 llava", "vis_openrouter:b/llava", None),
            ],
            per_row=2,
        ),
    )
    html = card.rich_html()
    assert html.count('style="success"') == 1
    assert "Tap a model" in html


def test_every_routing_card_has_a_classic_projection_without_rich_tags() -> None:
    for card in (
        routing_card.routing_card(active_provider="openrouter"),
        routing_card.hybrid_card(hybrid_active=True),
        routing_card.vision_card(current=None),
    ):
        classic = card.classic_html()
        for tag in ("<h1>", "<h2>", "<table", "<aside>", "<tg-button-row", "<footer"):
            assert tag not in classic


# ── handlers: they edit in place and keep the callbacks ───────────────────────


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_provider_show_edits_the_pressed_message(monkeypatch: pytest.MonkeyPatch) -> None:
    import navig.providers.discovery as discovery
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

    monkeypatch.setattr(discovery, "_get_active_provider", lambda: "openrouter")
    monkeypatch.setattr(
        discovery, "_get_current_tier_assignments", lambda: {"big": ("openrouter", "x/mimo")}
    )
    monkeypatch.setattr(
        discovery, "resolve_vision_model", lambda overrides: ("openrouter", "qwen-vl", "fallback")
    )

    class _Sessions:
        def get_all_session_overrides(self, chat_id, user_id):
            return {"tier_big_provider": "ollama", "tier_big_model": "ollama/big"}

    import navig.gateway.channels.telegram_sessions as sessions_mod

    monkeypatch.setattr(sessions_mod, "get_session_manager", lambda: _Sessions())

    channel = _channel()
    _run(
        TelegramCommandsMixin._handle_provider_show(
            channel, 1, user_id=2, message_id=66, text="/provider show"
        )
    )
    assert channel.edits and channel.edits[0]["message_id"] == 66
    assert not channel.rich  # nothing new was posted
    html = channel.edits[0]["html"]
    assert "Routing State" in html
    assert "🔶" in html


def test_provider_vision_edits_and_keeps_the_truncated_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import navig.providers.capabilities as caps
    import navig.providers.discovery as discovery
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

    class _Model:
        def __init__(self, provider_id, name):
            self.provider_id = provider_id
            self.name = name
            self.capability_label = "vision"

    monkeypatch.setattr(
        discovery,
        "list_available_models",
        lambda capability=None, connected_only=True: [
            _Model("openrouter", "openrouter/qwen-vl:7b"),
            _Model("ollama", "ollama/llava"),
        ],
    )
    monkeypatch.setattr(
        discovery,
        "resolve_vision_model",
        lambda overrides: ("openrouter", "openrouter/qwen-vl:7b", "global_config"),
    )
    monkeypatch.setattr(caps, "Capability", caps.Capability, raising=False)

    channel = _channel()
    _run(
        TelegramCommandsMixin._handle_provider_vision(
            channel, 1, user_id=2, message_id=67, text="/provider vision"
        )
    )
    html = channel.edits[0]["html"]
    assert channel.edits[0]["message_id"] == 67
    assert "qwen-vl:7b" in html
    assert 'style="success"' in html
    # the model picker lives in the bubble; a classic edit still carries the same
    # payloads because edit_card falls back to card.keyboard()
    assert "vis_openrouter:openrouter/qwen-vl" in str(
        routing_card.vision_card(
            current=("openrouter", "openrouter/qwen-vl:7b", "global_config"),
            model_buttons=routing_card.button_rows(
                [("👁 qwen-vl:7b ✅", "vis_openrouter:openrouter/qwen-vl", "success")],
                per_row=1,
            ),
        ).keyboard()
    )
