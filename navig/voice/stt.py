"""
Speech-to-Text Module for NAVIG

Multi-provider STT with support for Deepgram, OpenAI Whisper,
and local Whisper models.

Usage:
    from navig.voice.stt import transcribe, STT

    # Simple usage
    text = await transcribe("audio.wav")

    # With provider selection
    stt = STT(provider="deepgram")
    result = await stt.transcribe("audio.mp3")
    print(result.text)

Providers:
- deepgram: Real-time & batch, requires DEEPGRAM_API_KEY
- whisper_api: OpenAI Whisper API, requires OPENAI_API_KEY
- whisper_local: Local Whisper model (offline, requires whisper package)
"""

from __future__ import annotations

import asyncio
import mimetypes
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

from navig.llm_router import PROVIDER_RESOURCE_URLS as _PRUL  # noqa: F401

# =============================================================================
# Types
# =============================================================================

class STTProvider(str, Enum):
    """Supported STT providers."""
    DEEPGRAM = "deepgram"
    WHISPER_API = "whisper_api"
    WHISPER_LOCAL = "whisper_local"


@dataclass
class STTConfig:
    """STT configuration."""
    provider: STTProvider = STTProvider.WHISPER_API
    fallback_providers: List[STTProvider] = field(
        default_factory=lambda: [STTProvider.WHISPER_LOCAL]
    )

    # Language
    language: str = "en"
    detect_language: bool = False

    # Deepgram settings
    deepgram_model: str = "nova-2"
    deepgram_tier: str = "enhanced"
    deepgram_punctuate: bool = True
    deepgram_diarize: bool = False

    # Whisper API settings
    whisper_model: str = "whisper-1"

    # Whisper local settings
    whisper_local_model: str = "base"  # tiny, base, small, medium, large

    # General
    timeout_seconds: int = 60
    max_audio_size_mb: int = 25


@dataclass
class STTResult:
    """Result from STT transcription."""
    success: bool
    text: Optional[str] = None

    # Metadata
    provider: Optional[STTProvider] = None
    language: Optional[str] = None
    confidence: Optional[float] = None
    duration_ms: Optional[int] = None
    latency_ms: Optional[int] = None

    # Segments (if available)
    segments: Optional[List[Dict]] = None

    # Error
    error: Optional[str] = None

    def __bool__(self) -> bool:
        return self.success


# =============================================================================
# STT Engine
# =============================================================================

