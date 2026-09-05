"""OCR must read in the language the user already pinned.

Tesseract defaults to **English** and does not decline when pointed at another
script — it returns confident-looking nonsense, and the confidence floor cannot
catch it because the glyphs really are what Tesseract thinks they are. Measured
on a real Russian TikTok slide, same image, same code path:

    lang=(default) → "- He fap. STO / Bot Kak"                      21 chars
    lang=rus+eng   → "Интуиция - не дар. Это Функция …"              93 chars

`user.language` already held "Russian". Nothing passed it, so **every** OCR
surface in navig — wiki, inbox, the media briefing, the Telegram catalog, the
TikTok transcript — was reading non-Latin text as noise and presenting it as
content. The fix is one argument, applied at the single function they all read
through.

The safety rule these tests pin: **never request a pack Tesseract does not
have.** It raises on an unknown `lang`, which would turn a degraded read into no
read at all — strictly worse than the bug being fixed.
"""

from __future__ import annotations

import pytest

from navig.core import ocr as ocr_mod


def _clear(fn) -> None:
    """Drop a cached probe's memo, tolerating a monkeypatched stand-in.

    Teardown can run while the attribute is still the test's lambda, which has no
    `cache_clear` — the same guard `tests/cli/test_doctor_media_tools.py` uses.
    """
    clear = getattr(fn, "cache_clear", None)
    if clear is not None:
        clear()


@pytest.fixture(autouse=True)
def _clear_probes():
    """The installed-pack probe spawns the binary and is cached process-wide."""
    _clear(ocr_mod.installed_ocr_languages)
    _clear(ocr_mod.ocr_unavailable_reason)
    yield
    _clear(ocr_mod.installed_ocr_languages)
    _clear(ocr_mod.ocr_unavailable_reason)


def _installed(monkeypatch, *packs):
    monkeypatch.setattr(ocr_mod, "installed_ocr_languages",
                        lambda: frozenset(packs))


def _pinned(monkeypatch, value):
    monkeypatch.setattr("navig.core.language.resolve_language",
                        lambda override="": value)


class TestOcrLanguage:
    def test_a_pinned_language_is_read_in_that_language(self, monkeypatch):
        _installed(monkeypatch, "eng", "rus")
        _pinned(monkeypatch, "Russian")
        assert ocr_mod.ocr_language() == "rus+eng"

    def test_english_is_kept_alongside(self, monkeypatch):
        """Captions mix scripts constantly and Tesseract reads several at once."""
        _installed(monkeypatch, "eng", "deu")
        _pinned(monkeypatch, "German")
        assert ocr_mod.ocr_language() == "deu+eng"

    def test_english_pinned_is_not_doubled(self, monkeypatch):
        _installed(monkeypatch, "eng")
        _pinned(monkeypatch, "English")
        assert ocr_mod.ocr_language() == "eng"

    def test_without_english_the_pack_stands_alone(self, monkeypatch):
        _installed(monkeypatch, "rus")
        _pinned(monkeypatch, "Russian")
        assert ocr_mod.ocr_language() == "rus"

    def test_an_uninstalled_pack_is_never_requested(self, monkeypatch):
        """Tesseract RAISES on an unknown lang — asking for one would turn a
        wrong-model read into no read at all."""
        _installed(monkeypatch, "eng")
        _pinned(monkeypatch, "Russian")
        assert ocr_mod.ocr_language() is None

    def test_nothing_pinned_uses_tesseracts_own_default(self, monkeypatch):
        _installed(monkeypatch, "eng", "rus")
        _pinned(monkeypatch, None)
        assert ocr_mod.ocr_language() is None

    def test_a_pack_name_written_straight_into_config_is_honoured(self, monkeypatch):
        """`user.language: rus` is a reasonable thing for someone to write."""
        _installed(monkeypatch, "eng", "rus", "chi_sim")
        _pinned(monkeypatch, "chi_sim")
        assert ocr_mod.ocr_language() == "chi_sim+eng"

    def test_an_unmapped_language_falls_back_rather_than_guessing(self, monkeypatch):
        _installed(monkeypatch, "eng")
        _pinned(monkeypatch, "Klingon")
        assert ocr_mod.ocr_language() is None

    def test_it_is_not_cached_so_a_config_change_is_seen(self, monkeypatch):
        """`user.language` can change under a running daemon via `navig config set`.
        Only the binary probe behind this is cached."""
        _installed(monkeypatch, "eng", "rus")
        _pinned(monkeypatch, None)
        assert ocr_mod.ocr_language() is None
        _pinned(monkeypatch, "Russian")
        assert ocr_mod.ocr_language() == "rus+eng"

    def test_every_mapped_pack_looks_like_a_tesseract_name(self):
        for code, pack in ocr_mod._TESSERACT_LANG.items():
            assert len(code) == 2 and code.islower(), code
            assert pack.islower() and 3 <= len(pack) <= 8, pack

    def test_a_probe_failure_degrades_to_the_default(self, monkeypatch):
        """An unreadable pack list must not stop OCR — it just stops pinning."""
        monkeypatch.setattr(ocr_mod, "installed_ocr_languages", frozenset)
        _pinned(monkeypatch, "Russian")
        assert ocr_mod.ocr_language() is None


