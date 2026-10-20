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


# ── is_chat_model: filter for DISCOVERED catalog ids (model pickers) ────────
# Every id below came from a real provider catalog on 2026-10-15
# (OpenAI 137 ids · Groq 11 · NVIDIA 81 · OpenRouter 100 · xAI 14).


from navig.providers.capabilities import is_chat_model  # noqa: E402

_NON_CHAT_IDS = [
    "tts-1", "tts-1-hd", "gpt-4o-mini-tts", "whisper-1", "whisper-large-v3-turbo",
    "gpt-4o-transcribe", "gpt-live-transcribe", "text-embedding-3-large",
    "nvidia/nv-embedqa-mistral-7b-v2", "snowflake/arctic-embed-l", "nvidia/nemotron-4-340b-reward",
    "gpt-image-1", "chatgpt-image-latest", "sora-2", "omni-moderation-latest",
    "meta-llama/llama-prompt-guard-2-86m", "meta/llama-guard-4-12b",
    "nvidia/llama-3.1-nemoguard-8b-content-safety", "canopylabs/orpheus-v1-english",
    "gpt-realtime", "gpt-realtime-mini", "babbage-002", "davinci-002", "computer-use-preview",
]

_CHAT_IDS = [
    "gpt-5-mini", "gpt-4.1", "gpt-4o", "gpt-4o-audio-preview", "o3", "claude-opus-4-8",
    "claude-sonnet-4-6", "grok-4.20-0309-reasoning", "grok-4.20-multi-agent-0309",
    "meta/llama-3.1-70b-instruct", "nvidia/nemotron-3-super-120b-a12b", "openai/gpt-oss-120b",
    "anthropic/claude-sonnet-4.5", "nvidia/nemotron-3.5-super-vl:free", "~unbiased/pareto-latest",
    "01-ai/yi-large", "deepseek-chat", "mistral-large-latest", "qwen/qwen3-32b",
    # answered a real 1-token chat call in the registry audit (2026-09-26)
    "openai/gpt-oss-safeguard-20b",
]


@pytest.mark.parametrize("model_id", _NON_CHAT_IDS)
def test_is_chat_model_drops_non_chat_families(model_id):
    assert not is_chat_model(model_id), f"{model_id} cannot hold a chat turn"


@pytest.mark.parametrize("model_id", _CHAT_IDS)
def test_is_chat_model_keeps_chat_models(model_id):
    assert is_chat_model(model_id), f"{model_id} is a chat model and must stay pickable"


def test_is_chat_model_empty_id_is_not_a_model():
    assert not is_chat_model("")


# ── current families + intent_models (Auto in the model picker) ──────────────

from navig.providers.capabilities import (  # noqa: E402
    Capability,
    get_model_capabilities,
    intent_models,
)


@pytest.mark.parametrize(("model_id", "fast"), [
    ("claude-haiku-4-5", True), ("anthropic/claude-haiku-4.5", True), ("gpt-5-mini", True),
    ("gpt-5-nano", True), ("grok-4.20-0309-non-reasoning", True), ("openai/gpt-oss-20b", True),
    ("claude-opus-4-8", False), ("claude-sonnet-4-6", False), ("gpt-5", False),
    ("gpt-5.1-codex", False), ("grok-4.6", False), ("openai/gpt-oss-120b", False),
])
def test_current_families_carry_speed_tags(model_id, fast):
    caps, _ = get_model_capabilities(model_id)
    assert (Capability.FAST in caps) is fast, (model_id, caps)


def test_current_families_are_no_longer_bare_text_guesses():
    for m in ("claude-opus-4-8", "gpt-5", "grok-4.6", "openai/gpt-oss-120b"):
        caps, _ = get_model_capabilities(m)
        assert Capability.REASONING in caps, (m, caps)


@pytest.mark.parametrize(("saved", "default", "expect"), [
    # the operator's real connections (2026-10-16)
    (["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"], "claude-opus-4-8",
     {"ask": "claude-haiku-4-5", "act": "claude-opus-4-8"}),
    (["openai/gpt-oss-120b", "openai/gpt-oss-20b", "openai/gpt-oss-safeguard-20b"], "openai/gpt-oss-120b",
     {"ask": "openai/gpt-oss-20b", "act": "openai/gpt-oss-120b"}),
    # no FAST model saved -> Ask falls back to Act's model
    (["claude-opus-4-8"], "claude-opus-4-8", {"ask": "claude-opus-4-8", "act": "claude-opus-4-8"}),
    # no default -> first saved chat id; a non-chat saved id is skipped
    (["tts-1", "gpt-5", "gpt-5-mini"], None, {"ask": "gpt-5-mini", "act": "gpt-5"}),
    # nothing known
    ([], None, {"ask": "", "act": ""}),
])
def test_intent_models(saved, default, expect):
    assert intent_models(saved, default) == expect
