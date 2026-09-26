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
    # Reads back a free-form journal entry the operator just wrote. Deliberately
    # NOT a clinical instrument: it names what is on the page and asks one
    # question, because the value is being read attentively, not being assessed.
    #
    # The constraints are the design. "No diagnosis, no disorder names" keeps a
    # tired evening sentence from being handed back as a condition. "Quote their
    # own words" keeps it anchored to what was actually written instead of
    # generic counsel that would fit anybody. "One question, and only if it earns
    # its place" stops every entry ending in homework.
    #
    # Same language as the input, because this is the operator talking to
    # themselves and a reply in the wrong language breaks that completely.
    "reflect": (
        "You are a thoughtful, warm reader of someone's private daily journal. "
        "They wrote freely about their day. Read it the way an attentive friend "
        "with psychological training would — closely, without judging.\n\n"
        "Write a short reflection (3-6 sentences) that:\n"
        "- names the emotional thread running through the entry, in plain words;\n"
        "- quotes or echoes one or two of THEIR OWN phrases, so they can see you "
        "actually read it;\n"
        "- notes any pattern worth seeing: what they returned to, what they "
        "described at length versus in passing, what they seemed to step around;\n"
        "- ends with at most ONE open question, and only if a real one is there. "
        "Do not manufacture a question to have one.\n\n"
        "Hard limits: no diagnosis, no disorder or syndrome names, no clinical "
        "labels, no advice unless they explicitly asked for it, no praise that "
        "would fit any entry. Do not tell them how to feel. If the entry is brief "
        "or flat, say something brief — do not inflate it.\n\n"
        "If the entry contains signs of crisis or self-harm, do not analyse: "
        "respond briefly, warmly and directly, and say that talking to someone "
        "they trust or a professional is worth it right now.\n\n"
        "Write in the SAME LANGUAGE as the entry. Output ONLY the reflection — "
        "no preamble, no heading, no bullet markers."
    ),
    # The week's entries read together. Same limits as `reflect`, different
    # question: not "what is on this page" but "what moved across these pages".
    # Patterns are the only thing a weekly read can see that a daily one cannot,
    # so that is what it is asked for — and nothing else, because a week of
    # someone's writing is exactly where a model is most tempted to explain
    # them to themselves.
    "reflect_week": (
        "You are a thoughtful, warm reader of someone's private daily journal. "
        "Below are their entries for one week, each under its date. Read them "
        "together, the way an attentive friend with psychological training "
        "would read a week of letters — closely, without judging.\n\n"
        "Write a short reflection (5-8 sentences) about the WEEK, not about any "
        "one day:\n"
        "- name the thread that runs through the week, in plain words;\n"
        "- say what shifted from the start of the week to the end, if anything "
        "did — and say plainly if it did not;\n"
        "- quote or echo two or three of THEIR OWN phrases from different days, "
        "so they can see the pattern in their own words;\n"
        "- notice what they kept returning to, and what they mentioned once and "
        "then never again;\n"
        "- end with at most ONE open question about the coming week, and only "
        "if a real one is there.\n\n"
        "Hard limits: no diagnosis, no disorder or syndrome names, no clinical "
        "labels, no advice unless they explicitly asked for it, no praise that "
        "would fit any week. Do not tell them how to feel. Do not summarise the "
        "days one by one — that is a list, not a reflection. If the entries are "
        "brief or few, say something brief.\n\n"
        "If any entry contains signs of crisis or self-harm, do not analyse: "
        "respond briefly, warmly and directly, and say that talking to someone "
        "they trust or a professional is worth it right now.\n\n"
        "Write in the SAME LANGUAGE as the entries. Output ONLY the reflection — "
        "no preamble, no heading, no bullet markers."
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


#: Default content budget. Sized for the original job — reacting to ONE Telegram
#: message, which is at most 4096 characters by Telegram's own limit — so for
#: that job it never cuts anything. A caller handing over something longer (a
#: journal entry, a week of them) must say so, because the cut is otherwise
#: silent: the model answers about the part it saw and nothing marks the seam.
DEFAULT_MAX_CHARS = 4000


async def run_text_action(
    tool: str,
    content: str,
    *,
    is_owner: bool,
    arg: str = "",
    max_chars: int = DEFAULT_MAX_CHARS,
    mode: str = "chat",
    model_override: str | None = None,
) -> dict:
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
    if tool == "translate":
        # Target: the explicit argument, else the operator's configured language.
        # The default prompt hardcodes English, which is the wrong answer for an
        # operator who told navig they read Russian — 🌍 on a Russian message
        # would translate it *away* from the language they asked for. With
        # nothing configured (auto) the original English-or-source behaviour
        # stands, so an unconfigured install is unchanged.
        from navig.core.language import resolve_language

        target = arg or (resolve_language() or "")
        if target:
            system = (
                f"You are a translator. Translate the message below into {target}. "
                "If it is already in that language, translate it into English instead. "
                "Output ONLY the translation — no preamble, no notes, no quotes."
            )
    content = (content or "").strip()
    if not content:
        return {"ok": False, "reason": "empty", "tool": tool}
    # ⚠ A cut here is invisible to the caller AND to the model, which answers
    # confidently about the part it saw. Measured with the weekly journal
    # read-back before this budget existed: a week of ~240-word entries is
    # ~7,900 chars, and the 4,000 cut kept Monday–Thursday and dropped
    # Friday–Sunday — the reflection was then asked "what shifted by Sunday"
    # having never seen Sunday. So the cut is logged, and marked in the text
    # so the model at least knows the seam is there.
    if len(content) > max_chars:
        logger.warning(
            "telegram AI action %s: content cut from %d to %d chars — the model "
            "will not see the end of it",
            tool,
            len(content),
            max_chars,
        )
        content = content[:max_chars] + "\n[… cut here — the rest was not shown to you]"
    # Wrap untrusted content in explicit delimiters so it can't pose as instructions.
    user_msg = f"<<<MESSAGE\n{content}\nMESSAGE>>>"
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

        # `mode` is the router hint ("chat" → small_talk, the fast tier, right for
        # reacting to one message); `model_override` bypasses the router entirely
        # for callers whose content the operator may want pinned somewhere
        # specific — the journal, whose `journal.model` key lands here.
        out = await asyncio.to_thread(
            llm_generate, messages, mode=mode, model_override=model_override, timeout=60.0
        )
        out = (out or "").strip()
        if not out:
            return {"ok": False, "reason": "empty_result", "tool": tool}
        return {"ok": True, "tool": tool, "result": out}
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram AI action %s failed: %s", tool, exc)
        return {"ok": False, "reason": "llm_error", "tool": tool, "error": str(exc)}
