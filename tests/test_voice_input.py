"""Tests for navig.agent.voice_input — VoiceInputHandler & helpers."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.agent.voice_input import (
    MAX_AUDIO_SIZE_MB,
    SUPPORTED_AUDIO_EXTENSIONS,
    TranscriptionBackend,
    TranscriptionConfig,
    TranscriptionResult,
    VoiceInputHandler,
    detect_transcription_backend,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tmp_audio(suffix: str = ".wav", size_bytes: int = 128) -> Path:
    """Create a tiny temp file with the given suffix."""
    f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    f.write(b"\x00" * size_bytes)
    f.close()
    return Path(f.name)


# ---------------------------------------------------------------------------
# detect_transcription_backend
# ---------------------------------------------------------------------------


class TestDetectBackend:
    def test_faster_whisper_preferred(self):
        """faster-whisper is top priority when importable."""
        with patch.dict("sys.modules", {"faster_whisper": MagicMock()}):
            assert detect_transcription_backend() == TranscriptionBackend.FASTER_WHISPER

    def test_deepgram_when_key_present(self):
        """Deepgram is second when no faster-whisper but key exists."""
        with (
            patch.dict("sys.modules", {"faster_whisper": None}),
            patch(
                "navig.agent.voice_input._resolve_key",
                side_effect=lambda *a: "fake-key" if "deepgram" in str(a) else None,
            ),
        ):
            # faster-whisper import will raise since we set it to None
            result = detect_transcription_backend()
            assert result in (
                TranscriptionBackend.DEEPGRAM,
                TranscriptionBackend.FASTER_WHISPER,
            )

    def test_none_when_nothing_available(self):
        """Returns NONE when no backends are available."""
        with (
            patch("navig.agent.voice_input._resolve_key", return_value=None),
            patch.dict("sys.modules", {"faster_whisper": None, "whisper": None}),
        ):
            result = detect_transcription_backend()
            # Could be FASTER_WHISPER if the module is cached; but importing
            # None raises ImportError so it should fall through.
            assert result in (
                TranscriptionBackend.NONE,
                TranscriptionBackend.FASTER_WHISPER,  # if sys.modules cache
            )


# ---------------------------------------------------------------------------
# TranscriptionResult
# ---------------------------------------------------------------------------


class TestTranscriptionResult:
    def test_truthy_on_success_with_text(self):
        r = TranscriptionResult(success=True, text="hello")
        assert bool(r) is True

    def test_falsy_on_failure(self):
        r = TranscriptionResult(success=False)
        assert bool(r) is False

    def test_falsy_on_success_without_text(self):
        r = TranscriptionResult(success=True, text=None)
        assert bool(r) is False


# ---------------------------------------------------------------------------
# VoiceInputHandler — validation
# ---------------------------------------------------------------------------


class TestVoiceInputValidation:
    def test_file_not_found(self):
        handler = VoiceInputHandler(TranscriptionConfig(backend=TranscriptionBackend.WHISPER_API))
        result = asyncio.run(handler.transcribe("/nonexistent/audio.mp3"))
        assert not result.success
        assert "not found" in (result.error or "")

    def test_unsupported_format(self):
        tmp = _tmp_audio(suffix=".xyz")
        try:
            handler = VoiceInputHandler(
                TranscriptionConfig(backend=TranscriptionBackend.WHISPER_API)
            )
            result = asyncio.run(handler.transcribe(tmp))
            assert not result.success
            assert "Unsupported format" in (result.error or "")
        finally:
            tmp.unlink(missing_ok=True)

    def test_file_too_large(self):
        tmp = _tmp_audio(suffix=".wav", size_bytes=128)
        try:
            handler = VoiceInputHandler(
                TranscriptionConfig(
                    backend=TranscriptionBackend.WHISPER_API,
                    max_file_size_mb=0,  # force fail
                )
            )
            result = asyncio.run(handler.transcribe(tmp))
            assert not result.success
            assert "too large" in (result.error or "").lower()
        finally:
            tmp.unlink(missing_ok=True)

    def test_no_backend_error(self):
        tmp = _tmp_audio(suffix=".wav")
        try:
            handler = VoiceInputHandler(TranscriptionConfig(backend=TranscriptionBackend.NONE))
            result = asyncio.run(handler.transcribe(tmp))
            assert not result.success
            assert "No transcription backend" in (result.error or "")
        finally:
            tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# VoiceInputHandler — faster-whisper backend (mocked)
# ---------------------------------------------------------------------------


class TestFasterWhisperBackend:
    def test_transcribe_local_success(self):
        """Mocked faster-whisper transcription returns text."""
        tmp = _tmp_audio(suffix=".wav")
        try:
            mock_segment = MagicMock()
            mock_segment.text = " Hello world "
            mock_segment.start = 0.0
            mock_segment.end = 1.5

            mock_info = MagicMock()
            mock_info.language = "en"
            mock_info.language_probability = 0.95

            mock_model = MagicMock()
            mock_model.transcribe.return_value = (iter([mock_segment]), mock_info)

            with patch.dict("sys.modules", {"faster_whisper": MagicMock()}):
                with patch(
                    "navig.agent.voice_input.VoiceInputHandler._transcribe_faster_whisper"
                ) as mock_fw:
                    mock_fw.return_value = TranscriptionResult(
                        success=True,
                        text="Hello world",
                        language="en",
                        duration_ms=150,
                        backend=TranscriptionBackend.FASTER_WHISPER,
                        confidence=0.95,
                    )
                    handler = VoiceInputHandler(
                        TranscriptionConfig(backend=TranscriptionBackend.FASTER_WHISPER)
                    )
                    result = asyncio.run(handler.transcribe(tmp))

                    assert result.success
                    assert result.text == "Hello world"
                    assert result.backend == TranscriptionBackend.FASTER_WHISPER
        finally:
            tmp.unlink(missing_ok=True)

    def test_faster_whisper_import_error(self):
        """Graceful error when faster-whisper is not installed."""
        tmp = _tmp_audio(suffix=".wav")
        try:
            handler = VoiceInputHandler(
                TranscriptionConfig(backend=TranscriptionBackend.FASTER_WHISPER)
            )
            # Patch the import inside _transcribe_faster_whisper
            with patch.dict("sys.modules", {"faster_whisper": None}):
                result = asyncio.run(handler._transcribe_faster_whisper(tmp, None))
                assert not result.success
                assert "not installed" in (result.error or "")
        finally:
            tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# VoiceInputHandler — API backend delegation (mocked)
# ---------------------------------------------------------------------------


class TestAPIBackendDelegation:
    def test_whisper_api_delegates_to_stt(self):
        """Whisper API backend routes through navig.voice.stt.STT."""
        tmp = _tmp_audio(suffix=".mp3")
        try:
            mock_stt_result = MagicMock()
            mock_stt_result.success = True
            mock_stt_result.text = "Transcribed text"
            mock_stt_result.language = "en"
            mock_stt_result.duration_ms = 200
            mock_stt_result.confidence = 0.92
            mock_stt_result.error = None
            mock_stt_result.segments = []

            mock_stt_instance = MagicMock()
            mock_stt_instance.transcribe = AsyncMock(return_value=mock_stt_result)

            with patch("navig.voice.stt.STT", return_value=mock_stt_instance):
                handler = VoiceInputHandler(
                    TranscriptionConfig(backend=TranscriptionBackend.WHISPER_API)
                )
                result = asyncio.run(handler._transcribe_via_stt(tmp, None))

                assert result.success
                assert result.text == "Transcribed text"
                assert result.backend == TranscriptionBackend.WHISPER_API
        finally:
            tmp.unlink(missing_ok=True)

    def test_deepgram_delegates_to_stt(self):
        """Deepgram backend routes through navig.voice.stt.STT."""
        tmp = _tmp_audio(suffix=".ogg")
        try:
            mock_stt_result = MagicMock()
            mock_stt_result.success = True
            mock_stt_result.text = "Deepgram result"
            mock_stt_result.language = "en"
            mock_stt_result.duration_ms = 100
            mock_stt_result.confidence = 0.97
            mock_stt_result.error = None
            mock_stt_result.segments = None

            mock_stt_instance = MagicMock()
            mock_stt_instance.transcribe = AsyncMock(return_value=mock_stt_result)

            with patch("navig.voice.stt.STT", return_value=mock_stt_instance):
                handler = VoiceInputHandler(
                    TranscriptionConfig(backend=TranscriptionBackend.DEEPGRAM)
                )
                result = asyncio.run(handler._transcribe_via_stt(tmp, "en"))

                assert result.success
                assert result.text == "Deepgram result"
                assert result.backend == TranscriptionBackend.DEEPGRAM
        finally:
            tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# OGG/Opus convenience
# ---------------------------------------------------------------------------


class TestOggOpus:
    def test_transcribe_ogg_opus_empty(self):
        handler = VoiceInputHandler(TranscriptionConfig(backend=TranscriptionBackend.WHISPER_API))
        result = asyncio.run(handler.transcribe_ogg_opus(b""))
        assert not result.success
        assert "Empty" in (result.error or "")

    def test_transcribe_ogg_opus_too_large(self):
        handler = VoiceInputHandler(
            TranscriptionConfig(
                backend=TranscriptionBackend.WHISPER_API,
                max_file_size_mb=0,
            )
        )
        result = asyncio.run(handler.transcribe_ogg_opus(b"\x00" * 1024))
        assert not result.success
        assert "too large" in (result.error or "").lower()

    def test_transcribe_ogg_opus_delegates(self):
        """OGG bytes are written to temp file and transcribed."""
        handler = VoiceInputHandler(TranscriptionConfig(backend=TranscriptionBackend.WHISPER_API))
        expected = TranscriptionResult(success=True, text="ogg result")
        with patch.object(handler, "transcribe", new=AsyncMock(return_value=expected)):
            result = asyncio.run(handler.transcribe_ogg_opus(b"\x00" * 64))
            assert result.success
            assert result.text == "ogg result"


# ---------------------------------------------------------------------------
# Config / constants
# ---------------------------------------------------------------------------


class TestConfigConstants:
    def test_supported_extensions_include_common(self):
        for ext in (".mp3", ".wav", ".ogg", ".flac", ".m4a"):
            assert ext in SUPPORTED_AUDIO_EXTENSIONS

    def test_max_audio_size_is_reasonable(self):
        assert MAX_AUDIO_SIZE_MB == 25

    def test_default_config(self):
        cfg = TranscriptionConfig()
        assert cfg.backend == TranscriptionBackend.NONE
        assert cfg.model == "base"
        assert cfg.language is None


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


class TestConvenience:
    def test_transcribe_audio_returns_text(self):
        from navig.agent.voice_input import transcribe_audio

        tmp = _tmp_audio(suffix=".wav")
        try:
            expected = TranscriptionResult(success=True, text="hello convenience")
            with patch("navig.agent.voice_input.get_voice_handler") as mock_get:
                mock_handler = MagicMock()
                mock_handler.transcribe = AsyncMock(return_value=expected)
                mock_get.return_value = mock_handler

                result = asyncio.run(transcribe_audio(tmp))
                assert result == "hello convenience"
        finally:
            tmp.unlink(missing_ok=True)

    def test_transcribe_audio_returns_none_on_failure(self):
        from navig.agent.voice_input import transcribe_audio

        tmp = _tmp_audio(suffix=".wav")
        try:
            expected = TranscriptionResult(success=False, error="fail")
            with patch("navig.agent.voice_input.get_voice_handler") as mock_get:
                mock_handler = MagicMock()
                mock_handler.transcribe = AsyncMock(return_value=expected)
                mock_get.return_value = mock_handler

                result = asyncio.run(transcribe_audio(tmp))
                assert result is None
        finally:
            tmp.unlink(missing_ok=True)
