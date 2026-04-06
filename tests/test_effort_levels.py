"""Tests for FA-02 — Effort Levels (multi-tier thinking budget).

Covers:
- EffortLevel enum values
- Alias resolution (short + canonical forms)
- Provider budget maps (Anthropic, OpenAI, Google, DeepSeek)
- get_thinking_params() output shape per provider
- Auto-detection heuristics (LOW / HIGH / MEDIUM)
- Unsupported provider → empty dict (graceful no-op)
- resolve_effort() error handling
- supports_thinking() utility
"""

from __future__ import annotations

import pytest

from navig.agent.effort import (
    ANTHROPIC_THINKING_BUDGET,
    DEEPSEEK_THINKING_BUDGET,
    EFFORT_ALIASES,
    GOOGLE_THINKING_BUDGET,
    OPENAI_REASONING_EFFORT,
    EffortLevel,
    auto_detect_effort,
    get_thinking_params,
    resolve_effort,
    supports_thinking,
)

# ── EffortLevel Enum ─────────────────────────────────────────────


class TestEffortLevelEnum:
    """All five levels present with correct values."""

    def test_all_levels_present(self):
        levels = {e.value for e in EffortLevel}
        assert levels == {"low", "medium", "high", "maximum", "ultrathink"}

    def test_enum_count(self):
        assert len(EffortLevel) == 5

    def test_values_are_strings(self):
        for level in EffortLevel:
            assert isinstance(level.value, str)


# ── Alias Resolution ─────────────────────────────────────────────


class TestResolveEffort:
    """resolve_effort handles aliases, None, and bad input."""

    def test_none_returns_medium(self):
        assert resolve_effort(None) is EffortLevel.MEDIUM

    def test_passthrough_enum(self):
        assert resolve_effort(EffortLevel.HIGH) is EffortLevel.HIGH

    def test_canonical_strings(self):
        for val in ("low", "medium", "high", "maximum", "ultrathink"):
            assert resolve_effort(val).value == val

    def test_short_aliases(self):
        assert resolve_effort("l") is EffortLevel.LOW
        assert resolve_effort("lo") is EffortLevel.LOW
        assert resolve_effort("m") is EffortLevel.MEDIUM
        assert resolve_effort("med") is EffortLevel.MEDIUM
        assert resolve_effort("h") is EffortLevel.HIGH
        assert resolve_effort("hi") is EffortLevel.HIGH
        assert resolve_effort("max") is EffortLevel.MAXIMUM
        assert resolve_effort("ultra") is EffortLevel.ULTRATHINK
        assert resolve_effort("ut") is EffortLevel.ULTRATHINK

    def test_case_insensitive(self):
        assert resolve_effort("LOW") is EffortLevel.LOW
        assert resolve_effort("Ultra") is EffortLevel.ULTRATHINK
        assert resolve_effort("HI") is EffortLevel.HIGH

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown effort level"):
            resolve_effort("turbo")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            resolve_effort("")

    def test_aliases_cover_all_levels(self):
        """Every EffortLevel is reachable from at least one alias."""
        reachable = set(EFFORT_ALIASES.values())
        assert reachable == set(EffortLevel)


# ── Provider Budget Maps ─────────────────────────────────────────


class TestBudgetMaps:
    """Each map has all 5 levels keyed."""

    def test_anthropic_all_levels(self):
        assert set(ANTHROPIC_THINKING_BUDGET.keys()) == set(EffortLevel)

    def test_openai_all_levels(self):
        assert set(OPENAI_REASONING_EFFORT.keys()) == set(EffortLevel)

    def test_google_all_levels(self):
        assert set(GOOGLE_THINKING_BUDGET.keys()) == set(EffortLevel)

    def test_deepseek_all_levels(self):
        assert set(DEEPSEEK_THINKING_BUDGET.keys()) == set(EffortLevel)

    def test_anthropic_budget_values(self):
        assert ANTHROPIC_THINKING_BUDGET[EffortLevel.LOW] == 1024
        assert ANTHROPIC_THINKING_BUDGET[EffortLevel.MEDIUM] == 8192
        assert ANTHROPIC_THINKING_BUDGET[EffortLevel.HIGH] == 32768
        assert ANTHROPIC_THINKING_BUDGET[EffortLevel.MAXIMUM] == 65536
        assert ANTHROPIC_THINKING_BUDGET[EffortLevel.ULTRATHINK] == 131072

    def test_openai_values(self):
        assert OPENAI_REASONING_EFFORT[EffortLevel.LOW] == "low"
        assert OPENAI_REASONING_EFFORT[EffortLevel.MEDIUM] == "medium"
        assert OPENAI_REASONING_EFFORT[EffortLevel.HIGH] == "high"
        assert OPENAI_REASONING_EFFORT[EffortLevel.MAXIMUM] == "high"
        assert OPENAI_REASONING_EFFORT[EffortLevel.ULTRATHINK] == "high"

    def test_budgets_monotonically_increasing(self):
        order = [EffortLevel.LOW, EffortLevel.MEDIUM, EffortLevel.HIGH,
                 EffortLevel.MAXIMUM, EffortLevel.ULTRATHINK]
        for budget_map in (ANTHROPIC_THINKING_BUDGET, GOOGLE_THINKING_BUDGET, DEEPSEEK_THINKING_BUDGET):
            values = [budget_map[lvl] for lvl in order]
            assert values == sorted(values), f"Budget map not monotonic: {values}"


