"""Tests for the Cloudflare browser-OAuth flow + refresh-aware token resolution."""

from __future__ import annotations

import base64
import hashlib

import pytest

from navig.cloud import cf_oauth


class FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


def test_pkce_challenge_is_s256_of_verifier():
    verifier, challenge = cf_oauth._pkce()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected
    assert 43 <= len(verifier) <= 128


def test_is_expired():
    assert cf_oauth.is_expired(None) is True
    assert cf_oauth.is_expired("not-a-date") is True
    assert cf_oauth.is_expired("2000-01-01T00:00:00Z") is True
    assert cf_oauth.is_expired("2999-01-01T00:00:00Z") is False


def test_refresh_exchange(monkeypatch):
    captured = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        return FakeResp(200, {
            "access_token": "new-access", "refresh_token": "new-refresh",
            "expires_in": 3600, "scope": "account:read",
        })

    monkeypatch.setattr(cf_oauth.requests, "post", fake_post)
    bundle = cf_oauth.refresh("old-refresh")
    assert bundle.access_token == "new-access"
    assert bundle.refresh_token == "new-refresh"
    assert not cf_oauth.is_expired(bundle.expires_at)  # ~1h ahead
    assert captured["data"]["grant_type"] == "refresh_token"
    assert bundle.as_vault_data()["auth"] == "oauth"


def test_exchange_error_raises(monkeypatch):
    monkeypatch.setattr(cf_oauth.requests, "post",
                        lambda *a, **k: FakeResp(400, {"error": "invalid_grant"}))
    with pytest.raises(cf_oauth.OAuthError):
        cf_oauth.refresh("bad")


def test_refresh_keeps_old_refresh_token_when_omitted(monkeypatch):
    monkeypatch.setattr(cf_oauth.requests, "post",
                        lambda *a, **k: FakeResp(200, {"access_token": "a", "expires_in": 100}))
    bundle = cf_oauth.refresh("keep-me")
    assert bundle.refresh_token == "keep-me"


# ── resolve_cf_token refresh integration ──────────────────


class FakeCred:
    id = "cf-1"

    def __init__(self, fields):
        self._f = fields

    def get_secret(self, name):
        return self._f.get(name)


class FakeVault:
    def __init__(self, cred):
        self._cred = cred
        self.updated = None

    def get(self, provider, caller=None):
        return self._cred

    def update(self, cred_id, data=None):
        self.updated = (cred_id, data)

    def add(self, **kwargs):
        self.updated = ("add", kwargs)


def test_resolve_refreshes_expired_oauth(monkeypatch):
    import navig.vault as vault_mod
    from navig.commands import lighthouse

    cred = FakeCred({"auth": "oauth", "token": "stale", "refresh_token": "r1",
                     "expires_at": "2000-01-01T00:00:00Z"})
    fake_vault = FakeVault(cred)
    monkeypatch.setattr(vault_mod, "get_vault", lambda *a, **k: fake_vault)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "")  # ensure env doesn't short-circuit
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)

    fresh = cf_oauth.TokenBundle(access_token="fresh-access", refresh_token="r2",
                                 expires_at="2999-01-01T00:00:00Z")
    monkeypatch.setattr(cf_oauth, "refresh", lambda rt: fresh)

    assert lighthouse.resolve_cf_token() == "fresh-access"
    assert fake_vault.updated is not None  # persisted the refreshed bundle


def test_resolve_uses_valid_oauth_token(monkeypatch):
    import navig.vault as vault_mod
    from navig.commands import lighthouse

    cred = FakeCred({"auth": "oauth", "token": "still-good", "refresh_token": "r1",
                     "expires_at": "2999-01-01T00:00:00Z"})
    monkeypatch.setattr(vault_mod, "get_vault", lambda *a, **k: FakeVault(cred))
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    assert lighthouse.resolve_cf_token() == "still-good"


def test_api_token_resolver_skips_oauth(monkeypatch):
    # wrangler can't use OAuth tokens → resolve_cf_api_token() must return "" for them.
    import navig.vault as vault_mod
    from navig.commands import lighthouse

    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    oauth = FakeCred({"auth": "oauth", "token": "oauth-access", "refresh_token": "r"})
    monkeypatch.setattr(vault_mod, "get_vault", lambda *a, **k: FakeVault(oauth))
    assert lighthouse.resolve_cf_api_token() == ""

    # A real API token (no auth=oauth) is returned for wrangler to use.
    api = FakeCred({"token": "cf-api-token"})
    monkeypatch.setattr(vault_mod, "get_vault", lambda *a, **k: FakeVault(api))
    assert lighthouse.resolve_cf_api_token() == "cf-api-token"


def test_api_token_resolver_prefers_env(monkeypatch):
    from navig.commands import lighthouse

    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "env-token")
    assert lighthouse.resolve_cf_api_token() == "env-token"
