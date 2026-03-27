"""
Fact Extractor — Extracts key facts from conversation turns.

Two extraction modes:
  1. **Rule-based** (fast, zero-cost): Pattern matching for common
     fact shapes — preferences, decisions, identities, tech choices.
  2. **LLM-based** (deep, costs tokens): Sends a structured prompt to
     the active LLM to extract high-signal facts from a conversation turn.

The extractor runs *after* each assistant response is generated,
extracts candidate facts, deduplicates against existing store,
and persists new ones.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from navig.memory.key_facts import VALID_CATEGORIES, KeyFact, KeyFactStore

logger = logging.getLogger("navig.memory.fact_extractor")


# ── Extraction Result ─────────────────────────────────────────


@dataclass
class ExtractionResult:
    """Facts extracted from a single conversation turn."""

    facts: List[KeyFact] = field(default_factory=list)
    source_turn: str = ""
    method: str = "rule"  # "rule" | "llm" | "hybrid"
    raw_llm_response: str = ""

    @property
    def count(self) -> int:
        return len(self.facts)


# ── Rule-Based Extractor ─────────────────────────────────────

# Patterns compiled at module load — never re-compiled per call.
# Stored as (compiled_regex, category) pairs.
_PREFERENCE_PATTERNS = [
    (re.compile(r"(?:i\s+)?prefer\s+(.+?)(?:\.|$)", re.IGNORECASE), "preference"),
    (
        re.compile(
            r"(?:i\s+)?(?:always|usually|typically)\s+(?:use|work with|go with)\s+(.+?)(?:\.|$)",
            re.IGNORECASE,
        ),
        "preference",
    ),
    (
        re.compile(
            r"(?:i\s+)?like\s+(?:to\s+)?(?:use\s+)?(.+?)(?:\s+(?:for|when|because)|\.|$)",
            re.IGNORECASE,
        ),
        "preference",
    ),
    (
        re.compile(
            r"(?:my|our)\s+(?:preferred|default|standard)\s+(?:\w+\s+)?(?:is|are)\s+(.+?)(?:\.|$)",
            re.IGNORECASE,
        ),
        "preference",
    ),
    (
        re.compile(
            r"(?:i\s+)?(?:want|need)\s+(?:to\s+)?(.+?)(?:\s+(?:from now on|going forward))",
            re.IGNORECASE,
        ),
        "preference",
    ),
]

_DECISION_PATTERNS = [
    (
        re.compile(
            r"(?:let'?s?|we(?:'ll| will| should)?|i(?:'ll| will)?)\s+(?:go with|use|switch to|adopt)\s+(.+?)(?:\.|$)",
            re.IGNORECASE,
        ),
        "decision",
    ),
    (
        re.compile(
            r"(?:decided|decision)\s+(?:to\s+|is\s+)?(.+?)(?:\.|$)", re.IGNORECASE
        ),
        "decision",
    ),
    (
        re.compile(
            r"(?:from now on|going forward|henceforth)\s*[,:]?\s*(.+?)(?:\.|$)",
            re.IGNORECASE,
        ),
        "decision",
    ),
]

_IDENTITY_PATTERNS = [
    (
        re.compile(
            r"(?:my name is|i'?m called|call me)\s+(\w+(?:\s+\w+)?)", re.IGNORECASE
        ),
        "identity",
    ),
    (
        re.compile(
            r"(?:i work (?:at|for)|i'?m (?:a|an|the))\s+(.+?)(?:\.|$)", re.IGNORECASE
        ),
        "identity",
    ),
    (
        re.compile(r"(?:my (?:role|title|position) is)\s+(.+?)(?:\.|$)", re.IGNORECASE),
        "identity",
    ),
    (
        re.compile(
            r"(?:i'?m based in|i live in|my timezone is)\s+(.+?)(?:\.|$)", re.IGNORECASE
        ),
        "identity",
    ),
]

_TECHNICAL_PATTERNS = [
    (
        re.compile(
            r"(?:(?:our|my|the) (?:stack|tech stack|setup) (?:is|includes|uses))\s+(.+?)(?:\.|$)",
            re.IGNORECASE,
        ),
        "technical",
    ),
    (
        re.compile(
            r"(?:(?:we|i) (?:run|deploy|host) (?:on|with|using))\s+(.+?)(?:\.|$)",
            re.IGNORECASE,
        ),
        "technical",
    ),
    (
        re.compile(
            r"(?:(?:our|the) (?:database|db|server|infra) (?:is|runs))\s+(.+?)(?:\.|$)",
            re.IGNORECASE,
        ),
        "technical",
    ),
    (
        re.compile(
            r"(?:(?:we|i) use)\s+(\w+(?:\s+\w+){0,3})\s+(?:for|as|to)\s+", re.IGNORECASE
        ),
        "technical",
    ),
]

_PROBLEM_SOLUTION_PATTERNS = [
    re.compile(
        r"(?:getting|seeing|hitting|encountered?|ran? into)\s+(?:an?\s+)?(?:error|exception|bug|issue|problem):?\s+(.{10,200})",
        re.IGNORECASE,
    ),
    re.compile(r"(?:error|exception|traceback)[:\s]+(.{10,200})", re.IGNORECASE),
    re.compile(
        r"(?:fixed|resolved|solved|the fix (?:was|is)|solution (?:was|is)|fixed by|resolved by|the issue was)\s+(.{10,200})",
        re.IGNORECASE,
    ),
]

ALL_PATTERNS = (
    _PREFERENCE_PATTERNS + _DECISION_PATTERNS + _IDENTITY_PATTERNS + _TECHNICAL_PATTERNS
)


def extract_rules(
    user_text: str,
    assistant_text: str,
    source_conversation_id: str = "",
    source_platform: str = "core",
) -> ExtractionResult:
    """
    Fast, zero-cost rule-based extraction.
    Patterns are pre-compiled at module load — no re-compilation per call.
    Problem-solution patterns scan user and assistant text separately.
    """
    facts: List[KeyFact] = []
    seen_contents: set = set()

    for compiled_pat, category in ALL_PATTERNS:
        for match in compiled_pat.finditer(user_text):
            raw = match.group(1).strip()
            if not raw or len(raw) < 3 or len(raw) > 200:
                continue
            content = _normalize_fact_text(raw, category, user_text)
            if content in seen_contents:
                continue
            seen_contents.add(content)
            facts.append(
                KeyFact(
                    content=content,
                    category=category,
                    confidence=0.65,
                    source_conversation_id=source_conversation_id,
                    source_platform=source_platform,
                    tags=_auto_tags(content, category),
                )
            )

    # Problem-solution patterns: scan user and assistant text separately
    # (avoids string concatenation; assistant text often contains the resolution)
    for text_src in (user_text, assistant_text):
        if not text_src:
            continue
        for compiled_pat in _PROBLEM_SOLUTION_PATTERNS:
            for match in compiled_pat.finditer(text_src):
                raw = match.group(1).strip()
                if not raw or len(raw) < 10 or len(raw) > 300:
                    continue
                content = raw if raw[0].isupper() else raw[0].upper() + raw[1:]
                if content in seen_contents:
                    continue
                seen_contents.add(content)
                facts.append(
                    KeyFact(
                        content=content,
                        category="problem_solution",
                        confidence=0.9,
                        source_conversation_id=source_conversation_id,
                        source_platform=source_platform,
                        tags=["problem_solution"] + _auto_tags(content, "technical"),
                    )
                )

    return ExtractionResult(
        facts=facts,
        source_turn=user_text[:200],
        method="rule",
    )


def _normalize_fact_text(raw: str, category: str, context: str) -> str:
    """Clean and normalize a raw extracted snippet into a standalone fact."""
    # Remove trailing punctuation noise
    text = re.sub(r"[,;:]+$", "", raw).strip()
    # Capitalize first letter
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    # Add "User" prefix for identity/preference facts
    if category in ("preference", "identity") and not text.lower().startswith(
        ("user", "the user")
    ):
        text = f"User: {text}"
    return text


def _auto_tags(content: str, category: str) -> List[str]:
    """Generate tags from content keywords."""
    tags = [category]
    # Common tech keywords
    tech_keywords = {
        "python",
        "javascript",
        "typescript",
        "rust",
        "go",
        "java",
        "docker",
        "kubernetes",
        "linux",
        "windows",
        "macos",
        "postgresql",
        "mysql",
        "sqlite",
        "redis",
        "mongodb",
        "react",
        "vue",
        "angular",
        "next",
        "django",
        "flask",
        "fastapi",
        "aws",
        "gcp",
        "azure",
        "vercel",
        "netlify",
        "git",
        "github",
        "gitlab",
    }
    words = set(re.findall(r"\b\w+\b", content.lower()))
    for kw in words & tech_keywords:
        tags.append(kw)
    return tags


# ── LLM-Based Extractor ──────────────────────────────────────

EXTRACTION_PROMPT = """You are a memory extraction agent. Given a conversation turn between a user and an assistant, extract any important facts worth remembering for future conversations.

