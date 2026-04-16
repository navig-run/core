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
    r"\b(ping|check|fetch|search|find|look up|look for|scan|"
    r"download|visit|query|"
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
    r"what is|what are|what causes|what leads to|what makes|what affects|"
    r"what determines|what happens (?:when|if)|what(?:'s| is) the (?:difference|impact|effect|role)|"
    r"describe|summarize|summarise|tell me about|"
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

# ── Opinion / preference / casual-sharing phrases → TALK ─────────────────
_OPINION_PATTERNS = re.compile(
    r"\b(do you know|have you (?:seen|heard|watched|read)|do you (?:like|watch|think|enjoy)|"
    r"my favou?rite|it'?s my favou?rite|is my favou?rite|"
    r"i (?:love|like|enjoy|hate|miss|remember|saw|watched|read)|"
    r"me too|have you ever|what do you think|how about you|"
    r"pretty (?:good|cool|nice|bad)|not bad|you know (?:what|that)|"
    r"did you (?:know|see|hear|watch|read)|just (?:saw|watched|read|finished)|"
    r"just (?:found|discovered|tried)|makes sense|"
    r"that'?s (?:great|nice|cool|awesome|good|bad|sad|funny))\b",
    re.IGNORECASE,
)

# ── Casual self-referential / social questions → TALK ──────────────────────
#
# Algorithm — no per-language phrase lists.
#
# Detection is structural, not lexical: a message is self-talk when it is
# SHORT and DIRECTED at the bot (contains a 2nd-person / reflexive pronoun),
# optionally confirmed by a BOT-TOPIC word for longer messages.
#
# Two frozensets per signal type:
#   _SELF_TALK_DIRECTED_WORDS — exact word match  (Latin / Cyrillic / Devanagari)
#   _SELF_TALK_DIRECTED_SUB   — substring match  (CJK / Arabic / no-space scripts)
#   _SELF_TALK_CONCEPT_WORDS  — bot-topic word match
#   _SELF_TALK_CONCEPT_SUB    — bot-topic substring match
#
# Extending to a new language: add its 2nd-person pronoun to the matching
# frozenset — one token is sufficient, no other change needed.

# Maximum token length to consider for self-talk detection.
# Messages longer than this cannot be a short conversational greeting.
_SELF_TALK_MAX_TOKENS: int = 10

# ── Second-person / reflexive pronouns (directed-at-bot signal) ─────────────
# Whole-word matched on lowercase split tokens.
# Clustered by Unicode script — NOT by language.
# To support a new language: add its 2nd-person pronoun here.
_SELF_TALK_DIRECTED_WORDS: frozenset[str] = frozenset({
    # Latin-script cluster (EN, ES, FR, DE, PT, IT, NL, PL, TR, SV, NO, DA, RO, …)
    "you", "your", "yourself",
    "tu", "toi", "te", "ti", "tú",    # FR / ES / IT / PT informal
    "vous",                              # FR plural / formal
    "sie", "ihnen", "dich", "dir",      # DE formal / informal
    "du", "dig",                         # DE informal / SV / NO / DA
    "jij", "jou",                        # NL
    "você", "voce", "vos",               # PT / River-Plate ES
    "sen", "sizi", "sana",               # TR
    "ty", "cię", "tobě", "tebe",         # PL + West-Slavic
    "vy", "vam",                         # SV/NO/DA plural / RU formal
    "kim",                               # TR standalone (kimsin split = kim+sin)
    # Cyrillic cluster (RU, UK, BE, BG, SR, MK, …)
    "ты", "вы", "тебя", "тебе", "вам",
    "ти", "ви",                           # Ukrainian
    "себе", "собой",                      # reflexive (расскажи о себе)
    # Devanagari cluster (HI, MR, NE, …)
    "आप", "तुम", "तू", "आपको",
})

