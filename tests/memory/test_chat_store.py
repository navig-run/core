"""
tests/memory/test_chat_store.py
────────────────────────────────
Tests for navig.memory.chat_store and navig.memory.compactor (Item 9).
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from navig.memory.chat_store import (
    ChatMemoryStore,
    ConversationTurn,
    _CHARS_PER_TOKEN,
)
from navig.memory.compactor import CompactionResult, KeywordCompactor, KeywordSummariser


# ──────────────────────────────────────────────────────────────────────────────
# ConversationTurn
# ──────────────────────────────────────────────────────────────────────────────


class TestConversationTurn:
    def test_round_trip(self):
        t = ConversationTurn(role="user", content="hello world", timestamp=1000.0)
        d = t.as_dict()
        t2 = ConversationTurn.from_dict(d)
        assert t2.role == t.role
        assert t2.content == t.content
        assert t2.timestamp == t.timestamp

    def test_token_estimate_positive(self):
        t = ConversationTurn(role="user", content="a" * 40)
        assert t.token_estimate == 40 // _CHARS_PER_TOKEN

    def test_token_estimate_minimum_one(self):
        t = ConversationTurn(role="user", content="")
        assert t.token_estimate == 1

    def test_from_dict_defaults(self):
        t = ConversationTurn.from_dict({})
        assert t.role == "unknown"
        assert t.content == ""


# ──────────────────────────────────────────────────────────────────────────────
# ChatMemoryStore — append / read
# ──────────────────────────────────────────────────────────────────────────────


class TestChatMemoryStore:
    def _store(self, tmp_path):
        return ChatMemoryStore("chat42", base_dir=tmp_path)

    def test_empty_all(self, tmp_path):
        assert self._store(tmp_path).all() == []

    def test_append_and_all(self, tmp_path):
        s = self._store(tmp_path)
        t = ConversationTurn("user", "hello")
        s.append(t)
        turns = s.all()
        assert len(turns) == 1
        assert turns[0].content == "hello"

    def test_multiple_appends(self, tmp_path):
        s = self._store(tmp_path)
        for i in range(5):
            s.append(ConversationTurn("user", f"msg{i}"))
        assert len(s.all()) == 5

    def test_recent_returns_last_n(self, tmp_path):
        s = self._store(tmp_path)
        for i in range(10):
            s.append(ConversationTurn("user", f"m{i}", timestamp=float(i)))
        last3 = s.recent(3)
        assert len(last3) == 3
        assert last3[-1].content == "m9"

    def test_recent_zero_returns_empty(self, tmp_path):
        s = self._store(tmp_path)
        s.append(ConversationTurn("user", "hi"))
        assert s.recent(0) == []

    def test_search_finds_match(self, tmp_path):
        s = self._store(tmp_path)
        s.append(ConversationTurn("user", "hello world"))
        s.append(ConversationTurn("user", "goodbye"))
        matches = s.search("hello")
        assert len(matches) == 1
        assert matches[0].content == "hello world"

    def test_search_case_insensitive(self, tmp_path):
        s = self._store(tmp_path)
        s.append(ConversationTurn("user", "Hello World"))
        assert len(s.search("hello")) == 1

    def test_search_no_match_returns_empty(self, tmp_path):
        s = self._store(tmp_path)
        s.append(ConversationTurn("user", "foo"))
        assert s.search("bar") == []

    def test_transcript_path(self, tmp_path):
        s = self._store(tmp_path)
        assert s.transcript_path.name == "transcript.jsonl"

    def test_notes_path(self, tmp_path):
        s = self._store(tmp_path)
        assert s.notes_path.name == "notes.md"

    def test_creates_parent_dir(self, tmp_path):
        s = self._store(tmp_path)
        s.append(ConversationTurn("user", "hi"))
        assert s.transcript_path.parent.exists()


# ──────────────────────────────────────────────────────────────────────────────
# ChatMemoryStore — token budget
# ──────────────────────────────────────────────────────────────────────────────


class TestTokenBudget:
    def test_returns_within_budget(self, tmp_path):
        s = ChatMemoryStore("c", base_dir=tmp_path, max_context_tokens=5)
        # Each turn: "ab" = 2 chars → 1 token each (min 1)
        for i in range(10):
            s.append(ConversationTurn("user", "abcd" * 10, timestamp=float(i)))
        # Budget is small — only a subset should come back
        selected = s.recent_within_token_budget()
        total_tokens = sum(t.token_estimate for t in selected)
        assert total_tokens <= 5

    def test_falls_back_to_empty_if_first_turn_exceeds(self, tmp_path):
        s = ChatMemoryStore("c", base_dir=tmp_path, max_context_tokens=0)
        s.append(ConversationTurn("user", "hello"))
        # budget=0 in the call overrides
        selected = s.recent_within_token_budget(max_tokens=0)
        assert selected == []


# ──────────────────────────────────────────────────────────────────────────────
# ChatMemoryStore — prune_before
# ──────────────────────────────────────────────────────────────────────────────


class TestPruneBefore:
    def test_prunes_old_turns(self, tmp_path):
        s = ChatMemoryStore("c", base_dir=tmp_path)
        s.append(ConversationTurn("user", "old", timestamp=100.0))
        s.append(ConversationTurn("user", "new", timestamp=1000.0))
        removed = s.prune_before(500.0)
        assert removed == 1
        assert len(s.all()) == 1
        assert s.all()[0].content == "new"

    def test_prune_nothing_when_all_new(self, tmp_path):
        s = ChatMemoryStore("c", base_dir=tmp_path)
        s.append(ConversationTurn("user", "new", timestamp=9999.0))
        removed = s.prune_before(500.0)
        assert removed == 0
        assert len(s.all()) == 1


# ──────────────────────────────────────────────────────────────────────────────
# KeywordSummariser
# ──────────────────────────────────────────────────────────────────────────────


class TestKeywordSummariser:
    def test_empty_turns(self):
        s = KeywordSummariser()
        result = s.summarise([])
        assert "summarise" in result.lower() or "No content" in result

    def test_produces_markdown(self):
        turns = [
            ConversationTurn("user", "configure database server settings"),
            ConversationTurn("assistant", "Done!"),
        ]
        result = KeywordSummariser().summarise(turns)
        assert "**Summary**" in result
        assert "-" in result  # bullet points


# ──────────────────────────────────────────────────────────────────────────────
# KeywordCompactor
# ──────────────────────────────────────────────────────────────────────────────


class TestKeywordCompactor:
    def test_no_old_turns_returns_no_op(self, tmp_path):
        s = ChatMemoryStore("c", base_dir=tmp_path)
        s.append(ConversationTurn("user", "recent", timestamp=time.time()))
        c = KeywordCompactor(compact_after_days=7)
        result = c.compact(s)
        assert result.turns_removed == 0
        assert not result.notes_written

    def test_compacts_old_turns(self, tmp_path):
        s = ChatMemoryStore("c", base_dir=tmp_path)
        old_ts = time.time() - 10 * 86_400   # 10 days ago
        s.append(ConversationTurn("user", "old configure backup server", timestamp=old_ts))
        s.append(ConversationTurn("user", "recent msg", timestamp=time.time()))
        c = KeywordCompactor(compact_after_days=7)
        result = c.compact(s)
        assert result.turns_removed == 1
        assert result.notes_written
        # Notes file created
        assert s.notes_path.exists()
        # Recent entry preserved
        remaining = s.all()
        assert len(remaining) == 1
        assert remaining[0].content == "recent msg"

    def test_notes_file_appended_on_second_pass(self, tmp_path):
        s = ChatMemoryStore("c", base_dir=tmp_path)
        old_ts = time.time() - 10 * 86_400
        s.append(ConversationTurn("user", "first old message", timestamp=old_ts))
        c = KeywordCompactor(compact_after_days=7)
        c.compact(s)
        first_content = s.notes_path.read_text()
        # Add another old turn and compact again
        s.append(ConversationTurn("user", "second old event horizon", timestamp=old_ts - 1))
        c.compact(s)
        second_content = s.notes_path.read_text()
        assert len(second_content) > len(first_content)

    def test_invalid_compact_after_days_raises(self):
        with pytest.raises(ValueError, match="compact_after_days must be"):
            KeywordCompactor(compact_after_days=0)
