"""Tests for ReactiveCompactor (FA5 — Reactive Compaction).

Covers:
- Threshold detection (should_compact / compute_target)
- Compaction with cache-awareness
- Recent-turn preservation
- Digest building
- Stats tracking
- Edge cases (too short, summarizer failure, repeated compaction)
- Factory function
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from navig.agent.context_compressor import (
    ReactiveCompactor,
    _default_summarizer,
    _estimate_messages_tokens,
    _estimate_tokens,
    get_reactive_compactor,
)

# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────


def _make_msg(role: str, content: str, **extra) -> dict:
    """Build a minimal message dict."""
    msg = {"role": role, "content": content}
    msg.update(extra)
    return msg


def _make_conversation(n: int = 10, msg_size: int = 200) -> list[dict]:
    """Build a synthetic conversation with *n* messages.

    Index 0 is always a system prompt.  Subsequent messages alternate
    user/assistant.
    """
    msgs = [_make_msg("system", "You are a helpful assistant. " * 5)]
    for i in range(1, n):
        role = "user" if i % 2 == 1 else "assistant"
        msgs.append(_make_msg(role, f"Message {i}. " + ("x" * msg_size)))
    return msgs


def _fake_summarizer(digest: str) -> str:
    """Deterministic test summarizer — returns a short fixed summary."""
    return f"SUMMARY({len(digest)} chars)"


def _make_cached_msg(role: str, content: str) -> dict:
    """Build a message that has a cache_control annotation."""
    return {
        "role": role,
        "content": [
            {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}},
        ],
    }


# ─────────────────────────────────────────────────────────────
# TestReactiveCompactorInit
# ─────────────────────────────────────────────────────────────


class TestReactiveCompactorInit:
    """Constructor and basic attributes."""

    def test_default_thresholds(self):
        rc = ReactiveCompactor(max_context_tokens=100_000)
        assert rc.TRIGGER_THRESHOLD == 0.90
        assert rc.TARGET_FILL == 0.60
        assert rc.MIN_KEEP_TURNS == 4
        assert rc.max_tokens == 100_000

    def test_custom_summarizer_stored(self):
        rc = ReactiveCompactor(max_context_tokens=50_000, summarizer=_fake_summarizer)
        assert rc._summarizer is _fake_summarizer

    def test_zero_max_tokens_raises(self):
        with pytest.raises(ValueError, match="positive"):
            ReactiveCompactor(max_context_tokens=0)

    def test_negative_max_tokens_raises(self):
        with pytest.raises(ValueError, match="positive"):
            ReactiveCompactor(max_context_tokens=-1)


# ─────────────────────────────────────────────────────────────
# TestShouldCompact
# ─────────────────────────────────────────────────────────────


class TestShouldCompact:
    """Threshold-based trigger detection."""

    def test_below_threshold(self):
        rc = ReactiveCompactor(max_context_tokens=100_000)
        assert rc.should_compact(80_000) is False

    def test_at_threshold(self):
        rc = ReactiveCompactor(max_context_tokens=100_000)
        assert rc.should_compact(90_000) is True

    def test_above_threshold(self):
        rc = ReactiveCompactor(max_context_tokens=100_000)
        assert rc.should_compact(95_000) is True

    def test_just_below_threshold(self):
        rc = ReactiveCompactor(max_context_tokens=100_000)
        assert rc.should_compact(89_999) is False

    def test_exact_boundary(self):
        """90000 / 100000 == 0.90 exactly — should trigger."""
        rc = ReactiveCompactor(max_context_tokens=100_000)
        assert rc.should_compact(90_000) is True

    def test_zero_current(self):
        rc = ReactiveCompactor(max_context_tokens=100_000)
        assert rc.should_compact(0) is False


# ─────────────────────────────────────────────────────────────
# TestComputeTarget
# ─────────────────────────────────────────────────────────────


class TestComputeTarget:
    """Target fill computation."""

    def test_default_target(self):
        rc = ReactiveCompactor(max_context_tokens=200_000)
        assert rc.compute_target() == 120_000  # 200k * 0.60

    def test_small_window(self):
        rc = ReactiveCompactor(max_context_tokens=1_000)
        assert rc.compute_target() == 600

    def test_large_window(self):
        rc = ReactiveCompactor(max_context_tokens=1_000_000)
        assert rc.compute_target() == 600_000


# ─────────────────────────────────────────────────────────────
# TestCompactBasic
# ─────────────────────────────────────────────────────────────


class TestCompactBasic:
    """Core compaction behavior with injected summarizer."""

    def test_compact_reduces_messages(self):
        rc = ReactiveCompactor(max_context_tokens=100_000, summarizer=_fake_summarizer)
        msgs = _make_conversation(12)
        result, saved = asyncio.run(rc.compact(msgs))
        # Should have: system + summary + last 4 = at most 6 messages
        assert len(result) < len(msgs)
        assert saved > 0

    def test_compact_preserves_system_prompt(self):
        rc = ReactiveCompactor(max_context_tokens=100_000, summarizer=_fake_summarizer)
        msgs = _make_conversation(12)
        result, _ = asyncio.run(rc.compact(msgs))
        assert result[0]["role"] == "system"
        assert result[0]["content"] == msgs[0]["content"]

    def test_compact_preserves_last_n_turns(self):
        rc = ReactiveCompactor(max_context_tokens=100_000, summarizer=_fake_summarizer)
        msgs = _make_conversation(12)
        result, _ = asyncio.run(rc.compact(msgs))
        # Last MIN_KEEP_TURNS messages should be identical to originals
        for i in range(1, rc.MIN_KEEP_TURNS + 1):
            assert result[-i] == msgs[-i]

    def test_compact_inserts_summary_message(self):
        rc = ReactiveCompactor(max_context_tokens=100_000, summarizer=_fake_summarizer)
        msgs = _make_conversation(12)
        result, _ = asyncio.run(rc.compact(msgs))
        # Find the summary message
        summaries = [m for m in result if "[Conversation Summary" in m.get("content", "")]
        assert len(summaries) == 1
        assert "SUMMARY(" in summaries[0]["content"]

    def test_compact_returns_saved_tokens(self):
        rc = ReactiveCompactor(max_context_tokens=100_000, summarizer=_fake_summarizer)
        msgs = _make_conversation(12, msg_size=500)
        _, saved = asyncio.run(rc.compact(msgs))
        assert saved > 0

    def test_compact_too_short(self):
        """Conversations with ≤ MIN_KEEP_TURNS + 1 messages are never compacted."""
        rc = ReactiveCompactor(max_context_tokens=100_000, summarizer=_fake_summarizer)
        msgs = _make_conversation(5)  # system + 4 others
        result, saved = asyncio.run(rc.compact(msgs))
        assert result is msgs  # identity — nothing changed
        assert saved == 0

    def test_compact_exactly_min_boundary(self):
        """Exactly MIN_KEEP_TURNS + 1 = 5 messages → nothing beyond system to compact."""
        rc = ReactiveCompactor(max_context_tokens=100_000, summarizer=_fake_summarizer)
        # system + 4 = 5 — safe_end = 5-4=1, safe_start = 1 → end <= start
        msgs = _make_conversation(5)
        result, saved = asyncio.run(rc.compact(msgs))
        assert saved == 0


# ─────────────────────────────────────────────────────────────
# TestCompactCacheAware
# ─────────────────────────────────────────────────────────────


class TestCompactCacheAware:
    """Cache breakpoint preservation."""

    def test_cache_breakpoint_at_index_3_preserved(self):
        """Message at index 3 has cache_control — compaction starts after it."""
        rc = ReactiveCompactor(max_context_tokens=100_000, summarizer=_fake_summarizer)
        msgs = _make_conversation(14)
        # Add cache breakpoint at index 3
        msgs[3] = _make_cached_msg("user", "Cached prompt context " * 20)
        result, saved = asyncio.run(rc.compact(msgs))
        # Original messages 0..3 should be preserved
        for i in range(4):
            assert result[i] == msgs[i]

    def test_multiple_cache_breakpoints(self):
        """If multiple breakpoints exist, use the latest one before the tail."""
        rc = ReactiveCompactor(max_context_tokens=100_000, summarizer=_fake_summarizer)
        msgs = _make_conversation(16)
        msgs[2] = _make_cached_msg("user", "Cache BP 1 " * 20)
        msgs[5] = _make_cached_msg("user", "Cache BP 2 " * 20)
        result, _ = asyncio.run(rc.compact(msgs))
        # Messages 0..5 should all be preserved (safe_start = 6)
        for i in range(6):
            assert result[i] == msgs[i]

    def test_cache_breakpoint_in_frozen_tail_ignored(self):
        """A cache breakpoint in the frozen tail doesn't affect safe_start."""
        rc = ReactiveCompactor(max_context_tokens=100_000, summarizer=_fake_summarizer)
        msgs = _make_conversation(12)
        # Put breakpoint in last 4 (frozen tail) — should be ignored for safe_start
        msgs[-2] = _make_cached_msg("assistant", "Cached tail " * 20)
        result, saved = asyncio.run(rc.compact(msgs))
        # Should still compact (safe_start=1 since no BP before tail boundary)
        assert saved > 0

    def test_no_cache_breakpoints(self):
        """Without cache breakpoints, safe_start is 1 (after system)."""
        rc = ReactiveCompactor(max_context_tokens=100_000, summarizer=_fake_summarizer)
        msgs = _make_conversation(12)
        result, _ = asyncio.run(rc.compact(msgs))
        # system prompt preserved at index 0
        assert result[0] == msgs[0]
        # summary message at index 1
        assert "[Conversation Summary" in result[1]["content"]

    def test_all_messages_have_cache_breakpoint(self):
        """When every message is cached, safe_start moves to tail boundary — nothing to compact."""
        rc = ReactiveCompactor(max_context_tokens=100_000, summarizer=_fake_summarizer)
        msgs = [_make_cached_msg("system", "sys " * 20)]
        for i in range(1, 10):
            role = "user" if i % 2 == 1 else "assistant"
            msgs.append(_make_cached_msg(role, f"Msg {i} " * 20))
        result, saved = asyncio.run(rc.compact(msgs))
        # safe_start should be at or past safe_end — no compaction
        assert saved == 0


