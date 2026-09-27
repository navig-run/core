"""A local small_talk model must survive the fast-chat override.

`resolve_llm` has an override that sends `small_talk` to whichever of xai/groq/cerebras
has a key, ignoring the configured mode. It exists for latency: a 5-word reply off a slow
remote free tier took ~17s. That reasoning does not apply to a model running on this
machine — and applying it there silently undoes a deliberate "keep it local" setting, which
is the one setting whose whole value is that it cannot be quietly ignored.

These tests pin both halves: the override still fires for a cloud mode (the feature is
intact), and never fires for a local one.
"""

from __future__ import annotations

import pytest

from navig.llm import router as R

pytestmark = pytest.mark.unit


class _Mode:
    """The two fields the override reads, plus the provider it must now look at."""

    def __init__(self, provider: str, model: str = "m"):
        self.provider = provider
        self.model = model
        self.temperature = 0.8
        self.max_tokens = 256


class _Modes:
    def __init__(self, mode: _Mode):
        self._mode = mode

    def get_mode(self, name: str):
        return self._mode if name == "small_talk" else None


class _Router:
    def __init__(self, mode: _Mode):
        self.modes = _Modes(mode)
        self.asked: list[str] = []

    def resolve_mode(self, mode: str) -> str:
        return mode

    def get_config(self, canonical: str, prefer_uncensored=None):
        self.asked.append(canonical)
        m = self.modes.get_mode(canonical)
        return R.ResolvedLLMConfig(
            provider=m.provider,
            model=m.model,
            temperature=m.temperature,
            max_tokens=m.max_tokens,
            mode=canonical,
            resolution_reason="configured mode",
        )


def _route(monkeypatch, provider: str, *, key_available: bool = True):
    router = _Router(_Mode(provider))
    monkeypatch.setattr(R, "get_llm_router", lambda: router)
    monkeypatch.setattr(R, "_has_api_key", lambda p: key_available)
    monkeypatch.setattr(R, "_get_env_var_name", lambda p: "FAKE_KEY")
    return R.resolve_llm(mode="small_talk"), router


def test_a_cloud_chat_mode_still_gets_the_fast_provider(monkeypatch):
    # The feature is not being removed — only bounded.
    cfg, _ = _route(monkeypatch, "nvidia")
    assert cfg.provider == "xai" and cfg.model == "grok-3-mini"
    assert "fast-chat override" in cfg.resolution_reason


def test_a_local_chat_mode_is_never_sent_to_a_cloud_provider(monkeypatch):
    # A key IS present for xai — before the fix, that alone was enough to leave the machine.
    cfg, router = _route(monkeypatch, "ollama")
    assert cfg.provider == "ollama"
    assert router.asked == ["small_talk"]  # it went through the normal path


@pytest.mark.parametrize("provider", ["ollama", "llamacpp", "airllm"])
def test_every_local_provider_is_exempt(monkeypatch, provider):
    cfg, _ = _route(monkeypatch, provider)
    assert cfg.provider == provider


def test_no_fast_key_means_the_configured_mode_wins_as_before(monkeypatch):
    cfg, _ = _route(monkeypatch, "nvidia", key_available=False)
    assert cfg.provider == "nvidia"


def test_local_is_defined_in_exactly_one_place():
    # A second copy of the local-provider set is how "local" drifts from what is enforced.
    from navig.llm.guard import LOCAL_PROVIDERS

    assert R._provider_is_local("OLLAMA") is True  # case-insensitive
    assert R._provider_is_local("anthropic") is False
    assert R._provider_is_local("") is False
    assert "ollama" in LOCAL_PROVIDERS


def test_the_vscode_bridge_is_not_treated_as_local_inference():
    """`mcp_bridge` talks to ws://127.0.0.1 — and that socket reaches GitHub Copilot.

    A local transport is not local inference. Counting it as local would let a caller
    that explicitly refused cloud hand its document to Copilot, which is the one thing
    the guard exists to prevent.
    """
    from navig.llm.guard import (
        KEYLESS_PROVIDERS,
        LOCAL_PROVIDERS,
        CloudRefused,
        ensure_allowed,
        parse_model,
    )

    assert "mcp_bridge" not in LOCAL_PROVIDERS
    assert "mcp_bridge" in KEYLESS_PROVIDERS  # it still needs no navig-held key
    assert LOCAL_PROVIDERS < KEYLESS_PROVIDERS

    bridge = parse_model("mcp_bridge:gpt-4o")
    assert bridge.is_local is False
    with pytest.raises(CloudRefused):
        ensure_allowed(bridge, allow_cloud=False)
    ensure_allowed(bridge, allow_cloud=True)  # explicit opt-in still works

    assert R._provider_is_local("mcp_bridge") is False
