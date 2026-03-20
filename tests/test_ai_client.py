from __future__ import annotations

import types

import pytest

import navig.agent.ai_client as ai_client_module
from navig.agent.ai_client import AIClient


class _FakeResponse:
    def __init__(self, status: int, *, text: str = "", json_data: dict | None = None, headers: dict | None = None):
        self.status = status
        self._text = text
        self._json = json_data or {}
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return self._text

    async def json(self):
        return self._json


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]):
        self._responses = responses
        self.calls = 0

    def post(self, *args, **kwargs):
        response = self._responses[self.calls]
        self.calls += 1
        return response


def test_trim_messages_preserves_system_and_recent_history() -> None:
    client = AIClient(api_key="test", model="test", provider="openrouter")
    messages = [{"role": "system", "content": "rules"}] + [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
        for i in range(14)
    ]

    trimmed = client._trim_messages_for_retry(messages, keep_recent=4)

    assert trimmed[0] == {"role": "system", "content": "rules"}
    assert [m["content"] for m in trimmed[1:]] == ["m10", "m11", "m12", "m13"]


@pytest.mark.asyncio
async def test_chat_api_retries_once_on_rate_limit(monkeypatch) -> None:
    client = AIClient(api_key="test", model="test", provider="openrouter")
    fake_session = _FakeSession([
        _FakeResponse(429, text="rate limited", headers={"Retry-After": "1"}),
        _FakeResponse(200, json_data={"choices": [{"message": {"content": "ok"}}]}),
    ])

    async def fake_get_session():
        return fake_session

    sleep_calls: list[int] = []

    async def fake_sleep(seconds: int):
        sleep_calls.append(seconds)

    monkeypatch.setattr(client, "_get_session", fake_get_session)
    monkeypatch.setattr(ai_client_module, "aiohttp", types.SimpleNamespace(ClientTimeout=lambda **kwargs: kwargs))
    monkeypatch.setattr(ai_client_module.asyncio, "sleep", fake_sleep)

    result = await client._chat_api(
        [{"role": "user", "content": "hello"}],
        temperature=0.2,
        max_tokens=32,
    )

    assert result == "ok"
    assert fake_session.calls == 2
    assert sleep_calls == [1]