# ─────────────────────────────────────────────────────────────
# TestBuildDigest
# ─────────────────────────────────────────────────────────────


class TestBuildDigest:
    """Digest formatting for summarisation."""

    def test_basic_digest(self):
        msgs = [
            _make_msg("user", "Hello world"),
            _make_msg("assistant", "Hi there"),
        ]
        digest = ReactiveCompactor._build_digest(msgs)
        assert "[user]: Hello world" in digest
        assert "[assistant]: Hi there" in digest

    def test_tool_message_digest(self):
        msgs = [_make_msg("tool", "file contents here", tool_call_id="tc_123")]
        digest = ReactiveCompactor._build_digest(msgs)
        assert "[tool:tc_123]" in digest

    def test_assistant_with_tool_calls(self):
        msgs = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "read_file", "arguments": "{}"}},
                    {"function": {"name": "grep_search", "arguments": "{}"}},
                ],
            }
        ]
        digest = ReactiveCompactor._build_digest(msgs)
        assert "read_file" in digest
        assert "grep_search" in digest

    def test_long_content_truncated(self):
        msgs = [_make_msg("user", "x" * 2000)]
        digest = ReactiveCompactor._build_digest(msgs)
        assert "..." in digest
        assert len(digest) < 2000

    def test_structured_content_flattened(self):
        """Content provided as a list of blocks is flattened to text."""
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Part A"},
                    {"type": "text", "text": "Part B"},
                ],
            }
        ]
        digest = ReactiveCompactor._build_digest(msgs)
        assert "Part A" in digest
        assert "Part B" in digest


