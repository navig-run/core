"""
plugins/nlp_aliases.py — Multilingual NLP trigger aliases (EN / FR / RU).
Passive: reply to any message (or write inline) with a trigger word.
Skill  : skills/nlp_aliases.md
AI     : uses OPENROUTER_API_KEY or OPENAI_API_KEY from env / ~/.navig/config.yaml
"""
from __future__ import annotations
import json, os, re, urllib.request
from pathlib import Path
from typing import Any
from telegram import Update
from telegram.ext import ContextTypes
try:
    from plugin_base import BotPlugin, PluginMeta
except ImportError:
    from ..plugin_base import BotPlugin, PluginMeta  # type: ignore

_ACTIONS: dict[str, dict] = {
    "explain":    {"aliases":["explain","explique","объясни","explain this"],
                   "prompt":"Explain clearly and concisely:\n\n{text}","emoji":"🔍","label":"Explanation"},
    "correct":    {"aliases":["correct","corrige","исправь","fix this"],
                   "prompt":"Correct grammar and spelling. Show corrected text only:\n\n{text}","emoji":"✏️","label":"Correction"},
    "improve":    {"aliases":["improve","améliore","улучши","make it better"],
                   "prompt":"Improve clarity and style:\n\n{text}","emoji":"⬆️","label":"Improved"},
    "simplify":   {"aliases":["simplify","simplifie","упрости","make it simple"],
                   "prompt":"Simplify so a 12-year-old understands:\n\n{text}","emoji":"🧩","label":"Simplified"},
    "translate":  {"aliases":["translate","traduis","переведи","перевод"],
                   "prompt":"Translate to English if non-English, else to Russian:\n\n{text}","emoji":"🌐","label":"Translation"},
    "brainstorm": {"aliases":["brainstorm","идеи","remue-méninges","give ideas"],
                   "prompt":"Brainstorm 5 creative ideas about:\n\n{text}","emoji":"💡","label":"Ideas"},
    "proofread":  {"aliases":["proofread","proof","relis","проверь"],
                   "prompt":"Proofread and list all corrections:\n\n{text}","emoji":"📝","label":"Proofread"},
    "creative":   {"aliases":["creative","créatif","креатив","make it creative"],
                   "prompt":"Rewrite in a creative, engaging way:\n\n{text}","emoji":"🎨","label":"Creative"},
    "summary":    {"aliases":["summary","summarize","résumé","резюме","tl;dr","кратко"],
                   "prompt":"Summarize in 3-5 sentences:\n\n{text}","emoji":"📋","label":"Summary"},
    "context":    {"aliases":["context","contexte","контекст","analyse","analyze","анализ"],
                   "prompt":"Analyze: identify sentiment, key points, and notable observations:\n\n{text}","emoji":"🧠","label":"Analysis"},
}

_ALL = sorted([a for v in _ACTIONS.values() for a in v["aliases"]], key=len, reverse=True)
_PAT = r"^(" + "|".join(re.escape(a) for a in _ALL) + r")[\s:,]+(.+)?$"

def _detect(text: str):
    m = re.match(_PAT, text.strip(), re.I|re.S)
    if not m: return None
    trigger = m.group(1).lower()
    rest    = (m.group(2) or "").strip()
    for k, v in _ACTIONS.items():
        if trigger in [a.lower() for a in v["aliases"]]:
            return k, rest
    return None

def _ai_key():
    try:
        import yaml
        p = Path.home()/".navig"/"config.yaml"
        if p.exists():
            cfg = yaml.safe_load(p.read_text()) or {}
            k = cfg.get("openrouter_api_key") or os.environ.get("OPENROUTER_API_KEY")
            if k: return "openrouter", k
            k = cfg.get("openai_api_key") or os.environ.get("OPENAI_API_KEY")
            if k: return "openai", k
    except Exception: pass
    k = os.environ.get("OPENROUTER_API_KEY")
    if k: return "openrouter", k
    k = os.environ.get("OPENAI_API_KEY")
    if k: return "openai", k
    return None

def _call_ai(prompt, provider, key) -> str | None:
    url   = "https://openrouter.ai/api/v1/chat/completions" if provider=="openrouter" else "https://api.openai.com/v1/chat/completions"
    model = "openai/gpt-4o-mini" if provider=="openrouter" else "gpt-4o-mini"
    body  = json.dumps({"model":model,"messages":[{"role":"user","content":prompt}],"max_tokens":800}).encode()
    req   = urllib.request.Request(url, data=body,
            headers={"Content-Type":"application/json","Authorization":f"Bearer {key}"},method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]
    except Exception: return None

class NLPAliasPlugin(BotPlugin):
    """EN/FR/RU trigger aliases: explain·correct·translate·summarize + 6 more."""

    @property
    def meta(self):
        return PluginMeta("nlp_aliases","Multilingual AI triggers: explain/correct/translate/etc. in EN/FR/RU.","1.0.0")

    @property
    def command(self): return ""  # passive only

    @property
    def passive_patterns(self): return [_PAT]

    async def handle(self, update, context): pass

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        import asyncio
        msg  = update.message
        text = msg.text or ""
        res  = _detect(text)
        if not res: return
        key, inline = res
        act  = _ACTIONS[key]
        body = inline
        if not body and msg.reply_to_message:
            body = msg.reply_to_message.text or msg.reply_to_message.caption or ""
        if not body:
            await msg.reply_text(
                f"{act['emoji']} *{act['label']}*\n\nReply to a message with `{act['aliases'][0]}`, "
                f"or write:\n`{act['aliases'][0]} <your text>`", parse_mode="Markdown"); return
        status = await msg.reply_text(f"{act['emoji']} Processing…")
        prompt = act["prompt"].format(text=body)
        creds  = _ai_key()
        if creds:
            provider, key2 = creds
            resp = await asyncio.to_thread(_call_ai, prompt, provider, key2)
            if resp:
                await status.edit_text(f"{act['emoji']} *{act['label']}:*\n\n{resp}", parse_mode="Markdown"); return
        await status.edit_text(
            f"{act['emoji']} *{act['label']} detected* ✓\n\n"
            f"Target: _{body[:200]}_\n\n"
            "⚠️ No AI key configured. Add `openrouter_api_key` to `~/.navig/config.yaml` to enable responses.",
            parse_mode="Markdown")

def create(): return NLPAliasPlugin()
