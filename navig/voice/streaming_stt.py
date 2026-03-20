"""
Streaming STT Module for NAVIG

Real-time speech-to-text via Deepgram WebSocket API with file-based fallback
to OpenAI Whisper API or local Whisper model.

Architecture:
    Audio chunks (asyncio.Queue) ──► Deepgram WSS ──► interim + final results
                                        ↓ failure
                                    Whisper API  (file-based)
                                        ↓ failure
                                    Whisper Local (file-based, offline)

Design decisions:
- Streaming path uses Deepgram's "nova-2" model over WebSocket for ~200ms
  first-token latency.
- Fallback collapses all buffered chunks to a temp WAV file and calls the
  existing STT.transcribe() path — no code duplication.
- VaultV2 is the exclusive key source; env-vars are never consulted directly.
- Interim results are emitted as they arrive; callers can display them
  immediately for reduced perceived latency.

Usage:
    from navig.voice.streaming_stt import StreamingSTT, StreamingSTTConfig

    stt = StreamingSTT()
    audio_q: asyncio.Queue[bytes | None] = asyncio.Queue()

    async for result in stt.stream(audio_q):
        if result.is_final:
            print("Final:", result.transcript)
        else:
            print("Interim:", result.transcript)
"""

from __future__ import annotations

import asyncio
import io
import logging
import struct
import tempfile
import time
import wave
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import AsyncIterator, List, Optional

logger = logging.getLogger("navig.voice.streaming_stt")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class StreamingProvider(str, Enum):
    DEEPGRAM = "deepgram"
    WHISPER_API = "whisper_api"
    WHISPER_LOCAL = "whisper_local"


@dataclass
class StreamingSTTConfig:
    """Configuration for StreamingSTT."""

    # Primary provider for streaming
    primary: StreamingProvider = StreamingProvider.DEEPGRAM

    # Fallback when primary fails (file-based)
    fallback: StreamingProvider = StreamingProvider.WHISPER_API

    # Deepgram model
    deepgram_model: str = "nova-2"

    # Language code (e.g. "en-US")
    language: str = "en"

    # Deepgram: auto-detect language (overrides language above)
    detect_language: bool = False

    # Deepgram: emit interim results
    interim_results: bool = True

    # Deepgram: utterance end detection (ms of silence)
    utterance_end_ms: int = 1000

    # Audio: sample rate fed to Deepgram
    sample_rate: int = 16_000

    # Audio: channels
    channels: int = 1

    # Audio: bits per sample (for WAV header)
    bits_per_sample: int = 16

    # Whisper API model fallback
    whisper_model: str = "whisper-1"

    # Whisper local model size fallback
    whisper_local_model: str = "base"

    # Vault labels for API keys
    deepgram_vault_label: str = "deepgram/api-key"
    openai_vault_label: str = "openai/api-key"

    # Total timeout for a streaming session (seconds)
    session_timeout: float = 60.0

    # Deepgram WebSocket endpoint
    deepgram_ws_url: str = "wss://api.deepgram.com/v1/listen"


# ---------------------------------------------------------------------------
# Result Types
# ---------------------------------------------------------------------------

@dataclass
class StreamingSTTResult:
    """A single STT result from the streaming pipeline."""

    transcript:  str
    is_final:    bool
    confidence:  float = 0.0
    language:    Optional[str] = None
    provider:    StreamingProvider = StreamingProvider.DEEPGRAM
    latency_ms:  Optional[float] = None

    # Word-level timestamps (if available from Deepgram)
    words: List[dict] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.transcript)

    def __repr__(self) -> str:
        flag = "✓" if self.is_final else "…"
        return f"<STTResult {flag} {self.transcript!r} conf={self.confidence:.2f}>"


# ---------------------------------------------------------------------------
# Streaming STT Engine
# ---------------------------------------------------------------------------

