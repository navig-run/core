"""OAuth provider config: extra authorize params + CLI loopback redirect.

Regression for the Google refresh-token bug: without ``access_type=offline`` Google
returns no refresh token and a linked Gmail dies one hour later. And the CLI flow
must exchange the code with the SAME redirect_uri it authorized with, or Google
rejects the exchange (``redirect_uri_mismatch``).
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from navig.providers import oauth as oauth_mod
from navig.providers.oauth import OAuthCredentials, OAuthProviderConfig

pytestmark = pytest.mark.unit


def _cfg(**overrides) -> OAuthProviderConfig:
    base = dict(
        name="Test",
        authorize_url="https://auth.example/authorize",
        token_url="https://auth.example/token",
        client_id="cid",
        redirect_uri="http://localhost:8789/api/deck/connectors/oauth/callback",
        scopes=["a", "b"],
    )
    base.update(overrides)
    return OAuthProviderConfig(**base)


class TestExtraAuthorizeParams:
    def test_extra_params_are_appended(self):
        cfg = _cfg(extra_authorize_params={"access_type": "offline", "prompt": "consent"})
        url = cfg.build_authorize_url("st", "ch")
        q = parse_qs(urlparse(url).query)
        assert q["access_type"] == ["offline"]
        assert q["prompt"] == ["consent"]
        assert q["scope"] == ["a b"]

    def test_extra_params_never_override_pkce_core(self):
        cfg = _cfg(extra_authorize_params={"state": "evil", "client_id": "other"})
        q = parse_qs(urlparse(cfg.build_authorize_url("real-state", "ch")).query)
        assert q["state"] == ["real-state"]
        assert q["client_id"] == ["cid"]

    def test_default_is_empty_and_url_unchanged(self):
        cfg = _cfg()
        q = parse_qs(urlparse(cfg.build_authorize_url("s", "c")).query)
        assert "access_type" not in q
        assert set(q) == {
            "client_id",
            "redirect_uri",
            "response_type",
            "state",
            "code_challenge",
            "code_challenge_method",
            "scope",
        }


class TestGmailConfigRequestsOfflineAccess:
    def test_gmail_config_has_offline_and_cli_redirect(self):
        from navig.connectors.gmail.oauth_config import build_gmail_oauth_config

        cfg = build_gmail_oauth_config("cid", "sec")
        assert cfg.extra_authorize_params["access_type"] == "offline"
        assert cfg.extra_authorize_params["prompt"] == "consent"
        assert cfg.cli_redirect_uri.startswith("http://127.0.0.1:")
        assert cfg.cli_redirect_uri.endswith("/auth/callback")

    def test_env_wins_then_vault_fallback(self, monkeypatch):
        from navig.connectors.gmail import oauth_config as oc

        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
        monkeypatch.setattr(oc, "_vault_secret", lambda label: "")
        assert oc.get_gmail_oauth_config() is None

        store = {oc.VAULT_CLIENT_ID_LABEL: "vault-id", oc.VAULT_CLIENT_SECRET_LABEL: "vault-sec"}
        monkeypatch.setattr(oc, "_vault_secret", lambda label: store.get(label, ""))
        cfg = oc.get_gmail_oauth_config()
        assert cfg is not None
        assert cfg.client_id == "vault-id"
        assert cfg.client_secret == "vault-sec"

        monkeypatch.setenv("GOOGLE_CLIENT_ID", "env-id")
        cfg = oc.get_gmail_oauth_config()
        assert cfg.client_id == "env-id"


class TestInteractiveFlowUsesCliRedirect:
    def _run(self, monkeypatch, cfg: OAuthProviderConfig):
        captured: dict[str, object] = {}

        class FakeServer:
            def __init__(self, *a, **kw):
                pass

            def start(self):
                return True

            def wait_for_callback(self):
                return {"code": "the-code", "state": captured["state"]}

            def stop(self):
                pass

        def fake_authorize(self, state, challenge):
            captured["state"] = state
            captured["authorize_redirect"] = self.redirect_uri
            return "https://auth.example/authorize?x=1"

        async def fake_exchange(provider, code, verifier):
            captured["exchange_redirect"] = provider.redirect_uri
            captured["code"] = code
            return OAuthCredentials(access="a", refresh="r", expires=0)

        monkeypatch.setattr(oauth_mod, "OAuthCallbackServer", FakeServer)
        monkeypatch.setattr(OAuthProviderConfig, "build_authorize_url", fake_authorize)
        monkeypatch.setattr(oauth_mod, "exchange_code_for_tokens", fake_exchange)
        monkeypatch.setattr(oauth_mod.webbrowser, "open", lambda url: True)
        monkeypatch.setitem(oauth_mod.OAUTH_PROVIDERS, "unit-test", cfg)
        result = oauth_mod.run_oauth_flow_interactive("unit-test", on_progress=lambda m: None)
        return result, captured

    def test_cli_redirect_used_for_authorize_and_exchange(self, monkeypatch):
        cfg = _cfg(cli_redirect_uri="http://127.0.0.1:1455/auth/callback")
        result, captured = self._run(monkeypatch, cfg)
        assert result.success, result.error
        assert captured["authorize_redirect"] == "http://127.0.0.1:1455/auth/callback"
        assert captured["exchange_redirect"] == "http://127.0.0.1:1455/auth/callback"
        # The registry entry itself was not mutated.
        assert oauth_mod.OAUTH_PROVIDERS["unit-test"].redirect_uri.startswith(
            "http://localhost:8789"
        )

    def test_without_cli_redirect_gateway_uri_is_kept(self, monkeypatch):
        cfg = _cfg()
        result, captured = self._run(monkeypatch, cfg)
        assert result.success
        assert captured["authorize_redirect"] == cfg.redirect_uri
        assert captured["exchange_redirect"] == cfg.redirect_uri
