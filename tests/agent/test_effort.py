"""Unit tests for navig/agent/effort.py.

Covers:
- EffortLevel enum membership and string values
- EFFORT_ALIASES canonical and short forms
- auto_detect_effort() keyword matching and word-count heuristics
- resolve_effort() None/EffortLevel/str dispatch and ValueError
- supports_thinking() provider membership
- get_thinking_params() per-provider params structure
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

# ── EffortLevel enum ───────────────────────────────────────────────


class TestEffortLevelEnum:
    def test_five_levels_exist(self):
        levels = [e.value for e in EffortLevel]
        assert sorted(levels) == sorted(["low", "medium", "high", "maximum", "ultrathink"])

    def test_string_values(self):
        assert EffortLevel.LOW.value == "low"
        assert EffortLevel.MEDIUM.value == "medium"
        assert EffortLevel.HIGH.value == "high"
        assert EffortLevel.MAXIMUM.value == "maximum"
        assert EffortLevel.ULTRATHINK.value == "ultrathink"

    def test_enum_iteration_count(self):
        assert len(list(EffortLevel)) == 5


# ── EFFORT_ALIASES ─────────────────────────────────────────────────


class TestEffortAliases:
    def test_canonical_aliases_resolve(self):
        for canonical, expected in [
            ("low", EffortLevel.LOW),
            ("medium", EffortLevel.MEDIUM),
            ("high", EffortLevel.HIGH),
            ("maximum", EffortLevel.MAXIMUM),
            ("ultrathink", EffortLevel.ULTRATHINK),
        ]:
            assert EFFORT_ALIASES[canonical] == expected, f"Alias '{canonical}' wrong"

    def test_short_aliases_resolve(self):
        for short, expected in [
            ("l", EffortLevel.LOW),
            ("lo", EffortLevel.LOW),
            ("m", EffortLevel.MEDIUM),
            ("med", EffortLevel.MEDIUM),
            ("h", EffortLevel.HIGH),
            ("hi", EffortLevel.HIGH),
            ("max", EffortLevel.MAXIMUM),
            ("ultra", EffortLevel.ULTRATHINK),
            ("ut", EffortLevel.ULTRATHINK),
        ]:
            assert EFFORT_ALIASES[short] == expected, f"Short alias '{short}' wrong"

    def test_all_values_are_effort_levels(self):
        for key, value in EFFORT_ALIASES.items():
            assert isinstance(value, EffortLevel), f"Alias '{key}' maps to non-EffortLevel"


# ── auto_detect_effort() ──────────────────────────────────────────


class TestAutoDetectEffort:
    def test_empty_string_returns_medium(self):
        assert auto_detect_effort("") == EffortLevel.MEDIUM

    def test_none_equivalent_empty(self):
        # empty string is the falsy path
        assert auto_detect_effort("") == EffortLevel.MEDIUM

    def test_low_keyword_rename(self):
        assert auto_detect_effort("rename this variable") == EffortLevel.LOW

    def test_low_keyword_fix_typo(self):
        assert auto_detect_effort("fix typo in readme") == EffortLevel.LOW

    def test_low_keyword_bump_version(self):
        assert auto_detect_effort("bump version to 2.0") == EffortLevel.LOW

    def test_low_keyword_add_import(self):
        assert auto_detect_effort("add import for pandas") == EffortLevel.LOW

    def test_high_keyword_architect(self):
        assert auto_detect_effort("architect a new system") == EffortLevel.HIGH

    def test_high_keyword_refactor_entire(self):
        assert auto_detect_effort("refactor entire codebase") == EffortLevel.HIGH

    def test_high_keyword_security_audit(self):
        assert auto_detect_effort("security audit for this module") == EffortLevel.HIGH

    def test_high_keyword_rewrite(self):
        assert auto_detect_effort("rewrite the authentication module") == EffortLevel.HIGH

    def test_high_keyword_migrate(self):
        assert auto_detect_effort("migrate database to PostgreSQL") == EffortLevel.HIGH

    def test_word_count_below_10_returns_low(self):
        # 5 words — below threshold
        assert auto_detect_effort("What time is it") == EffortLevel.LOW

    def test_word_count_above_100_returns_high(self):
        # Build a message with > 100 words, no keyword matches
        long_message = " ".join(["word"] * 110)
        assert auto_detect_effort(long_message) == EffortLevel.HIGH

    def test_word_count_between_10_and_100_returns_medium(self):
        # 20 neutral words
        medium_message = " ".join(["neutral"] * 20)
        assert auto_detect_effort(medium_message) == EffortLevel.MEDIUM

    def test_case_insensitive_low_match(self):
        assert auto_detect_effort("RENAME this function") == EffortLevel.LOW

    def test_case_insensitive_high_match(self):
        assert auto_detect_effort("ARCHITECT a solution") == EffortLevel.HIGH

    def test_low_keyword_wins_over_short_word_count(self):
        # "rename" alone — both short(1 word < 10) and has keyword; LOW either way
        assert auto_detect_effort("rename") == EffortLevel.LOW


# ── resolve_effort() ──────────────────────────────────────────────


class TestResolveEffort:
    def test_none_returns_medium(self):
        assert resolve_effort(None) == EffortLevel.MEDIUM

    def test_effort_level_passthrough(self):
        for level in EffortLevel:
            assert resolve_effort(level) == level

    def test_canonical_string(self):
        assert resolve_effort("low") == EffortLevel.LOW
        assert resolve_effort("medium") == EffortLevel.MEDIUM
        assert resolve_effort("high") == EffortLevel.HIGH
        assert resolve_effort("maximum") == EffortLevel.MAXIMUM
        assert resolve_effort("ultrathink") == EffortLevel.ULTRATHINK

    def test_short_alias_strings(self):
        assert resolve_effort("max") == EffortLevel.MAXIMUM
        assert resolve_effort("ultra") == EffortLevel.ULTRATHINK
        assert resolve_effort("hi") == EffortLevel.HIGH

    def test_case_insensitive(self):
        assert resolve_effort("LOW") == EffortLevel.LOW
        assert resolve_effort("High") == EffortLevel.HIGH
        assert resolve_effort("MEDIUM") == EffortLevel.MEDIUM

    def test_whitespace_stripped(self):
        assert resolve_effort("  low  ") == EffortLevel.LOW

    def test_unknown_string_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown effort level"):
            resolve_effort("extreme")

    def test_error_message_includes_valid_options(self):
        with pytest.raises(ValueError, match="low"):
            resolve_effort("bogus")


# ── supports_thinking() ───────────────────────────────────────────


class TestSupportsThinking:
    def test_anthropic_supported(self):
        assert supports_thinking("anthropic") is True

    def test_openai_supported(self):
        assert supports_thinking("openai") is True

    def test_google_supported(self):
        assert supports_thinking("google") is True

    def test_deepseek_supported(self):
        assert supports_thinking("deepseek") is True

    def test_openrouter_not_supported(self):
        assert supports_thinking("openrouter") is False

    def test_github_models_not_supported(self):
        assert supports_thinking("github_models") is False

    def test_xai_not_supported(self):
        assert supports_thinking("xai") is False

    def test_case_insensitive(self):
        assert supports_thinking("Anthropic") is True
        assert supports_thinking("OPENAI") is True


# ── get_thinking_params() ─────────────────────────────────────────


class TestGetThinkingParams:
    def test_anthropic_low_disables_thinking(self):
        params = get_thinking_params(EffortLevel.LOW, provider="anthropic")
        assert params == {"thinking": {"type": "disabled"}}

    def test_anthropic_medium_enables_thinking(self):
        params = get_thinking_params(EffortLevel.MEDIUM, provider="anthropic")
        assert params["thinking"]["type"] == "enabled"
        assert params["thinking"]["budget_tokens"] == ANTHROPIC_THINKING_BUDGET[EffortLevel.MEDIUM]

    def test_anthropic_high_budget(self):
        params = get_thinking_params(EffortLevel.HIGH, provider="anthropic")
        assert params["thinking"]["budget_tokens"] == 32768

    def test_anthropic_ultrathink_budget(self):
        params = get_thinking_params(EffortLevel.ULTRATHINK, provider="anthropic")
        assert params["thinking"]["budget_tokens"] == 131072

    def test_openai_returns_reasoning_effort(self):
        params = get_thinking_params(EffortLevel.HIGH, provider="openai")
        assert "reasoning_effort" in params
        assert params["reasoning_effort"] == "high"

    def test_openai_medium_effort(self):
        params = get_thinking_params(EffortLevel.MEDIUM, provider="openai")
        assert params["reasoning_effort"] == OPENAI_REASONING_EFFORT[EffortLevel.MEDIUM]

    def test_openai_maximum_maps_to_high(self):
        params = get_thinking_params(EffortLevel.MAXIMUM, provider="openai")
        assert params["reasoning_effort"] == "high"

    def test_openai_ultrathink_maps_to_high(self):
        params = get_thinking_params(EffortLevel.ULTRATHINK, provider="openai")
        assert params["reasoning_effort"] == "high"

    def test_google_returns_thinking_config(self):
        params = get_thinking_params(EffortLevel.HIGH, provider="google")
        assert "thinking_config" in params
        assert (
            params["thinking_config"]["thinking_budget"] == GOOGLE_THINKING_BUDGET[EffortLevel.HIGH]
        )

    def test_google_ultrathink_budget(self):
        params = get_thinking_params(EffortLevel.ULTRATHINK, provider="google")
        assert params["thinking_config"]["thinking_budget"] == 131072

    def test_deepseek_low_disables_thinking(self):
        params = get_thinking_params(EffortLevel.LOW, provider="deepseek")
        assert params == {"thinking": {"type": "disabled"}}

    def test_deepseek_high_enables_thinking(self):
        params = get_thinking_params(EffortLevel.HIGH, provider="deepseek")
        assert params["thinking"]["type"] == "enabled"
        assert params["thinking"]["budget_tokens"] == DEEPSEEK_THINKING_BUDGET[EffortLevel.HIGH]

    def test_unsupported_provider_returns_empty_dict(self):
        params = get_thinking_params(EffortLevel.HIGH, provider="groq")
        assert params == {}

    def test_unknown_provider_returns_empty_dict(self):
        params = get_thinking_params(EffortLevel.MAXIMUM, provider="xai")
        assert params == {}

    def test_provider_case_insensitive(self):
        params_lower = get_thinking_params(EffortLevel.HIGH, provider="anthropic")
        params_upper = get_thinking_params(EffortLevel.HIGH, provider="Anthropic")
        assert params_lower == params_upper


# ── Budget table completeness ─────────────────────────────────────


class TestBudgetTables:
    def test_anthropic_budget_covers_all_levels(self):
        for level in EffortLevel:
            assert level in ANTHROPIC_THINKING_BUDGET, f"Missing {level}"

    def test_openai_reasoning_covers_all_levels(self):
        for level in EffortLevel:
            assert level in OPENAI_REASONING_EFFORT, f"Missing {level}"

    def test_google_budget_covers_all_levels(self):
        for level in EffortLevel:
            assert level in GOOGLE_THINKING_BUDGET, f"Missing {level}"

    def test_deepseek_budget_covers_all_levels(self):
        for level in EffortLevel:
            assert level in DEEPSEEK_THINKING_BUDGET, f"Missing {level}"

    def test_anthropic_budgets_are_positive_ints(self):
        for level, budget in ANTHROPIC_THINKING_BUDGET.items():
            assert isinstance(budget, int) and budget > 0, f"Bad budget for {level}"

    def test_ultrathink_has_highest_anthropic_budget(self):
        assert ANTHROPIC_THINKING_BUDGET[EffortLevel.ULTRATHINK] == max(
            ANTHROPIC_THINKING_BUDGET.values()
        )
