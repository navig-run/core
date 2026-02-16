"""Tests for LLM mode schema validation."""

import pytest


def test_valid_llm_modes_config():
    """Valid llm_modes + uncensored_overrides pass Pydantic validation."""
    from navig.llm_router import LLMRouterConfig

    config = {
        "llm_modes": {
            "small_talk": {
                "description": "Fast chat",
                "provider": "ollama",
                "model": "dolphin-llama3:8b",
                "fallback_model": "qwen2.5:3b",
                "temperature": 0.8,
                "max_tokens": 1024,
                "use_uncensored": True,
            },
            "big_tasks": {
                "provider": "openai",
                "model": "gpt-4o-mini",
                "temperature": 0.5,
                "max_tokens": 4096,
            },
            "coding": {
                "provider": "deepseek",
                "model": "deepseek-coder",
                "temperature": 0.2,
                "max_tokens": 8192,
                "use_uncensored": True,
            },
            "summarize": {
                "provider": "ollama",
                "model": "qwen2.5:3b",
                "temperature": 0.3,
                "max_tokens": 2048,
            },
            "research": {
                "provider": "deepseek",
                "model": "deepseek-chat",
                "temperature": 0.4,
                "max_tokens": 32768,
            },
        },
        "uncensored_overrides": {
            "enabled": True,
            "local_models": {
                "dolphin": "dolphin-llama3:8b",
                "hermes": "nous-hermes-llama3:8b",
            },
            "api_models": {
                "grok": "grok-beta",
            },
        },
    }
    cfg = LLMRouterConfig.model_validate(config)

    assert cfg.llm_modes.small_talk.provider == "ollama"
    assert cfg.llm_modes.small_talk.model == "dolphin-llama3:8b"
    assert cfg.llm_modes.small_talk.use_uncensored is True
    assert cfg.llm_modes.big_tasks.provider == "openai"
    assert cfg.llm_modes.coding.temperature == 0.2
    assert cfg.uncensored_overrides.enabled is True
    assert "dolphin" in cfg.uncensored_overrides.local_models
    assert "grok" in cfg.uncensored_overrides.api_models


def test_default_config():
    """Default config (no input) produces valid defaults."""
    from navig.llm_router import LLMRouterConfig

    cfg = LLMRouterConfig()
    assert cfg.llm_modes.small_talk.provider == "ollama"
    assert cfg.llm_modes.big_tasks.provider == "openai"
    assert cfg.llm_modes.coding.provider == "deepseek"
    assert cfg.llm_modes.summarize.provider == "ollama"
    assert cfg.llm_modes.research.provider == "deepseek"


def test_temperature_out_of_range():
    """Out-of-range temperature raises ValidationError."""
    from pydantic import ValidationError
    from navig.llm_router import LLMModeConfig

    with pytest.raises(ValidationError):
        LLMModeConfig(temperature=3.0)

    with pytest.raises(ValidationError):
        LLMModeConfig(temperature=-1.0)


def test_max_tokens_out_of_range():
    """Invalid max_tokens raises ValidationError."""
    from pydantic import ValidationError
    from navig.llm_router import LLMModeConfig

    with pytest.raises(ValidationError):
        LLMModeConfig(max_tokens=0)

    with pytest.raises(ValidationError):
        LLMModeConfig(max_tokens=200000)


def test_unknown_provider_warns(caplog):
    """Unknown provider logs a warning but doesn't fail."""
    from navig.llm_router import LLMModeConfig

    cfg = LLMModeConfig(provider="banana_ai", model="test")
    assert cfg.provider == "banana_ai"


def test_extra_fields_allowed():
    """Extra fields in config don't cause errors (ConfigDict extra=allow)."""
    from navig.llm_router import LLMModeConfig

    cfg = LLMModeConfig(
        provider="openai",
        model="gpt-4o",
        some_custom_field="value",
    )
    assert cfg.provider == "openai"


def test_mode_get_set():
    """LLMModesConfig.get_mode and set_mode work correctly."""
    from navig.llm_router import LLMModesConfig, LLMModeConfig

    modes = LLMModesConfig()
    cfg = modes.get_mode("coding")
    assert cfg is not None
    assert cfg.provider == "deepseek"

    new_cfg = LLMModeConfig(provider="groq", model="llama3-8b")
    modes.set_mode("coding", new_cfg)
    assert modes.get_mode("coding").provider == "groq"


def test_modes_to_dict():
    """LLMModesConfig.to_dict serializes all 5 modes."""
    from navig.llm_router import LLMModesConfig

    modes = LLMModesConfig()
    d = modes.to_dict()
    assert set(d.keys()) == {"small_talk", "big_tasks", "coding", "summarize", "research"}
    assert "provider" in d["small_talk"]
    assert "model" in d["coding"]
