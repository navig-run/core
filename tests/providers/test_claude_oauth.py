"""
Claude Pro/Max OAuth token-minting flow — PKCE/url/exchange (pure + mocked) and
the begin→complete orchestration that creates a routable claude-max connection.
No network: the token endpoint and the validate call are monkeypatched.
"""

from __future__ import annotations

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlparse

import pytest

from navig.providers import claude_oauth
from navig.providers import connect as connect_mod
from navig.providers.connect import begin_oauth, complete_oauth
from navig.providers.connection_types import AuthState, ConnectionValidationError
from navig.providers.connections import ConnectionStore
from navig.providers.drivers.base import ValidationResult


class FakeVault:
    def __init__(self):
        self.items = {}

    def put(self, label, payload, *, provider=None, **kw):
        self.items[label] = payload
        return "id-" + label

    def get_secret(self, label):
        return self.items[label].decode()

    def delete(self, label):
        return self.items.pop(label, None) is not None


@pytest.fixture
def store(tmp_path):
    return ConnectionStore(tmp_path / "connections.db", vault=FakeVault())


# ── pure PKCE / URL ──────────────────────────────────────────────────────────


def test_pkce_challenge_is_s256_of_verifier():
    verifier, challenge = claude_oauth.generate_pkce()
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    assert challenge == expected
    assert "=" not in verifier and "=" not in challenge  # base64url, unpadded


def test_authorize_url_has_required_params():
    url, flow = claude_oauth.build_authorize_url()
    q = parse_qs(urlparse(url).query)
    assert url.startswith("https://claude.ai/oauth/authorize?")
    assert q["client_id"][0] == claude_oauth.CLAUDE_CLIENT_ID
    assert q["response_type"][0] == "code"
    assert q["code_challenge_method"][0] == "S256"
    assert q["redirect_uri"][0] == claude_oauth.CLAUDE_REDIRECT_URI
    assert q["scope"][0] == claude_oauth.CLAUDE_OAUTH_SCOPES
    assert q["state"][0] == flow.state


def test_clean_code_strips_state_fragment():
    assert claude_oauth._clean_code("THECODE#somestate") == "THECODE"
    assert claude_oauth._clean_code("THECODE&x=1") == "THECODE"
    assert claude_oauth._clean_code("  THECODE  ") == "THECODE"


async def test_exchange_code_parses_tokens(monkeypatch):
    async def fake_post(body):
        assert body["grant_type"] == "authorization_code"
        assert body["code"] == "abc"  # cleaned
        return {"access_token": "at", "refresh_token": "rt", "expires_in": 3600, "scope": "a b"}

    monkeypatch.setattr(claude_oauth, "_post_token", fake_post)
    _, flow = claude_oauth.build_authorize_url()
    # Code carries the flow's real state (CSRF check must pass); _clean_code strips it.
    tokens = await claude_oauth.exchange_code(f"abc#{flow.state}", flow)
    assert tokens["access_token"] == "at" and tokens["refresh_token"] == "rt"
    assert tokens["expires_at"] and tokens["scopes"] == ["a", "b"]


async def test_exchange_expired_flow_raises(monkeypatch):
    _, flow = claude_oauth.build_authorize_url()
    flow.created_at -= 10_000  # force expiry
    with pytest.raises(RuntimeError):
        await claude_oauth.exchange_code("abc", flow)


# ── begin / complete orchestration ───────────────────────────────────────────


def test_begin_oauth_returns_handle_and_url(store):
    flow = begin_oauth("claude-max", store=store)
    assert flow["handle"] and flow["auth_url"].startswith("https://claude.ai/oauth/authorize")
    # The PKCE state is PERSISTED (not held in a module dict), so the login survives
    # a daemon restart between opening the browser and pasting the code.
    pending = store.take_pending_oauth(flow["handle"])
    assert pending is not None
    assert pending["kind"] == "claude"
    assert pending["code_verifier"]


def test_begin_oauth_rejects_non_oauth_template():
    with pytest.raises(ConnectionValidationError):
        begin_oauth("openai-api")


async def test_complete_oauth_creates_routable_connection(monkeypatch, store):
    # mock the token exchange + the validate network call
    async def fake_exchange(code, flow):
        return {"access_token": "live-token", "refresh_token": "r", "expires_at": None, "scopes": []}

    async def fake_validate(self, *, secret_ref, endpoint=None, model=None):
        return ValidationResult(ok=True, health="healthy", models=[])

    monkeypatch.setattr(claude_oauth, "exchange_code", fake_exchange)
    monkeypatch.setattr("navig.providers.drivers.native.NativeDriver.validate", fake_validate)

    flow = begin_oauth("claude-max", store=store)
    conn = await complete_oauth(flow["handle"], "code#state", name="My Claude", store=store)

    assert conn.template_id == "claude-max"
    assert conn.auth_state == AuthState.CONNECTED and conn.is_routable
    # token bundle stored in the vault (never on the record)
    assert conn.secret_ref and conn.secret_ref in store._vault.items
    blob = json.loads(store._vault.items[conn.secret_ref].decode())
    assert blob["access_token"] == "live-token"
    # handle consumed — single-use, exactly like the dict.pop it replaced
    assert store.take_pending_oauth(flow["handle"]) is None


async def test_complete_oauth_unknown_handle_raises(store):
    with pytest.raises(ConnectionValidationError):
        await complete_oauth("ghost", "code", store=store)
