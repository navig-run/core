"""
Phase 1 (OAuth token auto-refresh) + Phase 3 (generalized provider config
resolution) unit tests for the NativeDriver — no network.
"""

from __future__ import annotations

import json
import time

import pytest

from navig.providers import claude_oauth
from navig.providers.drivers.native import NativeDriver

# ── Phase 3: config resolution by provider_id ────────────────────────────────


def test_resolve_config_builtin_provider():
    d = NativeDriver(provider_id="openrouter")
    cfg = d._resolve_config(endpoint=None, model=None)
    assert cfg is not None and "openrouter" in cfg.base_url


def test_resolve_config_from_base_url_table():
    # deepseek has a base URL but no full BUILTIN_PROVIDERS entry → built OpenAI-compat
    d = NativeDriver(provider_id="deepseek")
    cfg = d._resolve_config(endpoint=None, model=None)
    assert cfg is not None and "deepseek" in cfg.base_url


def test_resolve_config_custom_endpoint_wins():
    d = NativeDriver(provider_id="openai")
    cfg = d._resolve_config(endpoint="https://my.host/v1", model=None)
    assert cfg.base_url == "https://my.host/v1"


# ── Phase 1: OAuth token auto-refresh ────────────────────────────────────────


def _driver_with_blob(blob: dict):
    store = {"ref": json.dumps(blob).encode()}

    def resolver(ref):
        return store[ref].decode()

    def writer(ref, payload):
        store[ref] = payload.encode() if isinstance(payload, str) else payload

    d = NativeDriver(secret_resolver=resolver, secret_writer=writer, oauth=True)
    return d, store


async def test_access_token_refreshes_within_buffer(monkeypatch):
    # token expires in 60s → inside the 5-min buffer → must refresh
    d, store = _driver_with_blob({
        "access_token": "old", "refresh_token": "rt", "expires_at": int(time.time()) + 60,
    })

    async def fake_refresh(refresh_token):
        assert refresh_token == "rt"
        return {"access_token": "new", "refresh_token": "rt2", "expires_at": int(time.time()) + 3600, "scopes": []}

    monkeypatch.setattr(claude_oauth, "refresh_tokens", fake_refresh)
    token = await d._oauth_access_token("ref")
    assert token == "new"
    # new bundle persisted
    assert json.loads(store["ref"].decode())["access_token"] == "new"


async def test_access_token_no_refresh_when_fresh(monkeypatch):
    d, _ = _driver_with_blob({
        "access_token": "fresh", "refresh_token": "rt", "expires_at": int(time.time()) + 3600,
    })

    async def boom(_):
        raise AssertionError("should not refresh a fresh token")

    monkeypatch.setattr(claude_oauth, "refresh_tokens", boom)
    assert await d._oauth_access_token("ref") == "fresh"


async def test_force_refresh_keeps_old_refresh_when_none_returned(monkeypatch):
    d, store = _driver_with_blob({
        "access_token": "a", "refresh_token": "rt", "expires_at": int(time.time()) + 3600,
    })

    async def fake_refresh(refresh_token):
        # craft contract: keep the old refresh token if none returned (refresh_tokens
        # already applies that; here it echoes the passed-in token)
        return {"access_token": "a2", "refresh_token": refresh_token, "expires_at": None, "scopes": []}

    monkeypatch.setattr(claude_oauth, "refresh_tokens", fake_refresh)
    token = await d._oauth_access_token("ref", force_refresh=True)
    assert token == "a2"
    assert json.loads(store["ref"].decode())["refresh_token"] == "rt"


async def test_raw_token_is_passed_through_unchanged():
    # a raw pasted token (not JSON) is used as-is, no refresh attempt
    def resolver(ref):
        return "sk-ant-raw-token"

    d = NativeDriver(secret_resolver=resolver, oauth=True)
    assert await d._oauth_access_token("ref") == "sk-ant-raw-token"
