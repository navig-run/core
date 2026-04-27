"""
Tests for navig.memory.fact_retriever — RetrievalConfig, RankedFact, FactRetrievalResult.
"""

from __future__ import annotations

import pytest

from navig.memory.fact_retriever import (
    DEFAULT_CONFIG,
    FactRetrievalResult,
    RankedFact,
    RetrievalConfig,
)


# ─── RetrievalConfig ──────────────────────────────────────────────────────────


def test_retrieval_config_defaults():
    cfg = RetrievalConfig()
    assert cfg.max_tokens == 600
    assert cfg.candidate_limit == 50
    assert cfg.min_score == 0.15
    assert cfg.weight_semantic == 0.40
    assert cfg.weight_keyword == 0.20
    assert cfg.weight_recency == 0.15
    assert cfg.weight_confidence == 0.15
    assert cfg.weight_access == 0.10
    assert cfg.recency_half_life_days == 30.0


def test_retrieval_config_weights_sum():
    cfg = RetrievalConfig()
    total = cfg.weight_semantic + cfg.weight_keyword + cfg.weight_recency + cfg.weight_confidence + cfg.weight_access
    assert abs(total - 1.0) < 1e-9


def test_retrieval_config_category_boosts_defaults():
    cfg = RetrievalConfig()
    assert cfg.category_boosts["identity"] == 1.3
    assert cfg.category_boosts["preference"] == 1.2
    assert cfg.category_boosts["decision"] == 1.1
    assert cfg.category_boosts["technical"] == 1.0
    assert cfg.category_boosts["context"] == 0.9


def test_retrieval_config_custom():
    cfg = RetrievalConfig(max_tokens=1200, min_score=0.3, candidate_limit=20)
    assert cfg.max_tokens == 1200
    assert cfg.min_score == 0.3
    assert cfg.candidate_limit == 20


def test_default_config_is_retrieval_config():
    assert isinstance(DEFAULT_CONFIG, RetrievalConfig)


# ─── RankedFact ───────────────────────────────────────────────────────────────


def test_ranked_fact_defaults():
    from navig.memory.key_facts import KeyFact
    kf = KeyFact(content="User prefers Python", category="preference", confidence=0.8)
    rf = RankedFact(fact=kf)
    assert rf.combined_score == 0.0
    assert rf.semantic_score == 0.0
    assert rf.keyword_score == 0.0
    assert rf.recency_score == 0.0
    assert rf.confidence_score == 0.0
    assert rf.access_score == 0.0


def test_ranked_fact_with_scores():
    from navig.memory.key_facts import KeyFact
    kf = KeyFact(content="test", category="technical", confidence=0.9)
    rf = RankedFact(
        fact=kf,
        combined_score=0.75,
        semantic_score=0.8,
        keyword_score=0.6,
    )
    assert rf.combined_score == 0.75
    assert rf.semantic_score == 0.8


def test_ranked_fact_repr():
    from navig.memory.key_facts import KeyFact
    kf = KeyFact(content="User likes Docker", category="preference", confidence=0.7)
    rf = RankedFact(fact=kf, combined_score=0.65)
    r = repr(rf)
    assert "0.650" in r
    assert "User" in r


# ─── FactRetrievalResult ──────────────────────────────────────────────────────


def test_fact_retrieval_result_defaults():
    r = FactRetrievalResult()
    assert r.facts == []
    assert r.formatted == ""
    assert r.token_estimate == 0
    assert r.query == ""
    assert r.total_candidates == 0
    assert r.total_active == 0


def test_fact_retrieval_result_count_empty():
    r = FactRetrievalResult()
    assert r.count == 0


def test_fact_retrieval_result_count():
    from navig.memory.key_facts import KeyFact
    rf = RankedFact(fact=KeyFact(content="x", category="technical", confidence=0.5))
    r = FactRetrievalResult(facts=[rf, rf])
    assert r.count == 2


def test_fact_retrieval_result_with_data():
    r = FactRetrievalResult(
        formatted="## Key Facts\n- Python preference",
        token_estimate=30,
        query="preferred language",
        total_candidates=10,
        total_active=5,
    )
    assert "Python" in r.formatted
    assert r.token_estimate == 30
    assert r.total_active == 5
