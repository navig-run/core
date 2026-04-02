"""Tests for FC4 — Extended Prompt Cache (strategic breakpoints + stats).

Covers:
- ExtendedCacheConfig defaults and overrides
- CacheBreakpointPlacer: system, tool-def, skills, conversation-prefix
- Max breakpoints enforcement (hard cap at 4)
- Content-block format (cache_control annotation shape)
- CacheStats accumulation, hit_rate, savings_estimate, reset, summary
- has_cache_breakpoint() utility
- Disabled config passthrough
- apply_anthropic_cache_control with strategy="strategic"
"""

from __future__ import annotations

import copy
import pytest

from navig.agent.prompt_caching import (
    EXTENDED_CACHE_BETA_HEADER,
    CacheBreakpointPlacer,
    CacheStats,
    ExtendedCacheConfig,
    apply_anthropic_cache_control,
    has_cache_breakpoint,
    _content_as_str,
    _is_prefix_boundary,
    _matches_markers,
    _TOOL_DEF_MARKERS,
    _SKILLS_MARKERS,
)


# ── Fixtures ───────────────────────────────────────────────────────


def _system_msg(text: str = "You are a helpful assistant.") -> dict:
    return {"role": "system", "content": text}


def _user_msg(text: str = "Hello") -> dict:
    return {"role": "user", "content": text}


def _assistant_msg(text: str = "Hi there!") -> dict:
    return {"role": "assistant", "content": text}


def _tool_def_msg() -> dict:
    return {
        "role": "user",
        "content": 'Available tools:\n{"type": "function", "name": "bash"}',
    }


def _skills_msg() -> dict:
    return {
        "role": "user",
        "content": "Active skills:\n- code_review\n- file_search",
    }


def _content_block_msg(text: str) -> dict:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


# ── ExtendedCacheConfig ───────────────────────────────────────────


class TestExtendedCacheConfig:
    """Test defaults and overrides."""

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

    def test_override(self):
        cfg = ExtendedCacheConfig(max_breakpoints=2, cache_conversation_prefix=True)
        assert cfg.max_breakpoints == 2
        assert cfg.cache_conversation_prefix is True

    def test_disabled(self):
        cfg = ExtendedCacheConfig(enabled=False)
        assert cfg.enabled is False


# ── CacheBreakpointPlacer ────────────────────────────────────────


class TestBreakpointPlacerBasic:
    """Basic annotation scenarios."""

    def test_empty_messages(self):
        result = CacheBreakpointPlacer().annotate_messages([], ExtendedCacheConfig())
        assert result == []

    def test_disabled_config_returns_deep_copy(self):
        msgs = [_system_msg(), _user_msg()]
        cfg = ExtendedCacheConfig(enabled=False)
        result = CacheBreakpointPlacer().annotate_messages(msgs, cfg)
        assert result == msgs
        # Verify deep copy
        result[0]["content"] = "MODIFIED"
        assert msgs[0]["content"] != "MODIFIED"

    def test_system_prompt_gets_breakpoint(self):
        msgs = [_system_msg(), _user_msg()]
        result = CacheBreakpointPlacer().annotate_messages(msgs)
        # System msg should be annotated
        sys_msg = result[0]
        assert isinstance(sys_msg["content"], list)
        assert any(
            isinstance(b, dict) and "cache_control" in b
            for b in sys_msg["content"]
        )

    def test_tool_definitions_get_breakpoint(self):
        msgs = [_system_msg(), _tool_def_msg(), _user_msg()]
        result = CacheBreakpointPlacer().annotate_messages(msgs)
        tool_msg = result[1]
        assert isinstance(tool_msg["content"], list)
        assert any(
            isinstance(b, dict) and "cache_control" in b
            for b in tool_msg["content"]
        )

    def test_skills_context_gets_breakpoint(self):
        msgs = [_system_msg(), _skills_msg(), _user_msg()]
        result = CacheBreakpointPlacer().annotate_messages(msgs)
        skills_msg = result[1]
        assert isinstance(skills_msg["content"], list)
        assert any(
            isinstance(b, dict) and "cache_control" in b
            for b in skills_msg["content"]
        )

    def test_no_mutation_of_originals(self):
        original_msgs = [_system_msg(), _user_msg()]
        frozen = copy.deepcopy(original_msgs)
        CacheBreakpointPlacer().annotate_messages(original_msgs)
        assert original_msgs == frozen


