"""Test configuration dictionary type coercion safety in navig.agent subsystems.

This test validates the safety guards placed around `int()` and `float()` casts
when reading potentially malformed configuration dictionaries across the agent codebase.
"""

from typing import Any

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