# Substring matched — for scripts that do not use whitespace word-breaks.
_SELF_TALK_DIRECTED_SUB: frozenset[str] = frozenset({
    "你", "您",              # Chinese (Simplified + Traditional share these)
    "あなた", "きみ", "君",  # Japanese
    "너", "당신",            # Korean
    "أنت", "أنتِ", "أنتم",  # Arabic (all genders / numbers)
})

# ── Bot-identity / state / capability confirmers ─────────────────────────────
# Only consulted when token count is 5-10 (short messages are unconditional).
# Whole-word matched on lowercase split tokens.
_SELF_TALK_CONCEPT_WORDS: frozenset[str] = frozenset({
    # Latin-script cluster
    "name", "called",
    "do", "doing", "work", "working",
    "help", "feel", "feeling",
    "ok", "okay",
    "bot", "ai", "robot", "assistant", "human", "person",
    "from", "who", "introduce", "capable",
    "alive", "there", "free", "available", "busy",
    # Cyrillic cluster
    "зовут", "делаешь", "умеешь", "можешь",
    "бот", "робот", "ии", "себя", "кто",
    "знаешь",    # "Откуда ты знаешь про X" → TALK
    # Devanagari cluster
    "नाम", "बॉट", "कौन",
})

# Substring matched for no-space scripts.
_SELF_TALK_CONCEPT_SUB: frozenset[str] = frozenset({
    "叫", "做", "能", "会", "谁", "名",  # Chinese
    "名前", "誰", "何",                   # Japanese
    "이름", "누구", "뭐",                  # Korean
    "اسم", "تفعل", "بوت",                 # Arabic
})


def _is_casual_self_talk(text: str) -> bool:
    """Language-agnostic detector for casual self-referential questions.

    Detects messages directed AT the bot about its own identity, state, or
    capability — in any language — without per-language phrase lists.

    Algorithm:
      1. Token-count gate: > _SELF_TALK_MAX_TOKENS → False immediately.
         CJK / Hiragana / Hangul characters each count as one token;
         all other scripts use whitespace splitting.
      2. Directed signal: message contains a 2nd-person or reflexive pronoun.
         Latin / Cyrillic / Devanagari → exact lowercase word match.
         CJK / Arabic / no-space → substring match on full lowercased text.
      3. No directed signal → False (let downstream classifiers decide).
      4. Short (≤ 4 tokens) + directed → True unconditionally.
      5. Longer (5-10 tokens) + directed + bot-concept word → True.
    """
    stripped = text.strip()

    # ── 1. Token-count gate ───────────────────────────────────────────────────
    # Count CJK / Japanese kana / Korean Hangul characters as individual tokens.
    cjk_count = sum(
        1 for c in stripped
        if (
            "\u4e00" <= c <= "\u9fff"     # CJK unified ideographs
            or "\u3040" <= c <= "\u30ff"  # Hiragana + Katakana
            or "\uac00" <= c <= "\ud7af"  # Hangul syllables
        )
    )
    token_count = cjk_count if cjk_count > 0 else len(stripped.split())

    if token_count > _SELF_TALK_MAX_TOKENS:
        return False

    # ── 2. Directed signal ───────────────────────────────────────────────────
    lower = stripped.lower()
    # Split on whitespace and full-width space (U+3000) for CJK contexts.
    words = frozenset(re.split(r"[\s\u3000\u00a0]+", lower))

    has_directed = bool(words & _SELF_TALK_DIRECTED_WORDS)
    if not has_directed:
        # Substring fallback: CJK, Arabic, and agglutinative forms (French
        # "es-tu", Turkish "kimsin") that don't cleanly split at word boundaries.
        has_directed = any(tok in lower for tok in _SELF_TALK_DIRECTED_SUB)

    if not has_directed:
        return False

    # ── 3. Short + directed is sufficient ────────────────────────────────────
    if token_count <= 4:
        return True

    # ── 4. Moderate length: require a bot-concept word to confirm ────────────
    has_concept = bool(words & _SELF_TALK_CONCEPT_WORDS)
    if not has_concept:
        has_concept = any(tok in lower for tok in _SELF_TALK_CONCEPT_SUB)

    return has_concept

