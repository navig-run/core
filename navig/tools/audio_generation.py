"""
NAVIG Audio Generation Tool

AI audio via ElevenLabs (official free tier — music, sound effects, and TTS):
- music : compose a track from a text prompt        (POST /v1/music)
- sfx   : generate a sound effect from a description (POST /v1/sound-generation)
- tts   : text-to-speech in a chosen voice           (POST /v1/text-to-speech/{voice})

Each call returns raw audio bytes, saved to a local file. The key resolves
through the shared media resolver (env → vault). Nothing here deletes or
overwrites a source; it only produces new audio files.

Beyond one-shot clips, this also carries what **long-form narration** needs:
:meth:`AudioGenerator.tts_with_timestamps` returns audio plus character-level timings
(the only honest source for subtitles), and ``generate()`` accepts the surrounding-text
parameters that keep prosody continuous when a script is too long for one request and
has to be split — without them, split speech audibly restarts flat at every seam.
Voice management (:meth:`list_voices`, :meth:`create_instant_voice_clone`) and
:meth:`subscription` round it out, so a plan limit can be reported before a render
starts rather than discovered halfway through one.
"""

from __future__ import annotations

import base64
import mimetypes
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

# The API rejects a request carrying more than three previous ids. Trimming here rather
# than forwarding the caller's list means a long render fails at request 4 in testing
# instead of in production.
_MAX_PREVIOUS_REQUEST_IDS = 3


class AudioGenerationError(RuntimeError):
    """The provider refused the request — quota, plan, voice, or a malformed body."""


def _check(resp: Any, what: str) -> None:
    """Raise with the provider's OWN message, not just a status code.

    The failures that actually happen here — out of credits, a voice the plan may not
    use, a feature above the tier — all arrive as 401/402, and a bare status code gives
    the user nothing to act on. ElevenLabs puts the useful sentence in the body.
    """
    if resp.status_code < 400:
        return
    detail: Any = None
    try:
        payload = resp.json()
        detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
        if isinstance(detail, dict):
            detail = detail.get("message") or detail.get("status") or detail
    except Exception:  # noqa: BLE001 — a non-JSON error body is still worth reporting
        detail = (resp.text or "").strip()[:300] or None
    raise AudioGenerationError(
        f"ElevenLabs {what} failed ({resp.status_code}): {detail or 'no detail returned'}"
    )


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
    # The bytes themselves. Without this, a ``save=False`` call described audio that
    # existed nowhere, which made the client unusable to any caller assembling something
    # larger out of many clips.
    audio: bytes | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "kind": self.kind.value,
            "provider": self.provider.value,
            "local_path": self.local_path,
            "model": self.model,
            "generation_time": self.generation_time,
            "created_at": self.created_at.isoformat(),
            # A count, never the blob — this dict gets logged and JSON-serialised.
            "bytes": len(self.audio or b""),
        }


