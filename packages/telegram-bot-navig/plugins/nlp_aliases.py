"""
plugins/nlp_aliases.py — Multilingual NLP trigger aliases (EN / FR / RU).
Passive: reply to any message (or write inline) with a trigger word.
Skill  : skills/nlp_aliases.md
AI     : routes through NAVIG's LLM layer (vault-aware, provider-agnostic).
         Falls back to a direct API call only when the core is unavailable.
"""

from __future__ import annotations

import re

from telegram import Update
from telegram.ext import ContextTypes

try:
    from navig.ui.icons import icon as _ni
except ImportError:  # running outside NAVIG runtime
    def _ni(name: str) -> str:  # type: ignore[misc]  # noqa: E302
        _fb = {"search": "🔍", "pencil": "✏", "improve": "⬆", "puzzle": "🧩",
               "globe": "🌐", "idea": "💡", "note": "📝", "palette": "🎨",
               "clipboard": "📋", "brain": "🧠"}
        return _fb.get(name, "?")

try:
    from plugin_base import BotPlugin, PluginMeta
except ImportError:
    from ..plugin_base import BotPlugin, PluginMeta  # type: ignore

_ACTIONS: dict[str, dict] = {
    "explain": {
        "aliases": ["explain", "explique", "объясни", "explain this"],
        "prompt": "Explain clearly and concisely:\n\n{text}",
        "emoji": _ni("search"),
        "label": "Explanation",
    },
    "correct": {
        "aliases": ["correct", "corrige", "исправь", "fix this"],
        "prompt": "Correct grammar and spelling. Show corrected text only:\n\n{text}",
        "emoji": _ni("pencil"),
        "label": "Correction",
    },
    "improve": {
        "aliases": ["improve", "améliore", "улучши", "make it better"],
        "prompt": "Improve clarity and style:\n\n{text}",
        "emoji": _ni("improve"),
        "label": "Improved",
    },
    "simplify": {
        "aliases": ["simplify", "simplifie", "упрости", "make it simple"],
        "prompt": "Simplify so a 12-year-old understands:\n\n{text}",
        "emoji": _ni("puzzle"),
        "label": "Simplified",
    },
    "translate": {
        "aliases": ["translate", "traduis", "переведи", "перевод"],
        "prompt": "Translate to English if non-English, else to Russian:\n\n{text}",
        "emoji": _ni("globe"),
        "label": "Translation",
    },
    "brainstorm": {
        "aliases": ["brainstorm", "идеи", "remue-méninges", "give ideas"],
        "prompt": "Brainstorm 5 creative ideas about:\n\n{text}",
        "emoji": _ni("idea"),
        "label": "Ideas",
    },
    "proofread": {
        "aliases": ["proofread", "proof", "relis", "проверь"],
        "prompt": "Proofread and list all corrections:\n\n{text}",
        "emoji": _ni("note"),
        "label": "Proofread",
    },
    "creative": {
        "aliases": ["creative", "créatif", "креатив", "make it creative"],
        "prompt": "Rewrite in a creative, engaging way:\n\n{text}",
        "emoji": _ni("palette"),
        "label": "Creative",
    },
    "summary": {
        "aliases": ["summary", "summarize", "résumé", "резюме", "tl;dr", "кратко"],
        "prompt": "Summarize in 3-5 sentences:\n\n{text}",
        "emoji": _ni("clipboard"),
        "label": "Summary",
    },
    "context": {
        "aliases": ["context", "contexte", "контекст", "analyse", "analyze", "анализ"],
        "prompt": "Analyze: identify sentiment, key points, and notable observations:\n\n{text}",
        "emoji": _ni("brain"),
        "label": "Analysis",
    },
}

_ALL = sorted(
    [a for v in _ACTIONS.values() for a in v["aliases"]], key=len, reverse=True
)
_PAT = r"^(" + "|".join(re.escape(a) for a in _ALL) + r")[\s:,]+(.+)?$"


def _detect(text: str):
    m = re.match(_PAT, text.strip(), re.I | re.S)
    if not m:
        return None
    trigger = m.group(1).lower()
    rest = (m.group(2) or "").strip()
    for k, v in _ACTIONS.items():
        if trigger in [a.lower() for a in v["aliases"]]:
            return k, rest
    return None


def _call_ai_via_navig(prompt: str) -> str | None:
    """Route AI calls through NAVIG core (vault-aware, provider-agnostic)."""
    # Primary: canonical typed orchestrator.
    try:
        from navig.llm_generate import run_llm  # type: ignore[import]

        result = run_llm(
            messages=[{"role": "user", "content": prompt}],
            user_input=prompt,
            mode="chat",
            max_tokens=800,
        )
        content = getattr(result, "content", "")
        if isinstance(content, str) and content.strip():
            return content.strip()
    except Exception:
        pass

    # Secondary: legacy NAVIG adapter (still vault/config aware).
    try:
        from navig.ai import ask_ai_with_context  # type: ignore[import]

        content = ask_ai_with_context(prompt=prompt, model=None)
        if isinstance(content, str) and content.strip():
            return content.strip()
    except Exception:
        pass

    # No direct provider HTTP fallback here: keep one source of truth in NAVIG core.
    return None


class NLPAliasPlugin(BotPlugin):
    """EN/FR/RU trigger aliases: explain·correct·translate·summarize + 6 more."""

    @property
    def meta(self):
        return PluginMeta(
            "nlp_aliases",
            "Multilingual AI triggers: explain/correct/translate/etc. in EN/FR/RU.",
            "1.0.0",
        )

    @property
    def command(self):
        return ""  # passive only

    @property
    def passive_patterns(self):
        return [_PAT]

    async def handle(self, update, context):
        pass

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        import asyncio

        msg = update.message
        text = msg.text or ""
        res = _detect(text)
        if not res:
            return
        key, inline = res
        act = _ACTIONS[key]
        body = inline
        if not body and msg.reply_to_message:
            body = msg.reply_to_message.text or msg.reply_to_message.caption or ""
        if not body:
            await msg.reply_text(
                f"{act['emoji']} *{act['label']}*\n\nReply to a message with `{act['aliases'][0]}`, "
                f"or write:\n`{act['aliases'][0]} <your text>`",
                parse_mode="Markdown",
            )
            return
        status = await msg.reply_text(f"{act['emoji']} Processing…")
        prompt = act["prompt"].format(text=body)
        resp = await asyncio.to_thread(_call_ai_via_navig, prompt)
        if resp:
            await status.edit_text(
                f"{act['emoji']} *{act['label']}:*\n\n{resp}", parse_mode="Markdown"
            )
            return
        await status.edit_text(
            f"{act['emoji']} *{act['label']} detected* ✓\n\n"
            f"Target: _{body[:200]}_\n\n"
            "{_ni("warn")} AI not configured. Run `navig init` to set up your AI provider, "
            "then try again.",
            parse_mode="Markdown",
        )


def create():
    return NLPAliasPlugin()
