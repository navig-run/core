"""
Unit tests for the memory module.

Tests conversation storage, embeddings, knowledge base, and RAG pipeline.
"""

from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from navig.memory.conversation import ConversationStore, Message
from navig.memory.knowledge_base import KnowledgeBase, KnowledgeEntry
from navig.memory.rag import ContextWindow, RAGConfig, RAGPipeline, RetrievalResult

# ============================================================================
# CONVERSATION STORE TESTS
# ============================================================================


class TestMessage:
    """Tests for Message dataclass."""

    def test_message_creation(self):
        """Test creating a message with defaults."""
        msg = Message()

        assert msg.id is not None
        assert msg.session_key == ""
        assert msg.role == "user"
        assert msg.content == ""
        assert isinstance(msg.timestamp, datetime)
        assert msg.metadata == {}
        assert msg.token_count == 0

    def test_message_with_values(self):
        """Test creating a message with custom values."""
        msg = Message(
            session_key="test-session",
            role="assistant",
            content="Hello, world!",
            token_count=5,
            metadata={"model": "gpt-4"},
        )

        assert msg.session_key == "test-session"
        assert msg.role == "assistant"
        assert msg.content == "Hello, world!"
        assert msg.token_count == 5
        assert msg.metadata["model"] == "gpt-4"

    def test_message_to_dict(self):
        """Test serializing message to dict."""
        msg = Message(
            session_key="test",
            role="user",
            content="test content",
        )

        data = msg.to_dict()

        assert data["id"] == msg.id
        assert data["session_key"] == "test"
        assert data["role"] == "user"
        assert data["content"] == "test content"
        assert "timestamp" in data

    def test_message_from_dict(self):
        """Test deserializing message from dict."""
        data = {
            "id": "test-id",
            "session_key": "session-1",
            "role": "assistant",
            "content": "response",
            "timestamp": "2024-01-01T12:00:00",
            "metadata": "{}",
            "token_count": 10,
        }

        msg = Message.from_dict(data)

        assert msg.id == "test-id"
        assert msg.session_key == "session-1"
        assert msg.role == "assistant"
        assert msg.content == "response"
        assert msg.token_count == 10


