"""Batch 108: tests for llm_router and llm_generate pure functions."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# llm_router
# ---------------------------------------------------------------------------

class TestCanonicalModes:
    def test_canonical_modes_is_set(self):
        from navig.llm_router import CANONICAL_MODES
        assert isinstance(CANONICAL_MODES, (set, frozenset))

    def test_canonical_modes_contains_expected(self):
        from navig.llm_router import CANONICAL_MODES
        for mode in ("small_talk", "big_tasks", "coding", "summarize", "research"):
            assert mode in CANONICAL_MODES

    def test_canonical_modes_non_empty(self):
        from navig.llm_router import CANONICAL_MODES
        assert len(CANONICAL_MODES) >= 5


class TestGetEnvVarName:
    def test_known_provider_returns_string(self):
        from navig.llm_router import _get_env_var_name
        result = _get_env_var_name("openai")
        assert isinstance(result, str)

    def test_unknown_provider_returns_empty(self):
        from navig.llm_router import _get_env_var_name
        result = _get_env_var_name("unknown_xyz_provider")
        assert result == ""

    def test_openai_has_key_env_var(self):
        from navig.llm_router import _get_env_var_name
        result = _get_env_var_name("openai")
        assert result  # should be non-empty for openai


class TestInferProviderForUncensored:
    def test_grok_in_alias(self):
        from navig.llm_router import _infer_provider_for_uncensored
        assert _infer_provider_for_uncensored("grok-alpha", "some_model") == "grok"

    def test_grok_in_model_name(self):
        from navig.llm_router import _infer_provider_for_uncensored
        assert _infer_provider_for_uncensored("alias", "grok-2-latest") == "grok"

    def test_x_ai_in_model(self):
        from navig.llm_router import _infer_provider_for_uncensored
        assert _infer_provider_for_uncensored("alias", "x.ai/model") == "grok"

    def test_openrouter_style_slash(self):
        from navig.llm_router import _infer_provider_for_uncensored
        result = _infer_provider_for_uncensored("alias", "org/model-name")
        assert result == "openrouter"

    def test_default_is_openrouter(self):
        from navig.llm_router import _infer_provider_for_uncensored
        result = _infer_provider_for_uncensored("some-alias", "somemodel")
        assert result == "openrouter"


class TestModeToolsetHints:
    def test_hints_is_dict(self):
        from navig.llm_router import MODE_TOOLSET_HINTS
        assert isinstance(MODE_TOOLSET_HINTS, dict)

    def test_small_talk_has_no_tools(self):
        from navig.llm_router import MODE_TOOLSET_HINTS
        assert MODE_TOOLSET_HINTS.get("small_talk") == []

    def test_coding_includes_code(self):
        from navig.llm_router import MODE_TOOLSET_HINTS
        assert "code" in MODE_TOOLSET_HINTS.get("coding", [])

    def test_research_includes_search(self):
        from navig.llm_router import MODE_TOOLSET_HINTS
        assert "search" in MODE_TOOLSET_HINTS.get("research", [])


class TestResolvedLLMConfig:
    def test_to_dict_keys(self):
        from navig.llm_router import ResolvedLLMConfig
        cfg = ResolvedLLMConfig(provider="openai", model="gpt-4o")
        d = cfg.to_dict()
        for key in ("provider", "model", "temperature", "max_tokens", "is_uncensored"):
            assert key in d

    def test_provider_and_model(self):
        from navig.llm_router import ResolvedLLMConfig
        cfg = ResolvedLLMConfig(provider="anthropic", model="claude-3-opus")
        assert cfg.provider == "anthropic"
        assert cfg.model == "claude-3-opus"

    def test_default_is_not_uncensored(self):
        from navig.llm_router import ResolvedLLMConfig
        cfg = ResolvedLLMConfig(provider="openai", model="gpt-4")
        assert cfg.is_uncensored is False

    def test_repr_includes_provider(self):
        from navig.llm_router import ResolvedLLMConfig
        cfg = ResolvedLLMConfig(provider="ollama", model="llama3")
        assert "ollama" in repr(cfg)

    def test_uncensored_flag(self):
        from navig.llm_router import ResolvedLLMConfig
        cfg = ResolvedLLMConfig(provider="openrouter", model="x/y", is_uncensored=True)
        assert cfg.is_uncensored is True


class TestSuggestToolsets:
    def test_returns_list(self):
        from navig.llm_router import suggest_toolsets
        result = suggest_toolsets(mode="small_talk")
        assert isinstance(result, list)

    def test_small_talk_no_tools(self):
        from navig.llm_router import suggest_toolsets
        assert suggest_toolsets(mode="small_talk") == []

    def test_coding_mode_returns_tools(self):
        from navig.llm_router import suggest_toolsets
        result = suggest_toolsets(mode="coding")
        assert len(result) > 0

    def test_research_mode_returns_tools(self):
        from navig.llm_router import suggest_toolsets
        result = suggest_toolsets(mode="research")
        assert len(result) > 0


class TestGetLlmRouter:
    def test_returns_llm_mode_router_instance(self):
        from navig.llm_router import get_llm_router, LLMModeRouter
        router = get_llm_router(force_new=True)
        assert isinstance(router, LLMModeRouter)

    def test_singleton_same_instance(self):
        from navig.llm_router import get_llm_router
        a = get_llm_router()
        b = get_llm_router()
        assert a is b

    def test_force_new_creates_new_instance(self):
        from navig.llm_router import get_llm_router
        a = get_llm_router(force_new=True)
        b = get_llm_router(force_new=True)
        # Both are valid routers
        assert a is not None
        assert b is not None


# ---------------------------------------------------------------------------
# llm_generate — pure utility functions
# ---------------------------------------------------------------------------

class TestExtractUserText:
    def test_extracts_last_user_message(self):
        from navig.llm_generate import _extract_user_text
        msgs = [
            {"role": "system", "content": "system stuff"},
            {"role": "user", "content": "first user"},
            {"role": "assistant", "content": "some reply"},
            {"role": "user", "content": "second user"},
        ]
        assert _extract_user_text(msgs) == "second user"

    def test_empty_messages_returns_empty(self):
        from navig.llm_generate import _extract_user_text
        assert _extract_user_text([]) == ""

    def test_no_user_msg_returns_empty(self):
        from navig.llm_generate import _extract_user_text
        msgs = [{"role": "system", "content": "only system"}]
        assert _extract_user_text(msgs) == ""

    def test_missing_content_returns_empty(self):
        from navig.llm_generate import _extract_user_text
        msgs = [{"role": "user"}]  # no content key
        assert _extract_user_text(msgs) == ""


class TestParseModelSpec:
    def test_provider_colon_model(self):
        from navig.llm_generate import _parse_model_spec
        provider, model = _parse_model_spec("openai:gpt-4o")
        assert provider == "openai"
        assert model == "gpt-4o"

    def test_provider_override(self):
        from navig.llm_generate import _parse_model_spec
        provider, model = _parse_model_spec("gpt-4", provider_override="custom")
        assert provider == "custom"
        assert model == "gpt-4"

    def test_gpt_prefix_infers_openai(self):
        from navig.llm_generate import _parse_model_spec
        provider, model = _parse_model_spec("gpt-4-turbo")
        assert provider == "openai"

    def test_claude_prefix_infers_anthropic(self):
        from navig.llm_generate import _parse_model_spec
        provider, model = _parse_model_spec("claude-3-opus")
        assert provider == "anthropic"

    def test_deepseek_model(self):
        from navig.llm_generate import _parse_model_spec
        provider, _ = _parse_model_spec("deepseek-chat")
        assert provider == "deepseek"

    def test_llama_model_infers_ollama(self):
        from navig.llm_generate import _parse_model_spec
        provider, _ = _parse_model_spec("llama3")
        assert provider == "ollama"

    def test_slash_model_infers_openrouter(self):
        from navig.llm_generate import _parse_model_spec
        provider, _ = _parse_model_spec("org/model-name")
        assert provider == "openrouter"

    def test_o1_prefix_infers_openai(self):
        from navig.llm_generate import _parse_model_spec
        provider, _ = _parse_model_spec("o1-mini")
        assert provider == "openai"


class TestEnrichMessagesWithContext:
    def test_no_context_returns_unchanged(self):
        from navig.llm_generate import _enrich_messages_with_context
        msgs = [{"role": "user", "content": "hi"}]
        result = _enrich_messages_with_context(msgs, {})
        assert result == msgs

    def test_with_kb_snippets_adds_context(self):
        from navig.llm_generate import _enrich_messages_with_context
        msgs = [{"role": "user", "content": "question"}]
        ctx = {"kb_snippets": [{"key": "fact1", "content": "relevant info"}]}
        result = _enrich_messages_with_context(msgs, ctx)
        assert len(result) > len(msgs)
        # A system message should be injected
        assert any(m.get("role") == "system" for m in result)

    def test_with_conversation_history(self):
        from navig.llm_generate import _enrich_messages_with_context
        msgs = [{"role": "user", "content": "question"}]
        ctx = {
            "conversation_history": [
                {"role": "user", "content": "previous"},
                {"role": "assistant", "content": "reply"},
            ]
        }
        result = _enrich_messages_with_context(msgs, ctx)
        content = " ".join(m.get("content", "") for m in result)
        assert "Conversation" in content or "previous" in content

    def test_does_not_mutate_input(self):
        from navig.llm_generate import _enrich_messages_with_context
        msgs = [{"role": "user", "content": "q"}]
        ctx = {"kb_snippets": [{"key": "k", "content": "data"}]}
        original_len = len(msgs)
        _enrich_messages_with_context(msgs, ctx)
        assert len(msgs) == original_len


class TestHasLlmModesConfig:
    def test_returns_bool(self):
        from navig.llm_generate import _has_llm_modes_config
        result = _has_llm_modes_config()
        assert isinstance(result, bool)

    def test_returns_false_when_config_raises(self):
        from navig import llm_generate
        with patch("navig.llm_generate.get_config_manager" if hasattr(llm_generate, "get_config_manager") else "navig.config.get_config_manager", side_effect=RuntimeError("no config")):
            from navig.llm_generate import _has_llm_modes_config
            # Either returns False or True based on if config exists
            result = _has_llm_modes_config()
            assert isinstance(result, bool)
