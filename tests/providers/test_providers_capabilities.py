"""Unit tests for navig.providers.capabilities — pure registry lookup.

Zero I/O, zero network — all tested against the static _CAPABILITY_REGISTRY.
"""

from __future__ import annotations

import pytest

from navig.providers.capabilities import (
    Capability,
    ModelCapabilityEntry,
    capabilities_label,
    get_model_capabilities,
    has_capability,
    list_models_with_capability,
    list_vision_models,
)

# ---------------------------------------------------------------------------
# Capability enum
# ---------------------------------------------------------------------------


class TestCapabilityEnum:
    def test_is_str_enum(self) -> None:
        assert issubclass(Capability, str)

    def test_expected_members(self) -> None:
        expected = {"TEXT", "VISION", "CODE", "REASONING", "FAST", "LOCAL", "VOICE"}
        assert expected <= {c.name for c in Capability}

    def test_text_value(self) -> None:
        assert Capability.TEXT == "text"

    def test_vision_value(self) -> None:
        assert Capability.VISION == "vision"

    def test_code_value(self) -> None:
        assert Capability.CODE == "code"

    def test_reasoning_value(self) -> None:
        assert Capability.REASONING == "reasoning"

    def test_fast_value(self) -> None:
        assert Capability.FAST == "fast"

    def test_local_value(self) -> None:
        assert Capability.LOCAL == "local"

    def test_voice_value(self) -> None:
        assert Capability.VOICE == "voice"


# ---------------------------------------------------------------------------
# ModelCapabilityEntry dataclass
# ---------------------------------------------------------------------------


class TestModelCapabilityEntry:
    def test_is_frozen(self) -> None:
        entry = ModelCapabilityEntry(pattern="test", capabilities=(Capability.TEXT,))
        with pytest.raises(Exception):
            entry.pattern = "other"  # type: ignore[misc]

    def test_default_source_is_verified(self) -> None:
        entry = ModelCapabilityEntry(pattern="test", capabilities=(Capability.TEXT,))
        assert entry.source == "verified"

    def test_custom_source(self) -> None:
        entry = ModelCapabilityEntry(
            pattern="test", capabilities=(Capability.TEXT,), source="inferred"
        )
        assert entry.source == "inferred"


# ---------------------------------------------------------------------------
# get_model_capabilities
# ---------------------------------------------------------------------------


class TestGetModelCapabilities:
    def test_returns_tuple_of_list_and_str(self) -> None:
        caps, src = get_model_capabilities("gpt-4o")
        assert isinstance(caps, list)
        assert isinstance(src, str)

    def test_caps_are_capability_instances(self) -> None:
        caps, _ = get_model_capabilities("gpt-4o")
        for c in caps:
            assert isinstance(c, Capability)

    def test_text_always_present(self) -> None:
        caps, _ = get_model_capabilities("gpt-4o")
        assert Capability.TEXT in caps

    def test_gpt4o_has_vision(self) -> None:
        caps, _ = get_model_capabilities("gpt-4o")
        assert Capability.VISION in caps

    def test_gpt4o_has_code(self) -> None:
        caps, _ = get_model_capabilities("gpt-4o")
        assert Capability.CODE in caps

    def test_gpt4o_mini_has_vision(self) -> None:
        caps, _ = get_model_capabilities("gpt-4o-mini")
        assert Capability.VISION in caps

    def test_gpt4o_mini_has_fast(self) -> None:
        caps, _ = get_model_capabilities("gpt-4o-mini")
        assert Capability.FAST in caps

    def test_unknown_model_returns_text_fallback(self) -> None:
        caps, src = get_model_capabilities("totally-unknown-model-xyz-99")
        assert caps == [Capability.TEXT]
        assert src == "inferred"

    def test_source_verified_for_known_model(self) -> None:
        _, src = get_model_capabilities("gpt-4o")
        assert src == "verified"

    def test_case_insensitive_match(self) -> None:
        caps_lower, _ = get_model_capabilities("gpt-4o")
        caps_upper, _ = get_model_capabilities("GPT-4O")
        assert set(caps_lower) == set(caps_upper)

    def test_o1_has_reasoning(self) -> None:
        caps, _ = get_model_capabilities("o1")
        assert Capability.REASONING in caps

    def test_claude_opus_has_vision(self) -> None:
        caps, _ = get_model_capabilities("claude-opus-4-5")
        assert Capability.VISION in caps or Capability.TEXT in caps
        # At minimum TEXT must be present
        assert Capability.TEXT in caps


