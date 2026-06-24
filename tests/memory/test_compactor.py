"""
Tests for navig/memory/compactor.py

Strategy: use tmp_path for ChatMemoryStore; inject old timestamps directly
to trigger compaction without sleeping.
"""

import time
from pathlib import Path

import pytest

from navig.memory.chat_store import ChatMemoryStore, ConversationTurn
from navig.memory.compactor import (
    CompactionResult,
    KeywordCompactor,
    KeywordSummariser,
)


# ---------------------------------------------------------------------------
# CompactionResult defaults
# ---------------------------------------------------------------------------


class TestCompactionResult:
    def test_default_turns_removed_is_zero(self):
        r = CompactionResult()
        assert r.turns_removed == 0

    def test_default_notes_written_is_false(self):
        r = CompactionResult()
        assert r.notes_written is False

    def test_can_set_fields(self):
        r = CompactionResult(turns_removed=5, notes_written=True)
        assert r.turns_removed == 5
        assert r.notes_written is True


# ---------------------------------------------------------------------------
# KeywordSummariser
# ---------------------------------------------------------------------------


class TestKeywordSummariser:
    @pytest.fixture
    def summariser(self):
        return KeywordSummariser()

    def test_empty_turns_returns_no_content(self, summariser):
        result = summariser.summarise([])
        assert "no content" in result.lower()

    def test_only_assistant_turns_too_short(self, summariser):
        turns = [ConversationTurn(role="assistant", content="OK", timestamp=time.time())]
        result = summariser.summarise(turns)
        assert "short" in result.lower() or "no content" in result.lower()

    def test_user_turns_produce_keywords(self, summariser):
        turns = [
            ConversationTurn(
                role="user",
                content="I want to deploy kubernetes cluster configuration",
                timestamp=time.time(),
            )
        ]
        result = summariser.summarise(turns)
        assert "**Summary**" in result
        # At least one keyword from the content
        assert any(kw in result for kw in ("deploy", "kubernetes", "cluster", "configuration"))

    def test_summary_includes_date_range(self, summariser):
        ts = time.time()
        turns = [
            ConversationTurn(role="user", content="ansible playbook automation", timestamp=ts),
            ConversationTurn(role="user", content="docker swarm orchestration", timestamp=ts + 60),
        ]
        result = summariser.summarise(turns)
        assert "**Summary**" in result
        assert "turns" in result

    def test_stop_words_excluded(self, summariser):
        # "this", "that", "with" are stop words and should not appear as keywords
        turns = [
            ConversationTurn(
                role="user",
                content="this that with from have will what when",
                timestamp=time.time(),
            )
        ]
        result = summariser.summarise(turns)
        # No meaningful keywords → too short message
        assert "short" in result.lower() or "no content" in result.lower()

    def test_short_words_excluded(self, summariser):
        # Words < 4 chars should not appear as keywords
        turns = [
            ConversationTurn(role="user", content="do it now ok yes", timestamp=time.time())
        ]
        result = summariser.summarise(turns)
        assert "short" in result.lower() or "no content" in result.lower()

    def test_max_words_respected(self, summariser):
        # Generate many unique keywords; output should not have > MAX_WORDS bullets
        words = [f"keyword{i:03d}" for i in range(50)]
        content = " ".join(words)
        turns = [ConversationTurn(role="user", content=content, timestamp=time.time())]
        result = summariser.summarise(turns)
        bullet_count = result.count("- ")
        assert bullet_count <= KeywordSummariser._MAX_WORDS

    def test_mixed_roles_only_user_counted(self, summariser):
        ts = time.time()
        turns = [
            ConversationTurn(role="user", content="terraform kubernetes deployment", timestamp=ts),
            ConversationTurn(role="assistant", content="sure infrastructure monitoring", timestamp=ts + 1),
        ]
        result = summariser.summarise(turns)
        # User keywords should appear; assistant keywords may not
        assert "terraform" in result or "kubernetes" in result or "deployment" in result


# ---------------------------------------------------------------------------
# KeywordCompactor init validation
# ---------------------------------------------------------------------------