class STT:
    """
    Speech-to-Text engine with multi-provider support.

    Supports Deepgram, OpenAI Whisper API, and local Whisper models
    with automatic fallback.
    """

    def __init__(self, config: Optional[STTConfig] = None):
        self.config = config or STTConfig()

    async def transcribe(
        self,
        audio_path: str | Path,
        provider: Optional[STTProvider] = None,
        language: Optional[str] = None,
        **kwargs,
    ) -> STTResult:
        """
        Transcribe audio to text.

        Args:
            audio_path: Path to audio file (MP3, WAV, FLAC, etc.)
            provider: Override provider
            language: Override language code
            **kwargs: Provider-specific options

        Returns:
            STTResult with transcribed text or error
        """
        start_time = datetime.utcnow()
        audio_path = Path(audio_path)

        if not audio_path.exists():
            return STTResult(success=False, error=f"Audio file not found: {audio_path}")

        # Check file size
        size_mb = audio_path.stat().st_size / (1024 * 1024)
        if size_mb > self.config.max_audio_size_mb:
            return STTResult(
                success=False,
                error=f"Audio file too large: {size_mb:.1f}MB (max {self.config.max_audio_size_mb}MB)"
            )

        lang = language or self.config.language
        providers = [provider] if provider else [self.config.provider] + self.config.fallback_providers

        last_error = None
        for prov in providers:
            if prov is None:
                continue
            try:
                result = await self._transcribe_with_provider(audio_path, prov, lang, **kwargs)
                if result.success:
                    result.latency_ms = int(
                        (datetime.utcnow() - start_time).total_seconds() * 1000
                    )
                    return result
                else:
                    last_error = result.error
            except Exception as e:
                last_error = str(e)
                continue

        return STTResult(
            success=False,
            error=f"All STT providers failed. Last error: {last_error}",
        )

    # =========================================================================
    # Provider Implementations
    # =========================================================================

    async def _transcribe_with_provider(
        self,
        audio_path: Path,
        provider: STTProvider,
        language: str,
        **kwargs,
    ) -> STTResult:
        """Dispatch to provider-specific implementation."""
        if provider == STTProvider.DEEPGRAM:
            return await self._transcribe_deepgram(audio_path, language, **kwargs)
        elif provider == STTProvider.WHISPER_API:
            return await self._transcribe_whisper_api(audio_path, language, **kwargs)
        elif provider == STTProvider.WHISPER_LOCAL:
            return await self._transcribe_whisper_local(audio_path, language, **kwargs)
        else:
            return STTResult(success=False, error=f"Unknown STT provider: {provider}")

    async def _transcribe_deepgram(
        self, audio_path: Path, language: str, **kwargs
    ) -> STTResult:
        """Transcribe using Deepgram API."""
        try:
            import aiohttp
        except ImportError:
            return STTResult(success=False, error="aiohttp not installed")

        api_key = os.environ.get("DEEPGRAM_API_KEY")
        if not api_key:
            return STTResult(success=False, error="DEEPGRAM_API_KEY not set")

        url = "https://api.deepgram.com/v1/listen"
        params = {
            "model": self.config.deepgram_model,
            "tier": self.config.deepgram_tier,
            "language": language,
            "punctuate": str(self.config.deepgram_punctuate).lower(),
            "diarize": str(self.config.deepgram_diarize).lower(),
        }
        if self.config.detect_language:
            params["detect_language"] = "true"
            params.pop("language", None)

        headers = {
            "Authorization": f"Token {api_key}",
            "Content-Type": self._get_content_type(audio_path),
        }

        try:
            audio_data = audio_path.read_bytes()
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    params=params,
                    headers=headers,
                    data=audio_data,
                    timeout=aiohttp.ClientTimeout(total=self.config.timeout_seconds),
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        return STTResult(
                            success=False,
                            error=f"Deepgram API error {response.status}: {error_text}",
                        )

                    data = await response.json()
                    channel = data.get("results", {}).get("channels", [{}])[0]
                    alt = channel.get("alternatives", [{}])[0]
                    text = alt.get("transcript", "")
                    confidence = alt.get("confidence", 0.0)
                    detected_lang = (
                        data.get("results", {}).get("channels", [{}])[0]
                        .get("detected_language", language)
                    )

                    return STTResult(
                        success=bool(text),
                        text=text or None,
                        provider=STTProvider.DEEPGRAM,
                        language=detected_lang,
                        confidence=confidence,
                    )
        except asyncio.TimeoutError:
            return STTResult(success=False, error="Deepgram transcription timeout")
        except Exception as e:
            return STTResult(success=False, error=f"Deepgram error: {e}")

    async def _transcribe_whisper_api(
        self, audio_path: Path, language: str, **kwargs
    ) -> STTResult:
        """Transcribe using OpenAI Whisper API."""
        try:
            import aiohttp
        except ImportError:
            return STTResult(success=False, error="aiohttp not installed")

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return STTResult(success=False, error="OPENAI_API_KEY not set")

        url = "https://api.openai.com/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {api_key}"}

        try:
            data = aiohttp.FormData()
            data.add_field(
                "file",
                audio_path.read_bytes(),
                filename=audio_path.name,
                content_type=self._get_content_type(audio_path),
            )
            data.add_field("model", self.config.whisper_model)
            if not self.config.detect_language:
                data.add_field("language", language)
            data.add_field("response_format", "verbose_json")

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=headers,
                    data=data,
                    timeout=aiohttp.ClientTimeout(total=self.config.timeout_seconds),
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        return STTResult(
                            success=False,
                            error=f"Whisper API error {response.status}: {error_text}",
                        )

                    result_data = await response.json()
                    text = result_data.get("text", "")
                    detected_lang = result_data.get("language", language)
                    duration = result_data.get("duration")
                    segments = result_data.get("segments")

                    return STTResult(
                        success=bool(text),
                        text=text or None,
                        provider=STTProvider.WHISPER_API,
                        language=detected_lang,
                        duration_ms=int(duration * 1000) if duration else None,
                        segments=segments,
                    )
        except asyncio.TimeoutError:
            return STTResult(success=False, error="Whisper API timeout")
        except Exception as e:
            return STTResult(success=False, error=f"Whisper API error: {e}")

    async def _transcribe_whisper_local(
        self, audio_path: Path, language: str, **kwargs
    ) -> STTResult:
        """Transcribe using local Whisper model (offline)."""
        try:
            import whisper
        except ImportError:
            return STTResult(
                success=False,
                error="whisper not installed. Install with: pip install openai-whisper",
            )

        model_name = kwargs.get("model", self.config.whisper_local_model)

        try:
            loop = asyncio.get_event_loop()

            def _run():
                model = whisper.load_model(model_name)
                result = model.transcribe(
                    str(audio_path),
                    language=None if self.config.detect_language else language,
                )
                return result

            result = await loop.run_in_executor(None, _run)
            text = result.get("text", "").strip()
            detected_lang = result.get("language", language)
            segments = result.get("segments")

            return STTResult(
                success=bool(text),
                text=text or None,
                provider=STTProvider.WHISPER_LOCAL,
                language=detected_lang,
                segments=segments,
            )
        except Exception as e:
            return STTResult(success=False, error=f"Whisper local error: {e}")

    # =========================================================================
    # Helpers
    # =========================================================================

    @staticmethod
    def _get_content_type(path: Path) -> str:
        """Get MIME type for audio file."""
        ext = path.suffix.lower()
        return {
            ".mp3": "audio/mpeg",
            ".wav": "audio/wav",
            ".flac": "audio/flac",
            ".ogg": "audio/ogg",
            ".m4a": "audio/mp4",
            ".webm": "audio/webm",
        }.get(ext, "application/octet-stream")

    async def list_models(self, provider: Optional[STTProvider] = None) -> List[Dict]:
        """List available models for a provider."""
        prov = provider or self.config.provider

        if prov == STTProvider.DEEPGRAM:
            return [
                {"id": "nova-2", "name": "Nova 2", "description": "Latest, most accurate"},
                {"id": "nova", "name": "Nova", "description": "Fast and accurate"},
                {"id": "enhanced", "name": "Enhanced", "description": "High accuracy"},
                {"id": "base", "name": "Base", "description": "Standard quality"},
            ]
        elif prov == STTProvider.WHISPER_API:
            return [{"id": "whisper-1", "name": "Whisper v1", "description": "OpenAI Whisper"}]
        elif prov == STTProvider.WHISPER_LOCAL:
            return [
                {"id": "tiny", "name": "Tiny", "description": "39M params, fastest"},
                {"id": "base", "name": "Base", "description": "74M params"},
                {"id": "small", "name": "Small", "description": "244M params"},
                {"id": "medium", "name": "Medium", "description": "769M params"},
                {"id": "large", "name": "Large", "description": "1550M params, most accurate"},
            ]
        return []


