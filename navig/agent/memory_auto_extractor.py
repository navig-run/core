"""
navig.agent.memory_auto_extractor — Automatic key-fact extraction from conversations.

Records user/assistant turns and triggers extraction every N assistant turns,
accumulating a batch for richer context.  Delegates to the existing
:mod:`navig.memory.fact_extractor` infrastructure.

Design principles:
  - **Silent**: Extraction errors never interrupt the conversation.
  - **Non-blocking**: ``maybe_extract`` is async; callers can fire-and-forget.
  - **Configurable**: interval, model, max facts, confidence threshold.
  - **Composable**: Works with any ``KeyFactStore`` and optional LLM callback.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("navig.agent.memory_auto_extractor")


# ── Constants ─────────────────────────────────────────────────

MEMORY_EXTRACTION_INTERVAL = 5
"""Default: extract every 5 assistant turns."""

MAX_FACTS_PER_EXTRACTION = 3
"""Maximum facts stored per extraction cycle."""

MIN_CONFIDENCE = 0.6
"""Minimum confidence for a fact to be persisted."""

MAX_TURN_CONTENT_CHARS = 2000
"""Truncate individual turn content to this length."""

MAX_PENDING_TURNS = 20
"""Safety cap — keep at most the last 20 turns in the buffer."""

CATEGORIES = [
    "preferences",
    "environment",
    "project",
    "relationships",
    "procedures",
]
"""Canonical fact categories for the extraction prompt."""

EXTRACTION_PROMPT = """\
You are a memory extraction agent. Given the last few conversation turns,
extract 0-{max_facts} key facts worth remembering for future sessions.

Rules:
- Only extract DURABLE facts (valid beyond this session)
- Skip ephemeral info (current task status, temp file paths)
- Each fact: one sentence, specific, actionable
- Assign a category: {categories}
- If nothing worth remembering, return empty list

Recent conversation:
{turns}

