"""The model list as a card: the pick in the bubble, the context in the words."""

from __future__ import annotations

import pytest

from navig.telegram import model_list_card as mlc

MODELS = [
    "openrouter/xiaomi/mimo-v2.6-flash",
    "openrouter/anthropic/claude-sonnet-4",
    "openrouter/qwen/qwen-vl:7b",
    "openrouter/meta/llama-3.3-70b",
    "openrouter/google/gemini-2.5-pro",
    "openrouter/deepseek/deepseek-v3",
    "openrouter/mistral/large",
    "openrouter/openai/gpt-5",
    "openrouter/x/extra",
]


def _card(**overrides):
    kwargs = dict(
        tier_label="Small",
        provider_emoji="🟢",
        provider_name="OpenRouter",
        provider_id="openrouter",
        tier_code="s",
        models=MODELS,
        page=0,
        page_size=8,
        current_model="openrouter/xiaomi/mimo-v2.6-flash",
    )
    kwargs.update(overrides)
    return mlc.model_list_card(**kwargs)


def test_every_model_on_the_page_is_a_button_in_the_bubble() -> None:
    html = _card().rich_html()
    assert "<h2>📝 Small — 🟢 OpenRouter</h2>" in html
    assert html.count('<tg-button type="callback_data"') == 8 + 3  # models, Next, back, close
    assert "<tg-button-row" in html


def test_the_assigned_model_is_the_only_styled_one_and_says_so() -> None:
    html = _card().rich_html()
    assert html.count('style="success"') == 1
    assert "✅ mimo-v2.6-flash" in html


def test_a_colon_tag_survives_the_short_name() -> None:
    """`qwen-vl:7b` is a name, not a version suffix to strip."""
    assert mlc.short_name("openrouter/qwen/qwen-vl:7b") == "qwen-vl:7b"
    assert mlc.short_name("openrouter/xiaomi/mimo-v2.6-flash") == "mimo-v2.6-flash"
    assert mlc.short_name("") == "—"


def test_a_bare_router_model_marks_the_qualified_catalogue_entry() -> None:
    """The router stores `mimo-v2.6-flash`; the provider lists it with its path."""
    assert mlc.is_current_model("openrouter/xiaomi/mimo-v2.6-flash", "mimo-v2.6-flash")
    assert mlc.is_current_model("openrouter/qwen/qwen-vl:7b", "qwen-vl:7b")
    assert not mlc.is_current_model("openrouter/qwen/qwen-vl:7b", "qwen-vl")
    assert not mlc.is_current_model("openrouter/a/b", "")


def test_the_qualified_entry_is_marked_when_the_router_stores_it_bare() -> None:
    html = _card(current_model="mimo-v2.6-flash").rich_html()
    assert html.count('style="success"') == 1
    assert "✅ mimo-v2.6-flash" in html


def test_the_meta_states_the_page_and_the_count() -> None:
    html = _card().rich_html()
    assert "Page 1/2 · 9 models" in html


def test_the_current_model_is_folded_not_shouted() -> None:
    html = _card().rich_html()
    assert "<details><summary>Current</summary>" in html
    assert "<code>openrouter/xiaomi/mimo-v2.6-flash</code>" in html


def test_paging_buttons_appear_only_where_there_is_somewhere_to_go() -> None:
    keyboard = _card(page=0).keyboard()
    texts = [cell["text"] for row in keyboard for cell in row]
    assert "Next ▶" in texts
    assert "◀ Prev" not in texts

    keyboard = _card(page=1).keyboard()
    texts = [cell["text"] for row in keyboard for cell in row]
    assert "◀ Prev" in texts
    assert "Next ▶" not in texts
    assert "extra" in texts  # page 2 holds the ninth model


def test_a_single_page_offers_no_paging_at_all() -> None:
    keyboard = _card(models=MODELS[:3], current_model="").keyboard()
    texts = [cell["text"] for row in keyboard for cell in row]
    assert "Next ▶" not in texts and "◀ Prev" not in texts


def test_the_callbacks_are_the_ones_the_router_already_owns() -> None:
    keyboard = _card().keyboard()
    flat = [cell["callback_data"] for row in keyboard for cell in row]
    assert flat[0] == "mdl_sel_openrouter_0_s_0"
    assert "mdl_sel_openrouter_7_s_0" in flat
    assert "mdl_page_openrouter_s_1" in flat
    assert "mdl_back_tiers_openrouter" in flat
    assert flat[-1] == "mdl_close"


def test_a_tier_that_runs_somebody_else_says_so() -> None:
    html = _card(current_model="", current_provider_name="Ollama").rich_html()
    assert "This tier runs Ollama today" in html
    assert 'style="success"' not in html


def test_an_empty_list_states_the_reason_and_keeps_the_way_back() -> None:
    card = mlc.empty_card(
        tier_label="Small",
        provider_emoji="🟢",
        provider_name="OpenRouter",
        provider_id="openrouter",
    )
    html = card.rich_html()
    assert "<h2>📝 Small — 🟢 OpenRouter</h2>" in html
    assert "No models available" in html
    assert card.keyboard()[0][0]["callback_data"] == "mdl_back_tiers_openrouter"