class TestBreakpointPlacerPriority:
    """Priority ordering and max breakpoints enforcement."""

    def test_all_four_priorities(self):
        """System + tools + skills + prefix → 4 breakpoints when prefix enabled."""
        msgs = [
            _system_msg(),
            _tool_def_msg(),
            _skills_msg(),
        ] + [_user_msg(f"msg-{i}") for i in range(10)]

        cfg = ExtendedCacheConfig(cache_conversation_prefix=True)
        result = CacheBreakpointPlacer().annotate_messages(msgs, cfg)
        bp_count = sum(1 for m in result if has_cache_breakpoint(m))
        assert bp_count == 4

    def test_max_breakpoints_enforced(self):
        """Even with many candidate msgs, never exceed max_breakpoints."""
        msgs = [_system_msg()] + [_tool_def_msg() for _ in range(6)]
        cfg = ExtendedCacheConfig(max_breakpoints=2)
        result = CacheBreakpointPlacer().annotate_messages(msgs, cfg)
        bp_count = sum(1 for m in result if has_cache_breakpoint(m))
        assert bp_count == 2

    def test_reduced_max_breakpoints(self):
        cfg = ExtendedCacheConfig(max_breakpoints=1)
        msgs = [_system_msg(), _tool_def_msg(), _skills_msg()]
        result = CacheBreakpointPlacer().annotate_messages(msgs, cfg)
        bp_count = sum(1 for m in result if has_cache_breakpoint(m))
        assert bp_count == 1
        # Should be the system msg (highest priority)
        assert has_cache_breakpoint(result[0])

    def test_disabled_system_prompt_skips_it(self):
        cfg = ExtendedCacheConfig(cache_system_prompt=False)
        msgs = [_system_msg(), _tool_def_msg()]
        result = CacheBreakpointPlacer().annotate_messages(msgs, cfg)
        assert not has_cache_breakpoint(result[0])  # system skipped
        assert has_cache_breakpoint(result[1])  # tool def still placed

    def test_disabled_tool_defs_skips_them(self):
        cfg = ExtendedCacheConfig(cache_tool_definitions=False)
        msgs = [_system_msg(), _tool_def_msg()]
        result = CacheBreakpointPlacer().annotate_messages(msgs, cfg)
        assert has_cache_breakpoint(result[0])  # system placed
        assert not has_cache_breakpoint(result[1])  # tool def skipped