# ── get_thinking_params ──────────────────────────────────────────


class TestGetThinkingParams:
    """Provider-specific param generation."""

    def test_anthropic_high(self):
        params = get_thinking_params(EffortLevel.HIGH, provider="anthropic")
        assert params == {"thinking": {"type": "enabled", "budget_tokens": 32768}}

    def test_anthropic_low_disabled(self):
        params = get_thinking_params(EffortLevel.LOW, provider="anthropic")
        assert params == {"thinking": {"type": "disabled"}}

    def test_anthropic_ultrathink(self):
        params = get_thinking_params(EffortLevel.ULTRATHINK, provider="anthropic")
        assert params["thinking"]["budget_tokens"] == 131072

    def test_openai_medium(self):
        params = get_thinking_params(EffortLevel.MEDIUM, provider="openai")
        assert params == {"reasoning_effort": "medium"}

    def test_openai_low(self):
        params = get_thinking_params(EffortLevel.LOW, provider="openai")
        assert params == {"reasoning_effort": "low"}

    def test_google_high(self):
        params = get_thinking_params(EffortLevel.HIGH, provider="google")
        assert params == {"thinking_config": {"thinking_budget": 32768}}

    def test_deepseek_medium(self):
        params = get_thinking_params(EffortLevel.MEDIUM, provider="deepseek")
        assert params["thinking"]["budget_tokens"] == 8192

    def test_deepseek_low_disabled(self):
        params = get_thinking_params(EffortLevel.LOW, provider="deepseek")
        assert params == {"thinking": {"type": "disabled"}}

    def test_unsupported_provider_empty(self):
        params = get_thinking_params(EffortLevel.HIGH, provider="mistral")
        assert params == {}

    def test_unsupported_unknown_provider(self):
        params = get_thinking_params(EffortLevel.ULTRATHINK, provider="local-llama")
        assert params == {}

    def test_provider_case_insensitive(self):
        params = get_thinking_params(EffortLevel.HIGH, provider="ANTHROPIC")
        assert "thinking" in params


# ── supports_thinking ────────────────────────────────────────────


class TestSupportsThinking:

    def test_supported_providers(self):
        for p in ("anthropic", "openai", "google", "deepseek"):
            assert supports_thinking(p) is True

    def test_unsupported_providers(self):
        for p in ("mistral", "groq", "local", "openrouter"):
            assert supports_thinking(p) is False

    def test_case_insensitive(self):
        assert supports_thinking("Anthropic") is True
        assert supports_thinking("OPENAI") is True


# ── Auto-Detection Heuristics ────────────────────────────────────


class TestAutoDetectEffort:

    def test_empty_returns_medium(self):
        assert auto_detect_effort("") is EffortLevel.MEDIUM

    def test_low_keyword_fix_typo(self):
        assert auto_detect_effort("fix typo in readme") is EffortLevel.LOW

    def test_low_keyword_rename(self):
        assert auto_detect_effort("rename variable foo to bar") is EffortLevel.LOW

    def test_low_keyword_add_import(self):
        assert auto_detect_effort("add import for os") is EffortLevel.LOW

    def test_high_keyword_architect(self):
        assert auto_detect_effort("architect a new auth system") is EffortLevel.HIGH

    def test_high_keyword_security_audit(self):
        assert auto_detect_effort("security audit the codebase") is EffortLevel.HIGH

    def test_high_keyword_refactor_entire(self):
        assert auto_detect_effort("refactor entire module structure") is EffortLevel.HIGH

    def test_short_message_low(self):
        assert auto_detect_effort("hello") is EffortLevel.LOW

    def test_long_message_high(self):
        # > 100 words → HIGH
        msg = " ".join(f"word{i}" for i in range(120))
        assert auto_detect_effort(msg) is EffortLevel.HIGH

    def test_medium_message(self):
        # 10-100 words, no keyword → MEDIUM
        msg = " ".join(f"word{i}" for i in range(50))
        assert auto_detect_effort(msg) is EffortLevel.MEDIUM

    def test_keyword_overrides_length(self):
        # Short but with HIGH keyword → HIGH
        assert auto_detect_effort("architect it") is EffortLevel.HIGH

    def test_low_keyword_overrides_length(self):
        # Medium-length but with LOW keyword
        msg = "please rename this variable in the module to something better"
        assert auto_detect_effort(msg) is EffortLevel.LOW


# ── Integration: CompletionRequest.extra_body ────────────────────


class TestCompletionRequestExtraBody:
    """Verify the extra_body field on CompletionRequest works."""

    def test_default_none(self):
        from navig.providers.clients import CompletionRequest, Message

        req = CompletionRequest(messages=[Message(role="user", content="hi")], model="test")
        assert req.extra_body is None

    def test_with_thinking_params(self):
        from navig.providers.clients import CompletionRequest, Message

        params = get_thinking_params(EffortLevel.HIGH, provider="anthropic")
        req = CompletionRequest(
            messages=[Message(role="user", content="hi")],
            model="test",
            extra_body=params,
        )
        assert req.extra_body == {"thinking": {"type": "enabled", "budget_tokens": 32768}}
