"""Voice and Command API routes.

Routes: /api/voice/transcribe, /api/voice/synthesize, /api/voice/poll_wake, /api/command
"""
from __future__ import annotations

import base64
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

try:
    from aiohttp import web
except ImportError:
    pass

if TYPE_CHECKING:
    from aiohttp import web  # noqa: F811

    from navig.gateway.server import NavigGateway  # noqa: F401

from navig.debug_logger import get_debug_logger
from navig.gateway.routes.common import json_error_response, json_ok, require_bearer_auth
from navig.voice.stt import get_stt
from navig.voice.tts import get_tts
from navig.voice.wake_word import WakeWordDetection

logger = get_debug_logger()

# Global queue for wake word events detected by the backend engine (polled by rust bridge)
PENDING_WAKES: deque[WakeWordDetection] = deque(maxlen=10)

def register(app, gateway):
    app.router.add_post("/api/voice/transcribe", _transcribe(gateway))
    app.router.add_post("/api/voice/synthesize", _synthesize(gateway))
    app.router.add_post("/api/command", _command(gateway))
    app.router.add_get("/api/voice/poll_wake", _poll_wake(gateway))
    app.router.add_get("/api/voice/events", _events(gateway))



def _transcribe(gw):
    async def h(r: web.Request):
        # Allow anonymous local access; Rust bridge is expected to send the token.
        _auth = require_bearer_auth(r, gw, allow_anonymous=True)
        reader = await r.multipart()
        audio_bytes = None
        is_voice = False

        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == 'audio':
                audio_bytes = await part.read()
            elif part.name == 'is_voice':
                val = await part.read()
                is_voice = (val.decode('utf-8').strip().lower() == 'true')

        if not audio_bytes:
            return json_error_response("Missing audio part", status=400)

        # Write bytes to a temp file; STT.transcribe() requires a Path.
        tmp_path: Path | None = None
        try:
            suffix = ".oga" if is_voice else ".wav"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = Path(tmp.name)

            stt = get_stt()
            result = await stt.transcribe(tmp_path, is_voice=is_voice)
            if not result.success:
                return json_error_response(
                    "Transcription failed",
                    details={"error": result.error},
                    status=500,
                )
            return json_ok({
                "text": result.text or "",
                "confidence": result.confidence or 1.0,
                "provider": (
                    result.provider.value
                    if result.provider
                    else stt.config.provider.value
                ),
            })
        except Exception as e:
            logger.exception("Transcribe failed")
            return json_error_response(
                "Transcription failed", details={"error": str(e)}, status=500
            )
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    return h


def _synthesize(gw):
    async def h(r: web.Request):
        data = await r.json()
        text = data.get("text")
        if not text:
            return json_error_response("Missing 'text'", status=400)

        try:
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

            return json_ok({
                "audio_b64": b64,
                "provider": (
                    result.provider.value
                    if result.provider
                    else tts.config.provider.value
                ),
            })
        except Exception as e:
            logger.exception("Synthesize failed")
            return json_error_response("Synthesis failed", details={"error": str(e)}, status=500)

    return h


def _command(gw):
    async def h(r: web.Request):
        data = await r.json()
        text = data.get("text")
        if not text:
            return json_error_response("Missing 'text'", status=400)

        try:
            start_ts = time.monotonic()
            resp = await gw.router.route_message(
                channel="desktop_voice",
                user_id="local",
                message=text,
                metadata={"source": "voice_command"}
            )
            model = "unknown"
            if isinstance(resp, dict) and "model" in resp:
                model = resp["model"]
            return json_ok({
                "response": str(resp),
                "model": model,
                "latency_ms": (time.monotonic() - start_ts) * 1000
            })
        except Exception as e:
            logger.exception("Command failed")
            return json_error_response("Command failed", details={"error": str(e)}, status=500)

    return h


def _poll_wake(gw):
    async def h(r: web.Request):
        # Pop the oldest wake if any
        if PENDING_WAKES:
            wake = PENDING_WAKES.popleft()
            return json_ok({
                "keyword": wake.keyword,
                "score": wake.score,
                "timestamp": wake.timestamp
            })
        else:
            # 404 means no wake event pending this poll
            return web.json_response({"status": "no_event"}, status=404)

    return h


class _SSEClient:
    def __init__(self):
        import asyncio
        self.queue = asyncio.Queue()

    async def send(self, data: str) -> None:
        await self.queue.put(data)

def _events(gw):
    async def h(r: web.Request):
        import asyncio
        import json

        from aiohttp import web
        response = web.StreamResponse(
            status=200,
            reason='OK',
            headers={
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'Access-Control-Allow-Origin': '*',
            }
        )
        await response.prepare(r)

        client = _SSEClient()
        event_bridge = getattr(gw, 'event_bridge', None)

        if event_bridge:
            from navig.event_bridge import SubscriptionFilter
            filt = SubscriptionFilter(topics={'voice.session.*'})
            event_bridge.register_client(client, filt)
        else:
            logger.warning('EventBridge not available on gateway')

        try:
            while True:
                if event_bridge:
                    msg = await client.queue.get()
                    try:
                        parsed = json.loads(msg)
                        if 'params' in parsed and 'data' in parsed['params']:
                            data_block = parsed['params']['data']
                            sse_msg = f"data: {json.dumps(data_block)}\n\n"
                            await response.write(sse_msg.encode('utf-8'))
                    except Exception as e:
                        logger.debug('Failed to process SSE message: %s', e)
                else:
                    await asyncio.sleep(10)
                    await response.write(b": keepalive\n\n")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error('SSE connection error: %s', e)
        finally:
            if event_bridge:
                event_bridge.unregister_client(client)

        return response
    return h
