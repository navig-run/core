"""
Hermetic unit tests for navig.agent.prompt_caching

Covers:
- _CACHEABLE_MODELS membership
- supports_caching (exact, prefix, unknown)
- _make_cache_block (with/without ttl)
- apply_anthropic_cache_control (empty, system_and_3, >3 users, unknown strategy)
- ExtendedCacheConfig defaults
- CacheStats: hit_rate, savings_estimate, api_calls
- CacheBreakpointPlacer: system prompt gets cache_control injected
"""

import pytest

from navig.agent.prompt_caching import (
    EXTENDED_CACHE_BETA_HEADER,
    CacheBreakpointPlacer,
    CacheStats,
    ExtendedCacheConfig,
    _CACHEABLE_MODELS,
    _make_cache_block,
    apply_anthropic_cache_control,
    supports_caching,
)


# ─────────────────────────────────────────────────────────────
# _CACHEABLE_MODELS
# ─────────────────────────────────────────────────────────────


class TestCacheableModels:
    def test_contains_claude_sonnet(self):
        assert "claude-3-5-sonnet-20241022" in _CACHEABLE_MODELS

    def test_contains_claude_haiku(self):
        assert "claude-3-5-haiku-20241022" in _CACHEABLE_MODELS

    def test_contains_claude_opus_4(self):
        assert "claude-opus-4" in _CACHEABLE_MODELS

    def test_is_frozenset(self):
        assert isinstance(_CACHEABLE_MODELS, frozenset)


# ─────────────────────────────────────────────────────────────
# supports_caching
# ─────────────────────────────────────────────────────────────


class TestSupportsCaching:
    def test_exact_match(self):
        assert supports_caching("claude-3-5-sonnet-20241022") is True

    def test_exact_match_case_insensitive(self):
        assert supports_caching("Claude-3-5-Sonnet-20241022") is True

    def test_non_cacheable_model(self):
        assert supports_caching("gpt-4o") is False

    def test_unknown_model_returns_false(self):
        assert supports_caching("mystery-llm-9000") is False

    def test_claude_opus_4(self):
        assert supports_caching("claude-opus-4") is True


# ─────────────────────────────────────────────────────────────
# _make_cache_block
# ─────────────────────────────────────────────────────────────


class TestMakeCacheBlock:
    def test_no_ttl_simple_ephemeral(self):
        block = _make_cache_block(None)
        assert block == {"type": "ephemeral"}

    def test_ttl_1h(self):
        block = _make_cache_block("1h")
        assert block["type"] == "ephemeral"
        assert block["ttl"] == 3600

    def test_truthy_non_1h_still_3600(self):
        block = _make_cache_block("2h")
        assert block["ttl"] == 3600

    def test_false_string_no_ttl(self):
        # Empty string is falsy — treated as no TTL
        block = _make_cache_block("")
        assert block == {"type": "ephemeral"}


# ─────────────────────────────────────────────────────────────
# apply_anthropic_cache_control
# ─────────────────────────────────────────────────────────────


def _system(*extra):
    return [{"role": "system", "content": "You are helpful"}] + list(extra)


def _user(text):
    return {"role": "user", "content": text}


def _assistant(text):
    return {"role": "assistant", "content": text}


class TestApplyAnthropicCacheControl:
    def test_empty_messages_returns_same(self):
        assert apply_anthropic_cache_control([]) == []

    def test_unknown_strategy_returns_original(self):
        msgs = [_user("hello")]
        result = apply_anthropic_cache_control(msgs, strategy="unknown")
        assert result == msgs  # same object returned

    def test_system_message_tagged(self):
        msgs = _system(_user("hi"))
        result = apply_anthropic_cache_control(msgs, strategy="system_and_3")
        sys_msg = result[0]
        # The system message content should be a list now
        assert isinstance(sys_msg["content"], list)
        last_block = sys_msg["content"][-1]
        assert "cache_control" in last_block

    def test_first_three_users_tagged(self):
        msgs = [
            _user("q1"),
            _user("q2"),
            _user("q3"),
            _user("q4"),
        ]
        result = apply_anthropic_cache_control(msgs, strategy="system_and_3")
        tagged = [m for m in result if isinstance(m["content"], list)]
        untagged = [m for m in result if isinstance(m["content"], str)]
        assert len(tagged) == 3
        assert len(untagged) == 1

    def test_fourth_user_not_tagged(self):
        msgs = [_user(f"q{i}") for i in range(5)]
        result = apply_anthropic_cache_control(msgs)
        # first 3 users (indices 0,1,2) are tagged; 4th and 5th are not
        assert isinstance(result[2]["content"], list)  # q2 tagged (3rd user)
        assert isinstance(result[3]["content"], str)   # q3 not tagged
        assert isinstance(result[4]["content"], str)   # q4 not tagged

    def test_original_not_mutated(self):
        msgs = [_user("hello")]
        original_content = msgs[0]["content"]
        apply_anthropic_cache_control(msgs)
        assert msgs[0]["content"] == original_content

    def test_with_ttl_1h_cache_block(self):
        msgs = [_user("go")]
        result = apply_anthropic_cache_control(msgs, strategy="system_and_3", ttl="1h")
        tagged = result[0]
        last_block = tagged["content"][-1]
        assert last_block["cache_control"]["ttl"] == 3600

    def test_assistant_messages_not_tagged(self):
        msgs = [_assistant("sure"), _user("q1")]
        result = apply_anthropic_cache_control(msgs)
        # Assistant message should be a deep copy, not tagged
        assert result[0]["role"] == "assistant"
        assert isinstance(result[0]["content"], str)


