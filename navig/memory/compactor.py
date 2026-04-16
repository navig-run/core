"""
navig/memory/compactor.py
──────────────────────────
Transcript compaction for per-chat memory (Item 9).

The compactor scans a ``ChatMemoryStore``, identifies turns older than
``compact_after_days``, summarises them into ``notes.md``, and prunes the
raw entries from the transcript.

Summary strategy
----------------
The default ``KeywordCompactor`` produces a simple bullet-list summary
(no LLM dependency).  A ``LLMCompactor`` hook is defined so callers can
inject a richer summariser when the AI provider is available.

Scheduling
----------
Compaction is triggered externally (e.g. on bot startup or via a background
task).  This module is stateless and side-effect-free except for file writes.

Usage::

    from navig.memory.chat_store import ChatMemoryStore
    from navig.memory.compactor import KeywordCompactor

    store = ChatMemoryStore("42")
    compactor = KeywordCompactor(compact_after_days=7)
    result = compactor.compact(store)
    print(result.turns_removed, "turns compacted")
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from navig.memory.chat_store import (
    ChatMemoryStore,
    ConversationTurn,
    _DEFAULT_COMPACT_AFTER_DAYS,
)

# ──────────────────────────────────────────────────────────────────────────────
# Result type
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class CompactionResult:
    """Summary of one compaction pass.

    Attributes
    ----------
    turns_removed:
        Number of old transcript entries pruned.
    notes_written:
        ``True`` if a notes file was written (or updated).
    """

    turns_removed: int = 0
    notes_written: bool = False


# ──────────────────────────────────────────────────────────────────────────────
# Summariser protocol
# ──────────────────────────────────────────────────────────────────────────────


class Summariser(Protocol):
    """Protocol for pluggable summarisation backends."""

    def summarise(self, turns: list[ConversationTurn]) -> str:
        """Return a Markdown summary string for *turns*."""
        ...  # pragma: no cover


# ──────────────────────────────────────────────────────────────────────────────
# Built-in keyword summariser (no LLM, no network)
# ──────────────────────────────────────────────────────────────────────────────


class KeywordSummariser:
    """Produce a simple bullet-list summary from raw turns.

    Extracts unique non-trivial words (≥ 4 chars) from user turns and formats
    them as a Markdown note block.  Safe for all environments.
    """

    _STOP_WORDS = frozenset(
        "this that with from have will what when where your your some also "
        "been into then them they were their there which while about after "
        "before could would should might more than like just very much make "
        "over have each both does done gone here need such".split()
    )
    _MIN_WORD_LEN = 4
    _MAX_WORDS = 30

    def summarise(self, turns: list[ConversationTurn]) -> str:
        if not turns:
            return "_No content to summarise._"
        # Collect user turn content
        user_content = " ".join(
            t.content for t in turns if t.role in ("user", "human")
        )
        words = user_content.lower().split()
        seen: dict[str, int] = {}
        for w in words:
            # Strip punctuation
            clean = w.strip(".,!?;:\"'()[]{}")
            if (
                len(clean) >= self._MIN_WORD_LEN
                and clean not in self._STOP_WORDS
                and clean.isalpha()
            ):
                seen[clean] = seen.get(clean, 0) + 1
        top = sorted(seen, key=lambda k: seen[k], reverse=True)[: self._MAX_WORDS]
        if not top:
            return "_Content too short to summarise._"
        start_ts = turns[0].timestamp
        end_ts = turns[-1].timestamp
        import datetime
        fmt = lambda ts: datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        header = f"**Summary** ({fmt(start_ts)} – {fmt(end_ts)}, {len(turns)} turns)"
        bullets = "\n".join(f"- {w}" for w in top)
        return f"{header}\n\n{bullets}"


# ──────────────────────────────────────────────────────────────────────────────
# Compactor
# ──────────────────────────────────────────────────────────────────────────────


class KeywordCompactor:
    """Compact old transcript turns into ``notes.md``.

    Parameters
    ----------
    compact_after_days:
        Turns older than this (measured from *now*) are eligible for
        compaction.  Defaults to ``_DEFAULT_COMPACT_AFTER_DAYS`` (7).
    summariser:
        Pluggable summariser.  Defaults to :class:`KeywordSummariser`.
    """

    def __init__(
        self,
        compact_after_days: int = _DEFAULT_COMPACT_AFTER_DAYS,
        summariser: Summariser | None = None,
    ) -> None:
        if compact_after_days < 1:
            raise ValueError(
                f"compact_after_days must be ≥ 1; got {compact_after_days}"
            )
        self._days = compact_after_days
        self._summariser: Summariser = summariser or KeywordSummariser()

    def compact(self, store: ChatMemoryStore) -> CompactionResult:
        """Run a compaction pass on *store*.

        Parameters
        ----------
        store:
            The :class:`~navig.memory.chat_store.ChatMemoryStore` to compact.

        Returns
        -------
        CompactionResult
        """
        result = CompactionResult()
        cutoff = time.time() - self._days * 86_400
        all_turns = store.all()
        old_turns = [t for t in all_turns if t.timestamp < cutoff]
        if not old_turns:
            return result

        # Summarise old turns
        summary = self._summariser.summarise(old_turns)

        # Append summary to notes.md (create or append)
        notes_path = store.notes_path
        notes_path.parent.mkdir(parents=True, exist_ok=True)
        separator = "\n\n---\n\n"
        if notes_path.exists():
            existing = notes_path.read_text(encoding="utf-8")
            notes_path.write_text(existing + separator + summary, encoding="utf-8")
        else:
            notes_path.write_text(summary, encoding="utf-8")
        result.notes_written = True

        # Prune old turns from the transcript
        result.turns_removed = store.prune_before(cutoff)
        return result
