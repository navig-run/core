"""navig.agent.memory_proposer — turn frequent command patterns into memory proposals.

The agent should not just learn from what you *say* (conversational fact extraction)
but from what you *do*. This wires the otherwise-dormant
:class:`~navig.agent.pattern_analyzer.PatternAnalyzer` as a second memory proposer:
it mines the command pattern log for repeated sequences and proposes them as
**pending** key facts (e.g. "Frequently runs: git pull → docker compose up").

Proposals are pending (``approved=None``) — they surface in ``navig memory pending``
/ the review surface and are never injected into prompts until the user approves
them. This keeps curation = trust.

Entry point: ``navig memory learn`` (see navig/commands/memory.py).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _render_sequence(seq: Any) -> str:
    """Render a pattern sequence to a readable one-liner."""
    if isinstance(seq, (list, tuple)):
        parts = [str(s).strip() for s in seq if str(s).strip()]
        return " → ".join(parts)
    return str(seq).strip()


def propose_from_command_patterns(
    store: Any | None = None,
    *,
    min_occurrences: int = 3,
    limit: int = 10,
    record_limit: int = 500,
) -> list[Any]:
    """Mine the command pattern log and upsert frequent sequences as PENDING facts.

    Returns the list of proposed :class:`KeyFact` objects (already persisted as
    pending). Best-effort: returns ``[]`` if the pattern-log pipeline or store is
    unavailable. Idempotent on content — ``upsert`` dedups, so re-running won't
    create duplicates (it bumps confidence on the existing proposal).
    """
    try:
        from navig.agent.pattern_analyzer import PatternAnalyzer
        from navig.agent.pattern_observer import DEFAULT_DB_PATH, PatternObserver
        from navig.memory.key_facts import KeyFact, KeyFactStore
    except ImportError as exc:
        logger.debug("pattern proposer unavailable: %s", exc)
        return []

    try:
        records = PatternObserver(DEFAULT_DB_PATH).get_recent(limit=record_limit)
    except Exception as exc:  # noqa: BLE001
        logger.debug("pattern log read failed: %s", exc)
        return []
    if not records:
        return []

    scored = PatternAnalyzer(min_occurrences=min_occurrences, max_results=limit).score_by_frequency(
        records
    )
    if not scored:
        return []

    store = store or KeyFactStore()
    proposed: list[Any] = []
    for sp in scored:
        seq_text = _render_sequence(getattr(sp, "sequence", sp))
        if not seq_text:
            continue
        occurrences = getattr(sp, "occurrences", 0)
        content = f"Frequently runs: {seq_text}"
        try:
            fact = KeyFact(
                content=content,
                category="technical",
                confidence=0.7,
                source_platform="pattern",
                tags=["workflow", "command_pattern"],
                metadata={"occurrences": occurrences, "proposed_by": "pattern_analyzer"},
                approved=None,  # pending — user curates before retrieval uses it
            )
            store.upsert(fact)
            proposed.append(fact)
        except Exception as exc:  # noqa: BLE001
            logger.debug("failed to propose pattern fact: %s", exc)
    logger.info("pattern proposer: %d command-pattern proposal(s) written", len(proposed))
    return proposed
