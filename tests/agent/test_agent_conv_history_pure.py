"""
Unit tests for pure-logic helpers in navig/agent/conv/history.py.

Covers:
- Module-level constants
- _estimate_tokens() heuristic
- _strip_cjk() regex helper
- ConversationHistory.__init__ validation / user_id sanitization
- _VALID_ROLES membership
"""

from __future__ import annotations

import pytest

from navig.agent.conv.history import (
    _DEFAULT_MAX_TOKENS,
    _SESSION_LOAD_DEPTH,
    _TRUNCATE_ANCHOR,
    _TRUNCATE_MIN_MSGS,
    _TRUNCATE_RECENCY,
    _VALID_ROLES,
    ConversationHistory,
    _estimate_tokens,
    _strip_cjk,
)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_default_max_tokens_is_int(self):
        assert isinstance(_DEFAULT_MAX_TOKENS, int)

    def test_default_max_tokens_positive(self):
        assert _DEFAULT_MAX_TOKENS > 0

    def test_session_load_depth_is_int(self):
        assert isinstance(_SESSION_LOAD_DEPTH, int)

    def test_session_load_depth_positive(self):
        assert _SESSION_LOAD_DEPTH > 0

    def test_truncate_anchor_is_int(self):
        assert isinstance(_TRUNCATE_ANCHOR, int)

    def test_truncate_recency_is_int(self):
        assert isinstance(_TRUNCATE_RECENCY, int)

    def test_truncate_min_msgs_equals_sum(self):
        assert _TRUNCATE_MIN_MSGS == _TRUNCATE_ANCHOR + _TRUNCATE_RECENCY + 1

    def test_valid_roles_is_frozenset(self):
        assert isinstance(_VALID_ROLES, frozenset)

    def test_valid_roles_contains_user(self):
        assert "user" in _VALID_ROLES

    def test_valid_roles_contains_assistant(self):
        assert "assistant" in _VALID_ROLES

    def test_valid_roles_contains_system(self):
        assert "system" in _VALID_ROLES

    def test_valid_roles_contains_tool(self):
        assert "tool" in _VALID_ROLES

    def test_valid_roles_excludes_invalid(self):
        assert "human" not in _VALID_ROLES
        assert "" not in _VALID_ROLES


# ---------------------------------------------------------------------------
# _estimate_tokens
# ---------------------------------------------------------------------------


class TestEstimateTokens:
    def test_empty_string_returns_zero(self):
        assert _estimate_tokens("") == 0

    def test_single_word_returns_one_ish(self):
        # int(1 * 1.3) = 1
        result = _estimate_tokens("hello")
        assert result == int(1 * 1.3)

    def test_five_words(self):
        assert _estimate_tokens("one two three four five") == int(5 * 1.3)

    def test_result_is_int(self):
        assert isinstance(_estimate_tokens("a b c"), int)

    def test_proportional_to_word_count(self):
        short = _estimate_tokens("short")
        long = _estimate_tokens("word " * 100)
        assert long > short

    def test_whitespace_only(self):
        # split() on whitespace returns [] so 0 tokens
        assert _estimate_tokens("   ") == 0

    def test_newlines_count_as_separators(self):
        result = _estimate_tokens("line one\nline two\nline three")
        assert result == int(6 * 1.3)


# ---------------------------------------------------------------------------
# _strip_cjk
# ---------------------------------------------------------------------------


class TestStripCjk:
    def test_pure_ascii_unchanged(self):
        s = "hello world"
        assert _strip_cjk(s) == s

    def test_strips_cjk_block(self):
        result = _strip_cjk("hello 你好 world")
        assert "你好" not in result
        assert "hello" in result
        assert "world" in result

    def test_empty_string_returns_empty(self):
        assert _strip_cjk("") == ""

    def test_only_cjk_returns_empty(self):
        assert _strip_cjk("你好世界") == ""

    def test_cjk_range_3400(self):
        # U+3400–U+4DBF: CJK Unified Ideographs Extension A
        assert _strip_cjk("\u3400") == ""

    def test_cjk_range_3000(self):
        # U+3000–U+303F: CJK Symbols and Punctuation
        assert _strip_cjk("\u3000") == ""

    def test_non_cjk_special_chars_preserved(self):
        s = "café résumé naïve"
        assert _strip_cjk(s) == s

    def test_returns_stripped_string(self):
        # Leading/trailing whitespace left by strip inside the function
        result = _strip_cjk("  hello  ")
        # The re.sub(...).strip() inside means outer whitespace is removed
        assert result == "hello"


# ---------------------------------------------------------------------------
# ConversationHistory.__init__ validation
# ---------------------------------------------------------------------------


class TestConversationHistoryInit:
    def _make(self, user_id="testuser", max_tokens=512):
        return ConversationHistory(user_id=user_id, max_tokens=max_tokens)

    def test_raises_on_zero_max_tokens(self):
        with pytest.raises(ValueError, match="max_tokens"):
            ConversationHistory(user_id="u", max_tokens=0)

    def test_raises_on_negative_max_tokens(self):
        with pytest.raises(ValueError, match="max_tokens"):
            ConversationHistory(user_id="u", max_tokens=-10)

    def test_valid_construction(self):
        h = self._make()
        assert h is not None

    def test_messages_start_empty(self):
        h = self._make()
        assert h._messages == []

    def test_user_id_sanitized_path_traversal(self):
        h = ConversationHistory(user_id="../../evil", max_tokens=100)
        # should not contain path separators
        assert "/" not in h._user_id
        assert "\\" not in h._user_id

    def test_user_id_sanitized_spaces(self):
        h = ConversationHistory(user_id="my user", max_tokens=100)
        assert " " not in h._user_id

    def test_user_id_alphanumeric_preserved(self):
        h = ConversationHistory(user_id="alice123", max_tokens=100)
        assert h._user_id == "alice123"

    def test_user_id_hyphens_preserved(self):
        h = ConversationHistory(user_id="alice-bob", max_tokens=100)
        assert h._user_id == "alice-bob"

    def test_default_max_tokens_used_when_omitted(self):
        h = ConversationHistory(user_id="u")
        assert h._max_tokens == _DEFAULT_MAX_TOKENS

    def test_custom_max_tokens_stored(self):
        h = ConversationHistory(user_id="u", max_tokens=1024)
        assert h._max_tokens == 1024

    def test_no_summarizer_by_default(self):
        h = ConversationHistory(user_id="u")
        assert h._summarizer is None

    def test_summarizer_accepted(self):
        def fake_sum(msgs):
            return "summary"

        h = ConversationHistory(user_id="u", summarizer=fake_sum)
        assert h._summarizer is fake_sum
