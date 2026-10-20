"""NAVIG Voice deck routes — the reader surface the Dock speaks through.

Pins the contract in `navig/gateway/deck/routes/voice.py`:

  * the catalog answers without a network call and tells paid from free;
  * `speak` returns audio bytes for one bounded chunk, never truncating silently;
  * Auto never routes to a paid provider, and a paid provider runs only when named;
  * a provider failure is a 502 with the reason, a missing plugin a 503.
"""

from __future__ import annotations

import pytest

pytest.importorskip("aiohttp")


@pytest.fixture
def engine(monkeypatch, tmp_path):
    """Every provider 'installed', no keys, and a fake synthesize that records calls."""
    from navig_audio.voice import catalog as cat
    from navig_audio.voice import packs as pk
    from navig_audio.voice.tts import TTS, TTSProvider, TTSResult

    keys: dict[str, str] = {}
    calls: list[dict] = []

    monkeypatch.setattr(cat, "_installed", lambda module: True)
    monkeypatch.setattr(TTS, "_resolve_api_key", staticmethod(lambda provider, *env: keys.get(provider)))
    # LOCAL's availability must come from THIS test's packs root, never the operator's.
    monkeypatch.setattr(pk, "packs_root", lambda: tmp_path / "packs")

    async def fake_synthesize(self, text, provider=None, voice=None, output_path=None, **kw):
        calls.append({"text": text, "provider": provider, "voice": voice, **kw,
                      "fallbacks": list(self.config.fallback_providers)})
        out = tmp_path / f"clip{len(calls)}.mp3"
        out.write_bytes(b"ID3fake-mp3")
        fmt = "wav" if provider == TTSProvider.LOCAL else "mp3"
        return TTSResult(success=True, audio_path=out, provider=provider, voice=voice, format=fmt)

    monkeypatch.setattr(TTS, "synthesize", fake_synthesize)
    # Nothing in the cache: every paid request bills (tests that need a hit set `hits`).
    hits: set[str] = set()
    monkeypatch.setattr(TTS, "cached_clip", lambda self, text, prov=None, v=None, **kw: (tmp_path / "hit.mp3") if text in hits else None)

    # Isolate the operator's state: voice policy, the usage ledger, the preset store.
    from navig_audio.voice import usage

    from navig.gateway.deck.routes import voice as route

    settings = {"policy": "auto", "daily_char_budget": 20000, "paid_fallback": False}
    monkeypatch.setattr(route, "voice_settings", lambda: dict(settings))
    monkeypatch.setattr(usage, "ledger_path", lambda: tmp_path / "voice" / "usage.json")
    profiles: dict[str, dict] = {}
    defaults: dict[str, str] = {}

    def seed_pack(name="en_US-ljspeech-medium"):
        # A minimal installed pack so offline can route to LOCAL inside a test.
        return _seed_pack(tmp_path, name)

    class _Preset:
        def __init__(self, pid, params, tool="speech"):
            self.id, self.name, self.params, self.tool = pid, pid, params, tool
            self.description, self.source = "", "user"

    from navig.presets import catalog as pcat

    monkeypatch.setattr(pcat, "get_preset", lambda pid: _Preset(pid, profiles[pid]) if pid in profiles else None)
    monkeypatch.setattr(pcat, "get_default", lambda tool: defaults.get(tool))
    monkeypatch.setattr(pcat, "list_presets", lambda tool=None, **kw: [_Preset(k, v) for k, v in profiles.items()])
    return {"keys": keys, "calls": calls, "settings": settings, "profiles": profiles,
            "defaults": defaults, "hits": hits, "usage": usage, "seed_pack": seed_pack}


