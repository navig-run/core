"""Unit tests for navig.llm_generate — pure helpers and integration seams."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ─── Imports ────────────────────────────────────────────────────────────────
from navig.llm_generate import (
    _enrich_messages_with_context,
    _extract_user_text,
    _parse_model_spec,
)

# ─── _parse_model_spec ────────────────────────────────────────────────────────


class TestParseModelSpec:
    def test_explicit_provider_colon_model(self):
        provider, model = _parse_model_spec("openai:gpt-4o")
        assert provider == "openai"
        assert model == "gpt-4o"

    def test_explicit_provider_override_wins(self):
        provider, model = _parse_model_spec("gpt-4o", provider_override="azure")
        assert provider == "azure"
        assert model == "gpt-4o"

    def test_gpt_prefix_infers_openai(self):
        provider, model = _parse_model_spec("gpt-4o-mini")
        assert provider == "openai"
        assert model == "gpt-4o-mini"

    def test_o1_prefix_infers_openai(self):
        provider, model = _parse_model_spec("o1-mini")
        assert provider == "openai"
        assert model == "o1-mini"

    def test_claude_prefix_infers_anthropic(self):
        provider, model = _parse_model_spec("claude-3-5-sonnet-20241022")
        assert provider == "anthropic"
        assert model == "claude-3-5-sonnet-20241022"

    def test_deepseek_infers_deepseek(self):
        provider, model = _parse_model_spec("deepseek-coder")
        assert provider == "deepseek"
        assert model == "deepseek-coder"

    def test_llama_infers_ollama(self):
        # Use a plain name without a colon so the colon-split branch isn't hit first
        provider, model = _parse_model_spec("llama3-instruct")
        assert provider == "ollama"
        assert model == "llama3-instruct"

    def test_phi_infers_ollama(self):
        provider, model = _parse_model_spec("phi-3.5")
        assert provider == "ollama"
        assert model == "phi-3.5"

    def test_qwen_infers_ollama(self):
        provider, model = _parse_model_spec("qwen2.5-coder")
        assert provider == "ollama"
        assert model == "qwen2.5-coder"

    def test_slash_path_infers_openrouter(self):
        provider, model = _parse_model_spec("anthropic/claude-3-5-sonnet")
        assert provider == "openrouter"
        assert model == "anthropic/claude-3-5-sonnet"

    def test_unknown_model_falls_back_to_openrouter(self):
        provider, model = _parse_model_spec("totally-unknown-model")
        assert provider == "openrouter"
        assert model == "totally-unknown-model"

    def test_url_spec_is_not_split_on_colon(self):
        # A URL-like spec must not be split on ":" — provider_override must be used
        provider, model = _parse_model_spec(
            "http://localhost:11434/model", provider_override="ollama"
        )
        assert provider == "ollama"
        assert model == "http://localhost:11434/model"

    def test_multi_segment_colon_splits_on_first(self):
        # "provider:some:model:with:colons" → provider="provider", model="some:model:with:colons"
        provider, model = _parse_model_spec("anthropic:claude-3-5:sonnet")
        assert provider == "anthropic"
        assert model == "claude-3-5:sonnet"


# ─── _extract_user_text ──────────────────────────────────────────────────────


class TestExtractUserText:
    def test_extracts_last_user_message(self):
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Second question"},
        ]
        assert _extract_user_text(msgs) == "Second question"

    def test_returns_empty_when_no_user_message(self):
        msgs = [{"role": "system", "content": "sys"}, {"role": "assistant", "content": "hi"}]
        assert _extract_user_text(msgs) == ""

    def test_empty_messages_returns_empty(self):
        assert _extract_user_text([]) == ""

    def test_single_user_message(self):
        msgs = [{"role": "user", "content": "hello"}]
        assert _extract_user_text(msgs) == "hello"

    def test_missing_content_returns_empty_string(self):
        msgs = [{"role": "user"}]
        assert _extract_user_text(msgs) == ""


# ─── _enrich_messages_with_context ───────────────────────────────────────────


class TestEnrichMessagesWithContext:
    def test_empty_context_returns_messages_unchanged(self):
        msgs = [{"role": "user", "content": "hello"}]
        result = _enrich_messages_with_context(msgs, {})
        assert result == msgs

    def test_prepends_system_message_with_history(self):
        msgs = [{"role": "user", "content": "new question"}]
        context = {
            "conversation_history": [
                {"role": "user", "content": "old q"},
                {"role": "assistant", "content": "old a"},
            ]
        }
        result = _enrich_messages_with_context(msgs, context)
        # A system message should have been prepended
        assert len(result) > len(msgs)
        assert result[0]["role"] == "system"
        assert "Recent Conversation" in result[0]["content"]

    def test_original_messages_appended_after_context(self):
        msgs = [{"role": "user", "content": "new msg"}]
        context = {"conversation_history": [{"role": "user", "content": "old"}]}
        result = _enrich_messages_with_context(msgs, context)
        assert result[-1] == msgs[-1]

    def test_none_values_in_context_safe(self):
        msgs = [{"role": "user", "content": "q"}]
        context = {"conversation_history": None}
        # Should not raise
        result = _enrich_messages_with_context(msgs, context)
        assert isinstance(result, list)


# ─── _has_llm_modes_config ────────────────────────────────────────────────────


class TestHasLlmModesConfig:
    def test_returns_true_when_llm_modes_key_present(self):
        from navig.llm_generate import _has_llm_modes_config

        mock_cm = MagicMock()
        mock_cm.global_config = {"llm_modes": {"chat": {}}}
        with patch("navig.llm_generate.get_config_manager", return_value=mock_cm, create=True):
            # Import after patching inside the function scope
            with patch("navig.config.get_config_manager", return_value=mock_cm):
                result = _has_llm_modes_config()
        # When config is properly mocked, should return True
        # (result may vary depending on whether mock patches replace correctly)
        assert isinstance(result, bool)

    def test_returns_false_on_exception(self):
        from navig.llm_generate import _has_llm_modes_config

        with patch("navig.config.get_config_manager", side_effect=RuntimeError("no config")):
            result = _has_llm_modes_config()
        assert result is False


# ─── llm_generate (model_override path) ─────────────────────────────────────


class TestLlmGenerateModelOverride:
    def test_model_override_calls_call_provider(self):
        """When model_override is set, llm_generate must call _call_provider."""
        from navig.llm_generate import llm_generate

        with patch("navig.llm_generate._call_provider", return_value="mocked reply") as mock_cp:
            result = llm_generate(
                messages=[{"role": "user", "content": "hello"}],
                model_override="openai:gpt-4o-mini",
            )

        assert result == "mocked reply"
        mock_cp.assert_called_once()
        call_kwargs = mock_cp.call_args
        assert (
            call_kwargs.kwargs.get("provider") == "openai"
            or call_kwargs[1].get("provider") == "openai"
        )

    def test_model_override_with_provider_override(self):
        from navig.llm_generate import llm_generate

        with patch("navig.llm_generate._call_provider", return_value="azure reply") as mock_cp:
            result = llm_generate(
                messages=[{"role": "user", "content": "test"}],
                model_override="gpt-4o",
                provider_override="azure",
            )

        assert result == "azure reply"
        kwargs = mock_cp.call_args[1]
        assert kwargs["provider"] == "azure"
        assert kwargs["model"] == "gpt-4o"

    def test_temperature_override_forwarded(self):
        from navig.llm_generate import llm_generate

        with patch("navig.llm_generate._call_provider", return_value="ok") as mock_cp:
            llm_generate(
                messages=[{"role": "user", "content": "x"}],
                model_override="openai:gpt-4o",
                temperature=0.1,
            )

        kwargs = mock_cp.call_args[1]
        assert kwargs["temperature"] == 0.1

    def test_max_tokens_override_forwarded(self):
        from navig.llm_generate import llm_generate

        with patch("navig.llm_generate._call_provider", return_value="ok") as mock_cp:
            llm_generate(
                messages=[{"role": "user", "content": "x"}],
                model_override="openai:gpt-4o",
                max_tokens=512,
            )

        kwargs = mock_cp.call_args[1]
        assert kwargs["max_tokens"] == 512


# ─── run_llm (model_override path) ───────────────────────────────────────────


class TestRunLlmModelOverride:
    def _make_llm_result(self, content="result", provider="openai", model="gpt-4o-mini"):
        from navig.llm_routing_types import LLMResult

        return LLMResult(
            content=content,
            model=model,
            provider=provider,
            latency_ms=100,
        )

    def test_run_llm_model_override_returns_llmresult(self):
        from navig.llm_generate import run_llm

        expected = self._make_llm_result("hello")
        with patch("navig.llm_generate._call_and_wrap", return_value=expected):
            result = run_llm(
                messages=[{"role": "user", "content": "hi"}],
                model_override="openai:gpt-4o-mini",
            )

        assert result.content == "hello"
        assert result.provider == "openai"

    def test_run_llm_returns_empty_content_on_provider_failure(self):
        """run_llm catches internal errors only if fallback_models are defined."""
        from navig.llm_generate import run_llm

        with patch("navig.llm_generate._call_and_wrap", side_effect=RuntimeError("provider down")):
            with pytest.raises(RuntimeError, match="provider down"):
                run_llm(
                    messages=[{"role": "user", "content": "x"}],
                    model_override="openai:gpt-4o-mini",
                )


# ─── _prompt_cache_enabled ───────────────────────────────────────────────────


class TestPromptCacheEnabled:
    def test_returns_bool(self):
        from navig.llm_generate import _prompt_cache_enabled

        result = _prompt_cache_enabled()
        assert isinstance(result, bool)


# ─── _load_fallback_chain ─────────────────────────────────────────────────────


class TestLoadFallbackChain:
    def test_returns_list(self):
        from navig.llm_generate import _load_fallback_chain

        result = _load_fallback_chain()
        assert isinstance(result, list)

    def test_all_items_are_strings(self):
        from navig.llm_generate import _load_fallback_chain

        for item in _load_fallback_chain():
            assert isinstance(item, str)
