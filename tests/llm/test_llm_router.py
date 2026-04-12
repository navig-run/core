"""Tests for LLMModeRouter — alias resolution, detect_mode, and routing."""

from unittest.mock import patch
import pytest

pytestmark = pytest.mark.integration

# ── Alias Resolution ─────────────────────────────────────


class TestAliasResolution:
    def test_canonical_names(self):
        from navig.llm_router import LLMModeRouter

        r = LLMModeRouter({})
        assert r.resolve_mode("small_talk") == "small_talk"
        assert r.resolve_mode("big_tasks") == "big_tasks"
        assert r.resolve_mode("coding") == "coding"
        assert r.resolve_mode("summarize") == "summarize"
        assert r.resolve_mode("research") == "research"

    def test_small_talk_aliases(self):
        from navig.llm_router import LLMModeRouter

        r = LLMModeRouter({})
        for alias in ("small", "chat", "casual", "talk", "hi", "hello"):
            assert r.resolve_mode(alias) == "small_talk", f"{alias} should map to small_talk"

    def test_big_tasks_aliases(self):
        from navig.llm_router import LLMModeRouter

        r = LLMModeRouter({})
        for alias in ("big", "complex", "plan", "reason", "think"):
            assert r.resolve_mode(alias) == "big_tasks", f"{alias} should map to big_tasks"

    def test_coding_aliases(self):
        from navig.llm_router import LLMModeRouter

        r = LLMModeRouter({})
        for alias in ("code", "dev", "debug", "program", "script"):
            assert r.resolve_mode(alias) == "coding", f"{alias} should map to coding"

    def test_summarize_aliases(self):
        from navig.llm_router import LLMModeRouter

        r = LLMModeRouter({})
        for alias in ("sum", "summary", "tl;dr", "tldr", "digest"):
            assert r.resolve_mode(alias) == "summarize", f"{alias} should map to summarize"

    def test_research_aliases(self):
        from navig.llm_router import LLMModeRouter

        r = LLMModeRouter({})
        for alias in ("research", "analysis", "compare", "sources", "analyze", "study"):
            assert r.resolve_mode(alias) == "research", f"{alias} should map to research"

    def test_unknown_defaults_to_big_tasks(self):
        from navig.llm_router import LLMModeRouter

        r = LLMModeRouter({})
        assert r.resolve_mode("unknown_thing") == "big_tasks"
        assert r.resolve_mode("") == "big_tasks"

    def test_case_insensitive(self):
        from navig.llm_router import LLMModeRouter

        r = LLMModeRouter({})
        assert r.resolve_mode("CHAT") == "small_talk"
        assert r.resolve_mode("Code") == "coding"


# ── Mode Detection ───────────────────────────────────────


class TestDetectMode:
    def test_code_snippets(self):
        from navig.llm_router import detect_mode

        assert detect_mode("```python\nprint('hello')\n```") == "coding"
        assert detect_mode("def calculate_total(items):") == "coding"
        assert detect_mode("Write a script to process CSV files") == "coding"
        assert detect_mode("fix this code: function foo() {}") == "coding"
        assert detect_mode("debug the python module") == "coding"

    def test_greetings_small_talk(self):
        from navig.llm_router import detect_mode

        assert detect_mode("hey") == "small_talk"
        assert detect_mode("Hi there!") == "small_talk"
        assert detect_mode("Hello, how are you?") == "small_talk"
        assert detect_mode("good morning") == "small_talk"

    def test_casual_small_talk(self):
        from navig.llm_router import detect_mode

        assert detect_mode("thanks") == "small_talk"
        assert detect_mode("ok") == "small_talk"
        assert detect_mode("cool") == "small_talk"
        assert detect_mode("lol") == "small_talk"

    def test_summarize_keywords(self):
        from navig.llm_router import detect_mode

        assert detect_mode("summarize this document for me") == "summarize"
        assert detect_mode("tl;dr of the meeting notes") == "summarize"
        assert detect_mode("give me a brief overview of this") == "summarize"

    def test_research_keywords(self):
        from navig.llm_router import detect_mode

        assert detect_mode("research the latest AI trends") == "research"
        assert detect_mode("analyze this data set and compare") == "research"
        assert detect_mode("what are the differences between X and Y") == "research"
        assert detect_mode("investigate the performance issue") == "research"

    def test_ambiguous_defaults_to_big_tasks(self):
        from navig.llm_router import detect_mode

        assert (
            detect_mode(
                "I need you to create a comprehensive plan for the project migration including all dependencies and timeline"
            )
            == "big_tasks"
        )

    def test_short_question_small_talk(self):
        from navig.llm_router import detect_mode

        assert detect_mode("what time is it?") == "small_talk"

    def test_empty_input(self):
        from navig.llm_router import detect_mode

        assert detect_mode("") == "small_talk"


