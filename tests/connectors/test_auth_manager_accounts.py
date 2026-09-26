"""Multi-account connectors: a second Gmail never overwrites the first.

The vault keys a connector's token on ``(provider, profile)``. The first linked account
keeps the historical ``connector`` profile; another account of the same connector
lands in ``connector:<email>``, and every token method takes ``account=`` to pick it.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from navig.connectors.auth_manager import ConnectorAuthManager, profile_for
from navig.connectors.errors import ConnectorAuthError
from navig.providers.oauth import OAuthCredentials, OAuthProviderConfig
from navig.vault import CredentialType

pytestmark = pytest.mark.unit


class FakeVault:
    """Just enough of the vault: rows keyed by (provider, profile)."""

    def __init__(self):
        self.rows: dict[tuple[str, str], SimpleNamespace] = {}

    def add(self, *, provider, credential_type, data, profile_id, label, metadata):
        row = SimpleNamespace(
            id=f"{provider}/{profile_id}",
            provider=provider,
            profile_id=profile_id,
            credential_type=CredentialType.OAUTH,
            data=data,
            label=label,
            metadata=metadata,
        )
        self.rows[(provider, profile_id)] = row
        return row.id

    def get(self, provider, profile_id="default", caller=None):
        return self.rows.get((provider, profile_id))

    def list(self, provider=None, profile_id=None):
        return [
            r
            for (p, prof), r in self.rows.items()
            if (provider is None or p == provider) and (profile_id is None or prof == profile_id)
        ]

    def remove(self, cred_id):
        for k, r in list(self.rows.items()):
            if r.id == cred_id:
                del self.rows[k]


def _mgr() -> ConnectorAuthManager:
    m = ConnectorAuthManager.__new__(ConnectorAuthManager)
    m._vault = FakeVault()
    ConnectorAuthManager.register_provider(
        "gmail",
        OAuthProviderConfig(
            name="Gmail", authorize_url="https://a", token_url="https://t", client_id="cid"
        ),
    )
    return m


def _creds(email: str, access: str = "tok") -> OAuthCredentials:
    return OAuthCredentials(
        access=access, refresh="r", expires=int((time.time() + 3600) * 1000), email=email
    )


def test_profile_for():
    assert profile_for(None) == "connector"
    assert profile_for("") == "connector"
    assert profile_for(" Me@Gmail.com ") == "connector:me@gmail.com"


def test_first_account_takes_the_default_slot_second_gets_its_own():
    m = _mgr()
    m._save_to_vault(
        "gmail", _creds("studio@gmail.com"), m._slot_for_new("gmail", _creds("studio@gmail.com"))
    )
    assert m.get_connected_account("gmail") == "studio@gmail.com"

    second = _creds("me@gmail.com", access="tok2")
    m._save_to_vault("gmail", second, m._slot_for_new("gmail", second))
    # The first account is untouched; the second lives in its own profile.
    assert m.get_connected_account("gmail") == "studio@gmail.com"
    assert m.get_connected_account("gmail", "me@gmail.com") == "me@gmail.com"
    assert m.list_accounts("gmail") == ["studio@gmail.com", "me@gmail.com"]

    # Re-linking the SAME default account updates the default slot, not a new one.
    again = _creds("studio@gmail.com", access="tok3")
    assert m._slot_for_new("gmail", again) is None
    m._save_to_vault("gmail", again, None)
    assert asyncio.run(m.get_access_token("gmail")) == "tok3"
    assert asyncio.run(m.get_access_token("gmail", "me@gmail.com")) == "tok2"
    # The default account's own email resolves to the default slot too.
    assert asyncio.run(m.get_access_token("gmail", "studio@gmail.com")) == "tok3"


def test_unknown_account_is_a_clear_error():
    m = _mgr()
    m._save_to_vault("gmail", _creds("studio@gmail.com"))
    with pytest.raises(ConnectorAuthError) as exc:
        asyncio.run(m.get_access_token("gmail", "nobody@gmail.com"))
    assert "nobody@gmail.com" in str(exc.value) and "studio@gmail.com" in str(exc.value)
    assert m.is_connected("gmail") and not m.is_connected("gmail", "connector:nobody@gmail.com")


def test_inject_and_revoke_per_account():
    m = _mgr()
    m._save_to_vault("gmail", _creds("studio@gmail.com", "A"))
    m._save_to_vault("gmail", _creds("me@gmail.com", "B"), "me@gmail.com")
    connector = MagicMock(id="gmail")
    assert asyncio.run(m.inject_token(connector, "me@gmail.com"))
    connector.set_access_token.assert_called_with("B")
    assert asyncio.run(m.inject_token(connector))
    connector.set_access_token.assert_called_with("A")

    asyncio.run(m.revoke("gmail", "me@gmail.com"))
    assert m.list_accounts("gmail") == ["studio@gmail.com"]
    asyncio.run(m.revoke("gmail"))
    assert m.list_accounts("gmail") == []


def test_authenticate_refuses_a_browser_that_picked_the_wrong_account(monkeypatch):
    import navig.connectors.auth_manager as am

    m = _mgr()
    m._save_to_vault("gmail", _creds("studio@gmail.com"))
    monkeypatch.setattr(
        am,
        "run_oauth_flow_interactive",
        lambda name: SimpleNamespace(
            success=True, credentials=_creds("wrong@gmail.com"), error=None
        ),
    )
    with pytest.raises(ConnectorAuthError) as exc:
        asyncio.run(m.authenticate("gmail", interactive=True, account="me@gmail.com"))
    assert "wrong@gmail.com" in str(exc.value)
    assert m.list_accounts("gmail") == ["studio@gmail.com"]  # nothing saved

    monkeypatch.setattr(
        am,
        "run_oauth_flow_interactive",
        lambda name: SimpleNamespace(
            success=True, credentials=_creds("me@gmail.com", "NEW"), error=None
        ),
    )
    assert asyncio.run(m.authenticate("gmail", interactive=True, account="me@gmail.com")) == "NEW"
    assert m.list_accounts("gmail") == ["studio@gmail.com", "me@gmail.com"]
