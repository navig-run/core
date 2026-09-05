"""
Tests for navig.connectors.auth_manager.ConnectorAuthManager — mocked vault/OAuth.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.connectors.auth_manager import ConnectorAuthManager
from navig.connectors.errors import ConnectorAuthError, ConnectorNotFoundError
from navig.providers.oauth import OAuthCredentials, OAuthProviderConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_manager() -> ConnectorAuthManager:
    """Return a new manager with a mocked vault."""
    mgr = ConnectorAuthManager.__new__(ConnectorAuthManager)
    mgr._vault = MagicMock()
    mgr._vault.get.return_value = None
    return mgr


def _provider_config(connector_id: str) -> OAuthProviderConfig:
    return OAuthProviderConfig(
        name=connector_id,
        client_id="cid",
        authorize_url="https://auth.example.com/oauth/authorize",
        token_url="https://auth.example.com/oauth/token",
        scopes=["read"],
    )


def _valid_creds(access: str = "access_tok") -> OAuthCredentials:
    import time
    future_ms = int(time.time() * 1000) + 3_600_000  # 1 hour from now
    return OAuthCredentials(
        access=access,
        refresh="refresh_tok",
        expires=future_ms,
    )


def _expired_creds() -> OAuthCredentials:
    return OAuthCredentials(
        access="old_access",
        refresh="refresh_tok",
        expires=1,  # 1ms since epoch — definitely expired
    )


# ---------------------------------------------------------------------------
# register_provider / get_provider_config / reset_providers
# ---------------------------------------------------------------------------


def test_register_provider_stores_config():
    ConnectorAuthManager.reset_providers()
    cfg = _provider_config("test_svc")
    ConnectorAuthManager.register_provider("test_svc", cfg)
    assert ConnectorAuthManager.get_provider_config("test_svc") is cfg
    ConnectorAuthManager.reset_providers()


def test_get_provider_config_returns_none_for_unknown():
    ConnectorAuthManager.reset_providers()
    assert ConnectorAuthManager.get_provider_config("unknown") is None


def test_reset_providers_clears_provider_configs():
    ConnectorAuthManager.reset_providers()
    ConnectorAuthManager.register_provider("svc_a", _provider_config("svc_a"))
    ConnectorAuthManager.reset_providers()
    # _provider_configs is empty after reset
    assert "svc_a" not in ConnectorAuthManager._provider_configs


# ---------------------------------------------------------------------------
# authenticate — missing provider
# ---------------------------------------------------------------------------


def test_authenticate_raises_connector_not_found():
    ConnectorAuthManager.reset_providers()
    mgr = _fresh_manager()
    with pytest.raises(ConnectorNotFoundError):
        asyncio.run(mgr.authenticate("no_such_svc"))


# ---------------------------------------------------------------------------
# authenticate — vault hit (valid token)
# ---------------------------------------------------------------------------


def test_authenticate_returns_cached_token():
    ConnectorAuthManager.reset_providers()
    ConnectorAuthManager.register_provider("svc_cached", _provider_config("svc_cached"))
    mgr = _fresh_manager()

    cred_obj = MagicMock()
    from navig.vault import CredentialType
    cred_obj.credential_type = CredentialType.OAUTH
    cred_obj.data = {"access": "cached_tok", "refresh": "r", "expires": 9_999_999_999_999}
    mgr._vault.get.return_value = cred_obj

    with patch("navig.connectors.auth_manager.OAuthCredentials.from_dict", return_value=_valid_creds("cached_tok")):
        result = asyncio.run(mgr.authenticate("svc_cached"))
    assert result == "cached_tok"
    ConnectorAuthManager.reset_providers()


# ---------------------------------------------------------------------------
# authenticate — expired, refresh succeeds
# ---------------------------------------------------------------------------


def test_authenticate_refreshes_expired_token():
    ConnectorAuthManager.reset_providers()
    ConnectorAuthManager.register_provider("svc_refresh", _provider_config("svc_refresh"))
    mgr = _fresh_manager()

    cred_obj = MagicMock()
    from navig.vault import CredentialType
    cred_obj.credential_type = CredentialType.OAUTH
    cred_obj.data = {"access": "old", "refresh": "r", "expires": 1}
    mgr._vault.get.return_value = cred_obj

    new_creds = _valid_creds("new_access")

    with (
        patch("navig.connectors.auth_manager.OAuthCredentials.from_dict", return_value=_expired_creds()),
        patch("navig.connectors.auth_manager.refresh_oauth_tokens", AsyncMock(return_value=new_creds)),
    ):
        result = asyncio.run(mgr.authenticate("svc_refresh"))
    assert result == "new_access"
    ConnectorAuthManager.reset_providers()


# ---------------------------------------------------------------------------
# authenticate — non-interactive with no token raises
# ---------------------------------------------------------------------------


def test_authenticate_non_interactive_no_token_raises():
    ConnectorAuthManager.reset_providers()
    ConnectorAuthManager.register_provider("svc_notoken", _provider_config("svc_notoken"))
    mgr = _fresh_manager()
    # vault returns no creds
    with pytest.raises(ConnectorAuthError):
        asyncio.run(mgr.authenticate("svc_notoken", interactive=False))
    ConnectorAuthManager.reset_providers()


# ---------------------------------------------------------------------------
# get_access_token — no stored creds
# ---------------------------------------------------------------------------


def test_get_access_token_no_creds_raises():
    ConnectorAuthManager.reset_providers()
    mgr = _fresh_manager()
    with pytest.raises(ConnectorAuthError, match="No stored credentials"):
        asyncio.run(mgr.get_access_token("no_connector"))


# ---------------------------------------------------------------------------
# get_access_token — valid creds returned immediately
# ---------------------------------------------------------------------------


def test_get_access_token_returns_valid():
    ConnectorAuthManager.reset_providers()
    mgr = _fresh_manager()

    cred_obj = MagicMock()
    from navig.vault import CredentialType
    cred_obj.credential_type = CredentialType.OAUTH
    cred_obj.data = {}
    mgr._vault.get.return_value = cred_obj

    valid = _valid_creds("live_tok")
    with patch("navig.connectors.auth_manager.OAuthCredentials.from_dict", return_value=valid):
        result = asyncio.run(mgr.get_access_token("any"))
    assert result == "live_tok"


# ---------------------------------------------------------------------------
# revoke — removes vault entry
# ---------------------------------------------------------------------------


def test_revoke_calls_vault_remove():
    mgr = _fresh_manager()
    fake_cred = MagicMock()
    fake_cred.id = "vault-123"
    mgr._vault.get.return_value = fake_cred

    asyncio.run(mgr.revoke("svc_revoke"))
    mgr._vault.remove.assert_called_once_with("vault-123")


def test_revoke_no_cred_does_not_raise():
    mgr = _fresh_manager()
    mgr._vault.get.return_value = None
    asyncio.run(mgr.revoke("nonexistent"))  # should not raise


# ---------------------------------------------------------------------------
# _save_to_vault — atomic upsert, never remove-then-add
# ---------------------------------------------------------------------------


def test_save_to_vault_upserts_in_place_without_remove():
    """The save/refresh path must NOT remove-then-add.

    ``vault.add`` upserts by the unique (provider, profile) label, so removing first only opened a
    window where a failed ``add`` after a committed ``remove`` lost the refresh token (silent
    logout) and churned a new credential id every hourly refresh.
    """
    mgr = _fresh_manager()
    existing = MagicMock()
    existing.id = "old-id"
    mgr._vault.get.return_value = existing  # a credential already exists in the vault

    mgr._save_to_vault("svc", _valid_creds("tok"))

    mgr._vault.add.assert_called_once()
    mgr._vault.remove.assert_not_called()  # no destructive remove-before-write
    _args, kwargs = mgr._vault.add.call_args
    assert kwargs["provider"] == "svc"
    assert kwargs["profile_id"] == "connector"  # canonical label → in-place upsert


def test_refresh_saves_new_token_without_remove():
    """End-to-end: an expired token refresh persists via a single upsert, no remove."""
    ConnectorAuthManager.reset_providers()
    ConnectorAuthManager.register_provider("svc_r2", _provider_config("svc_r2"))
    mgr = _fresh_manager()

    cred_obj = MagicMock()
    from navig.vault import CredentialType
    cred_obj.credential_type = CredentialType.OAUTH
    cred_obj.data = {"access": "old", "refresh": "r", "expires": 1}
    mgr._vault.get.return_value = cred_obj

    with (
        patch("navig.connectors.auth_manager.OAuthCredentials.from_dict", return_value=_expired_creds()),
        patch("navig.connectors.auth_manager.refresh_oauth_tokens", AsyncMock(return_value=_valid_creds("fresh"))),
    ):
        result = asyncio.run(mgr.get_access_token("svc_r2"))

    assert result == "fresh"
    mgr._vault.add.assert_called_once()
    mgr._vault.remove.assert_not_called()
    ConnectorAuthManager.reset_providers()


# ---------------------------------------------------------------------------
# is_connected — an EXPIRED access token with a refresh token is still connected
#
# Regression: `is_connected` was `creds is not None and not creds.is_expired`.
# OAuth access tokens are short-lived (Google's last 1 hour), so a healthy
# account reported "not connected" ~55 min after it was linked (is_expired adds
# a 5-minute buffer). `navig/notify/email.py` gates on this BEFORE calling
# inject_token() -- the very call that refreshes -- so email notifications died
# with "Gmail not connected (Settings -> Connectors)", telling the user to
# reconnect, which fixed it for exactly one more hour.
# ---------------------------------------------------------------------------


def _stub_vault_creds(mgr: ConnectorAuthManager, creds: OAuthCredentials) -> None:
    """Point the mocked vault at *creds* the way the real OAUTH path returns them."""
    from navig.vault import CredentialType

    cred_obj = MagicMock()
    cred_obj.credential_type = CredentialType.OAUTH
    cred_obj.data = creds.to_dict()
    mgr._vault.get.return_value = cred_obj


def test_is_connected_true_for_valid_token():
    mgr = _fresh_manager()
    _stub_vault_creds(mgr, _valid_creds())
    assert mgr.is_connected("svc_ok") is True


def test_is_connected_true_for_expired_token_with_refresh_token():
    """THE REGRESSION: expired access + valid refresh == still connected."""
    mgr = _fresh_manager()
    expired = _expired_creds()
    assert expired.is_expired, "fixture must actually be expired"
    assert expired.refresh, "fixture must carry a refresh token"
    _stub_vault_creds(mgr, expired)

    assert mgr.is_connected("svc_expired") is True, (
        "an expired access token with a refresh token is still connected -- "
        "get_access_token() refreshes it transparently on the next call"
    )


def test_is_connected_false_when_expired_and_no_refresh_token():
    """The one case that genuinely needs the user to re-authenticate."""
    mgr = _fresh_manager()
    dead = OAuthCredentials(access="old", refresh="", expires=1)
    _stub_vault_creds(mgr, dead)
    assert mgr.is_connected("svc_dead") is False


def test_is_connected_false_when_nothing_stored():
    mgr = _fresh_manager()
    mgr._vault.get.return_value = None
    assert mgr.is_connected("svc_absent") is False


def test_is_connected_agrees_with_list_connected_accounts():
    """The two surfaces must not disagree about the same expired credential.

    `list_connected_accounts` documents "Includes expired tokens (still
    'connected', just needs refresh)" -- `is_connected` said the opposite.
    """
    mgr = _fresh_manager()
    _stub_vault_creds(mgr, _expired_creds())
    mgr._vault.list.return_value = [
        MagicMock(provider="svc_expired", metadata={"email": "user@example.com"})
    ]

    listed = "svc_expired" in mgr.list_connected_accounts()
    assert listed is mgr.is_connected("svc_expired") is True
