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
    """The button layout `_handle_audio_file_message` ACTUALLY builds.

    These used to rebuild the rows inline and assert against their own copy — they
    would have passed with the real function deleted, which is close to what happened:
    nothing ever called it (see TestHandlerIsWired). Call the real thing instead.
    """

    @staticmethod
    def _invoke(audio: dict) -> list:
        """Run the real handler against a stub channel and return its keyboard."""
        import asyncio

        from navig.gateway.channels.telegram_voice import TelegramVoiceMixin

        sent: dict = {}

        class _Stub(TelegramVoiceMixin):
            async def send_message(self, chat_id, text, **kwargs):
                sent["chat_id"] = chat_id
                sent["text"] = text
                sent.update(kwargs)
                return {"message_id": 1}

        asyncio.run(_Stub()._handle_audio_file_message(42, audio, 7))
        assert sent, "the handler sent nothing at all"
        return sent["keyboard"]

    def _collect_cb_data(self, keyboard: list) -> list[str]:
        return [btn["callback_data"] for row in keyboard for btn in row]

    def test_detect_language_absent_for_music(self):
        """Music files should NOT have a Detect Language button."""
        kb = self._invoke({"title": "Bohemian Rhapsody", "performer": "Queen"})
        assert not any("audmsg:lang" in d for d in self._collect_cb_data(kb))

    def test_detect_language_present_for_speech(self):
        """Voice recordings SHOULD have a Detect Language button."""
        kb = self._invoke({"file_name": "voice_memo_001.ogg"})
        assert any("audmsg:lang" in d for d in self._collect_cb_data(kb))

    def test_the_core_actions_are_always_offered(self):
        kb = self._invoke({"title": "Bohemian Rhapsody"})
        data = self._collect_cb_data(kb)
        for action in ("transcribe", "identify", "info", "dismiss"):
            assert any(d.startswith(f"audmsg:{action}:") for d in data), f"missing {action}"


# ---------------------------------------------------------------------------
# The wiring — a handler nothing calls is a feature nobody has
# ---------------------------------------------------------------------------


class TestHandlerIsWired:
    """`_handle_audio_file_message` drew this card since it was written and was never
    invoked: the only references in the package were its own `def` and a comment. Every
    .mp3/.wav therefore hit the generic "can't read files through Telegram yet" ack.
    These pin the two halves of the fix — the payload detector, and the call itself."""

    def test_an_audio_message_is_detected(self):
        from navig.gateway.channels.telegram import _audio_file_payload

        assert _audio_file_payload({"audio": {"file_id": "a"}}) == {"file_id": "a"}

    def test_a_wav_sent_as_a_document_is_detected(self):
        """The case a user describes as "I sent it a wav": the sending client decides
        whether it arrives as `audio` or `document`, and .wav essentially always
        arrives as a document."""
        from navig.gateway.channels.telegram import _audio_file_payload

        doc = {"file_id": "d", "file_name": "take-1.wav", "mime_type": "application/octet-stream"}
        assert _audio_file_payload({"document": doc}) == doc

    def test_an_audio_mime_with_no_filename_is_detected(self):
        from navig.gateway.channels.telegram import _audio_file_payload

        doc = {"file_id": "d", "mime_type": "audio/mpeg"}
        assert _audio_file_payload({"document": doc}) == doc

    def test_a_voice_note_is_NOT_treated_as_a_file(self):
        """Anti-vacuity, and a real behaviour: a voice note is someone talking TO the
        bot, so it must keep going straight to STT rather than getting a card."""
        from navig.gateway.channels.telegram import _audio_file_payload

        assert _audio_file_payload({"voice": {"file_id": "v"}}) is None

    def test_a_pdf_is_not_audio(self):
        from navig.gateway.channels.telegram import _audio_file_payload

        doc = {"file_id": "d", "file_name": "report.pdf", "mime_type": "application/pdf"}
        assert _audio_file_payload({"document": doc}) is None

    def test_the_dispatch_actually_calls_the_handler(self):
        """The half that was missing. Source-level, because reaching this branch in a
        live update needs the whole channel; the point is that a call EXISTS at all."""
        import inspect

        from navig.gateway.channels import telegram

        src = inspect.getsource(telegram)
        assert "await self._handle_audio_file_message(" in src, (
            "nothing calls _handle_audio_file_message — the audio action card is dead "
            "code again and every audio file falls through to the generic ack"
        )


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
import pytest

from navig.gateway.channels.audio_menu.config import PROVIDERS

pytestmark = pytest.mark.integration


class TestAudioConfigLabels:
    def test_no_gender_symbols(self):
        assert chr(9792) not in str(PROVIDERS)
        assert chr(9794) not in str(PROVIDERS)


# ---------------------------------------------------------------------------
# Re-timing actions (⏩ / 🐢) — the buttons, the guards, and the failure text
# ---------------------------------------------------------------------------