# ─────────────────────────────────────────────────────────────
# TestStats
# ─────────────────────────────────────────────────────────────


class TestStats:
    """Cumulative statistics tracking."""

    def test_initial_stats_zero(self):
        rc = ReactiveCompactor(max_context_tokens=100_000)
        s = rc.stats
        assert s["compact_count"] == 0
        assert s["tokens_saved"] == 0
        assert s["estimated_cost_saved"] == "$0.0000"

    def test_stats_after_one_compaction(self):
        rc = ReactiveCompactor(max_context_tokens=100_000, summarizer=_fake_summarizer)
        msgs = _make_conversation(12, msg_size=500)
        asyncio.run(rc.compact(msgs))
        s = rc.stats
        assert s["compact_count"] == 1
        assert s["tokens_saved"] > 0

    def test_stats_accumulate(self):
        rc = ReactiveCompactor(max_context_tokens=100_000, summarizer=_fake_summarizer)
        for _ in range(3):
            msgs = _make_conversation(12, msg_size=500)
            asyncio.run(rc.compact(msgs))
        s = rc.stats
        assert s["compact_count"] == 3
        assert s["tokens_saved"] > 0

    def test_stats_no_change_when_skipped(self):
        rc = ReactiveCompactor(max_context_tokens=100_000, summarizer=_fake_summarizer)
        # Too short — won't compact
        msgs = _make_conversation(3)
        asyncio.run(rc.compact(msgs))
        assert rc.stats["compact_count"] == 0

    def test_cost_estimate_format(self):
        rc = ReactiveCompactor(max_context_tokens=100_000, summarizer=_fake_summarizer)
        msgs = _make_conversation(12, msg_size=500)
        asyncio.run(rc.compact(msgs))
        cost = rc.stats["estimated_cost_saved"]
        assert cost.startswith("$")
        # Ensure it parses as a float
        float(cost[1:])


