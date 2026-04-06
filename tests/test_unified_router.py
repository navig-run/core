"""Regression tests for Unified Router AI provider routing fixes.

Validates that:
1. _create_provider() creates generic providers (xai, anthropic, etc.) via factory
2. _get_provider_chain() prepends user-configured providers
3. _discover_user_providers() reads from all config sources
4. AIClient._detect_provider_from_registry() finds providers with vault keys
5. ConversationalAgent._get_ai_response() reaches mode routing even when
   is_available() returns False
6. MODE_MODEL_PREFERENCE has entries for all supported providers
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── 1. UnifiedRouter._create_generic_provider ─────────────────────────


class TestCreateGenericProvider:
    """_create_provider() should fall through to _create_generic_provider for
    providers not in the special-case if/elif chain."""

    def test_create_provider_xai_returns_instance(self, monkeypatch):
        monkeypatch.setenv("GROK_KEY", "grok-test-key")
        from navig.routing.router import UnifiedRouter

        router = UnifiedRouter(config={})
        provider = router._create_provider("xai")
        assert provider is not None
        # Provider should have the api_key set
        assert getattr(provider, "api_key", None) == "grok-test-key"

    def test_create_provider_anthropic_returns_instance(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
        from navig.routing.router import UnifiedRouter

        router = UnifiedRouter(config={})
        provider = router._create_provider("anthropic")
        assert provider is not None

    def test_create_provider_unknown_returns_none(self):
        """A completely unknown provider with no key should return None."""
        from navig.routing.router import UnifiedRouter

        router = UnifiedRouter(config={})
        provider = router._create_provider("totally_fake_provider_12345")
        assert provider is None

    def test_create_provider_ollama_still_works(self):
        """Ensure the special-cased providers still work."""
        from navig.routing.router import UnifiedRouter

        router = UnifiedRouter(config={})
        provider = router._create_provider("ollama")
        # Ollama doesn't need a key; always returns an instance
        assert provider is not None


# ── 2. UnifiedRouter._get_provider_chain ──────────────────────────────


class TestProviderChain:
    """_get_provider_chain() should prepend user-configured providers."""

    def test_default_chain_without_user_config(self):
        from navig.routing.router import UnifiedRouter

        router = UnifiedRouter(config={})
        chain = router._get_provider_chain()
        assert chain == ["mcp_bridge", "openrouter", "github_models", "ollama"]

    def test_chain_prepends_default_provider(self):
        from navig.routing.router import UnifiedRouter

        router = UnifiedRouter(config={"ai": {"default_provider": "xai"}})
        chain = router._get_provider_chain()
        assert chain[0] == "xai"
        # Static chain entries should still be present
        assert "mcp_bridge" in chain
        assert "ollama" in chain

    def test_chain_deduplicates(self):
        """If user chooses 'openrouter' (already in static chain), no dups."""
        from navig.routing.router import UnifiedRouter

        router = UnifiedRouter(config={"ai": {"default_provider": "openrouter"}})
        chain = router._get_provider_chain()
        assert chain.count("openrouter") == 1
        # openrouter should be first
        assert chain[0] == "openrouter"


# ── 3. UnifiedRouter._discover_user_providers ─────────────────────────


class TestDiscoverUserProviders:
    """_discover_user_providers() should find providers from all config sources."""

    def test_from_ai_default_provider(self):
        from navig.routing.router import UnifiedRouter

        router = UnifiedRouter(config={"ai": {"default_provider": "xai"}})
        providers = router._discover_user_providers()
        assert "xai" in providers

    def test_from_llm_modes(self):
        from navig.routing.router import UnifiedRouter

        router = UnifiedRouter(
            config={
                "llm_router": {
                    "llm_modes": {
                        "coding": {"provider": "anthropic"},
                        "small_talk": {"provider": "groq"},
                    }
                }
            }
        )
        providers = router._discover_user_providers()
        assert "anthropic" in providers
        assert "groq" in providers

    def test_from_hybrid_router_tiers(self):
        from navig.routing.router import UnifiedRouter

        router = UnifiedRouter(
            config={
                "ai": {
                    "routing": {
                        "models": {
                            "small": {"provider": "cerebras"},
                            "big": {"provider": "xai"},
                        }
                    }
                }
            }
        )
        providers = router._discover_user_providers()
        assert "cerebras" in providers
        assert "xai" in providers

    def test_empty_config_returns_empty(self):
        from navig.routing.router import UnifiedRouter

        router = UnifiedRouter(config={})
        providers = router._discover_user_providers()
        assert providers == []


# ── 4. AIClient._detect_provider_from_registry ────────────────────────


class TestDetectProviderFromRegistry:
    """_detect_provider_from_registry() should find providers with available keys
    that _detect_best_provider() would otherwise miss."""

    def test_finds_xai_from_env(self, monkeypatch):
        monkeypatch.setenv("GROK_KEY", "grok-test-key")
        # Isolate from vault state: prevent real vault keys from interfering
        monkeypatch.setattr("navig.vault.get_vault_v2", lambda: None, raising=False)
        _mock_vault = MagicMock()
        _mock_vault.get_api_key.return_value = None
        monkeypatch.setattr("navig.vault.get_vault", lambda: _mock_vault, raising=False)
        from navig.agent.ai_client import AIClient

        client = AIClient.__new__(AIClient)
        # Minimal init for the method to work
        client._config = {}
        result = client._detect_provider_from_registry()
        assert result == "xai"

    def test_returns_empty_when_no_keys(self, monkeypatch):
        # Ensure none of the provider keys are set
        for var in (
            "GROK_KEY",
            "XAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "CLAUDE_API_KEY",
            "GOOGLE_API_KEY",
            "GEMINI_API_KEY",
            "GROQ_API_KEY",
            "NVIDIA_API_KEY",
            "NIM_API_KEY",
            "MISTRAL_API_KEY",
            "CEREBRAS_API_KEY",
            "GITHUB_TOKEN",
            "GH_TOKEN",
            "GITHUB_COPILOT_TOKEN",
            "KILOCODE_API_KEY",
            "QWEN_API_KEY",
            "BLOCKRUN_WALLET_KEY",
        ):
            monkeypatch.delenv(var, raising=False)
        # Isolate from both vault v2 and v1 so dev-machine credentials don't leak in.
        monkeypatch.setattr("navig.vault.get_vault_v2", lambda: None, raising=False)
        _mock_vault = MagicMock()
        _mock_vault.get_api_key.return_value = None
        monkeypatch.setattr("navig.vault.get_vault", lambda: _mock_vault, raising=False)
        from navig.agent.ai_client import AIClient

        client = AIClient.__new__(AIClient)
        client._config = {}
        result = client._detect_provider_from_registry()
        assert result == ""

    def test_skips_already_checked_providers(self, monkeypatch):
        """Should not return openrouter/ollama/etc — those are checked earlier."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
        monkeypatch.setattr("navig.vault.get_vault_v2", lambda: None, raising=False)
        _mock_vault = MagicMock()
        _mock_vault.get_api_key.return_value = None
        monkeypatch.setattr("navig.vault.get_vault", lambda: _mock_vault, raising=False)
        from navig.agent.ai_client import AIClient

        client = AIClient.__new__(AIClient)
        client._config = {}
        result = client._detect_provider_from_registry()
        # openrouter is in the skip list, so it shouldn't be returned here
        assert result != "openrouter"

    def test_deterministic_when_multiple_keys_set(self, monkeypatch):
        """With both NVIDIA and xai keys set, xai must win (precedence order)."""
        monkeypatch.setenv("GROK_KEY", "grok-test-key")
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key")
        monkeypatch.setenv("MISTRAL_API_KEY", "mistral-test-key")
        monkeypatch.setattr("navig.vault.get_vault_v2", lambda: None, raising=False)
        _mock_vault = MagicMock()
        _mock_vault.get_api_key.return_value = None
        monkeypatch.setattr("navig.vault.get_vault", lambda: _mock_vault, raising=False)
        from navig.agent.ai_client import AIClient

        client = AIClient.__new__(AIClient)
        client._config = {}
        result = client._detect_provider_from_registry()
        # xai must win — it is first in _PROVIDER_DETECTION_PRECEDENCE
        assert result == "xai"

    def test_skips_disabled_providers(self, monkeypatch):
        """Providers with enabled=False must never be returned even when a key is set.

        cerebras and github_copilot both ship as enabled=False (opt-in). If their
        env vars are set on the developer's machine, detection must still return ''
        (or an enabled provider), never a disabled one.
        """
        # Set keys ONLY for known-disabled providers; clear all enabled ones.
        for var in (
            "GROK_KEY",
            "XAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "CLAUDE_API_KEY",
            "GOOGLE_API_KEY",
            "GEMINI_API_KEY",
            "GROQ_API_KEY",
            "NVIDIA_API_KEY",
            "NIM_API_KEY",
            "BLOCKRUN_WALLET_KEY",
        ):
            monkeypatch.delenv(var, raising=False)
        # These are all enabled=False
        monkeypatch.setenv("CEREBRAS_API_KEY", "csk-test")
        monkeypatch.setenv("GITHUB_COPILOT_TOKEN", "ghp-test")
        monkeypatch.setenv("KILOCODE_API_KEY", "kilo-test")
        monkeypatch.setenv("MISTRAL_API_KEY", "ms-test")
        monkeypatch.setattr("navig.vault.get_vault_v2", lambda: None, raising=False)
        _mock_vault = MagicMock()
        _mock_vault.get_api_key.return_value = None
        monkeypatch.setattr("navig.vault.get_vault", lambda: _mock_vault, raising=False)
        from navig.agent.ai_client import AIClient

        client = AIClient.__new__(AIClient)
        client._config = {}
        result = client._detect_provider_from_registry()
        # Must not return any disabled provider
        assert result not in ("cerebras", "github_copilot", "kilocode", "mistral", "qwen")
        # With no enabled provider keys set, must return empty string
        assert result == ""


