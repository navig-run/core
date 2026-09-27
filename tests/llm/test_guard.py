"""navig.llm.guard — the local-first model door.

Three properties this file exists to pin:
* an Ollama model TAG must not be mistaken for a provider (`ollama:qwen2.5:7b` — the
  colon after the version number);
* a caller's explicit model must bypass the mode router (which has a fast-chat bypass that
  sends `chat` to a cloud provider whenever such a key exists);
* a local provider whose server is down gets started, or produces an actionable error —
  never a bare connection failure mid-summary.
"""

from __future__ import annotations

import pytest

from navig.llm import guard

pytestmark = pytest.mark.unit


def test_parse_model_splits_on_the_first_colon_only():
    r = guard.parse_model("ollama:qwen2.5:7b-instruct")
    assert (r.provider, r.model) == ("ollama", "qwen2.5:7b-instruct")
    assert r.is_local and str(r) == "ollama:qwen2.5:7b-instruct"
    assert guard.parse_model("gpt-4o") == guard.Resolved("?", "gpt-4o")
    assert guard.parse_model("OpenAI:gpt-4o").provider == "openai"


def test_resolve_prefers_the_explicit_model_over_the_router(monkeypatch):
    def boom(**kwargs):  # the router must not even be consulted
        raise AssertionError("resolve_llm called despite an explicit model")

    monkeypatch.setattr("navig.llm.router.resolve_llm", boom)
    assert guard.resolve(mode="chat", model="ollama:qwen2.5:7b-instruct").is_local


def test_resolve_falls_back_to_the_router(monkeypatch):
    class R:
        provider = "XAI"
        model = "grok-3-mini"

    monkeypatch.setattr("navig.llm.router.resolve_llm", lambda **kw: R())
    r = guard.resolve(mode="chat")
    assert (r.provider, r.model) == ("xai", "grok-3-mini") and not r.is_local


def test_cloud_is_refused_unless_allowed():
    cloud = guard.Resolved("openai", "gpt-4o")
    with pytest.raises(guard.CloudRefused):
        guard.ensure_allowed(cloud, allow_cloud=False)
    guard.ensure_allowed(cloud, allow_cloud=True)
    guard.ensure_allowed(guard.Resolved("ollama", "x"), allow_cloud=False)


class TestLocalServer:
    def test_reachable_server_is_left_alone(self, monkeypatch):
        started: list[bool] = []
        monkeypatch.setattr(guard, "_ollama_reachable", lambda base, timeout=2.0: True)
        monkeypatch.setattr(guard, "_start_ollama", lambda: started.append(True) or True)
        guard.ensure_local_server(guard.Resolved("ollama", "m"))
        assert started == []

    def test_down_local_server_is_started_then_awaited(self, monkeypatch):
        states = iter([False, False, True])
        monkeypatch.setattr(
            guard, "_ollama_reachable", lambda base, timeout=2.0: next(states, True)
        )
        monkeypatch.setattr(guard, "_start_ollama", lambda: True)
        monkeypatch.setattr(guard.time, "sleep", lambda s: None)
        guard.ensure_local_server(guard.Resolved("ollama", "m"))  # no raise

    def test_missing_binary_says_so(self, monkeypatch):
        monkeypatch.setattr(guard, "_ollama_reachable", lambda base, timeout=2.0: False)
        monkeypatch.setattr(guard, "_start_ollama", lambda: False)
        with pytest.raises(guard.LocalServerUnavailable) as exc:
            guard.ensure_local_server(guard.Resolved("ollama", "m"))
        assert "ollama.com" in str(exc.value)

    def test_a_remote_ollama_is_never_started_here(self, monkeypatch):
        monkeypatch.setattr(guard, "_ollama_reachable", lambda base, timeout=2.0: False)
        monkeypatch.setattr(
            "navig.providers._local_defaults.ollama_base_url", lambda: "http://10.0.0.42:11434"
        )
        monkeypatch.setattr("navig.providers._local_defaults.ollama_is_local", lambda: False)
        monkeypatch.setattr(
            guard, "_start_ollama", lambda: pytest.fail("must not start a remote server")
        )
        with pytest.raises(guard.LocalServerUnavailable) as exc:
            guard.ensure_local_server(guard.Resolved("ollama", "m"))
        assert "10.0.0.42" in str(exc.value) and "ai.ollama_host" in str(exc.value)

    def test_non_ollama_local_providers_are_not_probed(self, monkeypatch):
        monkeypatch.setattr(
            guard, "_ollama_reachable", lambda base, timeout=2.0: pytest.fail("probed")
        )
        guard.ensure_local_server(guard.Resolved("llamacpp", "m"))


def test_generate_checks_flags_and_server_before_calling(monkeypatch):
    calls: dict = {}

    def fake_generate(**kw):
        calls["override"] = kw.get("model_override")
        calls["mode"] = kw.get("mode")
        return "  prose  "

    monkeypatch.setattr(
        guard, "ensure_local_server", lambda r, autostart=True: calls.setdefault("server", str(r))
    )
    monkeypatch.setattr("navig.llm.generate.llm_generate", fake_generate)
    text, resolved = guard.generate(
        [{"role": "user", "content": "x"}], mode="chat", model="ollama:qwen2.5:7b-instruct"
    )
    assert text == "prose" and str(resolved) == "ollama:qwen2.5:7b-instruct"
    assert calls["server"] == "ollama:qwen2.5:7b-instruct"
    assert calls["override"] == "ollama:qwen2.5:7b-instruct"

    # Cloud: refused before the server probe and before the call.
    monkeypatch.setattr("navig.llm.generate.llm_generate", lambda **kw: pytest.fail("called"))
    with pytest.raises(guard.CloudRefused):
        guard.generate([{"role": "user", "content": "x"}], model="openai:gpt-4o")
