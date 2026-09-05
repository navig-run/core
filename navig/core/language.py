"""navig.core.language — the one place that answers "what language should output be in?".

Language is cross-cutting: speech-to-text, AI briefings, OCR summaries and chat
replies all need the same answer, and a per-feature setting for each would drift
into a set of switches that disagree. So there is ONE durable preference,
``user.language`` (alongside ``user.name``), and features may take an override.

The default is **auto**, meaning *follow the content* — paste a Russian video,
get a Russian transcript and a Russian briefing. That is the right default for a
tool whose whole job is consuming other people's media; pinning English would be
a choice nobody made, which is exactly the bug this module was written to end
(``STTConfig.language`` shipped as a hard-coded ``"en"``, so every transcription
in navig was *told* the audio was English).

``None`` is the canonical "auto" value on the wire, because that is what every
downstream API wants in order to detect the language itself — never the string
``"auto"``, which a provider would try to interpret as a language code.
"""

from __future__ import annotations

#: Durable, global output-language preference. "" / "auto" ⇒ follow the content.
CFG_LANGUAGE = "user.language"

#: Values that all mean "don't pin a language; detect it".
_AUTO = {"", "auto", "detect", "source", "any"}


def normalise_language(value: object) -> str | None:
    """Map a configured value onto a language name/code, or ``None`` for auto."""
    text = str(value or "").strip()
    return None if text.lower() in _AUTO else text


def resolve_language(override: object = "") -> str | None:
    """The language output should be in: *override* → ``user.language`` → auto.

    Returns ``None`` when nothing is pinned, which every caller must treat as
    "let the provider detect it" rather than substituting a default of its own.
    A feature-level *override* (e.g. ``telegram.tiktok_cards.language``) wins so
    one surface can be pinned without moving everything else.
    """
    if (explicit := normalise_language(override)) is not None:
        return explicit
    try:
        from navig.core import Config

        return normalise_language(Config().get(CFG_LANGUAGE, ""))
    except Exception:  # noqa: BLE001 — config unreadable → auto, never a crash
        return None


# ── names vs codes ────────────────────────────────────────────────────────────
#
# ``user.language`` is written by a HUMAN, so it holds a name — "Russian". Two
# kinds of consumer read it and they want different things:
#
#   * an LLM briefing wants the NAME ("Write the briefing in Russian");
#   * speech-to-text wants an ISO-639-1 CODE, and rejects anything else outright:
#     faster-whisper answers ``'Russian' is not a valid language code``.
#
# Nothing bridged the two, so a perfectly reasonable ``user.language: Russian``
# silently turned every transcription in navig OFF — the STT call failed, the
# caller got ``None``, and the surface reported "no speech detected". Measured on
# a real clip: ``None`` and ``'ru'`` both transcribe, ``'Russian'`` fails.
#
# That is the same failure this module was written to end, one level along: the
# docstring above records ``STTConfig.language`` shipping a hard-coded ``"en"``.
# A pinned language must never be worse than no preference at all.

#: English language names → the ISO code the speech models accept. Generated
#: from Whisper's own ``TO_LANGUAGE_CODE``, filtered to the codes faster-whisper
#: actually accepts, so a name here is always a code a model will take.
_NAME_TO_CODE: dict[str, str] = {
    'afrikaans': 'af', 'albanian': 'sq', 'amharic': 'am', 'arabic': 'ar', 'armenian': 'hy',
    'assamese': 'as', 'azerbaijani': 'az', 'bashkir': 'ba', 'basque': 'eu',
    'belarusian': 'be', 'bengali': 'bn', 'bosnian': 'bs', 'breton': 'br', 'bulgarian': 'bg',
    'burmese': 'my', 'cantonese': 'yue', 'castilian': 'es', 'catalan': 'ca',
    'chinese': 'zh', 'croatian': 'hr', 'czech': 'cs', 'danish': 'da', 'dutch': 'nl',
    'english': 'en', 'estonian': 'et', 'faroese': 'fo', 'finnish': 'fi', 'flemish': 'nl',
    'french': 'fr', 'galician': 'gl', 'georgian': 'ka', 'german': 'de', 'greek': 'el',
    'gujarati': 'gu', 'haitian': 'ht', 'haitian creole': 'ht', 'hausa': 'ha',
    'hawaiian': 'haw', 'hebrew': 'he', 'hindi': 'hi', 'hungarian': 'hu', 'icelandic': 'is',
    'indonesian': 'id', 'italian': 'it', 'japanese': 'ja', 'javanese': 'jw',
    'kannada': 'kn', 'kazakh': 'kk', 'khmer': 'km', 'korean': 'ko', 'lao': 'lo',
    'latin': 'la', 'latvian': 'lv', 'letzeburgesch': 'lb', 'lingala': 'ln',
    'lithuanian': 'lt', 'luxembourgish': 'lb', 'macedonian': 'mk', 'malagasy': 'mg',
    'malay': 'ms', 'malayalam': 'ml', 'maltese': 'mt', 'mandarin': 'zh', 'maori': 'mi',
    'marathi': 'mr', 'moldavian': 'ro', 'moldovan': 'ro', 'mongolian': 'mn',
    'myanmar': 'my', 'nepali': 'ne', 'norwegian': 'no', 'nynorsk': 'nn', 'occitan': 'oc',
    'panjabi': 'pa', 'pashto': 'ps', 'persian': 'fa', 'polish': 'pl', 'portuguese': 'pt',
    'punjabi': 'pa', 'pushto': 'ps', 'romanian': 'ro', 'russian': 'ru', 'sanskrit': 'sa',
    'serbian': 'sr', 'shona': 'sn', 'sindhi': 'sd', 'sinhala': 'si', 'sinhalese': 'si',
    'slovak': 'sk', 'slovenian': 'sl', 'somali': 'so', 'spanish': 'es', 'sundanese': 'su',
    'swahili': 'sw', 'swedish': 'sv', 'tagalog': 'tl', 'tajik': 'tg', 'tamil': 'ta',
    'tatar': 'tt', 'telugu': 'te', 'thai': 'th', 'tibetan': 'bo', 'turkish': 'tr',
    'turkmen': 'tk', 'ukrainian': 'uk', 'urdu': 'ur', 'uzbek': 'uz', 'valencian': 'ca',
    'vietnamese': 'vi', 'welsh': 'cy', 'yiddish': 'yi', 'yoruba': 'yo',
}

#: Every code the above maps onto — used to recognise a value that is ALREADY a
#: code, so ``user.language: ru`` passes through untouched.
_CODES: frozenset[str] = frozenset(_NAME_TO_CODE.values())


def language_code(value: object) -> str | None:
    """The ISO code a speech model will accept, or ``None`` for auto-detect.

    Accepts a display name ("Russian", "Brazilian Portuguese" → no, but
    "portuguese" yes), a code already ("ru"), or an auto value ("" / "auto").

    **An unrecognised value becomes ``None``, deliberately.** Auto-detect is what
    a transcription model does well and a rejected pin is total failure, so a
    language we cannot map must degrade to "detect it" rather than to nothing at
    all. The alternative — passing the name through — is the bug this exists for.
    """
    text = normalise_language(value)
    if text is None:
        return None
    key = text.strip().lower()
    if key in _CODES:
        return key
    return _NAME_TO_CODE.get(key)
