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
        "capturing the key point(s), in the same language as the input. Output ONLY the summary."
    ),
    "context": (
        "You are a neutral analyst. Briefly explain the context, intent, and any implied "
        "meaning of the message below. Be concise and factual, in the same language as the "
        "message. Output ONLY the analysis."
    ),
    "explain": (
        "You explain things simply. Rewrite/explain the message below in plain language a "
        "non-expert understands, in the same language as the message. Output ONLY the explanation."
    ),
    # ── Writing transforms (adapted from the AI-Neuromancer prompt library) ──
    # Every one is a bounded text-in → text-out op: same language as the input,
    # preserve meaning, output ONLY the transformed text (no quotes / preamble).
    "improve": (
        "You are an expert editor. Improve the message below — clarity, flow, grammar, "
        "and word choice — while preserving its original meaning, tone, language, and any "
        "intentional style. Output ONLY the improved text."
    ),
    "fix": (
        "You are a proofreader. Correct ONLY spelling, grammar, and punctuation in the "
        "message below. Do not change wording, tone, meaning, or style beyond fixing "
        "mechanics. Same language as the input. Output ONLY the corrected text."
    ),
    "shorten": (
        "You are a concise editor. Make the message below shorter and tighter while keeping "
        "all key information and the original language. Output ONLY the shortened text."
    ),
    "expand": (
        "You are a writing assistant. Expand the message below with useful detail and "
        "context (roughly twice the length) while preserving its meaning, tone, and "
        "language. Output ONLY the expanded text."
    ),
    "professional": (
        "You are a business-writing assistant. Rewrite the message below in a clear, "
        "polished, professional tone, preserving its meaning and language. Output ONLY the "
        "rewritten text."
    ),
    "casual": (
        "You are a friendly writing assistant. Rewrite the message below in a relaxed, "
        "natural, casual tone, preserving its meaning and language. Output ONLY the "
        "rewritten text."
    ),
    "persuasive": (
        "You are a persuasion expert. Rewrite the message below to be more compelling and "
        "persuasive while preserving its meaning and language. Output ONLY the rewritten text."
    ),
    "rewrite": (
        "You are a writing assistant. Rewrite the message below using different structure "
        "and wording while preserving its exact meaning, tone, and language. Output ONLY "
        "the rewritten text."
    ),
    "outline": (
        "You are a structuring assistant. Turn the message below into a clear, hierarchical "
        "bullet-point outline of its key ideas, in the same language. Output ONLY the outline."
    ),
    "keypoints": (
        "You extract key points. List the main points of the message below as concise "
        "bullets, in the same language. Output ONLY the bullet list."
    ),
    "actions": (
        "You extract action items. List every task, to-do, or next step implied by the "
        "message below as a checklist (one per line, prefixed with '- [ ] '), in the same "
        "language. If there are none, say so briefly. Output ONLY the list."
    ),
    "debug": (
        "You are a code-debugging assistant. Identify bugs or errors in the code/text below, "
        "explain each briefly, and provide the corrected version. Output the explanation "
        "then a fenced code block with the fix."
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

# NOTE: reply-keyword → action parsing lives in navig.telegram.reply_actions
# (the single source of truth for the keyword trigger that replaced reactions).


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


async def run_text_action(tool: str, content: str, *, is_owner: bool, arg: str = "") -> dict:
    """Run a sandboxed (no-tools) AI text action on message content.

    ``arg`` is an optional parameter for arg-aware tools — currently ``translate``,
    where it is the target language (e.g. ``"translate fr"`` → French).

    Returns ``{ok, tool, result}`` or ``{ok: False, reason, tool}``.
    """
    if not permissions.can_use(tool, is_owner=is_owner):
        return {"ok": False, "reason": "not_permitted", "tool": tool}
    system = _SYSTEM.get(tool)
    if not system:
        return {"ok": False, "reason": "not_llm_tool", "tool": tool}
    arg = (arg or "").strip()
    if tool == "translate" and arg:
        system = (
            f"You are a translator. Translate the message below into {arg}. "
            "Output ONLY the translation — no preamble, no notes, no quotes."
        )
    content = (content or "").strip()
    if not content:
        return {"ok": False, "reason": "empty", "tool": tool}
    # Wrap untrusted content in explicit delimiters so it can't pose as instructions.
    user_msg = f"<<<MESSAGE\n{content[:4000]}\nMESSAGE>>>"
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]
    try:
        # Route through llm_generate (the unified llm_router path the conversational
        # agent + Studio use) — NOT the legacy AIClient, which has its own provider
        # detection and reports "no provider" even when the brain's model is set.
        import asyncio

        from navig.llm.generate import llm_generate

        out = await asyncio.to_thread(llm_generate, messages, mode="chat", timeout=60.0)
        out = (out or "").strip()
        if not out:
            return {"ok": False, "reason": "empty_result", "tool": tool}
        return {"ok": True, "tool": tool, "result": out}
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram AI action %s failed: %s", tool, exc)
        return {"ok": False, "reason": "llm_error", "tool": tool, "error": str(exc)}
