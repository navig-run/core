"""Test configuration dictionary type coercion safety in navig.agent subsystems.

This test validates the safety guards placed around `int()` and `float()` casts
when reading potentially malformed configuration dictionaries across the agent codebase.
"""


from navig.agent.model_router import ModelSlot
from navig.agent.prompt_caching import CacheStats


def test_model_slot_safety():
    """Test `ModelSlot.from_dict` safe coercion."""
    valid_data = {"max_tokens": 1024, "temperature": "0.8", "num_ctx": "8192"}
    slot1 = ModelSlot.from_dict(valid_data)
    assert slot1.max_tokens == 1024
    assert slot1.temperature == 0.8
    assert slot1.num_ctx == 8192

    malformed_data = {"max_tokens": "big", "temperature": "hot", "num_ctx": "large"}
    slot2 = ModelSlot.from_dict(malformed_data)
    assert slot2.max_tokens == 512
    assert slot2.temperature == 0.7
    assert slot2.num_ctx == 4096


def test_prompt_caching_safety():
    """Test `CacheStats` safe handling of corrupt API usage metadata."""
    stats = CacheStats()
    valid_usage = {
        "input_tokens": "100", 
        "cache_creation_input_tokens": 50, 
        "cache_read_input_tokens": "25"
    }
    stats.record_response(valid_usage)
    assert stats.total_input_tokens == 100
    assert stats.cache_creation_tokens == 50
    assert stats.cache_read_tokens == 25
    assert stats.api_calls == 1

    malformed_usage = {
        "input_tokens": "none", 
        "cache_creation_input_tokens": "some", 
        "cache_read_input_tokens": "all"
    }
    stats.record_response(malformed_usage)
    assert stats.total_input_tokens == 100  # Should not have changed
    assert stats.cache_creation_tokens == 50
    assert stats.cache_read_tokens == 25
    assert stats.api_calls == 2


def test_agent_config_bool_toggles_coerce_config_set_strings():
    """`navig config set agent.<section>.enabled false` stores the STRING "false"
    (truthy). The agent-config from_dicts must coerce their booleans so a config-set
    toggle actually takes effect — a raw `.get("enabled", True)` read it as ON."""
    from navig.agent.config import (
        AgentConfig,
        MCPConfig,
        PersonalityConfig,
        TelegramConfig,
    )

    # a config-set string "false"/"off"/"0" disables; "true"/"on" enables
    assert TelegramConfig.from_dict({"enabled": "true"}).enabled is True
    assert MCPConfig.from_dict({"enabled": "false"}).enabled is False
    assert AgentConfig.from_dict({"agent": {"enabled": "false"}}).enabled is False
    p = PersonalityConfig.from_dict({"emoji_enabled": "off", "proactive": "0"})
    assert p.emoji_enabled is False and p.proactive is False
    # real bools pass through; missing keys keep each field's default
    assert MCPConfig.from_dict({"enabled": False}).enabled is False
    assert MCPConfig.from_dict({}).enabled is True  # MCPConfig default True
    assert TelegramConfig.from_dict({}).enabled is False  # TelegramConfig default False


def test_hands_safety_gates_honor_config_set_strings():
    """The command-execution safety gates must honor `navig config set` too. The
    critical case: `config set agent.hands.sudo_allowed false` (string "false") must
    DISABLE sudo — a raw read left it truthy (sudo silently stayed on)."""
    from navig.agent.config import HandsConfig

    # sudo_allowed: the string "false" now actually disables sudo (was left ON before)
    assert HandsConfig.from_dict({"sudo_allowed": "false"}).sudo_allowed is False
    assert HandsConfig.from_dict({"sudo_allowed": "true"}).sudo_allowed is True
    # safe_mode: config-set string now matches a direct-YAML edit
    assert HandsConfig.from_dict({"safe_mode": "off"}).safe_mode is False
    assert HandsConfig.from_dict({"safe_mode": "on"}).safe_mode is True
    # real bools pass through; defaults unchanged (safe_mode=True, sudo_allowed=False)
    assert HandsConfig.from_dict({"sudo_allowed": True}).sudo_allowed is True
    assert HandsConfig.from_dict({}).safe_mode is True
    assert HandsConfig.from_dict({}).sudo_allowed is False
