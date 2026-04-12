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
# Language labels and generic instruction templates.
# ---------------------------------------------------------------------------

_LANGUAGE_LABELS: dict[str, str] = {
    "en": "English",
    "fr": "French",
    "ru": "Russian",
    "zh": "Simplified Chinese",
    "es": "Spanish",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "ar": "Arabic",
    "ja": "Japanese",
    "ko": "Korean",
}

_GENERIC_INSTRUCTION = (
    "### LANGUAGE CONTEXT ###\n"
    "Reply in the same language as the user's current message.\n"
    "If the user switches language, follow the new language immediately.\n"
    "Do not lock to a previous session language when the current message indicates another language.\n"
    "Avoid unnecessary language mixing unless the user explicitly asks for bilingual output.\n"
    "### END LANGUAGE CONTEXT ###"
)


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
    """Return generic multilingual guidance with optional language hint."""
    normalized = (code or "").strip().lower()
    if normalized in _LANGUAGE_LABELS:
        label = _LANGUAGE_LABELS[normalized]
        return (
            "### LANGUAGE CONTEXT ###\n"
            f"Prefer replying in {label} when it matches the user's current message.\n"
            "If the current user message is in a different language, follow the current message language.\n"
            "Avoid unnecessary language mixing unless the user explicitly asks for bilingual output.\n"
            "### END LANGUAGE CONTEXT ###"
        )
    return _GENERIC_INSTRUCTION


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
