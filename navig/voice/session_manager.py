"""
Voice Session Manager for NAVIG

Manages the complete lifecycle of a voice interaction session:
  IDLE → WAKE_DETECTED → LISTENING → PROCESSING → RESPONDING → IDLE

Design decisions:
- Each session runs as an independent asyncio.Task for clean cancellation.
- Silence timeout is measured from last audio chunk, not session start.
- EventBridge integration is optional to allow headless / test usage.
- Thread-safe: public methods are async, internal state guarded by asyncio.Lock.

Usage:
    from navig.voice.session_manager import VoiceSessionManager, SessionConfig

    mgr = VoiceSessionManager()
    await mgr.start()

    # Trigger from wake-word engine:
    session = await mgr.activate(keyword="echo", score=0.82)

    # Feed mic audio in real-time:
    await mgr.feed_audio(chunk)

    # Interrupt an active session:
    await mgr.interrupt()
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("navig.voice.session_manager")


# ---------------------------------------------------------------------------
# Session State Machine
# ---------------------------------------------------------------------------


class SessionState(str, Enum):
    """Voice session states.

    Transition diagram:
        IDLE ──activate()──► WAKE_DETECTED ──audio_ready()──► LISTENING
        LISTENING ──silence_timeout / stop()──► PROCESSING
        PROCESSING ──llm_done()──► RESPONDING
        RESPONDING ──playback_done()──► IDLE
        Any ──error / interrupt()──► IDLE (with error logged)
    """

    IDLE = "idle"
    WAKE_DETECTED = "wake_detected"
    LISTENING = "listening"
    PROCESSING = "processing"
    RESPONDING = "responding"
    ERROR = "error"


class SessionTransitionError(RuntimeError):
    """Raised when an illegal state transition is attempted."""

    pass


# ---------------------------------------------------------------------------
# Data Containers
# ---------------------------------------------------------------------------


@dataclass
class SessionTiming:
    """Latency breakdown for diagnostics and guardrails."""

    activated_at: float = field(default_factory=time.monotonic)
    listening_at: float | None = None
    processing_at: float | None = None
    responding_at: float | None = None
    completed_at: float | None = None

    @property
    def wake_to_listen_ms(self) -> float | None:
        if self.listening_at:
            return (self.listening_at - self.activated_at) * 1000
        return None

    @property
    def stt_latency_ms(self) -> float | None:
        if self.processing_at and self.listening_at:
            return (self.processing_at - self.listening_at) * 1000
        return None

    @property
    def total_ms(self) -> float | None:
        if self.completed_at:
            return (self.completed_at - self.activated_at) * 1000
        return None


@dataclass
class VoiceSession:
    """Immutable snapshot of a voice interaction session.

    Do not mutate directly — VoiceSessionManager manages transitions.
    """

    id: str
    state: SessionState
    keyword: str
    score: float
    timing: SessionTiming = field(default_factory=SessionTiming)

    # Content accumulated during the session
    audio_chunks: list[bytes] = field(default_factory=list)
    transcript: str | None = None
    response_text: str | None = None
    audio_path: str | None = None  # path to synthesised audio file

    # Error details (only populated in ERROR state)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state.value,
            "keyword": self.keyword,
            "score": self.score,
            "transcript": self.transcript,
            "response_text": self.response_text,
            "audio_path": self.audio_path,
            "error": self.error,
            "timing": {
                "wake_to_listen_ms": self.timing.wake_to_listen_ms,
                "stt_latency_ms": self.timing.stt_latency_ms,
                "total_ms": self.timing.total_ms,
            },
        }


# ---------------------------------------------------------------------------
# Callbacks / Injectable Dependencies
# ---------------------------------------------------------------------------

# Signature: (session) -> (transcript: str | None)
STTCallable = Callable[[VoiceSession], Coroutine[Any, Any, str | None]]

# Signature: (transcript: str) -> (response_text: str)
LLMCallable = Callable[[str], Coroutine[Any, Any, str]]

# Signature: (response_text: str) -> (audio_path: str | None)
TTSCallable = Callable[[str], Coroutine[Any, Any, str | None]]

# Signature: (session: VoiceSession) -> None
SessionCallback = Callable[[VoiceSession], Coroutine[Any, Any, None]]


# ---------------------------------------------------------------------------
# Session Manager Configuration
# ---------------------------------------------------------------------------


@dataclass
class SessionConfig:
    """Tunable parameters for VoiceSessionManager."""

    # After this many seconds of audio silence, end the listening phase.
    silence_timeout_seconds: float = 2.0

    # Hard cap on total listening time regardless of silence.
    max_listen_seconds: float = 30.0

    # Minimum audio length (ms) before STT is attempted (avoids empty transcripts).
    min_audio_ms: int = 300

    # Maximum concurrent active sessions (extra activations are dropped).
    max_concurrent_sessions: int = 1

    # Log timing breakdown at the end of every session.
    log_timing: bool = True

    # Optional: URL to POST wake-word events to navig-echo bridge.
    echo_bridge_url: str | None = None

    # Seconds to wait for echo bridge HTTP call.
    echo_bridge_timeout: float = 1.0


# ---------------------------------------------------------------------------
# Voice Session Manager
# ---------------------------------------------------------------------------


class VoiceSessionManager:
    """
    Manages concurrent voice interaction sessions.

    Inject STT, LLM, and TTS callables for full pipeline execution.
    Callbacks (on_state_change) allow EventBridge integration without
    creating a hard dependency.

    Example (minimal headless usage without audio processing):
        mgr = VoiceSessionManager(config=SessionConfig())
        await mgr.start()
        session = await mgr.activate(keyword="echo", score=0.9)
        await mgr.feed_audio(raw_bytes)
        await mgr.stop_listening()     # triggers STT + LLM + TTS

    Example (full pipeline):
        async def my_stt(sess): return await stt_engine.transcribe(...)
        async def my_llm(text): return await llm_client.complete(text)
        async def my_tts(text): return await tts_engine.speak(text)

        mgr = VoiceSessionManager(stt_fn=my_stt, llm_fn=my_llm, tts_fn=my_tts)
        await mgr.start()
    """

    def __init__(
        self,
        config: SessionConfig | None = None,
        *,
        stt_fn: STTCallable | None = None,
        llm_fn: LLMCallable | None = None,
        tts_fn: TTSCallable | None = None,
        on_state_change: SessionCallback | None = None,
        on_session_complete: SessionCallback | None = None,
        event_bridge: Any | None = None,  # navig.event_bridge.EventBridge
    ):
        self.config = config or SessionConfig()
        self._stt_fn = stt_fn
        self._llm_fn = llm_fn
        self._tts_fn = tts_fn
        self._on_state_change = on_state_change
        self._on_session_complete = on_session_complete
        self._event_bridge = event_bridge

        self._sessions: dict[str, VoiceSession] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._interrupt_events: dict[str, asyncio.Event] = {}
        self._audio_queues: dict[str, asyncio.Queue] = {}
        self._running = False

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        """Prepare the manager for accepting sessions."""
        self._running = True
        logger.info(
            "VoiceSessionManager started (max_concurrent=%d)",
            self.config.max_concurrent_sessions,
        )

    async def stop(self, timeout: float = 3.0) -> None:
        """Stop all active sessions and shut down."""
        self._running = False
        # Cancel and await all running session tasks
        tasks = list(self._tasks.values())
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._sessions.clear()
        self._tasks.clear()
        self._interrupt_events.clear()
        self._audio_queues.clear()
        logger.info("VoiceSessionManager stopped")

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def activate(self, keyword: str, score: float = 1.0) -> VoiceSession | None:
        """Signal a wake-word detection and start a new session.

        Returns the new session, or None if max_concurrent_sessions exceeded.
        """
        if not self._running:
            logger.warning("activate() called on stopped VoiceSessionManager")
            return None

        async with self._lock:
            active = sum(
                1
                for s in self._sessions.values()
                if s.state not in (SessionState.IDLE, SessionState.ERROR)
            )
            if active >= self.config.max_concurrent_sessions:
                logger.debug(
                    "Wake-word '%s' ignored — %d session(s) already active",
                    keyword,
                    active,
                )
                return None

            session_id = str(uuid.uuid4())[:8]
            session = VoiceSession(
                id=session_id,
                state=SessionState.WAKE_DETECTED,
                keyword=keyword,
                score=score,
            )
            self._sessions[session_id] = session
            self._interrupt_events[session_id] = asyncio.Event()
            self._audio_queues[session_id] = asyncio.Queue(maxsize=1024)

        logger.info("Session %s activated — keyword=%r score=%.2f", session_id, keyword, score)
        await self._emit_state_change(session)
        await self._notify_echo_bridge(session_id, keyword, score)

        # Spawn the pipeline task
        task = asyncio.create_task(
            self._run_session(session_id),
            name=f"voice-session-{session_id}",
        )
        self._tasks[session_id] = task
        task.add_done_callback(lambda t: self._cleanup_task(session_id, t))

        return session

    async def feed_audio(self, chunk: bytes, session_id: str | None = None) -> None:
        """Push a raw audio chunk into the active session's buffer.

        If session_id is None, targets the most-recently-activated session.
        Audio is silently dropped if no session is active.
        """
        sid = session_id or self._latest_active_id()
        if sid is None:
            return
        q = self._audio_queues.get(sid)
        if q is None:
            return
        try:
            q.put_nowait(chunk)
        except asyncio.QueueFull:
            logger.warning(
                "Session %s audio queue full — dropping chunk (%d bytes)",
                sid,
                len(chunk),
            )

    async def stop_listening(self, session_id: str | None = None) -> None:
        """Manually signal end-of-audio for the session (triggers STT)."""
        sid = session_id or self._latest_active_id()
        if sid is None:
            return
        q = self._audio_queues.get(sid)
        if q:
            # None sentinel signals end of audio stream
            await q.put(None)

    async def interrupt(self, session_id: str | None = None) -> None:
        """Interrupt and cancel the active session."""
        sid = session_id or self._latest_active_id()
        if sid is None:
            return
        event = self._interrupt_events.get(sid)
        if event:
            event.set()
        task = self._tasks.get(sid)
        if task and not task.done():
            task.cancel()
            logger.info("Session %s interrupted", sid)

    @property
    def active_session(self) -> VoiceSession | None:
        """Return the most recently activated non-idle session, or None."""
        sid = self._latest_active_id()
        return self._sessions.get(sid) if sid else None

    def get_session(self, session_id: str) -> VoiceSession | None:
        return self._sessions.get(session_id)

    # ------------------------------------------------------------------ #
    # Session Pipeline (internal)
    # ------------------------------------------------------------------ #

    async def _run_session(self, session_id: str) -> None:
        """Full pipeline coroutine for a single session."""
        session = self._sessions[session_id]
        interrupt = self._interrupt_events[session_id]
        audio_q = self._audio_queues[session_id]

        try:
            # ── 1. LISTENING ──────────────────────────────────────────
            await self._transition(session, SessionState.LISTENING)
            session.timing.listening_at = time.monotonic()

            audio_data = await self._collect_audio(session_id, audio_q, interrupt)
            if interrupt.is_set():
                return

            # ── 2. PROCESSING (STT → LLM) ─────────────────────────────
            await self._transition(session, SessionState.PROCESSING)
            session.timing.processing_at = time.monotonic()

            transcript = None
            if self._stt_fn and audio_data:
                try:
                    transcript = await asyncio.wait_for(self._stt_fn(session), timeout=30.0)
                    session.transcript = transcript
                except asyncio.TimeoutError:
                    logger.error("Session %s: STT timeout", session_id)
                except Exception as exc:
                    logger.error("Session %s: STT error: %s", session_id, exc)

            response_text: str | None = None
            if transcript and self._llm_fn:
                try:
                    response_text = await asyncio.wait_for(self._llm_fn(transcript), timeout=60.0)
                    session.response_text = response_text
                except asyncio.TimeoutError:
                    logger.error("Session %s: LLM timeout", session_id)
                except Exception as exc:
                    logger.error("Session %s: LLM error: %s", session_id, exc)

            if interrupt.is_set():
                return

            # ── 3. RESPONDING (TTS + playback) ────────────────────────
            await self._transition(session, SessionState.RESPONDING)
            session.timing.responding_at = time.monotonic()

            if response_text and self._tts_fn:
                try:
                    audio_path = await asyncio.wait_for(self._tts_fn(response_text), timeout=30.0)
                    session.audio_path = audio_path
                except asyncio.TimeoutError:
                    logger.error("Session %s: TTS timeout", session_id)
                except Exception as exc:
                    logger.error("Session %s: TTS error: %s", session_id, exc)

            # ── 4. IDLE ───────────────────────────────────────────────
            await self._transition(session, SessionState.IDLE)
            session.timing.completed_at = time.monotonic()

            if self.config.log_timing:
                logger.info(
                    "Session %s complete — wake_to_listen=%.0fms stt=%.0fms total=%.0fms transcript=%r",
                    session_id,
                    session.timing.wake_to_listen_ms or 0,
                    session.timing.stt_latency_ms or 0,
                    session.timing.total_ms or 0,
                    (session.transcript or "")[:80],
                )

            if self._on_session_complete:
                await self._on_session_complete(session)

        except asyncio.CancelledError:
            logger.info("Session %s cancelled", session_id)
            session.state = SessionState.IDLE
        except Exception as exc:
            logger.exception("Session %s unhandled error: %s", session_id, exc)
            session.state = SessionState.ERROR
            session.error = str(exc)
            await self._emit_event("voice.session.error", session)

    async def _collect_audio(
        self,
        session_id: str,
        audio_q: asyncio.Queue,
        interrupt: asyncio.Event,
    ) -> bytes:
        """
        Drain audio_q until:
          - None sentinel (manual stop)
          - Silence timeout (no new chunk for silence_timeout_seconds)
          - Max listen duration exceeded
          - Interrupt event is set
        Returns concatenated audio bytes.
        """
        chunks: list[bytes] = []
        deadline = time.monotonic() + self.config.max_listen_seconds

        while True:
            if interrupt.is_set():
                break

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.info("Session %s: max listen duration reached", session_id)
                break

            try:
                chunk = await asyncio.wait_for(
                    audio_q.get(),
                    timeout=min(self.config.silence_timeout_seconds, remaining),
                )
                if chunk is None:  # manual stop sentinel
                    logger.debug("Session %s: silence sentinel received", session_id)
                    break
                chunks.append(chunk)
                # Update session's audio_chunks (shared reference)
                session = self._sessions.get(session_id)
                if session:
                    session.audio_chunks.append(chunk)

            except asyncio.TimeoutError:
                # Silence timeout — enough audio collected
                total_ms = (len(b"".join(chunks))) / 16000 * 1000  # rough estimate at 16kHz
                if total_ms < self.config.min_audio_ms:
                    logger.debug(
                        "Session %s: silence timeout but audio too short (%.0fms), waiting",
                        session_id,
                        total_ms,
                    )
                    continue
                logger.debug(
                    "Session %s: silence timeout after %.0fms audio",
                    session_id,
                    total_ms,
                )
                break

        return b"".join(chunks)

    # ------------------------------------------------------------------ #
    # State transition helpers
    # ------------------------------------------------------------------ #

    async def _transition(self, session: VoiceSession, new_state: SessionState) -> None:
        session.state = new_state
        await self._emit_state_change(session)
        logger.debug("Session %s → %s", session.id, new_state.value)

    async def _emit_state_change(self, session: VoiceSession) -> None:
        if self._on_state_change:
            try:
                await self._on_state_change(session)
            except Exception as exc:
                logger.warning("on_state_change callback error: %s", exc)
        await self._emit_event(f"voice.session.{session.state.value}", session)

    async def _emit_event(self, topic: str, session: VoiceSession) -> None:
        if self._event_bridge is None:
            return
        try:
            await self._event_bridge.push_direct(
                topic=topic,
                source="voice.session_manager",
                data=session.to_dict(),
            )
        except Exception as exc:
            logger.debug("EventBridge push failed (%s): %s", topic, exc)

    # ------------------------------------------------------------------ #
    # Echo bridge notification (HTTP → navig-echo Tauri)
    # ------------------------------------------------------------------ #

    async def _notify_echo_bridge(self, session_id: str, keyword: str, score: float) -> None:
        """Notify navig-echo of wake-word detection via HTTP."""
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
                            "session_id": session_id,
                            "keyword": keyword,
                            "score": score,
                        },
                    ),
                    timeout=self.config.echo_bridge_timeout,
                )
        except Exception as exc:
            # Non-critical: echo may not be running in headless mode
            logger.debug("Echo bridge notify failed: %s", exc)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _latest_active_id(self) -> str | None:
        """Return the most-recently activated non-completed session id."""
        for sid, sess in reversed(list(self._sessions.items())):
            if sess.state not in (SessionState.IDLE, SessionState.ERROR):
                return sid
        return None

    def _cleanup_task(self, session_id: str, task: asyncio.Task) -> None:
        """Remove all per-session state after the session task completes."""
        self._tasks.pop(session_id, None)
        self._interrupt_events.pop(session_id, None)
        self._audio_queues.pop(session_id, None)
        self._sessions.pop(session_id, None)
        exc = task.exception() if not task.cancelled() else None
        if exc:
            logger.error("Session %s task raised: %s", session_id, exc)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_manager: VoiceSessionManager | None = None


def get_session_manager(config: SessionConfig | None = None, **kwargs) -> VoiceSessionManager:
    """Return (or create) the global VoiceSessionManager singleton."""
    global _manager
    if _manager is None:
        _manager = VoiceSessionManager(config=config, **kwargs)
    return _manager
