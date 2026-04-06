"""
Text-to-Speech Module for NAVIG

Provides multi-provider TTS with voice messaging support,
inspired by comprehensive TTS pipeline patterns.

Supported Providers:
- OpenAI TTS (gpt-4o-mini-tts, tts-1, tts-1-hd)
- ElevenLabs
- Edge TTS (free, no API key required)

Features:
- Provider fallback chain
- Voice-compatible audio for Telegram/WhatsApp
- Configurable voice settings
- Auto-summarization for long text
- Caching for repeated phrases

Usage:
    from navig.voice import speak, TTS

    # Simple usage
    audio_path = await speak("Hello world!")

    # With provider selection
    tts = TTS(provider="openai")
    result = await tts.synthesize("Hello!", voice="nova")
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from navig.llm_router import PROVIDER_RESOURCE_URLS as _PRUL  # noqa: F401

# =============================================================================
# Types
# =============================================================================


class TTSProvider(str, Enum):
    """Supported TTS providers."""

    OPENAI = "openai"
    ELEVENLABS = "elevenlabs"
    EDGE = "edge"
    GOOGLE_CLOUD = "google_cloud"
    DEEPGRAM = "deepgram"


class TTSVoice(str, Enum):
    """Common voice identifiers."""

    # OpenAI voices
    ALLOY = "alloy"
    ECHO = "echo"
    FABLE = "fable"
    ONYX = "onyx"
    NOVA = "nova"
    SHIMMER = "shimmer"

    # Edge TTS voices (selected)
    EDGE_EN_US_JENNY = "en-US-JennyNeural"
    EDGE_EN_US_GUY = "en-US-GuyNeural"
    EDGE_EN_GB_SONIA = "en-GB-SoniaNeural"
    EDGE_FR_DENISE = "fr-FR-DeniseNeural"
    EDGE_DE_KATJA = "de-DE-KatjaNeural"


@dataclass
class TTSConfig:
    """TTS configuration."""

    # Provider
    provider: TTSProvider = TTSProvider.EDGE
    fallback_providers: list[TTSProvider] = field(default_factory=lambda: [TTSProvider.EDGE])

    # Voice settings
    voice: str = "en-US-JennyNeural"
    speed: float = 1.0
    pitch: str = "+0Hz"

    # OpenAI settings
    openai_model: str = "tts-1"
    openai_voice: str = "nova"

    # ElevenLabs settings
    elevenlabs_model: str = "eleven_multilingual_v2"
    elevenlabs_voice_id: str = "pMsXgVXv3BLzUgSXRplE"
    elevenlabs_stability: float = 0.5
    elevenlabs_similarity: float = 0.75

    # Google Cloud TTS settings
    google_cloud_language: str = "en-US"
    google_cloud_voice: str = "en-US-Neural2-C"
    google_cloud_encoding: str = "MP3"
    google_cloud_speaking_rate: float = 1.0

    # Deepgram TTS settings
    deepgram_model: str = "aura-asteria-en"

    # Output
    output_format: str = "mp3"
    sample_rate: int = 24000

    # Limits
    max_text_length: int = 4096
    auto_summarize: bool = True
    summarize_threshold: int = 1500

    # Caching
    cache_enabled: bool = True
    cache_dir: Path | None = None

    # Timeout
    timeout_seconds: int = 30

    def get_cache_dir(self) -> Path:
        """Get cache directory, creating if needed."""
        if self.cache_dir:
            cache = self.cache_dir
        else:
            cache = Path.home() / ".navig" / "cache" / "tts"
        cache.mkdir(parents=True, exist_ok=True)
        return cache


@dataclass
class TTSResult:
    """Result from TTS synthesis."""

    success: bool
    audio_path: Path | None = None
    audio_data: bytes | None = None

    # Metadata
    provider: TTSProvider | None = None
    voice: str | None = None
    duration_ms: int | None = None
    latency_ms: int | None = None

    # Audio properties
    format: str = "mp3"
    sample_rate: int = 24000
    voice_compatible: bool = False  # True if suitable for voice messages

    # Error info
    error: str | None = None

    def __bool__(self) -> bool:
        return self.success


# =============================================================================
# TTS Engine
# =============================================================================


class TTS:
    """
    Text-to-Speech engine with multi-provider support.

    Supports OpenAI, ElevenLabs, and Edge TTS with automatic fallback.
    """

    def __init__(self, config: TTSConfig | None = None):
        self.config = config or TTSConfig()
        self._cache: dict[str, Path] = {}

    # =========================================================================
    # Main API
    # =========================================================================

    async def synthesize(
        self,
        text: str,
        provider: TTSProvider | None = None,
        voice: str | None = None,
        output_path: Path | None = None,
        **kwargs,
    ) -> TTSResult:
        """
        Synthesize speech from text.

        Args:
            text: Text to convert to speech
            provider: Override provider (uses config default if not specified)
            voice: Override voice
            output_path: Where to save audio (temp file if not specified)
            **kwargs: Additional provider-specific options

        Returns:
            TTSResult with audio path or error
        """
        start_time = datetime.now()  # utcnow() deprecated in Py3.12+

        # Validate and prepare text
        text = self._prepare_text(text)
        if not text:
            return TTSResult(success=False, error="Empty text")

        # Check cache
        cache_key = self._get_cache_key(text, provider, voice)
        if self.config.cache_enabled:
            cached = self._get_cached(cache_key)
            if cached:
                return TTSResult(
                    success=True,
                    audio_path=cached,
                    provider=provider or self.config.provider,
                    voice=voice or self.config.voice,
                    format=self.config.output_format,
                )

        # Try providers in order
        providers = (
            [provider] if provider else [self.config.provider] + self.config.fallback_providers
        )

        last_error = None
        for prov in providers:
            if prov is None:
                continue

            try:
                result = await self._synthesize_with_provider(
                    text, prov, voice, output_path, **kwargs
                )
                if result.success:
                    # Calculate latency
                    result.latency_ms = int((datetime.now() - start_time).total_seconds() * 1000)

                    # Cache on success
                    if self.config.cache_enabled and result.audio_path:
                        self._cache[cache_key] = result.audio_path

                    return result
                else:
                    last_error = result.error
            except Exception as e:
                last_error = str(e)
                continue

        return TTSResult(success=False, error=f"All providers failed. Last error: {last_error}")

    async def speak(self, text: str, **kwargs) -> Path | None:
        """
        Convenience method - synthesize and return path.

        Returns None on failure.
        """
        result = await self.synthesize(text, **kwargs)
        return result.audio_path if result.success else None

    # =========================================================================
    # Provider Implementations
    # =========================================================================

    async def _synthesize_with_provider(
        self,
        text: str,
        provider: TTSProvider,
        voice: str | None,
        output_path: Path | None,
        **kwargs,
    ) -> TTSResult:
        """Dispatch to provider-specific implementation."""
        if provider == TTSProvider.OPENAI:
            return await self._synthesize_openai(text, voice, output_path, **kwargs)
        elif provider == TTSProvider.ELEVENLABS:
            return await self._synthesize_elevenlabs(text, voice, output_path, **kwargs)
        elif provider == TTSProvider.EDGE:
            return await self._synthesize_edge(text, voice, output_path, **kwargs)
        elif provider == TTSProvider.GOOGLE_CLOUD:
            return await self._synthesize_google_cloud(text, voice, output_path, **kwargs)
        elif provider == TTSProvider.DEEPGRAM:
            return await self._synthesize_deepgram(text, voice, output_path, **kwargs)
        else:
            return TTSResult(success=False, error=f"Unknown provider: {provider}")

    async def _synthesize_openai(
        self, text: str, voice: str | None, output_path: Path | None, **kwargs
    ) -> TTSResult:
        """Synthesize using OpenAI TTS API."""
        try:
            import aiohttp
        except ImportError:
            return TTSResult(success=False, error="aiohttp not installed")

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return TTSResult(success=False, error="OPENAI_API_KEY not set")

        voice = voice or self.config.openai_voice
        model = kwargs.get("model", self.config.openai_model)

        url = "https://api.openai.com/v1/audio/speech"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "input": text,
            "voice": voice,
            "response_format": "mp3",
            "speed": self.config.speed,
        }

        try:
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.config.timeout_seconds),
                ) as response,
            ):
                if response.status != 200:
                    error_text = await response.text()
                    return TTSResult(
                        success=False,
                        error=f"OpenAI API error {response.status}: {error_text}",
                    )

                audio_data = await response.read()

                # Save to file
                if output_path is None:
                    output_path = self._get_temp_path("openai", ".mp3")

                output_path.write_bytes(audio_data)

                return TTSResult(
                    success=True,
                    audio_path=output_path,
                    audio_data=audio_data,
                    provider=TTSProvider.OPENAI,
                    voice=voice,
                    format="mp3",
                    sample_rate=24000,
                    voice_compatible=True,  # OpenAI MP3 works for voice messages
                )

        except asyncio.TimeoutError:
            return TTSResult(success=False, error="OpenAI TTS timeout")
        except Exception as e:
            return TTSResult(success=False, error=f"OpenAI TTS error: {e}")

    async def _synthesize_elevenlabs(
        self, text: str, voice: str | None, output_path: Path | None, **kwargs
    ) -> TTSResult:
        """Synthesize using ElevenLabs API."""
        try:
            import aiohttp
        except ImportError:
            return TTSResult(success=False, error="aiohttp not installed")

        api_key = os.environ.get("ELEVENLABS_API_KEY") or os.environ.get("XI_API_KEY")
        if not api_key:
            return TTSResult(success=False, error="ELEVENLABS_API_KEY not set")

        voice_id = voice or self.config.elevenlabs_voice_id
        model_id = kwargs.get("model_id", self.config.elevenlabs_model)

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {
            "xi-api-key": api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "text": text,
            "model_id": model_id,
            "voice_settings": {
                "stability": self.config.elevenlabs_stability,
                "similarity_boost": self.config.elevenlabs_similarity,
            },
        }

        try:
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.config.timeout_seconds),
                ) as response,
            ):
                if response.status != 200:
                    error_text = await response.text()
                    return TTSResult(
                        success=False,
                        error=f"ElevenLabs API error {response.status}: {error_text}",
                    )

                audio_data = await response.read()

                if output_path is None:
                    output_path = self._get_temp_path("elevenlabs", ".mp3")

                output_path.write_bytes(audio_data)

                return TTSResult(
                    success=True,
                    audio_path=output_path,
                    audio_data=audio_data,
                    provider=TTSProvider.ELEVENLABS,
                    voice=voice_id,
                    format="mp3",
                    sample_rate=44100,
                    voice_compatible=True,
                )

        except asyncio.TimeoutError:
            return TTSResult(success=False, error="ElevenLabs TTS timeout")
        except Exception as e:
            return TTSResult(success=False, error=f"ElevenLabs TTS error: {e}")

    async def _synthesize_edge(
        self, text: str, voice: str | None, output_path: Path | None, **kwargs
    ) -> TTSResult:
        """Synthesize using Edge TTS (free, no API key)."""
        try:
            import edge_tts
        except ImportError:
            return TTSResult(
                success=False,
                error="edge-tts not installed. Install with: pip install edge-tts",
            )

        voice = voice or self.config.voice

        if output_path is None:
            output_path = self._get_temp_path("edge", ".mp3")

        try:
            communicate = edge_tts.Communicate(
                text,
                voice,
                rate=f"{int((self.config.speed - 1) * 100):+d}%",
                pitch=self.config.pitch,
            )

            await communicate.save(str(output_path))

            return TTSResult(
                success=True,
                audio_path=output_path,
                provider=TTSProvider.EDGE,
                voice=voice,
                format="mp3",
                sample_rate=24000,
                voice_compatible=True,
            )

        except Exception as e:
            return TTSResult(success=False, error=f"Edge TTS error: {e}")

    async def _synthesize_google_cloud(
        self, text: str, voice: str | None, output_path: Path | None, **kwargs
    ) -> TTSResult:
        """Synthesize using Google Cloud Text-to-Speech API."""
        try:
            import aiohttp
        except ImportError:
            return TTSResult(success=False, error="aiohttp not installed")

        api_key = os.environ.get("GOOGLE_CLOUD_API_KEY") or os.environ.get("GOOGLE_TTS_API_KEY")
        if not api_key:
            return TTSResult(success=False, error="GOOGLE_CLOUD_API_KEY not set")

        voice_name = voice or self.config.google_cloud_voice
        language = kwargs.get("language", self.config.google_cloud_language)

        url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={api_key}"
        payload = {
            "input": {"text": text},
            "voice": {
                "languageCode": language,
                "name": voice_name,
            },
            "audioConfig": {
                "audioEncoding": self.config.google_cloud_encoding,
                "speakingRate": self.config.google_cloud_speaking_rate,
            },
        }

        try:
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.config.timeout_seconds),
                ) as response,
            ):
                if response.status != 200:
                    error_text = await response.text()
                    return TTSResult(
                        success=False,
                        error=f"Google Cloud TTS error {response.status}: {error_text}",
                    )

                import base64

                data = await response.json()
                audio_content = data.get("audioContent", "")
                audio_data = base64.b64decode(audio_content)

                if output_path is None:
                    output_path = self._get_temp_path("google", ".mp3")

                output_path.write_bytes(audio_data)

                return TTSResult(
                    success=True,
                    audio_path=output_path,
                    audio_data=audio_data,
                    provider=TTSProvider.GOOGLE_CLOUD,
                    voice=voice_name,
                    format="mp3",
                    sample_rate=24000,
                    voice_compatible=True,
                )
        except asyncio.TimeoutError:
            return TTSResult(success=False, error="Google Cloud TTS timeout")
        except Exception as e:
            return TTSResult(success=False, error=f"Google Cloud TTS error: {e}")

    async def _synthesize_deepgram(
        self, text: str, voice: str | None, output_path: Path | None, **kwargs
    ) -> TTSResult:
        """Synthesize using Deepgram Aura TTS API."""
        try:
            import aiohttp
        except ImportError:
            return TTSResult(success=False, error="aiohttp not installed")

        api_key = os.environ.get("DEEPGRAM_API_KEY")
        if not api_key:
            return TTSResult(success=False, error="DEEPGRAM_API_KEY not set")

        model = voice or self.config.deepgram_model

        url = f"https://api.deepgram.com/v1/speak?model={model}"
        headers = {
            "Authorization": f"Token {api_key}",
            "Content-Type": "application/json",
        }
        payload = {"text": text}

        try:
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.config.timeout_seconds),
                ) as response,
            ):
                if response.status != 200:
                    error_text = await response.text()
                    return TTSResult(
                        success=False,
                        error=f"Deepgram TTS error {response.status}: {error_text}",
                    )

                audio_data = await response.read()

                if output_path is None:
                    output_path = self._get_temp_path("deepgram", ".mp3")

                output_path.write_bytes(audio_data)

                return TTSResult(
                    success=True,
                    audio_path=output_path,
                    audio_data=audio_data,
                    provider=TTSProvider.DEEPGRAM,
                    voice=model,
                    format="mp3",
                    sample_rate=24000,
                    voice_compatible=True,
                )
        except asyncio.TimeoutError:
            return TTSResult(success=False, error="Deepgram TTS timeout")
        except Exception as e:
            return TTSResult(success=False, error=f"Deepgram TTS error: {e}")

    # =========================================================================
    # Helpers
    # =========================================================================

    def _prepare_text(self, text: str) -> str:
        """Prepare text for synthesis."""
        # Strip whitespace
        text = text.strip()

        # Truncate if too long
        if len(text) > self.config.max_text_length:
            text = text[: self.config.max_text_length] + "..."

        return text

    def _get_cache_key(self, text: str, provider: TTSProvider | None, voice: str | None) -> str:
        """Generate cache key for text+provider+voice combo."""
        prov = provider or self.config.provider
        v = voice or self.config.voice
        key_data = f"{prov.value}:{v}:{text}"
        return hashlib.sha256(key_data.encode()).hexdigest()[:16]

    def _get_cached(self, key: str) -> Path | None:
        """Check cache for existing audio."""
        if key in self._cache:
            path = self._cache[key]
            if path.exists():
                return path
            else:
                del self._cache[key]

        # Check disk cache
        cache_dir = self.config.get_cache_dir()
        cache_file = cache_dir / f"{key}.mp3"
        if cache_file.exists():
            self._cache[key] = cache_file
            return cache_file

        return None

    def _get_temp_path(self, prefix: str, suffix: str) -> Path:
        """Get a temp file path for audio output."""
        if self.config.cache_enabled:
            cache_dir = self.config.get_cache_dir()
            return cache_dir / f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{suffix}"
        else:
            fd, path = tempfile.mkstemp(suffix=suffix, prefix=f"navig_tts_{prefix}_")
            os.close(fd)
            return Path(path)

    # =========================================================================
    # Voice Listing
    # =========================================================================

    async def list_voices(self, provider: TTSProvider | None = None) -> list[dict[str, str]]:
        """
        List available voices for a provider.

        Args:
            provider: Provider to list voices for (uses config default if None)

        Returns:
            List of voice dicts with id, name, language
        """
        provider = provider or self.config.provider

        if provider == TTSProvider.OPENAI:
            return [
                {"id": "alloy", "name": "Alloy", "language": "en"},
                {"id": "echo", "name": "Echo", "language": "en"},
                {"id": "fable", "name": "Fable", "language": "en"},
                {"id": "onyx", "name": "Onyx", "language": "en"},
                {"id": "nova", "name": "Nova", "language": "en"},
                {"id": "shimmer", "name": "Shimmer", "language": "en"},
            ]

        elif provider == TTSProvider.EDGE:
            try:
                import edge_tts

                voices = await edge_tts.list_voices()
                return [
                    {
                        "id": v["ShortName"],
                        "name": v["FriendlyName"],
                        "language": v["Locale"],
                    }
                    for v in voices
                ]
            except ImportError:
                return []

        return []


# =============================================================================
# Module-level convenience
# =============================================================================

_default_tts: TTS | None = None


def get_tts() -> TTS:
    """Get default TTS instance."""
    global _default_tts
    if _default_tts is None:
        _default_tts = TTS()
    return _default_tts


async def speak(text: str, **kwargs) -> Path | None:
    """Synthesize speech and return audio path."""
    return await get_tts().speak(text, **kwargs)


async def synthesize(text: str, **kwargs) -> TTSResult:
    """Synthesize speech and return full result."""
    return await get_tts().synthesize(text, **kwargs)


def speak_sync(text: str, **kwargs) -> Path | None:
    """Synchronous wrapper for speak()."""
    return asyncio.run(speak(text, **kwargs))
