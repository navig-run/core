"""Unit tests for navig/ai.py — AIAssistant helpers and ask_ai_with_context.

Covers:
- _get_model_preference:  canonical key / legacy key (DeprecationWarning) / default
- _resolve_openrouter_api_key: env var / canonical / legacy / empty / return_source
- ask_ai_with_context: message construction, history, system prompt, effort pass-through
- AIAssistant.analyze_error: delegation + exception swallowing
- AIAssistant.generate_context_summary: backward-compat identity return
"""

from __future__ import annotations

import warnings
from unittest.mock import MagicMock, patch

import pytest

from navig.ai import (
    _DEFAULT_MODELS,
    AIAssistant,
    _get_model_preference,
    _resolve_openrouter_api_key,
    ask_ai_with_context,
)

# ---------------------------------------------------------------------------
# _get_model_preference
# ---------------------------------------------------------------------------


class TestGetModelPreference:
    def test_canonical_path(self):
        cfg = {"ai": {"model_preference": ["modelA", "modelB"]}}
        result = _get_model_preference(cfg)
        assert result == ["modelA", "modelB"]

    def test_canonical_path_returns_copy(self):
        inner = ["x"]
        cfg = {"ai": {"model_preference": inner}}
        result = _get_model_preference(cfg)
        result.append("y")
        assert inner == ["x"], "must not mutate original list"

    def test_legacy_path_emits_deprecation_warning(self):
        cfg = {"ai_model_preference": ["legacyA"]}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _get_model_preference(cfg)
        assert result == ["legacyA"]
        assert any(issubclass(warning.category, DeprecationWarning) for warning in w)

    def test_legacy_warning_message_mentions_canonical_key(self):
        cfg = {"ai_model_preference": ["m"]}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _get_model_preference(cfg)
        msg = str(w[0].message)
        assert "ai.model_preference" in msg

    def test_canonical_wins_over_legacy(self):
        cfg = {
            "ai": {"model_preference": ["canonical"]},
            "ai_model_preference": ["legacy"],
        }
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _get_model_preference(cfg)
        assert result == ["canonical"]
        assert not w, "no deprecation warning should be emitted when canonical key is present"

    def test_default_fallback_when_no_key(self):
        result = _get_model_preference({})
        assert result == list(_DEFAULT_MODELS)

    def test_default_fallback_returns_copy(self):
        result1 = _get_model_preference({})
        result1.clear()
        result2 = _get_model_preference({})
        assert result2 == list(_DEFAULT_MODELS)


# ---------------------------------------------------------------------------
# _resolve_openrouter_api_key
# ---------------------------------------------------------------------------