# ── 5. ConversationalAgent._get_ai_response gate removal ─────────────


class TestModeRoutingGateRemoval:
    """_try_llm_mode_routing() should be called even when is_available() is False."""

    @pytest.mark.asyncio
    async def test_mode_routing_reached_when_unavailable(self):
        """When is_available() returns False, _try_llm_mode_routing should still run."""
        from navig.agent.conversational import ConversationalAgent

        agent = ConversationalAgent.__new__(ConversationalAgent)
        # Mock ai_client where is_available() returns False
        mock_ai = MagicMock()
        mock_ai.is_available.return_value = False
        agent.ai_client = mock_ai

        # Minimal internal state _get_ai_response expects
        agent.conversation_history = []
        agent.context = {}
        agent._tier_override = ""
        agent._entrypoint = "channel"
        agent._detected_language_hint = ""

        # Mock internal helper methods
        agent._build_system_prompt = MagicMock(return_value="system prompt")
        agent._get_pinned_language_override = MagicMock(return_value="")
        agent._detect_language_code = MagicMock(return_value="")
        agent._try_llm_mode_routing = AsyncMock(
            return_value=("AI response from mode routing", None)
        )
        agent._simple_response = AsyncMock(return_value="fallback")

        # Patch get_router so the unified router raises → falls to legacy
        with patch(
            "navig.routing.router.get_router",
            side_effect=ImportError("mocked"),
        ):
            result = await agent._get_ai_response("hello")

        # _try_llm_mode_routing should have been called
        agent._try_llm_mode_routing.assert_called_once()
        assert result == "AI response from mode routing"
        # _simple_response should NOT have been called
        agent._simple_response.assert_not_called()