class TestConversationStore:
    """Tests for ConversationStore."""

    @pytest.fixture
    def store(self, tmp_path):
        """Create a temporary conversation store."""
        db_path = tmp_path / "test_memory.db"
        return ConversationStore(db_path)

    def test_store_initialization(self, store):
        """Test store initializes correctly."""
        assert store.db_path.exists()

    def test_add_message(self, store):
        """Test adding a message."""
        msg = Message(
            session_key="session-1",
            role="user",
            content="Hello!",
            token_count=2,
        )

        result = store.add_message(msg)

        assert result.id == msg.id
        assert result.session_key == "session-1"

    def test_get_history(self, store):
        """Test retrieving message history."""
        # Add messages
        for i in range(5):
            msg = Message(
                session_key="session-1",
                role="user" if i % 2 == 0 else "assistant",
                content=f"Message {i}",
            )
            store.add_message(msg)

        history = store.get_history("session-1")

        assert len(history) == 5
        # Should be in chronological order
        assert history[0].content == "Message 0"
        assert history[4].content == "Message 4"

    def test_get_history_with_limit(self, store):
        """Test history limit."""
        for i in range(10):
            msg = Message(
                session_key="session-1",
                role="user",
                content=f"Message {i}",
            )
            store.add_message(msg)

        history = store.get_history("session-1", limit=3)

        assert len(history) == 3

    def test_get_history_by_role(self, store):
        """Test filtering by role."""
        for i in range(6):
            msg = Message(
                session_key="session-1",
                role="user" if i % 2 == 0 else "assistant",
                content=f"Message {i}",
            )
            store.add_message(msg)

        user_messages = store.get_history("session-1", roles=["user"])

        assert len(user_messages) == 3
        assert all(m.role == "user" for m in user_messages)

    def test_get_session(self, store):
        """Test getting session info."""
        for i in range(3):
            msg = Message(
                session_key="session-1",
                role="user",
                content=f"Message {i}",
                token_count=10,
            )
            store.add_message(msg)

        session = store.get_session("session-1")

        assert session is not None
        assert session.session_key == "session-1"
        assert session.message_count == 3
        assert session.total_tokens == 30

    def test_get_nonexistent_session(self, store):
        """Test getting a session that doesn't exist."""
        session = store.get_session("nonexistent")
        assert session is None

    def test_list_sessions(self, store):
        """Test listing all sessions."""
        # Create multiple sessions
        for session in ["s1", "s2", "s3"]:
            msg = Message(
                session_key=session,
                role="user",
                content="test",
            )
            store.add_message(msg)

        sessions = store.list_sessions()

        assert len(sessions) == 3

    def test_delete_session(self, store):
        """Test deleting a session."""
        msg = Message(
            session_key="to-delete",
            role="user",
            content="test",
        )
        store.add_message(msg)

        assert store.get_session("to-delete") is not None

        result = store.delete_session("to-delete")

        assert result is True
        assert store.get_session("to-delete") is None

    def test_clear_old_messages(self, store):
        """Test compacting session history."""
        for i in range(20):
            msg = Message(
                session_key="session-1",
                role="user",
                content=f"Message {i}",
            )
            store.add_message(msg)

        deleted = store.clear_old_messages("session-1", keep_last=5)

        # Should delete most messages, keeping ~5 recent ones
        assert deleted >= 14
        history = store.get_history("session-1")
        assert len(history) <= 6  # Allow for off-by-one

    def test_search_content(self, store):
        """Test searching message content."""
        messages = [
            ("session-1", "How do I configure SSH?"),
            ("session-1", "Here is how to configure SSH..."),
            ("session-2", "What is Docker?"),
        ]

        for session, content in messages:
            msg = Message(session_key=session, role="user", content=content)
            store.add_message(msg)

        results = store.search_content("SSH")

        assert len(results) == 2
        assert all("SSH" in m.content for m in results)

    def test_get_token_count(self, store):
        """Test getting session token count."""
        for i in range(3):
            msg = Message(
                session_key="session-1",
                role="user",
                content="test",
                token_count=100,
            )
            store.add_message(msg)

        count = store.get_token_count("session-1")

        assert count == 300


# ============================================================================
# KNOWLEDGE BASE TESTS
# ============================================================================


class TestKnowledgeEntry:
    """Tests for KnowledgeEntry dataclass."""

    def test_entry_creation(self):
        """Test creating a knowledge entry."""
        entry = KnowledgeEntry(
            key="test-key",
            content="Test content",
            tags=["tag1", "tag2"],
            source="cli",
        )

        assert entry.key == "test-key"
        assert entry.content == "Test content"
        assert entry.tags == ["tag1", "tag2"]
        assert entry.source == "cli"
        assert entry.expires_at is None

    def test_entry_expiration(self):
        """Test entry expiration check."""
        past = datetime.utcnow() - timedelta(hours=1)
        future = datetime.utcnow() + timedelta(hours=1)

        expired_entry = KnowledgeEntry(
            key="expired",
            content="old",
            expires_at=past,
        )

        valid_entry = KnowledgeEntry(
            key="valid",
            content="new",
            expires_at=future,
        )

        assert expired_entry.is_expired is True
        assert valid_entry.is_expired is False

    def test_entry_serialization(self):
        """Test entry to/from dict."""
        entry = KnowledgeEntry(
            key="test",
            content="content",
            tags=["a", "b"],
            source="test",
        )

        data = entry.to_dict()
        restored = KnowledgeEntry.from_dict(data)

        assert restored.key == entry.key
        assert restored.content == entry.content
        assert restored.tags == entry.tags


