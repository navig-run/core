"""
Provider Model Capabilities Registry

Static capability metadata for known models, replacing substring heuristics
with verified capability flags.  Used by discovery.py, vision routing, and
the Telegram provider control surface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from collections.abc import Sequence

# ─────────────────────────────────────────────────────────────────────────────
# Capability enum
# ─────────────────────────────────────────────────────────────────────────────


class Capability(str, Enum):
    """Capabilities a model may advertise."""

    TEXT = "text"  # Standard text completion (all models)
    VISION = "vision"  # Can accept image inputs
    CODE = "code"  # Optimised for code generation
    REASONING = "reasoning"  # Chain-of-thought / extended thinking
    FAST = "fast"  # Optimised for low latency
    LOCAL = "local"  # Runs on-device (Ollama, llama.cpp, AirLLM)
    VOICE = "voice"  # Speech-to-text / text-to-speech


# ─────────────────────────────────────────────────────────────────────────────
# Capability registry entry
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ModelCapabilityEntry:
    """A pattern → capabilities mapping.

    ``pattern`` is matched against the full model name using
    :func:`re.search` (case-insensitive).  The *first* matching entry wins,
    so entries are ordered from most-specific to least-specific.

    ``source`` is ``"verified"`` when the mapping comes from official vendor
    docs and ``"inferred"`` when derived from naming conventions.
    """

    pattern: str
    capabilities: tuple[Capability, ...]
    source: str = "verified"  # "verified" | "inferred"


# ─────────────────────────────────────────────────────────────────────────────
# Static registry — ordered most-specific first
# ─────────────────────────────────────────────────────────────────────────────

_CAPABILITY_REGISTRY: list[ModelCapabilityEntry] = [
    # ── OpenAI ───────────────────────────────────────────────────────────
    ModelCapabilityEntry(
        r"gpt-4\.1(?!-mini|-nano)",
        (Capability.TEXT, Capability.VISION, Capability.CODE),
    ),
    ModelCapabilityEntry(
        r"gpt-4\.1-mini",
        (Capability.TEXT, Capability.VISION, Capability.FAST),
    ),
    ModelCapabilityEntry(
        r"gpt-4\.1-nano",
        (Capability.TEXT, Capability.FAST),
    ),
    ModelCapabilityEntry(
        r"gpt-4o(?!-mini)",
        (Capability.TEXT, Capability.VISION, Capability.CODE),
    ),
    ModelCapabilityEntry(
        r"gpt-4o-mini",
        (Capability.TEXT, Capability.VISION, Capability.FAST),
    ),
    ModelCapabilityEntry(
        r"o4-mini",
        (Capability.TEXT, Capability.VISION, Capability.REASONING),
    ),
    ModelCapabilityEntry(
        r"^o3$|/o3$",
        (Capability.TEXT, Capability.VISION, Capability.REASONING, Capability.CODE),
    ),
    ModelCapabilityEntry(
        r"o3-mini",
        (Capability.TEXT, Capability.REASONING, Capability.FAST),
    ),
    ModelCapabilityEntry(
        r"o1(?!-mini)",
        (Capability.TEXT, Capability.REASONING),
    ),
    ModelCapabilityEntry(
        r"o1-mini",
        (Capability.TEXT, Capability.REASONING, Capability.FAST),
    ),
    # ── Anthropic ────────────────────────────────────────────────────────
    ModelCapabilityEntry(
        r"claude-3-7-sonnet",
        (Capability.TEXT, Capability.VISION, Capability.CODE, Capability.REASONING),
    ),
    ModelCapabilityEntry(
        r"claude-3[.-]5-sonnet",
        (Capability.TEXT, Capability.VISION, Capability.CODE),
    ),
    ModelCapabilityEntry(
        r"claude-3-5-haiku",
        (Capability.TEXT, Capability.VISION, Capability.FAST),
    ),
    ModelCapabilityEntry(
        r"claude-3-opus",
        (Capability.TEXT, Capability.VISION, Capability.REASONING),
    ),
    # ── Google Gemini ────────────────────────────────────────────────────
    ModelCapabilityEntry(
        r"gemini-2\.5-pro",
        (Capability.TEXT, Capability.VISION, Capability.CODE, Capability.REASONING),
    ),
    ModelCapabilityEntry(
        r"gemini-2\.5-flash",
        (Capability.TEXT, Capability.VISION, Capability.FAST, Capability.REASONING),
    ),
    ModelCapabilityEntry(
        r"gemini-2\.0-flash(?!-lite)",
        (Capability.TEXT, Capability.VISION, Capability.FAST),
    ),
    ModelCapabilityEntry(
        r"gemini-2\.0-flash-lite",
        (Capability.TEXT, Capability.FAST),
    ),
    ModelCapabilityEntry(
        r"gemini-1\.5-pro",
        (Capability.TEXT, Capability.VISION),
    ),
    ModelCapabilityEntry(
        r"gemini-1\.5-flash",
        (Capability.TEXT, Capability.VISION, Capability.FAST),
    ),
    # ── xAI / Grok ──────────────────────────────────────────────────────
    ModelCapabilityEntry(
        r"grok-3(?!-mini|-fast)",
        (Capability.TEXT, Capability.CODE, Capability.REASONING),
    ),
    ModelCapabilityEntry(
        r"grok-3-fast",
        (Capability.TEXT, Capability.FAST),
    ),
    ModelCapabilityEntry(
        r"grok-3-mini(?!-fast)",
        (Capability.TEXT, Capability.FAST, Capability.REASONING),
    ),
    ModelCapabilityEntry(
        r"grok-3-mini-fast",
        (Capability.TEXT, Capability.FAST),
    ),
    ModelCapabilityEntry(
        r"grok-2-vision",
        (Capability.TEXT, Capability.VISION),
    ),
    ModelCapabilityEntry(
        r"grok-2",
        (Capability.TEXT,),
    ),
    # ── Mistral ──────────────────────────────────────────────────────────
    ModelCapabilityEntry(
        r"pixtral",
        (Capability.TEXT, Capability.VISION),
    ),
    ModelCapabilityEntry(
        r"codestral",
        (Capability.TEXT, Capability.CODE),
    ),
    ModelCapabilityEntry(
        r"mistral-large",
        (Capability.TEXT, Capability.CODE),
    ),
    ModelCapabilityEntry(
        r"mistral-medium",
        (Capability.TEXT,),
    ),
    ModelCapabilityEntry(
        r"mistral-small",
        (Capability.TEXT, Capability.FAST),
    ),
    ModelCapabilityEntry(
        r"open-mistral-nemo",
        (Capability.TEXT, Capability.FAST),
    ),
    # ── DeepSeek ─────────────────────────────────────────────────────────
    ModelCapabilityEntry(
        r"deepseek-r1",
        (Capability.TEXT, Capability.REASONING),
    ),
    ModelCapabilityEntry(
        r"deepseek-chat|deepseek-v3",
        (Capability.TEXT, Capability.CODE),
    ),
    ModelCapabilityEntry(
        r"deepseek-coder",
        (Capability.TEXT, Capability.CODE),
    ),
    # ── Meta Llama ───────────────────────────────────────────────────────
    ModelCapabilityEntry(
        r"llama-3\.3-70b|llama3-70b",
        (Capability.TEXT, Capability.CODE),
    ),
    ModelCapabilityEntry(
        r"llama-3\.1-405b",
        (Capability.TEXT, Capability.CODE),
    ),
    ModelCapabilityEntry(
        r"llama-3\.1-70b|llama3\.1-70b",
        (Capability.TEXT, Capability.CODE),
    ),
    ModelCapabilityEntry(
        r"llama-3\.1-8b|llama3\.1-8b|llama-3\.1-8b-instant",
        (Capability.TEXT, Capability.FAST),
    ),
    ModelCapabilityEntry(
        r"nemotron",
        (Capability.TEXT, Capability.CODE),
    ),
    # ── Qwen ─────────────────────────────────────────────────────────────
    ModelCapabilityEntry(
        r"qwq",
        (Capability.TEXT, Capability.REASONING),
    ),
    ModelCapabilityEntry(
        r"qwen2\.5-coder|qwen-2\.5-coder",
        (Capability.TEXT, Capability.CODE),
    ),
    ModelCapabilityEntry(
        r"qwen2\.5-72b|qwen-2\.5-72b",
        (Capability.TEXT, Capability.CODE),
    ),
    # ── Microsoft Phi ────────────────────────────────────────────────────
    ModelCapabilityEntry(
        r"phi-4(?!-mini)",
        (Capability.TEXT, Capability.REASONING, Capability.FAST),
    ),
    ModelCapabilityEntry(
        r"phi-4-mini|phi-3",
        (Capability.TEXT, Capability.FAST),
    ),
    # ── Google Gemma ─────────────────────────────────────────────────────
    ModelCapabilityEntry(
        r"gemma",
        (Capability.TEXT, Capability.FAST),
    ),
    # ── Mixtral ──────────────────────────────────────────────────────────
    ModelCapabilityEntry(
        r"mixtral",
        (Capability.TEXT, Capability.CODE),
    ),
    # ── Cerebras ─────────────────────────────────────────────────────────
    ModelCapabilityEntry(
        r"qwen-3-32b",
        (Capability.TEXT, Capability.CODE),
    ),
    # ── Bridge / Copilot ─────────────────────────────────────────────────
    ModelCapabilityEntry(
        r"copilot-gpt-4o",
        (Capability.TEXT, Capability.VISION, Capability.CODE),
    ),
    ModelCapabilityEntry(
        r"copilot-claude",
        (Capability.TEXT, Capability.VISION, Capability.CODE),
    ),
    # ── Compound / special ───────────────────────────────────────────────
    ModelCapabilityEntry(
        r"compound-beta",
        (Capability.TEXT,),
        source="inferred",
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Compiled patterns (lazy init for performance)
# ─────────────────────────────────────────────────────────────────────────────

_COMPILED: list[tuple[re.Pattern[str], ModelCapabilityEntry]] | None = None


def _ensure_compiled() -> list[tuple[re.Pattern[str], ModelCapabilityEntry]]:
    """Compile patterns on first use."""
    global _COMPILED
    if _COMPILED is None:
        _COMPILED = [
            (re.compile(entry.pattern, re.IGNORECASE), entry) for entry in _CAPABILITY_REGISTRY
        ]
    return _COMPILED


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


def get_model_capabilities(
    model_name: str,
) -> tuple[list[Capability], str]:
    """Return ``(capabilities, source)`` for *model_name*.

    If no specific entry matches, returns ``([Capability.TEXT], "inferred")``.
    """
    for pattern, entry in _ensure_compiled():
        if pattern.search(model_name):
            return list(entry.capabilities), entry.source
    # Fallback — every model can at least do text
    return [Capability.TEXT], "inferred"


def has_capability(model_name: str, cap: Capability) -> bool:
    """Check if *model_name* advertises *cap*."""
    caps, _ = get_model_capabilities(model_name)
    return cap in caps


def list_vision_models(
    models: Sequence[str],
) -> list[tuple[str, str]]:
    """Filter *models* to those with :attr:`Capability.VISION`.

    Returns ``[(model_name, source), ...]``.
    """
    result: list[tuple[str, str]] = []
    for m in models:
        caps, src = get_model_capabilities(m)
        if Capability.VISION in caps:
            result.append((m, src))
    return result


def list_models_with_capability(
    models: Sequence[str],
    cap: Capability,
) -> list[tuple[str, str]]:
    """Generic filter — return models that have *cap*.

    Returns ``[(model_name, source), ...]``.
    """
    result: list[tuple[str, str]] = []
    for m in models:
        caps, src = get_model_capabilities(m)
        if cap in caps:
            result.append((m, src))
    return result


def capabilities_label(model_name: str) -> str:
    """Return a compact emoji label string for *model_name*'s capabilities.

    Example: ``"👁 💻 🧠"`` for a model with VISION + CODE + REASONING.
    """
    _EMOJI_MAP: dict[Capability, str] = {
        Capability.VISION: "👁",
        Capability.CODE: "💻",
        Capability.REASONING: "🧠",
        Capability.FAST: "⚡",
        Capability.LOCAL: "🖥",
        Capability.VOICE: "🎙",
    }
    caps, _ = get_model_capabilities(model_name)
    parts = [_EMOJI_MAP[c] for c in caps if c in _EMOJI_MAP]
    return " ".join(parts)
