"""
Voice Input Handler for NAVIG Agent

Provides a unified transcription interface for the agent layer with
support for faster-whisper (local GPU/CPU), OpenAI Whisper API, and
Deepgram — routing through the existing navig.voice.stt module where
possible and adding faster-whisper as a high-performance local option.

Usage:
    from navig.agent.voice_input import VoiceInputHandler

    handler = VoiceInputHandler()
    result = await handler.transcribe("recording.mp3")
    print(result.text)

    # Telegram OGG/Opus shortcut
    result = await handler.transcribe_ogg_opus(ogg_bytes)
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger("navig.agent.voice_input")

# Supported audio extensions (all formats accepted by STT providers)
SUPPORTED_AUDIO_EXTENSIONS: frozenset[str] = frozenset(
    {".mp3", ".wav", ".flac", ".ogg", ".oga", ".m4a", ".webm", ".opus", ".mp4"}
)

# Limits
MAX_AUDIO_SIZE_MB: int = 25
MAX_AUDIO_DURATION_SECONDS: int = 120  # 2 minutes


# ---------------------------------------------------------------------------
# Configuration types
# ---------------------------------------------------------------------------


class TranscriptionBackend(str, Enum):
    """Transcription backend selection."""

    FASTER_WHISPER = "faster_whisper"
    WHISPER_API = "whisper_api"
    WHISPER_LOCAL = "whisper_local"
    DEEPGRAM = "deepgram"
    NONE = "none"


@dataclass
class TranscriptionConfig:
    """Configuration for VoiceInputHandler."""

    backend: TranscriptionBackend = TranscriptionBackend.NONE
    model: str = "base"  # faster-whisper model size: tiny, base, small, medium, large-v3
    language: str | None = None  # None = auto-detect
    api_key: str | None = None  # Override for Whisper API / Deepgram
    max_audio_seconds: int = MAX_AUDIO_DURATION_SECONDS
    max_file_size_mb: int = MAX_AUDIO_SIZE_MB
    device: str = "auto"  # faster-whisper: "auto", "cpu", "cuda"
    compute_type: str = "int8"  # faster-whisper: "int8", "float16", "float32"


@dataclass
class TranscriptionResult:
    """Result from voice transcription."""

    success: bool
    text: str | None = None
    language: str | None = None
    duration_ms: int | None = None
    backend: TranscriptionBackend | None = None
    confidence: float | None = None
    error: str | None = None
    segments: list[dict] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.success and bool(self.text)


# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------


def detect_transcription_backend() -> TranscriptionBackend:
    """Auto-detect the best available transcription backend.

    Priority:
      1. faster-whisper (local, fast, no API key)
      2. Deepgram        (API, fast, needs key)
      3. Whisper API      (API, OpenAI, needs key)
      4. whisper local    (openai-whisper, slow, no API key)
      5. NONE             (nothing available)
    """
    # 1. faster-whisper
    try:
        import faster_whisper  # noqa: F401

        return TranscriptionBackend.FASTER_WHISPER
    except ImportError:
        pass

    # 2. Deepgram key?
    if _resolve_key("deepgram/api-key", "DEEPGRAM_API_KEY", "DEEPGRAM_KEY"):
        return TranscriptionBackend.DEEPGRAM

    # 3. OpenAI Whisper API
    if _resolve_key("openai/api-key", "OPENAI_API_KEY"):
        return TranscriptionBackend.WHISPER_API

    # 4. openai-whisper local
    try:
        import whisper  # noqa: F401

        return TranscriptionBackend.WHISPER_LOCAL
    except ImportError:
        pass

    return TranscriptionBackend.NONE


def _resolve_key(*names: str) -> str | None:
    """Try vault then environment variables, return first hit."""
    # Vault
    try:
        from navig.vault import get_vault

        vault = get_vault()
        for name in names:
            if "/" in name:  # vault-style label
                val = vault.get_secret(name)
                if val:
                    return val
    except (ImportError, AttributeError, RuntimeError, KeyError) as exc:
        logger.debug("Voice key vault lookup failed: %s", exc)
    # Env vars
    for name in names:
        if "/" not in name:
            val = os.environ.get(name)
            if val:
                return val
    return None


# ---------------------------------------------------------------------------
# VoiceInputHandler
# ---------------------------------------------------------------------------


class VoiceInputHandler:
    """Unified voice-to-text handler for the NAVIG agent layer.

    Wraps the existing ``navig.voice.stt.STT`` engine for API-based providers
    and adds faster-whisper as a first-class local backend.
    """

    def __init__(self, config: TranscriptionConfig | None = None):
        if config is None:
            config = TranscriptionConfig()
        if config.backend == TranscriptionBackend.NONE:
            config.backend = detect_transcription_backend()
        self.config = config

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def transcribe(
        self,
        audio_path: str | Path,
        *,
        language: str | None = None,
    ) -> TranscriptionResult:
        """Transcribe an audio file to text.

        Args:
            audio_path: Path to any supported audio file.
            language:   Override language code (None = auto-detect).

        Returns:
            TranscriptionResult with text or error details.
        """
        path = Path(audio_path)

        # ── Validate ──────────────────────────────────────────────────
        if not path.exists():
            return TranscriptionResult(success=False, error=f"Audio file not found: {path}")

        if path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
            return TranscriptionResult(
                success=False,
                error=(
                    f"Unsupported format: {path.suffix}. "
                    f"Supported: {', '.join(sorted(SUPPORTED_AUDIO_EXTENSIONS))}"
                ),
            )

        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > self.config.max_file_size_mb:
            return TranscriptionResult(
                success=False,
                error=f"File too large: {size_mb:.1f} MB (max {self.config.max_file_size_mb} MB)",
            )

        # ── Check backend ─────────────────────────────────────────────
        if self.config.backend == TranscriptionBackend.NONE:
            return TranscriptionResult(
                success=False,
                error=(
                    "No transcription backend available. Install faster-whisper "
                    "(pip install faster-whisper) or set OPENAI_API_KEY / DEEPGRAM_API_KEY."
                ),
            )

        lang = language or self.config.language  # None means auto-detect

        # ── Dispatch ──────────────────────────────────────────────────
        if self.config.backend == TranscriptionBackend.FASTER_WHISPER:
            return await self._transcribe_faster_whisper(path, lang)

        # API-based backends delegate to existing STT class
        return await self._transcribe_via_stt(path, lang)

    async def transcribe_ogg_opus(
        self,
        ogg_bytes: bytes,
        *,
        language: str | None = None,
    ) -> TranscriptionResult:
        """Transcribe Telegram OGG/Opus voice data from raw bytes.

        Writes bytes to a temp .ogg file and runs normal transcription.
        """
        if not ogg_bytes:
            return TranscriptionResult(success=False, error="Empty audio data")

        size_mb = len(ogg_bytes) / (1024 * 1024)
        if size_mb > self.config.max_file_size_mb:
            return TranscriptionResult(
                success=False,
                error=f"Audio too large: {size_mb:.1f} MB (max {self.config.max_file_size_mb} MB)",
            )

        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
                tmp.write(ogg_bytes)
                tmp_path = Path(tmp.name)

            return await self.transcribe(tmp_path, language=language)
        finally:
            if tmp_path and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    # ------------------------------------------------------------------ #
    # faster-whisper backend
    # ------------------------------------------------------------------ #

    async def _transcribe_faster_whisper(
        self,
        audio_path: Path,
        language: str | None,
    ) -> TranscriptionResult:
        """Local transcription via faster-whisper (CTranslate2)."""
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            return TranscriptionResult(
                success=False,
                error="faster-whisper not installed. Install with: pip install faster-whisper",
            )

        model_size = self.config.model
        device = self.config.device
        compute_type = self.config.compute_type

        loop = asyncio.get_running_loop()

        def _run() -> TranscriptionResult:
            import time

            t0 = time.monotonic()
            try:
                model = WhisperModel(
                    model_size,
                    device=device,
                    compute_type=compute_type,
                )
                segments_iter, info = model.transcribe(
                    str(audio_path),
                    language=language,
                    beam_size=5,
                    vad_filter=True,
                )

                text_parts: list[str] = []
                seg_list: list[dict] = []
                for seg in segments_iter:
                    text_parts.append(seg.text.strip())
                    seg_list.append(
                        {
                            "start": seg.start,
                            "end": seg.end,
                            "text": seg.text.strip(),
                        }
                    )

                full_text = " ".join(text_parts).strip()
                duration_ms = int((time.monotonic() - t0) * 1000)
                detected_lang = info.language if info.language else language

                return TranscriptionResult(
                    success=bool(full_text),
                    text=full_text or None,
                    language=detected_lang,
                    duration_ms=duration_ms,
                    backend=TranscriptionBackend.FASTER_WHISPER,
                    confidence=info.language_probability
                    if hasattr(info, "language_probability")
                    else None,
                    segments=seg_list,
                )
            except Exception as exc:
                return TranscriptionResult(
                    success=False,
                    error=f"faster-whisper error: {exc}",
                    backend=TranscriptionBackend.FASTER_WHISPER,
                )

        return await loop.run_in_executor(None, _run)

    # ------------------------------------------------------------------ #
    # Delegate to existing navig.voice.stt
    # ------------------------------------------------------------------ #

    async def _transcribe_via_stt(
        self,
        audio_path: Path,
        language: str | None,
    ) -> TranscriptionResult:
        """Route API-based backends through the existing STT class."""
        from navig.voice.stt import STT, STTConfig, STTProvider

        provider_map = {
            TranscriptionBackend.DEEPGRAM: STTProvider.DEEPGRAM,
            TranscriptionBackend.WHISPER_API: STTProvider.WHISPER_API,
            TranscriptionBackend.WHISPER_LOCAL: STTProvider.WHISPER_LOCAL,
        }
        stt_provider = provider_map.get(self.config.backend)
        if stt_provider is None:
            return TranscriptionResult(
                success=False,
                error=f"Cannot map backend {self.config.backend} to STT provider",
            )

        stt_config = STTConfig(
            provider=stt_provider,
            language=language or "en",
            detect_language=language is None,
        )
        stt = STT(config=stt_config)
        result = await stt.transcribe(audio_path)

        return TranscriptionResult(
            success=result.success,
            text=result.text,
            language=result.language,
            duration_ms=result.duration_ms,
            backend=self.config.backend,
            confidence=result.confidence,
            error=result.error,
            segments=result.segments or [],
        )


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_default_handler: VoiceInputHandler | None = None


def get_voice_handler(config: TranscriptionConfig | None = None) -> VoiceInputHandler:
    """Return (or create) the default VoiceInputHandler singleton."""
    global _default_handler
    if _default_handler is None:
        _default_handler = VoiceInputHandler(config=config)
    return _default_handler


async def transcribe_audio(
    audio_path: str | Path,
    *,
    language: str | None = None,
) -> str | None:
    """Convenience: transcribe file and return text or None."""
    handler = get_voice_handler()
    result = await handler.transcribe(audio_path, language=language)
    return result.text if result.success else None
