"""
telegram_mode_classifier — Classify user intent into pipeline execution modes.

Modes map to the existing LLMModeRouter tiers:
  TALK  → small_talk   (1-3 line reply, no status overhead)
  REASON → big_tasks   (chain-of-thought, model footer)
  CODE  → coding       (fenced code + explain)
  ACT   → big_tasks    (cinematic status pipeline + tool calls)

Priority: CODE > ACT > REASON > TALK
"""
from __future__ import annotations

import re
from typing import Literal

# Mode type alias
Mode = Literal["TALK", "REASON", "ACT", "CODE"]

# ── CODE signals — writing/fixing code ────────────────────────────────────
_CODE_PATTERNS = re.compile(
    r"\b(write|code|script|function|class|method|implement|refactor|debug|fix|syntax|"
    r"snippet|program|module|def |import |async def|compile|unittest|pytest|regex|"
    r"loop|algorithm|data structure|parse|json|yaml)\b",
    re.IGNORECASE,
)

# ── ACT signals — actions on external systems / search / check ─────────────
_ACT_PATTERNS = re.compile(
    r"\b(check|run|test|ping|fetch|search|find|look up|look for|scan|deploy|"
    r"restart|show me|get|download|visit|open|call|query|monitor|verify|"
    r"is .{1,20} (up|down|online|running|alive)|curl|wget|head|request|"
    r"what(\'s| is) .{1,30} (ip|status|version|price|weather|time|rate))\b",
    re.IGNORECASE,
)

# ── MIC-CHECK / audio-probe patterns — conversational, not ACT ─────────────
_MIC_CHECK = re.compile(
    r"^(?:"
    r"(?:test[,.]?\s*){1,5}|"
    r"testing[,.]?\s*(?:testing)?|"
    r"(?:one|two|three|four|five|un|deux|trois|eins|zwei|drei)[,.]?\s*|"
    r"mic\s*(?:check|test|on)?|"
    r"(?:hello|check)[,.]?\s*(?:one|is this on|mic|audio|test)"
    r")(.{0,40})$",
    re.IGNORECASE,
)

# ── REASON signals — explanations / analysis ──────────────────────────────
_REASON_PATTERNS = re.compile(
    r"\b(explain|why|analyze|analyse|compare|difference|how does|how do|"
    r"what is|what are|describe|summarize|summarise|tell me about|"
    r"difference between|pros and cons|advantages|disadvantages|"
    r"when should|should i|which is better|review|assess|evaluate|"
    r"break down|elaborate|walk me through)\b",
    re.IGNORECASE,
)

# ── TALK — short phrases and social messages ───────────────────────────────
_TALK_PATTERNS = re.compile(
    r"^(hey|hi|hello|yo|sup|what'?s up|howdy|hola|good (morning|afternoon|evening|night)|"
    r"thanks|thank you|ok|okay|got it|noted|cool|nice|great|sure|yep|nope|no|yes|k |kk|lol|"
    r"haha|hehe|👍|👋|😊|😄|🙏)",
    re.IGNORECASE,
)


def classify_mode(text: str) -> Mode:
    """Classify a user message into a pipeline execution mode.

    Priority order: CODE > ACT > REASON > TALK

    This is a synchronous heuristic classifier — no LLM call, <1ms per message.
    """
    stripped = text.strip()

    if not stripped:
        return "TALK"

    # Mic-check / audio probe — always conversational, never triggers ACT
    if _MIC_CHECK.match(stripped):
        return "TALK"

    # Very short message (1–2 words) → only ACT if there's a clear target
    # (URL, hostname, ip) alongside the action word.  Bare words like
    # "ping", "test", "check" with nothing to act on go straight to TALK.
    word_count = len(stripped.split())
    if word_count <= 2:
        has_target = bool(re.search(r'https?://|\b(?:[\w-]+\.){1,}[a-z]{2,}\b|\d{1,3}(?:\.\d{1,3}){3}', stripped, re.IGNORECASE))
        if _ACT_PATTERNS.search(stripped) and has_target:
            return "ACT"
        return "TALK"

    # Check social openers first so "hey check this site" maps to ACT not TALK
    if _TALK_PATTERNS.match(stripped) and word_count <= 5:
        return "TALK"

    # Priority: CODE first — code-writing intent is clearest
    if _CODE_PATTERNS.search(stripped):
        return "CODE"

    # ACT next — real-world actions / lookups
    if _ACT_PATTERNS.search(stripped):
        return "ACT"

    # REASON — analytical questions
    if _REASON_PATTERNS.search(stripped):
        return "REASON"

    # Default for medium-length unclassified messages
    if word_count >= 8:
        return "REASON"

    return "TALK"


def mode_to_llm_tier(mode: Mode) -> str:
    """Map a pipeline mode to an LLMModeRouter mode string."""
    return {
        "TALK": "small_talk",
        "REASON": "big_tasks",
        "ACT": "big_tasks",
        "CODE": "coding",
    }.get(mode, "small_talk")


def select_tools_for_text(text: str) -> list[str]:
    """Return ordered list of tool names to invoke for an ACT-mode message.

    Rules (first match wins for primary tool; multiple can match):
    - URL/domain present   → site_check first, web_fetch second
    - search/find/look up  → search
    - code in backticks    → code_exec_sandbox
    - fallback             → search
    """
    tools: list[str] = []
    lower = text.lower()

    # URL or bare domain
    url_pattern = re.compile(
        r"https?://\S+|(?:^|\s)([a-zA-Z0-9-]+\.(com|net|org|io|dev|app|xyz|ai|co)\S*)",
        re.IGNORECASE,
    )
    if url_pattern.search(text):
        tools.append("site_check")
        tools.append("web_fetch")

    # Explicit search intent
    if re.search(r"\b(search|find|look up|look for|google|results for)\b", lower):
        tools.append("search")

    # Code in backticks → execute it
    if re.search(r"`[^`]+`", text) or re.search(r"```[\s\S]+```", text):
        tools.append("code_exec_sandbox")

    # Fallback: if nothing matched, try search
    if not tools:
        tools.append("search")

    # Deduplicate preserving order
    seen: set[str] = set()
    return [t for t in tools if not (t in seen or seen.add(t))]  # type: ignore[func-returns-value]


def extract_url(text: str) -> str | None:
    """Extract the first URL or bare domain from text."""
    m = re.search(
        r"(https?://[^\s]+)|"
        r"\b([a-zA-Z0-9-]+\.(?:com|net|org|io|dev|app|xyz|ai|co)(?:/[^\s]*)?)\b",
        text,
    )
    if not m:
        return None
    found = m.group(1) or m.group(2)
    if found and not found.startswith("http"):
        found = "https://" + found
    return found