# ── Language-agnostic structural signals ──────────────────────────────────
# Title/entity markers — detected regardless of surrounding language.
# Covers: guillemets «», curly quotes \u201c\u201d, straight-quoted multi-word,
# CJK corner brackets 「」【】『』, and runs of title-cased ASCII words.
_TITLE_MARKER = re.compile(
    r'«[^»]{2,}»'
    r'|\u201c[^\u201d]{2,}\u201d'
    r'|"[^"]{4,}"'
    r'|【[^】]{2,}】|「[^」]{2,}」|『[^』]{2,}』'
    r'|(?:[A-Z][a-z]+[ \t]){2,}[A-Z][a-z]+',
)


def _contains_title_marker(text: str) -> bool:
    """Return True if text contains a script-agnostic title or named-entity signal."""
    return bool(_TITLE_MARKER.search(text))


# Minimum fraction of alphabetic chars that must be non-Latin to classify a
# text as non-Latin-dominant.  Kept as a named constant so it is tunable in
# one place and self-documents the 30 % design decision.
_NON_LATIN_DOMINANCE_THRESHOLD: float = 0.30


def _is_non_latin_dominant(text: str) -> bool:
    """Return True when >30% of alphabetic chars are from non-Latin Unicode scripts.

    Checked ranges: Cyrillic, Arabic, CJK unified ideographs, Devanagari,
    Hebrew, Thai, Hangul, Hiragana, Katakana, Georgian, Armenian.

    Pure character-range arithmetic — no language-ID library required.
    """
    alpha_chars = [c for c in text if c.isalpha()]
    if len(alpha_chars) < 4:
        return False

    def _non_latin(c: str) -> bool:
        cp = ord(c)
        return (
            0x0400 <= cp <= 0x04FF  # Cyrillic
            or 0x0600 <= cp <= 0x06FF  # Arabic
            or 0x4E00 <= cp <= 0x9FFF  # CJK Unified Ideographs
            or 0x0900 <= cp <= 0x097F  # Devanagari
            or 0x0590 <= cp <= 0x05FF  # Hebrew
            or 0x0E00 <= cp <= 0x0E7F  # Thai
            or 0xAC00 <= cp <= 0xD7AF  # Hangul syllables
            or 0x3040 <= cp <= 0x309F  # Hiragana
            or 0x30A0 <= cp <= 0x30FF  # Katakana
            or 0x10A0 <= cp <= 0x10FF  # Georgian
            or 0x0530 <= cp <= 0x058F  # Armenian
        )

    non_latin_count = sum(1 for c in alpha_chars if _non_latin(c))
    return non_latin_count / len(alpha_chars) > _NON_LATIN_DOMINANCE_THRESHOLD


# ── Mid-sentence capitalisation — proper-noun probe ────────────────────────
# Matches any non-first token that starts with an ASCII uppercase letter and
# is at least 5 characters long.  This catches foreign proper nouns embedded
# in otherwise lower-case Latin sentences, e.g. "j'ai vu Inception hier".
_LATIN_UPPER = re.compile(r'^[A-Z]')


def _has_mid_sentence_cap(text: str) -> bool:
    """Return True when a non-first token is an uppercase-initial Latin word of 5+ chars.

    Language-agnostic: detects proper nouns (titles, names, places) in any
    Latin-script language without relying on a word list.
    """
    tokens = text.split()
    for tok in tokens[1:]:
        # Strip leading punctuation that might be attached to the word
        clean = tok.lstrip("'\"\u2018\u201c\u00ab([{-")
        if len(clean) >= 5 and _LATIN_UPPER.match(clean):
            return True
    return False


