"""
Fact Retriever — Retrieves and ranks relevant key facts for context injection.

Given a user query / conversation context, returns the most relevant
facts formatted for LLM context window injection.

Ranking formula combines:
  - Semantic similarity (vector cosine)
  - Keyword relevance (FTS5 rank)
  - Recency (time decay)
  - Confidence (extraction confidence score)
  - Access frequency (boost well-used facts)

Output: formatted text block within a strict token budget.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from navig.memory.key_facts import KeyFact, KeyFactStore

logger = logging.getLogger("navig.memory.fact_retriever")


# ── Configuration ─────────────────────────────────────────────


@dataclass
class RetrievalConfig:
    """Controls retrieval behavior and ranking weights."""

    # Token budget for the key_facts context section
    max_tokens: int = 600
    # How many candidates to pre-fetch before ranking
    candidate_limit: int = 50
    # Minimum combined score to include a fact
    min_score: float = 0.15
    # Ranking weights (must sum to ~1.0)
    weight_semantic: float = 0.40
    weight_keyword: float = 0.20
    weight_recency: float = 0.15
    weight_confidence: float = 0.15
    weight_access: float = 0.10
    # Recency half-life in days (facts lose 50% recency score after this many days)
    recency_half_life_days: float = 30.0
    # Category boost: multiply score for these categories
    category_boosts: dict[str, float] = field(
        default_factory=lambda: {
            "identity": 1.3,
            "preference": 1.2,
            "decision": 1.1,
            "technical": 1.0,
            "context": 0.9,
        }
    )


DEFAULT_CONFIG = RetrievalConfig()


# ── Ranked Fact ───────────────────────────────────────────────


@dataclass
class RankedFact:
    """A fact with its computed relevance scores."""

    fact: KeyFact
    combined_score: float = 0.0
    semantic_score: float = 0.0
    keyword_score: float = 0.0
    recency_score: float = 0.0
    confidence_score: float = 0.0
    access_score: float = 0.0

    def __repr__(self) -> str:
        return f"<RankedFact score={self.combined_score:.3f} {self.fact.content[:50]}>"


# ── Retrieval Result ──────────────────────────────────────────


@dataclass
class FactRetrievalResult:
    """Complete retrieval result with formatted output."""

    facts: list[RankedFact] = field(default_factory=list)
    formatted: str = ""
    token_estimate: int = 0
    query: str = ""
    total_candidates: int = 0
    total_active: int = 0

    @property
    def count(self) -> int:
        return len(self.facts)


# ── Fact Retriever ────────────────────────────────────────────


class FactRetriever:
    """
    Retrieves and ranks key facts for context injection.

    Usage:
        retriever = FactRetriever(store)
        result = retriever.retrieve("What Python version should I use?")
        context_block = result.formatted  # inject into LLM prompt
    """

    def __init__(
        self,
        store: KeyFactStore,
        embedding_provider: Any | None = None,
        config: RetrievalConfig | None = None,
    ):
        self.store = store
        self.embedding_provider = embedding_provider or store.embedding_provider
        self.config = config or DEFAULT_CONFIG

    def retrieve(
        self,
        query: str,
        category: str | None = None,
        max_tokens: int | None = None,
        config_override: RetrievalConfig | None = None,
    ) -> FactRetrievalResult:
        """
        Retrieve ranked facts relevant to query.

        Args:
            query: User query or conversation context
            category: Optional category filter
            max_tokens: Override token budget
            config_override: Override full config

        Returns:
            FactRetrievalResult with ranked facts and formatted text
        """
        cfg = config_override or self.config
        budget = max_tokens or cfg.max_tokens

        # 1. Gather candidates
        candidates = self._gather_candidates(query, category, cfg)

        total_active = len(self.store.get_active(limit=10000))

        if not candidates:
            return FactRetrievalResult(
                query=query,
                total_active=total_active,
            )

        # 2. Score and rank
        ranked = self._rank(candidates, cfg)

        # 3. Budget-constrained selection
        selected = self._select_within_budget(ranked, budget)

        # 4. Record access
        if selected:
            self.store.record_access([rf.fact.id for rf in selected])

        # 5. Format output
        formatted, token_est = self._format(selected)

        return FactRetrievalResult(
            facts=selected,
            formatted=formatted,
            token_estimate=token_est,
            query=query,
            total_candidates=len(candidates),
            total_active=total_active,
        )

    def retrieve_all_active(
        self,
        max_tokens: int | None = None,
    ) -> FactRetrievalResult:
        """
        Retrieve all active facts (no query-based filtering).
        Useful for "what do you remember about me?" queries.
        """
        budget = (
            max_tokens or self.config.max_tokens * 2
        )  # Allow larger budget for "show all"
        facts = self.store.get_active(limit=200)

        if not facts:
            return FactRetrievalResult(formatted="No memories stored yet.")

        # Convert to ranked (use confidence as primary sort)
        ranked = [
            RankedFact(
                fact=f,
                combined_score=f.confidence,
                confidence_score=f.confidence,
                recency_score=self._recency_score(f.updated_at),
            )
            for f in facts
        ]
        ranked.sort(key=lambda x: x.combined_score, reverse=True)

        selected = self._select_within_budget(ranked, budget)
        formatted, token_est = self._format(selected, header="All stored memories:")

        return FactRetrievalResult(
            facts=selected,
            formatted=formatted,
            token_estimate=token_est,
            total_candidates=len(facts),
            total_active=len(facts),
        )

    # ── Private: Candidate Gathering ──────────────────────────

    def _gather_candidates(
        self,
        query: str,
        category: str | None,
        cfg: RetrievalConfig,
    ) -> list[tuple[KeyFact, float, float]]:
        """
        Gather candidate facts via keyword + vector search.
        Returns [(fact, keyword_score, vector_score), ...]
        """
        seen: dict[str, tuple[float, float]] = {}  # id -> (kw_score, vec_score)

        # Keyword search
        try:
            kw_results = self.store.search_keyword(query, limit=cfg.candidate_limit)
            for fact, rank in kw_results:
                if category and fact.category != category:
                    continue
                seen[fact.id] = (self._normalize_kw_rank(rank), 0.0)
        except Exception as exc:
            logger.debug("Keyword search failed: %s", exc)

        # Vector search (if embeddings available)
        if self.embedding_provider:
            try:
                query_emb = self.embedding_provider.embed_text(query)
                vec_results = self.store.search_vector(
                    query_emb,
                    limit=cfg.candidate_limit,
                    min_similarity=0.2,
                )
                for fact, sim in vec_results:
                    if category and fact.category != category:
                        continue
                    if fact.id in seen:
                        kw_score, _ = seen[fact.id]
                        seen[fact.id] = (kw_score, sim)
                    else:
                        seen[fact.id] = (0.0, sim)
            except Exception as exc:
                logger.debug("Vector search failed: %s", exc)

        # Also include high-confidence facts that might not match the query
        # (identity/preference facts are always relevant)
        always_relevant = self.store.get_active(
            limit=20,
            category="identity",
            min_confidence=0.7,
        )
        for fact in always_relevant:
            if fact.id not in seen:
                seen[fact.id] = (0.0, 0.0)

        # Reconstruct full fact objects
        candidates: list[tuple[KeyFact, float, float]] = []
        for fact_id, (kw_score, vec_score) in seen.items():
            fact = self.store.get(fact_id)
            if fact and fact.is_active:
                candidates.append((fact, kw_score, vec_score))

        return candidates

    # ── Private: Ranking ──────────────────────────────────────

    def _rank(
        self,
        candidates: list[tuple[KeyFact, float, float]],
        cfg: RetrievalConfig,
    ) -> list[RankedFact]:
        """Score and rank candidates."""
        ranked: list[RankedFact] = []

        # Compute max access count for normalization
        max_access = max((f.access_count for f, _, _ in candidates), default=1) or 1

        for fact, kw_score, vec_score in candidates:
            recency = self._recency_score(fact.updated_at, cfg.recency_half_life_days)
            confidence = fact.confidence
            access = fact.access_count / max_access if max_access > 0 else 0.0

            # Weighted combination
            combined = (
                cfg.weight_semantic * vec_score
                + cfg.weight_keyword * kw_score
                + cfg.weight_recency * recency
                + cfg.weight_confidence * confidence
                + cfg.weight_access * access
            )

            # Category boost
            boost = cfg.category_boosts.get(fact.category, 1.0)
            combined *= boost

            if combined >= cfg.min_score:
                ranked.append(
                    RankedFact(
                        fact=fact,
                        combined_score=combined,
                        semantic_score=vec_score,
                        keyword_score=kw_score,
                        recency_score=recency,
                        confidence_score=confidence,
                        access_score=access,
                    )
                )

        ranked.sort(key=lambda x: x.combined_score, reverse=True)
        return ranked

    # ── Private: Budget Selection ─────────────────────────────

    def _select_within_budget(
        self,
        ranked: list[RankedFact],
        max_tokens: int,
    ) -> list[RankedFact]:
        """Select top-ranked facts that fit within the token budget."""
        selected: list[RankedFact] = []
        used_tokens = 0
        overhead = 30  # Header/footer tokens (~"## Key Memories\n" etc.)

        for rf in ranked:
            fact_tokens = rf.fact.token_count + 5  # bullet + newline overhead
            if used_tokens + fact_tokens + overhead > max_tokens:
                # Can we fit at least a truncated version?
                if used_tokens + overhead + 20 <= max_tokens:
                    # Try truncating content
                    remaining = max_tokens - used_tokens - overhead - 5
                    if remaining > 20:
                        truncated = rf.fact.content[
                            : remaining * 4
                        ]  # rough char estimate
                        rf.fact.content = truncated + "…"
                        selected.append(rf)
                break
            selected.append(rf)
            used_tokens += fact_tokens

        return selected

    # ── Private: Formatting ───────────────────────────────────

    def _format(
        self,
        ranked_facts: list[RankedFact],
        header: str = "Relevant memories about this user:",
    ) -> tuple[str, int]:
        """
        Format selected facts into a markdown block for LLM injection.

        Returns (formatted_text, token_estimate).
        """
        if not ranked_facts:
            return "", 0

        lines = [f"## {header}"]
        for rf in ranked_facts:
            tag_str = ""
            if rf.fact.tags:
                visible_tags = [t for t in rf.fact.tags if t != rf.fact.category][:3]
                if visible_tags:
                    tag_str = f" [{', '.join(visible_tags)}]"
            lines.append(f"- {rf.fact.content}{tag_str}")

        text = "\n".join(lines)
        tokens = max(1, len(text) // 4)
        return text, tokens

    # ── Private: Score Helpers ────────────────────────────────

    @staticmethod
    def _recency_score(updated_at: str, half_life_days: float = 30.0) -> float:
        """
        Exponential decay based on age.
        Score = 0.5^(age_days / half_life_days)
        """
        try:
            ts = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0
            return math.pow(0.5, age / half_life_days)
        except (ValueError, TypeError):
            return 0.5  # Unknown age → neutral score

    @staticmethod
    def _normalize_kw_rank(rank: float) -> float:
        """Normalize FTS5 rank (negative, lower = better) to 0-1 score."""
        # FTS5 rank is typically negative; closer to 0 = better match
        # Rough heuristic: map -25..0 → 0..1
        return max(0.0, min(1.0, 1.0 - abs(rank) / 25.0))
