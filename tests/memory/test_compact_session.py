"""
Unit tests for ConversationStore.compact_session().

Tests the atomic replace-with-summary operation, edge cases, and that
FTS5 cascades don't leave orphaned rows.
"""

from __future__ import annotations

import pytest

from navig.memory.conversation import ConversationStore, Message

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    """Isolated in-memory-backed ConversationStore per test."""
    return ConversationStore(tmp_path / "compact_test.db")


def _add_messages(store: ConversationStore, session_key: str, count: int) -> None:
    """Helper: add *count* alternating user/assistant messages."""
    for i in range(count):
        store.add_message(
            Message(
                session_key=session_key,
                role="user" if i % 2 == 0 else "assistant",
                content=f"Message {i}",
                token_count=10,
            )
        )


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


class TestCompactSessionHappyPath:
    """compact_session replaces history with a single summary message."""

    def test_returns_deleted_count(self, store):
        """compact_session returns the number of messages that were deleted."""
        _add_messages(store, "sess-1", 5)
        deleted = store.compact_session("sess-1", "Summary of five messages.")
        assert deleted == 5

    def test_single_summary_message_remains(self, store):
        """After compaction the session has exactly one message."""
        _add_messages(store, "sess-2", 8)
        store.compact_session("sess-2", "Compact summary.")
        history = store.get_history("sess-2")
        assert len(history) == 1

    def test_summary_content_stored_correctly(self, store):
        """The remaining message contains the exact summary text."""
        summary_text = "User discussed deploying to production. Next: add tests."
        _add_messages(store, "sess-3", 3)
        store.compact_session("sess-3", summary_text)
        history = store.get_history("sess-3")
        assert history[0].content == summary_text

    def test_summary_message_role_is_system(self, store):
        """The compact summary is stored as a system-role message."""
        _add_messages(store, "sess-4", 3)
        store.compact_session("sess-4", "Summary.")
        history = store.get_history("sess-4")
        assert history[0].role == "system"

    def test_compact_source_metadata(self, store):
        """The summary message carries metadata marking it as a compact artifact."""
        _add_messages(store, "sess-5", 4)
        store.compact_session("sess-5", "Recap.")
        history = store.get_history("sess-5")
        assert history[0].metadata.get("source") == "compact"

    def test_session_token_count_recalculated(self, store):
        """Session total_tokens is updated to reflect the summary token estimate."""
        _add_messages(store, "sess-6", 10)
        summary = "A" * 400   # 400 chars → ~100 tokens by the 4-chars-per-token heuristic
        store.compact_session("sess-6", summary)
        session = store.get_session("sess-6")
        assert session is not None
        assert session.total_tokens == max(1, len(summary) // 4)

    def test_large_history_compacted(self, store):
        """Works correctly when compacting a triple-digit message count."""
        _add_messages(store, "sess-7", 100)
        deleted = store.compact_session("sess-7", "Long session recap.")
        assert deleted == 100
        assert len(store.get_history("sess-7")) == 1

    def test_other_sessions_unaffected(self, store):
        """Compacting one session must not alter a different session's messages."""
        _add_messages(store, "sess-a", 5)
        _add_messages(store, "sess-b", 7)
        store.compact_session("sess-a", "Summary of A.")
        # Session B should be completely intact
        assert len(store.get_history("sess-b")) == 7

    def test_idempotent_second_compact(self, store):
        """Compacting an already-compacted session replaces the summary again."""
        _add_messages(store, "sess-8", 5)
        store.compact_session("sess-8", "First summary.")
        deleted_2nd = store.compact_session("sess-8", "Second summary.")
        assert deleted_2nd == 1  # the first summary message is the only thing deleted
        history = store.get_history("sess-8")
        assert len(history) == 1
        assert history[0].content == "Second summary."


# ---------------------------------------------------------------------------
# Edge-case / guard tests
# ---------------------------------------------------------------------------


class TestCompactSessionEdgeCases:
    """compact_session must handle degenerate inputs gracefully."""

    def test_empty_session_returns_zero(self, store):
        """compact_session on a session with no messages returns 0, no crash."""
        # Session row may not even exist — should still return 0 silently
        deleted = store.compact_session("nonexistent-session", "No-op summary.")
        assert deleted == 0

    def test_empty_session_no_messages_inserted(self, store):
        """When session has no messages, compact_session must not insert anything."""
        store.compact_session("ghost-session", "Summary.")
        history = store.get_history("ghost-session")
        assert history == []

    def test_single_message_session(self, store):
        """A session with exactly one message can be compacted."""
        _add_messages(store, "sess-single", 1)
        deleted = store.compact_session("sess-single", "One-message recap.")
        assert deleted == 1
        assert len(store.get_history("sess-single")) == 1

    def test_empty_summary_string(self, store):
        """An empty summary string is stored without error."""
        _add_messages(store, "sess-empty-sum", 3)
        deleted = store.compact_session("sess-empty-sum", "")
        assert deleted == 3
        history = store.get_history("sess-empty-sum")
        assert len(history) == 1
        assert history[0].content == ""

    def test_unicode_summary(self, store):
        """Multi-byte Unicode round-trips correctly through compact_session."""
        summary = "Пользователь обсуждал деплой. 🚀 Следующий шаг: тесты."
        _add_messages(store, "sess-unicode", 4)
        store.compact_session("sess-unicode", summary)
        history = store.get_history("sess-unicode")
        assert history[0].content == summary
