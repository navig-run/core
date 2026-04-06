from __future__ import annotations

import sys
import types

from navig import llm_generate


class _FakeResult:
    def __init__(self, content: str):
        self.content = content
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0


class _FakeClient:
    def __init__(self, content: str):
        self._content = content
        self.closed = False

    async def complete(self, request):  # noqa: ARG002
        return _FakeResult(self._content)

    async def close(self):
        self.closed = True


class _FakeMessage:
    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content


class _FakeCompletionRequest:
    def __init__(self, **kwargs):  # noqa: ANN003
        self.kwargs = kwargs


class _FakeAuthProfileManager:
    def resolve_auth(self, provider: str):  # noqa: ARG002
        return "", "none"


def _install_provider_stubs(monkeypatch, fake_client: _FakeClient):
    providers_mod = types.ModuleType("navig.providers")
    providers_mod.CompletionRequest = _FakeCompletionRequest
    providers_mod.Message = _FakeMessage
    providers_mod.get_builtin_provider = lambda _provider: types.SimpleNamespace(
        name="stub", base_url="http://localhost", api="openai"
    )
    providers_mod.create_client = lambda *_args, **_kwargs: fake_client

    auth_mod = types.ModuleType("navig.providers.auth")
    auth_mod.AuthProfileManager = _FakeAuthProfileManager

    monkeypatch.setitem(sys.modules, "navig.providers", providers_mod)
    monkeypatch.setitem(sys.modules, "navig.providers.auth", auth_mod)


def test_call_via_providers_system_closes_client(monkeypatch):
    fake_client = _FakeClient("ok")
    _install_provider_stubs(monkeypatch, fake_client)

    result = llm_generate._call_via_providers_system(
        provider="openrouter",
        model="dummy",
        messages=[{"role": "user", "content": "hello"}],
        temperature=0.1,
        max_tokens=32,
        timeout=5,
        base_url=None,
    )

    assert result == "ok"
    assert fake_client.closed is True


def test_call_provider_rich_closes_client(monkeypatch):
    fake_client = _FakeClient("rich-ok")
    _install_provider_stubs(monkeypatch, fake_client)

    content, prompt_tokens, completion_tokens, extra = llm_generate._call_provider_rich(
        provider="openrouter",
        model="dummy",
        messages=[{"role": "user", "content": "hello"}],
        temperature=0.1,
        max_tokens=32,
        timeout=5,
        base_url=None,
    )

    assert content == "rich-ok"
    assert prompt_tokens == 0
    assert completion_tokens == 0
    assert extra == {"cache_read_tokens": 0, "cache_write_tokens": 0}
    assert fake_client.closed is True