def _has_strong_entity_names(text: str) -> bool:
    """Return True when >=2 non-first tokens are uppercase-initial Latin words of 5+ chars.

    Requires *two or more* such tokens to reduce false positives on sentencces
    like "Good, do you know Fight Club?" where "Fight" alone triggered REASON.
    Multi-word proper nouns (e.g. "Breaking Bad", "Pulp Fiction") reliably have
    2+ capitalised tokens and still route correctly.
    """
    tokens = text.split()
    count = 0
    for tok in tokens[1:]:
        clean = tok.lstrip("'\"\u2018\u201c\u00ab([{-")
        if len(clean) >= 5 and _LATIN_UPPER.match(clean):
            count += 1
            if count >= 2:
                return True
    return False


# ── Unicode script buckets — for cross-script mixing detection ─────────────
_SCRIPT_BUCKETS: list[tuple[str, int, int]] = [
    ("latin",      0x0041, 0x024F),
    ("cyrillic",   0x0400, 0x04FF),
    ("arabic",     0x0600, 0x06FF),
    ("cjk",        0x4E00, 0x9FFF),
    ("devanagari", 0x0900, 0x097F),
    ("hebrew",     0x0590, 0x05FF),
    ("thai",       0x0E00, 0x0E7F),
    ("hangul",     0xAC00, 0xD7AF),
    ("hiragana",   0x3040, 0x309F),
    ("katakana",   0x30A0, 0x30FF),
    ("georgian",   0x10A0, 0x10FF),
    ("armenian",   0x0530, 0x058F),
]


def _has_script_mixing(text: str) -> bool:
    """Return True when text contains alphabetic chars from >=2 Unicode script buckets.

    A message like "смотрю Inception сейчас" uses Cyrillic + Latin, which
    strongly signals that a named entity (film title, person, place) from one
    script is being referenced inside a sentence written in another.  This
    heuristic is purely arithmetic — no word lists, no language IDs.
    """
    buckets_seen: set[str] = set()
    for ch in text:
        if not ch.isalpha():
            continue
        cp = ord(ch)
        for name, lo, hi in _SCRIPT_BUCKETS:
            if lo <= cp <= hi:
                buckets_seen.add(name)
                break
        if len(buckets_seen) >= 2:
            return True
    return False