class TestOcrLanguageGap:
    """'I read it with the wrong model' is a different answer from 'no text'."""

    def test_silent_when_the_pack_is_present(self, monkeypatch):
        _installed(monkeypatch, "eng", "rus")
        _pinned(monkeypatch, "Russian")
        monkeypatch.setattr(ocr_mod, "ocr_unavailable_reason", lambda: None)
        assert ocr_mod.ocr_language_gap() is None

    def test_silent_when_nothing_is_pinned(self, monkeypatch):
        _installed(monkeypatch, "eng")
        _pinned(monkeypatch, None)
        monkeypatch.setattr(ocr_mod, "ocr_unavailable_reason", lambda: None)
        assert ocr_mod.ocr_language_gap() is None

    def test_it_names_the_missing_pack(self, monkeypatch):
        _installed(monkeypatch, "eng")
        _pinned(monkeypatch, "Russian")
        monkeypatch.setattr(ocr_mod, "ocr_unavailable_reason", lambda: None)
        gap = ocr_mod.ocr_language_gap() or ""
        assert "rus" in gap and "Russian" in gap
        assert "eng" in gap, "say what IS installed, so the message is actionable"

    def test_it_says_so_when_no_pack_is_mapped(self, monkeypatch):
        _installed(monkeypatch, "eng")
        _pinned(monkeypatch, "Klingon")
        monkeypatch.setattr(ocr_mod, "ocr_unavailable_reason", lambda: None)
        assert "Klingon" in (ocr_mod.ocr_language_gap() or "")

    def test_it_defers_when_ocr_is_missing_entirely(self, monkeypatch):
        """Two warnings for one broken install is noise; the bigger one wins."""
        _installed(monkeypatch, "eng")
        _pinned(monkeypatch, "Russian")
        monkeypatch.setattr(ocr_mod, "ocr_unavailable_reason",
                            lambda: "the Tesseract binary isn't installed")
        assert ocr_mod.ocr_language_gap() is None


class TestTheSharedReaderUsesIt:
    """One function; eight surfaces. The language belongs here, not at each one."""

    def _run(self, monkeypatch, lang):
        seen: dict = {}

        class _FakeTess:
            class Output:
                DICT = "dict"

            @staticmethod
            def image_to_data(img, output_type=None, **kwargs):
                seen.update(kwargs)
                return {"text": ["hello"], "conf": ["95"],
                        "block_num": [0], "par_num": [0], "line_num": [0]}

        class _FakeImage:
            @staticmethod
            def open(_buf):
                return object()

        import sys
        import types

        tess = types.ModuleType("pytesseract")
        tess.Output = _FakeTess.Output
        tess.image_to_data = _FakeTess.image_to_data
        pil = types.ModuleType("PIL")
        pil_image = types.ModuleType("PIL.Image")
        pil_image.open = _FakeImage.open
        pil.Image = pil_image
        monkeypatch.setitem(sys.modules, "pytesseract", tess)
        monkeypatch.setitem(sys.modules, "PIL", pil)
        monkeypatch.setitem(sys.modules, "PIL.Image", pil_image)
        monkeypatch.setattr(ocr_mod, "ocr_language", lambda *a, **k: lang)

        text = ocr_mod.extract_ocr_text_from_image_bytes(b"png-bytes")
        return text, seen

    def test_the_pinned_language_reaches_tesseract(self, monkeypatch):
        text, kwargs = self._run(monkeypatch, "rus+eng")
        assert text == "hello"
        assert kwargs.get("lang") == "rus+eng"

    def test_no_lang_is_passed_when_nothing_is_pinned(self, monkeypatch):
        """Passing lang=None would be an error, not a default."""
        text, kwargs = self._run(monkeypatch, None)
        assert text == "hello"
        assert "lang" not in kwargs

    def test_the_signature_stays_single_argument(self):
        """Eight call sites and a dozen test fakes are `lambda b: None`; the
        language is resolved inside, so none of them had to change."""
        import inspect

        params = list(
            inspect.signature(ocr_mod.extract_ocr_text_from_image_bytes).parameters
        )
        assert params == ["file_bytes"]