# ── 6. MODE_MODEL_PREFERENCE completeness ─────────────────────────────


class TestModeModelPreference:
    """MODE_MODEL_PREFERENCE should have entries for all major providers."""

    REQUIRED_PROVIDERS = [
        "openrouter",
        "github_models",
        "ollama",
        "xai",
        "anthropic",
        "google",
        "groq",
        "nvidia",
        "mistral",
        "cerebras",
        "openai",
    ]

    MODES = ["coding", "small_talk", "big_tasks", "summarize", "research"]

    def test_all_providers_present_in_all_modes(self):
        from navig.routing.capabilities import MODE_MODEL_PREFERENCE

        for mode in self.MODES:
            assert mode in MODE_MODEL_PREFERENCE, f"Mode '{mode}' missing"
            for provider in self.REQUIRED_PROVIDERS:
                assert provider in MODE_MODEL_PREFERENCE[mode], (
                    f"Provider '{provider}' missing from MODE_MODEL_PREFERENCE['{mode}']"
                )

    def test_xai_coding_model(self):
        from navig.routing.capabilities import MODE_MODEL_PREFERENCE

        assert MODE_MODEL_PREFERENCE["coding"]["xai"] == "grok-3"

    def test_xai_small_talk_model(self):
        from navig.routing.capabilities import MODE_MODEL_PREFERENCE

        assert MODE_MODEL_PREFERENCE["small_talk"]["xai"] == "grok-3-mini"

    def test_anthropic_coding_model(self):
        from navig.routing.capabilities import MODE_MODEL_PREFERENCE

        model = MODE_MODEL_PREFERENCE["coding"]["anthropic"]
        assert "claude" in model.lower()

    def test_google_research_model(self):
        from navig.routing.capabilities import MODE_MODEL_PREFERENCE

        model = MODE_MODEL_PREFERENCE["research"]["google"]
        assert "gemini" in model.lower()