class TestBreakpointContentBlockFormat:
    """Verify the cache_control annotation shape."""

    def test_annotation_shape_ephemeral(self):
        msgs = [_system_msg()]
        result = CacheBreakpointPlacer().annotate_messages(msgs)
        sys_content = result[0]["content"]
        cache_blocks = [
            b for b in sys_content
            if isinstance(b, dict) and "cache_control" in b
        ]
        assert len(cache_blocks) >= 1
        cc = cache_blocks[0]["cache_control"]
        assert cc["type"] == "ephemeral"

    def test_existing_content_blocks_preserved(self):
        """If content is already a list of blocks, text should be preserved."""
        msgs = [_content_block_msg("System info")]
        msgs[0]["role"] = "system"
        result = CacheBreakpointPlacer().annotate_messages(msgs)
        texts = [
            b.get("text", "") for b in result[0]["content"]
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        assert any("System info" in t for t in texts)


class TestConversationPrefix:
    """Test the optional conversation prefix breakpoint."""

    def test_prefix_on_long_conversation(self):
        msgs = [_system_msg()] + [
            _user_msg(f"turn-{i}") if i % 2 == 0 else _assistant_msg(f"reply-{i}")
            for i in range(20)
        ]
        cfg = ExtendedCacheConfig(
            cache_system_prompt=False,
            cache_tool_definitions=False,
            cache_skills_context=False,
            cache_conversation_prefix=True,
        )
        result = CacheBreakpointPlacer().annotate_messages(msgs, cfg)
        # Should place 1 breakpoint at the 80% boundary
        bp_indices = [i for i, m in enumerate(result) if has_cache_breakpoint(m)]
        assert len(bp_indices) == 1
        boundary = int(len(msgs) * 0.8) - 1
        assert bp_indices[0] == boundary

    def test_prefix_not_on_short_conversation(self):
        msgs = [_system_msg(), _user_msg(), _assistant_msg()]
        cfg = ExtendedCacheConfig(
            cache_system_prompt=False,
            cache_tool_definitions=False,
            cache_skills_context=False,
            cache_conversation_prefix=True,
        )
        result = CacheBreakpointPlacer().annotate_messages(msgs, cfg)
        # < 5 messages → no prefix breakpoint
        bp_count = sum(1 for m in result if has_cache_breakpoint(m))
        assert bp_count == 0


# ── CacheStats ───────────────────────────────────────────────────


class TestCacheStats:
    """Accumulation, properties, reset, summary."""

    def test_initial_state(self):
        stats = CacheStats()
        assert stats.api_calls == 0
        assert stats.total_input_tokens == 0
        assert stats.cache_creation_tokens == 0
        assert stats.cache_read_tokens == 0
        assert stats.hit_rate == 0.0
        assert stats.savings_estimate == 0.0

    def test_record_single_response(self):
        stats = CacheStats()
        stats.record_response({
            "input_tokens": 1000,
            "cache_creation_input_tokens": 800,
            "cache_read_input_tokens": 200,
        })
        assert stats.api_calls == 1
        assert stats.total_input_tokens == 1000
        assert stats.cache_creation_tokens == 800
        assert stats.cache_read_tokens == 200

    def test_record_multiple_responses(self):
        stats = CacheStats()
        stats.record_response({
            "input_tokens": 500,
            "cache_creation_input_tokens": 400,
            "cache_read_input_tokens": 0,
        })
        stats.record_response({
            "input_tokens": 500,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 400,
        })
        assert stats.api_calls == 2
        assert stats.total_input_tokens == 1000
        assert stats.cache_creation_tokens == 400
        assert stats.cache_read_tokens == 400

    def test_hit_rate(self):
        stats = CacheStats()
        stats.record_response({
            "input_tokens": 1000,
            "cache_read_input_tokens": 750,
        })
        assert stats.hit_rate == pytest.approx(0.75)

    def test_savings_estimate(self):
        stats = CacheStats()
        stats.record_response({
            "input_tokens": 1_000_000,
            "cache_read_input_tokens": 1_000_000,
        })
        # Normal = $3.00, cached = $0.30 → savings = $2.70
        assert stats.savings_estimate == pytest.approx(2.70, abs=0.01)

    def test_reset(self):
        stats = CacheStats()
        stats.record_response({"input_tokens": 500, "cache_read_input_tokens": 300})
        stats.reset()
        assert stats.api_calls == 0
        assert stats.total_input_tokens == 0
        assert stats.hit_rate == 0.0

    def test_summary_keys(self):
        stats = CacheStats()
        stats.record_response({"input_tokens": 100})
        s = stats.summary()
        assert "api_calls" in s
        assert "total_input_tokens" in s
        assert "cache_read_tokens" in s
        assert "cache_creation_tokens" in s
        assert "hit_rate" in s
        assert "estimated_savings" in s

    def test_missing_keys_treated_as_zero(self):
        stats = CacheStats()
        stats.record_response({})  # empty dict
        assert stats.api_calls == 1
        assert stats.total_input_tokens == 0
        assert stats.cache_read_tokens == 0


# ── has_cache_breakpoint ─────────────────────────────────────────


class TestHasCacheBreakpoint:
    """Utility for compactor integration."""

    def test_plain_string_content(self):
        assert has_cache_breakpoint({"content": "hello"}) is False

    def test_no_content(self):
        assert has_cache_breakpoint({}) is False

    def test_list_without_cache(self):
        msg = {"content": [{"type": "text", "text": "hello"}]}
        assert has_cache_breakpoint(msg) is False

    def test_list_with_cache(self):
        msg = {
            "content": [
                {"type": "text", "text": "hello", "cache_control": {"type": "ephemeral"}}
            ]
        }
        assert has_cache_breakpoint(msg) is True


# ── apply_anthropic_cache_control strategy="strategic" ───────────


class TestStrategicStrategy:
    """Integration: apply_anthropic_cache_control with strategy='strategic'."""

    def test_strategic_places_breakpoints(self):
        msgs = [_system_msg(), _tool_def_msg(), _user_msg()]
        result = apply_anthropic_cache_control(msgs, strategy="strategic")
        bp_count = sum(1 for m in result if has_cache_breakpoint(m))
        assert bp_count >= 1

    def test_strategic_unknown_model_still_works(self):
        """Strategic strategy doesn't gate on model — caller must check."""
        msgs = [_system_msg()]
        result = apply_anthropic_cache_control(msgs, strategy="strategic")
        assert has_cache_breakpoint(result[0])


# ── Private helpers ──────────────────────────────────────────────


class TestPrivateHelpers:
    """_content_as_str, _matches_markers, _is_prefix_boundary."""

    def test_content_as_str_plain(self):
        assert _content_as_str({"content": "hello"}) == "hello"

    def test_content_as_str_blocks(self):
        msg = {"content": [{"type": "text", "text": "foo"}, {"type": "text", "text": "bar"}]}
        assert "foo" in _content_as_str(msg)
        assert "bar" in _content_as_str(msg)

    def test_content_as_str_empty(self):
        assert _content_as_str({}) == ""

    def test_matches_markers_positive(self):
        assert _matches_markers("Available tools: yes", _TOOL_DEF_MARKERS) is True

    def test_matches_markers_negative(self):
        assert _matches_markers("nothing here", _TOOL_DEF_MARKERS) is False

    def test_is_prefix_boundary_short(self):
        assert _is_prefix_boundary(0, 3) is False

    def test_is_prefix_boundary_correct_index(self):
        # 10 messages → boundary at int(10 * 0.8) - 1 = 7
        assert _is_prefix_boundary(7, 10) is True
        assert _is_prefix_boundary(6, 10) is False
