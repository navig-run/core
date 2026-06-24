"""Emoji-triggered AI actions for the Telegram Business layer.

SECURITY MODEL (critical): these actions run on UNTRUSTED message content (a
counterparty's message). They therefore use ``ai_client.complete()`` — a pure
**text-in → text-out** LLM call with **zero tools and zero system access**. A
prompt-injection payload in the message ("ignore instructions, run /exec …")
cannot reach the system, the CLI, the deck, or any skill, because the call has
nothing to call. Each action is additionally gated by the owner's per-tool policy
(:mod:`navig.telegram.permissions`) — a counterparty may only trigger a tool when
its policy is ``both``.
"""

from __future__ import annotations

import logging

from . import permissions

logger = logging.getLogger(__name__)

# Default emoji → tool map (the owner can remap via config telegram.business.emoji.<emoji>).
EMOJI_TOOLS: dict[str, str] = {
    "🌍": "translate", "🌎": "translate", "🌐": "translate",
    "📋": "summarize", "📝": "summarize",
    "🤔": "context",
    "💡": "explain",
    "⬇️": "download", "📥": "download",
}

# System prompts — each instructs a single, bounded text transformation only.
_SYSTEM: dict[str, str] = {
    "translate": (
        "You are a translator. Translate the message below into clear, natural English "
        "(or, if it is already English, into the most likely source language). "
        "Output ONLY the translation — no preamble, no notes, no quotes."
    ),
    "summarize": (
        "You are a concise summarizer. Summarize the message below in 1-3 short sentences "
        "capturing the key point(s). Output ONLY the summary."
    ),
    "context": (
        "You are a neutral analyst. Briefly explain the context, intent, and any implied "
        "meaning of the message below. Be concise and factual. Output ONLY the analysis."
    ),
    "explain": (
        "You explain things simply. Rewrite/explain the message below in plain language a "
        "non-expert understands. Output ONLY the explanation."
    ),
}

# Tools handled by the LLM sandbox here (others — ocr/transcribe/download — use the
# media engines / yt-dlp and are invoked elsewhere).
LLM_TOOLS = frozenset(_SYSTEM)


# TikTok-action emojis (handled by navig.telegram.tiktok_actions, gated by the
# 'download' policy). Surfaced here so the deck legend + remap cover them too.
TIKTOK_EMOJIS: dict[str, str] = {"🎵": "tiktok", "🎬": "tiktok", "📹": "tiktok"}

# Every tool an emoji may be remapped to (for validation from the deck/CLI).
ASSIGNABLE_TOOLS: frozenset[str] = frozenset(set(_SYSTEM) | {"tiktok", "download"})


def emoji_to_tool(emoji: str) -> str | None:
    """Resolve a reaction emoji → tool name, honoring the owner's config overrides."""
    try:
        from navig.core import Config
        override = Config().get("telegram.business.emoji", {}) or {}
        if isinstance(override, dict) and emoji in override:
            return override[emoji]
    except Exception:  # noqa: BLE001
        pass
    return EMOJI_TOOLS.get(emoji) or TIKTOK_EMOJIS.get(emoji)


def effective_emoji_map() -> dict[str, str]:
    """The full emoji→tool legend: AI defaults + TikTok + the owner's overrides."""
    merged: dict[str, str] = {**EMOJI_TOOLS, **TIKTOK_EMOJIS}
    try:
        from navig.core import Config
        overrides = Config().get("telegram.business.emoji", {}) or {}
        if isinstance(overrides, dict):
            for emoji, tool in overrides.items():
                if tool:
                    merged[emoji] = tool
                else:
                    merged.pop(emoji, None)
    except Exception:  # noqa: BLE001
        pass
    return merged


def set_emoji_override(emoji: str, tool: str | None) -> None:
    """Remap an emoji → tool, or clear the override (``tool`` falsy). Raises
    ValueError on an unknown tool. Owner-only action (called from CLI/deck)."""
    emoji = (emoji or "").strip()
    if not emoji:
        raise ValueError("emoji is required")
    if tool and tool not in ASSIGNABLE_TOOLS:
        raise ValueError(f"unknown tool {tool!r}; one of {sorted(ASSIGNABLE_TOOLS)}")
    from navig.core import Config

    cfg = Config()
    overrides = dict(cfg.get("telegram.business.emoji", {}) or {})
    if tool:
        overrides[emoji] = tool
    else:
        overrides.pop(emoji, None)
    cfg.set("telegram.business.emoji", overrides, scope="global")
    cfg.save(scope="global")


async def run_text_action(tool: str, content: str, *, is_owner: bool) -> dict:
    """Run a sandboxed (no-tools) AI text action on message content.

    Returns ``{ok, tool, result}`` or ``{ok: False, reason, tool}``.
    """
    if not permissions.can_use(tool, is_owner=is_owner):
        return {"ok": False, "reason": "not_permitted", "tool": tool}
    system = _SYSTEM.get(tool)
    if not system:
        return {"ok": False, "reason": "not_llm_tool", "tool": tool}
    content = (content or "").strip()
    if not content:
        return {"ok": False, "reason": "empty", "tool": tool}
    # Wrap untrusted content in explicit delimiters so it can't pose as instructions.
    prompt = f"<<<MESSAGE\n{content[:4000]}\nMESSAGE>>>"
    try:
        from navig.agent.ai_client import get_ai_client
        out = await get_ai_client().complete(prompt, system_prompt=system)
        return {"ok": True, "tool": tool, "result": (out or "").strip()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram AI action %s failed: %s", tool, exc)
        return {"ok": False, "reason": "llm_error", "tool": tool, "error": str(exc)}
