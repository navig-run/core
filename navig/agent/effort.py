"""FA-02  Effort Levels — multi-tier thinking-budget control per provider.

Provides five effort tiers (LOW → ULTRATHINK) that translate into
provider-specific thinking / reasoning parameters:

* **Anthropic** → ``thinking.budget_tokens``
* **OpenAI**    → ``reasoning_effort`` (``"low"`` / ``"medium"`` / ``"high"``)
* **Google**    → ``thinking_config.budget`` (token count)
* **DeepSeek**  → token budget (same shape as Anthropic)
* **Others**    → no-op (graceful degradation)

Usage::

    from navig.agent.effort import (
        EffortLevel,
        resolve_effort,
        auto_detect_effort,
        get_thinking_params,
    )

    level = resolve_effort("ultra")          # → EffortLevel.ULTRATHINK
    level = auto_detect_effort("fix typo")   # → EffortLevel.LOW
    params = get_thinking_params(level, provider="anthropic")
    # → {"thinking": {"type": "enabled", "budget_tokens": 131072}}
"""

from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Any

logger = logging.getLogger("navig.agent.effort")

__all__ = [
    "EffortLevel",
    "EFFORT_ALIASES",
    "ANTHROPIC_THINKING_BUDGET",
    "OPENAI_REASONING_EFFORT",
    "GOOGLE_THINKING_BUDGET",
    "DEEPSEEK_THINKING_BUDGET",
    "resolve_effort",
    "auto_detect_effort",
    "get_thinking_params",
    "supports_thinking",
]


# ─────────────────────────────────────────────────────────────────
# Effort enum
# ─────────────────────────────────────────────────────────────────


class EffortLevel(Enum):
    """Five-tier effort scale controlling thinking / reasoning budget."""

    LOW = "low"                # Trivial tasks — skip extended thinking
    MEDIUM = "medium"          # Balanced default
    HIGH = "high"              # Complex tasks
    MAXIMUM = "maximum"        # Highest provider-supported budget
    ULTRATHINK = "ultrathink"  # navig exclusive: 128 K thinking tokens


# ─────────────────────────────────────────────────────────────────
# Aliases
# ─────────────────────────────────────────────────────────────────


EFFORT_ALIASES: dict[str, EffortLevel] = {
    # Canonical values (so resolve_effort("low") works)
    "low": EffortLevel.LOW,
    "medium": EffortLevel.MEDIUM,
    "high": EffortLevel.HIGH,
    "maximum": EffortLevel.MAXIMUM,
    "ultrathink": EffortLevel.ULTRATHINK,
    # Short forms
    "l": EffortLevel.LOW,
    "lo": EffortLevel.LOW,
    "m": EffortLevel.MEDIUM,
    "med": EffortLevel.MEDIUM,
    "h": EffortLevel.HIGH,
    "hi": EffortLevel.HIGH,
    "max": EffortLevel.MAXIMUM,
    "ultra": EffortLevel.ULTRATHINK,
    "ut": EffortLevel.ULTRATHINK,
}


# ─────────────────────────────────────────────────────────────────
# Provider budget maps
# ─────────────────────────────────────────────────────────────────


ANTHROPIC_THINKING_BUDGET: dict[EffortLevel, int] = {
    EffortLevel.LOW: 1024,
    EffortLevel.MEDIUM: 8192,
    EffortLevel.HIGH: 32768,
    EffortLevel.MAXIMUM: 65536,
    EffortLevel.ULTRATHINK: 131072,
}

OPENAI_REASONING_EFFORT: dict[EffortLevel, str] = {
    EffortLevel.LOW: "low",
    EffortLevel.MEDIUM: "medium",
    EffortLevel.HIGH: "high",
    EffortLevel.MAXIMUM: "high",
    EffortLevel.ULTRATHINK: "high",
}

GOOGLE_THINKING_BUDGET: dict[EffortLevel, int] = {
    EffortLevel.LOW: 1024,
    EffortLevel.MEDIUM: 8192,
    EffortLevel.HIGH: 32768,
    EffortLevel.MAXIMUM: 65536,
    EffortLevel.ULTRATHINK: 131072,
}

DEEPSEEK_THINKING_BUDGET: dict[EffortLevel, int] = {
    EffortLevel.LOW: 1024,
    EffortLevel.MEDIUM: 8192,
    EffortLevel.HIGH: 32768,
    EffortLevel.MAXIMUM: 65536,
    EffortLevel.ULTRATHINK: 131072,
}

# Providers whose thinking model we know how to configure.
_THINKING_PROVIDERS: frozenset[str] = frozenset(
    {"anthropic", "openai", "google", "deepseek"}
)