# ─────────────────────────────────────────────────────────────
# ExtendedCacheConfig
# ─────────────────────────────────────────────────────────────


class TestExtendedCacheConfig:
    def test_defaults(self):
        cfg = ExtendedCacheConfig()
        assert cfg.enabled is True
        assert cfg.beta_header == EXTENDED_CACHE_BETA_HEADER
        assert cfg.max_breakpoints == 4
        assert cfg.min_cacheable_tokens == 1024
        assert cfg.track_stats is True
        assert cfg.cache_system_prompt is True
        assert cfg.cache_tool_definitions is True
        assert cfg.cache_skills_context is True
        assert cfg.cache_conversation_prefix is False

    def test_beta_header_value(self):
        assert EXTENDED_CACHE_BETA_HEADER == "prompt-caching-2024-07-31"

    def test_disabled_config(self):
        cfg = ExtendedCacheConfig()
        cfg.enabled = False
        assert cfg.enabled is False


# ─────────────────────────────────────────────────────────────
# CacheStats
# ─────────────────────────────────────────────────────────────


class TestCacheStats:
    def test_defaults_zero(self):
        s = CacheStats()
        assert s.cache_creation_tokens == 0
        assert s.cache_read_tokens == 0
        assert s.total_input_tokens == 0
        assert s.api_calls == 0

    def test_hit_rate_zero_when_no_tokens(self):
        assert CacheStats().hit_rate == 0.0

    def test_hit_rate_100_percent(self):
        s = CacheStats(total_input_tokens=100, cache_read_tokens=100)
        assert s.hit_rate == 1.0

    def test_hit_rate_partial(self):
        s = CacheStats(total_input_tokens=200, cache_read_tokens=50)
        assert s.hit_rate == pytest.approx(0.25)

    def test_savings_estimate_zero_when_no_reads(self):
        s = CacheStats(cache_read_tokens=0)
        assert s.savings_estimate == 0.0

    def test_savings_estimate_positive(self):
        # 1M read tokens = $3.00 normal - $0.30 cached = $2.70
        s = CacheStats(cache_read_tokens=1_000_000)
        assert s.savings_estimate == pytest.approx(2.70)


# ─────────────────────────────────────────────────────────────
# CacheBreakpointPlacer
# ─────────────────────────────────────────────────────────────


class TestCacheBreakpointPlacer:
    def test_empty_messages_returns_copy(self):
        placer = CacheBreakpointPlacer()
        result = placer.annotate_messages([])
        assert result == []

    def test_disabled_config_returns_deep_copy_unchanged(self):
        placer = CacheBreakpointPlacer()
        cfg = ExtendedCacheConfig()
        cfg.enabled = False
        msgs = [_user("hello")]
        result = placer.annotate_messages(msgs, cfg)
        assert result is not msgs
        assert result[0]["content"] == "hello"

    def test_system_prompt_gets_cache_control(self):
        placer = CacheBreakpointPlacer()
        msgs = [{"role": "system", "content": "You are helpful"}, _user("q")]
        result = placer.annotate_messages(msgs)
        sys_content = result[0]["content"]
        assert isinstance(sys_content, list)
        last = sys_content[-1]
        assert "cache_control" in last

    def test_original_not_mutated(self):
        placer = CacheBreakpointPlacer()
        msgs = [{"role": "system", "content": "Be helpful"}]
        placer.annotate_messages(msgs)
        assert msgs[0]["content"] == "Be helpful"

    def test_non_system_user_not_tagged_by_default(self):
        placer = CacheBreakpointPlacer()
        cfg = ExtendedCacheConfig()
        cfg.cache_system_prompt = False
        cfg.cache_tool_definitions = False
        cfg.cache_skills_context = False
        cfg.cache_conversation_prefix = False
        msgs = [_user("hello")]
        result = placer.annotate_messages(msgs, cfg)
        # No breakpoints placed — content stays string
        assert isinstance(result[0]["content"], str)
