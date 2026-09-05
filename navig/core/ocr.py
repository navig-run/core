"""Shared OCR helpers.

Every OCR surface in navig — the Telegram catalog, the TikTok Transcript button,
the inbox extractor, the media briefing, ``navig wiki`` — reads through
:func:`extract_ocr_text_from_image_bytes`. That function returns ``None`` for
*three* different situations: the image genuinely has no text, the text was too
short to be meaningful, and **OCR is not installed at all**. Collapsing the last
one into "no text found" is why a missing dependency looks like a working
feature that simply never finds anything.

So the availability question has its own answer: :func:`ocr_unavailable_reason`.
A caller that reports a result to a human should ask it before saying "nothing
found", and say "I had no tool to look with" instead.

OCR needs **two** things — the ``pytesseract`` Python package *and* the Tesseract
binary — and neither implies the other. Installing one without the other is the
common half-configured state, so the reason names which one is missing.
"""

from __future__ import annotations

import functools
import io
import logging

logger = logging.getLogger(__name__)

#: What to tell a user whose install cannot OCR. The Python package ships in an
#: extra; the binary is a separate OS-level install on every platform.
OCR_INSTALL_HINT = (
    "install OCR with `pip install navig[ocr]` plus the Tesseract binary "
    "(scoop install tesseract / brew install tesseract / apt install tesseract-ocr)"
)


@functools.lru_cache(maxsize=1)
def ocr_unavailable_reason() -> str | None:
    """Why local OCR cannot run here, or ``None`` when it can.

    Cached: the binary probe spawns ``tesseract -v``, which must not happen once
    per frame. A long-lived daemon therefore keeps its answer until restart —
    acceptable, because installing OCR into a running daemon is not a live
    operation anyway. Call ``ocr_unavailable_reason.cache_clear()`` in a test.
    """
    try:
        import pytesseract  # type: ignore  # noqa: PLC0415
    except Exception:  # noqa: BLE001 — any import failure means unusable
        return "the pytesseract package isn't installed"
    try:
        from PIL import Image  # type: ignore  # noqa: F401,PLC0415
    except Exception:  # noqa: BLE001
        return "Pillow isn't installed"
    try:
        # Resolves through pytesseract's own configured `tesseract_cmd`, so this
        # still passes on an install where the binary is off PATH but pointed at
        # explicitly — which a bare `shutil.which` would call missing.
        pytesseract.get_tesseract_version()
    except Exception:  # noqa: BLE001
        return "the Tesseract binary isn't installed or isn't on PATH"
    return None


def ocr_available() -> bool:
    """True when local OCR can actually run."""
    return ocr_unavailable_reason() is None


# ── which language Tesseract reads in ─────────────────────────────────────────
#
# Tesseract defaults to **English** and does not decline when pointed at another
# script — it returns confident-looking nonsense. A Russian TikTok slide came
# back as ``"- He fap. STO / Bot Kak"``, which reads as content and is not, and
# no caller could tell. The confidence floor below cannot catch it either: the
# glyphs really are what Tesseract thinks they are.
#
# The user already told us the language — `user.language` — so the answer is to
# pass it, here, at the one function every OCR surface reads through.

#: ISO-639-1 → the Tesseract traineddata name (ISO-639-2/T).
#:
#: Deliberately NOT exhaustive, and it does not need to be: a code is only ever
#: requested when Tesseract reports that pack installed, so a missing entry
#: degrades to Tesseract's own default — today's behaviour — rather than to a
#: crash or a worse read. Add entries freely; a wrong one cannot break a read.
_TESSERACT_LANG: dict[str, str] = {
    "ar": "ara", "bg": "bul", "bn": "ben", "ca": "cat", "cs": "ces", "da": "dan",
    "de": "deu", "el": "ell", "en": "eng", "es": "spa", "et": "est", "fa": "fas",
    "fi": "fin", "fr": "fra", "he": "heb", "hi": "hin", "hr": "hrv", "hu": "hun",
    "id": "ind", "it": "ita", "ja": "jpn", "ka": "kat", "kk": "kaz", "ko": "kor",
    "lt": "lit", "lv": "lav", "mk": "mkd", "ms": "msa", "nl": "nld", "no": "nor",
    "pl": "pol", "pt": "por", "ro": "ron", "ru": "rus", "sk": "slk", "sl": "slv",
    "sq": "sqi", "sr": "srp", "sv": "swe", "ta": "tam", "te": "tel", "th": "tha",
    "tr": "tur", "uk": "ukr", "ur": "urd", "vi": "vie", "zh": "chi_sim",
}


@functools.lru_cache(maxsize=1)
def installed_ocr_languages() -> frozenset[str]:
    """Traineddata packs Tesseract can actually load here.

    Cached like :func:`ocr_unavailable_reason`, and for the same reason: it
    spawns the binary, and this is consulted once per image.
    """
    try:
        import pytesseract  # type: ignore  # noqa: PLC0415

        return frozenset(pytesseract.get_languages(config=""))
    except Exception as exc:  # noqa: BLE001 — unknown ⇒ pin nothing, use the default
        logger.debug("could not list OCR languages: %s", exc)
        return frozenset()