class TestKeywordCompactorInit:
    def test_valid_days_accepted(self):
        kc = KeywordCompactor(compact_after_days=7)
        assert kc._days == 7

    def test_zero_days_raises_value_error(self):
        with pytest.raises(ValueError, match="compact_after_days"):
            KeywordCompactor(compact_after_days=0)

    def test_negative_days_raises_value_error(self):
        with pytest.raises(ValueError, match="compact_after_days"):
            KeywordCompactor(compact_after_days=-1)

    def test_default_summariser_is_keyword(self):
        kc = KeywordCompactor()
        assert isinstance(kc._summariser, KeywordSummariser)

    def test_custom_summariser_injected(self):
        from unittest.mock import MagicMock

        mock_s = MagicMock()
        kc = KeywordCompactor(summariser=mock_s)
        assert kc._summariser is mock_s


# ---------------------------------------------------------------------------
# KeywordCompactor.compact
# ---------------------------------------------------------------------------


class _FakeStore:
    """Minimal stub for ChatMemoryStore used by compactor."""

    def __init__(self, turns, notes_path, prune_return=0):
        self._turns = turns
        self.notes_path = notes_path
        self._prune_return = prune_return

    def all(self):
        return list(self._turns)

    def prune_before(self, cutoff):
        return self._prune_return


class TestKeywordCompactorCompact:
    def test_no_old_turns_returns_empty_result(self, tmp_path):
        notes = tmp_path / "notes.md"
        recent_ts = time.time()  # now → not old
        turns = [ConversationTurn(role="user", content="hello world", timestamp=recent_ts)]
        store = _FakeStore(turns, notes)
        kc = KeywordCompactor(compact_after_days=7)
        result = kc.compact(store)
        assert result.turns_removed == 0
        assert result.notes_written is False

    def test_old_turns_writes_notes(self, tmp_path):
        notes = tmp_path / "notes.md"
        old_ts = time.time() - 10 * 86_400  # 10 days ago → eligible
        turns = [
            ConversationTurn(role="user", content="deployment automation ansible", timestamp=old_ts)
        ]
        store = _FakeStore(turns, notes, prune_return=1)
        kc = KeywordCompactor(compact_after_days=7)
        result = kc.compact(store)
        assert result.notes_written is True
        assert result.turns_removed == 1
        assert notes.exists()

    def test_notes_file_created_with_summary_content(self, tmp_path):
        notes = tmp_path / "notes.md"
        old_ts = time.time() - 8 * 86_400
        turns = [
            ConversationTurn(role="user", content="kubernetes docker terraform", timestamp=old_ts)
        ]
        store = _FakeStore(turns, notes, prune_return=1)
        kc = KeywordCompactor(compact_after_days=7)
        kc.compact(store)
        content = notes.read_text(encoding="utf-8")
        assert len(content) > 0

    def test_notes_file_appended_when_exists(self, tmp_path):
        notes = tmp_path / "notes.md"
        notes.write_text("existing content", encoding="utf-8")
        old_ts = time.time() - 8 * 86_400
        turns = [
            ConversationTurn(role="user", content="network firewall security audit", timestamp=old_ts)
        ]
        store = _FakeStore(turns, notes, prune_return=1)
        kc = KeywordCompactor(compact_after_days=7)
        kc.compact(store)
        content = notes.read_text(encoding="utf-8")
        assert "existing content" in content
        assert "---" in content  # separator added

    def test_compact_after_days_boundary(self, tmp_path):
        notes = tmp_path / "notes.md"
        # Exactly on the boundary (0 seconds old) → not eligible
        just_recent = time.time()
        turns = [ConversationTurn(role="user", content="testing boundary", timestamp=just_recent)]
        store = _FakeStore(turns, notes)
        kc = KeywordCompactor(compact_after_days=1)
        result = kc.compact(store)
        assert result.turns_removed == 0
        assert not notes.exists()

    def test_custom_summariser_called(self, tmp_path):
        from unittest.mock import MagicMock

        notes = tmp_path / "notes.md"
        old_ts = time.time() - 10 * 86_400
        turns = [ConversationTurn(role="user", content="test", timestamp=old_ts)]
        store = _FakeStore(turns, notes, prune_return=1)

        mock_summariser = MagicMock()
        mock_summariser.summarise.return_value = "mocked summary"
        kc = KeywordCompactor(compact_after_days=7, summariser=mock_summariser)
        kc.compact(store)
        mock_summariser.summarise.assert_called_once_with(turns)
        assert "mocked summary" in notes.read_text(encoding="utf-8")