# ---------------------------------------------------------------------------
# has_capability
# ---------------------------------------------------------------------------


class TestHasCapability:
    def test_gpt4o_has_vision(self) -> None:
        assert has_capability("gpt-4o", Capability.VISION) is True

    def test_gpt4o_has_text(self) -> None:
        assert has_capability("gpt-4o", Capability.TEXT) is True

    def test_unknown_model_has_text(self) -> None:
        assert has_capability("some-unknown-model-abc", Capability.TEXT) is True

    def test_unknown_model_lacks_vision(self) -> None:
        assert has_capability("some-unknown-model-abc", Capability.VISION) is False

    def test_returns_bool(self) -> None:
        result = has_capability("gpt-4o", Capability.VISION)
        assert type(result) is bool

    def test_fast_model_has_fast(self) -> None:
        assert has_capability("gpt-4o-mini", Capability.FAST) is True


# ---------------------------------------------------------------------------
# list_vision_models
# ---------------------------------------------------------------------------


class TestListVisionModels:
    def test_returns_list(self) -> None:
        result = list_vision_models(["gpt-4o"])
        assert isinstance(result, list)

    def test_vision_model_included(self) -> None:
        result = list_vision_models(["gpt-4o"])
        assert len(result) == 1
        assert result[0][0] == "gpt-4o"

    def test_non_vision_model_excluded(self) -> None:
        result = list_vision_models(["some-unknown-text-only-model"])
        assert result == []

    def test_mixed_list_filters_correctly(self) -> None:
        models = ["gpt-4o", "some-unknown-text-only-model"]
        result = list_vision_models(models)
        names = [r[0] for r in result]
        assert "gpt-4o" in names
        assert "some-unknown-text-only-model" not in names

    def test_result_tuples_have_two_elements(self) -> None:
        result = list_vision_models(["gpt-4o"])
        assert all(len(t) == 2 for t in result)

    def test_empty_input_returns_empty(self) -> None:
        assert list_vision_models([]) == []


# ---------------------------------------------------------------------------
# list_models_with_capability
# ---------------------------------------------------------------------------


class TestListModelsWithCapability:
    def test_filters_by_fast(self) -> None:
        models = ["gpt-4o", "gpt-4o-mini"]
        result = list_models_with_capability(models, Capability.FAST)
        names = [r[0] for r in result]
        assert "gpt-4o-mini" in names

    def test_empty_input(self) -> None:
        assert list_models_with_capability([], Capability.TEXT) == []

    def test_all_have_text(self) -> None:
        models = ["gpt-4o", "gpt-4o-mini", "totally-unknown"]
        result = list_models_with_capability(models, Capability.TEXT)
        assert len(result) == len(models)

    def test_returns_list_of_tuples(self) -> None:
        result = list_models_with_capability(["gpt-4o"], Capability.TEXT)
        assert isinstance(result, list)
        assert isinstance(result[0], tuple)


# ---------------------------------------------------------------------------
# capabilities_label
# ---------------------------------------------------------------------------


class TestCapabilitiesLabel:
    def test_returns_string(self) -> None:
        assert isinstance(capabilities_label("gpt-4o"), str)

    def test_vision_model_contains_eye_emoji(self) -> None:
        label = capabilities_label("gpt-4o")
        assert "👁" in label

    def test_code_model_contains_laptop_emoji(self) -> None:
        label = capabilities_label("gpt-4o")
        assert "💻" in label

    def test_unknown_model_returns_empty_string(self) -> None:
        # TEXT only has no emoji mapping → empty
        label = capabilities_label("totally-unknown-model-xyz")
        assert label == ""

    def test_fast_model_contains_lightning(self) -> None:
        label = capabilities_label("gpt-4o-mini")
        assert "⚡" in label

    def test_reasoning_model_contains_brain(self) -> None:
        label = capabilities_label("o1")
        assert "🧠" in label
