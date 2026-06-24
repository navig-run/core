"""
Tests for navig.memory.knowledge_base — KnowledgeEntry dataclass.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from navig.memory.knowledge_base import KnowledgeEntry


# ─── KnowledgeEntry defaults ──────────────────────────────────────────────────


def test_knowledge_entry_defaults():
    e = KnowledgeEntry()
    assert e.key == ""
    assert e.content == ""
    assert e.summary is None
    assert e.tags == []
    assert e.source == ""
    assert e.expires_at is None
    assert e.metadata == {}
    assert e.embedding is None
    assert isinstance(e.id, str)
    assert len(e.id) == 36  # UUID4


def test_knowledge_entry_unique_ids():
    e1 = KnowledgeEntry()
    e2 = KnowledgeEntry()
    assert e1.id != e2.id


# ─── KnowledgeEntry.to_dict / from_dict ───────────────────────────────────────


def test_knowledge_entry_to_dict_fields():
    now = datetime(2024, 6, 1, 12, 0, 0)
    e = KnowledgeEntry(
        id="test-id",
        key="server.info",
        content="prod server uses Ubuntu 22.04",
        summary="Ubuntu prod",
        tags=["infra", "ubuntu"],
        source="agent",
        created_at=now,
        metadata={"host": "prod-01"},
    )
    d = e.to_dict()
    assert d["id"] == "test-id"
    assert d["key"] == "server.info"
    assert d["content"] == "prod server uses Ubuntu 22.04"
    assert d["summary"] == "Ubuntu prod"
    assert d["source"] == "agent"
    assert d["expires_at"] is None
    assert d["embedding"] is None
    # tags and metadata are JSON-encoded
    import json
    assert json.loads(d["tags"]) == ["infra", "ubuntu"]
    assert json.loads(d["metadata"]) == {"host": "prod-01"}


def test_knowledge_entry_roundtrip():
    now = datetime(2024, 3, 15, 9, 0, 0)
    exp = now + timedelta(days=7)
    original = KnowledgeEntry(
        id="roundtrip-id",
        key="deploy.preference",
        content="Deploy on Fridays",
        tags=["deploy", "preference"],
        source="user",
        created_at=now,
        expires_at=exp,
        metadata={"confidence": 0.9},
    )
    d = original.to_dict()
    restored = KnowledgeEntry.from_dict(d)
    assert restored.id == original.id
    assert restored.key == original.key
    assert restored.content == original.content
    assert restored.tags == original.tags
    assert restored.source == original.source
    assert restored.expires_at == original.expires_at
    assert restored.metadata == original.metadata


def test_knowledge_entry_from_dict_no_expiry():
    d = {
        "id": "e1",
        "key": "k",
        "content": "some fact",
        "tags": "[]",
        "source": "",
        "created_at": datetime.now().isoformat(),
        "metadata": "{}",
    }
    e = KnowledgeEntry.from_dict(d)
    assert e.expires_at is None


def test_knowledge_entry_with_embedding():
    e = KnowledgeEntry(embedding=[0.1, 0.2, 0.3])
    d = e.to_dict()
    import json
    assert json.loads(d["embedding"]) == [0.1, 0.2, 0.3]
    restored = KnowledgeEntry.from_dict(d)
    assert restored.embedding == [0.1, 0.2, 0.3]


def test_knowledge_entry_tags_as_list_in_from_dict():
    """from_dict must handle tags stored as a Python list (not JSON string)."""
    d = {
        "id": "x",
        "key": "k",
        "content": "c",
        "tags": ["a", "b"],
        "source": "",
        "created_at": datetime.now().isoformat(),
        "metadata": {},
    }
    e = KnowledgeEntry.from_dict(d)
    assert e.tags == ["a", "b"]


# ─── KnowledgeEntry.is_expired ────────────────────────────────────────────────


def test_is_expired_no_expiry():
    e = KnowledgeEntry()
    assert e.is_expired is False


def test_is_expired_future():
    e = KnowledgeEntry(expires_at=datetime.now() + timedelta(hours=1))
    assert e.is_expired is False


def test_is_expired_past():
    e = KnowledgeEntry(expires_at=datetime.now() - timedelta(seconds=1))
    assert e.is_expired is True
