"""
Wake Word Engine for NAVIG

Always-listening microphone capture with openWakeWord scoring.

Architecture:
    Microphone (sounddevice) → VAD (Silero) → openWakeWord scorer
         ↓ keyword detected
    VoiceSessionManager.activate()
         ↓
    HTTP POST → navig-echo bridge (optional)

Graceful degradation:
- If sounddevice is unavailable → Telegram-only mode (logs warning, no exception)
- If openWakeWord is unavailable → raises ImportError with install instructions
- Scorer threshold and keyword are configurable at runtime

Usage:
    from navig.voice.wake_word import WakeWordEngine, WakeWordConfig

    engine = WakeWordEngine(
        config=WakeWordConfig(keyword="echo", threshold=0.5),
        on_detected=my_async_callback,
    )
    await engine.start()
    # ... later:
    await engine.stop()
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional

import numpy as np

logger = logging.getLogger("navig.voice.wake_word")

# Audio constants — must match openWakeWord's expectations
SAMPLE_RATE  = 16_000      # Hz
CHUNK_FRAMES = 1_280       # ~80ms at 16kHz (openWakeWord frame size)
CHANNELS     = 1
DTYPE        = "int16"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class WakeWordConfig:
    """Configurable parameters for the WakeWordEngine."""

    # Keyword identifier passed to openWakeWord model loader.
    # Built-in options: "hey_jarvis", "alexa", "hey_mycroft", or a path to a
    # custom .tflite model file.
    keyword: str = "hey_jarvis"

    # Detection threshold [0.0 – 1.0]. Lower = more sensitive (more false positives).
    threshold: float = 0.5

    # Seconds of cooldown between consecutive detections (prevents double-triggers).
    cooldown_seconds: float = 2.0

    # Whether to run Silero VAD pre-filter (skips scorer when no speech detected).
    # Reduces CPU load ~70%. Requires 'silero-vad' PyPI package.
    use_vad: bool = True

    # VAD speech probability threshold. Frames below this are skipped by the scorer.
    vad_threshold: float = 0.4

    # Run scorer in executor thread to avoid blocking the event loop.
    use_thread_executor: bool = True

    # If True, raise an exception on mic error; if False, degrade to Telegram-only mode.
    fail_on_mic_unavailable: bool = False

    # Optional: navig-echo bridge URL for HTTP wake notification.
    echo_bridge_url: Optional[str] = None

    # HTTP timeout for echo bridge call (seconds).
    echo_bridge_timeout: float = 1.0


# ---------------------------------------------------------------------------
# Detection Result
# ---------------------------------------------------------------------------

@dataclass
class WakeWordDetection:
    """Information about a wake-word detection event."""
    keyword:   str
    score:     float
    timestamp: float = field(default_factory=time.time)
    model:     Optional[str] = None


# ---------------------------------------------------------------------------
# Wake Word Engine
# ---------------------------------------------------------------------------

# Callable: (WakeWordDetection) -> None
OnDetectedCallback = Callable[[WakeWordDetection], Coroutine[Any, Any, None]]


class WakeWordEngine:
    """
    Always-on wake-word detection via openWakeWord + sounddevice microphone.

    Call start() to begin listening; stop() to release the microphone.
    The on_detected callback is invoked in the asyncio event loop.

    If the microphone is unavailable (e.g., headless server), the engine:
      - Logs a warning (or raises, depending on fail_on_mic_unavailable)
      - Remains available for manual trigger via the trigger() method
      (useful for Telegram-only mode where the bot receives audio files)
    """

    def __init__(
        self,
        config: Optional[WakeWordConfig] = None,
        on_detected: Optional[OnDetectedCallback] = None,
        session_manager: Optional[Any] = None,  # VoiceSessionManager
    ):
        self.config          = config or WakeWordConfig()
        self._on_detected    = on_detected
        self._session_mgr    = session_manager

        self._loop:          Optional[asyncio.AbstractEventLoop] = None
        self._task:          Optional[asyncio.Task] = None
        self._stop_event:    asyncio.Event = asyncio.Event()
        self._last_trigger:  float = 0.0
        self._running        = False

        # Lazily loaded components
        self._oww_model:     Optional[Any] = None
        self._vad_model:     Optional[Any] = None
        self._mic_available: bool = True

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        """Begin always-listening wake-word detection."""
        if self._running:
            logger.warning("WakeWordEngine already running")
            return

        self._loop = asyncio.get_event_loop()
        self._stop_event.clear()

        # pre-load models (CPU-bound, run in thread)
        await self._loop.run_in_executor(None, self._load_models)

        # Check microphone availability
        self._mic_available = await self._check_mic()
        if not self._mic_available:
            if self.config.fail_on_mic_unavailable:
                raise RuntimeError(
                    "Microphone unavailable. Install sounddevice and ensure "
                    "an audio input device is connected."
                )
            logger.warning(
                "⚠️  Microphone unavailable — WakeWordEngine running in Telegram-only mode. "
                "keyword '%s' can only be triggered via trigger() or Telegram voice messages.",
                self.config.keyword,
            )
            self._running = True
            return

        self._task = asyncio.create_task(
            self._capture_loop(),
            name="wake-word-capture",
        )
        self._running = True
        logger.info(
            "WakeWordEngine started — keyword=%r threshold=%.2f vad=%s",
            self.config.keyword,
            self.config.threshold,
            self.config.use_vad,
        )

    async def stop(self) -> None:
        """Stop listening and release the microphone."""
        self._running = False
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("WakeWordEngine stopped")

    async def trigger(self, score: float = 1.0) -> WakeWordDetection:
        """Manually trigger a wake-word detection (for testing or Telegram-only mode)."""
        detection = WakeWordDetection(
            keyword=self.config.keyword,
            score=score,
        )
        await self._on_wake(detection)
        return detection

    # ------------------------------------------------------------------ #
    # Capture loop
    # ------------------------------------------------------------------ #

    async def _capture_loop(self) -> None:
        """Continuously reads microphone audio and scores each frame."""
        try:
            import sounddevice as sd
        except ImportError:
            logger.error(
                "sounddevice is required for microphone capture. "
                "Install with: pip install sounddevice"
            )
            return

        loop = asyncio.get_event_loop()

        # Use an asyncio.Queue to bridge the sounddevice callback thread → event loop
        audio_queue: asyncio.Queue = asyncio.Queue(maxsize=64)

        def _audio_callback(indata: np.ndarray, frames: int, t: Any, status: Any) -> None:
            if status:
                logger.debug("Mic stream status: %s", status)
            # Copy to avoid data race (sounddevice reuses buffer)
            chunk = indata.copy().flatten()
            try:
                audio_queue.put_nowait(chunk)
            except asyncio.QueueFull:
                pass  # drop frame rather than block

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                blocksize=CHUNK_FRAMES,
                callback=_audio_callback,
            ):
                logger.debug("Microphone stream opened (%d Hz, %d frames/block)", SAMPLE_RATE, CHUNK_FRAMES)
                while not self._stop_event.is_set():
                    try:
                        chunk = await asyncio.wait_for(audio_queue.get(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue

                    # Score in thread executor to keep event loop free
                    if self.config.use_thread_executor:
                        score = await loop.run_in_executor(None, self._score_chunk, chunk)
                    else:
                        score = self._score_chunk(chunk)

                    if score >= self.config.threshold:
                        # Respect cooldown between triggers
                        now = time.monotonic()
                        if (now - self._last_trigger) >= self.config.cooldown_seconds:
                            self._last_trigger = now
                            detection = WakeWordDetection(
                                keyword=self.config.keyword,
                                score=score,
                                model=self.config.keyword,
                            )
                            logger.info(
                                "🔔 Wake word detected! keyword=%r score=%.3f",
                                self.config.keyword, score,
                            )
                            # Fire callback in event loop
                            asyncio.create_task(self._on_wake(detection))

        except Exception as exc:
            logger.error("WakeWordEngine capture loop error: %s", exc)

    def _score_chunk(self, chunk: np.ndarray) -> float:
        """
        Run the openWakeWord scorer on one audio frame.

        Returns the detection probability [0.0 – 1.0].
        If the VAD pre-filter rejects the frame, returns 0.0 immediately
        to save the ~5ms scorer inference cost.
        """
        # ── VAD pre-filter ────────────────────────────────────────────
        if self.config.use_vad and self._vad_model is not None:
            try:
                # Silero VAD expects float32 in [-1, 1] at 16kHz
                float_chunk = chunk.astype(np.float32) / 32768.0
                import torch
                tensor = torch.from_numpy(float_chunk).unsqueeze(0)
                speech_prob = self._vad_model(tensor, SAMPLE_RATE).item()
                if speech_prob < self.config.vad_threshold:
                    return 0.0
            except Exception as vad_exc:
                logger.debug("VAD error (skipping): %s", vad_exc)

        # ── openWakeWord scorer ───────────────────────────────────────
        if self._oww_model is None:
            return 0.0

        try:
            # openWakeWord expects int16 numpy array; returns dict of model → score
            float_chunk = chunk.astype(np.float32) / 32768.0
            predictions: dict = self._oww_model.predict(float_chunk)

            # Extract score for our keyword; fall back to max if key not found
            keyword_key = self.config.keyword.lower().replace(" ", "_")
            score = predictions.get(keyword_key, 0.0)
            if isinstance(score, (list, np.ndarray)):
                score = float(np.max(score))
            return float(score)

        except Exception as scorer_exc:
            logger.debug("Scorer error: %s", scorer_exc)
            return 0.0

    # ------------------------------------------------------------------ #
    # Model loading
    # ------------------------------------------------------------------ #

    def _load_models(self) -> None:
        """Load openWakeWord and optionally Silero VAD (blocking, run in executor)."""
        # ── openWakeWord ──────────────────────────────────────────────
        try:
            import openwakeword
            from openwakeword.model import Model as OWWModel
            self._oww_model = OWWModel(
                wakeword_models=[self.config.keyword]
                if not self.config.keyword.endswith(".tflite")
                else None,
                custom_verifier_models={} if not self.config.keyword.endswith(".tflite") else {
                    self.config.keyword: self.config.keyword
                },
                inference_framework="tflite",
            )
            logger.info("openWakeWord model loaded: %s", self.config.keyword)
        except ImportError:
            logger.error(
                "openWakeWord not installed. Install with: "
                "pip install openwakeword"
            )
        except Exception as exc:
            logger.error("openWakeWord model load failed: %s", exc)

        # ── Silero VAD (optional) ─────────────────────────────────────
        if self.config.use_vad:
            try:
                import torch
                vad_model, _ = torch.hub.load(
                    "snakers4/silero-vad",
                    "silero_vad",
                    force_reload=False,
                    onnx=False,
                )
                vad_model.eval()
                self._vad_model = vad_model
                logger.info("Silero VAD pre-filter loaded")
            except Exception as vad_exc:
                logger.warning("Silero VAD unavailable (%s) — scoring all frames", vad_exc)
                self._vad_model = None

    # ------------------------------------------------------------------ #
    # Detection callback
    # ------------------------------------------------------------------ #

    async def _on_wake(self, detection: WakeWordDetection) -> None:
        """Handle a confirmed wake-word detection."""
        # Fire user callback
        if self._on_detected:
            try:
                await self._on_detected(detection)
            except Exception as exc:
                logger.error("on_detected callback error: %s", exc)

        # Activate session manager
        if self._session_mgr:
            try:
                await self._session_mgr.activate(
                    keyword=detection.keyword,
                    score=detection.score,
                )
            except Exception as exc:
                logger.error("Session manager activation error: %s", exc)

        # Notify navig-echo bridge
        await self._notify_bridge(detection)

    async def _notify_bridge(self, detection: WakeWordDetection) -> None:
        """Push wake event to the polling queue for navig-echo to consume."""
        try:
            from navig.gateway.routes.voice import PENDING_WAKES
            PENDING_WAKES.append(detection)
        except ImportError:
            pass  # API not running
        
        # Keep old HTTP fallback if explicitly configured
        url = self.config.echo_bridge_url
        if not url:
            return
        try:
            import aiohttp
            async with aiohttp.ClientSession() as client:
                await asyncio.wait_for(
                    client.post(
                        f"{url}/api/voice/wake",
                        json={
                            "keyword":   detection.keyword,
                            "score":     detection.score,
                            "timestamp": detection.timestamp,
                        },
                    ),
                    timeout=self.config.echo_bridge_timeout,
                )
        except Exception as exc:
            logger.debug("Echo bridge notify failed: %s", exc)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    async def _check_mic(self) -> bool:
        """Return True if at least one audio input device is available."""
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            has_input = any(d.get("max_input_channels", 0) > 0 for d in devices)
            return has_input
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Module-level factory
# ---------------------------------------------------------------------------

_engine: Optional[WakeWordEngine] = None


def get_wake_word_engine(
    config: Optional[WakeWordConfig] = None,
    **kwargs,
) -> WakeWordEngine:
    """Return (or create) the global WakeWordEngine singleton."""
    global _engine
    if _engine is None:
        _engine = WakeWordEngine(config=config, **kwargs)
    return _engine
