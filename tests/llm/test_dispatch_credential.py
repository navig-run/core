"""
Tests for the LLM dispatch credential seam (navig.llm.generate):
  * _resolve_dispatch_credential prefers a routable connection, falls back to
    the shared key store.
  * _call_direct_openai_compat NEVER degrades to a keyless request against a
    remote provider (the 410→keyless-401 trap), and refuses to route an OAuth
    subscription over the OpenAI-compatible fallback.
"""

from __future__ import annotations

import pytest

from navig.llm import generate as g


def test_resolve_dispatch_prefers_connection(monkeypatch):
    monkeypatch.setattr(
        "navig.providers.inference.credential_for_provider",
        lambda p: (None, "oauth-tok") if p == "anthropic" else None,
    )
    assert g._resolve_dispatch_credential("anthropic") == (None, "oauth-tok")


def test_resolve_dispatch_falls_back_to_authprofile(monkeypatch):
    monkeypatch.setattr(
        "navig.providers.inference.credential_for_provider", lambda p: None
    )

    class _AP:
        def resolve_auth(self, provider):
            return ("sk-shared", "vault:openai")

    monkeypatch.setattr("navig.providers.auth.AuthProfileManager", _AP)
    assert g._resolve_dispatch_credential("openai") == ("sk-shared", None)


def test_direct_compat_refuses_keyless_remote(monkeypatch):
    # No credential for a remote provider → raise, never send a keyless 401 request.
    monkeypatch.setattr(g, "_resolve_dispatch_credential", lambda p: (None, None))
    with pytest.raises(ValueError, match="keyless"):
        g._call_direct_openai_compat(
            "nvidia", "some-model", [{"role": "user", "content": "hi"}],
            0.2, 10, 10.0, None,
        )


def test_direct_compat_refuses_oauth_subscription(monkeypatch):
    # An OAuth subscription can't be presented over the OpenAI-compat fallback.
    monkeypatch.setattr(g, "_resolve_dispatch_credential", lambda p: (None, "tok"))
    with pytest.raises(ValueError, match="subscription"):
        g._call_direct_openai_compat(
            "anthropic", "claude-sonnet-4-6", [{"role": "user", "content": "hi"}],
            0.2, 10, 10.0, None,
        )


def test_parse_model_spec_routes_org_slash_to_openrouter():
    # Regression: 'org/model' ids must resolve to openrouter — the '/' check runs
    # BEFORE the bare-name substring heuristics (else meta-llama/… → ollama).
    from navig.llm.generate import _parse_model_spec

    assert _parse_model_spec("meta-llama/llama-3.3-70b-instruct")[0] == "openrouter"
    assert _parse_model_spec("qwen/qwen-2.5-72b-instruct")[0] == "openrouter"
    assert _parse_model_spec("deepseek/deepseek-chat")[0] == "openrouter"
    # bare names still infer their provider
    assert _parse_model_spec("llama-3.1-8b")[0] == "ollama"
    assert _parse_model_spec("deepseek-coder")[0] == "deepseek"
    assert _parse_model_spec("gpt-4o")[0] == "openai"
    assert _parse_model_spec("claude-opus-4-8")[0] == "anthropic"
    # explicit provider:model wins
    assert _parse_model_spec("anthropic:claude-opus-4-8") == ("anthropic", "claude-opus-4-8")


def test_with_base_url_does_not_mutate_singleton():
    # Regression: overriding base_url on a builtin must copy, never mutate the
    # shared BUILTIN_PROVIDERS singleton.
    from navig.llm.generate import _with_base_url
    from navig.providers.clients import get_builtin_provider

    oa = get_builtin_provider("openai")
    orig = oa.base_url
    copied = _with_base_url(oa, "https://proxy.example/v1")
    assert copied.base_url == "https://proxy.example/v1"
    assert get_builtin_provider("openai").base_url == orig  # untouched


def test_call_provider_does_not_openai_fallback_for_anthropic(monkeypatch):
    # Regression: anthropic (/. mcp_bridge) must NOT fall through to the OpenAI-shaped
    # httpx-direct fallback — that can only mask the real error with a 404.
    from navig.llm import generate as g

    def _boom(*a, **k):
        raise RuntimeError("boom from providers-system")

    called = {"direct": False}

    def _direct(*a, **k):
        called["direct"] = True
        return "should-not-happen"

    monkeypatch.setattr(g, "_resolve_dispatch_credential", lambda p: ("k", None))
    monkeypatch.setattr(g, "_call_via_providers_system", _boom)
    monkeypatch.setattr(g, "_call_direct_openai_compat", _direct)

    with pytest.raises(RuntimeError, match="boom"):
        g._call_provider("anthropic", "claude-opus-4-8",
                         [{"role": "user", "content": "hi"}], 0.2, 10, 30.0)
    assert called["direct"] is False  # never attempted the OpenAI-shaped fallback


def test_direct_compat_allows_local_keyless(monkeypatch):
    # Local providers legitimately need no key — must NOT raise the keyless guard.
    # (We stop before the network by making the credential lookup return nothing
    # and asserting the guard is skipped for a local provider.)
    monkeypatch.setattr(g, "_resolve_dispatch_credential", lambda p: (None, None))
    called = {}

    def _fake_post(url, json, headers, timeout):
        called["url"] = url

        class _R:
            status_code = 200
            text = ""

            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {"content": "ok"}}]}

        return _R()

    import httpx

    monkeypatch.setattr(httpx, "post", _fake_post)
    out = g._call_direct_openai_compat(
        "ollama", "qwen2.5:3b", [{"role": "user", "content": "hi"}],
        0.2, 10, 10.0, "http://127.0.0.1:11434/v1",
    )
    assert out == "ok"
    assert "called" and called.get("url")
