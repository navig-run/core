"""One global output language, with per-feature override — and never a hidden default.

Transcription and briefings were coming back in English for a Russian video. The
cause was not a missing setting: `STTConfig.language` shipped as a hard-coded
`"en"` and nothing upstream passed a language, so every transcription in navig
was *told* the audio was English — and a briefing built on that transcript
inherited the wrong language too.

The rule these tests pin: **nothing pinned means DETECT**, expressed on the wire
as `None`. Never `"auto"` (a provider would try to parse it as a language code),
never a default somebody else chose.
"""

from __future__ import annotations

import pytest

from navig.core.language import (
    CFG_LANGUAGE,
    language_code,
    normalise_language,
    resolve_language,
)


class _Cfg:
    def __init__(self, value):
        self._value = value

    def get(self, key, default=None):
        return self._value if key == CFG_LANGUAGE else default


def _with_global(monkeypatch, value):
    monkeypatch.setattr("navig.core.Config", lambda: _Cfg(value))


@pytest.mark.parametrize("value", ["", "  ", "auto", "AUTO", "detect", "source", "any", None])
def test_auto_shaped_values_mean_detect(value):
    assert normalise_language(value) is None


@pytest.mark.parametrize("value", ["Russian", "ru", "en-GB", " Spanish "])
def test_a_real_language_is_kept(value):
    assert normalise_language(value) == value.strip()


def test_global_preference_is_used_when_no_override(monkeypatch):
    _with_global(monkeypatch, "Russian")
    assert resolve_language() == "Russian"


def test_an_override_beats_the_global(monkeypatch):
    """One surface can be pinned without moving everything else."""
    _with_global(monkeypatch, "Russian")
    assert resolve_language("Japanese") == "Japanese"


def test_auto_override_falls_through_to_the_global(monkeypatch):
    """A feature left on 'auto' defers rather than overriding with nothing."""
    _with_global(monkeypatch, "Russian")
    assert resolve_language("auto") == "Russian"
    assert resolve_language("") == "Russian"


def test_nothing_configured_anywhere_is_detect(monkeypatch):
    _with_global(monkeypatch, "")
    assert resolve_language() is None


def test_unreadable_config_degrades_to_detect_not_to_a_guess(monkeypatch):
    def _boom():
        raise RuntimeError("config unreadable")

    monkeypatch.setattr("navig.core.Config", _boom)
    assert resolve_language() is None


# ── names vs codes: a pinned language must not be worse than no preference ────
#
# `user.language` is written by a human, so it holds a NAME. An LLM briefing wants
# exactly that ("Write the briefing in Russian"); a speech model wants an ISO code
# and rejects anything else outright — faster-whisper answers
# `'Russian' is not a valid language code`. Nothing bridged the two, so a
# perfectly reasonable `user.language: Russian` switched transcription OFF across
# navig: the STT call failed, the caller got None, and every surface reported
# "no speech detected". Measured on a real clip: None and 'ru' both transcribe,
# 'Russian' fails. This is the same bug as the hard-coded "en" above, one level on.


class TestLanguageCode:
    def test_a_display_name_becomes_a_code(self):
        assert language_code("Russian") == "ru"
        assert language_code("English") == "en"
        assert language_code("Japanese") == "ja"

    @pytest.mark.parametrize("written", ["russian", "RUSSIAN", "  Russian  "])
    def test_case_and_padding_do_not_matter(self, written):
        """A human typed this into a config file."""
        assert language_code(written) == "ru"

    def test_a_code_passes_through(self):
        """`user.language: ru` is just as reasonable a thing to write."""
        assert language_code("ru") == "ru"
        assert language_code("EN") == "en"

    @pytest.mark.parametrize("auto", ["", "auto", "detect", "source", "any", None])
    def test_auto_is_none(self, auto):
        assert language_code(auto) is None

    def test_an_unmappable_value_degrades_to_detect(self):
        """Auto-detect is what these models do well, and a rejected pin is total
        failure — so an unknown name must fall back to detection, never be passed
        through as a name. Passing it through IS the bug."""
        assert language_code("Klingon") is None
        assert language_code("Brazilian Portuguese") is None

    def test_every_mapped_code_is_one_a_model_accepts(self):
        """A name that maps onto a code no model takes just moves the failure."""
        from navig.core.language import _CODES, _NAME_TO_CODE

        assert set(_NAME_TO_CODE.values()) <= _CODES
        assert all(1 <= len(c) <= 3 and c.islower() for c in _CODES)

    def test_common_aliases_resolve(self):
        assert language_code("Mandarin") == "zh"
        assert language_code("Flemish") == "nl"
        assert language_code("Castilian") == "es"


class TestSttSeamConverts:
    """The conversion belongs at the ONE seam every backend passes through.

    Doing it per-caller is how a pinned language came to disable transcription
    instead of steering it — there is more than one caller and they each forgot.
    """

    async def test_a_name_reaches_the_backend_as_a_code(self, monkeypatch, tmp_path):
        from navig.agent import voice_input

        clip = tmp_path / "a.m4a"
        clip.write_bytes(b"\x00" * 64)
        seen: list = []

        handler = voice_input.VoiceInputHandler(
            voice_input.TranscriptionConfig(
                backend=voice_input.TranscriptionBackend.FASTER_WHISPER)
        )

        async def _spy(path, lang):
            seen.append(lang)
            return voice_input.TranscriptionResult(success=True, text="ok")

        monkeypatch.setattr(handler, "_transcribe_faster_whisper", _spy)
        await handler.transcribe(clip, language="Russian")
        assert seen == ["ru"], "the backend rejects a language NAME outright"

    async def test_an_unmappable_name_becomes_auto_not_a_failure(self, monkeypatch,
                                                                 tmp_path):
        from navig.agent import voice_input

        clip = tmp_path / "a.m4a"
        clip.write_bytes(b"\x00" * 64)
        seen: list = []

        handler = voice_input.VoiceInputHandler(
            voice_input.TranscriptionConfig(
                backend=voice_input.TranscriptionBackend.FASTER_WHISPER)
        )

        async def _spy(path, lang):
            seen.append(lang)
            return voice_input.TranscriptionResult(success=True, text="ok")

        monkeypatch.setattr(handler, "_transcribe_faster_whisper", _spy)
        await handler.transcribe(clip, language="Klingon")
        assert seen == [None], "detection beats a pin the model will refuse"
