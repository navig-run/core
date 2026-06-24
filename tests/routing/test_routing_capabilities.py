"""Unit tests for navig/routing/capabilities.py.

Covers:
- CAPABILITY_TAGS structure and membership
- ModeProfile.__init__ (defaults, frozenset coercion)
- ModeProfile.score_model() (pass/fail/score logic)
- MODE_CAPABILITIES completeness and type correctness
- OPENROUTER_MODELS / GITHUB_MODELS / OLLAMA_MODELS structure
"""

from __future__ import annotations

import pytest

from navig.routing.capabilities import (
    CAPABILITY_TAGS,
    GITHUB_MODELS,
    MODE_CAPABILITIES,
    OLLAMA_MODELS,
    OPENROUTER_MODELS,
    ModeProfile,
)

# ── CAPABILITY_TAGS ────────────────────────────────────────────────


class TestCapabilityTags:
    """CAPABILITY_TAGS must be an immutable frozenset with expected tags."""

    def test_is_frozenset(self):
        assert isinstance(CAPABILITY_TAGS, frozenset)

    def test_expected_tags_present(self):
        for tag in ("fast", "strong", "coder", "format_strict", "tool_capable", "long_context"):
            assert tag in CAPABILITY_TAGS, f"Tag '{tag}' missing from CAPABILITY_TAGS"

    def test_no_duplicate_tags(self):
        # frozenset by construction, just confirm uniqueness via length proxy
        assert len(CAPABILITY_TAGS) >= 6

    def test_tags_are_strings(self):
        for tag in CAPABILITY_TAGS:
            assert isinstance(tag, str)


# ── ModeProfile ────────────────────────────────────────────────────


class TestModeProfile:
    """ModeProfile init and score_model() logic."""

    def test_required_coerced_to_frozenset(self):
        p = ModeProfile(required={"fast"})
        assert isinstance(p.required, frozenset)
        assert "fast" in p.required

    def test_preferred_defaults_to_empty_frozenset(self):
        p = ModeProfile(required={"fast"})
        assert isinstance(p.preferred, frozenset)
        assert len(p.preferred) == 0

    def test_preferred_coerced_to_frozenset(self):
        p = ModeProfile(required={"fast"}, preferred={"strong"})
        assert isinstance(p.preferred, frozenset)
        assert "strong" in p.preferred

    def test_cost_target_default(self):
        p = ModeProfile(required=set())
        assert p.cost_target == "medium"

    def test_latency_target_default(self):
        p = ModeProfile(required=set())
        assert p.latency_target == "medium"

    def test_custom_cost_latency_targets(self):
        p = ModeProfile(required=set(), cost_target="minimal", latency_target="low")
        assert p.cost_target == "minimal"
        assert p.latency_target == "low"

    # score_model() tests

    def test_score_minus_one_when_required_not_met(self):
        p = ModeProfile(required={"strong", "coder"})
        # Model only has "fast" — neither required tag
        assert p.score_model(frozenset({"fast"})) == -1

    def test_score_minus_one_when_partially_required_not_met(self):
        p = ModeProfile(required={"strong", "coder"})
        # Model has "strong" but not "coder" → still fails
        assert p.score_model(frozenset({"strong", "fast"})) == -1

    def test_score_zero_when_required_met_no_preferred(self):
        p = ModeProfile(required={"fast"}, preferred=set())
        assert p.score_model(frozenset({"fast", "coder"})) == 0

    def test_score_equals_preferred_match_count(self):
        p = ModeProfile(required={"fast"}, preferred={"strong", "coder", "format_strict"})
        # Model has all required + 2 preferred
        score = p.score_model(frozenset({"fast", "strong", "coder"}))
        assert score == 2

    def test_score_max_when_all_preferred_met(self):
        p = ModeProfile(required={"fast"}, preferred={"strong", "coder"})
        score = p.score_model(frozenset({"fast", "strong", "coder"}))
        assert score == 2

    def test_score_with_empty_model_caps_and_no_required(self):
        p = ModeProfile(required=set(), preferred={"fast"})
        assert p.score_model(frozenset()) == 0

    def test_score_with_all_required_and_no_preferred(self):
        p = ModeProfile(required={"coder"}, preferred=set())
        assert p.score_model(frozenset({"coder", "fast", "strong"})) == 0


# ── MODE_CAPABILITIES ─────────────────────────────────────────────


