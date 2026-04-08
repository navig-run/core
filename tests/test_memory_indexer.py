"""
tests/test_memory_indexer.py — Unit tests for MemoryIndexer chunking and idempotency.

Covers:
- upsert_chunks ON CONFLICT deduplication (storage layer)
- file_needs_reindex hash-tracking (storage layer)
- _index_file skip-if-unchanged guard (idempotency fix)
- public index_file reports files_skipped on re-call with unchanged file
- sliding-window overlap produces distinct, non-suppressed chunk IDs
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from navig.memory.indexer import MemoryIndexer
from navig.memory.storage import FileMetadata, MemoryChunk, MemoryStorage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_storage(tmp_path: Path) -> MemoryStorage:
    """Return a fresh MemoryStorage backed by a temporary SQLite file."""
    return MemoryStorage(tmp_path / "test_index.db")


def _make_chunk(
    storage: MemoryStorage,
    *,
    file_path: str = "doc.md",
    content: str = "hello world",
    line_start: int = 1,
    line_end: int = 1,
    token_count: int = 3,
) -> MemoryChunk:
    """Build a MemoryChunk with the canonical content-addressed ID."""
    content_hash = MemoryStorage.compute_content_hash(content)
    return MemoryChunk(
        id=f"{file_path}:{line_start}-{line_end}:{content_hash}",
        file_path=file_path,
        content=content,
        line_start=line_start,
        line_end=line_end,
        token_count=token_count,
    )


# ---------------------------------------------------------------------------
# Storage layer: upsert_chunks deduplication
# ---------------------------------------------------------------------------

def test_upsert_chunks_dedup(tmp_path: Path) -> None:
    """Inserting the same chunk twice must not create a duplicate row."""
    storage = _make_storage(tmp_path)
    chunk = _make_chunk(storage)

    storage.upsert_chunks([chunk])
    storage.upsert_chunks([chunk])  # idempotent

    rows = storage.get_chunks_for_file("doc.md")
    assert len(rows) == 1, f"Expected 1 chunk, got {len(rows)}"
    assert rows[0].id == chunk.id


# ---------------------------------------------------------------------------
# Storage layer: file_needs_reindex tracks hash correctly
# ---------------------------------------------------------------------------

def test_file_needs_reindex_after_metadata_upsert(tmp_path: Path) -> None:
    """file_needs_reindex returns True for unknown file, False after upsert, True for changed hash."""
    storage = _make_storage(tmp_path)
    rel = "notes.md"

    # Unknown file → must be indexed
    assert storage.file_needs_reindex(rel, "abc123") is True

    # Record it with hash "abc123"
    storage.upsert_file_metadata(
        FileMetadata(
            file_path=rel,
            file_hash="abc123",
            last_modified="2026-01-01T00:00:00",
            chunk_count=1,
            total_tokens=10,
        )
    )

    # Same hash → skip
    assert storage.file_needs_reindex(rel, "abc123") is False
    # Different hash → must re-index
    assert storage.file_needs_reindex(rel, "xyz999") is True


# ---------------------------------------------------------------------------
# Indexer: _index_file is idempotent for unchanged files
# ---------------------------------------------------------------------------

def test_index_file_internal_skips_unchanged(tmp_path: Path) -> None:
    """_index_file returns an empty result on the second call when file hash is the same."""
    storage = _make_storage(tmp_path)
    indexer = MemoryIndexer(storage, embedding_provider=None)

    # Write a file large enough to exceed min_chunk_tokens (50)
    body = "word content filler text padding detail description summary context " * 12
    f = tmp_path / "note.md"
    f.write_text(f"# Notes\n\n{body}\n", encoding="utf-8")

    file_hash = indexer._compute_file_hash(f)
    base = tmp_path

    # First call — should index and return chunks
    r1 = indexer._index_file(f, base, file_hash, embed=False, force_reindex=False)
    assert r1["skipped"] is False, "First index must not be skipped"
    assert r1["chunks"] > 0, "First index should produce chunks"

    # Second call — same hash, no force → should skip
    r2 = indexer._index_file(f, base, file_hash, embed=False, force_reindex=False)
    assert r2["skipped"] is True, "Second index with same hash must be skipped"
    assert r2["chunks"] == 0
    assert r2["chunks_obj"] == []

    # force_reindex=True → re-indexes regardless
    r3 = indexer._index_file(f, base, file_hash, embed=False, force_reindex=True)
    assert r3["skipped"] is False, "force_reindex=True must not skip"
    assert r3["chunks"] > 0, "force_reindex=True should always produce chunks"


def test_index_file_does_not_delete_chunks_when_skipped(tmp_path: Path) -> None:
    """Chunks from the first index must survive a subsequent no-op re-index call."""
    storage = _make_storage(tmp_path)
    indexer = MemoryIndexer(storage, embedding_provider=None)

    body = "word content filler text padding detail description summary context " * 12
    f = tmp_path / "guide.md"
    f.write_text(f"# Guide\n\n{body}\n", encoding="utf-8")

    base = tmp_path
    file_hash = indexer._compute_file_hash(f)

    indexer._index_file(f, base, file_hash, embed=False)
    chunks_after_first = storage.get_chunks_for_file("guide.md")
    assert len(chunks_after_first) > 0

    # Second call — unchanged
    indexer._index_file(f, base, file_hash, embed=False)
    chunks_after_second = storage.get_chunks_for_file("guide.md")

    assert len(chunks_after_second) == len(chunks_after_first), (
        "Chunks must not be deleted or duplicated on a no-op re-index"
    )


# ---------------------------------------------------------------------------
# Public index_file: reports files_skipped on unchanged re-call
# ---------------------------------------------------------------------------

def test_public_index_file_skips_unchanged(tmp_path: Path) -> None:
    """Calling index_file (public) twice on an unchanged file should skip the second call."""
    storage = _make_storage(tmp_path)
    indexer = MemoryIndexer(storage, embedding_provider=None)

    body = "word content filler text padding detail description summary context " * 12
    f = tmp_path / "readme.md"
    f.write_text(f"# README\n\n{body}\n", encoding="utf-8")

    r1 = indexer.index_file(f, base_directory=tmp_path, embed=False)
    assert r1.files_processed == 1
    assert r1.files_skipped == 0

    # Second call — file unchanged
    r2 = indexer.index_file(f, base_directory=tmp_path, embed=False)
    assert r2.files_processed == 0
    assert r2.files_skipped == 1


def test_public_index_file_force_reindex_overrides_skip(tmp_path: Path) -> None:
    """force_reindex=True must always process even when hash is unchanged."""
    storage = _make_storage(tmp_path)
    indexer = MemoryIndexer(storage, embedding_provider=None)

    body = "word content filler text padding detail description summary context " * 12
    f = tmp_path / "doc.md"
    f.write_text(f"# Doc\n\n{body}\n", encoding="utf-8")

    indexer.index_file(f, base_directory=tmp_path, embed=False)

    r2 = indexer.index_file(f, base_directory=tmp_path, embed=False, force_reindex=True)
    assert r2.files_processed == 1
    assert r2.files_skipped == 0


# ---------------------------------------------------------------------------
# Chunking: overlap produces distinct chunk IDs, no suppression
# ---------------------------------------------------------------------------

def test_chunk_overlap_distinct_ids(tmp_path: Path) -> None:
    """
    A file large enough to produce multiple chunks must have distinct chunk IDs.
    Overlapping lines between consecutive chunks are part of different chunks
    (different line ranges → different IDs), so they must all be stored.
    """
    storage = _make_storage(tmp_path)
    indexer = MemoryIndexer(storage, embedding_provider=None)

    # Build 4 header-separated sections each ~175 tokens (4.0 chars/token,
    # ~700 chars per section).  Two sections exceed max_chunk_tokens (600)
    # so the chunker is forced to flush after the first two sections, producing
    # at least two distinct chunks.
    section_body = "word content filler text padding extra detail description summary context note " * 9
    lines = "\n\n".join(f"# Section {i}\n\n{section_body}" for i in range(1, 5))
    f = tmp_path / "big.md"
    f.write_text(lines + "\n", encoding="utf-8")

    result = indexer.index_file(f, base_directory=tmp_path, embed=False)
    assert result.files_processed == 1

    chunks = storage.get_chunks_for_file("big.md")
    assert len(chunks) >= 2, f"Expected ≥2 chunks, got {len(chunks)}"

    ids = [c.id for c in chunks]
    assert len(ids) == len(set(ids)), "Chunk IDs must be unique — no duplicates from overlap"


def test_index_directory_honors_internal_skipped_result(tmp_path: Path) -> None:
    """
    If _index_file reports skipped=True (e.g. race between pre-check and index),
    index_directory must count the file as skipped, not processed.
    """
    storage = _make_storage(tmp_path)
    indexer = MemoryIndexer(storage, embedding_provider=None)

    f = tmp_path / "race.md"
    f.write_text("# Race\n\nbody text " * 20, encoding="utf-8")

    # Outer pre-check says "needs indexing"...
    storage.file_needs_reindex = MagicMock(return_value=True)  # type: ignore[method-assign]
    # ...but internal call decides to skip.
    indexer._index_file = MagicMock(  # type: ignore[method-assign]
        return_value={
            "chunks": 0,
            "tokens": 0,
            "embedded": 0,
            "chunks_obj": [],
            "skipped": True,
        }
    )

    result = indexer.index_directory(tmp_path, force_reindex=False, embed=False)

    assert result.files_processed == 0
    assert result.files_skipped == 1