# ─────────────────────────────────────────────────────────────────
# Auto-detection heuristics
# ─────────────────────────────────────────────────────────────────


_LOW_PATTERNS: list[str] = [
    "rename",
    "fix typo",
    "change color",
    "update version",
    "bump version",
    "remove unused",
    "delete line",
    "add import",
    "add comma",
    "fix indent",
    "fix whitespace",
    "change name",
    "swap",
]

_HIGH_PATTERNS: list[str] = [
    "architect",
    "design",
    "refactor entire",
    "security audit",
    "migrate",
    "rewrite",
    "implement feature",
    "plan",
    "complex",
    "multi-step",
    "review all",
    "analyze codebase",
    "full audit",
]

# Pre-compiled for fast matching.
_LOW_RE = re.compile("|".join(re.escape(p) for p in _LOW_PATTERNS), re.IGNORECASE)
_HIGH_RE = re.compile("|".join(re.escape(p) for p in _HIGH_PATTERNS), re.IGNORECASE)


def auto_detect_effort(
    user_message: str,
    *,
    tool_history: list[str] | None = None,
) -> EffortLevel:
    """Heuristic effort detection from message complexity.

    Decision order:

    1. Keyword match against LOW / HIGH pattern lists
    2. Word-count heuristic (< 10 → LOW, > 100 → HIGH)
    3. Default → MEDIUM
    """
    if not user_message:
        return EffortLevel.MEDIUM

    text = user_message.strip()

    if _LOW_RE.search(text):
        return EffortLevel.LOW

    if _HIGH_RE.search(text):
        return EffortLevel.HIGH

    word_count = len(text.split())
    if word_count < 10:
        return EffortLevel.LOW
    if word_count > 100:
        return EffortLevel.HIGH

    return EffortLevel.MEDIUM


# ─────────────────────────────────────────────────────────────────
# Resolution
# ─────────────────────────────────────────────────────────────────


def resolve_effort(value: str | EffortLevel | None) -> EffortLevel:
    """Resolve an effort specifier to an :class:`EffortLevel`.

    Accepts:
    - ``None`` → ``MEDIUM``
    - An :class:`EffortLevel` instance (returned as-is)
    - A string matching an alias (case-insensitive)

    Raises :class:`ValueError` for unrecognised strings.
    """
    if value is None:
        return EffortLevel.MEDIUM

    if isinstance(value, EffortLevel):
        return value

    key = str(value).strip().lower()
    if key in EFFORT_ALIASES:
        return EFFORT_ALIASES[key]

    raise ValueError(
        f"Unknown effort level {value!r}. "
        f"Valid: {', '.join(sorted(EFFORT_ALIASES))}"
    )


# ─────────────────────────────────────────────────────────────────
# Provider-specific thinking parameters
# ─────────────────────────────────────────────────────────────────


def supports_thinking(provider: str) -> bool:
    """Return ``True`` if *provider* supports configurable thinking budgets."""
    return provider.lower() in _THINKING_PROVIDERS


def get_thinking_params(
    level: EffortLevel,
    *,
    provider: str,
) -> dict[str, Any]:
    """Return provider-specific params dict for the given effort level.

    The returned dict can be merged into the API request payload.  For
    providers without thinking support, an empty dict is returned (no-op).

    Examples::

        # Anthropic
        {"thinking": {"type": "enabled", "budget_tokens": 32768}}

        # OpenAI
        {"reasoning_effort": "high"}

        # Google
        {"thinking_config": {"thinking_budget": 32768}}

        # DeepSeek
        {"thinking": {"type": "enabled", "budget_tokens": 32768}}

        # Unknown provider
        {}
    """
    prov = provider.lower()

    if prov == "anthropic":
        budget = ANTHROPIC_THINKING_BUDGET[level]
        if level == EffortLevel.LOW:
            # LOW → disable extended thinking entirely
            return {"thinking": {"type": "disabled"}}
        return {"thinking": {"type": "enabled", "budget_tokens": budget}}

    if prov == "openai":
        return {"reasoning_effort": OPENAI_REASONING_EFFORT[level]}

    if prov == "google":
        budget = GOOGLE_THINKING_BUDGET[level]
        return {"thinking_config": {"thinking_budget": budget}}

    if prov == "deepseek":
        budget = DEEPSEEK_THINKING_BUDGET[level]
        if level == EffortLevel.LOW:
            return {"thinking": {"type": "disabled"}}
        return {"thinking": {"type": "enabled", "budget_tokens": budget}}

    # Unsupported provider → graceful no-op
    logger.debug(
        "Provider %r does not support thinking budgets — effort %s ignored",
        provider,
        level.value,
    )
    return {}