def _seed_pack(tmp_path, name="en_US-ljspeech-medium"):
    """A minimal installed pack (manifest + files) under this test's packs root."""
    import hashlib
    import json

    from navig_audio.voice import packs as pk

    d = tmp_path / "packs" / name
    (d / "espeak-ng-data").mkdir(parents=True, exist_ok=True)
    (d / "espeak-ng-data" / "phontab").write_text("x", encoding="utf-8")
    (d / f"{name}.onnx").write_bytes(b"O")
    (d / "tokens.txt").write_text("a 1\n", encoding="utf-8")
    (d / "MODEL_CARD").write_text("c")
    spec = pk.get_pack(name)

    def entry(fname: str) -> dict:  # sizes from the real files — is_installed checks them
        p = d / fname
        return {"name": fname, "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "bytes": p.stat().st_size}

    (d / "manifest.json").write_text(json.dumps({
        "name": name, "lang": spec.lang, "voice_id": name, "engine": spec.engine,
        "url": spec.url, "sha256": spec.sha256, "bytes": spec.bytes,
        "license": spec.license, "license_url": spec.license_url, "source": spec.source,
        "sample_rate": spec.sample_rate,
        "files": [
            entry(f"{name}.onnx"), entry("tokens.txt"), entry("MODEL_CARD"),
            {"name": "espeak-ng-data", "kind": "tree", "files": 1, "bytes": 1, "sha256": "t"},
        ],
        "installed_at": "2026-10-18T00:00:00+00:00",
    }), encoding="utf-8")
    return d


async def _client():
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from navig.gateway.deck.routes import voice

    app = web.Application()
    voice.register(app)
    return TestClient(TestServer(app))


async def test_catalog_lists_free_first_and_flags_paid(engine):
    async with await _client() as c:
        resp = await c.get("/api/deck/voice/catalog")
        assert resp.status == 200
        data = (await resp.json())["data"]
    assert data["auto_order"] == ["edge"]
    first = data["providers"][0]
    assert first["id"] == "edge" and first["cost_class"] == "free" and first["available"]
    langs = {v["lang"] for v in first["voices"]}
    assert {"fr", "ru", "en"} <= langs
    eleven = next(p for p in data["providers"] if p["id"] == "elevenlabs")
    assert eleven["cost_class"] == "paid"
    assert eleven["available"] is False and "key" in eleven["unavailable_reason"]


async def test_speak_auto_returns_audio_with_a_language_voice(engine):
    async with await _client() as c:
        resp = await c.post("/api/deck/voice/speak", json={"text": "Привет, мир.", "lang": "ru"})
        assert resp.status == 200, await resp.text()
        assert resp.headers["Content-Type"].startswith("audio/mpeg")
        assert resp.headers["X-Navig-Voice-Provider"] == "edge"
        assert await resp.read() == b"ID3fake-mp3"
    call = engine["calls"][0]
    assert call["voice"] == "ru-RU-SvetlanaNeural"
    assert call["lang"] == "ru"
    # Auto must never carry a fallback chain: a paid engine is never a silent fallback.
    assert call["fallbacks"] == []


async def test_speak_refuses_more_than_one_chunk(engine):
    from navig.gateway.deck.routes.voice import MAX_CHARS

    async with await _client() as c:
        resp = await c.post("/api/deck/voice/speak", json={"text": "a" * (MAX_CHARS + 1)})
        assert resp.status == 413
        assert (await resp.json())["code"] == "too_long"
    assert engine["calls"] == []


async def test_paid_provider_runs_only_when_named_and_keyed(engine):
    async with await _client() as c:
        resp = await c.post("/api/deck/voice/speak", json={"text": "Bonjour.", "provider": "elevenlabs"})
        assert resp.status == 422
        assert "key" in (await resp.json())["error"]

        engine["keys"]["elevenlabs"] = "sk-test"
        # Keyed, but not named → Auto still picks the free engine.
        resp = await c.post("/api/deck/voice/speak", json={"text": "Bonjour.", "lang": "fr"})
        assert resp.headers["X-Navig-Voice-Provider"] == "edge"
        # Named → it runs.
        resp = await c.post("/api/deck/voice/speak", json={"text": "Bonjour.", "provider": "elevenlabs"})
        assert resp.status == 200
        assert resp.headers["X-Navig-Voice-Provider"] == "elevenlabs"


async def test_bad_input_is_a_400(engine):
    async with await _client() as c:
        assert (await c.post("/api/deck/voice/speak", data="not json")).status == 400
        assert (await c.post("/api/deck/voice/speak", json={"text": "  "})).status == 400
        assert (await c.post("/api/deck/voice/speak", json=["x"])).status == 400
        r = await c.post("/api/deck/voice/speak", json={"text": "Hi.", "voice": "not-a-voice"})
        assert r.status == 400 and (await r.json())["code"] == "bad_voice"
        r = await c.post("/api/deck/voice/speak", json={"text": "Hi.", "provider": "nope"})
        assert r.status == 422
    assert engine["calls"] == []


async def test_provider_failure_is_a_502_with_the_reason(engine, monkeypatch):
    from navig_audio.voice.tts import TTS, TTSResult

    async def failing(self, text, **kw):
        return TTSResult(success=False, error="Edge TTS error: 403 Forbidden")

    monkeypatch.setattr(TTS, "synthesize", failing)
    async with await _client() as c:
        resp = await c.post("/api/deck/voice/speak", json={"text": "Hello."})
        assert resp.status == 502
        body = await resp.json()
    assert body["code"] == "tts_error" and "403" in body["error"]


async def test_edge_missing_makes_auto_unavailable(engine, monkeypatch):
    from navig_audio.voice import catalog as cat

    monkeypatch.setattr(cat, "_installed", lambda module: module != "edge_tts")
    async with await _client() as c:
        resp = await c.post("/api/deck/voice/speak", json={"text": "Hello."})
        assert resp.status == 422
        assert "edge-tts" in (await resp.json())["error"]


def test_routes_are_mounted_by_the_deck():
    import inspect

    import navig.gateway.deck as deck

    src = inspect.getsource(deck)
    assert "routes import voice as _voice" in src and "_voice.register(app)" in src


def test_engine_default_voice_falls_back_to_multilingual():
    from navig_audio.voice.catalog import default_voice
    from navig_audio.voice.tts import TTSProvider

    assert default_voice(TTSProvider.EDGE, "fr") == "fr-FR-DeniseNeural"
    assert default_voice(TTSProvider.EDGE, None) is None
    assert default_voice(TTSProvider.ELEVENLABS, "ru") == "pMsXgVXv3BLzUgSXRplE"


# ── branch 2: profiles, policy, budget, ledger ─────────────────────────────────────────


async def test_a_profile_picks_provider_voice_and_pronunciation(engine):
    engine["profiles"]["speech/deep-ru"] = {
        "provider": "edge", "voice": "ru-RU-DmitryNeural", "lang": "ru", "speed": 0.9,
        "pitch_hz": -6, "pronounce": ["NAVIG=Навиг"],
    }
    async with await _client() as c:
        resp = await c.post("/api/deck/voice/speak", json={"text": "Это NAVIG.", "profile": "speech/deep-ru"})
        assert resp.status == 200, await resp.text()
        assert resp.headers["X-Navig-Voice-Rate"] == "0.9"
        assert resp.headers["X-Navig-Voice-Profile"] == "speech/deep-ru"
    call = engine["calls"][0]
    assert call["voice"] == "ru-RU-DmitryNeural" and call["lang"] == "ru"
    assert call["text"] == "Это Навиг."


async def test_profile_fields_its_provider_cannot_do_are_reported(engine):
    engine["profiles"]["speech/x"] = {"provider": "edge", "stability": 0.4, "style": "warm"}
    async with await _client() as c:
        resp = await c.post("/api/deck/voice/speak", json={"text": "Hello.", "profile": "speech/x"})
        assert resp.status == 200
        assert resp.headers["X-Navig-Voice-Ignored"] == "stability,style"


async def test_unknown_profile_is_a_404(engine):
    async with await _client() as c:
        resp = await c.post("/api/deck/voice/speak", json={"text": "Hi.", "profile": "speech/nope"})
        assert resp.status == 404 and (await resp.json())["code"] == "unknown_profile"


async def test_the_default_profile_applies_when_nothing_is_named(engine):
    engine["profiles"]["speech/fr"] = {"provider": "edge", "voice": "fr-FR-HenriNeural", "lang": "fr"}
    engine["defaults"]["speech"] = "speech/fr"
    async with await _client() as c:
        await c.post("/api/deck/voice/speak", json={"text": "Bonjour."})
        # an explicit voice wins over the default profile
        await c.post("/api/deck/voice/speak", json={"text": "Bonjour.", "voice": "fr-FR-DeniseNeural"})
    assert [c["voice"] for c in engine["calls"]] == ["fr-FR-HenriNeural", "fr-FR-DeniseNeural"]


async def test_free_policy_refuses_a_named_paid_provider(engine):
    engine["keys"]["elevenlabs"] = "k"
    engine["settings"]["policy"] = "free"
    async with await _client() as c:
        resp = await c.post("/api/deck/voice/speak", json={"text": "Hi.", "provider": "elevenlabs"})
        assert resp.status == 422 and "Free mode" in (await resp.json())["error"]
    assert engine["calls"] == []


async def test_offline_policy_never_sends_text_out(engine):
    engine["settings"]["policy"] = "offline"
    async with await _client() as c:
        resp = await c.post("/api/deck/voice/speak", json={"text": "Private note."})
        assert resp.status == 422 and "Offline" in (await resp.json())["error"]
    assert engine["calls"] == []


async def test_premium_uses_the_first_keyed_paid_voice_or_says_why_not(engine):
    engine["settings"]["policy"] = "premium"
    async with await _client() as c:
        resp = await c.post("/api/deck/voice/speak", json={"text": "Hello."})
        assert resp.headers["X-Navig-Voice-Provider"] == "edge"
        assert "no paid voice" in resp.headers["X-Navig-Voice-Note"]
        engine["keys"]["openai"] = "k"
        resp = await c.post("/api/deck/voice/speak", json={"text": "Hello."})
        assert resp.headers["X-Navig-Voice-Provider"] == "openai"


async def test_paid_speech_is_budgeted_recorded_and_free_on_a_cache_hit(engine):
    engine["keys"]["elevenlabs"] = "k"
    engine["settings"]["daily_char_budget"] = 30
    body = {"text": "a" * 20, "provider": "elevenlabs"}
    async with await _client() as c:
        assert (await c.post("/api/deck/voice/speak", json=body)).status == 200
        assert engine["usage"].spent_today() == 20
        resp = await c.post("/api/deck/voice/speak", json=body)  # 20 + 20 > 30
        assert resp.status == 429
        assert (await resp.json())["code"] == "budget_exceeded"
        engine["hits"].add("a" * 20)  # the clip is cached now: serving it costs nothing
        assert (await c.post("/api/deck/voice/speak", json=body)).status == 200
        usage = (await (await c.get("/api/deck/voice/usage")).json())["data"]
    assert usage["paid_chars_today"] == 20 and usage["remaining_today"] == 10
    assert usage["today"]["elevenlabs"]["requests"] == 1
    assert usage["today"]["elevenlabs"]["usd"] == pytest.approx(0.0016)
    assert len(engine["calls"]) == 2  # the budget refusal never reached the provider


async def test_budget_zero_turns_paid_speech_off(engine):
    engine["keys"]["elevenlabs"] = "k"
    engine["settings"]["daily_char_budget"] = 0
    async with await _client() as c:
        resp = await c.post("/api/deck/voice/speak", json={"text": "Hi.", "provider": "elevenlabs"})
        assert resp.status == 429 and "off" in (await resp.json())["error"]


async def test_paid_fallback_only_when_opted_in(engine, monkeypatch):
    from navig_audio.voice.tts import TTS, TTSProvider, TTSResult

    engine["keys"]["elevenlabs"] = "k"
    real = TTS.synthesize

    async def edge_down(self, text, provider=None, **kw):
        if provider == TTSProvider.EDGE:
            return TTSResult(success=False, error="Edge TTS error: 403")
        return await real(self, text, provider=provider, **kw)

    monkeypatch.setattr(TTS, "synthesize", edge_down)
    async with await _client() as c:
        resp = await c.post("/api/deck/voice/speak", json={"text": "Hello."})
        assert resp.status == 502  # off by default: a free failure is a failure
        engine["settings"]["paid_fallback"] = True
        resp = await c.post("/api/deck/voice/speak", json={"text": "Hello."})
        assert resp.status == 200
        assert resp.headers["X-Navig-Voice-Provider"] == "elevenlabs"
        assert "paid fallback" in resp.headers["X-Navig-Voice-Fallback"]
    assert engine["usage"].spent_today() == len("Hello.")


async def test_catalog_carries_profiles_settings_and_prices(engine):
    engine["profiles"]["speech/a"] = {"provider": "edge"}
    async with await _client() as c:
        data = (await (await c.get("/api/deck/voice/catalog")).json())["data"]
    assert data["settings"]["policy"] == "auto"
    assert [p["id"] for p in data["profiles"]] == ["speech/a"]
    eleven = next(p for p in data["providers"] if p["id"] == "elevenlabs")
    assert eleven["price"]["usd_per_1k_chars"] == 0.08


async def test_settings_are_validated_and_written(engine, monkeypatch):
    from navig.gateway.deck.routes import voice as route

    written: dict = {}

    class _CM:
        def set_global(self, k, v):
            written[k] = v

    import navig.config as cfg

    monkeypatch.setattr(cfg, "get_config_manager", lambda *a, **k: _CM())
    async with await _client() as c:
        assert (await c.post("/api/deck/voice/settings", json={"policy": "loud"})).status == 400
        assert (await c.post("/api/deck/voice/settings", json={"daily_char_budget": -1})).status == 400
        assert (await c.post("/api/deck/voice/settings", json={"paid_fallback": "yes"})).status == 400
        assert (await c.post("/api/deck/voice/settings", json={})).status == 400
        r = await c.post("/api/deck/voice/settings", json={"policy": "free", "daily_char_budget": 5000, "paid_fallback": True})
        assert r.status == 200
    assert written == {"voice.policy": "free", "voice.daily_char_budget": 5000, "voice.paid_fallback": True}
    assert route.voice_settings  # the response is the read-back (patched here)


# ── branch 3: dictation (POST /api/deck/voice/transcribe) ─────────────────────────────


@pytest.fixture
def stt_env(engine, monkeypatch, tmp_path):
    from navig_audio.voice import dictation as dc
    from navig_audio.voice.stt import STT, STTResult

    seen: list[dict] = []
    reply = {"text": "um so this is a test , uh , really", "language": "en", "success": True}

    async def fake_transcribe(self, audio_path, provider=None, language=None, **kw):
        from pathlib import Path

        seen.append({"path": Path(audio_path), "exists": Path(audio_path).exists(), "provider": provider,
                     "language": language, "model": self.config.whisper_local_model,
                     "fallbacks": list(self.config.fallback_providers)})
        return STTResult(success=reply["success"], text=reply["text"], provider=provider, language=reply["language"])

    monkeypatch.setattr(STT, "transcribe", fake_transcribe)
    monkeypatch.setattr(dc, "local_available", lambda: True)
    engine["settings"]["stt_model"] = "base"
    return {"seen": seen, "reply": reply, **engine}


def _form(audio=b"\x1aE\xdf\xa3webm", **fields):
    from aiohttp import FormData

    fd = FormData()
    if audio is not None:
        fd.add_field("audio", audio, filename="rec.webm", content_type="audio/webm")
    for k, v in fields.items():
        fd.add_field(k, v)
    return fd


async def test_dictation_verbatim_is_exactly_what_was_heard_on_device(stt_env):
    async with await _client() as c:
        resp = await c.post("/api/deck/voice/transcribe", data=_form(mode="verbatim", lang="en"))
        assert resp.status == 200, await resp.text()
        data = (await resp.json())["data"]
    assert data["text"] == "um so this is a test , uh , really" == data["raw"]
    assert data["provider"] == "whisper_local" and data["on_device"] is True
    call = stt_env["seen"][0]
    assert call["path"].suffix == ".webm" and not call["path"].exists()  # temp file removed
    assert call["fallbacks"] == [] and call["model"] == "base" and call["language"] == "en"


async def test_dictation_no_fillers_mode(stt_env):
    async with await _client() as c:
        data = (await (await c.post("/api/deck/voice/transcribe", data=_form(mode="no_fillers"))).json())["data"]
    assert data["text"] == "So this is a test, really"


async def test_dictation_llm_modes_use_the_llm_seam_and_fall_back_honestly(stt_env, monkeypatch):
    from navig.gateway.deck.routes import voice as route

    monkeypatch.setattr(route, "_llm_shape", lambda text, mode: f"- {mode}: test")
    async with await _client() as c:
        data = (await (await c.post("/api/deck/voice/transcribe", data=_form(mode="notes"))).json())["data"]
        assert data["text"] == "- notes: test" and data["transform_error"] == ""

        def down(text, mode):
            raise RuntimeError("no AI provider available")

        monkeypatch.setattr(route, "_llm_shape", down)
        data = (await (await c.post("/api/deck/voice/transcribe", data=_form(mode="prompt"))).json())["data"]
    assert data["text"] == "Um so this is a test, uh, really"  # the clean form, never lost
    assert "AI connection" in data["transform_error"]


async def test_dictation_policy_keeps_audio_on_device(stt_env):
    stt_env["keys"]["elevenlabs"] = "k"
    stt_env["settings"]["policy"] = "free"
    async with await _client() as c:
        resp = await c.post("/api/deck/voice/transcribe", data=_form(provider="elevenlabs"))
        assert resp.status == 422 and "Free mode" in (await resp.json())["error"]
    assert stt_env["seen"] == []


async def test_dictation_rejects_bad_input(stt_env, monkeypatch):
    from navig.gateway.deck.routes import voice as route

    async with await _client() as c:
        assert (await c.post("/api/deck/voice/transcribe", data=_form(audio=None))).status == 400
        assert (await c.post("/api/deck/voice/transcribe", data=_form(mode="poem"))).status == 400
        assert (await c.post("/api/deck/voice/transcribe", json={"audio": "x"})).status == 400
        monkeypatch.setattr(route, "MAX_AUDIO_BYTES", 4)
        assert (await c.post("/api/deck/voice/transcribe", data=_form())).status == 413
    assert stt_env["seen"] == []


async def test_dictation_silence_is_said_plainly(stt_env):
    stt_env["reply"].update(text="", success=False)
    async with await _client() as c:
        resp = await c.post("/api/deck/voice/transcribe", data=_form())
        assert resp.status == 422
        assert (await resp.json())["code"] == "no_speech"


async def test_settings_take_the_dictation_model(engine, monkeypatch):
    written: dict = {}

    class _CM:
        def set_global(self, k, v):
            written[k] = v

    import navig.config as cfg

    monkeypatch.setattr(cfg, "get_config_manager", lambda *a, **k: _CM())
    async with await _client() as c:
        assert (await c.post("/api/deck/voice/settings", json={"stt_model": "large"})).status == 400
        assert (await c.post("/api/deck/voice/settings", json={"stt_model": "small"})).status == 200
    assert written == {"voice.stt_model": "small"}


def test_dictation_prompts_ship_with_navig():
    from navig.prompts.loader import load_prompt

    for mode in ("notes", "prompt"):
        text = load_prompt(f"voice/dictation_{mode}")
        assert not text.startswith("Warning:") and "language" in text


async def test_warm_loads_the_dictation_model_in_the_background(stt_env, monkeypatch):
    import asyncio

    from navig_audio.voice import dictation as dc

    loaded: list[str] = []
    monkeypatch.setattr(dc, "warm", lambda model: loaded.append(model) or True)
    stt_env["settings"]["stt_model"] = "small"
    async with await _client() as c:
        resp = await c.post("/api/deck/voice/warm")
        assert resp.status == 202
        assert (await resp.json())["data"] == {"warming": True, "model": "small"}
        for _ in range(50):
            if loaded:
                break
            await asyncio.sleep(0.01)
    assert loaded == ["small"]


# ── offline policy x the on-device engine (LOCAL) ─────────────────────────────


async def test_catalog_tells_the_truth_about_the_on_device_engine(engine):
    async with await _client() as c:
        resp = await c.get("/api/deck/voice/catalog")
        data = (await resp.json())["data"]
    local = next(p for p in data["providers"] if p["id"] == "local")
    assert local["sends_text_external"] is False  # the privacy fact every policy filters on
    assert local["cost_class"] == "free"
    assert local["available"] is False
    assert "voice pack" in local["unavailable_reason"]  # the pack is the gap, not the extra
    assert local["sherpa_installed"] is True  # the fixture says the extra is present
    assert data["auto_order"] == ["edge"]  # Auto never gains the local engine


async def test_offline_with_a_pack_speaks_on_device_only(engine):
    engine["seed_pack"]()
    engine["settings"]["policy"] = "offline"
    async with await _client() as c:
        resp = await c.post("/api/deck/voice/speak", json={"text": "Secret note.", "lang": "en"})
        assert resp.status == 200, await resp.text()
        assert resp.headers["X-Navig-Voice-Provider"] == "local"
        assert resp.headers["X-Navig-Voice-Id"] == "en_US-ljspeech-medium"
        # the Content-Type must name the bytes: local returns WAV, not mp3
        assert resp.headers["Content-Type"].startswith("audio/wav")
        assert await resp.read() == b"ID3fake-mp3"
    assert [call["provider"].value for call in engine["calls"]] == ["local"]
    assert engine["calls"][0]["fallbacks"] == [], "no fallback chain behind a local speak"


async def test_offline_named_cloud_is_422_even_with_a_pack_installed(engine):
    engine["seed_pack"]()
    engine["settings"]["policy"] = "offline"
    async with await _client() as c:
        resp = await c.post("/api/deck/voice/speak", json={"text": "Hi.", "provider": "elevenlabs"})
        assert resp.status == 422
        body = await resp.json()
        assert body["code"] == "provider_unavailable" and "online service" in body["error"]
    assert engine["calls"] == []


async def test_offline_without_any_pack_is_a_422_that_names_the_gap(engine):
    engine["settings"]["policy"] = "offline"
    async with await _client() as c:
        resp = await c.post("/api/deck/voice/speak", json={"text": "Hi."})
        assert resp.status == 422
        body = await resp.json()
        assert "Offline" in body["error"] and "voice pack" in body["error"]
        assert "navig voice packs install" in body["error"]  # actionable, not a shrug
    assert engine["calls"] == []


async def test_auto_never_speaks_on_device_even_when_edge_is_unavailable(engine, monkeypatch):
    """Auto = Edge through the daemon. With no free cloud engine, auto REFUSES — it
    never implicitly picks LOCAL (owner decision: local is offline-policy or explicit)."""
    from navig_audio.voice import catalog as cat

    engine["seed_pack"]()  # LOCAL is fully available here
    monkeypatch.setattr(cat, "_installed", lambda module: module != "edge_tts")
    engine["settings"]["policy"] = "auto"
    async with await _client() as c:
        resp = await c.post("/api/deck/voice/speak", json={"text": "Hi."})
        assert resp.status == 422
        assert "no free speech engine" in (await resp.json())["error"]
    assert engine["calls"] == []


async def test_a_local_voice_that_is_not_installed_is_a_400(engine):
    engine["seed_pack"]()
    engine["settings"]["policy"] = "offline"
    async with await _client() as c:
        resp = await c.post(
            "/api/deck/voice/speak",
            json={"text": "Hi.", "provider": "local", "voice": "xx_NotInstalled-medium"},
        )
        assert resp.status == 400 and (await resp.json())["code"] == "bad_voice"
    assert engine["calls"] == []


def test_a_policy_change_reaches_the_daemon_without_a_restart(tmp_path, monkeypatch):
    """`navig voice policy offline` from a terminal must bite now.

    The daemon's config snapshot is process-lifetime; the deck voice settings have to
    re-stat the file (`get_live`) or the running daemon keeps sending text to a cloud
    engine after the operator turned that off — which is exactly what a live probe
    showed before this fix (offline in the file, Edge in the response, until restart).
    """
    import navig.config as cfg
    from navig.gateway.deck.routes.voice import voice_settings

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "_config_manager_instance", None)
    monkeypatch.setattr(cfg, "_config_manager_config_dir", None)

    daemon = cfg.get_config_manager()          # the long-lived process's view
    daemon.set_global("voice.policy", "auto")
    assert voice_settings()["policy"] == "auto"

    cli = cfg.ConfigManager(config_dir=tmp_path)  # a second process: `navig voice policy offline`
    cli.set_global("voice.policy", "offline")

    assert voice_settings()["policy"] == "offline"
    assert voice_settings()["daily_char_budget"] >= 0  # the same live read serves the rest


