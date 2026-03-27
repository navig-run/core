"""Voice and Command API routes.

Routes: /api/voice/transcribe, /api/voice/synthesize, /api/voice/poll_wake, /api/command

Design note:
    ``PENDING_WAKES`` lives at module level and has **no** aiohttp dependency so
    that ``wake_word.py`` can do ``from navig.gateway.routes.voice import PENDING_WAKES``
    without aiohttp being installed.  All aiohttp-requiring helpers are imported
    lazily inside each handler closure, and any ``RuntimeError`` for missing
    aiohttp is raised only when a handler is actually invoked.
"""

from __future__ import annotations

import base64
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import web

    from navig.gateway.server import NavigGateway  # noqa: F401

from navig.debug_logger import get_debug_logger
from navig.voice.wake_word import WakeWordDetection

logger = get_debug_logger()

# ---------------------------------------------------------------------------
# Global queue — importable WITHOUT aiohttp (used by wake_word._notify_bridge)
# ---------------------------------------------------------------------------
PENDING_WAKES: deque[WakeWordDetection] = deque(maxlen=10)


# ---------------------------------------------------------------------------
# Lazy helpers — resolved once on first handler invocation
# ---------------------------------------------------------------------------


def _route_helpers():
    """Return (json_ok, json_error_response, require_bearer_auth) lazily."""
    from navig.gateway.routes.common import (  # noqa: PLC0415
        json_error_response,
        json_ok,
        require_bearer_auth,
    )

    return json_ok, json_error_response, require_bearer_auth


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def register(app, gateway):
    app.router.add_post("/api/voice/transcribe", _transcribe(gateway))
    app.router.add_post("/api/voice/synthesize", _synthesize(gateway))
    app.router.add_post("/api/command", _command(gateway))
    app.router.add_get("/api/voice/poll_wake", _poll_wake(gateway))
    app.router.add_get("/api/voice/events", _events(gateway))


def _transcribe(gw):
    async def h(r):  # type: aiohttp.web.Request  # noqa: ANN001
        # Allow anonymous local access; Rust bridge is expected to send the token.
        _, _, require_bearer_auth = _route_helpers()
        _auth = require_bearer_auth(r, gw, allow_anonymous=True)
        reader = await r.multipart()
        audio_bytes = None
        is_voice = False

        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "audio":
                audio_bytes = await part.read()
            elif part.name == "is_voice":
                val = await part.read()
                is_voice = val.decode("utf-8").strip().lower() == "true"

        if not audio_bytes:
            _, json_error_response, _ = _route_helpers()
            return json_error_response("Missing audio part", status=400)

        # Write bytes to a temp file; STT.transcribe() requires a Path.
        tmp_path: Path | None = None
        try:
            suffix = ".oga" if is_voice else ".wav"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = Path(tmp.name)

            from navig.voice.stt import get_stt  # noqa: PLC0415

            stt = get_stt()
            json_ok, json_error_response, _ = _route_helpers()
            result = await stt.transcribe(tmp_path, is_voice=is_voice)
            if not result.success:
                return json_error_response(
                    "Transcription failed",
                    details={"error": result.error},
                    status=500,
                )
            return json_ok(
                {
                    "text": result.text or "",
                    "confidence": result.confidence or 1.0,
                    "provider": (
                        result.provider.value
                        if result.provider
                        else stt.config.provider.value
                    ),
                }
            )
        except Exception as e:
            logger.exception("Transcribe failed")
            _, json_error_response, _ = _route_helpers()
            return json_error_response(
                "Transcription failed", details={"error": str(e)}, status=500
            )
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass  # best-effort cleanup

    return h


def _synthesize(gw):
    async def h(r):  # type: aiohttp.web.Request
        data = await r.json()
        text = data.get("text")
        if not text:
            _, json_error_response, _ = _route_helpers()
            return json_error_response("Missing 'text'", status=400)

        try:
            from navig.voice.tts import get_tts  # noqa: PLC0415

            json_ok, json_error_response, _ = _route_helpers()
            tts = get_tts()
            result = await tts.synthesize(text)
            if not result.success or not result.audio_path:
                return json_error_response(
                    "TTS returned no audio",
                    details={"error": result.error},
                    status=500,
                )

            with open(result.audio_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")

            return json_ok(
                {
                    "audio_b64": b64,
                    "provider": (
                        result.provider.value
                        if result.provider
                        else tts.config.provider.value
                    ),
                }
            )
        except Exception as e:
            logger.exception("Synthesize failed")
            _, json_error_response, _ = _route_helpers()
            return json_error_response(
                "Synthesis failed", details={"error": str(e)}, status=500
            )

    return h


def _command(gw):
    async def h(r):  # type: aiohttp.web.Request
        data = await r.json()
        text = data.get("text")
        if not text:
            _, json_error_response, _ = _route_helpers()
            return json_error_response("Missing 'text'", status=400)

        try:
            json_ok, json_error_response, _ = _route_helpers()
            start_ts = time.monotonic()
            resp = await gw.router.route_message(
                channel="desktop_voice",
                user_id="local",
                message=text,
                metadata={"source": "voice_command"},
            )
            model = "unknown"
            if isinstance(resp, dict) and "model" in resp:
                model = resp["model"]
            return json_ok(
                {
                    "response": str(resp),
                    "model": model,
                    "latency_ms": (time.monotonic() - start_ts) * 1000,
                }
            )
        except Exception as e:
            logger.exception("Command failed")
            _, json_error_response, _ = _route_helpers()
            return json_error_response(
                "Command failed", details={"error": str(e)}, status=500
            )

    return h


def _poll_wake(gw):
    async def h(r):  # type: aiohttp.web.Request
        # Pop the oldest wake if any
        if PENDING_WAKES:
            wake = PENDING_WAKES.popleft()
            return json_ok(
                {
                    "keyword": wake.keyword,
                    "score": wake.score,
                    "timestamp": wake.timestamp,
                }
            )
        else:
            # 404 means no wake event pending this poll
            return _get_web().json_response({"status": "no_event"}, status=404)

    return h


class _SSEClient:
    def __init__(self):
        import asyncio

        self.queue = asyncio.Queue()

    async def send(self, data: str) -> None:
        await self.queue.put(data)


def _events(gw):
    async def h(r):  # type: aiohttp.web.Request
        import asyncio
        import json

        _web = _get_web()
        response = _web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )
        await response.prepare(r)

        client = _SSEClient()
        event_bridge = getattr(gw, "event_bridge", None)

        if event_bridge:
            from navig.event_bridge import SubscriptionFilter

            filt = SubscriptionFilter(topics={"voice.session.*"})
            event_bridge.register_client(client, filt)
        else:
            logger.warning("EventBridge not available on gateway")

        try:
            while True:
                if event_bridge:
                    msg = await client.queue.get()
                    try:
                        parsed = json.loads(msg)
                        if "params" in parsed and "data" in parsed["params"]:
                            data_block = parsed["params"]["data"]
                            sse_msg = f"data: {json.dumps(data_block)}\n\n"
                            await response.write(sse_msg.encode("utf-8"))
                    except Exception as e:
                        logger.debug("Failed to process SSE message: %s", e)
                else:
                    await asyncio.sleep(10)
                    await response.write(b": keepalive\n\n")

        except asyncio.CancelledError:
            pass  # task cancelled; expected during shutdown
        except Exception as e:
            logger.error("SSE connection error: %s", e)
        finally:
            if event_bridge:
                event_bridge.unregister_client(client)

        return response

    return h