class TestKnowledgeBase:
    """Tests for KnowledgeBase."""

    @pytest.fixture
    def kb(self, tmp_path):
        """Create a temporary knowledge base."""
        db_path = tmp_path / "test_knowledge.db"
        return KnowledgeBase(db_path, embedding_provider=None)

    def test_kb_initialization(self, kb):
        """Test KB initializes correctly."""
        assert kb.db_path.exists()

    def test_upsert_entry(self, kb):
        """Test adding an entry."""
        entry = KnowledgeEntry(
            key="project-info",
            content="This project uses Python 3.10",
            tags=["project", "python"],
            source="user",
        )

        result = kb.upsert(entry, compute_embedding=False)

        assert result.key == "project-info"

    def test_get_entry(self, kb):
        """Test retrieving an entry."""
        entry = KnowledgeEntry(
            key="test-key",
            content="test content",
        )
        kb.upsert(entry, compute_embedding=False)

        retrieved = kb.get("test-key")

        assert retrieved is not None
        assert retrieved.content == "test content"

    def test_get_nonexistent_entry(self, kb):
        """Test getting entry that doesn't exist."""
        result = kb.get("nonexistent")
        assert result is None

    def test_update_existing_entry(self, kb):
        """Test upserting updates existing entry."""
        entry1 = KnowledgeEntry(
            key="same-key",
            content="original content",
        )
        kb.upsert(entry1, compute_embedding=False)

        entry2 = KnowledgeEntry(
            key="same-key",
            content="updated content",
        )
        kb.upsert(entry2, compute_embedding=False)

        retrieved = kb.get("same-key")

        assert retrieved.content == "updated content"

    def test_delete_entry(self, kb):
        """Test deleting an entry."""
        entry = KnowledgeEntry(
            key="to-delete",
            content="test",
        )
        kb.upsert(entry, compute_embedding=False)

        result = kb.delete("to-delete")

        assert result is True
        assert kb.get("to-delete") is None

    def test_list_by_tag(self, kb):
        """Test filtering by tag."""
        entries = [
            KnowledgeEntry(key="e1", content="c1", tags=["python"]),
            KnowledgeEntry(key="e2", content="c2", tags=["python", "django"]),
            KnowledgeEntry(key="e3", content="c3", tags=["javascript"]),
        ]
        for e in entries:
            kb.upsert(e, compute_embedding=False)

        python_entries = kb.list_by_tag("python")

        assert len(python_entries) == 2

    def test_list_by_source(self, kb):
        """Test filtering by source."""
        entries = [
            KnowledgeEntry(key="e1", content="c1", source="user"),
            KnowledgeEntry(key="e2", content="c2", source="system"),
            KnowledgeEntry(key="e3", content="c3", source="user"),
        ]
        for e in entries:
            kb.upsert(e, compute_embedding=False)

        user_entries = kb.list_by_source("user")

        assert len(user_entries) == 2

    def test_text_search(self, kb):
        """Test text-based search."""
        entries = [
            KnowledgeEntry(key="e1", content="Python is great"),
            KnowledgeEntry(key="e2", content="JavaScript is also good"),
            KnowledgeEntry(key="e3", content="Python and Django work together"),
        ]
        for e in entries:
            kb.upsert(e, compute_embedding=False)

        results = kb.text_search("Python")

        assert len(results) == 2

    def test_add_with_ttl(self, kb):
        """Test adding entry with TTL."""
        entry = kb.add_with_ttl(
            key="temp-key",
            content="temporary content",
            ttl_hours=24,
        )

        assert entry.expires_at is not None
        assert entry.expires_at > datetime.utcnow()

    def test_count(self, kb):
        """Test counting entries."""
        for i in range(5):
            entry = KnowledgeEntry(key=f"key-{i}", content=f"content-{i}")
            kb.upsert(entry, compute_embedding=False)

        assert kb.count() == 5

    def test_clear(self, kb):
        """Test clearing all entries."""
        for i in range(3):
            entry = KnowledgeEntry(key=f"key-{i}", content=f"content-{i}")
            kb.upsert(entry, compute_embedding=False)

        deleted = kb.clear()

        assert deleted == 3
        assert kb.count() == 0

    def test_export_import(self, kb):
        """Test exporting and importing entries."""
        for i in range(3):
            entry = KnowledgeEntry(
                key=f"key-{i}",
                content=f"content-{i}",
                tags=["test"],
            )
            kb.upsert(entry, compute_embedding=False)

        exported = kb.export_entries()

        assert len(exported) == 3

        # Clear and reimport
        kb.clear()
        imported = kb.import_entries(exported)

        assert imported == 3
        assert kb.count() == 3


