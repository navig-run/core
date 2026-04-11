"""
navig.agent.prompt_caching — Anthropic prompt cache injection.

Implements two strategies:

1. **system_and_3** (classic): marks the system prompt and the first three user
   messages with ``"cache_control": {"type": "ephemeral"}``.
2. **strategic** (new): uses :class:`CacheBreakpointPlacer` to place up to 4
   breakpoints on the system prompt, tool definitions, skills/context blocks,
   and optionally the stable conversation prefix.  Combined with the
   ``anthropic-beta: prompt-caching-2024-07-31`` header this yields 1-hour
   cache TTL and 80-90 % cost savings on Anthropic calls.

Reference:
  https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching

Usage::

    from navig.agent.prompt_caching import (
        apply_anthropic_cache_control,
        CacheBreakpointPlacer,
        CacheStats,
        ExtendedCacheConfig,
    )

    # Classic strategy
    messages = apply_anthropic_cache_control(messages, strategy="system_and_3")

    # Strategic breakpoint placement (preferred)
    cfg = ExtendedCacheConfig()
    placer = CacheBreakpointPlacer()
    messages = placer.annotate_messages(messages, cfg)

The public functions are pure transformations (no side effects) and **always**
return a copy of the message list — the original is never mutated.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Constants & model support
# ─────────────────────────────────────────────────────────────

#: Anthropic beta header value for extended (1-hour) prompt caching.
EXTENDED_CACHE_BETA_HEADER: str = "prompt-caching-2024-07-31"


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────

# Models that support Anthropic prompt caching
_CACHEABLE_MODELS: frozenset[str] = frozenset(
    {
        "claude-opus-4-5",
        "claude-opus-4",
        "claude-sonnet-4-5",
        "claude-sonnet-4",
        "claude-3-5-sonnet-20241022",
        "claude-3-5-sonnet-20241020",
        "claude-3-5-haiku-20241022",
        "claude-3-opus-20240229",
        "claude-3-haiku-20240307",
        "claude-3-sonnet-20240229",
    }
)


def supports_caching(model: str) -> bool:
    """Return True if *model* is known to support Anthropic prompt caching."""
    model_lower = model.lower()
    # Exact match first
    if model_lower in _CACHEABLE_MODELS:
        return True
    # Prefix match for new model aliases
    for m in _CACHEABLE_MODELS:
        if model_lower.startswith(m) or m.startswith(model_lower):
            return True
    return False


def apply_anthropic_cache_control(
    messages: list[dict[str, Any]],
    strategy: str = "system_and_3",
    ttl: str | None = None,
) -> list[dict[str, Any]]:
    """Inject ``cache_control`` blocks into *messages* for Anthropic caching.

    Parameters
    ----------
    messages:
        OpenAI-style chat messages (``[{"role": ..., "content": ...}, ...]``).
        The system message(s) and user messages are candidates.
    strategy:
        Caching strategy.  Currently only ``"system_and_3"`` is implemented:
        mark the system prompt + first 3 user messages as ephemeral.
    ttl:
        Optional TTL hint.  When ``"1h"``, writes ``{"type": "ephemeral",
        "ttl": 3600}`` (Anthropic extended beta).  Any other truthy value is
        treated as ``"1h"``.  ``None`` writes just ``{"type": "ephemeral"}``.

    Returns
    -------
    list[dict]
        A deep-copied message list with ``cache_control`` injected.
        Returns the original list (not a copy) on any error.
    """
    if not messages:
        return messages

    if strategy == "strategic":
        try:
            cfg = ExtendedCacheConfig()
            placer = CacheBreakpointPlacer()
            return placer.annotate_messages(messages, cfg, ttl=ttl)
        except Exception as exc:
            logger.debug("apply_anthropic_cache_control (strategic) failed: %s", exc)
            return messages

    if strategy != "system_and_3":
        logger.debug("Unsupported cache strategy %r — no cache_control injected", strategy)
        return messages

    try:
        return _apply_system_and_3(messages, ttl=ttl)
    except Exception as exc:
        logger.debug("apply_anthropic_cache_control failed, returning original: %s", exc)
        return messages


# ─────────────────────────────────────────────────────────────
# Strategy: system_and_3
# ─────────────────────────────────────────────────────────────


def _make_cache_block(ttl: str | None) -> dict[str, Any]:
    """Build the ``cache_control`` dictionary."""
    if ttl:
        ttl_secs = 3600 if ttl == "1h" else 3600
        return {"type": "ephemeral", "ttl": ttl_secs}
    return {"type": "ephemeral"}


def _inject_cache(msg: dict[str, Any], cache_block: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of *msg* with ``cache_control`` appended.

    Supports both ``"content": str`` and ``"content": list[dict]`` formats.
    """
    out = copy.deepcopy(msg)
    content = out.get("content", "")

    if isinstance(content, str):
        # Convert to multi-part content block format required by Anthropic
        out["content"] = [
            {"type": "text", "text": content},
            {"type": "text", "text": "", "cache_control": cache_block},
        ]
    elif isinstance(content, list):
        # Append a sentinel block with cache_control
        out["content"] = list(content) + [
            {"type": "text", "text": "", "cache_control": cache_block}
        ]
    # If content is anything else, skip injection
    return out