class TestModeCapabilities:
    """MODE_CAPABILITIES must cover all expected modes with valid ModeProfiles."""

    EXPECTED_MODES = {"coding", "small_talk", "big_tasks", "summarize", "research"}

    def test_all_expected_modes_present(self):
        for mode in self.EXPECTED_MODES:
            assert mode in MODE_CAPABILITIES, f"Mode '{mode}' missing from MODE_CAPABILITIES"

    def test_all_values_are_mode_profiles(self):
        for mode, profile in MODE_CAPABILITIES.items():
            assert isinstance(profile, ModeProfile), (
                f"MODE_CAPABILITIES['{mode}'] is not a ModeProfile"
            )

    def test_required_tags_are_valid_capability_tags(self):
        for mode, profile in MODE_CAPABILITIES.items():
            for tag in profile.required:
                assert tag in CAPABILITY_TAGS, (
                    f"Unknown tag '{tag}' in MODE_CAPABILITIES['{mode}'].required"
                )

    def test_preferred_tags_are_valid_capability_tags(self):
        for mode, profile in MODE_CAPABILITIES.items():
            for tag in profile.preferred:
                assert tag in CAPABILITY_TAGS, (
                    f"Unknown tag '{tag}' in MODE_CAPABILITIES['{mode}'].preferred"
                )

    def test_coding_requires_coder(self):
        assert "coder" in MODE_CAPABILITIES["coding"].required

    def test_small_talk_requires_fast(self):
        assert "fast" in MODE_CAPABILITIES["small_talk"].required

    def test_big_tasks_requires_strong(self):
        assert "strong" in MODE_CAPABILITIES["big_tasks"].required

    def test_cost_targets_are_strings(self):
        for mode, profile in MODE_CAPABILITIES.items():
            assert isinstance(profile.cost_target, str), f"cost_target for '{mode}' is not a string"

    def test_latency_targets_are_strings(self):
        for mode, profile in MODE_CAPABILITIES.items():
            assert isinstance(profile.latency_target, str), (
                f"latency_target for '{mode}' is not a string"
            )

    def test_score_model_works_for_all_modes(self):
        """ModeProfile.score_model() must not raise for any mode in MODE_CAPABILITIES."""
        all_caps = frozenset(CAPABILITY_TAGS)
        for mode, profile in MODE_CAPABILITIES.items():
            score = profile.score_model(all_caps)
            assert score >= 0, f"score_model failed for mode '{mode}' with all capabilities"


# ── Provider model tables ─────────────────────────────────────────


class TestProviderModelTables:
    """OPENROUTER_MODELS, GITHUB_MODELS, OLLAMA_MODELS must be well-formed."""

    @pytest.mark.parametrize(
        "table, name",
        [
            (OPENROUTER_MODELS, "OPENROUTER_MODELS"),
            (GITHUB_MODELS, "GITHUB_MODELS"),
            (OLLAMA_MODELS, "OLLAMA_MODELS"),
        ],
    )
    def test_table_is_dict(self, table, name):
        assert isinstance(table, dict), f"{name} is not a dict"

    @pytest.mark.parametrize(
        "table, name",
        [
            (OPENROUTER_MODELS, "OPENROUTER_MODELS"),
            (GITHUB_MODELS, "GITHUB_MODELS"),
            (OLLAMA_MODELS, "OLLAMA_MODELS"),
        ],
    )
    def test_keys_are_strings(self, table, name):
        for key in table:
            assert isinstance(key, str), f"{name} key {key!r} is not a string"

    @pytest.mark.parametrize(
        "table, name",
        [
            (OPENROUTER_MODELS, "OPENROUTER_MODELS"),
            (GITHUB_MODELS, "GITHUB_MODELS"),
            (OLLAMA_MODELS, "OLLAMA_MODELS"),
        ],
    )
    def test_values_are_frozensets(self, table, name):
        for model, caps in table.items():
            assert isinstance(caps, frozenset), (
                f"{name}['{model}'] capability set is not a frozenset"
            )

    @pytest.mark.parametrize(
        "table, name",
        [
            (OPENROUTER_MODELS, "OPENROUTER_MODELS"),
            (GITHUB_MODELS, "GITHUB_MODELS"),
            (OLLAMA_MODELS, "OLLAMA_MODELS"),
        ],
    )
    def test_capability_tags_are_valid(self, table, name):
        for model, caps in table.items():
            for tag in caps:
                assert tag in CAPABILITY_TAGS, (
                    f"{name}['{model}'] has unknown capability tag '{tag}'"
                )

    def test_openrouter_has_gpt4o(self):
        assert "openai/gpt-4o" in OPENROUTER_MODELS

    def test_openrouter_claude_has_coder_tag(self):
        assert "coder" in OPENROUTER_MODELS["anthropic/claude-sonnet-4.5"]

    def test_github_models_has_gpt4o(self):
        assert "gpt-4o" in GITHUB_MODELS

    def test_ollama_has_qwen_coder(self):
        assert "qwen2.5-coder:14b" in OLLAMA_MODELS

    def test_ollama_qwen_coder_has_coder_tag(self):
        assert "coder" in OLLAMA_MODELS["qwen2.5-coder:14b"]

    def test_tables_are_non_empty(self):
        assert len(OPENROUTER_MODELS) > 0
        assert len(GITHUB_MODELS) > 0
        assert len(OLLAMA_MODELS) > 0