# ============================================================================
# RAG PIPELINE TESTS
# ============================================================================


class TestRAGPipeline:
    """Tests for RAG pipeline."""

    @pytest.fixture
    def rag(self, tmp_path):
        """Create RAG pipeline with mocked stores."""
        conv_db = tmp_path / "memory.db"
        kb_db = tmp_path / "knowledge.db"

        store = ConversationStore(conv_db)
        kb = KnowledgeBase(kb_db, embedding_provider=None)

        return RAGPipeline(
            conversation_store=store,
            knowledge_base=kb,
            config=RAGConfig(
                max_history_messages=10,
                max_knowledge_entries=3,
            ),
        )

    def test_retrieve_empty(self, rag):
        """Test retrieval with no data."""
        result = rag.retrieve(
            query="test query",
            session_key="nonexistent",
        )

        assert isinstance(result, RetrievalResult)
        assert result.context == ""

    def test_retrieve_with_history(self, rag):
        """Test retrieval includes conversation history."""
        # Add some history
        for i in range(3):
            msg = Message(
                session_key="test-session",
                role="user" if i % 2 == 0 else "assistant",
                content=f"Test message {i}",
            )
            rag.conversation_store.add_message(msg)

        result = rag.retrieve(
            query="test query",
            session_key="test-session",
        )

        assert "Conversation History" in result.context
        assert "Test message" in result.context

    def test_retrieve_with_knowledge(self, rag):
        """Test retrieval includes knowledge base."""
        # Add some knowledge
        entry = KnowledgeEntry(
            key="ssh-config",
            content="SSH configuration guide",
            tags=["ssh"],
        )
        rag.knowledge_base.upsert(entry, compute_embedding=False)

        result = rag.retrieve(
            query="SSH",
        )

        assert "ssh-config" in result.context or "SSH" in result.context

    def test_build_prompt(self, rag):
        """Test building a complete prompt."""
        prompt = rag.build_prompt(
            query="How do I configure SSH?",
            system_prompt="You are a helpful assistant.",
        )

        assert "You are a helpful assistant" in prompt
        assert "How do I configure SSH?" in prompt

    def test_extract_file_references(self, rag, tmp_path):
        """Test extracting file references from text."""
        # Create a test file
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")

        text = f"See `{test_file}` for details."

        paths = rag.extract_file_references(text)

        assert len(paths) == 1
        assert paths[0] == test_file

    def test_summarize_session(self, rag):
        """Test session summarization."""
        for i in range(5):
            msg = Message(
                session_key="summary-test",
                role="user" if i % 2 == 0 else "assistant",
                content=f"Discussion about topic {i}",
            )
            rag.conversation_store.add_message(msg)

        summary = rag.summarize_session("summary-test")

        assert "summary-test" in summary
        assert "Messages:" in summary


class TestContextWindow:
    """Tests for ContextWindow budget management."""

    def test_allocate_default(self):
        """Test default allocation."""
        window = ContextWindow(max_tokens=8000)

        allocation = window.allocate()

        assert "history" in allocation
        assert "knowledge" in allocation
        assert "files" in allocation
        assert sum(allocation.values()) <= window.available

    def test_allocate_custom_priorities(self):
        """Test custom priority allocation."""
        window = ContextWindow(max_tokens=10000)

        allocation = window.allocate(
            history_priority=0.5,
            knowledge_priority=0.25,
            files_priority=0.25,
        )

        # History should get most tokens
        assert allocation["history"] > allocation["knowledge"]
        assert allocation["history"] > allocation["files"]

    def test_fits(self):
        """Test content fitting check."""
        window = ContextWindow(max_tokens=1000, reserved_for_response=200)

        short_content = "x" * 100  # ~25 tokens
        long_content = "x" * 10000  # ~2500 tokens

        assert window.fits(short_content) is True
        assert window.fits(long_content) is False

    def test_remaining_after(self):
        """Test remaining tokens calculation."""
        window = ContextWindow(max_tokens=1000, reserved_for_response=200)

        content = "x" * 400  # ~100 tokens
        remaining = window.remaining_after(content)

        assert remaining > 0
        assert remaining < window.available


