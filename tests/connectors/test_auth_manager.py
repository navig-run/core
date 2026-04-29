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
        provider=connector_id,
        client_id="cid",
        client_secret="csec",
        auth_url="https://auth.example.com/oauth/authorize",
        token_url="https://auth.example.com/oauth/token",
        scopes=["read"],
    )


def _valid_creds(access: str = "access_tok") -> OAuthCredentials:
    return OAuthCredentials(
        access=access,
        refresh="refresh_tok",
        expires_at=9_999_999_999,  # far future
    )


def _expired_creds() -> OAuthCredentials:
    return OAuthCredentials(
        access="old_access",
        refresh="refresh_tok",
        expires_at=1,  # epoch — definitely expired
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


def test_reset_providers_clears_all():
    ConnectorAuthManager.register_provider("svc_a", _provider_config("svc_a"))
    ConnectorAuthManager.reset_providers()
    assert ConnectorAuthManager.get_provider_config("svc_a") is None


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
    cred_obj.data = {"access": "cached_tok", "refresh": "r", "expires_at": 9_999_999_999}
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
    cred_obj.data = {"access": "old", "refresh": "r", "expires_at": 1}
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
