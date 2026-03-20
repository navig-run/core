"""
Voice Pipeline — End-to-End Orchestrator for NAVIG

Wires wake-word detection → streaming STT → LLM routing → TTS into a
single, batteries-included pipeline that can be started with one call.

Usage:
    from navig.voice.pipeline import VoicePipeline, PipelineConfig

    pipeline = VoicePipeline(
        config=PipelineConfig(
            keyword="echo",
            tts_provider="openai",
            llm_model="gpt-4o-mini",
            echo_bridge_url="http://localhost:8080",
        )
    )
    await pipeline.start()
    # Pipeline runs indefinitely; wake-word triggers activate sessions.
    await pipeline.stop()

For programmatic (non-mic) use — e.g., processing a Telegram voice note:
    result = await pipeline.process_audio_file(Path("voice.ogg"), is_voice=True)
    print(result.transcript, result.response_text)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("navig.voice.pipeline")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field
from navig.config import ConfigManager

def _get_voice_setting(key: str, default: Any) -> Any:
    # Safe fetch from global config at instantiation time
    try:
        mgr = ConfigManager()
        cfg = mgr.get_global_config().get("voice", {})
        return cfg.get(key, default)
    except Exception:
        return default

@dataclass
class PipelineConfig:
    """Unified configuration for the VoicePipeline."""

    # Wake word
    keyword:   str   = field(default_factory=lambda: _get_voice_setting("keyword", "hey_jarvis"))
    threshold: float = field(default_factory=lambda: _get_voice_setting("threshold", 0.45))

    # STT
    stt_primary:  str = field(default_factory=lambda: _get_voice_setting("stt_primary", "deepgram"))
    stt_fallback: str = field(default_factory=lambda: _get_voice_setting("stt_fallback", "whisper_api"))
    language:     str = field(default_factory=lambda: _get_voice_setting("language", "en"))

    # LLM
    llm_model:   Optional[str] = field(default_factory=lambda: _get_voice_setting("llm_model", None))
    llm_system_prompt: Optional[str] = (
        "You are NAVIG, an intelligent voice assistant. "
        "Be concise — responses are spoken aloud. "
        "Limit replies to 2–3 sentences unless the user asks for detail."
    )

    # TTS
    tts_provider: str = field(default_factory=lambda: _get_voice_setting("tts_provider", "edge"))
    tts_voice:    Optional[str] = field(default=None)

    # Session settings
    silence_timeout:    float = field(default_factory=lambda: _get_voice_setting("silence_timeout", 2.0))
    max_listen_seconds: float = field(default_factory=lambda: _get_voice_setting("max_listen_seconds", 30.0))

    # navig-echo bridge (optional)
    echo_bridge_url: Optional[str] = None

    # Graceful degradation
    fail_on_mic_unavailable: bool = False


# ---------------------------------------------------------------------------
# Pipeline result
# ---------------------------------------------------------------------------

@dataclass
class PipelineResult:
    """Result from a single pipeline invocation."""
    transcript:    Optional[str]
    response_text: Optional[str]
    audio_path:    Optional[str]
    duration_ms:   Optional[float] = None
    error:         Optional[str] = None

    def __bool__(self) -> bool:
        return self.error is None


# ---------------------------------------------------------------------------
# VoicePipeline
# ---------------------------------------------------------------------------

class VoicePipeline:
    """
    End-to-end voice interaction pipeline.

    Responsibilities:
    1. Manages WakeWordEngine lifecycle.
    2. Wires STT (StreamingSTT) as the VoiceSessionManager's stt_fn.
    3. Routes transcripts through navig-core's llm_router.
    4. Synthesises TTS response via navig.voice.TTS.
    5. Exposes process_audio_file() for Telegram / file-based usage
       (bypasses wake-word, goes straight to STT → LLM → TTS).
    """

    def __init__(self, config: Optional[PipelineConfig] = None):
        self.config = config or PipelineConfig()
        self._session_mgr: Optional[Any] = None
        self._wake_engine:  Optional[Any] = None
        self._running = False

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        """Build and start all pipeline components."""
        if self._running:
            return

        from navig.voice.session_manager import SessionConfig, VoiceSessionManager

        # ── Build STT callables ───────────────────────────────────────
        from navig.voice.streaming_stt import StreamingProvider, StreamingSTT, StreamingSTTConfig
        from navig.voice.wake_word import WakeWordConfig, WakeWordEngine
        _primary  = StreamingProvider(self.config.stt_primary)
        _fallback = StreamingProvider(self.config.stt_fallback)

        stt_config = StreamingSTTConfig(
            primary=_primary,
            fallback=_fallback,
            language=self.config.language,
        )
        stt = StreamingSTT(config=stt_config)

        async def _stt_fn(session) -> Optional[str]:
            """Transcribe buffered session audio."""
            from navig.voice.streaming_stt import transcribe_session_audio
            return await transcribe_session_audio(session, config=stt_config)

        # ── Build LLM callable ────────────────────────────────────────
        async def _llm_fn(transcript: str) -> str:
            return await self._call_llm(transcript)

        # ── Build TTS callable ────────────────────────────────────────
        async def _tts_fn(text: str) -> Optional[str]:
            return await self._call_tts(text)

        # ── Session manager ───────────────────────────────────────────
        session_config = SessionConfig(
            silence_timeout_seconds=self.config.silence_timeout,
            max_listen_seconds=self.config.max_listen_seconds,
            echo_bridge_url=self.config.echo_bridge_url,
        )
        self._session_mgr = VoiceSessionManager(
            config=session_config,
            stt_fn=_stt_fn,
            llm_fn=_llm_fn,
            tts_fn=_tts_fn,
        )
        await self._session_mgr.start()

        # ── Wake word engine ──────────────────────────────────────────
        wake_config = WakeWordConfig(
            keyword=self.config.keyword,
            threshold=self.config.threshold,
            fail_on_mic_unavailable=self.config.fail_on_mic_unavailable,
            echo_bridge_url=self.config.echo_bridge_url,
        )
        self._wake_engine = WakeWordEngine(
            config=wake_config,
            session_manager=self._session_mgr,
        )
        await self._wake_engine.start()

        self._running = True
        logger.info(
            "VoicePipeline started — keyword=%r stt=%s tts=%s",
            self.config.keyword,
            self.config.stt_primary,
            self.config.tts_provider,
        )

    async def stop(self) -> None:
        """Gracefully shut down all components."""
        self._running = False
        if self._wake_engine:
            await self._wake_engine.stop()
        if self._session_mgr:
            await self._session_mgr.stop()
        logger.info("VoicePipeline stopped")

    # ------------------------------------------------------------------ #
    # File-based (non-streaming) API — for Telegram and tests
    # ------------------------------------------------------------------ #

    async def process_audio_file(
        self,
        audio_path: Path,
        *,
        is_voice: bool = True,
    ) -> PipelineResult:
        """
        Process a pre-recorded audio file through the full pipeline.

        Skips wake-word detection; suitable for Telegram voice note handling.
        Does NOT require the pipeline to be started (components are built inline).

        Args:
            audio_path: Absolute path to audio file (OGG, WAV, MP3, etc.)
            is_voice:   True for Telegram voice notes (OGG/OPUS layout)

        Returns:
            PipelineResult with transcript, response_text, and audio_path.
        """
        t0 = time.monotonic()
        error: Optional[str] = None
        transcript: Optional[str] = None
        response_text: Optional[str] = None
        out_audio_path: Optional[str] = None

        # ── STT ───────────────────────────────────────────────────────
        try:
            from navig.voice.stt import STT, STTConfig, STTProvider
            _provider_map = {
                "deepgram":     STTProvider.DEEPGRAM,
                "whisper_api":  STTProvider.WHISPER_API,
                "whisper_local": STTProvider.WHISPER_LOCAL,
            }
            stt_config = STTConfig(
                provider=_provider_map.get(self.config.stt_primary, STTProvider.DEEPGRAM),
                fallback_providers=[
                    _provider_map.get(self.config.stt_fallback, STTProvider.WHISPER_API)
                ],
                language=self.config.language,
            )
            stt = STT(config=stt_config)
            result = await stt.transcribe(audio_path, is_voice=is_voice)
            if result.success and result.text:
                transcript = result.text
                logger.info("STT result (%.0fms): %r", result.latency_ms or 0, transcript[:80])
            else:
                logger.warning("STT produced no transcript: %s", result.error)
        except Exception as exc:
            error = f"STT error: {exc}"
            logger.error(error)

        # ── LLM ───────────────────────────────────────────────────────
        if transcript:
            try:
                response_text = await self._call_llm(transcript)
            except Exception as exc:
                error = f"LLM error: {exc}"
                logger.error(error)

        # ── TTS ───────────────────────────────────────────────────────
        if response_text:
            try:
                out_audio_path = await self._call_tts(response_text)
            except Exception as exc:
                logger.warning("TTS error: %s", exc)  # Non-fatal: text response still useful

        duration_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "Pipeline file result — transcript=%r response=%r duration=%.0fms",
            (transcript or "")[:80],
            (response_text or "")[:80],
            duration_ms,
        )

        return PipelineResult(
            transcript=transcript,
            response_text=response_text,
            audio_path=out_audio_path,
            duration_ms=duration_ms,
            error=error,
        )

    # ------------------------------------------------------------------ #
    # LLM routing
    # ------------------------------------------------------------------ #

    async def _call_llm(self, transcript: str) -> str:
        """Route transcript through navig-core's UnifiedRouter."""
        try:
            from navig.routing.router import RouteRequest, get_router
            router = get_router()

            messages = []
            if self.config.llm_system_prompt:
                messages.append({"role": "system", "content": self.config.llm_system_prompt})
            messages.append({"role": "user", "content": transcript})

            kwargs: dict = {}
            if self.config.llm_model:
                kwargs["model_override"] = self.config.llm_model

            request = RouteRequest(messages=messages, entrypoint="voice_pipeline", **kwargs)
            response_text, _trace = await router.run(request)
            return response_text or "I'm sorry, I couldn't process that request right now. Please try again."

        except Exception as exc:
            logger.error("LLM routing error: %s", exc)
            # Return graceful fallback message
            return "I'm sorry, I couldn't process that request right now. Please try again."

    # ------------------------------------------------------------------ #
    # TTS synthesis
    # ------------------------------------------------------------------ #

    async def _call_tts(self, text: str) -> Optional[str]:
        """Synthesise speech and return file path."""
        try:
            from navig.voice.tts import TTS, TTSConfig, TTSProvider

            _provider_map = {
                "openai":     TTSProvider.OPENAI,
                "elevenlabs": TTSProvider.ELEVENLABS,
                "edge":       TTSProvider.EDGE,
                "google_cloud": TTSProvider.GOOGLE_CLOUD,
                "deepgram":   TTSProvider.DEEPGRAM,
            }
            config = TTSConfig(
                provider=_provider_map.get(self.config.tts_provider, TTSProvider.EDGE),
                fallback_providers=[TTSProvider.EDGE],
            )
            if self.config.tts_voice:
                config.voice = self.config.tts_voice

            tts = TTS(config=config)
            result = await tts.synthesize(text)
            if result.success and result.audio_path:
                return str(result.audio_path)
            logger.warning("TTS failed: %s", result.error)
            return None
        except Exception as exc:
            logger.error("TTS call error: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_pipeline: Optional[VoicePipeline] = None


def get_pipeline(config: Optional[PipelineConfig] = None) -> VoicePipeline:
    """Return (or create) the global VoicePipeline singleton."""
    global _pipeline
    if _pipeline is None:
        _pipeline = VoicePipeline(config=config)
    return _pipeline

