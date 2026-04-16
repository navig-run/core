"""
navig.tools.memory — In-process key/value context store with similarity search.

Provides a lightweight session-scoped memory store that persists for the
lifetime of the running process (or optionally on disk).  Tools and agents
can stash arbitrary text or structured data under named keys and retrieve it
later — including fuzzy retrieval by keyword overlap.

Registered as ``"memory_store"`` and ``"memory_fetch"`` in the default registry.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any

from navig.core.yaml_io import atomic_write_text as _atomic_write_text
from navig.tools.registry import BaseTool, ToolResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core in-process store
# ---------------------------------------------------------------------------


@dataclass
class MemoryEntry:
    key: str
    value: Any
    tags: list[str] = field(default_factory=list)
    stored_at: float = field(default_factory=time.time)
    access_count: int = 0


class MemoryStore:
    """Thread-safe in-process key/value store with basic TF-IDF similarity.

    Can optionally persist entries to a JSON file so they survive restarts.
    """

    def __init__(self, persist_path: Path | None = None) -> None:
        self._lock = RLock()
        self._store: dict[str, MemoryEntry] = {}
        self._persist_path = persist_path
        if persist_path and persist_path.exists():
            self._load()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def put(
        self,
        key: str,
        value: Any,
        *,
        tags: list[str] | None = None,
        overwrite: bool = True,
    ) -> bool:
        """Store *value* under *key*.  Returns True if a new entry was created."""
        with self._lock:
            exists = key in self._store
            if exists and not overwrite:
                return False
            self._store[key] = MemoryEntry(
                key=key,
                value=value,
                tags=tags or [],
            )
            if self._persist_path:
                self._save()
            return not exists

    def get(self, key: str) -> Any | None:
        """Return the value stored under *key*, or None if not found."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            entry.access_count += 1
            return entry.value

    def delete(self, key: str) -> bool:
        """Remove *key* from the store.  Returns True if it existed."""
        with self._lock:
            if key not in self._store:
                return False
            del self._store[key]
            if self._persist_path:
                self._save()
            return True

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._store.keys())

    def list_entries(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "key": e.key,
                    "tags": e.tags,
                    "stored_at": e.stored_at,
                    "access_count": e.access_count,
                    "type": type(e.value).__name__,
                }
                for e in self._store.values()
            ]

    def clear(self) -> int:
        with self._lock:
            n = len(self._store)
            self._store.clear()
            if self._persist_path:
                self._save()
            return n

    # ------------------------------------------------------------------
    # Similarity search (keyword overlap / TF-IDF lite)
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        tags: list[str] | None = None,
    ) -> list[tuple[str, Any, float]]:
        """Return up to *top_k* entries most similar to *query*.

        Similarity is computed as cosine similarity of term-frequency vectors
        over whitespace-tokenised strings (no external dependencies).

        Returns list of (key, value, score) sorted descending by score.
        """
        query_tokens = _tokenise(str(query))
        if not query_tokens:
            return []

        with self._lock:
            results: list[tuple[str, Any, float]] = []
            for entry in self._store.values():
                if tags and not any(t in entry.tags for t in tags):
                    continue
                text = _entry_to_text(entry)
                score = _cosine(query_tokens, _tokenise(text))
                if score > 0:
                    results.append((entry.key, entry.value, score))

        results.sort(key=lambda x: x[2], reverse=True)
        return results[:top_k]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save(self) -> None:  # called under lock
        try:
            data = {
                k: {
                    "value": e.value,
                    "tags": e.tags,
                    "stored_at": e.stored_at,
                    "access_count": e.access_count,
                }
                for k, e in self._store.items()
            }
            _atomic_write_text(self._persist_path, json.dumps(data, indent=2, default=str))  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning("MemoryStore: failed to persist: %s", exc)

    def _load(self) -> None:
        try:
            data = json.loads(self._persist_path.read_text())  # type: ignore[union-attr]
            for k, v in data.items():
                self._store[k] = MemoryEntry(
                    key=k,
                    value=v["value"],
                    tags=v.get("tags", []),
                    stored_at=v.get("stored_at", time.time()),
                    access_count=v.get("access_count", 0),
                )
        except Exception as exc:
            logger.warning("MemoryStore: failed to load: %s", exc)


# ---------------------------------------------------------------------------
# Module-level default store (shared by both tools unless overridden)
# ---------------------------------------------------------------------------
_default_store = MemoryStore()


# ---------------------------------------------------------------------------
# TF-IDF helpers
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")


def _tokenise(text: str) -> dict[str, int]:
    tokens: dict[str, int] = defaultdict(int)
    for t in _TOKEN_RE.findall(text.lower()):
        tokens[t] += 1
    return dict(tokens)


