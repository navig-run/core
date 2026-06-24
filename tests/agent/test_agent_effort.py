"""
Batch 38 — navig/agent/effort.py

Covers:
  EffortLevel: enum values
  EFFORT_ALIASES: canonical + short aliases
  ANTHROPIC_THINKING_BUDGET: budget values
  OPENAI_REASONING_EFFORT: effort strings
  resolve_effort(): None→MEDIUM, EffortLevel passthrough, alias strings, ValueError
  auto_detect_effort(): empty→MEDIUM, low keywords, high keywords,
                        word count <10→LOW, word count >100→HIGH, default MEDIUM
  supports_thinking(): known vs unknown providers
  get_thinking_params(): anthropic (LOW→disabled, HIGH→enabled), openai, google, deepseek, unknown
"""

from __future__ import annotations

import pytest

from navig.agent.effort import (
    ANTHROPIC_THINKING_BUDGET,
    EFFORT_ALIASES,
    GOOGLE_THINKING_BUDGET,
    OPENAI_REASONING_EFFORT,
    EffortLevel,
    auto_detect_effort,
    get_thinking_params,
    resolve_effort,
    supports_thinking,
)


# ---------------------------------------------------------------------------
# EffortLevel
# ---------------------------------------------------------------------------

class TestEffortLevel:
    def test_values(self):
        assert EffortLevel.LOW.value == "low"
        assert EffortLevel.MEDIUM.value == "medium"
        assert EffortLevel.HIGH.value == "high"
        assert EffortLevel.MAXIMUM.value == "maximum"
        assert EffortLevel.ULTRATHINK.value == "ultrathink"

    def test_five_levels(self):
        assert len(EffortLevel) == 5


# ---------------------------------------------------------------------------
# EFFORT_ALIASES
# ---------------------------------------------------------------------------

class TestEffortAliases:
    def test_canonical_low(self):
        assert EFFORT_ALIASES["low"] == EffortLevel.LOW

    def test_canonical_medium(self):
        assert EFFORT_ALIASES["medium"] == EffortLevel.MEDIUM

    def test_canonical_ultrathink(self):
        assert EFFORT_ALIASES["ultrathink"] == EffortLevel.ULTRATHINK

    def test_short_ultra(self):
        assert EFFORT_ALIASES["ultra"] == EffortLevel.ULTRATHINK

    def test_short_h(self):
        assert EFFORT_ALIASES["h"] == EffortLevel.HIGH

    def test_short_m(self):
        assert EFFORT_ALIASES["m"] == EffortLevel.MEDIUM

    def test_short_max(self):
        assert EFFORT_ALIASES["max"] == EffortLevel.MAXIMUM


# ---------------------------------------------------------------------------
# ANTHROPIC / OPENAI / GOOGLE budget maps
# ---------------------------------------------------------------------------

class TestBudgetMaps:
    def test_anthropic_low_smallest(self):
        assert ANTHROPIC_THINKING_BUDGET[EffortLevel.LOW] == 1024

    def test_anthropic_ultrathink_largest(self):
        assert ANTHROPIC_THINKING_BUDGET[EffortLevel.ULTRATHINK] == 131072

    def test_openai_low_string(self):
        assert OPENAI_REASONING_EFFORT[EffortLevel.LOW] == "low"

    def test_openai_ultrathink_is_high(self):
        assert OPENAI_REASONING_EFFORT[EffortLevel.ULTRATHINK] == "high"

    def test_google_high_value(self):
        assert GOOGLE_THINKING_BUDGET[EffortLevel.HIGH] == 32768


# ---------------------------------------------------------------------------
# resolve_effort
# ---------------------------------------------------------------------------

class TestResolveEffort:
    def test_none_returns_medium(self):
        assert resolve_effort(None) == EffortLevel.MEDIUM

    def test_effort_level_passthrough(self):
        assert resolve_effort(EffortLevel.HIGH) == EffortLevel.HIGH

    def test_string_canonical(self):
        assert resolve_effort("low") == EffortLevel.LOW
        assert resolve_effort("high") == EffortLevel.HIGH

    def test_string_alias_ultra(self):
        assert resolve_effort("ultra") == EffortLevel.ULTRATHINK

    def test_string_case_insensitive(self):
        assert resolve_effort("MEDIUM") == EffortLevel.MEDIUM

    def test_unknown_string_raises(self):
        with pytest.raises(ValueError):
            resolve_effort("impossible_level")

    def test_short_lo(self):
        assert resolve_effort("lo") == EffortLevel.LOW

    def test_short_med(self):
        assert resolve_effort("med") == EffortLevel.MEDIUM


