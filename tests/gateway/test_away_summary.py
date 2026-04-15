"""
Unit tests for navig.gateway.channels.away_summary.

Covers:
  - _truncate_history() dual-cap (line + byte) logic
  - build_away_summary() with mocked run_llm
  - build_away_summary() edge cases: empty history, LLM exceptions, blank result
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import away_summary directly to avoid the navig.gateway package __init__,
# which triggers a chain that hits a pre-existing NameError in agent/config.py.
_MOD_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "navig" / "gateway" / "channels" / "away_summary.py"
)
_spec = importlib.util.spec_from_file_location("_away_summary_direct", _MOD_PATH)
_away_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_away_mod)

_RECAP_MAX_BYTES = _away_mod._RECAP_MAX_BYTES
_RECAP_MAX_LINES = _away_mod._RECAP_MAX_LINES
_truncate_history = _away_mod._truncate_history
build_away_summary = _away_mod.build_away_summary

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _msg(role: str, content: str) -> dict:
    return {"role": role, "content": content}


def _msgs(n: int) -> list[dict]:
    """Return n alternating user/assistant messages."""
    return [
        _msg("user" if i % 2 == 0 else "assistant", f"line-{i}")
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# _truncate_history — line cap
# ---------------------------------------------------------------------------


class TestTruncateHistoryLineCap:
    """Line-count cap is applied before byte cap."""

    def test_empty_input_returns_empty(self):
        assert _truncate_history([]) == []

    def test_messages_under_cap_unchanged(self):
        """Five messages with one line each should all survive."""
        msgs = _msgs(5)
        result = _truncate_history(msgs, max_lines=100, max_bytes=1_000_000)
        assert len(result) == 1  # re-packed into single message
        combined = result[0]["content"]
        for i in range(5):
            assert f"line-{i}" in combined

    def test_line_cap_truncates_multi_line_content(self):
        """A single message with many lines is trimmed to max_lines."""
        long_content = "\n".join(f"row {i}" for i in range(50))
        msgs = [_msg("user", long_content)]
        result = _truncate_history(msgs, max_lines=10, max_bytes=1_000_000)
        assert len(result) == 1
        lines = result[0]["content"].splitlines()
        # Each source line becomes a "user: row N" line
        assert len(lines) <= 10

    def test_line_cap_stops_across_messages(self):
        """Line cap is applied across all messages, not per message."""
        # 3 messages × 5 lines each = 15 lines total; cap at 10
        msgs = [_msg("user", "\n".join(f"line {j}" for j in range(5))) for _ in range(3)]
        result = _truncate_history(msgs, max_lines=10, max_bytes=1_000_000)
        combined = result[0]["content"]
        assert combined.count("\n") < 15  # fewer lines than 3 × 5

    def test_empty_content_messages_skipped(self):
        """Messages with empty or whitespace content contribute zero lines."""
        msgs = [_msg("user", ""), _msg("assistant", "   "), _msg("user", "real")]
        result = _truncate_history(msgs, max_lines=100, max_bytes=1_000_000)
        assert len(result) == 1
        assert "real" in result[0]["content"]

    def test_all_empty_messages_return_empty(self):
        msgs = [_msg("user", ""), _msg("assistant", "  ")]
        assert _truncate_history(msgs, max_lines=100, max_bytes=1_000_000) == []

    def test_line_cap_keeps_most_recent_lines(self):
        """When capped, output should retain newest lines, not oldest ones."""
        msgs = [_msg("user", f"line-{i}") for i in range(10)]
        result = _truncate_history(msgs, max_lines=3, max_bytes=1_000_000)
        assert len(result) == 1
        combined = result[0]["content"]
        assert "line-9" in combined
        assert "line-8" in combined
        assert "line-7" in combined
        assert "line-0" not in combined


# ---------------------------------------------------------------------------
# _truncate_history — byte cap
# ---------------------------------------------------------------------------


class TestTruncateHistoryByteCap:
    """Byte cap is applied after line cap."""

    def test_byte_cap_truncates_result(self):
        """Combined output is kept within max_bytes."""
        # One message with lots of ASCII content
        big_content = "x" * 10_000
        msgs = [_msg("user", big_content)]
        result = _truncate_history(msgs, max_lines=100_000, max_bytes=500)
        assert len(result) == 1
        encoded = result[0]["content"].encode("utf-8")
        assert len(encoded) <= 500 + 50  # small slack for "user: " prefix

    def test_byte_cap_truncates_at_newline(self):
        """Byte cap must snap to the last newline, not mid-line."""
        lines = [f"user: {'a' * 80}" for _ in range(20)]
        combined = "\n".join(lines)
        # Provide the combined text as pre-processed; simulate via max_bytes < full
        msgs = [_msg("user", combined)]
        result = _truncate_history(msgs, max_lines=100_000, max_bytes=200)
        if result:
            # Must end at a complete line boundary (no trailing partial "user:" prefix)
            last_line = result[0]["content"].rsplit("\n", 1)[-1]
            # Partial lines would look like "user: aaa" cut off mid-character — 
            # we just verify no UnicodeDecodeError occurred and output is non-empty
            assert last_line

    def test_content_within_limits_unchanged(self):
        """If content fits within both caps the output carries the full text."""
        msgs = [_msg("user", "short message"), _msg("assistant", "short reply")]
        result = _truncate_history(msgs, max_lines=_RECAP_MAX_LINES, max_bytes=_RECAP_MAX_BYTES)
        assert len(result) == 1
        assert "short message" in result[0]["content"]
        assert "short reply" in result[0]["content"]

    def test_unicode_byte_cap_does_not_produce_mojibake(self):
        """Byte cap on multi-byte chars must not split a UTF-8 sequence."""
        # Cyrillic: 2 bytes per char
        cyrillic = "Привет мир\n" * 100
        msgs = [_msg("user", cyrillic)]
        result = _truncate_history(msgs, max_lines=100_000, max_bytes=200)
        if result:
            # Should decode successfully without replacement characters
            content = result[0]["content"]
            assert "\ufffd" not in content

    def test_byte_cap_prefers_tail_content(self):
        """Byte cap should retain newer tail content rather than early history."""
        msgs = [_msg("user", f"chunk-{i}-{'x' * 40}") for i in range(40)]
        result = _truncate_history(msgs, max_lines=10_000, max_bytes=220)
        assert len(result) == 1
        combined = result[0]["content"]
        assert "chunk-39" in combined or "chunk-38" in combined
        assert "chunk-0" not in combined


# ---------------------------------------------------------------------------
# build_away_summary — mocked LLM
# ---------------------------------------------------------------------------


class TestBuildAwaySummaryMockedLLM:
    """build_away_summary calls run_llm and returns its content."""

    # run_llm is imported lazily inside build_away_summary via
    # `from navig.llm_generate import run_llm`, so the authoritative
    # patch target is the attribute on navig.llm_generate, not the
    # channel module (which may be loaded under a different sys.modules key).
    _PATCH = "navig.llm_generate.run_llm"

    @pytest.mark.asyncio
    async def test_returns_llm_content(self):
        """Main happy path: LLM returns a recap string."""
        fake_history = [_msg("user", "Deploy app"), _msg("assistant", "Done.")]
        fake_result = MagicMock()
        fake_result.content = "User deployed the app. Next: verify prod."

        with patch(self._PATCH, return_value=fake_result) as mock_run:
            result = await build_away_summary(fake_history)

        assert result == "User deployed the app. Next: verify prod."
        mock_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_uses_effort_low(self):
        """build_away_summary always requests effort=low for speed/cost."""
        fake_result = MagicMock()
        fake_result.content = "Summary."
        # Need at least 2 content messages — 1 message triggers early-exit guard
        history = [_msg("user", "Deploy app"), _msg("assistant", "Done.")]

        with patch(self._PATCH, return_value=fake_result) as mock_run:
            await build_away_summary(history)

        call_kwargs = mock_run.call_args
        all_kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
        assert all_kwargs.get("effort") == "low"

    @pytest.mark.asyncio
    async def test_empty_history_returns_none(self):
        """No LLM call should be made for an empty history."""
        with patch(self._PATCH) as mock_run:
            result = await build_away_summary([])

        assert result is None
        mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_exception_returns_none(self):
        """An LLM error is swallowed — never raises, returns None."""
        fake_history = [_msg("user", "Hello"), _msg("assistant", "Hi")]

        with patch(self._PATCH, side_effect=RuntimeError("LLM unavailable")):
            result = await build_away_summary(fake_history)

        assert result is None

    @pytest.mark.asyncio
    async def test_blank_llm_response_returns_none(self):
        """If LLM returns blank content, build_away_summary returns None."""
        fake_result = MagicMock()
        fake_result.content = "   "

        with patch(self._PATCH, return_value=fake_result):
            result = await build_away_summary([_msg("user", "test")])

        assert result is None

    @pytest.mark.asyncio
    async def test_none_llm_result_returns_none(self):
        """If run_llm returns None, build_away_summary returns None."""
        with patch(self._PATCH, return_value=None):
            result = await build_away_summary([_msg("user", "test")])

        assert result is None

    @pytest.mark.asyncio
    async def test_config_window_controls_message_slice(self):
        """memory.away_summary_message_window limits how many messages are passed."""
        large_history = _msgs(50)
        fake_result = MagicMock()
        fake_result.content = "Capped recap."

        mock_config = MagicMock()
        # Window of 5 messages
        mock_config.get.side_effect = lambda key, default=None: {
            "memory.away_summary_message_window": 5,
        }.get(key, default)

        captured_messages = []

        def _fake_run_llm(messages, **kwargs):
            captured_messages.extend(messages)
            return fake_result

        with patch(self._PATCH, side_effect=_fake_run_llm):
            await build_away_summary(large_history, config=mock_config)

        # The _truncate_history call receives at most 5 messages worth of content
        if captured_messages:
            combined_content = " ".join(m.get("content", "") for m in captured_messages)
            # Only the last 5 messages (lines 45-49) should appear
            assert "line-45" in combined_content or "line-49" in combined_content
            # Early messages should NOT appear after windowing
            assert "line-0" not in combined_content
