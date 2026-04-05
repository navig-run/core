"""Tests for the /models interactive flow and mdl_* callback namespace.

Covers:
- /models auto-detecting active provider → tier summary
- /models with no provider → provider picker
- /models quick-switch: /models big, /models auto
- mdl_prov_{id} → activate + show tier summary
- mdl_tier_{id}_{tc} → show model list
- mdl_sel_{id}_{idx}_{tc}_{pg} → assign model, refresh with ✅
- mdl_page_ pagination
- mdl_back_tiers_ → back to tier summary
- mdl_close → delete message
- mdl_chgprov → navigate to providers
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from navig.gateway.channels.telegram_commands import TelegramCommandsMixin
from navig.gateway.channels.telegram_keyboards import CallbackHandler


# ─── Fake channel with recording ───────────────────────────────────────────


class _FakeModelsChannel(TelegramCommandsMixin):
    """Minimal channel satisfying _handle_models_command and model flow methods."""

    def __init__(self):
        self.messages: list[tuple] = []
        self.edits: list[tuple] = []
        self.api_calls: list[tuple] = []
        self.provider_renders: list[tuple] = []
        self.tier_commands: list[str] = []
        self._user_model_prefs: dict = {}
        self._resolve_cache: list[str] = []

    async def send_message(self, chat_id, text, parse_mode=None, keyboard=None, **kw):
        self.messages.append((chat_id, text, parse_mode, keyboard))
        return {"ok": True}

    async def edit_message(self, chat_id, message_id, text, parse_mode=None, keyboard=None, **kw):
        self.edits.append((chat_id, message_id, text, parse_mode, keyboard))
        return {"ok": True}

    async def _api_call(self, method, data):
        self.api_calls.append((method, data))
        return {"ok": True}

    async def _handle_providers(self, chat_id, user_id=0, message_id=None):
        self.provider_renders.append((chat_id, user_id, message_id))

    async def _handle_tier_command(self, chat_id, user_id, text):
        self.tier_commands.append(text)

    async def _resolve_provider_models(self, prov_id, manifest=None):
        if self._resolve_cache:
            return self._resolve_cache
        return ["grok-3", "grok-3-mini", "grok-2"]

    @staticmethod
    def _select_curated_tier_defaults(prov_id, models):
        return {"small": models[-1], "big": models[0], "coder_big": models[0]}

    def _update_llm_mode_router(self, provider_id, tier_models):
        pass

    def _persist_hybrid_router_assignments(self, router_cfg):
        pass


def _make_fake_manifest(prov_id="openai", name="OpenAI", emoji="🤖", tier="cloud"):
    return SimpleNamespace(
        id=prov_id, display_name=name, emoji=emoji, tier=tier,
        requires_key=True, local_probe=None, env_vars=[], vault_keys=[],
        models=[], enabled=True,
    )


# ─── /models command ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_models_with_active_provider_shows_tier_summary(monkeypatch):
    """When a provider is active, /models shows the tier summary directly."""
    ch = _FakeModelsChannel()

    # Fake LLM router with big_tasks mode set
    class _Router:
        class modes:
            @staticmethod
            def get_mode(name):
                if name == "big_tasks":
                    return SimpleNamespace(model="openai/gpt-4o", provider="openai")
                return None

    monkeypatch.setattr("navig.llm_router.get_llm_router", lambda: _Router())
    monkeypatch.setattr("navig.providers.registry.get_provider",
                        lambda pid: _make_fake_manifest(pid))

    await ch._handle_models_command(chat_id=100, user_id=200, text="/models")

    # Should send tier summary (via send_message since no message_id)
    assert ch.messages, "Expected a message to be sent"
    text = ch.messages[0][1]
    assert "Models" in text
    assert "Small" in text or "Big" in text


@pytest.mark.asyncio
async def test_models_no_provider_shows_provider_picker(monkeypatch):
    """When no provider is active, /models shows a provider picker."""
    ch = _FakeModelsChannel()

    # LLM router with no big_tasks mode
    class _Router:
        class modes:
            @staticmethod
            def get_mode(name):
                return None

    monkeypatch.setattr("navig.llm_router.get_llm_router", lambda: _Router())
    monkeypatch.setattr("navig.providers.registry.list_enabled_providers", lambda: [])

    await ch._handle_models_command(chat_id=100, user_id=200, text="/models")

    assert ch.messages, "Expected a message to be sent"
    text = ch.messages[0][1]
    assert "No provider" in text or "Configure" in text


@pytest.mark.asyncio
async def test_models_quick_switch_big(monkeypatch):
    """/models big triggers _handle_tier_command."""
    ch = _FakeModelsChannel()

    await ch._handle_models_command(chat_id=100, user_id=200, text="/models big")

    assert ch.tier_commands == ["/big"]


@pytest.mark.asyncio
async def test_models_quick_switch_auto(monkeypatch):
    """/models auto triggers _handle_tier_command."""
    ch = _FakeModelsChannel()

    await ch._handle_models_command(chat_id=100, user_id=200, text="/models auto")

    assert ch.tier_commands == ["/auto"]


# ─── Tier summary ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tier_summary_shows_three_tiers(monkeypatch):
    """Tier summary has buttons for small, big, and code."""
    ch = _FakeModelsChannel()

    class _Router:
        class modes:
            @staticmethod
            def get_mode(name):
                return SimpleNamespace(model="openai/gpt-4o-mini", provider="openai")

    monkeypatch.setattr("navig.llm_router.get_llm_router", lambda: _Router())
    monkeypatch.setattr("navig.providers.registry.get_provider",
                        lambda pid: _make_fake_manifest(pid))

    await ch._show_models_tier_summary(chat_id=100, prov_id="openai")

    assert ch.messages
    kb = ch.messages[0][3]
    assert kb is not None
    all_cbs = [btn.get("callback_data", "") for row in kb for btn in row]
    assert "mdl_tier_openai_s" in all_cbs
    assert "mdl_tier_openai_b" in all_cbs
    assert "mdl_tier_openai_c" in all_cbs


@pytest.mark.asyncio
async def test_tier_summary_uses_edit_when_message_id(monkeypatch):
    """When message_id is provided, tier summary edits the message inline."""
    ch = _FakeModelsChannel()

    class _Router:
        class modes:
            @staticmethod
            def get_mode(name):
                return None

    monkeypatch.setattr("navig.llm_router.get_llm_router", lambda: _Router())
    monkeypatch.setattr("navig.providers.registry.get_provider",
                        lambda pid: _make_fake_manifest(pid))

    await ch._show_models_tier_summary(chat_id=100, prov_id="openai", message_id=500)

    assert ch.edits, "Expected edit_message to be called"
    assert ch.edits[0][1] == 500
    assert not ch.messages, "Should not fall back to send_message"


# ─── Model list (pagination + ✅) ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_model_list_shows_checkmark_on_current(monkeypatch):
    """The currently assigned model gets a ✅ prefix."""
    ch = _FakeModelsChannel()
    ch._resolve_cache = ["openai/gpt-4o", "openai/gpt-4o-mini", "openai/gpt-3.5-turbo"]

    class _Router:
        class modes:
            @staticmethod
            def get_mode(name):
                return SimpleNamespace(model="openai/gpt-4o-mini", provider="openai")

    monkeypatch.setattr("navig.llm_router.get_llm_router", lambda: _Router())
    monkeypatch.setattr("navig.providers.registry.get_provider",
                        lambda pid: _make_fake_manifest(pid))

    await ch._show_models_model_list(
        chat_id=100, prov_id="openai", tier_code="s", page=0,
    )

    assert ch.messages
    kb = ch.messages[0][3]
    labels = [btn.get("text", "") for row in kb for btn in row]
    # gpt-4o-mini is index 1 and should have ✅
    checked = [l for l in labels if l.startswith("✅")]
    assert checked, f"Expected ✅ on current model; labels: {labels}"
    assert "gpt-4o-mini" in checked[0]


@pytest.mark.asyncio
async def test_model_list_pagination_buttons(monkeypatch):
    """When models exceed PAGE_SIZE, pagination buttons appear."""
    ch = _FakeModelsChannel()
    ch._resolve_cache = [f"prov/model-{i}" for i in range(20)]

    class _Router:
        class modes:
            @staticmethod
            def get_mode(name):
                return None

    monkeypatch.setattr("navig.llm_router.get_llm_router", lambda: _Router())
    monkeypatch.setattr("navig.providers.registry.get_provider",
                        lambda pid: _make_fake_manifest(pid))

    # Page 0 of 20 models (PAGE_SIZE=8 → 3 pages)
    await ch._show_models_model_list(
        chat_id=100, prov_id="openai", tier_code="b", page=0,
    )

    kb = ch.messages[0][3]
    all_cbs = [btn.get("callback_data", "") for row in kb for btn in row]
    labels = [btn.get("text", "") for row in kb for btn in row]
    # Should have Next but not Prev on page 0
    assert any("mdl_page_" in cb for cb in all_cbs), "Expected pagination callback"
    assert any("Next" in l for l in labels), "Expected Next button on page 0"
    assert not any("Prev" in l for l in labels), "Should not have Prev on page 0"


@pytest.mark.asyncio
async def test_model_list_page_1_has_prev(monkeypatch):
    """Page > 0 has a Prev button."""
    ch = _FakeModelsChannel()
    ch._resolve_cache = [f"prov/model-{i}" for i in range(20)]

    class _Router:
        class modes:
            @staticmethod
            def get_mode(name):
                return None

    monkeypatch.setattr("navig.llm_router.get_llm_router", lambda: _Router())
    monkeypatch.setattr("navig.providers.registry.get_provider",
                        lambda pid: _make_fake_manifest(pid))

    await ch._show_models_model_list(
        chat_id=100, prov_id="openai", tier_code="b", page=1,
    )

    kb = ch.messages[0][3]
    labels = [btn.get("text", "") for row in kb for btn in row]
    assert any("Prev" in l for l in labels), "Expected Prev button on page 1"


@pytest.mark.asyncio
async def test_model_list_no_models_shows_warning(monkeypatch):
    """When no models are available, a warning is shown."""
    ch = _FakeModelsChannel()
    ch._resolve_cache = []

    monkeypatch.setattr("navig.providers.registry.get_provider",
                        lambda pid: _make_fake_manifest(pid))

    # Override resolver to return empty
    async def _empty(*a, **kw):
        return []

    ch._resolve_provider_models = _empty

    await ch._show_models_model_list(
        chat_id=100, prov_id="openai", tier_code="s", page=0,
    )

    assert ch.messages
    text = ch.messages[0][1]
    assert "No models" in text or "no models" in text.lower()


# ─── Callback handler: mdl_* ──────────────────────────────────────────────


class _FakeCallbackChannel:
    """Channel stub for CallbackHandler mdl_* tests."""

    def __init__(self):
        self.api_calls: list = []
        self.provider_renders: list = []
        self.tier_summaries: list = []
        self.model_lists: list = []
        self.llm_mode_calls: list = []
        self.persist_calls = 0
        self._user_model_prefs: dict = {}
        self._resolve_result: list = ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"]

    async def _api_call(self, method, data):
        self.api_calls.append((method, data))
        return {"ok": True}

    async def send_message(self, chat_id, text, **kw):
        pass

    async def edit_message(self, *a, **kw):
        pass

    async def _handle_providers(self, chat_id, user_id=0, message_id=None):
        self.provider_renders.append((chat_id, user_id, message_id))

    async def _show_models_tier_summary(self, chat_id, prov_id, message_id=None):
        self.tier_summaries.append((chat_id, prov_id, message_id))

    async def _show_models_model_list(self, chat_id, prov_id, tier_code, page=0, message_id=None):
        self.model_lists.append((chat_id, prov_id, tier_code, page, message_id))

    async def _resolve_provider_models(self, prov_id, manifest=None):
        return self._resolve_result

    @staticmethod
    def _select_curated_tier_defaults(prov_id, models):
        return {"small": models[-1], "big": models[0], "coder_big": models[0]}

    def _update_llm_mode_router(self, provider_id, tier_models):
        self.llm_mode_calls.append((provider_id, tier_models))

    def _persist_hybrid_router_assignments(self, router_cfg):
        self.persist_calls += 1


@pytest.mark.asyncio
async def test_mdl_close_deletes_message():
    """mdl_close answers and deletes the message."""
    ch = _FakeCallbackChannel()
    handler = CallbackHandler(ch)

    await handler._handle_models_callback(
        cb_id="cb-1", cb_data="mdl_close",
        chat_id=100, message_id=200, user_id=300,
    )

    # Should call answerCallbackQuery then deleteMessage
    methods = [c[0] for c in ch.api_calls]
    assert "answerCallbackQuery" in methods
    assert "deleteMessage" in methods


@pytest.mark.asyncio
async def test_mdl_chgprov_navigates_to_providers():
    """mdl_chgprov routes to _handle_providers."""
    ch = _FakeCallbackChannel()
    handler = CallbackHandler(ch)

    await handler._handle_models_callback(
        cb_id="cb-1", cb_data="mdl_chgprov",
        chat_id=100, message_id=200, user_id=300,
    )

    assert ch.provider_renders == [(100, 300, 200)]


@pytest.mark.asyncio
async def test_mdl_prov_activates_and_shows_tiers(monkeypatch):
    """mdl_prov_{id} activates provider and shows tier summary."""
    ch = _FakeCallbackChannel()
    handler = CallbackHandler(ch)

    monkeypatch.setattr("navig.providers.registry.get_provider",
                        lambda pid: _make_fake_manifest(pid))

    await handler._handle_models_callback(
        cb_id="cb-1", cb_data="mdl_prov_openai",
        chat_id=100, message_id=200, user_id=300,
    )

    # LLM mode router should be updated
    assert ch.llm_mode_calls, "Expected LLM mode router update"
    assert ch.llm_mode_calls[0][0] == "openai"

    # Tier summary should be shown
    assert ch.tier_summaries == [(100, "openai", 200)]


@pytest.mark.asyncio
async def test_mdl_prov_unknown_answers_warning(monkeypatch):
    """mdl_prov_ with unknown provider answers with warning."""
    ch = _FakeCallbackChannel()
    handler = CallbackHandler(ch)

    monkeypatch.setattr("navig.providers.registry.get_provider", lambda pid: None)

    await handler._handle_models_callback(
        cb_id="cb-1", cb_data="mdl_prov_nonexist",
        chat_id=100, message_id=200, user_id=300,
    )

    # Should answer with warning, not show tier summary
    assert not ch.tier_summaries
    answers = [c for c in ch.api_calls if c[0] == "answerCallbackQuery"]
    assert answers
    assert "Unknown" in answers[0][1].get("text", "") or "⚠️" in answers[0][1].get("text", "")


@pytest.mark.asyncio
async def test_mdl_tier_opens_model_list():
    """mdl_tier_{prov_id}_{tc} shows the model list."""
    ch = _FakeCallbackChannel()
    handler = CallbackHandler(ch)

    await handler._handle_models_callback(
        cb_id="cb-1", cb_data="mdl_tier_openai_s",
        chat_id=100, message_id=200, user_id=300,
    )

    assert ch.model_lists == [(100, "openai", "s", 0, 200)]


@pytest.mark.asyncio
async def test_mdl_tier_rejects_unknown_tier_code():
    """mdl_tier with unsupported tier code should warn and not render list."""
    ch = _FakeCallbackChannel()
    handler = CallbackHandler(ch)

    await handler._handle_models_callback(
        cb_id="cb-1", cb_data="mdl_tier_openai_x",
        chat_id=100, message_id=200, user_id=300,
    )

    assert ch.model_lists == []
    answers = [payload for method, payload in ch.api_calls if method == "answerCallbackQuery"]
    assert answers
    assert "Unknown tier" in answers[-1].get("text", "")


@pytest.mark.asyncio
async def test_mdl_back_tiers_returns_to_summary():
    """mdl_back_tiers_{id} navigates back to tier summary."""
    ch = _FakeCallbackChannel()
    handler = CallbackHandler(ch)

    await handler._handle_models_callback(
        cb_id="cb-1", cb_data="mdl_back_tiers_openai",
        chat_id=100, message_id=200, user_id=300,
    )

    assert ch.tier_summaries == [(100, "openai", 200)]


@pytest.mark.asyncio
async def test_mdl_page_navigates(monkeypatch):
    """mdl_page_{prov_id}_{tc}_{page} shows the correct page of models."""
    ch = _FakeCallbackChannel()
    handler = CallbackHandler(ch)

    await handler._handle_models_callback(
        cb_id="cb-1", cb_data="mdl_page_openai_b_2",
        chat_id=100, message_id=200, user_id=300,
    )

    assert ch.model_lists == [(100, "openai", "b", 2, 200)]


@pytest.mark.asyncio
async def test_mdl_page_rejects_unknown_tier_code():
    """mdl_page with unsupported tier code should warn and not render list."""
    ch = _FakeCallbackChannel()
    handler = CallbackHandler(ch)

    await handler._handle_models_callback(
        cb_id="cb-1", cb_data="mdl_page_openai_x_2",
        chat_id=100, message_id=200, user_id=300,
    )

    assert ch.model_lists == []
    answers = [payload for method, payload in ch.api_calls if method == "answerCallbackQuery"]
    assert answers
    assert "Unknown tier" in answers[-1].get("text", "")


@pytest.mark.asyncio
async def test_mdl_sel_assigns_model_and_refreshes(monkeypatch):
    """mdl_sel_ assigns the model, updates mode router, and refreshes model list."""
    ch = _FakeCallbackChannel()
    handler = CallbackHandler(ch)

    monkeypatch.setattr("navig.providers.registry.get_provider",
                        lambda pid: _make_fake_manifest(pid))

    # mdl_sel_openai_1_s_0  →  select model index 1 in small tier, page 0
    await handler._handle_models_callback(
        cb_id="cb-1", cb_data="mdl_sel_openai_1_s_0",
        chat_id=100, message_id=200, user_id=300,
    )

    # LLM mode router updated with small tier → model at index 1
    assert ch.llm_mode_calls
    prov, tier_map = ch.llm_mode_calls[0]
    assert prov == "openai"
    assert "small" in tier_map
    assert tier_map["small"] == "gpt-4o-mini"  # index 1 of _resolve_result

    # Model list refreshed at same page
    assert ch.model_lists == [(100, "openai", "s", 0, 200)]


@pytest.mark.asyncio
async def test_mdl_sel_save_failure_shows_warning_and_refreshes(monkeypatch):
    """mdl_sel should warn when mode-router save fails, but still refresh list."""

    class _FailingSaveCallbackChannel(_FakeCallbackChannel):
        def _update_llm_mode_router(self, provider_id, tier_models):
            raise RuntimeError("save failed")

    ch = _FailingSaveCallbackChannel()
    handler = CallbackHandler(ch)

    monkeypatch.setattr("navig.providers.registry.get_provider", lambda pid: _make_fake_manifest(pid))

    await handler._handle_models_callback(
        cb_id="cb-fail",
        cb_data="mdl_sel_openai_1_s_0",
        chat_id=100,
        message_id=200,
        user_id=300,
    )

    answers = [payload for method, payload in ch.api_calls if method == "answerCallbackQuery"]
    assert answers
    assert "could not be saved" in answers[-1].get("text", "")
    assert answers[-1].get("show_alert") is True

    # Still refresh to let user retry quickly.
    assert ch.model_lists == [(100, "openai", "s", 0, 200)]


@pytest.mark.asyncio
async def test_mdl_sel_rejects_negative_index(monkeypatch):
    """mdl_sel must reject negative model indices (no Python reverse indexing)."""
    ch = _FakeCallbackChannel()
    handler = CallbackHandler(ch)

    monkeypatch.setattr("navig.providers.registry.get_provider", lambda pid: _make_fake_manifest(pid))

    await handler._handle_models_callback(
        cb_id="cb-neg",
        cb_data="mdl_sel_openai_-1_s_0",
        chat_id=100,
        message_id=200,
        user_id=300,
    )

    # No save attempts and no refresh when index is invalid.
    assert ch.llm_mode_calls == []
    assert ch.model_lists == []

    answers = [payload for method, payload in ch.api_calls if method == "answerCallbackQuery"]
    assert answers
    assert "out of range" in answers[-1].get("text", "")


@pytest.mark.asyncio
async def test_mdl_sel_resolution_failure_shows_warning(monkeypatch):
    """mdl_sel should warn directly when model list resolution fails."""

    class _FailingResolveCallbackChannel(_FakeCallbackChannel):
        async def _resolve_provider_models(self, prov_id, manifest=None):
            raise RuntimeError("resolver down")

    ch = _FailingResolveCallbackChannel()
    handler = CallbackHandler(ch)

    monkeypatch.setattr("navig.providers.registry.get_provider", lambda pid: _make_fake_manifest(pid))

    await handler._handle_models_callback(
        cb_id="cb-resolve-fail",
        cb_data="mdl_sel_openai_0_s_0",
        chat_id=100,
        message_id=200,
        user_id=300,
    )

    answers = [payload for method, payload in ch.api_calls if method == "answerCallbackQuery"]
    assert answers
    assert "Could not load models" in answers[-1].get("text", "")
    assert answers[-1].get("show_alert") is True

    # Must not proceed to refresh list after a hard resolver failure.
    assert ch.model_lists == []


@pytest.mark.asyncio
async def test_mdl_unknown_callback_warns():
    """Unknown mdl_* callback answers with warning."""
    ch = _FakeCallbackChannel()
    handler = CallbackHandler(ch)

    await handler._handle_models_callback(
        cb_id="cb-1", cb_data="mdl_nonexistent_action",
        chat_id=100, message_id=200, user_id=300,
    )

    answers = [c for c in ch.api_calls if c[0] == "answerCallbackQuery"]
    assert answers
    data = answers[0][1]
    assert "Unknown" in data.get("text", "") or "⚠️" in data.get("text", "")


# ─── Provider picker in /models ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_provider_picker_shows_ready_providers(monkeypatch):
    """_show_models_provider_picker shows only ready providers with mdl_prov_ callbacks."""
    ch = _FakeModelsChannel()

    manifest = _make_fake_manifest("xai", "xAI Grok", "🦊")
    monkeypatch.setattr("navig.providers.registry.list_enabled_providers", lambda: [manifest])
    monkeypatch.setattr("navig.providers.verifier.verify_provider",
                        lambda m: SimpleNamespace(key_detected=True, local_probe_ok=False))

    await ch._show_models_provider_picker(chat_id=100)

    assert ch.messages
    kb = ch.messages[0][3]
    all_cbs = [btn.get("callback_data", "") for row in kb for btn in row]
    assert "mdl_prov_xai" in all_cbs


@pytest.mark.asyncio
async def test_provider_picker_has_nav_and_close(monkeypatch):
    """Provider picker always has Providers nav and Close buttons."""
    ch = _FakeModelsChannel()

    monkeypatch.setattr("navig.providers.registry.list_enabled_providers", lambda: [])

    await ch._show_models_provider_picker(chat_id=100)

    assert ch.messages
    kb = ch.messages[0][3]
    all_cbs = [btn.get("callback_data", "") for row in kb for btn in row]
    assert "mdl_chgprov" in all_cbs
    assert "mdl_close" in all_cbs