# ── Uncensored Routing ───────────────────────────────────


class TestUncensoredRouting:
    def _make_router(self):
        from navig.llm_router import LLMModeRouter

        config = {
            "llm_modes": {
                "small_talk": {
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "use_uncensored": True,
                    "temperature": 0.8,
                    "max_tokens": 1024,
                },
            },
            "uncensored_overrides": {
                "enabled": True,
                "local_models": {"dolphin": "dolphin-llama3:8b"},
                "api_models": {"grok": "grok-beta"},
            },
        }
        return LLMModeRouter(config)

    @patch("navig.llm_router._check_ollama_models")
    @patch("navig.llm_router._has_api_key")
    def test_uncensored_routes_to_local_ollama(self, mock_has_key, mock_ollama):
        """When use_uncensored=True + censored provider → selects local Ollama model."""
        mock_ollama.return_value = {"dolphin-llama3:8b": True, "dolphin-llama3": True}
        mock_has_key.return_value = True

        router = self._make_router()
        resolved = router.get_config("small_talk")

        assert resolved.provider == "ollama"
        assert resolved.model == "dolphin-llama3:8b"
        assert resolved.is_uncensored is True
        assert "local" in resolved.resolution_reason.lower()

    @patch("navig.llm_router._check_ollama_models")
    @patch("navig.llm_router._has_api_key")
    def test_uncensored_fallback_to_api(self, mock_has_key, mock_ollama):
        """When local not available → falls back to API uncensored model."""
        mock_ollama.return_value = {}  # No local models
        mock_has_key.return_value = True

        router = self._make_router()
        resolved = router.get_config("small_talk")

        assert resolved.model == "grok-beta"
        assert resolved.is_uncensored is True
        assert "api" in resolved.resolution_reason.lower()

    @patch("navig.llm_router._check_ollama_models")
    @patch("navig.llm_router._has_api_key")
    def test_uncensored_disabled_uses_standard(self, mock_has_key, mock_ollama):
        """When prefer_uncensored=False → standard route."""
        mock_ollama.return_value = {"dolphin-llama3:8b": True}
        mock_has_key.return_value = True

        router = self._make_router()
        resolved = router.get_config("small_talk", prefer_uncensored=False)

        assert resolved.provider == "openai"
        assert resolved.is_uncensored is False

    def test_resolution_reason_always_populated(self):
        """Every resolution has a non-empty resolution_reason."""
        from navig.llm_router import CANONICAL_MODES, LLMModeRouter

        router = LLMModeRouter({})
        for mode in CANONICAL_MODES:
            resolved = router.get_config(mode)
            assert resolved.resolution_reason, f"resolution_reason empty for {mode}"


# ── ResolvedLLMConfig ────────────────────────────────────


class TestResolvedLLMConfig:
    def test_to_dict(self):
        from navig.llm_router import ResolvedLLMConfig

        cfg = ResolvedLLMConfig(
            provider="openai",
            model="gpt-4o",
            mode="big_tasks",
            resolution_reason="test",
        )
        d = cfg.to_dict()
        assert d["provider"] == "openai"
        assert d["model"] == "gpt-4o"
        assert d["mode"] == "big_tasks"

    def test_repr(self):
        from navig.llm_router import ResolvedLLMConfig

        cfg = ResolvedLLMConfig(provider="ollama", model="dolphin:8b", mode="small_talk")
        assert "ollama" in repr(cfg)
        assert "dolphin" in repr(cfg)


def test_resolve_api_key_xai_accepts_grok_key_env(monkeypatch):
    from navig.llm_router import _resolve_api_key

    monkeypatch.setenv("GROK_KEY", "grok-test-key")

    assert _resolve_api_key("xai") == "grok-test-key"


def test_default_provider_override_keeps_provider_compatible_model():
    from navig.llm_router import LLMModeRouter

    router = LLMModeRouter(
        {
            "ai": {"default_provider": "openai"},
            "llm_modes": {
                "small_talk": {"provider": "ollama", "model": "qwen2.5:3b"},
                "big_tasks": {"provider": "openai", "model": "gpt-4o-mini"},
                "coding": {"provider": "openrouter", "model": "deepseek/deepseek-coder"},
                "summarize": {"provider": "openrouter", "model": "openai/gpt-4o-mini"},
                "research": {"provider": "openrouter", "model": "openai/gpt-4o-mini"},
            },
        }
    )

    resolved = router.get_config("small_talk")
    assert resolved.provider == "openai"
    assert resolved.model == "gpt-4o-mini"
    assert "default_provider override" in resolved.resolution_reason


def test_class_and_module_detect_mode_match_for_research():
    from navig.llm_router import LLMModeRouter, detect_mode

    text = "please research and compare these two approaches"
    router = LLMModeRouter({})

    assert router.detect_mode(text) == detect_mode(text)