# ---------------------------------------------------------------------------
# auto_detect_effort
# ---------------------------------------------------------------------------

class TestAutoDetectEffort:
    def test_empty_string_returns_medium(self):
        assert auto_detect_effort("") == EffortLevel.MEDIUM

    def test_low_keyword_fix_typo(self):
        assert auto_detect_effort("fix typo in README") == EffortLevel.LOW

    def test_low_keyword_rename(self):
        assert auto_detect_effort("rename variable x to y") == EffortLevel.LOW

    def test_high_keyword_refactor_entire(self):
        assert auto_detect_effort("refactor entire codebase") == EffortLevel.HIGH

    def test_high_keyword_security_audit(self):
        assert auto_detect_effort("security audit of all endpoints") == EffortLevel.HIGH

    def test_short_message_low(self):
        # < 10 words, no specific keyword
        assert auto_detect_effort("update the README file") == EffortLevel.LOW

    def test_long_message_high(self):
        # > 100 words
        long_text = " ".join(["word"] * 110)
        assert auto_detect_effort(long_text) == EffortLevel.HIGH

    def test_medium_message_no_keywords(self):
        # 20–50 words with no matching patterns
        text = " ".join(["ordinary"] * 25)
        assert auto_detect_effort(text) == EffortLevel.MEDIUM

    def test_case_insensitive_keyword(self):
        assert auto_detect_effort("Fix Typo in the header") == EffortLevel.LOW

    def test_plan_keyword_is_high(self):
        assert auto_detect_effort("plan the next release step by step") == EffortLevel.HIGH


# ---------------------------------------------------------------------------
# supports_thinking
# ---------------------------------------------------------------------------

class TestSupportsThinking:
    def test_anthropic_supported(self):
        assert supports_thinking("anthropic") is True

    def test_openai_supported(self):
        assert supports_thinking("openai") is True

    def test_google_supported(self):
        assert supports_thinking("google") is True

    def test_deepseek_supported(self):
        assert supports_thinking("deepseek") is True

    def test_unknown_not_supported(self):
        assert supports_thinking("myprovider") is False

    def test_case_insensitive(self):
        assert supports_thinking("Anthropic") is True


# ---------------------------------------------------------------------------
# get_thinking_params
# ---------------------------------------------------------------------------

class TestGetThinkingParams:
    def test_anthropic_low_disabled(self):
        params = get_thinking_params(EffortLevel.LOW, provider="anthropic")
        assert params["thinking"]["type"] == "disabled"

    def test_anthropic_high_enabled(self):
        params = get_thinking_params(EffortLevel.HIGH, provider="anthropic")
        assert params["thinking"]["type"] == "enabled"
        assert params["thinking"]["budget_tokens"] == 32768

    def test_anthropic_ultrathink_budget(self):
        params = get_thinking_params(EffortLevel.ULTRATHINK, provider="anthropic")
        assert params["thinking"]["budget_tokens"] == 131072

    def test_openai_low(self):
        params = get_thinking_params(EffortLevel.LOW, provider="openai")
        assert params["reasoning_effort"] == "low"

    def test_openai_high(self):
        params = get_thinking_params(EffortLevel.HIGH, provider="openai")
        assert params["reasoning_effort"] == "high"

    def test_google_medium(self):
        params = get_thinking_params(EffortLevel.MEDIUM, provider="google")
        assert "thinking_config" in params
        assert params["thinking_config"]["thinking_budget"] > 0

    def test_deepseek_low_disabled(self):
        params = get_thinking_params(EffortLevel.LOW, provider="deepseek")
        assert params["thinking"]["type"] == "disabled"

    def test_deepseek_high_enabled(self):
        params = get_thinking_params(EffortLevel.HIGH, provider="deepseek")
        assert params["thinking"]["type"] == "enabled"

    def test_unknown_provider_empty(self):
        params = get_thinking_params(EffortLevel.HIGH, provider="llama_local")
        assert params == {}

    def test_case_insensitive_provider(self):
        params = get_thinking_params(EffortLevel.HIGH, provider="Anthropic")
        assert "thinking" in params