class TestResolveOpenrouterApiKey:
    def test_env_var_takes_priority(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "env-key-123")
        result = _resolve_openrouter_api_key({"ai": {"api_key": "config-key"}})
        assert result == "env-key-123"

    def test_canonical_ai_api_key_used_when_no_env(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        result = _resolve_openrouter_api_key({"ai": {"api_key": "canonical-key"}})
        assert result == "canonical-key"

    def test_legacy_openrouter_key_used_as_fallback(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        result = _resolve_openrouter_api_key({"openrouter_api_key": "legacy-key"})
        assert result == "legacy-key"

    def test_empty_string_returned_when_nothing_configured(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        result = _resolve_openrouter_api_key({})
        assert result == ""

    def test_return_source_env(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        key, source = _resolve_openrouter_api_key({}, return_source=True)
        assert key == "k"
        assert source == "env"

    def test_return_source_canonical(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        key, source = _resolve_openrouter_api_key({"ai": {"api_key": "cfg"}}, return_source=True)
        assert key == "cfg"
        assert source == "ai.api_key"

    def test_return_source_legacy(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        key, source = _resolve_openrouter_api_key({"openrouter_api_key": "leg"}, return_source=True)
        assert key == "leg"
        assert source == "openrouter_api_key"

    def test_return_source_none(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        key, source = _resolve_openrouter_api_key({}, return_source=True)
        assert key == ""
        assert source == "none"

    def test_whitespace_only_env_var_ignored(self, monkeypatch):
        """Whitespace-only env var must not be treated as a valid key."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "   ")
        result = _resolve_openrouter_api_key({"openrouter_api_key": "fallback"})
        assert result == "fallback"


# ---------------------------------------------------------------------------
# ask_ai_with_context
# ---------------------------------------------------------------------------


class TestAskAiWithContext:
    # ask_ai_with_context does `from navig.llm.generate import llm_generate`
    # inside the function body, so we must patch at the source module.
    _PATCH = "navig.llm.generate.llm_generate"

    def test_basic_prompt_becomes_user_message(self):
        with patch(self._PATCH, return_value="reply") as mock:
            result = ask_ai_with_context("hello")
        assert result == "reply"
        messages = mock.call_args.kwargs["messages"]
        assert messages[-1] == {"role": "user", "content": "hello"}

    def test_system_prompt_prepended(self):
        with patch(self._PATCH, return_value="r") as mock:
            ask_ai_with_context("q", system_prompt="be helpful")
        messages = mock.call_args.kwargs["messages"]
        assert messages[0] == {"role": "system", "content": "be helpful"}

    def test_history_included(self):
        history = [
            {"role": "user", "content": "prev q"},
            {"role": "assistant", "content": "prev a"},
        ]
        with patch(self._PATCH, return_value="r") as mock:
            ask_ai_with_context("new q", history=history)
        messages = mock.call_args.kwargs["messages"]
        assert messages[-3:-1] == history

    def test_model_override_forwarded(self):
        with patch(self._PATCH, return_value="r") as mock:
            ask_ai_with_context("q", model="gpt-4o")
        assert mock.call_args.kwargs.get("model_override") == "gpt-4o"

    def test_effort_forwarded(self):
        with patch(self._PATCH, return_value="r") as mock:
            ask_ai_with_context("q", effort="high")
        assert mock.call_args.kwargs.get("effort") == "high"

    def test_no_system_prompt_skipped(self):
        with patch(self._PATCH, return_value="r") as mock:
            ask_ai_with_context("q")
        messages = mock.call_args.kwargs["messages"]
        roles = [m["role"] for m in messages]
        assert "system" not in roles

    def test_empty_history_not_added(self):
        with patch(self._PATCH, return_value="r") as mock:
            ask_ai_with_context("q", history=None)
        messages = mock.call_args.kwargs["messages"]
        assert len(messages) == 1  # only the user message


# ---------------------------------------------------------------------------
# AIAssistant.analyze_error
# ---------------------------------------------------------------------------


class TestAIAssistantAnalyzeError:
    def _make_assistant(self):
        cfg = MagicMock()
        cfg.get_ai_system_prompt.return_value = ""
        return AIAssistant(cfg)

    def test_returns_analysis_from_ask(self):
        assistant = self._make_assistant()
        with patch.object(assistant, "ask", return_value="root cause: bad perm"):
            result = assistant.analyze_error("ls /secret", "Permission denied", {})
        assert result == "root cause: bad perm"

    def test_prompt_includes_command_and_error(self):
        assistant = self._make_assistant()
        captured = {}

        def fake_ask(prompt, ctx):
            captured["prompt"] = prompt
            return "analysis"

        with patch.object(assistant, "ask", side_effect=fake_ask):
            assistant.analyze_error("rm -rf /", "err msg", {})

        assert "rm -rf /" in captured["prompt"]
        assert "err msg" in captured["prompt"]

    def test_exception_in_ask_returns_fallback_message(self):
        assistant = self._make_assistant()
        with patch.object(assistant, "ask", side_effect=RuntimeError("api down")):
            result = assistant.analyze_error("cmd", "err", {})
        assert "unavailable" in result.lower()
        assert "api down" in result


# ---------------------------------------------------------------------------
# AIAssistant.generate_context_summary — backward-compat identity
# ---------------------------------------------------------------------------


class TestAIAssistantGenerateContextSummary:
    def test_returns_input_unchanged(self):
        cfg = MagicMock()
        assistant = AIAssistant(cfg)
        ctx = {"host": "prod", "cpu": 42}
        result = assistant.generate_context_summary(ctx)
        assert result is ctx  # exact same object — identity return

    def test_returns_empty_dict_unchanged(self):
        cfg = MagicMock()
        assistant = AIAssistant(cfg)
        result = assistant.generate_context_summary({})
        assert result == {}


# ---------------------------------------------------------------------------
# AIAssistant.ask — surfaces an account/model rotation via last_fallback
# ---------------------------------------------------------------------------


def _boom(*a, **k):
    raise RuntimeError("no memory in test")


class TestAskSurfacesFallback:
    def test_last_fallback_set_when_connection_rotates(self, monkeypatch):
        # Keep memory enrichment hermetic (all best-effort → "").
        monkeypatch.setattr("navig.memory.manager.get_memory_manager", _boom, raising=False)
        monkeypatch.setattr(
            "navig.memory.knowledge_graph.get_knowledge_graph", _boom, raising=False
        )
        cfg = MagicMock()
        cfg.get_ai_system_prompt.return_value = "SYS"
        ai = AIAssistant(cfg)
        monkeypatch.setattr(ai, "_get_conv_store", lambda: None)
        monkeypatch.setattr(ai, "_build_context_string", lambda ctx: "CTX")
        monkeypatch.setattr(
            "navig.providers.inference.has_routable_connection", lambda *a, **k: True
        )

        def fake_complete(*, on_fallback=None, **kw):
            if on_fallback:
                on_fallback({"from": "Claude A", "to": "Claude B", "reason": "rate_limited"})
            return "answer"

        monkeypatch.setattr(
            "navig.providers.inference.complete_via_connection", fake_complete
        )

        out = ai.ask("hi", {"server": {"name": "x"}})
        assert out == "answer"
        assert ai.last_fallback is not None
        assert ai.last_fallback["to"] == "Claude B"
        assert ai.last_fallback["reason"] == "rate_limited"

    def test_last_fallback_none_when_no_rotation(self, monkeypatch):
        monkeypatch.setattr("navig.memory.manager.get_memory_manager", _boom, raising=False)
        monkeypatch.setattr(
            "navig.memory.knowledge_graph.get_knowledge_graph", _boom, raising=False
        )
        cfg = MagicMock()
        cfg.get_ai_system_prompt.return_value = "SYS"
        ai = AIAssistant(cfg)
        monkeypatch.setattr(ai, "_get_conv_store", lambda: None)
        monkeypatch.setattr(ai, "_build_context_string", lambda ctx: "CTX")
        monkeypatch.setattr(
            "navig.providers.inference.has_routable_connection", lambda *a, **k: True
        )
        monkeypatch.setattr(
            "navig.providers.inference.complete_via_connection",
            lambda *, on_fallback=None, **kw: "answer",
        )

        out = ai.ask("hi", {"server": {"name": "x"}})
        assert out == "answer"
        assert ai.last_fallback is None  # no rotation → nothing to surface


# ---------------------------------------------------------------------------
# AIAssistant.ask — when EVERY account is exhausted, give an actionable message
# classified by the canonical categorizer (not fragile string matching).
# ---------------------------------------------------------------------------


class TestAskExhaustionMessages:
    def _assistant_that_raises(self, monkeypatch, exc: Exception) -> AIAssistant:
        monkeypatch.setattr("navig.memory.manager.get_memory_manager", _boom, raising=False)
        monkeypatch.setattr(
            "navig.memory.knowledge_graph.get_knowledge_graph", _boom, raising=False
        )
        cfg = MagicMock()
        cfg.get_ai_system_prompt.return_value = "SYS"
        ai = AIAssistant(cfg)
        monkeypatch.setattr(ai, "_get_conv_store", lambda: None)
        monkeypatch.setattr(ai, "_build_context_string", lambda ctx: "CTX")
        monkeypatch.setattr(
            "navig.providers.inference.has_routable_connection", lambda *a, **k: True
        )

        def _raise(*, on_fallback=None, **kw):
            raise exc

        monkeypatch.setattr("navig.providers.inference.complete_via_connection", _raise)
        return ai

    def test_quota_wording_is_treated_as_rate_limit(self, monkeypatch):
        # The OLD ad-hoc matching only looked for "rate_limit"/"429" and MISSED
        # "quota exceeded" (a real Anthropic 429 phrasing) → generic error. The
        # categorizer catches it, so the operator gets the actionable message.
        ai = self._assistant_that_raises(
            monkeypatch, RuntimeError("Anthropic API error: quota exceeded")
        )
        with pytest.raises(RuntimeError, match="rate-limited"):
            ai.ask("hi", {"server": {"name": "x"}})

    def test_auth_exhaustion_tells_you_to_reconnect(self, monkeypatch):
        ai = self._assistant_that_raises(
            monkeypatch, RuntimeError("401 Unauthorized: invalid api key")
        )
        with pytest.raises(RuntimeError) as ei:
            ai.ask("hi", {"server": {"name": "x"}})
        msg = str(ei.value)
        assert "authenticate" in msg
        assert "navig connect login" in msg
        assert "--model" in msg  # the escape hatch is always offered

    def test_payment_exhaustion_mentions_billing(self, monkeypatch):
        ai = self._assistant_that_raises(
            monkeypatch, RuntimeError("402 Payment Required: insufficient credit")
        )
        with pytest.raises(RuntimeError, match="billing"):
            ai.ask("hi", {"server": {"name": "x"}})

    def test_unclassified_error_keeps_generic_message(self, monkeypatch):
        ai = self._assistant_that_raises(
            monkeypatch, RuntimeError("connection reset by peer at socket layer")
        )
        with pytest.raises(RuntimeError, match="Selected AI connection failed"):
            ai.ask("hi", {"server": {"name": "x"}})