async def test_speak_detects_the_language_when_nobody_named_one(engine):
    """No lang, no profile: the words themselves pick the language.

    A raw API caller (or a client that forgot lang) used to get whichever engine
    default came first — a French line read with an English voice. Detection fills
    the gap for every engine: default voice, lexicon and on-device pack.
    """
    async with await _client() as c:
        resp = await c.post(
            "/api/deck/voice/speak",
            json={"text": "Bonjour, le café est prêt et la fenêtre est ouverte."},
        )
        assert resp.status == 200, await resp.text()
        assert resp.headers["X-Navig-Voice-Id"].startswith("fr-FR")
        await resp.read()
    call = engine["calls"][-1]
    assert call["lang"] == "fr"
    assert call["voice"].startswith("fr-FR")

    # An explicit lang still beats detection.
    async with await _client() as c:
        resp = await c.post("/api/deck/voice/speak", json={"text": "Bonjour.", "lang": "en"})
        assert resp.status == 200, await resp.text()
        await resp.read()
    assert engine["calls"][-1]["lang"] == "en"
    assert engine["calls"][-1]["voice"].startswith("en-US")

    # Text detection cannot honestly name keeps the old behaviour: no lang, engine default.
    async with await _client() as c:
        resp = await c.post("/api/deck/voice/speak", json={"text": "Hola mundo bienvenidos amigos."})
        assert resp.status == 200, await resp.text()
        await resp.read()
    assert engine["calls"][-1]["lang"] is None
