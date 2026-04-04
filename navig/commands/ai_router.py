"""Hybrid intent classifier for `navig ai "<natural language>"`.

Routes bare ``navig ai "<text>"`` to the correct subcommand without
any API call.  Pure keyword-weight approach — deterministic, < 1 ms.

Design rules:
- No network, no LLM, no subprocess.
- Returns (subcommand_name, confidence) where confidence in [0, 1].
- confidence >= 0.85  → route automatically.
- confidence <  0.85  → caller must present top-2 to the user.
"""

from __future__ import annotations

import re
from typing import NamedTuple


# ---------------------------------------------------------------------------
# Keyword weight tables
# ---------------------------------------------------------------------------

# fmt: off
_WEIGHTS: dict[str, list[tuple[str, float]]] = {
    "diagnose": [
        (r"\bwhy\b",          0.35), (r"\bnot work",        0.40),
        (r"\bfail",           0.35), (r"\bdown\b",          0.35),
        (r"\bcrash",          0.55), (r"\bbroken\b",        0.40),
        (r"\b502\b",          0.65), (r"\b503\b",           0.65),
        (r"\b500\b",          0.45), (r"\berror\b",         0.25),
        (r"\bbad gateway\b",  0.40), (r"\btimeout\b",       0.35),
        (r"\bwon.t start\b",  0.45), (r"\brefused\b",       0.35),
        (r"\b(nginx|apache|mysql|redis|postgres)\b", 0.20),
        (r"\bdiagnos",        0.60), (r"\bdebug\b",         0.35),
        (r"\btroubleshoot",   0.55), (r"\bissue\b",         0.20),
        (r"\bwhat.*wrong",    0.50), (r"\bcan.t connect",   0.45),
    ],
    "explain": [
        (r"\bexplain\b",      0.90), (r"\bwhat is\b",        0.60),
        (r"\bwhat (is|does|are)\b", 0.55), (r"\bhow does\b",     0.50),
        (r"\bmean\b",          0.30), (r"\bunderstand\b",   0.35),
        (r"\bdescrib",         0.35), (r"\bcommand\b",      0.20),
        (r"\bsyntax\b",        0.35), (r"\bflag\b",          0.25),
        (r"\bmanual\b",        0.30), (r"\bwhat.*log",      0.25),
    ],
    "suggest": [
        (r"\bsuggest",        0.70), (r"\brecommend",       0.70),
        (r"\boptimi[sz]",     0.60), (r"\bimprove\b",       0.55),
        (r"\bshould i\b",     0.50), (r"\bwhat (should|next|can)\b", 0.45),
        (r"\bbetter\b",       0.30), (r"\bharden\b",        0.45),
        (r"\btune\b",         0.40), (r"\bperformance\b",   0.30),
    ],
    "show": [
        (r"\bshow\b",         0.50), (r"\blist\b",          0.45),
        (r"\bdisplay\b",      0.50), (r"\bhistory\b",       0.55),
        (r"\blast session\b", 0.65), (r"\bcontext\b",       0.45),
        (r"\bstatus\b",       0.40), (r"\bwhat.*remember",  0.55),
        (r"\bsess(ion)?\b",   0.35),
    ],
    "run": [
        (r"\brun\b",          0.40), (r"\bexecut",          0.45),
        (r"\banalys[ei]",     0.50), (r"\bscan\b",          0.40),
        (r"\binspect\b",      0.40), (r"\bcheck\b",         0.20),
        (r"\bperform\b",      0.30),
    ],
    "ask": [
        # Generic fallback patterns — intentionally lower individual weights
        (r"\b(how|what|why|when|where|who|which)\b", 0.20),
        (r"\bhelp\b",         0.20), (r"\bquestion\b",      0.30),
        (r"\bask\b",          0.60), (r"\btell me\b",       0.30),
        (r"\b\?\s*$",         0.15),  # ends with ?
    ],
}
# fmt: on

_COMPILED: dict[str, list[tuple[re.Pattern[str], float]]] = {
    sub: [(re.compile(pat, re.IGNORECASE), w) for pat, w in patterns]
    for sub, patterns in _WEIGHTS.items()
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class IntentResult(NamedTuple):
    subcommand: str
    confidence: float


def classify_intent(text: str) -> IntentResult:
    """Return the best-matching subcommand and its confidence score.

    The caller should check ``confidence >= CONFIDENCE_THRESHOLD`` (0.85)
    before routing automatically.  If below threshold, call
    ``top_two_intents()`` and present choices to the user.

    Args:
        text: Raw natural-language string from the user.

    Returns:
        IntentResult(subcommand, confidence) — confidence in [0.0, 1.0].
    """
    raw_scores = _score_all(text)
    best = max(raw_scores, key=lambda r: r.confidence)
    return best


def top_two_intents(text: str) -> list[IntentResult]:
    """Return the top-2 scored subcommands, sorted by confidence descending."""
    raw_scores = sorted(_score_all(text), key=lambda r: r.confidence, reverse=True)
    return raw_scores[:2]


CONFIDENCE_THRESHOLD: float = 0.85


# ---------------------------------------------------------------------------
# Internal scoring
# ---------------------------------------------------------------------------

def _score_all(text: str) -> list[IntentResult]:
    results: list[IntentResult] = []
    for subcommand, rules in _COMPILED.items():
        score = _score(text, rules)
        results.append(IntentResult(subcommand=subcommand, confidence=min(score, 1.0)))
    return results


def _score(text: str, rules: list[tuple[re.Pattern[str], float]]) -> float:
    """Sum weights for all matching patterns; cap at 1.0."""
    total = 0.0
    for pattern, weight in rules:
        if pattern.search(text):
            total += weight
    return total