def _has_entity_signal(text: str) -> bool:
    """Return True when text has language-agnostic named-entity structural signals."""
    return (
        _contains_title_marker(text)
        or _has_mid_sentence_cap(text)
        or _has_script_mixing(text)
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
        has_target = bool(
            re.search(
                r"https?://|\b(?:[\w-]+\.){1,}[a-z]{2,}\b|\d{1,3}(?:\.\d{1,3}){3}",
                stripped,
                re.IGNORECASE,
            )
        )
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

    # Self-referential / casual bot-addressed queries — always TALK, even when
    # the phrasing overlaps with _REASON_PATTERNS (e.g. "what are you doing"
    # contains "what are", which _REASON_PATTERNS matches).
    if _is_casual_self_talk(stripped):
        return "TALK"

    # REASON — analytical questions
    if _REASON_PATTERNS.search(stripped):
        return "REASON"

    # Language-agnostic structural signals:
    # Each heuristic is purely arithmetic / typographic — no word lists,
    # no language identifiers, no hardcoded vocabulary.

    # 1. Title/entity markers (guillemets, curly quotes, title-cased runs)
    if _contains_title_marker(stripped):
        return "REASON"

    # 2. Mid-sentence capital catches single-entity references (e.g. Inception).
    #    Strong entity names catches multi-token proper nouns with better precision.
    if _has_mid_sentence_cap(stripped) or _has_strong_entity_names(stripped):
        return "REASON"

    # 3. Cross-script mixing: Cyrillic + Latin in the same message signals an
    #    entity name embedded in a foreign-language sentence, e.g.
    #    "смотрю Inception сейчас".
    if _has_script_mixing(stripped):
        return "REASON"

    # 4. Non-Latin dominant script: route to REASON only when the message
    #    carries a genuine analytical signal.  Pure personal statements
    #    ("Я не выспался, еле встал") have no question mark and no entity
    #    marker — they must fall through to TALK, not be swept here.
    #    Two language-agnostic signals are sufficient:
    #      - ends with ? / \uff1f / \u061f   (universal across all scripts)
    #      - structural title/entity marker (guillemets, title-cased run…)
    if _is_non_latin_dominant(stripped) and word_count >= 3:
        _is_q = stripped.rstrip().endswith(("?", "\uff1f", "\u061f"))
        if _is_q or _contains_title_marker(stripped):
            return "REASON"
        # No question, no entity signal → personal statement, fall through.

    # Opinion / preference / casual-sharing phrases are conversational.
    if _OPINION_PATTERNS.search(stripped):
        return "TALK"

    # Default for medium-length unclassified messages.
    # Gate on analytical intent: long personal narratives ("Я лежу в кроватке
    # и пытаюсь заснуть блин" — 8 words, no ?, no entity) stay in TALK.
    if word_count >= 8:
        _is_q = stripped.rstrip().endswith(("?", "\uff1f", "\u061f")) or bool(
            _REASON_PATTERNS.search(stripped)
        )
        _has_entity = _contains_title_marker(stripped) or _has_strong_entity_names(
            stripped
        )
        if _is_q or _has_entity:
            return "REASON"
        return "TALK"

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

    # Entity signals — any of these indicate a named reference that should
    # be looked up rather than invented.  All three checks are language-agnostic.
    if _has_entity_signal(text):
        tools.append("search")

    # URL or bare domain
    url_pattern = re.compile(
        r"https?://\S+|(?:^|\s)([a-zA-Z0-9-]+\.(com|net|org|io|dev|app|xyz|ai|co)\S*)",
        re.IGNORECASE,
    )
    if url_pattern.search(text):
        tools.append("site_check")
        tools.append("browser_fetch")

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


# ── System-monitoring intent patterns ───────────────────────────────────────
# Maps free-text monitoring intents to bare command names that match both:
#  - a handler method  _handle_<name>_cmd  on TelegramChannel
#  - a slash command   /<name>             in _SLASH_REGISTRY
# Checked before LLM dispatch to prevent ACT → search → hallucination.
_SYSTEM_INTENT_MAP: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\b(?:run\s+)?disk\s*(?:check|usage|space|info)\b"
            r"|\bcheck\s+disk\b"
            r"|\bshow\s+disk(?:\s+usage)?\b"
            r"|\bdf\b",
            re.IGNORECASE,
        ),
        "disk",
    ),
    (
        re.compile(
            r"\b(?:check|show|get|view)\s+(?:ram|memory)\b"
            r"|\b(?:ram|memory)\s+(?:check|usage|status|info)\b",
            re.IGNORECASE,
        ),
        "memory",
    ),
    (
        re.compile(
            r"\b(?:check|show|get|view)\s+cpu\b"
            r"|\bcpu\s+(?:check|usage|load|status|info)\b"
            r"|\bprocessor\s+(?:usage|load)\b",
            re.IGNORECASE,
        ),
        "cpu",
    ),
    (
        re.compile(
            r"\b(?:running|list|check|show|view)\s+services\b"
            r"|\bservices\s+(?:status|list|running)\b",
            re.IGNORECASE,
        ),
        "services",
    ),
    (
        re.compile(
            r"\b(?:open|list|check|show|view)\s+ports\b"
            r"|\bports\s+(?:open|list|check|status)\b",
            re.IGNORECASE,
        ),
        "ports",
    ),
]


def _match_system_intent(text: str) -> str | None:
    """Return a bare command name if *text* matches a known system-monitoring intent.

    The returned name corresponds to both a ``_handle_<name>_cmd`` method on
    ``TelegramChannel`` and a ``/<name>`` entry in ``_SLASH_REGISTRY``.  When
    matched the caller should bypass the LLM pipeline and call the handler
    directly to prevent the ACT → search → hallucination path.

    Returns ``None`` if no pattern matches.
    """
    for pattern, cmd in _SYSTEM_INTENT_MAP:
        if pattern.search(text):
            return cmd
    return None
