"""
NAVIG Audio Generation Tool

AI audio via ElevenLabs (official free tier — music, sound effects, and TTS):
- music : compose a track from a text prompt        (POST /v1/music)
- sfx   : generate a sound effect from a description (POST /v1/sound-generation)
- tts   : text-to-speech in a chosen voice           (POST /v1/text-to-speech/{voice})

Each call returns raw audio bytes, saved to a local file. The key resolves
through the shared media resolver (env → vault). Nothing here deletes or
overwrites a source; it only produces new audio files.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from navig.platform.paths import media_dir

if TYPE_CHECKING:
    import httpx

try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    httpx = None  # type: ignore[assignment]
    HTTPX_AVAILABLE = False

from navig.debug_logger import get_debug_logger
from navig.tools.media_providers import resolve_media_key

logger = get_debug_logger()

_ELEVEN_BASE = "https://api.elevenlabs.io/v1"
# ElevenLabs' documented default voice ("Rachel").
_DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"


class AudioProvider(Enum):
    """Supported audio generation providers."""

    ELEVENLABS = "elevenlabs"


class AudioKind(Enum):
    """What to generate."""

    MUSIC = "music"
    SFX = "sfx"
    TTS = "tts"


@dataclass
class AudioGenerationConfig:
    """Configuration for audio generation."""

    provider: AudioProvider = AudioProvider.ELEVENLABS
    tts_voice_id: str = _DEFAULT_VOICE_ID
    tts_model: str = "eleven_multilingual_v2"
    music_model: str = "music_v1"
    output_format: str = "mp3_44100_128"

    output_dir: str = field(
        default_factory=lambda: str(media_dir("audio"))
    )
    save_locally: bool = True

    @classmethod
    def from_env(cls) -> AudioGenerationConfig:
        return cls()


@dataclass
class GeneratedAudio:
    """A generated audio result."""

    prompt: str
    kind: AudioKind
    provider: AudioProvider
    local_path: str | None = None
    model: str | None = None
    generation_time: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "kind": self.kind.value,
            "provider": self.provider.value,
            "local_path": self.local_path,
            "model": self.model,
            "generation_time": self.generation_time,
            "created_at": self.created_at.isoformat(),
        }


class AudioGenerator:
    """ElevenLabs-backed audio generation client."""

    def __init__(self, config: AudioGenerationConfig | None = None):
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx is required for audio generation. Install: pip install httpx")
        self.config = config or AudioGenerationConfig.from_env()
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=180.0)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _api_key(self) -> str:
        key = resolve_media_key("elevenlabs", "ELEVENLABS_API_KEY", "ELEVEN_API_KEY")
        if not key:
            raise ValueError("ElevenLabs API key not configured")
        return key

    async def generate(
        self,
        prompt: str,
        kind: AudioKind | str = AudioKind.MUSIC,
        duration_s: float | None = None,
        voice_id: str | None = None,
        save: bool = True,
    ) -> GeneratedAudio:
        """Generate audio (music / sfx / tts) from a text prompt."""
        if isinstance(kind, str):
            kind = AudioKind(kind)
        key = self._api_key()
        client = await self._get_client()
        headers = {"xi-api-key": key, "Content-Type": "application/json"}
        start = datetime.now()

        if kind == AudioKind.MUSIC:
            body: dict[str, Any] = {"prompt": prompt, "model_id": self.config.music_model}
            if duration_s:
                body["music_length_ms"] = int(duration_s * 1000)
            resp = await client.post(f"{_ELEVEN_BASE}/music", headers=headers, json=body)
            model = self.config.music_model
        elif kind == AudioKind.SFX:
            body = {"text": prompt}
            if duration_s:
                body["duration_seconds"] = duration_s
            resp = await client.post(
                f"{_ELEVEN_BASE}/sound-generation", headers=headers, json=body
            )
            model = "sound-generation"
        else:  # TTS
            vid = voice_id or self.config.tts_voice_id
            body = {"text": prompt, "model_id": self.config.tts_model}
            resp = await client.post(
                f"{_ELEVEN_BASE}/text-to-speech/{vid}?output_format={self.config.output_format}",
                headers=headers,
                json=body,
            )
            model = self.config.tts_model

        resp.raise_for_status()
        audio_bytes = resp.content
        generation_time = (datetime.now() - start).total_seconds()

        result = GeneratedAudio(
            prompt=prompt,
            kind=kind,
            provider=AudioProvider.ELEVENLABS,
            model=model,
            generation_time=generation_time,
        )

        if save and self.config.save_locally:
            out = Path(self.config.output_dir).expanduser()
            out.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = out / f"{kind.value}_{ts}.mp3"
            path.write_bytes(audio_bytes)
            result.local_path = str(path)

        return result


async def generate_audio(
    prompt: str,
    kind: str = "music",
    duration_s: float | None = None,
    **kwargs,
) -> GeneratedAudio:
    """Generate a single audio clip from a prompt. Returns a GeneratedAudio."""
    gen = AudioGenerator()
    try:
        return await gen.generate(prompt, kind=kind, duration_s=duration_s, **kwargs)
    finally:
        await gen.close()


def is_audio_generation_available() -> bool:
    """True when the ElevenLabs key is resolvable."""
    if not HTTPX_AVAILABLE:
        return False
    from navig.tools.media_providers import MEDIA_CATALOG, key_status

    return any(key_status(entry) for entry in MEDIA_CATALOG["audio"])
