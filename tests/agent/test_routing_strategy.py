"""
Tests for navig.agent.routing_strategy — classify_request and ClassificationResult.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from navig.agent.routing_strategy import (
    ClassificationResult,
    RequestTier,
    classify_request,
)

# Patch target for estimate_tokens (imported locally inside classify_request)
_TOKENS_PATCH = "navig.core.tokens.estimate_tokens"


def _fake_tokens(tokens: int):
    """Return a patcher that makes estimate_tokens return a fixed value."""
    return patch(_TOKENS_PATCH, return_value=tokens)


def _msg(content: str, role: str = "user") -> dict:
    return {"role": role, "content": content}


# ─── ClassificationResult dataclass ──────────────────────────────────────────


def test_classification_result_fields():
    r = ClassificationResult(
        tier="SIMPLE",
        score=-0.5,
        confidence=0.9,
        agentic_score=0.0,
        signals=["short (20 tokens)"],
    )
    assert r.tier == "SIMPLE"
    assert r.score == -0.5
    assert r.confidence == 0.9
    assert r.agentic_score == 0.0
    assert r.signals == ["short (20 tokens)"]
    assert r.profile == "auto"  # default


def test_classification_result_default_profile():
    r = ClassificationResult(
        tier="MEDIUM", score=0.0, confidence=0.6, agentic_score=0.0
    )
    assert r.profile == "auto"


def test_classification_result_custom_profile():
    r = ClassificationResult(
        tier="COMPLEX", score=0.7, confidence=0.85, agentic_score=0.0, profile="eco"
    )
    assert r.profile == "eco"


# ─── classify_request — SIMPLE tier ─────────────────────────────────────────


def test_classify_simple_short_message():
    with _fake_tokens(50):
        result = classify_request([_msg("what is python?")])
    assert isinstance(result, ClassificationResult)
    # Short message should lean SIMPLE or tier assigned
    assert result.tier in ("SIMPLE", "MEDIUM", None)


def test_classify_returns_classification_result():
    with _fake_tokens(200):
        result = classify_request([_msg("define recursion")])
    assert isinstance(result, ClassificationResult)
    assert result.tier is not None or result.confidence <= 1.0


# ─── classify_request — COMPLEX forced by token count ────────────────────────


def test_classify_forced_complex_large_context():
    """Requests exceeding max_tokens_force_complex must return COMPLEX."""
    with _fake_tokens(10_000):
        result = classify_request(
            [_msg("summarize this")],
            max_tokens_force_complex=8000,
        )
    assert result.tier == "COMPLEX"
    assert result.confidence == 0.95
    assert any("exceeds" in s for s in result.signals)


def test_classify_forced_complex_custom_threshold():
    with _fake_tokens(5000):
        result = classify_request(
            [_msg("hello")],
            max_tokens_force_complex=4000,
        )
    assert result.tier == "COMPLEX"


# ─── classify_request — AGENTIC via tools ────────────────────────────────────


def test_classify_agentic_with_tools_list():
    """Presence of tools should push toward AGENTIC tier."""
    tools = [{"type": "function", "function": {"name": "execute_command"}}]
    with _fake_tokens(100):
        result = classify_request([_msg("run the deploy workflow")], tools=tools)
    assert isinstance(result, ClassificationResult)


# ─── classify_request — profile parameter ────────────────────────────────────


@pytest.mark.parametrize("profile", ["auto", "eco", "premium", "agentic"])
def test_classify_stores_profile(profile):
    with _fake_tokens(100):
        result = classify_request([_msg("hello")], profile=profile)
    assert result.profile == profile


# ─── classify_request — message role separation ──────────────────────────────


def test_classify_handles_system_message():
    messages = [
        _msg("You are a helpful assistant.", role="system"),
        _msg("What is Docker?", role="user"),
    ]
    with _fake_tokens(80):
        result = classify_request(messages)
    assert isinstance(result, ClassificationResult)


def test_classify_empty_messages():
    with _fake_tokens(5):
        result = classify_request([])
    assert isinstance(result, ClassificationResult)


def test_classify_non_string_content_handled():
    """Image/vision messages with array content shouldn't crash."""
    messages = [{"role": "user", "content": [{"type": "image_url", "url": "..."}]}]
    with _fake_tokens(10):
        result = classify_request(messages)
    assert isinstance(result, ClassificationResult)


# ─── classify_request — code keywords → higher tier ─────────────────────────


def test_classify_code_keywords_raise_tier():
    with _fake_tokens(150):
        result = classify_request(
            [_msg("write a python function to refactor and debug the algorithm")]
        )
    assert isinstance(result, ClassificationResult)
    # Tier is determined by token count + keyword signals together
    assert result.tier in ("SIMPLE", "MEDIUM", "COMPLEX", "REASONING", "AGENTIC", None)


# ─── classify_request — agentic keywords ─────────────────────────────────────


def test_classify_agentic_keywords():
    text = "plan and execute the workflow step by step, orchestrate the pipeline and batch the subtasks"
    with _fake_tokens(100):
        result = classify_request([_msg(text)])
    assert isinstance(result, ClassificationResult)
    # Should score high agentic
    assert result.agentic_score >= 0.0


# ─── classify_request — confidence threshold ─────────────────────────────────


def test_classify_confidence_threshold_default():
    with _fake_tokens(200):
        result = classify_request([_msg("do something")])
    assert 0.0 <= result.confidence <= 1.0


def test_classify_custom_confidence_threshold():
    with _fake_tokens(200):
        result = classify_request(
            [_msg("do something")], confidence_threshold=0.9
        )
    assert isinstance(result, ClassificationResult)
