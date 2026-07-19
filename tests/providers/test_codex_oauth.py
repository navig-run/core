"""
ChatGPT/Codex OAuth — PKCE/url/exchange/RFC-8693 (pure + mocked) and the
begin→complete orchestration that creates a routable `chatgpt` (OpenAI) connection.
No network.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from navig.providers import codex_oauth
from navig.providers import connect as connect_mod
from navig.providers.connect import begin_oauth, complete_oauth
from navig.providers.connection_types import AuthState
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


# ── pure ─────────────────────────────────────────────────────────────────────


def test_authorize_url_has_codex_params():
    url, flow = codex_oauth.build_authorize_url()
    q = parse_qs(urlparse(url).query)
    assert url.startswith("https://auth.openai.com/oauth/authorize?")
    assert q["client_id"][0] == codex_oauth.CHATGPT_CLIENT_ID
    assert q["code_challenge_method"][0] == "S256"
    assert q["redirect_uri"][0] == "http://localhost:1455/auth/callback"
    assert q["codex_cli_simplified_flow"][0] == "true"
    assert q["id_token_add_organizations"][0] == "true"
    assert q["state"][0] == flow.state


def test_parse_code_from_redirect():
    assert codex_oauth.parse_code_from_redirect("RAWCODE") == "RAWCODE"
    assert codex_oauth.parse_code_from_redirect("RAWCODE#s") == "RAWCODE"
    assert codex_oauth.parse_code_from_redirect(
        "http://localhost:1455/auth/callback?code=THECODE&state=abc"
    ) == "THECODE"


async def test_exchange_code_and_key_exchange(monkeypatch):
    calls = []

    async def fake_post(body):
        calls.append(body)
        if body["grant_type"] == "authorization_code":
            return {"id_token": "idt", "access_token": "at", "refresh_token": "rt", "expires_in": 3600}
        # token-exchange
        assert body["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange"
        assert body["requested_token"] == "openai-api-key"
        return {"access_token": "sk-openai-from-exchange"}

    monkeypatch.setattr(codex_oauth, "_post_form", fake_post)
    _, flow = codex_oauth.build_authorize_url()
    tokens = await codex_oauth.exchange_code("THECODE", flow)
    assert tokens["id_token"] == "idt"
    key = await codex_oauth.exchange_id_token_for_api_key(tokens["id_token"])
    assert key == "sk-openai-from-exchange"


# ── orchestration ────────────────────────────────────────────────────────────


async def test_begin_oauth_chatgpt_is_codex_kind(store):
    flow = begin_oauth("chatgpt", store=store)
    assert flow["kind"] == "codex"
    assert flow["auth_url"].startswith("https://auth.openai.com/oauth/authorize")
    # `state` is returned so the loopback capture can match the redirect without
    # reaching into private in-flight state.
    assert flow["state"]
    assert store.take_pending_oauth(flow["handle"])["kind"] == "codex"


async def test_complete_chatgpt_creates_routable_openai_connection(monkeypatch, store):
    async def fake_exchange(code, flow):
        return {"id_token": "idt", "access_token": "at", "refresh_token": "rt", "expires_at": None}

    async def fake_key(id_token):
        assert id_token == "idt"
        return "sk-openai-live"

    async def fake_validate(self, *, secret_ref, endpoint=None, model=None):
        return ValidationResult(ok=True, health="healthy", models=[])

    monkeypatch.setattr(codex_oauth, "exchange_code", fake_exchange)
    monkeypatch.setattr(codex_oauth, "exchange_id_token_for_api_key", fake_key)
    monkeypatch.setattr("navig.providers.drivers.native.NativeDriver.validate", fake_validate)

    flow = begin_oauth("chatgpt", store=store)
    conn = await complete_oauth(flow["handle"], "code", name="My ChatGPT", store=store)

    assert conn.template_id == "chatgpt"
    assert conn.auth_state == AuthState.CONNECTED and conn.is_routable
    # the exchanged OpenAI key is stored as the connection secret (not an oauth blob)
    assert conn.secret_ref and store._vault.items[conn.secret_ref].decode() == "sk-openai-live"
