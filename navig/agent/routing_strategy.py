"""
routing_strategy.py — Request complexity classifier + routing tier selector.

Ported from .lab/ClawRouter/src/router/rules.ts + strategy.ts (TypeScript → Python).
Handles ~70–80% of routing decisions in <1ms with zero external calls.

Usage::

    from navig.agent.routing_strategy import classify_request, RequestTier

    tier = classify_request(messages, tools=tool_list)
    # Returns: "SIMPLE" | "MEDIUM" | "COMPLEX" | "REASONING" | "AGENTIC"
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from loguru import logger

# ─── Types ─────────────────────────────────────────────────────────────────────

RequestTier = Literal["SIMPLE", "MEDIUM", "COMPLEX", "REASONING", "AGENTIC"]
RoutingProfile = Literal["auto", "eco", "premium", "agentic"]

# ─── Keyword Lists (ported from ClawRouter config defaults) ────────────────────

_CODE_KEYWORDS: list[str] = [
    "code",
    "function",
    "class",
    "method",
    "bug",
    "error",
    "debug",
    "refactor",
    "implement",
    "algorithm",
    "syntax",
    "compile",
    "test",
    "unit test",
    "api",
    "endpoint",
    "database",
    "sql",
    "script",
    "module",
    "library",
    "import",
]

_REASONING_KEYWORDS: list[str] = [
    "explain",
    "analyze",
    "compare",
    "evaluate",
    "step by step",
    "pros and cons",
    "trade-off",
    "think through",
    "reasoning",
    "logic",
    "argument",
    "justify",
    "assess",
    "critique",
    "consider",
]

_TECHNICAL_KEYWORDS: list[str] = [
    "architecture",
    "system",
    "infrastructure",
    "deployment",
    "configuration",
    "performance",
    "scalability",
    "security",
    "authentication",
    "authorization",
    "microservice",
    "docker",
    "kubernetes",
    "ci/cd",
    "pipeline",
    "cache",
    "latency",
    "throughput",
    "concurrency",
]

_CREATIVE_KEYWORDS: list[str] = [
    "write",
    "create",
    "draft",
    "compose",
    "story",
    "poem",
    "essay",
    "content",
    "creative",
    "generate",
    "brainstorm",
    "ideas",
]

_SIMPLE_KEYWORDS: list[str] = [
    "what is",
    "define",
    "list",
    "summarize",
    "translate",
    "convert",
    "calculate",
    "format",
    "rename",
    "show me",
    "tell me",
]

_AGENTIC_KEYWORDS: list[str] = [
    "plan",
    "execute",
    "loop",
    "orchestrate",
    "workflow",
    "subtask",
    "delegate",
    "spawn",
    "tool_call",
    "function_call",
    "automated",
    "pipeline",
    "agent",
    "step by step do",
    "iterate",
    "continuously",
    "monitor and",
    "schedule",
    "batch",
]

_IMPERATIVE_VERBS: list[str] = [
    "build",
    "deploy",
    "migrate",
    "optimize",
    "automate",
    "integrate",
    "restructure",
    "redesign",
    "implement",
    "refactor",
]

_CONSTRAINT_INDICATORS: list[str] = [
    "must",
    "should",
    "cannot",
    "without",
    "avoid",
    "ensure",
    "require",
    "constraint",
    "limitation",
    "only if",
    "unless",
]

_OUTPUT_FORMAT_KEYWORDS: list[str] = [
    "json",
    "xml",
    "markdown",
    "table",
    "list",
    "csv",
    "structured",
    "format as",
    "output format",
]

_NEGATION_KEYWORDS: list[str] = [
    "not",
    "don't",
    "never",
    "no ",
    "without",
    "except",
    "exclude",
    "ignore",
]

_DOMAIN_KEYWORDS: list[str] = [
    "blockchain",
    "machine learning",
    "neural network",
    "llm",
    "embedding",
    "vector database",
    "transformer",
    "attention mechanism",
    "fine-tuning",
    "rag",
    "retrieval",
    "reinforcement learning",
]

# Multi-step patterns (regex)
_MULTI_STEP_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"first.*then", re.IGNORECASE),
    re.compile(r"step \d", re.IGNORECASE),
    re.compile(r"\d\.\s"),
]

# ─── Dimension Weights (matching ClawRouter defaults) ──────────────────────────

_DIMENSION_WEIGHTS: dict[str, float] = {
    "tokenCount": 0.20,
    "codePresence": 0.15,
    "reasoningMarkers": 0.20,
    "technicalTerms": 0.10,
    "creativeMarkers": 0.05,
    "simpleIndicators": 0.15,
    "multiStepPatterns": 0.05,
    "questionComplexity": 0.05,
    "imperativeVerbs": 0.05,
    "constraintCount": 0.05,
    "outputFormat": 0.05,
    "referenceComplexity": 0.03,
    "negationComplexity": 0.02,
    "domainSpecificity": 0.10,
    "agenticTask": 0.20,
}

# Tier score boundaries (lower → higher complexity)
_TIER_BOUNDARIES = {
    "simple_medium": 0.10,
    "medium_complex": 0.35,
    "complex_reasoning": 0.65,
}

# ─── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class ClassificationResult:
    """Full scoring breakdown for a single request."""

    tier: RequestTier | None
    """Resolved tier — None means ambiguous (below confidence threshold)."""

    score: float
    """Weighted aggregate score in roughly [-1, 1]."""

    confidence: float
    """Sigmoid-calibrated confidence [0.5, 1.0]."""

    agentic_score: float
    """Raw agentic signal [0, 1] independent of tier."""

    signals: list[str] = field(default_factory=list)
    """Human-readable signal labels that contributed to the score."""

    profile: RoutingProfile = "auto"
    """Routing profile that was active during classification."""


# ─── Dimension Scorers ─────────────────────────────────────────────────────────


def _score_token_count(
    tokens: int, simple_threshold: int = 500, complex_threshold: int = 2000
) -> tuple[float, str | None]:
    if tokens < simple_threshold:
        return -1.0, f"short ({tokens} tokens)"
    if tokens > complex_threshold:
        return 1.0, f"long ({tokens} tokens)"
    return 0.0, None


def _score_keywords(
    text: str,
    keywords: list[str],
    low_threshold: int,
    high_threshold: int,
    score_none: float,
    score_low: float,
    score_high: float,
    signal_label: str,
) -> tuple[float, str | None]:
    matches = [kw for kw in keywords if kw.lower() in text]
    count = len(matches)
    preview = ", ".join(matches[:3])
    if count >= high_threshold:
        return score_high, f"{signal_label} ({preview})"
    if count >= low_threshold:
        return score_low, f"{signal_label} ({preview})"
    return score_none, None


def _score_multi_step(text: str) -> tuple[float, str | None]:
    for pat in _MULTI_STEP_PATTERNS:
        if pat.search(text):
            return 0.5, "multi-step"
    return 0.0, None


def _score_question_complexity(text: str) -> tuple[float, str | None]:
    count = text.count("?")
    if count > 3:
        return 0.5, f"{count} questions"
    return 0.0, None


def _score_agentic(text: str) -> tuple[float, str | None, float]:
    """Returns (dimension_score, signal, raw_agentic_score)."""
    matched = [kw for kw in _AGENTIC_KEYWORDS if kw.lower() in text]
    count = len(matched)
    preview = ", ".join(matched[:3])
    if count >= 4:
        return 1.0, f"agentic ({preview})", 1.0
    if count >= 3:
        return 0.6, f"agentic ({preview})", 0.6
    if count >= 1:
        return 0.2, f"agentic-light ({preview})", 0.2
    return 0.0, None, 0.0


def _calibrate_confidence(distance: float, steepness: float = 8.0) -> float:
    """Sigmoid confidence: maps distance from tier boundary to [0.5, 1.0]."""
    return 1.0 / (1.0 + math.exp(-steepness * distance))


# ─── Main Classifier ───────────────────────────────────────────────────────────


def classify_request(
    messages: list[dict[str, Any]],
    *,
    tools: list[Any] | None = None,
    profile: RoutingProfile = "auto",
    max_tokens_force_complex: int = 8000,
    confidence_threshold: float = 0.60,
) -> ClassificationResult:
    """
    Classify a request into a routing tier.

    Args:
        messages:               OpenAI-format message list (role/content).
        tools:                  Optional list of tool definitions. Presence triggers AGENTIC.
        profile:                Routing profile — ``"auto"`` (default), ``"eco"``, ``"premium"``.
        max_tokens_force_complex: Override — requests longer than this are always COMPLEX.
        confidence_threshold:   Minimum confidence to accept a rule classification.
                                Below threshold → tier is ambiguous, defaults to MEDIUM.

    Returns:
        :class:`ClassificationResult` with tier, score, confidence, and signals.
    """
    # Extract user prompt (last user message) and system prompt separately.
    # ClawRouter scores agentic/keyword dimensions against USER text only
    # to avoid system-prompt boilerplate contaminating results (see issue #50).
    system_parts: list[str] = []
    user_parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if not isinstance(content, str):
            # Handle array-content (vision messages etc.) — fallback to empty
            content = ""
        if role == "system":
            system_parts.append(content)
        else:
            user_parts.append(content)

    system_text = " ".join(system_parts)
    user_text = " ".join(user_parts)
    full_text = f"{system_text} {user_text}"

    # Token estimate: ~4 chars per token
    estimated_tokens = max(1, len(full_text) // 4)

    # --- Early exit: large context always COMPLEX ---
    if estimated_tokens > max_tokens_force_complex:
        logger.debug(
            "routing_strategy: force COMPLEX — {} tokens > {}",
            estimated_tokens,
            max_tokens_force_complex,
        )
        return ClassificationResult(
            tier="COMPLEX",
            score=1.0,
            confidence=0.95,
            agentic_score=0.0,
            signals=[f"exceeds {max_tokens_force_complex} tokens"],
            profile=profile,
        )

    # Score dimensions against user text (lowercased)
    ut = user_text.lower()
    dimensions: dict[str, tuple[float, str | None]] = {}

    dimensions["tokenCount"] = _score_token_count(estimated_tokens)
    dimensions["codePresence"] = _score_keywords(
        ut, _CODE_KEYWORDS, 1, 2, 0.0, 0.5, 1.0, "code"
    )
    dimensions["reasoningMarkers"] = _score_keywords(
        ut, _REASONING_KEYWORDS, 1, 2, 0.0, 0.7, 1.0, "reasoning"
    )
    dimensions["technicalTerms"] = _score_keywords(
        ut, _TECHNICAL_KEYWORDS, 2, 4, 0.0, 0.5, 1.0, "technical"
    )
    dimensions["creativeMarkers"] = _score_keywords(
        ut, _CREATIVE_KEYWORDS, 1, 2, 0.0, 0.5, 0.7, "creative"
    )
    dimensions["simpleIndicators"] = _score_keywords(
        ut, _SIMPLE_KEYWORDS, 1, 2, 0.0, -1.0, -1.0, "simple"
    )
    dimensions["multiStepPatterns"] = _score_multi_step(ut)
    dimensions["questionComplexity"] = _score_question_complexity(user_text)
    dimensions["imperativeVerbs"] = _score_keywords(
        ut, _IMPERATIVE_VERBS, 1, 2, 0.0, 0.3, 0.5, "imperative"
    )
    dimensions["constraintCount"] = _score_keywords(
        ut, _CONSTRAINT_INDICATORS, 1, 3, 0.0, 0.3, 0.7, "constraints"
    )
    dimensions["outputFormat"] = _score_keywords(
        ut, _OUTPUT_FORMAT_KEYWORDS, 1, 2, 0.0, 0.4, 0.7, "format"
    )
    dimensions["referenceComplexity"] = _score_keywords(
        ut, _REASONING_KEYWORDS, 1, 2, 0.0, 0.3, 0.5, "references"
    )
    dimensions["negationComplexity"] = _score_keywords(
        ut, _NEGATION_KEYWORDS, 2, 3, 0.0, 0.3, 0.5, "negation"
    )
    dimensions["domainSpecificity"] = _score_keywords(
        ut, _DOMAIN_KEYWORDS, 1, 2, 0.0, 0.5, 0.8, "domain-specific"
    )

    agentic_dim_score, agentic_signal, agentic_score = _score_agentic(ut)
    dimensions["agenticTask"] = (agentic_dim_score, agentic_signal)

    # Collect signals
    signals = [sig for (_, sig) in dimensions.values() if sig is not None]

    # Weighted aggregate score
    weighted_score = sum(
        score * _DIMENSION_WEIGHTS.get(name, 0.0)
        for name, (score, _) in dimensions.items()
    )

    # --- Agentic override ---
    # tools present → always AGENTIC
    # agentic_score >= 0.5 (3+ keyword matches) → AGENTIC in auto profile
    has_tools = bool(tools)
    is_agentic = has_tools or (profile == "auto" and agentic_score >= 0.5)
    if is_agentic:
        reason = "tools present" if has_tools else f"agentic score {agentic_score:.1f}"
        logger.debug("routing_strategy: AGENTIC — {}", reason)
        return ClassificationResult(
            tier="AGENTIC",
            score=weighted_score,
            confidence=0.90,
            agentic_score=agentic_score,
            signals=signals,
            profile="agentic" if profile == "auto" else profile,
        )

    # --- Reasoning override: 2+ reasoning markers in user text ---
    reasoning_hits = [kw for kw in _REASONING_KEYWORDS if kw.lower() in ut]
    if len(reasoning_hits) >= 2:
        conf = max(_calibrate_confidence(max(weighted_score, 0.3)), 0.85)
        return ClassificationResult(
            tier="REASONING",
            score=weighted_score,
            confidence=conf,
            agentic_score=agentic_score,
            signals=signals,
            profile=profile,
        )

    # --- Tier from score boundaries ---
    sm = _TIER_BOUNDARIES["simple_medium"]
    mc = _TIER_BOUNDARIES["medium_complex"]
    cr = _TIER_BOUNDARIES["complex_reasoning"]

    tier: RequestTier | None
    distance: float

    if weighted_score < sm:
        tier = "SIMPLE"
        distance = sm - weighted_score
    elif weighted_score < mc:
        tier = "MEDIUM"
        distance = min(weighted_score - sm, mc - weighted_score)
    elif weighted_score < cr:
        tier = "COMPLEX"
        distance = min(weighted_score - mc, cr - weighted_score)
    else:
        tier = "REASONING"
        distance = weighted_score - cr

    confidence = _calibrate_confidence(distance)

    # Below confidence threshold → ambiguous; default to MEDIUM
    if confidence < confidence_threshold:
        logger.debug(
            "routing_strategy: ambiguous (score={:.3f}, conf={:.3f}) → MEDIUM",
            weighted_score,
            confidence,
        )
        tier = "MEDIUM"
        confidence = 0.5

    logger.debug(
        "routing_strategy: {} | score={:.3f} conf={:.3f} tokens={} signals=[{}]",
        tier,
        weighted_score,
        confidence,
        estimated_tokens,
        ", ".join(signals[:5]),
    )

    return ClassificationResult(
        tier=tier,
        score=weighted_score,
        confidence=confidence,
        agentic_score=agentic_score,
        signals=signals,
        profile=profile,
    )


# ─── Convenience helpers ───────────────────────────────────────────────────────


def classify_prompt(
    prompt: str,
    *,
    system_prompt: str = "",
    tools: list[Any] | None = None,
    profile: RoutingProfile = "auto",
) -> ClassificationResult:
    """
    Convenience wrapper when you have raw strings instead of a messages list.

    Args:
        prompt:        The user prompt text.
        system_prompt: Optional system prompt.
        tools:         Optional tool list.
        profile:       Routing profile.
    """
    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    return classify_request(messages, tools=tools, profile=profile)


def tier_rank(tier: RequestTier | None) -> int:
    """Numeric rank for tier comparison (SIMPLE=0, MEDIUM=1, COMPLEX=2, REASONING=3, AGENTIC=4)."""
    return {"SIMPLE": 0, "MEDIUM": 1, "COMPLEX": 2, "REASONING": 3, "AGENTIC": 4}.get(
        tier or "MEDIUM", 1
    )
