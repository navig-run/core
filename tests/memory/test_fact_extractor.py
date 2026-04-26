"""
Tests for navig.memory.fact_extractor — ExtractionResult and extract_rules().
"""

from __future__ import annotations

import pytest

from navig.memory.fact_extractor import ExtractionResult, extract_rules


# ─── ExtractionResult ─────────────────────────────────────────────────────────


def test_extraction_result_defaults():
    r = ExtractionResult()
    assert r.facts == []
    assert r.source_turn == ""
    assert r.method == "rule"
    assert r.raw_llm_response == ""


def test_extraction_result_count():
    r = ExtractionResult()
    assert r.count == 0


def test_extraction_result_count_with_facts():
    from navig.memory.key_facts import KeyFact

    r = ExtractionResult(
        facts=[
            KeyFact(content="Python", category="technical", confidence=0.8),
            KeyFact(content="Docker", category="technical", confidence=0.9),
        ]
    )
    assert r.count == 2


# ─── extract_rules — basic ────────────────────────────────────────────────────


def test_extract_rules_returns_extraction_result():
    result = extract_rules("I prefer Python", "")
    assert isinstance(result, ExtractionResult)
    assert result.method == "rule"


def test_extract_rules_empty_text():
    result = extract_rules("", "")
    assert result.count == 0


def test_extract_rules_short_turn_stored():
    result = extract_rules("I prefer Python for all my projects.", "")
    assert len(result.source_turn) <= 200


# ─── extract_rules — preference patterns ─────────────────────────────────────


def test_extract_rules_detects_preference():
    result = extract_rules("I prefer Python over Java.", "")
    assert result.count >= 1
    contents = [f.content for f in result.facts]
    assert any("python" in c.lower() or "Python" in c for c in contents)


def test_extract_rules_like_pattern():
    result = extract_rules("I like to use Docker for containerization.", "")
    assert result.count >= 1


def test_extract_rules_typically_pattern():
    result = extract_rules("I typically use nginx for reverse proxy.", "")
    assert result.count >= 1


# ─── extract_rules — identity patterns ───────────────────────────────────────


def test_extract_rules_name():
    result = extract_rules("My name is Alex.", "")
    assert result.count >= 1
    assert any("Alex" in f.content for f in result.facts)


def test_extract_rules_role():
    result = extract_rules("My role is DevOps Engineer.", "")
    assert result.count >= 1


def test_extract_rules_location():
    result = extract_rules("I'm based in Berlin.", "")
    assert result.count >= 1


# ─── extract_rules — decision patterns ────────────────────────────────────────


def test_extract_rules_decision_lets():
    result = extract_rules("Let's use PostgreSQL for the database.", "")
    assert result.count >= 1


def test_extract_rules_decided():
    result = extract_rules("We decided to use Redis for caching.", "")
    assert result.count >= 1


# ─── extract_rules — technical patterns ────────────────────────────────────────


def test_extract_rules_stack():
    result = extract_rules("Our stack includes Laravel, Docker, PostgreSQL.", "")
    assert result.count >= 1


def test_extract_rules_we_use_for():
    result = extract_rules("We use Nginx for load balancing.", "")
    assert result.count >= 1


# ─── extract_rules — problem/solution patterns ────────────────────────────────


def test_extract_rules_error_pattern_user():
    result = extract_rules("Getting an error: Connection refused on port 5432.", "")
    assert result.count >= 1
    assert any(f.category == "problem_solution" for f in result.facts)


def test_extract_rules_resolved_pattern_assistant():
    result = extract_rules("", "Fixed by restarting the Docker container and checking the logs.")
    assert result.count >= 1
    assert any(f.category == "problem_solution" for f in result.facts)


# ─── extract_rules — deduplication ────────────────────────────────────────────


def test_extract_rules_no_duplicates():
    # Repeat same preference — should yield only one fact
    result = extract_rules("I prefer Docker. I prefer Docker.", "")
    contents = [f.content for f in result.facts]
    assert len(contents) == len(set(contents))


# ─── extract_rules — categories ───────────────────────────────────────────────


def test_extract_rules_category_assignment():
    result = extract_rules("I prefer TypeScript over JavaScript.", "")
    for fact in result.facts:
        assert fact.category in ("preference", "decision", "identity", "technical", "problem_solution")


# ─── extract_rules — source params ────────────────────────────────────────────


def test_extract_rules_source_params_propagated():
    result = extract_rules(
        "I prefer Python.",
        "",
        source_conversation_id="conv-123",
        source_platform="telegram",
    )
    for fact in result.facts:
        assert fact.source_conversation_id == "conv-123"
        assert fact.source_platform == "telegram"
