"""LanguageDetector: script-heuristic + langdetect language identification."""

from __future__ import annotations

import functools
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_FRENCH_MARKERS = (
    "à",
    "â",
    "é",
    "è",
    "ê",
    "ë",
    "î",
    "ï",
    "ô",
    "ù",
    "û",
    "ü",
    "ç",
    "œ",
    "æ",
)
_FRENCH_KEYWORDS = (
    "bonjour",
    "salut",
    "merci",
    "s'il vous",
    "comment",
    "pourquoi",
    "qu'est-ce",
    "je suis",
    "c'est",
    "est-ce que",
    "oui",
    "non",
    "bonsoir",
    "au revoir",
)

# Language codes recognised as first-class by this module.
_SUPPORTED_CODES: frozenset[str] = frozenset(
    {"fr", "en", "ru", "zh", "es", "de", "it", "pt", "ar", "ja", "ko"}
)
# langdetect Chinese variant codes that all normalise to internal "zh".
_ZH_VARIANTS: frozenset[str] = frozenset({"zh-cn", "zh-tw", "zh"})


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LanguageResult:
    """Outcome of a language-detection call.

    Frozen so it is safe to return from an LRU-cached function — callers
    cannot mutate a cached instance and corrupt subsequent lookups.

    Attributes:
        code:       Internal language code (one of _SUPPORTED_CODES or ``"mixed"``)
        confidence: Probability estimate in ``[0.0, 1.0]``.
        method:     Detection path taken: ``"langdetect"``, ``"script"``,
                    or ``"fallback"``.
    """

    code: str
    confidence: float
    method: str


# ---------------------------------------------------------------------------
# Pre-built enforcement blocks keyed by language code.
# ---------------------------------------------------------------------------

_INSTRUCTIONS: dict[str, str] = {
    "ru": (
        "### ABSOLUTE LANGUAGE RULE — HIGHEST PRIORITY ###\n"
        "You MUST reply ONLY in Russian using Cyrillic script.\n"
        "NEVER output Chinese characters (汉字/漢字), Japanese kana, "
        "or any CJK Unicode (U+4E00–U+9FFF, U+3400–U+4DBF).\n"
        "NEVER mix languages. Every single word of your reply must be Russian.\n"
        "If you are unsure of a term, transliterate it into Cyrillic.\n"
        "Violation of this rule makes the entire response invalid.\n"
        "### END LANGUAGE RULE ###"
    ),
    "zh": (
        "### ABSOLUTE LANGUAGE RULE — HIGHEST PRIORITY ###\n"
        "You MUST reply ONLY in Simplified Chinese (简体中文).\n"
        "NEVER output Cyrillic, Latin, Arabic, or any non-CJK script.\n"
        "NEVER mix languages. Every single word of your reply must be Chinese.\n"
        "If you are unsure of a term, render it in Chinese characters or pinyin.\n"
        "Violation of this rule makes the entire response invalid.\n"
        "### END LANGUAGE RULE ###"
    ),
    "es": (
        "### ABSOLUTE LANGUAGE RULE — HIGHEST PRIORITY ###\n"
        "You MUST reply ONLY in Spanish (español) using the Latin script.\n"
        "NEVER output Cyrillic, Arabic, CJK, or any non-Latin script.\n"
        "NEVER mix languages. Every single word of your reply must be Spanish.\n"
        "If you are unsure of a term, transliterate it into Spanish orthography.\n"
        "Violation of this rule makes the entire response invalid.\n"
        "### END LANGUAGE RULE ###"
    ),
    "de": (
        "### ABSOLUTE LANGUAGE RULE — HIGHEST PRIORITY ###\n"
        "You MUST reply ONLY in German (Deutsch) using the Latin script.\n"
        "NEVER output Cyrillic, Arabic, CJK, or any non-Latin script.\n"
        "NEVER mix languages. Every single word of your reply must be German.\n"
        "If you are unsure of a term, transliterate it into German orthography.\n"
        "Violation of this rule makes the entire response invalid.\n"
        "### END LANGUAGE RULE ###"
    ),
    "it": (
        "### ABSOLUTE LANGUAGE RULE — HIGHEST PRIORITY ###\n"
        "You MUST reply ONLY in Italian (italiano) using the Latin script.\n"
        "NEVER output Cyrillic, Arabic, CJK, or any non-Latin script.\n"
        "NEVER mix languages. Every single word of your reply must be Italian.\n"
        "If you are unsure of a term, transliterate it into Italian orthography.\n"
        "Violation of this rule makes the entire response invalid.\n"
        "### END LANGUAGE RULE ###"
    ),
    "pt": (
        "### ABSOLUTE LANGUAGE RULE — HIGHEST PRIORITY ###\n"
        "You MUST reply ONLY in Portuguese (português) using the Latin script.\n"
        "NEVER output Cyrillic, Arabic, CJK, or any non-Latin script.\n"
        "NEVER mix languages. Every single word of your reply must be Portuguese.\n"
        "If you are unsure of a term, transliterate it into Portuguese orthography.\n"
        "Violation of this rule makes the entire response invalid.\n"
        "### END LANGUAGE RULE ###"
    ),
    "ar": (
        "### ABSOLUTE LANGUAGE RULE — HIGHEST PRIORITY ###\n"
        "You MUST reply ONLY in Arabic (العربية) using the Arabic script.\n"
        "NEVER output Latin characters, Cyrillic, CJK, or any non-Arabic script.\n"
        "NEVER mix languages. Every single word of your reply must be Arabic.\n"
        "If you are unsure of a term, render it in Arabic script.\n"
        "Violation of this rule makes the entire response invalid.\n"
        "### END LANGUAGE RULE ###"
    ),
    "en": (
        "### LANGUAGE RULE ###\n"
        "Reply in English only. Do not mix in other languages or scripts.\n"
        "### END LANGUAGE RULE ###"
    ),
    "fr": (
        "### LANGUAGE RULE ###\n"
        "Reply ONLY in French (français). Do not mix in English or other languages.\n"
        "### END LANGUAGE RULE ###"
    ),
    "mixed": (
        "### LANGUAGE RULE ###\n"
        "Reply in the same language as the user's message.\n"
        "Do not mix multiple languages or scripts in your reply.\n"
        "### END LANGUAGE RULE ###"
    ),
}