Focus on:
- User preferences (tools, languages, styles, workflows)
- Explicit decisions (choices made, approach selected)
- Identity information (name, role, timezone, company, team)
- Technical context (stack, infrastructure, deployment setup)
- Recurring patterns or constraints

Rules:
- Each fact must be a single, concise, standalone sentence.
- Do NOT extract: greetings, small talk, questions, or transient task details.
- Do NOT extract facts about the current task being worked on (that's session context, not memory).
- Only extract what is likely to be useful in FUTURE conversations.
- Confidence: 0.0-1.0 (how certain you are this is a persistent fact, not a one-time mention).
- If there are NO extractable facts, return an empty array.

Output strictly as JSON array:
[
  {"content": "...", "category": "preference|decision|identity|technical|context", "confidence": 0.8, "tags": ["tag1"]}
]

Conversation turn:
USER: {user_text}
ASSISTANT: {assistant_text}

Extract facts (JSON array only, no explanation):"""


async def extract_llm(
    user_text: str,
    assistant_text: str,
    llm_call: Callable,
    source_conversation_id: str = "",
    source_platform: str = "core",
    model: Optional[str] = None,
) -> ExtractionResult:
    """
    LLM-based fact extraction.

    Args:
        user_text: The user's message
        assistant_text: The assistant's response
        llm_call: Async callable that takes (prompt, model?) and returns text
        source_conversation_id: Session ID
        source_platform: Platform of origin
        model: Optional model override (prefer small/fast model)

    Returns:
        ExtractionResult with extracted facts
    """
    prompt = EXTRACTION_PROMPT.format(
        user_text=user_text[:2000],
        assistant_text=assistant_text[:2000],
    )

    try:
        if model:
            raw = await llm_call(prompt, model=model)
        else:
            raw = await llm_call(prompt)
    except Exception as exc:
        logger.warning("LLM extraction failed: %s", exc)
        return ExtractionResult(source_turn=user_text[:200], method="llm")

    # Parse JSON array from LLM output
    facts = _parse_llm_facts(raw, source_conversation_id, source_platform)
    return ExtractionResult(
        facts=facts,
        source_turn=user_text[:200],
        method="llm",
        raw_llm_response=raw,
    )


def _parse_llm_facts(
    raw: str,
    source_conversation_id: str,
    source_platform: str,
) -> List[KeyFact]:
    """Parse the LLM's JSON response into KeyFact objects."""
    # Extract JSON array from response (LLM might wrap it in markdown fences)
    raw = raw.strip()
    json_match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not json_match:
        logger.debug("No JSON array found in LLM extraction response")
        return []

    try:
        items = json.loads(json_match.group(0))
    except json.JSONDecodeError as exc:
        logger.debug("JSON parse error in LLM facts: %s", exc)
        return []

    if not isinstance(items, list):
        return []

    facts: List[KeyFact] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        content = item.get("content", "").strip()
        if not content or len(content) < 5:
            continue

        category = item.get("category", "context")
        if category not in VALID_CATEGORIES:
            category = "context"

        confidence = item.get("confidence", 0.7)
        if not isinstance(confidence, (int, float)):
            confidence = 0.7
        confidence = max(0.0, min(1.0, float(confidence)))

        tags = item.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        tags = [str(t) for t in tags if isinstance(t, str)]

        facts.append(
            KeyFact(
                content=content,
                category=category,
                confidence=confidence,
                tags=tags,
                source_conversation_id=source_conversation_id,
                source_platform=source_platform,
            )
        )

    return facts


# ── Hybrid Extractor (Main Entry Point) ──────────────────────


class FactExtractor:
    """
    Coordinates fact extraction from conversation turns.

    Default mode: rule-based (zero cost).
    Optional LLM mode: deeper extraction at token cost.
    Hybrid mode: rules first, then LLM for turns that have high
    information density but no rule matches.
    """

    def __init__(
        self,
        store: Optional[KeyFactStore] = None,
        llm_call: Optional[Callable] = None,
        mode: str = "hybrid",  # "rule" | "llm" | "hybrid"
        min_user_length: int = 20,  # Skip very short messages
        max_facts_per_turn: int = 5,
    ):
        self.store = store
        self.llm_call = llm_call
        self.mode = mode
        self.min_user_length = min_user_length
        self.max_facts_per_turn = max_facts_per_turn

    async def extract_and_store(
        self,
        user_text: str,
        assistant_text: str,
        source_conversation_id: str = "",
        source_platform: str = "core",
    ) -> ExtractionResult:
        """
        Extract facts from a conversation turn and persist them.

        Returns the combined ExtractionResult.
        """
        if len(user_text.strip()) < self.min_user_length:
            return ExtractionResult(source_turn=user_text[:200])

        # Skip common non-informative patterns
        if _is_low_signal(user_text):
            return ExtractionResult(source_turn=user_text[:200])

        result = ExtractionResult(source_turn=user_text[:200])

        # Phase 1: Rule-based extraction (always runs)
        if self.mode in ("rule", "hybrid"):
            rule_result = extract_rules(
                user_text,
                assistant_text,
                source_conversation_id,
                source_platform,
            )
            result.facts.extend(rule_result.facts)
            result.method = "rule"

        # Phase 2: LLM extraction (if enabled and rules didn't find much)
        if self.mode in ("llm", "hybrid") and self.llm_call:
            should_llm = self.mode == "llm" or (
                self.mode == "hybrid"
                and len(result.facts) == 0
                and _is_high_signal(user_text)
            )
            if should_llm:
                try:
                    llm_result = await extract_llm(
                        user_text,
                        assistant_text,
                        self.llm_call,
                        source_conversation_id,
                        source_platform,
                    )
                    result.facts.extend(llm_result.facts)
                    result.method = "hybrid" if result.method == "rule" else "llm"
                    result.raw_llm_response = llm_result.raw_llm_response
                except Exception as exc:
                    logger.warning("LLM extraction phase failed: %s", exc)

        # Limit total facts per turn
        result.facts = result.facts[: self.max_facts_per_turn]

        # Persist to store
        if self.store and result.facts:
            for fact in result.facts:
                try:
                    self.store.upsert(fact)
                except Exception as exc:
                    logger.warning("Failed to store fact: %s", exc)

        if result.facts:
            logger.info(
                "Extracted %d fact(s) from turn [%s]: %s",
                len(result.facts),
                result.method,
                [f.content[:50] for f in result.facts],
            )

        return result

    def extract_sync(
        self,
        user_text: str,
        assistant_text: str,
        source_conversation_id: str = "",
        source_platform: str = "core",
    ) -> ExtractionResult:
        """
        Synchronous rule-based extraction only (no LLM).
        Use this in sync contexts where async is not available.
        """
        if len(user_text.strip()) < self.min_user_length:
            return ExtractionResult(source_turn=user_text[:200])

        if _is_low_signal(user_text):
            return ExtractionResult(source_turn=user_text[:200])

        result = extract_rules(
            user_text,
            assistant_text,
            source_conversation_id,
            source_platform,
        )

        result.facts = result.facts[: self.max_facts_per_turn]

        if self.store and result.facts:
            for fact in result.facts:
                try:
                    self.store.upsert(fact)
                except Exception as exc:
                    logger.warning("Failed to store fact: %s", exc)

        return result


# ── Signal Detection ──────────────────────────────────────────

_LOW_SIGNAL_PATTERNS = [
    r"^(hi|hello|hey|thanks|thank you|ok|okay|sure|yes|no|bye|great|good|nice|cool)\s*[.!?]*$",
    r"^(what|how|why|when|where|can you|could you|please|help)\s",
    r"^(show me|list|display|print|run|execute|debug|fix|build|deploy)\s",
]

_HIGH_SIGNAL_KEYWORDS = {
    "prefer",
    "always",
    "usually",
    "typically",
    "never",
    "my name",
    "i work",
    "our stack",
    "we use",
    "i use",
    "decided",
    "going forward",
    "from now on",
    "timezone",
    "based in",
    "my role",
    "my team",
    "don't like",
    "don't use",
    "don't want",
    "remember",
    "keep in mind",
    "note that",
    "standard",
    "convention",
    "rule",
    "policy",
}


def _is_low_signal(text: str) -> bool:
    """Check if the text is likely just a command or greeting (no facts to extract)."""
    text_lower = text.strip().lower()
    for pattern in _LOW_SIGNAL_PATTERNS:
        if re.match(pattern, text_lower, re.IGNORECASE):
            return True
    return False


def _is_high_signal(text: str) -> bool:
    """Check if text likely contains extractable facts (worth LLM cost)."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in _HIGH_SIGNAL_KEYWORDS)