# ─────────────────────────────────────────────────────────────
# TestSummarizerFallback
# ─────────────────────────────────────────────────────────────


class TestSummarizerFallback:
    """Summarizer injection and fallback behavior."""

    def test_custom_summarizer_called(self):
        calls = []

        def tracking_summarizer(digest: str) -> str:
            calls.append(digest)
            return "TRACKED"

        rc = ReactiveCompactor(max_context_tokens=100_000, summarizer=tracking_summarizer)
        msgs = _make_conversation(12)
        asyncio.run(rc.compact(msgs))
        assert len(calls) == 1
        assert (
            "TRACKED"
            in [
                m["content"]
                for m in asyncio.run(rc.compact(msgs))[0]
                if "[Conversation Summary" in m.get("content", "")
            ][-1]
        )

    def test_summarizer_exception_returns_original(self):
        def failing_summarizer(digest: str) -> str:
            raise RuntimeError("LLM down")

        rc = ReactiveCompactor(max_context_tokens=100_000, summarizer=failing_summarizer)
        msgs = _make_conversation(12)
        result, saved = asyncio.run(rc.compact(msgs))
        # On failure, returns originals
        assert result is msgs
        assert saved == 0

    def test_async_summarizer_supported(self):
        """An async callback should also work."""

        async def async_summarizer(digest: str) -> str:
            return "ASYNC_SUMMARY"

        rc = ReactiveCompactor(max_context_tokens=100_000, summarizer=async_summarizer)
        msgs = _make_conversation(12)
        result, saved = asyncio.run(rc.compact(msgs))
        summaries = [m for m in result if "ASYNC_SUMMARY" in m.get("content", "")]
        assert len(summaries) == 1
        assert saved > 0

    def test_default_summarizer_with_mocked_llm(self):
        """Default summarizer calls run_llm — mock it."""

        class FakeResult:
            content = "Mocked LLM summary"

        with patch("navig.llm_generate.run_llm", return_value=FakeResult()) as mock_llm:
            from navig.agent.context_compressor import _default_summarizer

            result = _default_summarizer("some digest text")
            assert result == "Mocked LLM summary"
            mock_llm.assert_called_once()

    def test_default_summarizer_fallback_on_error(self):
        """Default summarizer falls back to truncation when LLM fails."""
        with patch(
            "navig.llm_generate.run_llm",
            side_effect=RuntimeError("no api key"),
        ):
            from navig.agent.context_compressor import _default_summarizer

            result = _default_summarizer("digest " * 500)
            assert "[...abbreviated...]" in result


# ─────────────────────────────────────────────────────────────
# TestMultipleCompactions
# ─────────────────────────────────────────────────────────────


class TestMultipleCompactions:
    """Repeated compaction on expanding conversations."""

    def test_double_compact(self):
        rc = ReactiveCompactor(max_context_tokens=100_000, summarizer=_fake_summarizer)
        msgs = _make_conversation(20, msg_size=500)
        result1, saved1 = asyncio.run(rc.compact(msgs))
        assert saved1 > 0

        # Simulate more messages arriving after compaction
        for i in range(8):
            role = "user" if i % 2 == 0 else "assistant"
            result1.append(_make_msg(role, f"New msg {i}. " + ("y" * 500)))

        result2, saved2 = asyncio.run(rc.compact(result1))
        assert rc.stats["compact_count"] == 2
        # Both rounds saved tokens
        assert rc.stats["tokens_saved"] == saved1 + saved2

    def test_compact_preserves_prior_summary(self):
        """A previous summary message (role=system) in the middle survives compaction."""
        rc = ReactiveCompactor(max_context_tokens=100_000, summarizer=_fake_summarizer)
        msgs = _make_conversation(20, msg_size=300)
        result1, _ = asyncio.run(rc.compact(msgs))

        # Add more messages
        for i in range(10):
            role = "user" if i % 2 == 0 else "assistant"
            result1.append(_make_msg(role, f"Extra {i}. " + ("z" * 300)))

        result2, _ = asyncio.run(rc.compact(result1))
        # Should still have a summary in the output
        summaries = [m for m in result2 if "[Conversation Summary" in m.get("content", "")]
        assert len(summaries) >= 1