def _apply_system_and_3(
    messages: list[dict[str, Any]],
    ttl: str | None,
) -> list[dict[str, Any]]:
    """Mark system prompt and first 3 user messages as cacheable."""
    cache_block = _make_cache_block(ttl)
    result: list[dict[str, Any]] = []
    user_tagged = 0

    for msg in messages:
        role = msg.get("role", "")

        if role == "system":
            result.append(_inject_cache(msg, cache_block))

        elif role == "user" and user_tagged < 3:
            result.append(_inject_cache(msg, cache_block))
            user_tagged += 1

        else:
            result.append(copy.deepcopy(msg))

    logger.debug(
        "apply_anthropic_cache_control: %d/%d messages tagged (strategy=%s, ttl=%s)",
        user_tagged + (1 if any(m.get("role") == "system" for m in messages) else 0),
        len(messages),
        "system_and_3",
        ttl,
    )
    return result


# ─────────────────────────────────────────────────────────────
# Config helper
# ─────────────────────────────────────────────────────────────


def get_prompt_cache_config() -> tuple[bool, str | None]:
    """Read ``config.agent.prompt_cache`` and ``config.agent.prompt_cache_ttl``.

    Returns
    -------
    tuple[bool, str | None]
        ``(enabled, ttl)`` where *enabled* is whether caching should be
        applied and *ttl* is the optional TTL string (``"1h"`` or ``None``).
    """
    try:
        from navig.core.config_loader import load_config

        config = load_config()
        agent_cfg = getattr(config, "agent", None)
        if agent_cfg is None:
            return True, None  # default: enabled, no TTL extension

        enabled = getattr(agent_cfg, "prompt_cache", True)
        ttl = getattr(agent_cfg, "prompt_cache_ttl", None)
        return bool(enabled), ttl or None
    except Exception:
        return True, None  # safe default


# ─────────────────────────────────────────────────────────────
# Extended Cache: Config
# ─────────────────────────────────────────────────────────────


@dataclass
class ExtendedCacheConfig:
    """Configuration for Anthropic extended prompt caching.

    Attributes
    ----------
    enabled:
        Master switch.  When ``False`` no breakpoints are placed.
    beta_header:
        Value for the ``anthropic-beta`` request header.
    max_breakpoints:
        Hard upper limit (Anthropic allows 4).
    min_cacheable_tokens:
        Rough character threshold below which caching a block is not
        worthwhile (1 token ≈ 4 chars).
    track_stats:
        Whether :class:`CacheStats` should record usage data.
    cache_system_prompt:
        Place a breakpoint on the ``system`` message.
    cache_tool_definitions:
        Place a breakpoint on messages that look like tool definitions.
    cache_skills_context:
        Place a breakpoint on messages that look like skills/context.
    cache_conversation_prefix:
        Place a breakpoint on the stable (first 80 %) conversation prefix
        for very long sessions.
    """

    enabled: bool = True
    beta_header: str = EXTENDED_CACHE_BETA_HEADER
    max_breakpoints: int = 4
    min_cacheable_tokens: int = 1024
    track_stats: bool = True
    cache_system_prompt: bool = True
    cache_tool_definitions: bool = True
    cache_skills_context: bool = True
    cache_conversation_prefix: bool = False


# ─────────────────────────────────────────────────────────────
# Extended Cache: Breakpoint Placer
# ─────────────────────────────────────────────────────────────


_TOOL_DEF_MARKERS: frozenset[str] = frozenset(
    {"Available tools:", '"type": "function"', "tool_definitions", "function_call"}
)

_SKILLS_MARKERS: frozenset[str] = frozenset(
    {"Skills:", "Context:", "Active skills:", "Background context:"}
)


