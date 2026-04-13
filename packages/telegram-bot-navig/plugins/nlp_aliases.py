"""
plugins/nlp_aliases.py — Multilingual NLP trigger aliases (EN / FR / RU).
Passive: reply to any message (or write inline) with a trigger word.
Skill  : skills/nlp_aliases.md
AI     : delegates to navig.llm_generate.llm_generate() which transparently
         resolves credentials from the NAVIG Vault, config manager, and the
         LLM mode router (supports OpenRouter, OpenAI, Ollama, NVIDIA NIMs,
         and any other configured provider — including local instances).
"""

from __future__ import annotations

import re

from telegram import Update
from telegram.ext import ContextTypes

try:
    from plugin_base import BotPlugin, PluginMeta
except ImportError:
    from ..plugin_base import BotPlugin, PluginMeta  # type: ignore

_ACTIONS: dict[str, dict] = {
    "explain": {
        "aliases": ["explain", "explique", "объясни", "explain this"],
        "prompt": "Explain clearly and concisely:\n\n{text}",
        "emoji": "🔍",
        "label": "Explanation",
    },
    "correct": {
        "aliases": ["correct", "corrige", "исправь", "fix this"],
        "prompt": "Correct grammar and spelling. Show corrected text only:\n\n{text}",
        "emoji": "✏️",
        "label": "Correction",
    },
    "improve": {
        "aliases": ["improve", "améliore", "улучши", "make it better"],
        "prompt": "Improve clarity and style:\n\n{text}",
        "emoji": "⬆️",
        "label": "Improved",
    },
    "simplify": {
        "aliases": ["simplify", "simplifie", "упрости", "make it simple"],
        "prompt": "Simplify so a 12-year-old understands:\n\n{text}",
        "emoji": "🧩",
        "label": "Simplified",
    },
    "translate": {
        "aliases": ["translate", "traduis", "переведи", "перевод"],
        "prompt": "Translate to English if non-English, else to Russian:\n\n{text}",
        "emoji": "🌐",
        "label": "Translation",
    },
    "brainstorm": {
        "aliases": ["brainstorm", "идеи", "remue-méninges", "give ideas"],
        "prompt": "Brainstorm 5 creative ideas about:\n\n{text}",
        "emoji": "💡",
        "label": "Ideas",
    },
    "proofread": {
        "aliases": ["proofread", "proof", "relis", "проверь"],
        "prompt": "Proofread and list all corrections:\n\n{text}",
        "emoji": "📝",
        "label": "Proofread",
    },
    "creative": {
        "aliases": ["creative", "créatif", "креатив", "make it creative"],
        "prompt": "Rewrite in a creative, engaging way:\n\n{text}",
        "emoji": "🎨",
        "label": "Creative",
    },
    "summary": {
        "aliases": ["summary", "summarize", "résumé", "резюме", "tl;dr", "кратко"],
        "prompt": "Summarize in 3-5 sentences:\n\n{text}",
        "emoji": "📋",
        "label": "Summary",
    },
    "context": {
        "aliases": ["context", "contexte", "контекст", "analyse", "analyze", "анализ"],
        "prompt": "Analyze: identify sentiment, key points, and notable observations:\n\n{text}",
        "emoji": "🧠",
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


def _call_llm(prompt: str) -> str | None:
    """
    Delegate to navig.llm_generate.llm_generate() which transparently resolves
    credentials via the NAVIG Vault and routes through the LLM mode router.
    Supports all configured providers: OpenRouter, OpenAI, Ollama, NVIDIA NIMs, etc.

    Returns the response text, or None if the call fails or no provider is available.
    """
    try:
        from navig.llm_generate import llm_generate

        return llm_generate(
            messages=[{"role": "user", "content": prompt}],
            mode="big_tasks",
            max_tokens=800,
        )
    except Exception:  # noqa: BLE001 — best-effort; caller shows fallback message
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
                f"{act['emoji']} <b>{act['label']}</b>\n\nReply to a message with <code>{act['aliases'][0]}</code>, "
                f"or write:\n<code>{act['aliases'][0]} &lt;your text&gt;</code>",
                parse_mode="HTML",
            )
            return
        status = await msg.reply_text(f"{act['emoji']} Processing…")
        prompt = act["prompt"].format(text=body)
        resp = await asyncio.to_thread(_call_llm, prompt)
        if resp:
            await status.edit_text(
                f"{act['emoji']} <b>{act['label']}:</b>\n\n{resp}", parse_mode="HTML"
            )
            return
        await status.edit_text(
            f"{act['emoji']} <b>{act['label']} detected</b> ✓\n\n"
            f"Target: <i>{body[:200]}</i>\n\n"
            "⚠️ No AI provider available. Configure a provider via:\n"
            "<code>navig vault set openrouter/api_key &lt;your-key&gt;</code>\n"
            "or set up a local model with <code>navig ai</code>.",
            parse_mode="HTML",
        )


def create():
    return NLPAliasPlugin()