# ============================================================================
# EMBEDDING PROVIDER TESTS
# ============================================================================


class TestEmbeddingProviders:
    """Tests for embedding providers."""

    def test_local_provider_lazy_load(self):
        """Test local provider doesn't load model on init."""
        from navig.memory.embeddings import LocalEmbeddingProvider

        provider = LocalEmbeddingProvider()

        # Model should not be loaded yet
        assert provider._model is None

    def test_local_provider_dimension(self):
        """Test dimension property."""
        from navig.memory.embeddings import LocalEmbeddingProvider

        provider = LocalEmbeddingProvider(model_name="all-MiniLM-L6-v2")

        assert provider.dimension == 384

    def test_openai_provider_requires_key(self):
        """Test OpenAI provider requires API key."""
        from navig.memory.embeddings import OpenAIEmbeddingProvider

        # Clear any environment variable
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="API key required"):
                OpenAIEmbeddingProvider(api_key=None)

    def test_cached_provider(self, tmp_path):
        """Test cached embedding provider."""
        from navig.memory.embeddings import CachedEmbeddingProvider, EmbeddingProvider

        # Create mock provider
        mock_provider = Mock(spec=EmbeddingProvider)
        mock_provider.dimension = 384
        mock_provider.embed_text.return_value = [0.1] * 384
        mock_provider.embed_batch.return_value = [[0.1] * 384]

        cached = CachedEmbeddingProvider(
            mock_provider,
            cache_dir=tmp_path / "cache",
        )

        # First call should hit provider
        result1 = cached.embed_text("test text")
        assert mock_provider.embed_text.call_count == 1

        # Second call should use cache
        result2 = cached.embed_text("test text")
        assert mock_provider.embed_text.call_count == 1  # No new calls
        assert result1 == result2

    def test_similarity_calculation(self):
        """Test cosine similarity calculation."""
        from navig.memory.embeddings import LocalEmbeddingProvider

        provider = LocalEmbeddingProvider()

        # Identical vectors
        vec1 = [1.0, 0.0, 0.0]
        vec2 = [1.0, 0.0, 0.0]

        similarity = provider.similarity(vec1, vec2)
        assert abs(similarity - 1.0) < 0.001

        # Orthogonal vectors
        vec3 = [0.0, 1.0, 0.0]

        similarity = provider.similarity(vec1, vec3)
        assert abs(similarity) < 0.001


# ============================================================================
# INTEGRATION TESTS
# ============================================================================


class TestMemoryIntegration:
    """Integration tests for memory module."""

    def test_full_workflow(self, tmp_path):
        """Test complete memory workflow."""
        from navig.memory import (
            ConversationStore,
            KnowledgeBase,
            KnowledgeEntry,
            Message,
            RAGPipeline,
        )

        # Setup
        store = ConversationStore(tmp_path / "memory.db")
        kb = KnowledgeBase(tmp_path / "knowledge.db")
        rag = RAGPipeline(
            conversation_store=store,
            knowledge_base=kb,
        )

        # Simulate a conversation
        session_key = "task-123"

        # User asks a question
        user_msg = Message(
            session_key=session_key,
            role="user",
            content="How do I backup my database?",
        )
        store.add_message(user_msg)

        # Add relevant knowledge
        kb_entry = KnowledgeEntry(
            key="db-backup-guide",
            content="Use mysqldump for MySQL or pg_dump for PostgreSQL",
            tags=["database", "backup"],
        )
        kb.upsert(kb_entry, compute_embedding=False)

        # Build context for AI
        result = rag.retrieve(
            query="database backup",
            session_key=session_key,
        )

        assert "How do I backup" in result.context

        # Record assistant response
        assistant_msg = Message(
            session_key=session_key,
            role="assistant",
            content="For MySQL, use: mysqldump -u user -p database > backup.sql",
            token_count=20,
        )
        store.add_message(assistant_msg)

        # Verify history
        history = store.get_history(session_key)
        assert len(history) == 2

        # Cleanup
        store.close()
        kb.close()
