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

from tests.fixtures.module_eviction import evicted_modules


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
    # Everything the simulated uninstall has to hide. `evicted_modules` restores the
    # originals AND drops whatever is imported inside, which a hand-rolled
    # `monkeypatch.delitem` loop cannot do — see its module docstring for the orphaned
    # module that reddened an unrelated file for four pre-push runs (#1109).
    with evicted_modules(monkeypatch, ("navig.voice", "navig_audio")):
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


# ── Hardening: the routes refuse websites and relayed callers ────────────────────────


@pytest.fixture(autouse=False)
def no_network_tts(monkeypatch):
    """Past the guard the handler would really synthesize; stub the engine so these
    tests never reach a speech provider over the network."""
    try:
        import navig.voice.tts as shim
    except ImportError:
        return  # no plugin → the handler answers 503 without touching the network

    class _Result:
        success = False
        audio_path = None
        provider = None
        error = "stubbed"

    class _Tts:
        config = types.SimpleNamespace(provider=types.SimpleNamespace(value="stub"))

        async def synthesize(self, text, **kw):  # noqa: ANN001, ANN003, ANN201
            return _Result()

    monkeypatch.setattr(shim, "get_tts", lambda: _Tts())


async def _post_synthesize(headers: dict, token: str | None = None):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from navig.gateway.routes import voice

    app = web.Application()
    gw = types.SimpleNamespace(config=types.SimpleNamespace(auth_token=token))
    voice.register(app, gw)
    async with TestClient(TestServer(app)) as client:
        # A text/plain body: the "simple" cross-origin request a page can send with no
        # preflight — exactly the shape a credit-burning website would use.
        resp = await client.post(
            "/api/voice/synthesize",
            data='{"text": "hello"}',
            headers={"Content-Type": "text/plain", **headers},
        )
        return resp.status, await resp.json()


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"X-Navig-Via": "lighthouse"},  # relayed in from outside this machine
        {"CF-Ray": "abc123"},  # arrived through a Cloudflare tunnel
    ],
)
async def test_synthesize_refuses_websites_and_relayed_callers(headers):
    pytest.importorskip("aiohttp")
    status, body = await _post_synthesize(headers)
    assert status == 403, body
    assert body.get("error_code") == "forbidden"


@pytest.mark.parametrize(
    "headers",
    [
        {},  # Echo's Rust bridge: loopback, no Origin
        {"Origin": "chrome-extension://abcdefghijklmnop"},
        {"Origin": "http://localhost:7432"},
    ],
)
async def test_synthesize_still_serves_this_machines_clients(headers, no_network_tts):
    pytest.importorskip("aiohttp")
    status, body = await _post_synthesize(headers)
    # Past the guard: whatever happens next (no plugin → 503, audio → 200, provider
    # failure → 500), it is never the 403 a website gets.
    assert status != 403, body


async def test_a_relayed_caller_with_the_gateway_token_is_served(no_network_tts):
    pytest.importorskip("aiohttp")
    status, body = await _post_synthesize(
        {"X-Navig-Via": "lighthouse", "Authorization": "Bearer tok-123456"}, token="tok-123456"
    )
    assert status != 403, body
    status, _ = await _post_synthesize(
        {"X-Navig-Via": "lighthouse", "Authorization": "Bearer wrong"}, token="tok-123456"
    )
    assert status == 403


# ── voice.policy decides here too: offline never reaches a cloud engine ───────


@pytest.fixture
def policy_engine(monkeypatch, tmp_path):
    """Record engine calls, isolate the packs root, and let each test set the policy.

    Without the plugin the fixture skips — the routing lives in navig-audio, and the
    plugin-less degrade path is pinned by the 503 tests above.
    """
    pytest.importorskip("aiohttp")
    try:
        import navig.voice.tts as shim
    except ImportError:
        pytest.skip("navig-audio (navig.voice.tts shim) not installed")

    from navig_audio.voice import packs as pk

    from navig.gateway.routes import voice as vroute

    monkeypatch.setattr(pk, "packs_root", lambda: tmp_path / "packs")
    calls: list[dict] = []
    state = {"policy": "auto"}

    class _Result:
        success = False
        audio_path = None
        provider = None
        error = "stubbed on purpose"
        format = "mp3"

    class _Tts:
        config = types.SimpleNamespace(provider=types.SimpleNamespace(value="stub"))

        async def synthesize(self, text, **kw):  # noqa: ANN001, ANN003, ANN201
            calls.append({"text": text, **kw})
            return _Result()

    monkeypatch.setattr(shim, "get_tts", lambda: _Tts())
    monkeypatch.setattr(vroute, "_voice_policy", lambda: state["policy"])
    return {"calls": calls, "state": state}


async def test_synthesize_under_offline_never_reaches_a_cloud_engine(policy_engine):
    """Regression: this endpoint ignored voice.policy, so offline text still went out
    through the default cloud engine. Now offline resolves only to LOCAL (or refuses)."""
    policy_engine["state"]["policy"] = "offline"
    status, body = await _post_synthesize({})
    assert status == 422, body
    assert "Offline" in str(body.get("error", ""))
    assert body.get("error_code") == "provider_unavailable"
    assert policy_engine["calls"] == [], "the engine ran while offline had no on-device voice"


async def test_synthesize_pins_the_routed_provider_into_the_engine(policy_engine):
    policy_engine["state"]["policy"] = "auto"
    status, body = await _post_synthesize({})
    # The stub answers "no audio" (500), but only AFTER the route chose the provider —
    # pinned into synthesize(), never the TTS default chain behind it.
    assert status == 500, body
    assert policy_engine["calls"], "the routed provider never reached the engine"
    assert policy_engine["calls"][0]["provider"].value == "edge"


def test_voice_policy_reads_live_not_the_lifetime_snapshot(tmp_path, monkeypatch):
    """The Echo synthesize route runs in the daemon: its policy gate must see a
    terminal's `navig voice policy offline` without a restart (get_live), or text
    keeps leaving the machine after the operator switched it off."""
    import navig.config as cfg
    from navig.gateway.routes.voice import _voice_policy

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "_config_manager_instance", None)
    monkeypatch.setattr(cfg, "_config_manager_config_dir", None)

    cfg.get_config_manager().set_global("voice.policy", "auto")
    assert _voice_policy() == "auto"

    cfg.ConfigManager(config_dir=tmp_path).set_global("voice.policy", "offline")
    assert _voice_policy() == "offline"