class CacheBreakpointPlacer:
    """Place up to *max_breakpoints* ``cache_control`` annotations on a
    message list, following a priority order:

    1. System prompt
    2. Tool definitions block
    3. Skills / context injection block
    4. Stable conversation prefix (optional)
    """

    def annotate_messages(
        self,
        messages: list[dict[str, Any]],
        config: ExtendedCacheConfig | None = None,
        *,
        ttl: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return a deep-copied message list with cache breakpoints.

        Parameters
        ----------
        messages:
            OpenAI-style chat messages.
        config:
            Extended cache configuration.  Defaults to :class:`ExtendedCacheConfig()`.
        ttl:
            Optional TTL hint (``"1h"`` for extended beta).

        Returns
        -------
        list[dict]
            Annotated messages (deep copy — originals untouched).
        """
        if config is None:
            config = ExtendedCacheConfig()

        if not config.enabled or not messages:
            return copy.deepcopy(messages)

        cache_block = _make_cache_block(ttl)
        max_bp = config.max_breakpoints
        bp_used = 0

        result: list[dict[str, Any]] = []

        for idx, msg in enumerate(messages):
            role = msg.get("role", "")
            content_str = _content_as_str(msg)

            placed = False

            # Priority 1 — system prompt
            if role == "system" and config.cache_system_prompt and bp_used < max_bp or (
                config.cache_tool_definitions
                and bp_used < max_bp
                and _matches_markers(content_str, _TOOL_DEF_MARKERS)
            ) or (
                config.cache_skills_context
                and bp_used < max_bp
                and _matches_markers(content_str, _SKILLS_MARKERS)
            ) or (
                config.cache_conversation_prefix
                and bp_used < max_bp
                and _is_prefix_boundary(idx, len(messages))
            ):
                result.append(_inject_cache(msg, cache_block))
                bp_used += 1
                placed = True

            if not placed:
                result.append(copy.deepcopy(msg))

        logger.debug(
            "CacheBreakpointPlacer: %d breakpoints placed across %d messages",
            bp_used,
            len(messages),
        )
        return result


# ─────────────────────────────────────────────────────────────
# Extended Cache: Statistics Tracker
# ─────────────────────────────────────────────────────────────


@dataclass
class CacheStats:
    """Accumulates Anthropic cache hit / miss statistics across API calls.

    Usage::

        stats = CacheStats()
        # After each API call:
        stats.record_response(response_usage_dict)
        print(stats.summary())
    """

    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_input_tokens: int = 0
    api_calls: int = 0

    # ── Properties ──────────────────────────────────────────

    @property
    def hit_rate(self) -> float:
        """Fraction of input tokens served from cache (0.0 – 1.0)."""
        if self.total_input_tokens == 0:
            return 0.0
        return self.cache_read_tokens / self.total_input_tokens

    @property
    def savings_estimate(self) -> float:
        """Estimated USD saved from cache reads (Anthropic input pricing).

        Assumes $3.00 / 1M input tokens normal price and $0.30 / 1M for
        cache reads (90 % discount).
        """
        normal_cost = self.cache_read_tokens * 3.0 / 1_000_000
        cached_cost = self.cache_read_tokens * 0.3 / 1_000_000
        return normal_cost - cached_cost

    # ── Recording ───────────────────────────────────────────

    def record_response(self, usage: dict[str, Any]) -> None:
        """Ingest a single API response ``usage`` dict.

        Expected keys (all optional — missing treated as 0):

        - ``input_tokens``
        - ``cache_creation_input_tokens``
        - ``cache_read_input_tokens``
        """
        self.api_calls += 1
        
        try:
            self.total_input_tokens += int(usage.get("input_tokens", 0))
        except (ValueError, TypeError):
            pass
            
        try:
            self.cache_creation_tokens += int(usage.get("cache_creation_input_tokens", 0))
        except (ValueError, TypeError):
            pass
            
        try:
            self.cache_read_tokens += int(usage.get("cache_read_input_tokens", 0))
        except (ValueError, TypeError):
            pass

    def reset(self) -> None:
        """Reset all counters to zero."""
        self.cache_creation_tokens = 0
        self.cache_read_tokens = 0
        self.total_input_tokens = 0
        self.api_calls = 0

    def summary(self) -> dict[str, Any]:
        """Return a human-readable summary dict."""
        return {
            "api_calls": self.api_calls,
            "total_input_tokens": self.total_input_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "hit_rate": f"{self.hit_rate:.1%}",
            "estimated_savings": f"${self.savings_estimate:.4f}",
        }


# ─────────────────────────────────────────────────────────────
# Utility: detect cache breakpoints
# ─────────────────────────────────────────────────────────────


def has_cache_breakpoint(msg: dict[str, Any]) -> bool:
    """Return ``True`` if *msg* contains a ``cache_control`` annotation.

    Useful for compaction / summary passes that should preserve cached blocks.
    """
    content = msg.get("content", "")
    if isinstance(content, list):
        return any(
            isinstance(block, dict) and "cache_control" in block
            for block in content
        )
    return False


# ─────────────────────────────────────────────────────────────
# Private helpers (strategic placer)
# ─────────────────────────────────────────────────────────────


def _content_as_str(msg: dict[str, Any]) -> str:
    """Flatten message content to a plain string for marker detection."""
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)


def _matches_markers(text: str, markers: frozenset[str]) -> bool:
    """Return ``True`` if *text* contains any of the *markers*."""
    return any(m in text for m in markers)


def _is_prefix_boundary(idx: int, total: int) -> bool:
    """Return ``True`` if *idx* is the last message in the first 80 %."""
    if total < 5:
        return False  # too short to bother
    boundary = int(total * 0.8) - 1
    return idx == boundary