class StreamingSTT:
    """
    Real-time STT via Deepgram WebSocket with automatic fallback.

    Callers feed raw audio bytes (int16 PCM at 16kHz) into the provided
    asyncio.Queue and iterate over StreamingSTTResult objects.

    Sending None into the queue signals end-of-audio.
    """

    def __init__(self, config: Optional[StreamingSTTConfig] = None):
        self.config = config or StreamingSTTConfig()
        self._start_time: Optional[float] = None

    async def stream(
        self,
        audio_queue: "asyncio.Queue[Optional[bytes]]",
        *,
        is_voice: bool = True,
    ) -> AsyncIterator[StreamingSTTResult]:
        """
        Async generator that yields STT results from the audio stream.

        Args:
            audio_queue: Queue of raw PCM bytes (int16 @ 16kHz). Put None to end.
            is_voice:    True for Telegram voice notes (OGG/OPUS handled separately).

        Yields:
            StreamingSTTResult — interim (is_final=False) and final (is_final=True) results.
        """
        self._start_time = time.monotonic()

        # Collect all audio so fallback can use it
        buffered: List[bytes] = []

        if self.config.primary == StreamingProvider.DEEPGRAM:
            api_key = self._get_deepgram_key()
            if api_key:
                try:
                    async for result in self._stream_deepgram(audio_queue, api_key, buffered):
                        yield result
                    return  # success — no fallback needed
                except Exception as exc:
                    logger.warning(
                        "Deepgram streaming failed (%s) — falling back to %s",
                        exc, self.config.fallback.value,
                    )
                    # buffered was populated during the Deepgram attempt

        # Drain remaining queue into buffer
        while True:
            try:
                chunk = audio_queue.get_nowait()
                if chunk is None:
                    break
                buffered.append(chunk)
            except asyncio.QueueEmpty:
                break

        # Fallback: file-based STT on the buffered audio
        if buffered:
            result = await self._fallback(buffered, is_voice=is_voice)
            if result:
                yield result

    # ------------------------------------------------------------------ #
    # Deepgram WebSocket Streaming
    # ------------------------------------------------------------------ #

    async def _stream_deepgram(
        self,
        audio_queue: "asyncio.Queue[Optional[bytes]]",
        api_key: str,
        buffer_sink: List[bytes],
    ) -> AsyncIterator[StreamingSTTResult]:
        """
        Open a Deepgram WebSocket, stream audio, and yield results.

        The buffer_sink list is populated as chunks arrive, enabling
        fallback without re-reading the microphone.
        """
        try:
            import websockets
        except ImportError:
            raise RuntimeError(
                "websockets library required for streaming STT. "
                "Install with: pip install websockets"
            )

        params = {
            "model":         self.config.deepgram_model,
            "language":      self.config.language,
            "encoding":      "linear16",
            "sample_rate":   str(self.config.sample_rate),
            "channels":      str(self.config.channels),
            "interim_results": "true" if self.config.interim_results else "false",
            "utterance_end_ms": str(self.config.utterance_end_ms),
            "punctuate":     "true",
            "endpointing":   "500",
            "smart_format":  "true",
        }
        if self.config.detect_language:
            params.pop("language", None)
            params["detect_language"] = "true"

        # Build URL with query parameters
        from urllib.parse import urlencode
        ws_url = f"{self.config.deepgram_ws_url}?{urlencode(params)}"

        headers = {"Authorization": f"Token {api_key}"}
        results_queue: asyncio.Queue[Optional[StreamingSTTResult]] = asyncio.Queue()

        import json

        async def _receiver(ws) -> None:
            """Receive messages from Deepgram and push to results_queue."""
            try:
                async for raw in ws:
                    if isinstance(raw, bytes):
                        continue  # Deepgram sends text JSON
                    data = json.loads(raw)

                    # Handle UtteranceEnd event
                    if data.get("type") == "UtteranceEnd":
                        logger.debug("Deepgram UtteranceEnd received")
                        continue

                    # Handle transcript result
                    msg_type = data.get("type")
                    if msg_type != "Results":
                        continue

                    channel    = data.get("channel", {})
                    alts       = channel.get("alternatives", [{}])
                    alt        = alts[0] if alts else {}
                    transcript = alt.get("transcript", "")
                    confidence = alt.get("confidence", 0.0)
                    words      = alt.get("words", [])
                    is_final   = data.get("is_final", False)
                    lang       = channel.get("detected_language", self.config.language)

                    latency_ms: Optional[float] = None
                    if self._start_time is not None:
                        latency_ms = (time.monotonic() - self._start_time) * 1000

                    result = StreamingSTTResult(
                        transcript=transcript,
                        is_final=is_final,
                        confidence=confidence,
                        language=lang,
                        provider=StreamingProvider.DEEPGRAM,
                        latency_ms=latency_ms,
                        words=words,
                    )
                    await results_queue.put(result)

            except Exception as rx_exc:
                logger.error("Deepgram receiver error: %s", rx_exc)
            finally:
                await results_queue.put(None)  # sentinel

        async def _sender(ws) -> None:
            """Read from audio_queue and send PCM frames to Deepgram."""
            try:
                async with asyncio.timeout(self.config.session_timeout):
                    while True:
                        chunk = await audio_queue.get()
                        if chunk is None:  # end-of-audio
                            # Send Close Frame to signal end of audio
                            await ws.send(json.dumps({"type": "CloseStream"}))
                            break
                        buffer_sink.append(chunk)
                        await ws.send(chunk)
            except TimeoutError:
                logger.warning("Deepgram streaming session timeout hit")
                await ws.send(json.dumps({"type": "CloseStream"}))
            except Exception as tx_exc:
                logger.error("Deepgram sender error: %s", tx_exc)

        import websockets as _ws

        async with _ws.connect(ws_url, additional_headers=headers) as ws:
            # Run sender and receiver concurrently
            receiver_task = asyncio.create_task(_receiver(ws))
            sender_task   = asyncio.create_task(_sender(ws))

            try:
                while True:
                    result = await results_queue.get()
                    if result is None:
                        break
                    yield result
            finally:
                sender_task.cancel()
                receiver_task.cancel()
                await asyncio.gather(sender_task, receiver_task, return_exceptions=True)

    # ------------------------------------------------------------------ #
    # File-based Fallback
    # ------------------------------------------------------------------ #

    async def _fallback(
        self, chunks: List[bytes], *, is_voice: bool = True
    ) -> Optional[StreamingSTTResult]:
        """
        Convert buffered PCM chunks into a WAV file and transcribe with STT.

        Uses the existing navig.voice.stt.STT for provider dispatch and fallback.
        """
        from navig.voice.stt import STT, STTConfig, STTProvider

        logger.info("StreamingSTT fallback: transcribing %.1f KB via %s",
                    sum(len(c) for c in chunks) / 1024, self.config.fallback.value)

        # Build WAV bytes from raw PCM
        wav_bytes = self._chunks_to_wav(chunks)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(wav_bytes)
            tmp_path = Path(tmp.name)

        try:
            provider = {
                StreamingProvider.WHISPER_API:   STTProvider.WHISPER_API,
                StreamingProvider.WHISPER_LOCAL:  STTProvider.WHISPER_LOCAL,
                StreamingProvider.DEEPGRAM:       STTProvider.DEEPGRAM,
            }.get(self.config.fallback, STTProvider.WHISPER_API)

            config = STTConfig(
                provider=provider,
                whisper_model=self.config.whisper_model,
                whisper_local_model=self.config.whisper_local_model,
                language=self.config.language,
                detect_language=self.config.detect_language,
            )
            stt = STT(config=config)
            result = await stt.transcribe(tmp_path, is_voice=is_voice)

            if not result.success:
                logger.error("STT fallback failed: %s", result.error)
                return None

            latency_ms = (time.monotonic() - (self._start_time or time.monotonic())) * 1000
            return StreamingSTTResult(
                transcript=result.text or "",
                is_final=True,
                confidence=result.confidence or 0.0,
                language=result.language,
                provider=self.config.fallback,
                latency_ms=latency_ms,
            )
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

    def _chunks_to_wav(self, chunks: List[bytes]) -> bytes:
        """Pack raw PCM int16 chunks into an in-memory WAV file."""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(self.config.channels)
            wf.setsampwidth(self.config.bits_per_sample // 8)
            wf.setframerate(self.config.sample_rate)
            for chunk in chunks:
                wf.writeframes(chunk)
        return buf.getvalue()

    # ------------------------------------------------------------------ #
    # Vault key resolution
    # ------------------------------------------------------------------ #

    def _get_deepgram_key(self) -> Optional[str]:
        """Resolve Deepgram API key exclusively from VaultV2."""
        try:
            from navig.vault import get_vault_v2
            key = get_vault_v2().get_secret(self.config.deepgram_vault_label)
            if key:
                return key
        except KeyError:
            logger.warning(
                "Deepgram key not found in vault (label=%r). "
                "Add it with: navig vault put %s <your-key>",
                self.config.deepgram_vault_label,
                self.config.deepgram_vault_label,
            )
        except Exception as exc:
            logger.warning("Vault key resolution error: %s", exc)
        return None

    def _get_openai_key(self) -> Optional[str]:
        """Resolve OpenAI API key exclusively from VaultV2."""
        try:
            from navig.vault import get_vault_v2
            return get_vault_v2().get_secret(self.config.openai_vault_label)
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Convenience: transcribe a VoiceSession's buffered audio
# ---------------------------------------------------------------------------

async def transcribe_session_audio(
    session: Any,  # VoiceSession
    config: Optional[StreamingSTTConfig] = None,
) -> Optional[str]:
    """
    Transcribe audio chunks stored in a VoiceSession.

    Suitable as the STT callable for VoiceSessionManager:

        mgr = VoiceSessionManager(stt_fn=transcribe_session_audio)

    Returns the final transcript string or None on failure.
    """
    from navig.voice.session_manager import VoiceSession as _VS
    assert isinstance(session, _VS), "session must be a VoiceSession instance"

    if not session.audio_chunks:
        logger.warning("Session %s has no audio chunks to transcribe", session.id)
        return None

    stt = StreamingSTT(config=config)
    # Use fallback directly since audio is already buffered
    result = await stt._fallback(session.audio_chunks, is_voice=False)
    if result and result.transcript:
        return result.transcript
    return None


# ---------------------------------------------------------------------------
# Imports that are only needed at module load for type annotations
# ---------------------------------------------------------------------------
from typing import Any  # noqa: E402 (after Any usage in type hints above)

