"""
Enhanced mode detection with confidence scoring.

Replaces the separate detect_mode() functions in llm_router.py and
model_router.py with a single canonical implementation that returns
(mode, confidence, reasons[]).
"""

from __future__ import annotations

import re

# ── Detection Patterns ─────────────────────────────────────────────

# <\w+>[^<]*</\w+> replaces the former <\w+>.*</\w+> to eliminate polynomial
# backtracking on adversarial HTML-like input (CWE-400 / ReDoS).
_CODE_PATTERNS = re.compile(
    r"```|def\s+\w+|class\s+\w+|function\s+\w+|const\s+\w+\s*=|"
    r"import\s+\w+|from\s+\w+\s+import|#include|<\w+>[^<]*</\w+>|"
    r"\bsyntax error\b|\bcompile\b|\bruntime\b|\bsegfault\b|"
    r"\.py\b|\.ts\b|\.js\b|\.rs\b|\.go\b|\.java\b|\.cpp\b|"
    r"\bfix\s+(the\s+)?(bug|error|issue|code|function|method)\b|"
    r"\bwrite\s+(a\s+)?(function|class|script|program|code)\b|"
    r"\brefactor\b|\bdebug\b|\bimplement\b|\boptimize\s+(the\s+)?code\b",
    re.IGNORECASE,
)

# Set-based greeting/casual detection avoids polynomial backtracking from
# adjacent optional quantifiers (\s*[!.,?]*\s*$) in equivalent regexes.
_GREETING_WORDS: frozenset[str] = frozenset(
    {
        "hey",
        "hi",
        "hello",
        "hola",
        "sup",
        "yo",
        "howdy",
        "greetings",
        "greeting",
        "good morning",
        "good afternoon",
        "good evening",
    }
)
_CASUAL_WORDS: frozenset[str] = frozenset(
    {
        "thanks",
        "thank",
        "thx",
        "ok",
        "okay",
        "cool",
        "nice",
        "great",
        "sure",
        "yep",
        "nope",
        "no",
        "yes",
        "lol",
        "haha",
        "wow",
    }
)


def _is_greeting(text: str) -> bool:
    """Return True if *text* is a greeting phrase (exact, after stripping punctuation)."""
    normalized = text.strip().rstrip("!.,? \t").casefold()
    return normalized in _GREETING_WORDS


def _is_casual(text: str) -> bool:
    """Return True if *text* is a casual one-word acknowledgement."""
    normalized = text.strip().rstrip("!.? \t").casefold()
    return normalized in _CASUAL_WORDS


_SUMMARIZE_PATTERNS = re.compile(
    r"\b(summarize|summary|summarise|tl;?dr|condense|digest|recap|brief|shorten|"
    r"key\s+points|main\s+points|overview\s+of|give\s+me\s+the\s+gist)\b",
    re.IGNORECASE,
)

_RESEARCH_PATTERNS = re.compile(
    r"\b(research|analyze|analyse|analysis|compare|comparison|evaluate|"
    r"investigate|study|review|examine|assess|explore|deep\s*dive|"
    r"differences?\s+between|"
    r"pros?\s+and\s+cons?|trade-?offs?|benchmark|survey)\b",
    re.IGNORECASE,
)

_BIG_TASK_PATTERNS = re.compile(
    r"\b(plan|design|architect|strategy|roadmap|migration|refactor|rewrite|"
    r"build\s+a|create\s+a|develop\s+a|implement\s+a|step[- ]by[- ]step|"
    r"comprehensive|detailed|thorough|full|complete)\b",
    re.IGNORECASE,
)

_QUESTION_ENDINGS = re.compile(r"\?\s*$")


def detect_mode(text: str) -> tuple[str, float, list[str]]:
    """
    Classify user input into a task mode with confidence score.

    Returns:
        (mode, confidence, reasons)
        mode: one of 'coding', 'small_talk', 'big_tasks', 'summarize', 'research'
        confidence: 0.0–1.0 float
        reasons: list of strings explaining the classification
    """
    text = text.strip()
    reasons: list[str] = []

    if not text:
        return "small_talk", 0.95, ["empty_input"]

    word_count = len(text.split())
    line_count = text.count("\n") + 1
    has_question = bool(_QUESTION_ENDINGS.search(text))

    # ── High-confidence short patterns ──

    if len(text) < 60 and _is_greeting(text):
        return "small_talk", 0.95, ["greeting_pattern"]

    if _is_casual(text):
        return "small_talk", 0.95, ["casual_pattern"]

    # ── Code detection (high signal) ──

    code_matches = _CODE_PATTERNS.findall(text)
    if code_matches:
        confidence = min(0.95, 0.7 + len(code_matches) * 0.05)
        reasons.append(f"code_patterns({len(code_matches)})")

        # Check for research-about-code (e.g. "compare React vs Vue for coding")
        if _RESEARCH_PATTERNS.search(text):
            reasons.append("research_override")
            return "research", 0.75, reasons

        return "coding", confidence, reasons

    # ── Summarize detection ──

    if _SUMMARIZE_PATTERNS.search(text):
        confidence = 0.9 if word_count < 200 else 0.8
        reasons.append("summarize_keywords")
        return "summarize", confidence, reasons

    # ── Research detection ──

    if _RESEARCH_PATTERNS.search(text):
        confidence = 0.85
        reasons.append("research_keywords")
        if word_count > 50:
            confidence = 0.9
            reasons.append("long_research_query")
        return "research", confidence, reasons

    # ── Big task detection ──

    if _BIG_TASK_PATTERNS.search(text):
        confidence = 0.8
        reasons.append("big_task_keywords")
        if word_count > 100:
            confidence = 0.85
            reasons.append("long_complex_request")
        return "big_tasks", confidence, reasons

    # ── Length-based heuristics ──

    # Short questions → small_talk
    if word_count < 15 and has_question and line_count == 1:
        return "small_talk", 0.7, ["short_question"]

    # Medium single-line → small_talk with lower confidence
    if word_count < 30 and line_count == 1:
        return "small_talk", 0.55, ["medium_single_line"]

    # Long or multi-line → big_tasks (fail up)
    if word_count > 80 or line_count > 5:
        return "big_tasks", 0.5, ["long_input_failup"]

    # ── Default: big_tasks (fail up, not down) ──
    return "big_tasks", 0.45, ["default_failup"]