# ─────────────────────────────────────────────────────────────
# TestEdgeCases
# ─────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_messages(self):
        rc = ReactiveCompactor(max_context_tokens=100_000, summarizer=_fake_summarizer)
        result, saved = asyncio.run(rc.compact([]))
        assert result == []
        assert saved == 0

    def test_single_message(self):
        rc = ReactiveCompactor(max_context_tokens=100_000, summarizer=_fake_summarizer)
        msgs = [_make_msg("system", "Hello")]
        result, saved = asyncio.run(rc.compact(msgs))
        assert result is msgs
        assert saved == 0

    def test_messages_with_empty_content(self):
        rc = ReactiveCompactor(max_context_tokens=100_000, summarizer=_fake_summarizer)
        msgs = _make_conversation(12)
        msgs[3]["content"] = ""
        msgs[5]["content"] = ""
        result, saved = asyncio.run(rc.compact(msgs))
        # Should still work
        assert len(result) < len(msgs)

    def test_tool_messages_in_compactable_range(self):
        rc = ReactiveCompactor(max_context_tokens=100_000, summarizer=_fake_summarizer)
        msgs = _make_conversation(12)
        # Replace some middle messages with tool messages
        msgs[3] = _make_msg("tool", "File content " * 50, tool_call_id="tc_001")
        msgs[4] = _make_msg("tool", "Grep result " * 50, tool_call_id="tc_002")
        result, saved = asyncio.run(rc.compact(msgs))
        assert saved > 0


# ─────────────────────────────────────────────────────────────
# TestFindSafeStart
# ─────────────────────────────────────────────────────────────


class TestFindSafeStart:
    """Internal _find_safe_start method."""

    def test_no_breakpoints(self):
        rc = ReactiveCompactor(max_context_tokens=100_000)
        msgs = _make_conversation(12)
        assert rc._find_safe_start(msgs) == 1

    def test_breakpoint_early(self):
        rc = ReactiveCompactor(max_context_tokens=100_000)
        msgs = _make_conversation(12)
        msgs[2] = _make_cached_msg("user", "bp " * 20)
        assert rc._find_safe_start(msgs) == 3  # after breakpoint

    def test_breakpoint_at_tail_boundary(self):
        """Breakpoint exactly at tail_boundary index is NOT included (idx >= boundary)."""
        rc = ReactiveCompactor(max_context_tokens=100_000)
        msgs = _make_conversation(10)
        # tail_boundary = 10 - 4 = 6
        msgs[6] = _make_cached_msg("user", "bp " * 20)
        # idx=6 is at boundary, loop breaks before processing it
        assert rc._find_safe_start(msgs) == 1

    def test_breakpoint_just_before_tail(self):
        rc = ReactiveCompactor(max_context_tokens=100_000)
        msgs = _make_conversation(10)
        # tail_boundary = 10 - 4 = 6, so idx=5 is last checked
        msgs[5] = _make_cached_msg("user", "bp " * 20)
        assert rc._find_safe_start(msgs) == 6


# ─────────────────────────────────────────────────────────────
# TestFactory
# ─────────────────────────────────────────────────────────────


class TestFactory:
    """get_reactive_compactor factory function."""

    def test_default_factory(self):
        rc = get_reactive_compactor()
        assert isinstance(rc, ReactiveCompactor)
        assert rc.max_tokens == 200_000

    def test_factory_with_custom_tokens(self):
        rc = get_reactive_compactor(max_context_tokens=500_000)
        assert rc.max_tokens == 500_000

    def test_factory_with_summarizer(self):
        rc = get_reactive_compactor(summarizer=_fake_summarizer)
        assert rc._summarizer is _fake_summarizer


# ─────────────────────────────────────────────────────────────
# TestTokenEstimationReuse
# ─────────────────────────────────────────────────────────────


class TestTokenEstimationReuse:
    """Ensure ReactiveCompactor reuses module-level token estimators."""

    def test_estimate_tokens_available(self):
        assert _estimate_tokens("hello world") > 0

    def test_estimate_messages_tokens_available(self):
        msgs = [_make_msg("user", "test")]
        assert _estimate_messages_tokens(msgs) > 0

    def test_compact_uses_module_estimator(self):
        """Saved tokens should be consistent with module-level estimator."""
        rc = ReactiveCompactor(max_context_tokens=100_000, summarizer=_fake_summarizer)
        msgs = _make_conversation(12, msg_size=500)
        original_est = _estimate_messages_tokens(msgs[1:-4])  # compactable range
        _, saved = asyncio.run(rc.compact(msgs))
        # saved should be close to original_est minus the summary cost
        # Just verify it's a positive fraction of the original
        assert 0 < saved <= original_est