Return JSON array:
[{{"fact": "...", "category": "...", "confidence": 0.0-1.0}}]
Return [] if nothing worth remembering."""


# ── Data ──────────────────────────────────────────────────────


@dataclass
class ExtractedFact:
    """A single fact extracted by the auto-extractor."""

    fact: str
    category: str = "environment"
    confidence: float = 0.7

    def to_dict(self) -> dict[str, Any]:
        return {"fact": self.fact, "category": self.category, "confidence": self.confidence}


@dataclass
class _Turn:
    """Internal representation of one conversation turn."""

    role: str
    content: str


@dataclass
class ExtractionConfig:
    """Configuration for the auto-extraction scheduler."""

    interval: int = MEMORY_EXTRACTION_INTERVAL
    max_facts: int = MAX_FACTS_PER_EXTRACTION
    min_confidence: float = MIN_CONFIDENCE
    model: str = ""
    enabled: bool = True

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> ExtractionConfig:
        if not d:
            return cls()
        return cls(
            interval=int(d.get("extraction_interval", MEMORY_EXTRACTION_INTERVAL)),
            max_facts=int(d.get("max_facts_per_extraction", MAX_FACTS_PER_EXTRACTION)),
            min_confidence=float(d.get("min_confidence", MIN_CONFIDENCE)),
            model=str(d.get("extraction_model", "")),
            enabled=bool(d.get("enabled", True)),
        )


# ── Core Class ────────────────────────────────────────────────


class MemoryAutoExtractor:
    """Auto-extract key facts from conversation turns at configurable intervals.

    Parameters
    ----------
    store : object | None
        Any object with an ``upsert(fact)`` method (typically ``KeyFactStore``).
        If *None*, facts are extracted but not persisted — useful for dry-run /
        testing.
    llm_call : callable | None
        Async callable ``(prompt: str, **kw) -> str``.  If *None*, the
        extractor parses nothing (extraction always returns ``[]``).
    config : dict | ExtractionConfig | None
        Dict parsed via :class:`ExtractionConfig.from_dict`, or a pre-built
        config object.

    Usage
    -----
    >>> ext = MemoryAutoExtractor(store=my_store, llm_call=my_llm)
    >>> ext.record_turn("user", "The production DB is on port 5433")
    >>> ext.record_turn("assistant", "Got it, I'll use port 5433")
    >>> facts = await ext.maybe_extract()  # triggers every N assistant turns
    """

    def __init__(
        self,
        store: Any = None,
        llm_call: Callable[..., Any] | None = None,
        config: dict[str, Any] | ExtractionConfig | None = None,
    ) -> None:
        if isinstance(config, ExtractionConfig):
            self._config = config
        else:
            self._config = ExtractionConfig.from_dict(config)  # type: ignore[arg-type]

        self._store = store
        self._llm_call = llm_call
        self._turn_count: int = 0
        self._pending_turns: list[_Turn] = []
        self._total_extracted: int = 0

    # ── Properties ────────────────────────────────────────────

    @property
    def config(self) -> ExtractionConfig:
        return self._config

    @property
    def turn_count(self) -> int:
        """Number of assistant turns since last extraction (or start)."""
        return self._turn_count

    @property
    def pending_turns(self) -> int:
        """Number of turns waiting in the buffer."""
        return len(self._pending_turns)

    @property
    def total_extracted(self) -> int:
        """Cumulative facts extracted across all cycles."""
        return self._total_extracted

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    # ── Turn Recording ────────────────────────────────────────

    def record_turn(self, role: str, content: str) -> None:
        """Record a conversation turn for future extraction.

        Only ``"user"`` and ``"assistant"`` roles are accepted; others are
        silently ignored.  Content is truncated to
        :data:`MAX_TURN_CONTENT_CHARS`.
        """
        if role not in ("user", "assistant"):
            return
        truncated = (content or "")[:MAX_TURN_CONTENT_CHARS]
        self._pending_turns.append(_Turn(role=role, content=truncated))

        if role == "assistant":
            self._turn_count += 1

        # Safety cap: keep only the most recent turns
        if len(self._pending_turns) > MAX_PENDING_TURNS:
            self._pending_turns = self._pending_turns[-MAX_PENDING_TURNS:]

    def clear(self) -> None:
        """Reset turn counter and pending buffer."""
        self._turn_count = 0
        self._pending_turns.clear()

    # ── Extraction ────────────────────────────────────────────

    async def maybe_extract(self) -> list[ExtractedFact]:
        """Extract facts if the interval threshold has been reached.

        Returns the list of stored facts (may be empty even if extraction
        was attempted).  Resets the turn counter and buffer on every call
        that meets the threshold — even if extraction itself produces no
        facts.
        """
        if not self._config.enabled:
            return []

        if self._turn_count < self._config.interval:
            return []

        if not self._pending_turns:
            self._turn_count = 0
            return []

        try:
            facts = await self._extract_from_batch()
            return facts
        except Exception:
            logger.debug("Memory auto-extraction failed", exc_info=True)
            return []
        finally:
            # Always reset after an attempt
            self._turn_count = 0
            self._pending_turns.clear()

    async def force_extract(self) -> list[ExtractedFact]:
        """Force an extraction cycle regardless of turn count.

        Useful for session-end flush or explicit ``/remember`` commands.
        """
        if not self._pending_turns:
            return []

        try:
            facts = await self._extract_from_batch()
            return facts
        except Exception:
            logger.debug("Forced memory extraction failed", exc_info=True)
            return []
        finally:
            self._turn_count = 0
            self._pending_turns.clear()

    # ── Internal ──────────────────────────────────────────────

    async def _extract_from_batch(self) -> list[ExtractedFact]:
        """Build prompt from pending turns, call LLM, parse, filter, store."""
        if not self._llm_call:
            return []

        # Build turns text (last 10 for prompt size)
        recent = self._pending_turns[-10:]
        turns_text = "\n".join(f"[{t.role}]: {t.content}" for t in recent)

        prompt = EXTRACTION_PROMPT.format(
            max_facts=self._config.max_facts,
            categories=", ".join(CATEGORIES),
            turns=turns_text,
        )

        # Call LLM
        kwargs: dict[str, Any] = {}
        if self._config.model:
            kwargs["model"] = self._config.model

        response = await self._llm_call(prompt, **kwargs)

        # Parse
        raw_text = str(response or "").strip()
        parsed = parse_extraction_response(raw_text)

        # Filter by confidence and limit
        filtered = [
            f for f in parsed if f.confidence >= self._config.min_confidence
        ][: self._config.max_facts]

        # Store
        stored = self._store_facts(filtered)
        self._total_extracted += len(stored)

        if stored:
            logger.debug(
                "Auto-extracted %d fact(s): %s",
                len(stored),
                [f.fact[:50] for f in stored],
            )

        return stored

    def _store_facts(self, facts: list[ExtractedFact]) -> list[ExtractedFact]:
        """Persist facts to the store.  Returns only successfully stored ones."""
        if not self._store or not facts:
            return facts  # no store → treat as "stored" for return value

        stored: list[ExtractedFact] = []
        for fact in facts:
            try:
                key = fact_key(fact.fact, fact.category)
                # Build a KeyFact-compatible object if store expects one.
                # We use duck-typing: if store has ``put(key, value, **kw)``
                # use that; otherwise fall back to ``upsert()`` with a dict.
                if hasattr(self._store, "put"):
                    self._store.put(
                        key,
                        fact.fact,
                        metadata={
                            "category": fact.category,
                            "confidence": fact.confidence,
                            "auto_extracted": True,
                        },
                    )
                elif hasattr(self._store, "upsert"):
                    self._store.upsert(fact.to_dict())
                stored.append(fact)
            except Exception:
                logger.debug("Failed to store fact: %s", fact.fact[:60], exc_info=True)

        return stored


# ── Parsing Helpers ───────────────────────────────────────────


def parse_extraction_response(text: str) -> list[ExtractedFact]:
    """Parse a JSON array of facts from the LLM response.

    Handles markdown code fences and various malformed outputs gracefully.
    Returns an empty list on any parse error.
    """
    if not text:
        return []

    # Strip markdown code fences
    cleaned = text.strip()
    if "```" in cleaned:
        parts = cleaned.split("```")
        # Take the content inside the first fence
        if len(parts) >= 2:
            inner = parts[1]
            # Strip optional language tag
            if inner.startswith("json"):
                inner = inner[4:]
            cleaned = inner.strip()

    # Try to find a JSON array
    match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if not match:
        return []

    try:
        items = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return []

    if not isinstance(items, list):
        return []

    facts: list[ExtractedFact] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        fact_text = str(item.get("fact", "")).strip()
        if not fact_text or len(fact_text) < 5:
            continue

        category = str(item.get("category", "environment")).strip().lower()
        if category not in CATEGORIES:
            category = "environment"

        confidence = item.get("confidence", 0.7)
        if not isinstance(confidence, (int, float)):
            try:
                confidence = float(confidence)
            except (TypeError, ValueError):
                confidence = 0.7
        confidence = max(0.0, min(1.0, float(confidence)))

        facts.append(
            ExtractedFact(fact=fact_text, category=category, confidence=confidence)
        )

    return facts


def fact_key(fact_text: str, category: str = "environment") -> str:
    """Generate a storage key from fact text and category.

    Format: ``category/slug`` where slug is the first 4 alphanumeric
    words joined by underscores.

    >>> fact_key("Production DB is on port 5433", "environment")
    'environment/production_db_is_on'
    """
    words = re.findall(r"[a-z0-9]+", fact_text.lower())[:4]
    slug = "_".join(words) if words else "unknown"
    safe_cat = re.sub(r"[^a-z0-9_]", "", category.lower()) or "general"
    return f"{safe_cat}/{slug}"