def _cosine(a: dict[str, int], b: dict[str, int]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(a.get(k, 0) * b.get(k, 0) for k in a)
    mag_a = math.sqrt(sum(v * v for v in a.values()))
    mag_b = math.sqrt(sum(v * v for v in b.values()))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _entry_to_text(entry: MemoryEntry) -> str:
    parts = [entry.key]
    parts.extend(entry.tags)
    if isinstance(entry.value, str):
        parts.append(entry.value)
    elif isinstance(entry.value, dict):
        parts.append(json.dumps(entry.value))
    else:
        parts.append(str(entry.value))
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Tool: memory_store
# ---------------------------------------------------------------------------


class MemoryStoreTool(BaseTool):
    """Store a value in the session memory under a named key.

    Args schema (dict)
    ------------------
    key       : str   — Unique identifier for the stored value
    value     : any   — Value to store (str, dict, list, etc.)
    tags      : list  — Optional list of tag strings for grouping
    overwrite : bool  — If False, skip if key already exists (default True)
    """

    name = "memory_store"
    description = (
        "Store a value in the in-process memory under a unique key. "
        "Use tags to group related entries for later retrieval."
    )
    parameters = [
        {
            "name": "key",
            "type": "string",
            "description": "Unique identifier for the stored value",
            "required": True,
        },
        {
            "name": "value",
            "type": "any",
            "description": "Value to store (str, dict, list, etc.)",
            "required": True,
        },
        {
            "name": "tags",
            "type": "string[]",
            "description": "Optional list of tag strings for grouping",
            "required": False,
        },
        {
            "name": "overwrite",
            "type": "boolean",
            "description": "If False, skip if key already exists (default True)",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: Callable[[str], None] | None = None,
    ) -> ToolResult:
        t0 = time.monotonic()
        key = args.get("key", "").strip()
        if not key:
            return ToolResult(
                name=self.name,
                success=False,
                output=None,
                error="'key' arg is required",
                elapsed_ms=0.0,
                status_events=[],
            )
        value = args.get("value")
        tags: list[str] = args.get("tags") or []
        overwrite = bool(args.get("overwrite", True))

        store: MemoryStore = args.get("_store") or _default_store
        is_new = store.put(key, value, tags=tags, overwrite=overwrite)

        verb = "stored" if is_new else ("updated" if overwrite else "skipped (exists)")
        return ToolResult(
            name=self.name,
            success=True,
            output={"key": key, "action": verb},
            error=None,
            elapsed_ms=(time.monotonic() - t0) * 1000,
            status_events=[verb],
        )


# ---------------------------------------------------------------------------
# Tool: memory_fetch
# ---------------------------------------------------------------------------


class MemoryFetchTool(BaseTool):
    """Retrieve or search session memory.

    Args schema (dict)
    ------------------
    key    : str   — Exact key to fetch (returns value or null if not found)
    query  : str   — Free-text search across all stored entries (if key absent)
    top_k  : int   — Max results for search mode (default 5)
    tags   : list  — Filter search results to entries with any of these tags
    list   : bool  — If True, return metadata for all entries
    """

    name = "memory_fetch"
    description = (
        "Retrieve a stored value by exact key, or search memory by keyword. "
        "Can also list all stored entry metadata."
    )
    parameters = [
        {
            "name": "key",
            "type": "string",
            "description": "Exact key to fetch (returns value or null if not found)",
            "required": False,
        },
        {
            "name": "query",
            "type": "string",
            "description": "Free-text search across all stored entries (if key absent)",
            "required": False,
        },
        {
            "name": "top_k",
            "type": "number",
            "description": "Max results for search mode (default 5)",
            "required": False,
        },
        {
            "name": "tags",
            "type": "string[]",
            "description": "Filter search results to entries with any of these tags",
            "required": False,
        },
        {
            "name": "list",
            "type": "boolean",
            "description": "If True, return metadata for all entries",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: Callable[[str], None] | None = None,
    ) -> ToolResult:
        t0 = time.monotonic()
        store: MemoryStore = args.get("_store") or _default_store

        # List mode
        if args.get("list"):
            entries = store.list_entries()
            return ToolResult(
                name=self.name,
                success=True,
                output=entries,
                error=None,
                elapsed_ms=(time.monotonic() - t0) * 1000,
                status_events=[f"listed {len(entries)} entries"],
            )

        # Exact key lookup
        key = args.get("key", "").strip()
        if key:
            value = store.get(key)
            found = value is not None
            return ToolResult(
                name=self.name,
                success=True,
                output={"key": key, "value": value, "found": found},
                error=None,
                elapsed_ms=(time.monotonic() - t0) * 1000,
                status_events=["found" if found else "not found"],
            )

        # Similarity search
        query = args.get("query", "").strip()
        if query:
            top_k = int(args.get("top_k", 5))
            tags: list[str] | None = args.get("tags")  # type: ignore[assignment]
            hits = store.search(query, top_k=top_k, tags=tags)
            results = [{"key": k, "value": v, "score": round(s, 4)} for k, v, s in hits]
            return ToolResult(
                name=self.name,
                success=True,
                output=results,
                error=None,
                elapsed_ms=(time.monotonic() - t0) * 1000,
                status_events=[f"search returned {len(results)} results"],
            )

        return ToolResult(
            name=self.name,
            success=False,
            output=None,
            error="Provide 'key', 'query', or set 'list=True'",
            elapsed_ms=(time.monotonic() - t0) * 1000,
            status_events=[],
        )
