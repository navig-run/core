"""
Voice pipeline mixin for TelegramChannel — Phase 2 extraction.

Provides STT transcription, TTS synthesis, audio upload, and multipart
HTTP helpers.  Consumed via multiple-inheritance:

    class TelegramChannel(TelegramVoiceMixin, ...): ...

Every method uses ``self`` to call back into ``TelegramChannel``
(``send_message``, ``_api_call``, ``_keep_recording``, ``base_url``,
``_bot_token``, ``_session``, ``allowed_users``, ``_voice_last``,
``_debug_users``).

Public surface:
  _resolve_api_key           static — env-var → vault key helper (DUP-3 fix)
  _transcribe_voice_message  Telegram voice → STT → transcript
  _prepare_for_tts           static — strip markdown for TTS
  _maybe_send_voice          conditional TTS reply dispatch (returns bool)
  _send_voice_reply          multi-provider TTS → Telegram voice
  _send_audio_bytes          write bytes → temp file → _send_voice_file
  _send_voice_file           upload local audio file to Telegram
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Metadata cache for received audio files: file_id -> dict
# Populated in _handle_audio_file_message; read by the audmsg: callback handler.
_af_cache: dict = {}

_VOICE_HTTP_TIMEOUT: int = 30  # TTS provider request timeout (audio synthesis can be slow)

# ── Audio classifier helpers ─────────────────────────────────────────────────

# Keywords in title/filename that strongly suggest a speech recording
_SPEECH_INDICATORS = (
    "voice",
    "memo",
    "recording",
    "voicemail",
    "lecture",
    "interview",
    "podcast",
    "speech",
    "talk",
    "meeting",
    "call",
    "note",
    "dictation",
)

# MIME types that are exclusively used for Telegram voice notes / speech
_VOICE_MIMES = {"audio/ogg", "audio/opus", "audio/x-opus"}



def _classify_audio(audio: dict) -> dict:
    """Heuristically classify an audio dict as music / voice_recording / unknown.

    Returns a dict with keys:
        kind      : "music" | "voice_recording" | "unknown"
        is_speech : bool  — True when kind is voice_recording
    """
    title = (audio.get("title") or "").lower()
    file_name = (audio.get("file_name") or "").lower()
    performer = (audio.get("performer") or "").lower()
    mime = (audio.get("mime_type") or "").lower()

    # Telegram tags a file as audio/music when it has title or performer metadata
    if title and performer:
        return {"kind": "music", "is_speech": False}

    # MIME types that are only used for speech recordings
    if mime in _VOICE_MIMES:
        return {"kind": "voice_recording", "is_speech": True}

    # Check filename / title for speech keywords
    combined = f"{title} {file_name}"
    if any(kw in combined for kw in _SPEECH_INDICATORS):
        return {"kind": "voice_recording", "is_speech": True}

    # Has a performer but no title, or has a title but no performer — likely music
    if performer or title:
        return {"kind": "music", "is_speech": False}

    return {"kind": "unknown", "is_speech": False}


# ── Session management ───────────────────────────────────────────────────────
try:
    from navig.gateway.channels.telegram_sessions import get_session_manager

    _HAS_SESSIONS = True
except ImportError:
    _HAS_SESSIONS = False

# ── Voice STT/TTS pipeline ────────────────────────────────────────────────────
try:
    from navig.voice.stt import STT as _STT
    from navig.voice.stt import STTConfig as _STTConfig
    from navig.voice.stt import STTProvider as _STTProvider
    from navig.voice.tts import TTS as _TTS
    from navig.voice.tts import TTSConfig as _TTSConfig
    from navig.voice.tts import TTSProvider as _TTSProvider

    _HAS_VOICE = True
except ImportError:
    _HAS_VOICE = False


# ── Module-level language tables (DUP-4 fix: single source) ──────────────────


def _detect_lang(text: str) -> str:
    """Return ISO-639-1 code based on dominant Unicode script and accent chars."""
    cyrillic = sum(1 for c in text if "\u0400" <= c <= "\u04ff")
    arabic = sum(1 for c in text if "\u0600" <= c <= "\u06ff")
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    fr_chars = sum(1 for c in text if c in "àâæçéèêëîïôùûüÿœÀÂÆÇÉÈÊËÎÏÔÙÛÜŸŒ")
    de_chars = sum(1 for c in text if c in "äöüÄÖÜß")
    es_chars = sum(1 for c in text if c in "ñáéíóúÑÁÉÍÓÚ¿¡")
    if cyrillic > 3:
        return "ru"
    if arabic > 3:
        return "ar"
    if cjk > 3:
        return "zh"
    if fr_chars > 2:
        return "fr"
    if de_chars > 2:
        return "de"
    if es_chars > 2:
        return "es"
    return "en"


#: Edge TTS neural voice per ISO-639-1 code
_EDGE_VOICE: dict[str, str] = {
    "ru": "ru-RU-SvetlanaNeural",
    "ar": "ar-SA-ZariyahNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KatjaNeural",
    "es": "es-ES-ElviraNeural",
    "it": "it-IT-ElsaNeural",
    "en": "en-US-AriaNeural",
}

#: Google Cloud TTS language-code per ISO-639-1 code
_GOOGLE_LANG: dict[str, str] = {
    "ru": "ru-RU",
    "ar": "ar-XA",
    "zh": "zh-CN",
    "fr": "fr-FR",
    "de": "de-DE",
    "es": "es-ES",
    "it": "it-IT",
    "en": "en-US",
}


class TelegramVoiceMixin:
    """Mixin for TelegramChannel — voice STT/TTS pipeline methods."""

    # ── Shared key-resolution helper (DUP-3 fix) ─────────────────────────────

    @staticmethod
    def _resolve_api_key(env_vars: list[str], vault_key: str) -> str | None:
        """Try *env_vars* in order, then vault.  Returns the first non-empty value."""
        for var in env_vars:
            val = os.environ.get(var, "")
            if val:
                return val
        try:
            from navig.vault import get_vault as _gv2

            return _gv2().get_secret(vault_key) or None
        except Exception:
            return None

    async def _get_file_path(self, file_id: str) -> str:
        """Resolve a Telegram ``file_id`` into its downloadable file path."""
        file_info = await self._api_call("getFile", {"file_id": file_id})
        if not file_info:
            raise ValueError(f"Telegram getFile failed for {file_id}")
        file_path = file_info.get("file_path", "")
        if not file_path:
            raise ValueError(f"Telegram returned no file_path for {file_id}")
        return file_path

    def _build_file_url(self, file_path: str) -> str:
        """Build a Telegram file download URL from a resolved file path."""
        return f"https://api.telegram.org/file/bot{self._bot_token}/{file_path}"

    # ── STT (Speech-to-Text) ─────────────────────────────────────────────────

    async def _transcribe_voice_message(
        self,
        chat_id: int,
        is_group: bool,
        voice_data: dict | None,
        user_id: int = 0,
    ) -> tuple[str | None, str]:
        """Download a Telegram voice message, transcribe via STT, echo transcript.

        Returns ``(transcript, detected_lang)`` on success, ``(None, "")`` on
        failure — a friendly error message has already been sent to the chat.

        Provider priority: Deepgram (fastest) → Whisper API → local Whisper.
        """
        import time as _time

        file_id = voice_data.get("file_id") if voice_data else None
        if not file_id:
            await self.send_message(chat_id, "🎙️ Couldn't read the voice message.", parse_mode=None)
            return None, ""

        # ── Rate-limit: max 1 transcription per 2 s per user ─────────────────
        if user_id:
            _now = _time.monotonic()
            _last = self._voice_last.get(user_id, 0.0)
            if _now - _last < 2.0:
                logger.debug("Voice rate-limit hit for user_id=%s — skipping burst", user_id)
                return None, ""
            self._voice_last[user_id] = _now

        # ── Dedup: skip already-processed file_ids ────────────────────────────
        if _HAS_SESSIONS and user_id:
            try:
                _sm = get_session_manager()
                _sess = _sm.get_session(chat_id, user_id, is_group)
                if _sess and file_id in getattr(_sess, "processed_voice_ids", set()):
                    logger.debug("Voice file %s already processed — skipping", file_id)
                    return None, ""
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        # ── Resolve STT provider ──────────────────────────────────────────────
        stt_provider = None
        fallback_providers: list = []

        dg_key = self._resolve_api_key(["DEEPGRAM_KEY", "DEEPGRAM_API_KEY"], "deepgram/api-key")
        if dg_key and _HAS_VOICE:
            stt_provider = _STTProvider.DEEPGRAM

        oai_key = self._resolve_api_key(["OPENAI_API_KEY"], "openai/api-key")
        if oai_key and _HAS_VOICE:
            if stt_provider is None:
                stt_provider = _STTProvider.WHISPER_API
            else:
                fallback_providers.append(_STTProvider.WHISPER_API)

        try:
            from navig.voice.stt import whisper_local_available as _wla

            _has_local = _wla()
        except Exception:
            _has_local = False
        if _has_local and _HAS_VOICE:
            if stt_provider is None:
                stt_provider = _STTProvider.WHISPER_LOCAL
            else:
                fallback_providers.append(_STTProvider.WHISPER_LOCAL)

        if stt_provider is None:
            await self.send_message(
                chat_id,
                "🎙️ <b>Voice transcription not configured.</b>\n\n"
                "Add any of the following to <code>~/.navig/.env</code> and restart:\n"
                "• <code>DEEPGRAM_KEY=&lt;key&gt;</code> — blazing fast, recommended\n"
                "• <code>OPENAI_API_KEY=&lt;key&gt;</code> — Whisper API fallback\n"
                "• <code>pip install openai-whisper</code> — offline, no key needed",
                parse_mode="HTML",
            )
            return None, ""

        tmp_path: str | None = None
        _recording_task: asyncio.Task | None = None
        try:
            await self._api_call("sendChatAction", {"chat_id": chat_id, "action": "record_voice"})
            _recording_task = asyncio.create_task(self._keep_recording(chat_id))

            file_info = await self._api_call("getFile", {"file_id": file_id})
            if not file_info:
                await self.send_message(
                    chat_id, "🎙️ Couldn't retrieve the voice file.", parse_mode=None
                )
                return None, ""
            file_path = file_info.get("file_path", "")
            if not file_path:
                await self.send_message(
                    chat_id, "🎙️ Couldn't retrieve the voice file.", parse_mode=None
                )
                return None, ""

            dl_url = f"https://api.telegram.org/file/bot{self._bot_token}/{file_path}"
            if not self._session:
                await self.send_message(
                    chat_id, "🎙️ Internal error: no HTTP session.", parse_mode=None
                )
                return None, ""

            async with self._session.get(dl_url) as dl_resp:
                if dl_resp.status != 200:
                    await self.send_message(
                        chat_id, "🎙️ Failed to download voice message.", parse_mode=None
                    )
                    return None, ""
                audio_bytes = await dl_resp.read()

            # Use real extension from Telegram's file_path (voice = .oga = OGG/OPUS).
            # .oga explicitly signals OGG Audio (OPUS codec) to Whisper/Deepgram,
            # avoiding the ambiguity of .ogg (which may be rejected as OGG Vorbis).
            import os as _os2

            _tg_suffix = _os2.path.splitext(file_path)[1] or ".oga"
            logger.info(
                "Voice download: %d bytes  magic=%s  tg_suffix=%s",
                len(audio_bytes),
                audio_bytes[:4].hex() if audio_bytes else "empty",
                _tg_suffix,
            )
            if not audio_bytes:
                await self.send_message(
                    chat_id, "🎙️ Voice file is empty — try again.", parse_mode=None
                )
                return None, ""

            with tempfile.NamedTemporaryFile(suffix=_tg_suffix, delete=False) as tmp:
                tmp_path = tmp.name
                tmp.write(audio_bytes)

            stt_cfg = _STTConfig(
                provider=stt_provider,
                fallback_providers=fallback_providers,
                detect_language=True,
            )
            result = await _STT(stt_cfg).transcribe(Path(tmp_path))

            if not result.success or not result.text:
                raw_err = result.error or ""
                logger.warning("STT transcription failed (provider=%s): %s", stt_provider, raw_err)
                if "whisper not installed" in raw_err or "No module named 'whisper'" in raw_err:
                    user_msg = (
                        "🎙️ Transcription failed: local Whisper is not installed.\n"
                        "Run <code>pip install openai-whisper</code>, or add <code>DEEPGRAM_KEY</code> / "
                        "<code>OPENAI_API_KEY</code> to <code>~/.navig/.env</code>."
                    )
                elif "API key" in raw_err or "not set" in raw_err or "not configured" in raw_err:
                    user_msg = "🎙️ Transcription failed: no STT API key — type your message instead."
                elif "timeout" in raw_err.lower():
                    user_msg = "🎙️ Transcription timed out — try a shorter clip or type it out."
                elif "too large" in raw_err:
                    user_msg = f"🎙️ Audio file too large — {raw_err.split(':', 1)[-1].strip()}"
                else:
                    user_msg = "🎙️ Couldn't transcribe audio — try again or type it out."
                await self.send_message(chat_id, user_msg, parse_mode="HTML")
                return None, ""

            transcript = result.text.strip()

            # ── Mark as processed (dedup on replay) ─────────────────────────
            if _HAS_SESSIONS and user_id:
                try:
                    _sm = get_session_manager()
                    _sess = _sm.get_or_create_session(chat_id, user_id, is_group)
                    if not hasattr(_sess, "processed_voice_ids"):
                        _sess.processed_voice_ids = set()
                    _sess.processed_voice_ids.add(file_id)
                    if len(_sess.processed_voice_ids) > 100:
                        _sess.processed_voice_ids = set(list(_sess.processed_voice_ids)[-100:])
                    _sm._save_session(_sess)
                except Exception as _e:
                    logger.debug("Could not mark voice as processed: %s", _e)

            # "Heard" echo — only in /trace debug mode
            _debug_active = any(
                uid in getattr(self, "_debug_users", set()) for uid in self.allowed_users
            )
            if _debug_active:
                heard_kb = [
                    [
                        {"text": "💡 Process", "callback_data": "heard_process"},
                        {"text": "🔁 Re-transcribe", "callback_data": "heard_retry"},
                        {"text": "📝 Edit", "callback_data": "heard_edit"},
                    ]
                ]
                await self.send_message(
                    chat_id,
                    f"🎙️ <b>Heard:</b>\n<i>{_html.escape(transcript)}</i>",
                    parse_mode="HTML",
                    keyboard=heard_kb,
                )
            detected_lang = (result.language or "") if hasattr(result, "language") else ""
            return transcript, detected_lang

        except Exception as e:
            logger.error("Voice transcription error: %s", e)
            await self.send_message(
                chat_id,
                "🎙️ Something went wrong processing your voice message — please try again.",
                parse_mode=None,
            )
            return None, ""
        finally:
            if _recording_task and not _recording_task.done():
                _recording_task.cancel()
                try:
                    await _recording_task
                except asyncio.CancelledError:
                    pass  # task cancelled; expected during shutdown
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass  # best-effort cleanup

    # ── TTS (Text-to-Speech) helpers ─────────────────────────────────────────

    @staticmethod
    async def _generate_audio_title(text: str) -> str:
        """Generate a short contextual title for the voice message."""
        if not text or len(text.strip()) < 3:
            return "NAVIG Voice"

        try:
            import asyncio
            import logging

            from navig.llm_generate import run_llm

            prompt = f"Extract or generate a 2-4 word title for this text. Reply ONLY with the title without any quotes or punctuation.\n\nText: {text[:1000]}"

            def _get():
                res = run_llm(
                    messages=[{"role": "user", "content": prompt}],
                    mode="fast",
                    temperature=0.3,
                    max_tokens=15,
                    timeout=5.0,
                )
                if res and res.content:
                    return res.content.strip(" \"'\n*\r\t.-")
                return "NAVIG Voice"

            title = await asyncio.to_thread(_get)
            return title if title else "NAVIG Voice"
        except Exception as e:
            import logging

            logging.getLogger("navig.telegram_voice").warning(
                "Failed to generate audio title: %s", e
            )
            return "NAVIG Voice"

    @staticmethod
    def _prepare_for_tts(text: str, max_chars: int = 500) -> str:
        """Strip markdown/code/URLs from *text* before sending to TTS."""

        text = re.sub(r"```[\s\S]*?```", "", text)
        text = re.sub(r"`[^`]+`", "", text)
        text = re.sub(r"https?://\S+", "", text)
        text = re.sub(r"[*_~]{1,3}", "", text)
        text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > max_chars:
            text = text[:max_chars].rsplit(" ", 1)[0] + "…"
        return text

    async def _maybe_send_voice(
        self,
        chat_id: int,
        user_id: int,
        is_group: bool,
        text: str,
        force_reply: bool | None = None,
    ) -> bool:
        """Attempt a TTS voice reply.  Returns True if voice was sent.

        Priority:
          1. New provider-aware path (``session.voice_replies=True``)
          2. Legacy ``navig.voice.tts`` path (``session.voice_enabled=True``)
        """
        # ── New provider-aware path ───────────────────────────────────────────
        if _HAS_SESSIONS:
            try:
                sm = get_session_manager()
                session = sm.get_or_create_session(chat_id, user_id, is_group)
                should_reply = getattr(session, "voice_replies", False)
                if force_reply is not None:
                    should_reply = force_reply
                if session is not None and should_reply:
                    if is_group and not getattr(session, "voice_in_groups", False):
                        return False
                    tts_text = self._prepare_for_tts(text)
                    if tts_text:
                        await self._api_call(
                            "sendChatAction",
                            {"chat_id": chat_id, "action": "record_voice"},
                        )
                        title = await self._generate_audio_title(tts_text)
                        success = await self._send_voice_reply(
                            chat_id, tts_text, session, title=title
                        )
                        if success:
                            return True
                        logger.warning(
                            "Voice reply failed for chat_id=%s provider=%s",
                            chat_id,
                            getattr(session, "tts_provider", "auto"),
                        )
                    return False
                if force_reply is False:
                    return False
            except Exception as exc:
                logger.warning("New voice path error: %s", exc)

        if force_reply is False:
            return False

        # ── Legacy navig.voice.tts path ───────────────────────────────────────
        if not _HAS_VOICE or is_group:
            return False

        if _HAS_SESSIONS:
            try:
                sm = get_session_manager()
                session = sm.get_session(chat_id, user_id, is_group=False)
                if session is not None and not session.voice_enabled:
                    return False
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        tts_text = self._prepare_for_tts(text)
        if not tts_text:
            return False

        tts_result = None
        try:
            tts = _TTS(_TTSConfig(provider=_TTSProvider.GOOGLE_CLOUD))
            tts_result = await tts.synthesize(tts_text)
            if not tts_result.success:
                logger.warning("TTS synthesis failed (non-fatal): %s", tts_result.error)
                return False

            audio_data: bytes | None = tts_result.audio_data
            if audio_data is None and tts_result.audio_path and tts_result.audio_path.exists():
                audio_data = tts_result.audio_path.read_bytes()

            if not audio_data:
                logger.warning("TTS returned empty audio (non-fatal)")
                return False

            title = await self._generate_audio_title(tts_text)
            await self.send_voice(chat_id, audio_data, title=title, performer="NAVIG AI")
            return True

        except Exception as e:
            logger.warning("Voice reply failed (non-fatal): %s", e)
            return False
        finally:
            try:
                if tts_result and tts_result.audio_path and tts_result.audio_path.exists():
                    tts_result.audio_path.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

    async def _send_voice_reply(
        self, chat_id: int, text: str, session: Any, title: str = "NAVIG Voice"
    ) -> bool:
        """Synthesize *text* to audio and send as a Telegram voice message.

        Dispatch order (provider-aware):
          deepgram      → Deepgram TTS API  (DEEPGRAM_API_KEY required)
          google_cloud  → Google Cloud TTS  (GOOGLE_APPLICATION_CREDENTIALS required)
          openai        → OpenAI TTS        (OPENAI_API_KEY required)
          edge / auto   → edge-tts          (no API key, Microsoft Edge voices)

        Returns True if voice was sent, False to fall back to text.
        """
        provider = getattr(session, "tts_provider", "auto")
        lang = getattr(session, "_stt_lang", "") or _detect_lang(text)

        # ── Deepgram TTS ──────────────────────────────────────────────────────
        if provider == "deepgram":
            api_key = self._resolve_api_key(["DEEPGRAM_API_KEY"], "deepgram_api_key")
            if not api_key:
                logger.warning("Deepgram TTS: no API key — falling back to Edge TTS")
                provider = "auto"
            elif lang != "en":
                logger.debug("Deepgram: non-English (%s) — routing to Edge TTS", lang)
                provider = "auto"
            else:
                try:
                    import httpx

                    async with httpx.AsyncClient(timeout=_VOICE_HTTP_TIMEOUT) as client:
                        resp = await client.post(
                            "https://api.deepgram.com/v1/speak?model=aura-asteria-en",
                            headers={
                                "Authorization": f"Token {api_key}",
                                "Content-Type": "application/json",
                            },
                            json={"text": text[:4000]},
                        )
                        resp.raise_for_status()
                    return await self._send_audio_bytes(
                        chat_id,
                        resp.content,
                        suffix=".mp3",
                        title=title,
                        performer="NAVIG AI",
                    )
                except Exception as exc:
                    logger.warning("Deepgram TTS failed, falling back to Edge TTS: %s", exc)
                provider = "auto"

        # ── Google Cloud TTS ──────────────────────────────────────────────────
        if provider == "google_cloud":
            try:
                from google.cloud import texttospeech  # type: ignore

                client = texttospeech.TextToSpeechAsyncClient()
                google_lang = _GOOGLE_LANG.get(lang, lang + "-" + lang.upper())
                response = await client.synthesize_speech(
                    input=texttospeech.SynthesisInput(text=text[:5000]),
                    voice=texttospeech.VoiceSelectionParams(
                        language_code=google_lang,
                        ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL,
                    ),
                    audio_config=texttospeech.AudioConfig(
                        audio_encoding=texttospeech.AudioEncoding.OGG_OPUS
                    ),
                )
                return await self._send_audio_bytes(
                    chat_id,
                    response.audio_content,
                    suffix=".ogg",
                    title=title,
                    performer="NAVIG AI",
                )
            except Exception as exc:
                logger.warning("Google Cloud TTS failed, falling back to Edge TTS: %s", exc)
                provider = "auto"

        # ── OpenAI TTS ────────────────────────────────────────────────────────
        if provider == "openai":
            api_key = self._resolve_api_key(["OPENAI_API_KEY"], "openai_api_key")
            if not api_key:
                logger.warning("OpenAI TTS: no API key found")
                return False
            try:
                import httpx

                async with httpx.AsyncClient(timeout=_VOICE_HTTP_TIMEOUT) as client:
                    resp = await client.post(
                        "https://api.openai.com/v1/audio/speech",
                        headers={"Authorization": f"Bearer {api_key}"},
                        json={"model": "tts-1", "input": text[:4096], "voice": "nova"},
                    )
                    resp.raise_for_status()
                return await self._send_audio_bytes(
                    chat_id,
                    resp.content,
                    suffix=".mp3",
                    title=title,
                    performer="NAVIG AI",
                )
            except Exception as exc:
                logger.warning("OpenAI TTS failed, falling back to Edge TTS: %s", exc)
                provider = "auto"

        # ── Edge TTS (default / auto) ─────────────────────────────────────────
        try:
            import edge_tts  # type: ignore

            voice_name = _EDGE_VOICE.get(lang, "en-US-AriaNeural")
            communicate = edge_tts.Communicate(text[:4000], voice_name)
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                tmp_path = f.name
            try:
                await communicate.save(tmp_path)
                await self._send_voice_file(chat_id, tmp_path, title=title, performer="NAVIG AI")
                return True
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass  # best-effort cleanup
        except ImportError:
            logger.debug("edge-tts not installed (pip install edge-tts)")
            return False
        except Exception as exc:
            logger.warning("Edge TTS failed: %s", exc)
            return False

    async def _send_audio_bytes(
        self,
        chat_id: int,
        audio_bytes: bytes,
        suffix: str = ".mp3",
        title: str = "NAVIG",
        performer: str = "Voice Reply",
    ) -> bool:
        """Write *audio_bytes* to a temp file and send via ``_send_voice_file``.

        Extracts the write→upload→cleanup triad that was repeated across
        Deepgram, Google, and OpenAI TTS branches (DUP-2 fix).
        """
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name
        try:
            await self._send_voice_file(chat_id, tmp_path, title=title, performer=performer)
            return True
        except Exception as exc:
            logger.warning("_send_audio_bytes failed: %s", exc)
            return False
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass  # best-effort cleanup

    async def _handle_audio_file_message(
        self,
        chat_id: int,
        audio_data: dict | None,
        message_id: int | None = None,
    ) -> None:
        """Send an info card + action buttons when the user sends a music / audio file.

        Telegram delivers music files as ``message["audio"]`` (distinct from
        ``message["voice"]`` which is a voice recording).  We never try STT on
        these — instead we present contextual actions.
        """
        if not audio_data:
            return

        file_id = audio_data.get("file_id", "")
        title = audio_data.get("title") or audio_data.get("file_name") or "Unknown file"
        performer = audio_data.get("performer") or ""
        duration_sec = int(audio_data.get("duration") or 0)
        file_size = int(audio_data.get("file_size") or 0)
        mime_type = audio_data.get("mime_type") or "audio"

        classification = _classify_audio(audio_data)
        is_speech = classification["is_speech"]
        kind = classification["kind"]

        import uuid

        short_id = uuid.uuid4().hex[:12]

        # Cache metadata for the callback handler
        _af_cache[short_id] = {
            "file_id": file_id,
            "title": title,
            "performer": performer,
            "duration": duration_sec,
            "file_size": file_size,
            "mime_type": mime_type,
            "is_speech": is_speech,
        }

        mins, secs = divmod(duration_sec, 60)
        dur_str = f"{mins}:{secs:02d}" if duration_sec else "—"
        size_str = f"{file_size // 1024:,} KB" if file_size else "—"

        if kind == "voice_recording":
            header = "🎤 <b>Voice recording</b>"
        elif kind == "music":
            header = f"🎵 <b>{_html.escape(title)}</b>"
            if performer:
                header += f"\nby {_html.escape(performer)}"
        else:
            header = f"🎵 <b>{_html.escape(title)}</b>"

        card = header
        card += (
            f"\n⏱ {_html.escape(dur_str)}  \u00b7  💾 {_html.escape(size_str)}  \u00b7  "
            f"<code>{_html.escape(mime_type)}</code>"
        )

        # First row: Transcribe + Identify
        row1 = [
            {"text": "🎤 Transcribe", "callback_data": f"audmsg:transcribe:{short_id}"},
            {"text": "🔍 Identify", "callback_data": f"audmsg:identify:{short_id}"},
        ]
        # Second row: Info + optional Detect Language
        row2 = [
            {"text": "ℹ️ Info", "callback_data": f"audmsg:info:{short_id}"},
        ]
        if is_speech:
            row2.append(
                {
                    "text": "🌐 Detect Language",
                    "callback_data": f"audmsg:lang:{short_id}",
                }
            )
        # Third row: Dismiss
        row3 = [
            {"text": "❌ Dismiss", "callback_data": f"audmsg:dismiss:{short_id}"},
        ]
        keyboard = [row1, row2, row3]

        await self.send_message(
            chat_id,
            card,
            parse_mode="HTML",
            keyboard=keyboard,
            reply_to_message_id=message_id,
        )

    async def _transcribe_audio_file(
        self,
        chat_id: int,
        file_id: str,
        is_voice: bool = False,
        task_view: Any = None,
    ) -> str | None:
        """Download a Telegram audio file and run STT with correct MIME type.

        Unlike the voice-note path, audio files may be MP3/M4A/FLAC — we must
        not hardcode ``audio/ogg`` for them.  The ``is_voice`` flag is forwarded
        to ``STT.transcribe()`` so the Whisper API upload uses a real extension
        and MIME for non-OGA files.

        Returns the transcript string on success, or ``None`` on failure
        (the caller is responsible for sending an error reply).
        """
        from navig.gateway.channels.task_card import StepState, update_task_card

        if task_view:
            task_view.set_step("download", StepState.ACTIVE, "Locating file...")
            task_view.recompute_percent()
            await update_task_card(self, chat_id, task_view)

        try:
            file_path = await self._get_file_path(file_id)
        except Exception as exc:
            if task_view:
                task_view.set_step("download", StepState.FAILED, "File path resolution failed")
                await update_task_card(self, chat_id, task_view, force=True)
            logger.warning("_transcribe_audio_file: get_file_path failed: %s", exc)
            return None

        import os
        import tempfile

        import aiohttp

        dl_url = self._build_file_url(file_path)
        suffix = "." + file_path.rsplit(".", 1)[-1] if "." in file_path else ".audio"

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name

        try:
            if task_view:
                task_view.set_step("download", StepState.ACTIVE, "Downloading from Telegram...")
                task_view.recompute_percent()
                await update_task_card(self, chat_id, task_view)

            async with aiohttp.ClientSession() as sess, sess.get(dl_url) as resp:
                if resp.status != 200:
                    if task_view:
                        task_view.set_step(
                            "download",
                            StepState.FAILED,
                            f"Download HTTP {resp.status}",
                        )
                        await update_task_card(self, chat_id, task_view, force=True)
                    logger.warning(
                        "_transcribe_audio_file: download %s -> %s",
                        dl_url,
                        resp.status,
                    )
                    return None
                with open(tmp_path, "wb") as fh:
                    fh.write(await resp.read())

            if task_view:
                task_view.set_step("download", StepState.DONE)
                task_view.set_step("stt", StepState.ACTIVE, "Running Whisper model...")
                task_view.recompute_percent()
                await update_task_card(self, chat_id, task_view, force=True)

            from navig.voice.stt import STT as _STT2

            stt = _STT2()
            result = await stt.transcribe(tmp_path, is_voice=is_voice)

            if task_view:
                task_view.set_step("stt", StepState.DONE if result.success else StepState.FAILED)
                task_view.set_step("finalize", StepState.ACTIVE)
                task_view.recompute_percent()
                await update_task_card(self, chat_id, task_view, force=True)

            if result.success:
                return result.text
            logger.warning("_transcribe_audio_file: STT failed: %s", result.error)
            return None
        except Exception as exc:
            logger.warning("_transcribe_audio_file exception: %s", exc)
            return None
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass  # best-effort cleanup

    async def _send_voice_file(
        self,
        chat_id: int,
        file_path: str,
        title: str = "NAVIG",
        performer: str = "Voice Reply",
    ) -> None:
        """Upload a local audio file to Telegram as a voice message.

        Falls back to ``sendAudio`` when the user has restricted voice note
        reception (Telegram privacy: Settings → Privacy → Voice Messages).
        """
        import mimetypes
        import os

        with open(file_path, "rb") as f:
            audio_data = f.read()

        ext = os.path.splitext(file_path)[1].lower()
        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = "audio/mpeg" if ext == ".mp3" else "audio/ogg"

        filename = f"voice{ext}" if ext else "voice.mp3"

        result = await self._api_call_multipart(
            "sendVoice",
            data={"chat_id": str(chat_id)},
            files={"voice": (filename, audio_data, mime_type)},
        )

        if not result:
            raise RuntimeError("sendVoice failed: no response from Telegram API")

        if not result.get("ok"):
            desc = result.get("description", "")
            if "restricted" in desc.lower():
                logger.info("sendVoice blocked by privacy — falling back to sendAudio")
                result2 = await self._api_call_multipart(
                    "sendAudio",
                    data={
                        "chat_id": str(chat_id),
                        "title": title,
                        "performer": performer,
                    },
                    files={"audio": (f"navig_reply{ext or '.mp3'}", audio_data, mime_type)},
                )
                if not result2 or not result2.get("ok"):
                    desc2 = result2.get("description", "no response") if result2 else "no response"
                    raise RuntimeError(f"sendAudio fallback failed: {desc2}")
            else:
                raise RuntimeError(f"sendVoice failed: {desc}")
