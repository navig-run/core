"""
Provider Model Capabilities Registry

Static capability metadata for known models, replacing substring heuristics
with verified capability flags.  Used by discovery.py, vision routing, and
the Telegram provider control surface.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

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
    # ── Current families (2026) — first, so they win over older patterns ─
    # Measured 2026-10-15: every id the operator actually holds (claude-*-4-x,
    # gpt-5*, grok-4*, gpt-oss-*) fell through to a bare TEXT guess, so nothing
    # could be routed by speed. Tags follow the vendors' published model cards;
    # marked "inferred" because NAVIG did not probe each capability itself.
    ModelCapabilityEntry(
        r"claude-(?:opus|sonnet)-4|claude-(?:opus|sonnet)-4[.-]\d",
        (Capability.TEXT, Capability.VISION, Capability.CODE, Capability.REASONING),
        source="inferred",
    ),
    ModelCapabilityEntry(
        r"claude-haiku-4",
        (Capability.TEXT, Capability.VISION, Capability.FAST),
        source="inferred",
    ),
    ModelCapabilityEntry(
        r"gpt-5[.\d]*-(?:mini|nano)",
        (Capability.TEXT, Capability.VISION, Capability.FAST, Capability.REASONING),
        source="inferred",
    ),
    ModelCapabilityEntry(
        r"gpt-5[.\d]*-codex",
        (Capability.TEXT, Capability.CODE, Capability.REASONING),
        source="inferred",
    ),
    ModelCapabilityEntry(
        r"gpt-5(?![.\d]*-(?:mini|nano|codex))",
        (Capability.TEXT, Capability.VISION, Capability.CODE, Capability.REASONING),
        source="inferred",
    ),
    ModelCapabilityEntry(
        r"grok-4[.\d-]*(?:fast|non-reasoning|mini)",
        (Capability.TEXT, Capability.VISION, Capability.FAST),
        source="inferred",
    ),
    ModelCapabilityEntry(
        r"grok-4",
        (Capability.TEXT, Capability.VISION, Capability.CODE, Capability.REASONING),
        source="inferred",
    ),
    ModelCapabilityEntry(
        r"gpt-oss-(?:safeguard-)?20b",
        (Capability.TEXT, Capability.REASONING, Capability.FAST),
        source="inferred",
    ),
    ModelCapabilityEntry(
        r"gpt-oss-120b",
        (Capability.TEXT, Capability.CODE, Capability.REASONING),
        source="inferred",
    ),
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


# Families a provider's ``GET /v1/models`` lists that cannot answer a chat turn:
# speech (TTS/ASR), embeddings, rerankers/reward/safety classifiers, image/video
# generators, realtime (websocket-only) and legacy completion-only ids. Measured
# 2026-10-15 against the operator's live catalogs: OpenAI listed 137 ids incl.
# tts-1 / whisper-1 / text-embedding-3-*; Groq listed orpheus (TTS) and
# llama-prompt-guard (a classifier) — each one a picker entry that fails every turn.
_NON_CHAT = re.compile(
    # `guard` but not `safeguard`: groq's openai/gpt-oss-safeguard-20b answered a
    # real 1-token chat call in the registry audit (2026-09-26) — it can chat.
    r"embed|rerank|retriev|reward|moderation|(?<!safe)guard"
    r"|(?<![a-z])tts(?![a-z])|text-to-speech|orpheus|whisper|transcrib|parakeet|canary"
    r"|dall-e|gpt-image|imagen|stable-diffusion|sdxl|(?<![a-z])flux|(?<![a-z])sora"
    r"|realtime|davinci-002|babbage-002|computer-use",
    re.IGNORECASE,
)


def is_chat_model(model_id: str) -> bool:
    """True unless *model_id* belongs to a family that cannot hold a chat turn.

    A denylist on purpose: a provider's catalog grows faster than any allowlist,
    and an unknown id is far more often a new chat model than a new TTS one.
    Use it to filter DISCOVERED ids — never to drop an id a user or the
    registry chose explicitly.
    """
    return bool(model_id) and not _NON_CHAT.search(model_id)


def intent_models(saved: Sequence[str], default: str | None) -> dict[str, str]:
    """Which model to use per intent when the user leaves the picker on Auto.

    ``act`` — driving a page needs the strongest model: the connection's default
    (the id that answered its connect-time probe), else the first saved id.
    ``ask`` — answering about a page favours speed: the first SAVED id tagged
    FAST, else ``act``. Saved ids only: they answered a real call when the
    connection was made; a catalog id never has, and may need access the key
    lacks.
    """
    ids = [m for m in saved if m and is_chat_model(m)]
    act = default if default and is_chat_model(default) else (ids[0] if ids else "")
    ask = next((m for m in ids if has_capability(m, Capability.FAST)), "") or act
    return {"ask": ask, "act": act}


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
