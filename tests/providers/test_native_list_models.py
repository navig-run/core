"""NativeDriver.list_models — URL shape, auth headers, pagination, fallbacks.

The model picker's live catalog: OpenAI-compatible ``{base}/models`` for API-key
providers, ``/v1/models`` for Anthropic (its builtin base carries no /v1), the
OAuth bearer for the Claude subscription, and ``[]`` on any failure so callers
keep the connection's stored list.
"""

from __future__ import annotations

import asyncio

import aiohttp
import pytest

from navig.providers.drivers.native import NativeDriver


class FakeResponse:
    def __init__(self, status: int, data: dict):
        self.status = status
        self._data = data

    async def json(self, content_type=None):
        return self._data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Stands in for aiohttp.ClientSession; records every GET."""

    pages: list[dict] = []
    status: int = 200
    calls: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, headers=None, params=None):
        FakeSession.calls.append({"url": url, "headers": dict(headers or {}), "params": dict(params or {})})
        page = FakeSession.pages[min(len(FakeSession.calls) - 1, len(FakeSession.pages) - 1)] if FakeSession.pages else {}
        return FakeResponse(FakeSession.status, page)


@pytest.fixture
def fake_http(monkeypatch):
    FakeSession.pages = []
    FakeSession.status = 200
    FakeSession.calls = []
    monkeypatch.setattr(aiohttp, "ClientSession", FakeSession)
    yield FakeSession


def test_openai_style_catalog_and_bearer(fake_http):
    fake_http.pages = [{"data": [{"id": "gpt-x"}, {"id": "gpt-y"}]}]
    drv = NativeDriver(secret_resolver=lambda ref: "sk-test", provider_id="openai")

    out = asyncio.run(drv.list_models(secret_ref="ref"))

    assert [m.id for m in out] == ["gpt-x", "gpt-y"]
    call = fake_http.calls[0]
    assert call["url"] == "https://api.openai.com/v1/models"
    assert call["headers"]["Authorization"] == "Bearer sk-test"
    assert "x-api-key" not in call["headers"]


def test_anthropic_base_gets_v1_and_version_header(fake_http):
    fake_http.pages = [{"data": [{"id": "claude-a"}]}]
    drv = NativeDriver(secret_resolver=lambda ref: "sk-ant", provider_id="anthropic")

    out = asyncio.run(drv.list_models(secret_ref="ref"))

    assert [m.id for m in out] == ["claude-a"]
    call = fake_http.calls[0]
    assert call["url"] == "https://api.anthropic.com/v1/models"
    assert call["headers"]["x-api-key"] == "sk-ant"
    assert call["headers"]["anthropic-version"] == "2023-06-01"


def test_oauth_presentation_uses_bearer_token(fake_http):
    fake_http.pages = [{"data": [{"id": "claude-opus-4-8"}]}]
    drv = NativeDriver(secret_resolver=lambda ref: "{}", provider_id="anthropic", oauth=True)
    drv._oauth_access_token = lambda ref, force_refresh=False: _async_value("oauth-token")

    out = asyncio.run(drv.list_models(secret_ref="ref"))

    assert [m.id for m in out] == ["claude-opus-4-8"]
    call = fake_http.calls[0]
    assert call["headers"]["Authorization"] == "Bearer oauth-token"
    assert "x-api-key" not in call["headers"]
    assert call["url"] == "https://api.anthropic.com/v1/models"


async def _async_value(v):
    return v


def test_anthropic_pagination_follows_has_more(fake_http):
    fake_http.pages = [
        {"data": [{"id": "m1"}], "has_more": True, "last_id": "m1"},
        {"data": [{"id": "m2"}], "has_more": False, "last_id": None},
    ]
    drv = NativeDriver(secret_resolver=lambda ref: "sk-ant", provider_id="anthropic")

    out = asyncio.run(drv.list_models(secret_ref="ref"))

    assert [m.id for m in out] == ["m1", "m2"]
    assert fake_http.calls[1]["params"].get("after_id") == "m1"


def test_http_error_returns_empty(fake_http):
    fake_http.pages = [{"data": [{"id": "ghost"}]}]
    fake_http.status = 401
    drv = NativeDriver(secret_resolver=lambda ref: "sk-test", provider_id="openai")

    assert asyncio.run(drv.list_models(secret_ref="ref")) == []


def test_no_key_and_no_oauth_still_returns_empty_on_failure(fake_http):
    # No credential → the request goes out unauthenticated → 401 → [].
    fake_http.status = 401
    drv = NativeDriver(secret_resolver=lambda ref: None, provider_id="openai")

    assert asyncio.run(drv.list_models(secret_ref="ref")) == []


def test_custom_endpoint_appends_models_path(fake_http):
    fake_http.pages = [{"data": [{"id": "local-model"}]}]
    drv = NativeDriver(secret_resolver=lambda ref: "k", provider_id=None)

    out = asyncio.run(drv.list_models(secret_ref="ref", endpoint="https://proxy.internal/v1"))
    assert [m.id for m in out] == ["local-model"]
    assert fake_http.calls[0]["url"] == "https://proxy.internal/v1/models"


def test_non_http_base_returns_empty_without_any_request(fake_http):
    drv = NativeDriver(secret_resolver=lambda ref: "k", provider_id="ollama")

    drv._provider_id = None
    out = asyncio.run(drv.list_models(secret_ref="ref", endpoint="local://airllm"))
    assert out == []
    assert fake_http.calls == []
