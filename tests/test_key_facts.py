"""
Tests for the key facts conversational memory system.

Covers:
  - KeyFactStore CRUD operations
  - Soft-delete, restore, supersession
  - Keyword search (FTS5 + LIKE fallback)
  - Duplicate detection (exact + Jaccard)
  - Rule-based fact extraction
  - Fact retriever ranking + formatting
  - Context builder integration
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path

import pytest

from navig.memory.fact_extractor import (
    ExtractionResult,
    FactExtractor,
    _is_high_signal,
    _is_low_signal,
    extract_rules,
)
from navig.memory.fact_retriever import (
    FactRetrievalResult,
    FactRetriever,
    RetrievalConfig,
)
from navig.memory.key_facts import VALID_CATEGORIES, KeyFact, KeyFactStore, _utcnow

# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path):
    """Create a fresh temp database for each test."""
    return tmp_path / "test_key_facts.db"


@pytest.fixture
def store(tmp_db):
    """Fresh KeyFactStore for each test."""
    s = KeyFactStore(db_path=tmp_db)
    yield s
    s.close()


@pytest.fixture
def populated_store(store):
    """Store pre-loaded with a set of test facts."""
    facts = [
        KeyFact(
            content="User prefers Python 3.12+",
            category="preference",
            confidence=0.9,
            tags=["python", "preference"],
        ),
        KeyFact(
            content="User works at Navig Labs",
            category="identity",
            confidence=0.95,
            tags=["identity", "company"],
        ),
        KeyFact(
            content="User deploys using Docker on Ubuntu",
            category="technical",
            confidence=0.85,
            tags=["docker", "linux", "technical"],
        ),
        KeyFact(
            content="User decided to use PostgreSQL over MySQL",
            category="decision",
            confidence=0.8,
            tags=["database", "decision"],
        ),
        KeyFact(
            content="User likes dark mode in all editors",
            category="preference",
            confidence=0.7,
            tags=["ui", "preference"],
        ),
    ]
    for f in facts:
        store.upsert(f)
    return store


# ── KeyFact Dataclass Tests ───────────────────────────────────


class TestKeyFact:
    def test_default_values(self):
        f = KeyFact(content="test fact")
        assert f.content == "test fact"
        assert f.category == "context"
        assert f.confidence == 0.8
        assert f.is_active is True
        assert f.deleted is False
        assert f.superseded_by is None
        assert len(f.id) == 36  # UUID format

    def test_token_count(self):
        f = KeyFact(content="x" * 100)
        assert f.token_count == 25  # 100 / 4

    def test_is_active_when_deleted(self):
        f = KeyFact(content="test", deleted=True)
        assert f.is_active is False

    def test_is_active_when_superseded(self):
        f = KeyFact(content="test", superseded_by="other-id")
        assert f.is_active is False

    def test_to_dict_roundtrip(self):
        f = KeyFact(content="test", tags=["a", "b"], metadata={"key": "val"})
        d = f.to_dict()
        assert isinstance(d["tags"], str)
        assert json.loads(d["tags"]) == ["a", "b"]
        assert json.loads(d["metadata"]) == {"key": "val"}


# ── KeyFactStore CRUD Tests ───────────────────────────────────


class TestKeyFactStore:
    def test_upsert_and_get(self, store):
        f = KeyFact(content="User prefers vim", category="preference")
        result = store.upsert(f)
        assert result.id == f.id

        retrieved = store.get(f.id)
        assert retrieved is not None
        assert retrieved.content == "User prefers vim"
        assert retrieved.category == "preference"

    def test_get_nonexistent(self, store):
        assert store.get("nonexistent-id") is None

    def test_get_active(self, populated_store):
        facts = populated_store.get_active()
        assert len(facts) == 5

    def test_get_active_by_category(self, populated_store):
        prefs = populated_store.get_active(category="preference")
        assert len(prefs) == 2
        assert all(f.category == "preference" for f in prefs)

    def test_get_active_min_confidence(self, populated_store):
        high = populated_store.get_active(min_confidence=0.85)
        assert len(high) == 3  # 0.95, 0.9, 0.85

    def test_soft_delete(self, store):
        f = KeyFact(content="will be deleted")
        store.upsert(f)
        assert store.soft_delete(f.id) is True

        deleted = store.get(f.id)
        assert deleted.deleted is True
        assert deleted.is_active is False

        # Should not appear in active list
        active = store.get_active()
        assert all(a.id != f.id for a in active)

    def test_restore(self, store):
        f = KeyFact(content="will be restored")
        store.upsert(f)
        store.soft_delete(f.id)
        assert store.restore(f.id) is True

        restored = store.get(f.id)
        assert restored.deleted is False
        assert restored.is_active is True

    def test_supersede(self, store):
        old = KeyFact(content="User uses Python 3.10")
        store.upsert(old)

        new = KeyFact(content="User uses Python 3.12")
        result = store.supersede(old.id, new)
        assert result.id == new.id

        old_fact = store.get(old.id)
        assert old_fact.superseded_by == new.id
        assert old_fact.is_active is False

    def test_update_content(self, store):
        f = KeyFact(content="User likes tabs")
        store.upsert(f)

        updated = store.update_content(f.id, "User prefers spaces over tabs")
        assert updated.content == "User prefers spaces over tabs"
        assert updated.id == f.id

    def test_update_content_nonexistent(self, store):
        assert store.update_content("no-id", "new") is None

    def test_duplicate_detection_exact(self, store):
        f1 = KeyFact(content="User prefers dark mode", confidence=0.7)
        store.upsert(f1)

        f2 = KeyFact(content="User prefers dark mode", confidence=0.8)
        result = store.upsert(f2)

        # Should merge, not duplicate
        active = store.get_active()
        assert len(active) == 1
        # Confidence should have been bumped
        assert active[0].confidence >= 0.75

    def test_duplicate_detection_jaccard(self, store):
        f1 = KeyFact(content="user prefers dark mode themes")
        store.upsert(f1)

        f2 = KeyFact(content="User prefers dark mode themes")  # case difference
        store.upsert(f2)

        active = store.get_active()
        assert len(active) == 1

    def test_invalid_category_normalized(self, store):
        f = KeyFact(content="test", category="invalid_cat")
        result = store.upsert(f)
        assert result.category == "context"  # normalized

    def test_record_access(self, store):
        f = KeyFact(content="accessed fact")
        store.upsert(f)
        assert f.access_count == 0

        store.record_access([f.id])
        updated = store.get(f.id)
        assert updated.access_count == 1
        assert updated.last_accessed is not None

    def test_stats(self, populated_store):
        stats = populated_store.get_stats()
        assert stats["total"] == 5
        assert stats["active"] == 5
        assert stats["deleted"] == 0
        assert "preference" in stats["by_category"]

    def test_search_keyword(self, populated_store):
        results = populated_store.search_keyword("Python")
        assert len(results) > 0
        assert any("python" in f.content.lower() for f, _ in results)

    def test_search_keyword_no_results(self, populated_store):
        results = populated_store.search_keyword("xyznonexistent123")
        assert len(results) == 0

    def test_purge_deleted(self, store):
        f = KeyFact(content="old deleted fact")
        store.upsert(f)
        store.soft_delete(f.id)
        # Purge with -1 days = purge anything (since age >= 0 > -1)
        count = store.purge_deleted(older_than_days=-1)
        assert count == 1
        assert store.get(f.id) is None


# ── Rule-Based Extraction Tests ───────────────────────────────


class TestRuleExtraction:
    def test_preference_pattern(self):
        result = extract_rules(
            "I prefer Python over JavaScript for backend work",
            "Good choice!",
        )
        assert result.count >= 1
        assert any("python" in f.content.lower() for f in result.facts)

    def test_identity_pattern(self):
        result = extract_rules(
            "My name is Alex and I work at TechCorp",
            "Nice to meet you, Alex!",
        )
        assert result.count >= 1
        assert any(f.category == "identity" for f in result.facts)

    def test_decision_pattern(self):
        result = extract_rules(
            "Let's go with PostgreSQL for the database",
            "PostgreSQL it is.",
        )
        assert result.count >= 1
        assert any(f.category == "decision" for f in result.facts)

    def test_technical_pattern(self):
        result = extract_rules(
            "Our stack uses Docker and Kubernetes on AWS",
            "Got it.",
        )
        assert result.count >= 1
        assert any(f.category == "technical" for f in result.facts)

    def test_low_signal_message(self):
        assert _is_low_signal("hello") is True
        assert _is_low_signal("thanks!") is True
        assert _is_low_signal("fix the bug") is True

    def test_high_signal_message(self):
        assert _is_high_signal("I prefer dark mode") is True
        assert _is_high_signal("From now on, use TypeScript") is True
        assert _is_high_signal("My timezone is UTC+2") is True

    def test_short_message_skipped(self):
        result = extract_rules("hi", "hello")
        assert result.count == 0

    def test_no_false_positives_on_questions(self):
        result = extract_rules(
            "what is the best Python version?",
            "Python 3.12 is recommended.",
        )
        # Should not extract the question as a fact
        assert all(f.confidence < 0.8 for f in result.facts)


# ── FactExtractor Integration Tests ──────────────────────────


class TestFactExtractor:
    def test_sync_extraction(self, store):
        extractor = FactExtractor(store=store, mode="rule")
        result = extractor.extract_sync(
            "I always use VSCode for development",
            "VSCode is a great choice.",
        )
        # Rules may or may not match — just verify no crash
        assert isinstance(result, ExtractionResult)

    def test_sync_skips_short_messages(self, store):
        extractor = FactExtractor(store=store, mode="rule")
        result = extractor.extract_sync("ok", "okay")
        assert result.count == 0

    def test_max_facts_per_turn(self, store):
        extractor = FactExtractor(store=store, mode="rule", max_facts_per_turn=1)
        result = extractor.extract_sync(
            "I prefer Python, I use Docker, I like dark mode, I decided to go with PostgreSQL",
            "Noted.",
        )
        assert result.count <= 1


# ── FactRetriever Tests ───────────────────────────────────────


class TestFactRetriever:
    def test_retrieve_basic(self, populated_store):
        retriever = FactRetriever(populated_store)
        result = retriever.retrieve("Python version")
        assert isinstance(result, FactRetrievalResult)
        assert result.total_active == 5

    def test_retrieve_formats_output(self, populated_store):
        retriever = FactRetriever(populated_store)
        result = retriever.retrieve("Python")
        if result.count > 0:
            assert "memories" in result.formatted.lower() or "##" in result.formatted

    def test_retrieve_all_active(self, populated_store):
        retriever = FactRetriever(populated_store)
        result = retriever.retrieve_all_active()
        assert result.count == 5
        assert len(result.formatted) > 0

    def test_retrieve_empty_store(self, tmp_db):
        store = KeyFactStore(db_path=tmp_db)
        retriever = FactRetriever(store)
        result = retriever.retrieve("anything")
        assert result.count == 0
        store.close()

    def test_token_budget_respected(self, populated_store):
        retriever = FactRetriever(
            populated_store,
            config=RetrievalConfig(max_tokens=50),
        )
        result = retriever.retrieve("anything")
        assert result.token_estimate <= 60  # Some slack for header

    def test_category_filter(self, populated_store):
        retriever = FactRetriever(populated_store)
        result = retriever.retrieve("identity", category="identity")
        if result.count > 0:
            assert all(rf.fact.category == "identity" for rf in result.facts)

    def test_recency_score(self):
        from navig.memory.fact_retriever import FactRetriever

        score = FactRetriever._recency_score(_utcnow())
        assert score > 0.95  # Just created = near 1.0


# ── Context Builder Integration Tests ─────────────────────────


class TestContextBuilderIntegration:
    def test_key_facts_in_empty_context(self):
        from navig.memory.context_builder import EMPTY_CONTEXT

        assert "key_facts" in EMPTY_CONTEXT

    def test_build_context_includes_key_facts_key(self, tmp_path):
        from navig.memory.context_builder import ContextBuilder

        builder = ContextBuilder(
            config={"enabled": True, "include_key_facts": True},
            project_root=tmp_path,
        )
        ctx = builder.build_context("test query")
        assert "key_facts" in ctx

    def test_build_context_disables_key_facts(self, tmp_path):
        from navig.memory.context_builder import ContextBuilder

        builder = ContextBuilder(
            config={"enabled": True, "include_key_facts": False},
            project_root=tmp_path,
        )
        ctx = builder.build_context("test query")
        assert ctx["key_facts"] == ""