# =============================================================================
# Module-level convenience
# =============================================================================

_default_stt: Optional[STT] = None


def _resolve_audio_file_params(filename: str, *, is_voice: bool = False) -> tuple[str, str]:
    """Return a normalized upload filename and MIME type for an audio file.

    Voice messages are normalized to Telegram's preferred ``.oga`` filename
    while preserving the Ogg MIME type.
    """
    path = Path(filename)

    if is_voice:
        return "voice.oga", "audio/ogg"

    mime, _ = mimetypes.guess_type(str(path))
    if mime:
        return path.name, mime

    mime = STT._get_content_type(path)
    return path.name, mime


def get_stt() -> STT:
    """Get default STT instance."""
    global _default_stt
    if _default_stt is None:
        _default_stt = STT()
    return _default_stt


async def transcribe(audio_path: str | Path, **kwargs) -> Optional[str]:
    """Transcribe audio file to text. Returns None on failure."""
    result = await get_stt().transcribe(audio_path, **kwargs)
    return result.text if result.success else None


async def transcribe_full(audio_path: str | Path, **kwargs) -> STTResult:
    """Transcribe audio and return full result."""
    return await get_stt().transcribe(audio_path, **kwargs)


def transcribe_sync(audio_path: str | Path, **kwargs) -> Optional[str]:
    """Synchronous wrapper for transcribe()."""
    return asyncio.run(transcribe(audio_path, **kwargs))