class TestRetimingActions:
    def _keyboard(self, audio: dict) -> list:
        return TestSmartKeyboard._invoke(audio)

    def test_the_card_offers_speed_and_slowed(self):
        data = [b["callback_data"] for row in self._keyboard({"title": "Track"}) for b in row]
        assert any(d.startswith("audmsg:speed:") for d in data)
        assert any(d.startswith("audmsg:slowed:") for d in data)

    def test_every_real_button_fits_telegrams_64_byte_callback_limit(self):
        """The existing size test asserts against a hand-written string, so it cannot
        notice a new button. Measure the ones the handler actually emits."""
        for row in self._keyboard({"title": "Track"}):
            for btn in row:
                payload = btn["callback_data"]
                assert len(payload.encode()) <= 64, f"{payload} is {len(payload.encode())} bytes"

    def test_the_button_label_and_the_applied_rate_cannot_drift(self):
        from navig.gateway.channels.telegram_voice import _SPEED_UP_RATE

        labels = [b["text"] for row in self._keyboard({"title": "T"}) for b in row]
        assert any(f"{_SPEED_UP_RATE:g}" in lbl for lbl in labels), (
            "the speed button must render the rate it actually applies"
        )


class TestOversizeAndExpiry:
    """Two failures a user can act on, which must not surface as opaque errors."""

    def test_a_file_over_telegrams_download_limit_is_refused_before_downloading(self):
        import asyncio

        from navig.gateway.channels.telegram_voice import TelegramVoiceMixin

        class _Stub(TelegramVoiceMixin):
            downloaded = False

            async def _download_telegram_file(self, file_id):
                _Stub.downloaded = True
                return None

        ok, msg = asyncio.run(
            _Stub()._edit_audio_and_reply(1, "fid", "speed", meta={"file_size": 40 * 1024 * 1024})
        )
        assert ok is False
        assert "20 MB" in msg, msg
        assert _Stub.downloaded is False, "a getFile round-trip for a file we cannot fetch is waste"

    def test_a_failed_download_reports_it_rather_than_claiming_success(self):
        import asyncio

        from navig.gateway.channels.telegram_voice import TelegramVoiceMixin

        class _Stub(TelegramVoiceMixin):
            async def _download_telegram_file(self, file_id):
                return None

        ok, msg = asyncio.run(_Stub()._edit_audio_and_reply(1, "fid", "speed", meta={}))
        assert ok is False and "download" in msg.lower()

    def test_an_expired_card_says_so_instead_of_using_the_short_id_as_a_file_id(self):
        """`_af_cache` keeps 500 entries; a button on an older message finds nothing.
        The old fallback passed the 12-hex short_id to getFile as if it were a file_id,
        so the user got an opaque failure instead of "send it again"."""
        import inspect

        from navig.gateway.channels import telegram_keyboards

        src = inspect.getsource(telegram_keyboards.CallbackHandler._handle_audio_file_callback)
        assert "expired" in src.lower(), "an evicted card must be reported as expired"
        guard = src.index("expired")
        assert guard < src.index('file_id = meta.get('), (
            "the expiry check must come BEFORE the file_id fallback it exists to prevent"
        )


class TestUploadHonesty:
    def test_a_rejected_send_is_not_reported_as_delivered(self):
        """`sendAudio` returning an error payload means the user never got the file."""
        import asyncio

        from navig.gateway.channels.telegram_voice import TelegramVoiceMixin

        class _Stub(TelegramVoiceMixin):
            async def _api_call_multipart(self, method, data=None, files=None):
                return {"ok": False, "description": "FILE_TOO_BIG"}

        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as fh:
            fh.write(b"\x00" * 16)
            path = fh.name
        try:
            assert asyncio.run(_Stub()._send_audio_document(1, path, title="t")) is False
        finally:
            import os

            os.unlink(path)

    def test_an_unreadable_file_is_a_failure_not_an_exception(self):
        import asyncio

        from navig.gateway.channels.telegram_voice import TelegramVoiceMixin

        class _Stub(TelegramVoiceMixin):
            async def _api_call_multipart(self, method, data=None, files=None):
                raise AssertionError("must not attempt an upload it cannot read")

        assert asyncio.run(_Stub()._send_audio_document(1, "/no/such/file.mp3", title="t")) is False


class TestConversionDoesNotBlockTheEventLoop:
    """ffmpeg is a BLOCKING subprocess with a 300s ceiling.

    Run from the coroutine directly it holds the event loop for the whole conversion —
    every other chat, the uplink heartbeat and the daemon's timers stop with it. A 20 MB
    track is minutes of freeze, and it would look like the bot had died.
    """

    def test_ffmpeg_runs_off_the_event_loop_thread(self, tmp_path):
        import asyncio
        import threading

        import navig.media.audio_edit as audio_edit
        from navig.gateway.channels.telegram_voice import TelegramVoiceMixin

        loop_thread: dict = {}
        worker_thread: dict = {}
        out = tmp_path / "converted.mp3"
        out.write_bytes(b"\x00" * 8)

        def _fake_speed(src, dst, rate, **kw):
            worker_thread["name"] = threading.current_thread().name
            return audio_edit.EditResult(path=out, rate=rate, pitch_shifted=False, filters="x")

        src = tmp_path / "in.mp3"
        src.write_bytes(b"\x00" * 8)

        class _Stub(TelegramVoiceMixin):
            async def _download_telegram_file(self, file_id):
                loop_thread["name"] = threading.current_thread().name
                return str(src)

            async def _send_audio_document(self, *a, **kw):
                return True

        original = audio_edit.speed
        audio_edit.speed = _fake_speed
        try:
            ok, _ = asyncio.run(_Stub()._edit_audio_and_reply(1, "fid", "speed", meta={}))
        finally:
            audio_edit.speed = original

        assert ok is True
        assert worker_thread.get("name"), "the conversion never ran"
        assert worker_thread["name"] != loop_thread["name"], (
            "ffmpeg ran on the event-loop thread — a long conversion would freeze the "
            "whole gateway, not just this chat"
        )
