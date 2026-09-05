"""Tests for ``navig.notify.email.send_email`` — the notify router's email channel.

Regression guard for the "email notifications die one hour after you connect Gmail"
bug: ``send_email`` gates on ``ConnectorAuthManager.is_connected("gmail")`` **before**
calling ``inject_token()``, which is the very call that refreshes an expired access
token. ``is_connected`` used to reject any expired token, and Google access tokens
expire in one hour — so a healthy account got "Gmail not connected", which told the
user to reconnect, which fixed it for exactly one more hour.

These tests drive the **real** ``ConnectorAuthManager`` (only the vault and the token
endpoint are stubbed) rather than a hand-rolled fake, so a fake can't agree with a bug
and keep the suite green.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.connectors.auth_manager import ConnectorAuthManager
from navig.providers.oauth import OAuthCredentials, OAuthProviderConfig
from navig.vault import CredentialType


def _manager_holding(creds: OAuthCredentials) -> ConnectorAuthManager:
    """A real manager whose (mocked) vault holds *creds* for "gmail"."""
    mgr = ConnectorAuthManager.__new__(ConnectorAuthManager)
    mgr._vault = MagicMock()
    cred_obj = MagicMock()
    cred_obj.credential_type = CredentialType.OAUTH
    cred_obj.data = creds.to_dict()
    mgr._vault.get.return_value = cred_obj
    return mgr


def _expired_but_refreshable() -> OAuthCredentials:
    """The overwhelmingly common steady state: hourly access token aged out."""
    return OAuthCredentials(
        access="stale_access",
        refresh="refresh_still_good",
        expires=int((time.time() - 60) * 1000),  # expired a minute ago
    )


def _gmail_provider() -> OAuthProviderConfig:
    return OAuthProviderConfig(
        name="gmail",
        client_id="cid",
        authorize_url="https://accounts.example.com/o/oauth2/auth",
        token_url="https://oauth2.example.com/token",
        scopes=["https://www.googleapis.com/auth/gmail.send"],
    )


def _connector_that_sends() -> MagicMock:
    connector = MagicMock()
    connector.id = "gmail"
    connector.act = AsyncMock(return_value=MagicMock(success=True))
    return connector


@pytest.mark.asyncio
async def test_send_email_refreshes_expired_token_instead_of_reporting_disconnected():
    """THE REGRESSION: an hour-old access token must not read as "not connected"."""
    from navig.notify.email import send_email

    ConnectorAuthManager.reset_providers()
    ConnectorAuthManager.register_provider("gmail", _gmail_provider())
    mgr = _manager_holding(_expired_but_refreshable())
    connector = _connector_that_sends()

    refreshed = OAuthCredentials(
        access="fresh_access",
        refresh="refresh_still_good",
        expires=int((time.time() + 3600) * 1000),
    )

    try:
        with (
            patch("navig.connectors.bootstrap.ensure_connectors_loaded"),
            patch("navig.connectors.auth_manager.ConnectorAuthManager", return_value=mgr),
            patch(
                "navig.connectors.registry.get_connector_registry",
                return_value=MagicMock(get=MagicMock(return_value=connector)),
            ),
            patch(
                "navig.connectors.auth_manager.refresh_oauth_tokens",
                AsyncMock(return_value=refreshed),
            ),
        ):
            ok, detail = await send_email("dest@example.com", "Subject", "Body")
    finally:
        ConnectorAuthManager.reset_providers()

    assert "not connected" not in detail.lower(), (
        f"an expired-but-refreshable Gmail token was reported as disconnected: {detail!r}"
    )
    assert (ok, detail) == (True, "sent")
    connector.act.assert_awaited_once()
    # The refreshed token — not the stale one — reached the connector.
    connector.set_access_token.assert_called_once_with("fresh_access")


@pytest.mark.asyncio
async def test_send_email_still_reports_disconnected_when_nothing_is_stored():
    """The honest case must keep its actionable message."""
    from navig.notify.email import send_email

    mgr = ConnectorAuthManager.__new__(ConnectorAuthManager)
    mgr._vault = MagicMock()
    mgr._vault.get.return_value = None

    with (
        patch("navig.connectors.bootstrap.ensure_connectors_loaded"),
        patch("navig.connectors.auth_manager.ConnectorAuthManager", return_value=mgr),
    ):
        ok, detail = await send_email("dest@example.com", "Subject", "Body")

    assert ok is False
    assert "not connected" in detail.lower()


@pytest.mark.asyncio
async def test_send_email_requires_a_recipient():
    from navig.notify.email import send_email

    ok, detail = await send_email("", "Subject", "Body")
    assert (ok, detail) == (False, "no recipient configured")
