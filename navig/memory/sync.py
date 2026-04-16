from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from navig.memory.storage import MemoryChunk, MemoryStorage


def _as_chunk(item: dict[str, Any], default_file: str) -> MemoryChunk | None:
    content = item.get("content")
    if not isinstance(content, str) or not content.strip():
        return None

    chunk_id = str(item.get("id") or item.get("chunk_id") or "")
    if not chunk_id:
        chunk_id = f"sync::{abs(hash((default_file, content, item.get('line_start', 1), item.get('line_end', 1))))}"

    metadata = item.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {"raw_metadata": metadata}
    if not isinstance(metadata, dict):
        metadata = {}

    return MemoryChunk(
        id=chunk_id,
        file_path=str(item.get("file_path") or default_file),
        content=content,
        line_start=int(item.get("line_start") or 1),
        line_end=int(item.get("line_end") or 1),
        token_count=int(item.get("token_count") or 0),
        metadata=metadata,
    )


def import_chunks(db_path: Path, chunks: list[dict[str, Any]], formation: str | None = None) -> tuple[int, int]:
    """Import remote chunk payload into local MemoryStorage."""
    storage = MemoryStorage(db_path)
    default_file = f"remote/{formation or 'default'}/sync"

    parsed: list[MemoryChunk] = []
    skipped = 0
    for item in chunks:
        if not isinstance(item, dict):
            skipped += 1
            continue
        chunk = _as_chunk(item, default_file)
        if chunk is None:
            skipped += 1
            continue
        parsed.append(chunk)

    imported = storage.upsert_chunks(parsed)
    return imported, skipped