def _preferred_ocr_pack(preference: object = "") -> str | None:
    """The Tesseract pack name for the pinned language, mapped or verbatim."""
    from navig.core.language import language_code, resolve_language  # noqa: PLC0415

    pinned = resolve_language(preference)
    if not pinned:
        return None
    # Someone may write the pack name straight into the config ("rus", "chi_sim").
    raw = pinned.strip().lower()
    if raw in installed_ocr_languages():
        return raw
    code = language_code(pinned)
    return _TESSERACT_LANG.get(code) if code else None


def ocr_language(preference: object = "") -> str | None:
    """The ``lang=`` string to hand Tesseract, or ``None`` for its own default.

    Returns ``"<pinned>+eng"`` when both packs exist — captions mix scripts
    constantly, Tesseract reads several languages in one pass, and keeping
    English costs little. **A pack that is not installed is never requested:**
    Tesseract raises on an unknown ``lang``, which would turn a degraded read
    into no read at all.

    Not cached, deliberately — the binary probe behind it is, and `user.language`
    can change under a running daemon via ``navig config set``.
    """
    installed = installed_ocr_languages()
    pack = _preferred_ocr_pack(preference)
    if not pack or pack not in installed:
        return None
    return f"{pack}+eng" if pack != "eng" and "eng" in installed else pack


def ocr_language_gap(preference: object = "") -> str | None:
    """Why OCR will misread the pinned language, or ``None`` when it won't.

    "I read it with the wrong model" is a different answer from "there was no
    text", and only this tells them apart. Returns ``None`` when OCR is missing
    altogether — :func:`ocr_unavailable_reason` already owns that message, and
    two warnings for one broken install is noise.
    """
    if ocr_unavailable_reason() is not None:
        return None
    from navig.core.language import resolve_language  # noqa: PLC0415

    pinned = resolve_language(preference)
    if not pinned or ocr_language(preference) is not None:
        return None
    pack = _preferred_ocr_pack(preference)
    have = ", ".join(sorted(installed_ocr_languages())) or "none"
    if pack is None:
        return f"no Tesseract language pack is mapped for {pinned} (installed: {have})"
    return (f"the '{pack}' Tesseract language pack isn't installed, so {pinned} text "
            f"is read with the wrong model (installed: {have})")


#: Per-word confidence floor. Tesseract does not decline to answer: pointed at a
#: textureless video frame it returns confident-looking glyphs
#: (``"aed KOO | 1 mine | | lh Nt ot ot?"`` — measured on a real TikTok), and
#: `image_to_string` hands them over with no signal that they are noise.
#: `image_to_data` carries per-word confidence, and it separates the two cleanly.
#: Measured: rendered text "BUY NOW - LIMITED OFFER" → 5 words, **median 95**, all
#: surviving any threshold; four frames of that TikTok → three with no words at
#: all and one with 11 words, **median 39**, of which exactly one (``|``) clears
#: 60 and none clears the alphanumeric rule below. 60 sits in the gap.
OCR_MIN_CONFIDENCE = 60.0

#: A kept result must contain at least one run of this many alphanumerics.
#: Confidence alone is not enough: punctuation fragments like ``|`` score high
#: because they genuinely look like what tesseract thinks they are.
_MIN_WORD_RUN = 3


def _confident_text(data: dict) -> str | None:
    """Reassemble `image_to_data` output, keeping only confident words.

    Line structure is preserved (words are grouped by block/paragraph/line) so a
    multi-line caption does not collapse into one run-on string.
    """
    lines: dict[tuple, list[str]] = {}
    for i, raw_word in enumerate(data.get("text") or []):
        word = (raw_word or "").strip()
        if not word:
            continue
        try:
            conf = float(data["conf"][i])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if conf < OCR_MIN_CONFIDENCE:
            continue
        key = (
            data.get("block_num", [0] * (i + 1))[i],
            data.get("par_num", [0] * (i + 1))[i],
            data.get("line_num", [0] * (i + 1))[i],
        )
        lines.setdefault(key, []).append(word)

    text = "\n".join(" ".join(words) for _key, words in sorted(lines.items())).strip()
    if not text:
        return None
    # Require something word-shaped. Without this, a frame of visual texture
    # yields a line of stray pipes and dashes that reads as "found some text".
    run = 0
    for char in text:
        run = run + 1 if char.isalnum() else 0
        if run >= _MIN_WORD_RUN:
            return text
    return None


def extract_ocr_text_from_image_bytes(file_bytes: bytes) -> str | None:
    """Best-effort OCR text extraction from image bytes.

    Returns ``None`` when OCR is unavailable, the image has no text, or what was
    read is not confidently text (see :data:`OCR_MIN_CONFIDENCE`). Ask
    :func:`ocr_unavailable_reason` to tell "no text" from "no OCR engine" apart
    before reporting an empty result to a human, and :func:`ocr_language_gap` to
    tell either from "read with the wrong language model".

    Reads in the user's pinned language when its pack is installed
    (:func:`ocr_language`); the signature stays single-argument on purpose, so
    every one of the surfaces that call this — wiki, inbox, the media briefing,
    the Telegram catalog, the TikTok transcript — gets it without changing.
    """
    try:
        import pytesseract  # type: ignore  # noqa: PLC0415
        from PIL import Image  # type: ignore  # noqa: PLC0415

        img = Image.open(io.BytesIO(file_bytes))
        lang = ocr_language()
        data = pytesseract.image_to_data(
            img, output_type=pytesseract.Output.DICT, **({"lang": lang} if lang else {})
        )
        return _confident_text(data)
    except Exception as exc:  # noqa: BLE001
        logger.debug("OCR extraction failed: %s", exc)
        return None