# ---------------------------------------------------------------------------
# Script-based heuristic detector (Unicode + French keyword scoring)
# ---------------------------------------------------------------------------


def _script_detect(text: str) -> str:
    """Return a language code using Unicode script ranges and French keywords.

    Returns one of: ``'ru'``, ``'zh'``, ``'en'``, ``'fr'``, ``'mixed'``.
    Never raises.
    """
    has_cyrillic = any("\u0400" <= ch <= "\u04ff" for ch in text)
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in text)
    has_latin = any(("A" <= ch <= "Z") or ("a" <= ch <= "z") for ch in text)
    has_arabic = any("\u0600" <= ch <= "\u06ff" for ch in text)

    if has_cyrillic and not has_cjk:
        return "ru"
    if has_cjk and not has_cyrillic:
        return "zh"
    if has_arabic and not has_cyrillic and not has_cjk:
        return "ar"
    if has_latin and not has_cyrillic and not has_cjk:
        lower = text.lower()
        score = sum(1 for m in _FRENCH_MARKERS if m in lower) + sum(
            2 for k in _FRENCH_KEYWORDS if k in lower
        )
        return "fr" if score >= 2 else "en"
    return "mixed"


# ---------------------------------------------------------------------------
# detect_confidence — module-level public helper
# ---------------------------------------------------------------------------


def detect_confidence(message: str) -> float:
    """Return the probability of the top-ranked language from ``langdetect``.

    Returns a float in ``[0.0, 1.0]``.  Returns ``0.5`` on any error
    (import failure, ``LangDetectException``, empty input, …).
    """
    try:
        from langdetect import detect_langs as _dl  # type: ignore[import]

        results = _dl(message)
        if results:
            return float(results[0].prob)
        return 0.5
    except Exception:
        return 0.5


# ---------------------------------------------------------------------------
# Core detection function
# ---------------------------------------------------------------------------


def _detect_language_code(message: str) -> LanguageResult:
    """Detect the language of *message* and return a :class:`LanguageResult`.

    Decision tree:

    * **< 4 words** — skip ``langdetect`` entirely; use :func:`_script_detect`.
      ``method="script"``, ``confidence=1.0``.
    * **≥ 4 words, langdetect succeeds** — map ISO 639-1 code to internal
      codes; unmapped codes become ``"mixed"``.  ``method="langdetect"``;
      confidence from :func:`detect_confidence`.
    * **≥ 4 words, langdetect raises** — fall back to :func:`_script_detect`.
      ``method="fallback"``, ``confidence=0.5``.
    """
    word_count = len(message.split())

    if word_count < 4:
        code = _script_detect(message)
        return LanguageResult(code=code, confidence=1.0, method="script")

    try:
        from langdetect import detect as _ld  # type: ignore[import]

        raw_code: str = _ld(message)
        # Normalise Chinese variants.
        if raw_code in _ZH_VARIANTS:
            raw_code = "zh"
        # Map to internal code set.
        code = raw_code if raw_code in _SUPPORTED_CODES else "mixed"
        confidence = detect_confidence(message)
        return LanguageResult(code=code, confidence=confidence, method="langdetect")
    except Exception:
        code = _script_detect(message)
        return LanguageResult(code=code, confidence=0.5, method="fallback")


# ---------------------------------------------------------------------------
# LRU-cached wrapper — use this for repeated calls with the same message.
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=50)
def _cached_detect(message: str) -> LanguageResult:
    """Thin LRU-cached wrapper around :func:`_detect_language_code`.

    Cache key is the *message* string verbatim.
    """
    return _detect_language_code(message)


# ---------------------------------------------------------------------------
# Module-level instruction builder
# ---------------------------------------------------------------------------


def _build_language_instruction(code: str) -> str:
    """Return the pre-built language-enforcement block for *code*."""
    return _INSTRUCTIONS.get(code, _INSTRUCTIONS["mixed"])


# ---------------------------------------------------------------------------
# Public LanguageDetector class — backward-compatible str-returning API
# ---------------------------------------------------------------------------


class LanguageDetector:
    """
    Identifies the dominant language/script in a user message.
    Delegates to :func:`_cached_detect` for detection and
    :func:`_build_language_instruction` for prompt engineering.
    Guarantees: :meth:`detect` never raises; always returns a valid language
    code string.
    """

    def __init__(self, language_override: str = "") -> None:
        self._override = language_override

    def detect(self, text: str) -> str:
        """Return language code string (e.g. ``'ru'``, ``'zh'``, ``'en'``, ``'fr'``)."""
        if self._override:
            return self._override
        return _cached_detect(text).code

    def build_instruction(self, lang_code: str) -> str:
        """Return the pre-built language-enforcement block for *lang_code*."""
        return _build_language_instruction(lang_code)