@dataclass
class TimedAudio:
    """Speech plus the character-level timings subtitles are cut from."""

    audio: bytes
    characters: list[str] = field(default_factory=list)
    starts: list[float] = field(default_factory=list)
    ends: list[float] = field(default_factory=list)
    request_id: str | None = None
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "bytes": len(self.audio or b""),
            "characters": len(self.characters),
            "request_id": self.request_id,
            "model": self.model,
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

    def _tts_body(
        self,
        text: str,
        *,
        model_id: str | None,
        voice_settings: dict[str, Any] | None,
        language_code: str | None,
        previous_text: str | None,
        next_text: str | None,
        previous_request_ids: list[str] | None,
    ) -> dict[str, Any]:
        """The TTS request body, shared by the plain and timestamped endpoints.

        ``previous_text`` / ``next_text`` are what stop a long script from sounding like
        a series of unrelated takes: the model reads each chunk knowing what surrounds
        it, so intonation carries across the seam instead of resetting.
        """
        body: dict[str, Any] = {"text": text, "model_id": model_id or self.config.tts_model}
        if voice_settings:
            body["voice_settings"] = voice_settings
        if language_code:
            body["language_code"] = language_code
        if previous_text:
            body["previous_text"] = previous_text
        if next_text:
            body["next_text"] = next_text
        if previous_request_ids:
            body["previous_request_ids"] = list(previous_request_ids)[-_MAX_PREVIOUS_REQUEST_IDS:]
        return body

    async def generate(
        self,
        prompt: str,
        kind: AudioKind | str = AudioKind.MUSIC,
        duration_s: float | None = None,
        voice_id: str | None = None,
        save: bool = True,
        *,
        model_id: str | None = None,
        voice_settings: dict[str, Any] | None = None,
        language_code: str | None = None,
        previous_text: str | None = None,
        next_text: str | None = None,
        previous_request_ids: list[str] | None = None,
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
            body = self._tts_body(
                prompt, model_id=model_id, voice_settings=voice_settings,
                language_code=language_code, previous_text=previous_text,
                next_text=next_text, previous_request_ids=previous_request_ids,
            )
            resp = await client.post(
                f"{_ELEVEN_BASE}/text-to-speech/{vid}?output_format={self.config.output_format}",
                headers=headers,
                json=body,
            )
            model = body["model_id"]

        _check(resp, kind.value)
        audio_bytes = resp.content
        generation_time = (datetime.now() - start).total_seconds()

        result = GeneratedAudio(
            prompt=prompt,
            kind=kind,
            provider=AudioProvider.ELEVENLABS,
            model=model,
            generation_time=generation_time,
            audio=audio_bytes,
        )

        if save and self.config.save_locally:
            out = Path(self.config.output_dir).expanduser()
            out.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = out / f"{kind.value}_{ts}.mp3"
            path.write_bytes(audio_bytes)
            result.local_path = str(path)

        return result

    async def tts_with_timestamps(
        self,
        text: str,
        voice_id: str | None = None,
        *,
        model_id: str | None = None,
        voice_settings: dict[str, Any] | None = None,
        language_code: str | None = None,
        previous_text: str | None = None,
        next_text: str | None = None,
        previous_request_ids: list[str] | None = None,
    ) -> TimedAudio:
        """Speak ``text`` and return the audio **with character-level timings**.

        Subtitles cut from a duration estimate drift; these timings come from the same
        synthesis that produced the audio, so a caption lands on the word it belongs to.
        """
        key = self._api_key()
        client = await self._get_client()
        vid = voice_id or self.config.tts_voice_id
        body = self._tts_body(
            text, model_id=model_id, voice_settings=voice_settings,
            language_code=language_code, previous_text=previous_text,
            next_text=next_text, previous_request_ids=previous_request_ids,
        )
        resp = await client.post(
            f"{_ELEVEN_BASE}/text-to-speech/{vid}/with-timestamps"
            f"?output_format={self.config.output_format}",
            headers={"xi-api-key": key, "Content-Type": "application/json"},
            json=body,
        )
        _check(resp, "tts-with-timestamps")
        payload = resp.json()
        # `normalized_alignment` describes the text as spoken (numbers expanded, etc.);
        # `alignment` maps to the characters the caller actually passed in, which is what
        # a caption has to line up with.
        alignment = payload.get("alignment") or payload.get("normalized_alignment") or {}
        return TimedAudio(
            audio=base64.b64decode(payload.get("audio_base64") or ""),
            characters=list(alignment.get("characters") or []),
            starts=list(alignment.get("character_start_times_seconds") or []),
            ends=list(alignment.get("character_end_times_seconds") or []),
            request_id=resp.headers.get("request-id"),
            model=body["model_id"],
        )

    async def list_voices(self) -> list[dict[str, Any]]:
        """Every voice this key can speak with (stock library plus your own)."""
        client = await self._get_client()
        resp = await client.get(
            f"{_ELEVEN_BASE}/voices", headers={"xi-api-key": self._api_key()}
        )
        _check(resp, "voice listing")
        payload = resp.json()
        return list(payload.get("voices") or []) if isinstance(payload, dict) else []

    async def subscription(self) -> dict[str, Any]:
        """The plan: tier, credits used/remaining, and cloning entitlements."""
        client = await self._get_client()
        resp = await client.get(
            f"{_ELEVEN_BASE}/user/subscription", headers={"xi-api-key": self._api_key()}
        )
        _check(resp, "subscription lookup")
        payload = resp.json()
        return payload if isinstance(payload, dict) else {}

    async def create_instant_voice_clone(
        self,
        name: str,
        files: list[Path],
        *,
        description: str | None = None,
        remove_background_noise: bool = True,
    ) -> dict[str, Any]:
        """Clone a voice from recordings. Returns the payload, including ``voice_id``."""
        if not files:
            raise AudioGenerationError("voice cloning needs at least one audio sample")
        missing = [f for f in files if not Path(f).exists()]
        if missing:
            raise AudioGenerationError(
                f"missing sample(s): {', '.join(Path(f).name for f in missing)}"
            )
        client = await self._get_client()
        uploads = [
            (
                "files",
                (
                    Path(f).name,
                    Path(f).read_bytes(),
                    mimetypes.guess_type(Path(f).name)[0] or "application/octet-stream",
                ),
            )
            for f in files
        ]
        data = {"name": name, "remove_background_noise": str(remove_background_noise).lower()}
        if description:
            data["description"] = description
        resp = await client.post(
            f"{_ELEVEN_BASE}/voices/add",
            headers={"xi-api-key": self._api_key()},  # no Content-Type: httpx sets the boundary
            data=data,
            files=uploads,
        )
        _check(resp, "voice cloning")
        payload = resp.json()
        return payload if isinstance(payload, dict) else {}


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
