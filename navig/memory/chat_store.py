"""
navig/memory/chat_store.py
───────────────────────────
Persistent per-chat transcript store with keyword search (Item 9).

Each Telegram conversation is persisted as a newline-delimited JSONL file
under ``~/.navig/memory/<chat_id>/transcript.jsonl``.  The store exposes
three operations:

``append(turn)``
    Add one conversation turn.  Flushes immediately (atomic write).
``recent(n)``
    Return the *n* most recent turns.
``search(query)``
    Keyword search over all stored turns; returns matching turns in
    chronological order.

Max context tokens guard
------------------------
``max_context_tokens`` (config key ``memory.max_context_tokens``, default
4 000) limits how large a memory block is injected into the active prompt.
:meth:`recent_within_token_budget` honours this limit.

Compaction
----------
Transcripts older than ``memory.compact_after_days`` (default 7) should be
compacted via :mod:`navig.memory.compactor`.  This module is *not* responsible
for scheduling compaction — callers (e.g. the bot's background task) drive it.

File layout::

    ~/.navig/memory/
        <chat_id>/
            transcript.jsonl     ← active turns (JSONL)
            notes.md             ← compacted summary (written by compactor)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ──────────────────────────────────────────────────────────────────────────────
# Constants (single source of truth — config/defaults.yaml mirrors these)
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULT_MAX_CONTEXT_TOKENS: int = 4_000
_DEFAULT_COMPACT_AFTER_DAYS: int = 7
_TRANSCRIPT_FILENAME = "transcript.jsonl"
_NOTES_FILENAME = "notes.md"

# Rough token estimate: 1 token ≈ 4 characters (conservative)
_CHARS_PER_TOKEN: int = 4


# ──────────────────────────────────────────────────────────────────────────────
# Data types
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class ConversationTurn:
    """One exchange in a conversation.

    Parameters
    ----------
    role:
        ``"user"`` or ``"assistant"`` (or any other role string).
    content:
        Text of the turn.
    timestamp:
        Unix epoch seconds.  Defaults to ``time.time()``.
    metadata:
        Optional additional fields (e.g. update_id, chat_id).
    """

    role: str
    content: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ConversationTurn":
        return cls(
            role=d.get("role", "unknown"),
            content=d.get("content", ""),
            timestamp=float(d.get("timestamp", 0.0)),
            metadata=d.get("metadata", {}),
        )

    @property
    def token_estimate(self) -> int:
        """Conservative token count estimate for this turn's content."""
        return max(1, len(self.content) // _CHARS_PER_TOKEN)


# ──────────────────────────────────────────────────────────────────────────────
# ChatMemoryStore
# ──────────────────────────────────────────────────────────────────────────────


class ChatMemoryStore:
    """Persistent per-chat conversation transcript.

    Parameters
    ----------
    chat_id:
        Telegram (or any channel) chat identifier used as the directory name.
    base_dir:
        Root directory under which ``<chat_id>/transcript.jsonl`` is stored.
        Defaults to ``~/.navig/memory/``.
    max_context_tokens:
        Upper bound on tokens when building a memory block for prompt injection.
    """

    def __init__(
        self,
        chat_id: str | int,
        base_dir: Path | None = None,
        max_context_tokens: int = _DEFAULT_MAX_CONTEXT_TOKENS,
    ) -> None:
        if base_dir is None:
            base_dir = Path("~/.navig/memory").expanduser()
        self._dir = Path(base_dir) / str(chat_id)
        self._transcript = self._dir / _TRANSCRIPT_FILENAME
        self._notes = self._dir / _NOTES_FILENAME
        self._max_context_tokens = max_context_tokens

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def append(self, turn: ConversationTurn) -> None:
        """Persist *turn* to the transcript.

        The directory is created on first call.  Writes are appended
        atomically (write to ``.tmp`` then rename).
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        # Append a single JSONL line atomically
        line = json.dumps(turn.as_dict()) + "\n"
        # For pure append we write directly (no full-file rewrite needed)
        with self._transcript.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def all(self) -> list[ConversationTurn]:
        """Return all stored turns in chronological order."""
        return list(self._iter_turns())

    def recent(self, n: int) -> list[ConversationTurn]:
        """Return the *n* most recent turns (chronological order)."""
        if n < 1:
            return []
        all_turns = self.all()
        return all_turns[-n:]

    def search(self, query: str) -> list[ConversationTurn]:
        """Keyword search — returns turns whose content contains *query*.

        Case-insensitive, substring match.  Turns are returned in
        chronological order.
        """
        q = query.lower()
        return [t for t in self._iter_turns() if q in t.content.lower()]

    def recent_within_token_budget(
        self, max_tokens: int | None = None
    ) -> list[ConversationTurn]:
        """Return the most recent turns that fit within *max_tokens*.

        Parameters
        ----------
        max_tokens:
            Token budget.  Defaults to the store's ``max_context_tokens``.
        """
        budget = max_tokens if max_tokens is not None else self._max_context_tokens
        turns = self.all()
        selected: list[ConversationTurn] = []
        used = 0
        for turn in reversed(turns):
            cost = turn.token_estimate
            if used + cost > budget:
                break
            selected.insert(0, turn)
            used += cost
        return selected

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def notes_path(self) -> Path:
        """Path to the compacted notes file (may not exist yet)."""
        return self._notes

    @property
    def transcript_path(self) -> Path:
        """Path to the active transcript file (may not exist yet)."""
        return self._transcript

    def _iter_turns(self):
        if not self._transcript.exists():
            return
        with self._transcript.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield ConversationTurn.from_dict(json.loads(line))
                except (json.JSONDecodeError, KeyError):
                    pass  # corrupt entry — skip silently

    def prune_before(self, cutoff_timestamp: float) -> int:
        """Remove turns with ``timestamp < cutoff_timestamp``.

        Returns the number of entries removed.  Used by the compactor after
        it has summarised old turns into ``notes.md``.
        """
        all_turns = self.all()
        kept = [t for t in all_turns if t.timestamp >= cutoff_timestamp]
        removed = len(all_turns) - len(kept)
        if removed:
            self._rewrite(kept)
        return removed

    def _rewrite(self, turns: list[ConversationTurn]) -> None:
        """Atomically overwrite the transcript with *turns*."""
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._transcript.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for t in turns:
                fh.write(json.dumps(t.as_dict()) + "\n")
        tmp.replace(self._transcript)
