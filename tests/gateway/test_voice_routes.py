"""The voice bridge's desktop surface is now mounted.

`routes/voice.py` defines STT/TTS/wake endpoints plus an `/api/command` agent endpoint,
but `register_all_routes` never mounted the module — so every call 404'd and Echo's voice
features silently never worked. These tests pin the decision made when mounting it:

  * the four STT/TTS/wake routes ARE mounted (low-sensitivity, anonymous-local by design);
  * `/api/command` is NOT — it invoked the agent with NO auth check, and the deck / Anchor /
    Echo all route commands through the authed `/llm/chat` now (that is the one command path);
  * a call that needs the navig-audio plugin degrades to an actionable 503, not a 500/crash.
"""

from __future__ import annotations

import sys
import types

import pytest


def _build_app():
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.routes import voice

    app = web.Application()
    voice.register(app, None)  # registration adds routes; it doesn't touch the gateway
    return app


def _paths(app) -> set[str]:
    return {r.resource.canonical for r in app.router.routes() if r.resource is not None}


def test_voice_routes_are_mounted():
    paths = _paths(_build_app())
    for p in ("/api/voice/transcribe", "/api/voice/synthesize", "/api/voice/poll_wake", "/api/voice/events"):
        assert p in paths, f"{p} should be mounted"


def test_command_endpoint_is_not_mounted():
    # `/api/command` was an UNAUTHENTICATED agent endpoint (route_message with no auth
    # check). The command path is the authed `/llm/chat`; this must stay unmounted.
    assert "/api/command" not in _paths(_build_app())


def test_register_all_routes_wires_voice():
    # The routes only mount if `voice` is in the register_all_routes module tuple.
    import inspect

    from navig.gateway.routes import register_all_routes

    src = inspect.getsource(register_all_routes)
    assert "voice" in src, "voice must be imported + iterated in register_all_routes"


async def test_transcribe_degrades_without_navig_audio(monkeypatch):
    """Without the navig-audio plugin, STT returns an actionable 503 — never a crash."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    # Simulate navig-audio uninstalled: the `navig.voice.stt` shim import raises.
    class _Blocker:
        def find_spec(self, name, path=None, target=None):  # noqa: ANN001, ANN201
            if name == "navig_audio" or name.startswith("navig_audio."):
                raise ImportError("navig-audio not installed (simulated)")
            return None

    monkeypatch.setattr(sys, "meta_path", [_Blocker(), *sys.meta_path])
    for mod in list(sys.modules):
        if mod == "navig.voice" or mod.startswith("navig.voice.") or mod.startswith("navig_audio"):
            monkeypatch.delitem(sys.modules, mod, raising=False)

    from aiohttp import web

    from navig.gateway.routes import voice

    app = web.Application()
    gw = types.SimpleNamespace(config=types.SimpleNamespace(auth_token=None))
    voice.register(app, gw)

    async with TestClient(TestServer(app)) as client:
        form = {"audio": b"\x00\x01", "is_voice": "false"}
        resp = await client.post("/api/voice/transcribe", data=form)
        assert resp.status == 503
        body = await resp.json()
        assert body.get("error_code") == "plugin_required"
        assert "navig-audio" in str(body).lower()