def test_a_failed_resolution_is_a_different_sentence() -> None:
    card = mlc.empty_card(
        tier_label="Small",
        provider_emoji="🟢",
        provider_name="OpenRouter",
        provider_id="openrouter",
        failed=True,
    )
    assert "Could not load models" in card.rich_html()


def test_the_classic_projection_keeps_the_words_and_drops_the_rich_tags() -> None:
    html = _card().classic_html()
    for tag in ("<h2", "<tg-button", "<details", "<footer", "<table"):
        assert tag not in html
    assert "📝 <b>Small — 🟢 OpenRouter</b>" in html
    assert "<code>openrouter/xiaomi/mimo-v2.6-flash</code>" in html


# -- the handler -----------------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.rich: list[dict] = []
        self.plain: list[dict] = []

    async def send_rich_message(self, chat_id, *, html, fallback_html=None, keyboard=None, **kw):
        self.rich.append({"chat_id": chat_id, "html": html, "keyboard": keyboard})
        return {"ok": True, "result": {"message_id": 555}}

    async def send_message(self, chat_id, text, **kw):
        self.plain.append({"chat_id": chat_id, "text": text, "keyboard": kw.get("keyboard")})
        return {"ok": True}


class _Modes:
    def __init__(self, provider: str, model: str) -> None:
        self._provider = provider
        self._model = model

    def get_mode(self, name: str):
        class _Mode:
            provider = self._provider
            model = self._model

        return _Mode()


class _Router:
    def __init__(self, provider: str, model: str) -> None:
        self.modes = _Modes(provider, model)


def _channel(recorder: _Recorder, monkeypatch, *, provider="openrouter", model=MODELS[0]):
    from navig.gateway.channels.telegram import TelegramChannel

    channel = TelegramChannel(bot_token="1:FAKE")
    channel.send_rich_message = recorder.send_rich_message
    channel.send_message = recorder.send_message

    async def _resolve(prov_id: str, *, manifest=None):
        return list(MODELS)

    channel._resolve_provider_models = _resolve
    monkeypatch.setattr(
        "navig.llm.router.get_llm_router", lambda: _Router(provider, model), raising=False
    )
    return channel


@pytest.mark.asyncio
async def test_the_list_lands_as_one_rich_card(monkeypatch):
    recorder = _Recorder()
    channel = _channel(recorder, monkeypatch)

    await channel._show_models_model_list(1, "openrouter", "s")

    assert len(recorder.rich) == 1
    assert recorder.plain == []
    html = recorder.rich[0]["html"]
    # The provider's emoji and display name come from the registry.
    assert "<h2>📝 ⚡ Small" in html
    assert "OpenRouter" in html
    assert "Page 1/2 · 9 models" in html
    assert "✅ mimo-v2.6-flash" in html
    keyboard = recorder.rich[0]["keyboard"]
    assert keyboard[0][0]["callback_data"] == "mdl_sel_openrouter_0_s_0"


@pytest.mark.asyncio
async def test_a_page_press_rewrites_the_same_message(monkeypatch):
    recorder = _Recorder()
    channel = _channel(recorder, monkeypatch)
    from navig.telegram import card_transport

    seen: list[tuple[int, int]] = []

    async def _edit_card(chan, chat_id, message_id, card, *, keyboard=None):
        seen.append((chat_id, message_id))
        assert "Page 2/2" in card.rich_html()
        return True

    monkeypatch.setattr(card_transport, "edit_card", _edit_card)

    await channel._show_models_model_list(1, "openrouter", "s", page=1, message_id=88)

    assert seen == [(1, 88)]
    assert recorder.rich == []
    assert recorder.plain == []


@pytest.mark.asyncio
async def test_an_empty_list_still_answers_with_a_card(monkeypatch):
    recorder = _Recorder()
    channel = _channel(recorder, monkeypatch)

    async def _resolve(prov_id: str, *, manifest=None):
        return []

    channel._resolve_provider_models = _resolve

    # Without a static manifest to fall back on, "no models" is what the handler sees.
    class _Manifest:
        emoji = "🟢"
        display_name = "OpenRouter"
        models: list = []

    # The package re-exports get_provider lazily and caches the first resolution into
    # `navig.providers.__dict__`, so the package name is patched FIRST — otherwise the
    # handler keeps the real one for the rest of the session.
    monkeypatch.setattr("navig.providers.get_provider", lambda prov_id: _Manifest(), raising=False)
    monkeypatch.setattr(
        "navig.providers.registry.get_provider",
        lambda prov_id: _Manifest(),
        raising=False,
    )

    await channel._show_models_model_list(1, "openrouter", "s")

    assert len(recorder.rich) == 1
    assert "No models available" in recorder.rich[0]["html"]
