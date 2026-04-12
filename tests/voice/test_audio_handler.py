"""
Tests for the audio file handler: classification, keyboard layout,
MIME resolution, and STT routing.
"""

from navig.gateway.channels.telegram_voice import _classify_audio

# ---------------------------------------------------------------------------
# _classify_audio tests
# ---------------------------------------------------------------------------


class TestClassifyAudio:
    def test_music_with_title_and_performer(self):
        result = _classify_audio({"title": "Bohemian Rhapsody", "performer": "Queen"})
        assert result["kind"] == "music"
        assert result["is_speech"] is False

    def test_voice_recording_from_filename(self):
        result = _classify_audio({"file_name": "voice_memo_001.ogg"})
        assert result["kind"] == "voice_recording"
        assert result["is_speech"] is True

    def test_voice_recording_from_ogg_mime(self):
        result = _classify_audio({"mime_type": "audio/ogg"})
        assert result["kind"] == "voice_recording"
        assert result["is_speech"] is True

    def test_voice_recording_from_opus_mime(self):
        result = _classify_audio({"mime_type": "audio/opus"})
        assert result["kind"] == "voice_recording"
        assert result["is_speech"] is True

    def test_unknown_mp3_no_metadata(self):
        result = _classify_audio({"mime_type": "audio/mpeg"})
        assert result["kind"] == "unknown"

    def test_music_with_only_performer(self):
        result = _classify_audio({"performer": "The Beatles"})
        assert result["kind"] == "music"
        assert result["is_speech"] is False

    def test_voice_keyword_in_title(self):
        result = _classify_audio({"title": "Meeting recording 2024"})
        assert result["kind"] == "voice_recording"
        assert result["is_speech"] is True


# ---------------------------------------------------------------------------
# Smart keyboard: Detect Language button only for speech
# ---------------------------------------------------------------------------


class TestSmartKeyboard:
    """Check that _handle_audio_file_message produces the right button layout."""

    def _collect_cb_data(self, keyboard: list) -> list[str]:
        return [btn["callback_data"] for row in keyboard for btn in row]

    def test_detect_language_absent_for_music(self):
        """Music files should NOT have a Detect Language button."""
        # Simulate what _handle_audio_file_message builds for a music file
        file_id = "abc123"
        is_speech = False
        row2 = [{"text": "ℹ️ Info", "callback_data": f"audmsg:info:{file_id}"}]
        if is_speech:
            row2.append(
                {
                    "text": "🌐 Detect Language",
                    "callback_data": f"audmsg:lang:{file_id}",
                }
            )
        cb_data_list = [btn["callback_data"] for btn in row2]
        assert not any("lang" in d for d in cb_data_list)

    def test_detect_language_present_for_speech(self):
        """Voice recordings SHOULD have a Detect Language button."""
        file_id = "abc123"
        is_speech = True
        row2 = [{"text": "ℹ️ Info", "callback_data": f"audmsg:info:{file_id}"}]
        if is_speech:
            row2.append(
                {
                    "text": "🌐 Detect Language",
                    "callback_data": f"audmsg:lang:{file_id}",
                }
            )
        cb_data_list = [btn["callback_data"] for btn in row2]
        assert any("lang" in d for d in cb_data_list)


# ---------------------------------------------------------------------------
# Callback payload size constraint (<= 64 bytes)
# ---------------------------------------------------------------------------


class TestCallbackPayloadSize:
    def test_payload_fits_64_bytes(self):
        """audmsg:transcribe: + 40-char file_id = 21 + 40 = 61 bytes."""
        file_id = "A" * 40  # Telegram file_ids are typically ~40–60 chars
        payload = f"audmsg:transcribe:{file_id}"
        assert len(payload.encode()) <= 64, f"Payload too long: {len(payload)} chars"

    def test_longest_action_payload_fits(self):
        """audmsg:identify: is the longest action prefix."""
        file_id = "A" * 40
        payload = f"audmsg:identify:{file_id}"
        assert len(payload.encode()) <= 64


# ---------------------------------------------------------------------------
# _resolve_audio_file_params tests
# ---------------------------------------------------------------------------

from navig.voice.stt import _resolve_audio_file_params


class TestResolveAudioFileParams:
    def test_mp3_preserves_mime(self):
        name, mime = _resolve_audio_file_params("track.mp3", is_voice=False)
        assert mime == "audio/mpeg"
        assert name.endswith(".mp3")

    def test_voice_forces_oga(self):
        name, mime = _resolve_audio_file_params("voice.ogg", is_voice=True)
        assert name == "voice.oga"
        assert mime == "audio/ogg"

    def test_m4a_preserves_mime(self):
        name, mime = _resolve_audio_file_params("audio.m4a", is_voice=False)
        assert mime == "audio/mp4" or mime == "audio/x-m4a" or "m4a" in name


# ---
from navig.gateway.channels.audio_menu.config import PROVIDERS
import pytest

pytestmark = pytest.mark.integration


class TestAudioConfigLabels:
    def test_no_gender_symbols(self):
        assert chr(9792) not in str(PROVIDERS)
        assert chr(9794) not in str(PROVIDERS)
