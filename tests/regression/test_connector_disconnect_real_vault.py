"""Disconnect removes the credential — proven against a REAL vault, not a fake.

``revoke`` called ``vault.remove()``, which the vault does not have; the AttributeError
was swallowed at debug level, so every Disconnect (deck, Email app, services catalog)
answered success and the account stayed connected. ``list_accounts`` read a
``profile_id`` attribute that the vault's list rows do not carry, so it returned [] on
every real install. Both hid behind a test fake that had the attribute and the method.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from navig.connectors.auth_manager import ConnectorAuthManager
from navig.connectors.errors import ConnectorAuthError
from navig.providers.oauth import OAuthCredentials
from navig.vault.core import Vault


@pytest.fixture
def mgr(tmp_path):
    vault = Vault(tmp_path)
    m = ConnectorAuthManager.__new__(ConnectorAuthManager)
    m._vault = vault
    try:
        yield m
    finally:
        vault._store.close()


def _creds(email: str) -> OAuthCredentials:
    return OAuthCredentials(access=f"a-{email}", refresh="r", expires=time.time() + 3600, email=email)


def test_list_accounts_sees_every_linked_account(mgr):
    mgr._save_to_vault("gmail", _creds("work@example.com"))
    mgr._save_to_vault("gmail", _creds("me@example.com"), "me@example.com")
    assert mgr.list_accounts("gmail") == ["work@example.com", "me@example.com"]


def test_disconnect_actually_removes_the_account(mgr):
    mgr._save_to_vault("gmail", _creds("work@example.com"))
    assert asyncio.run(mgr.revoke("gmail")) is True
    assert mgr.list_connected_accounts() == {}
    assert mgr._load_from_vault("gmail") is None


def test_removing_the_default_promotes_the_next_account(mgr):
    mgr._save_to_vault("gmail", _creds("work@example.com"))
    mgr._save_to_vault("gmail", _creds("me@example.com"), "me@example.com")
    asyncio.run(mgr.revoke("gmail"))
    assert mgr.list_connected_accounts() == {"gmail": "me@example.com"}
    assert mgr.list_accounts("gmail") == ["me@example.com"]


def test_removing_one_extra_account_leaves_the_default(mgr):
    mgr._save_to_vault("gmail", _creds("work@example.com"))
    mgr._save_to_vault("gmail", _creds("me@example.com"), "me@example.com")
    asyncio.run(mgr.revoke("gmail", "me@example.com"))
    assert mgr.list_accounts("gmail") == ["work@example.com"]


def test_an_unlinked_account_is_an_error_not_a_silent_success(mgr):
    mgr._save_to_vault("gmail", _creds("work@example.com"))
    with pytest.raises(ConnectorAuthError):
        asyncio.run(mgr.revoke("gmail", "nobody@example.com"))
    assert mgr.list_accounts("gmail") == ["work@example.com"]


def test_nothing_to_remove_says_so(mgr):
    assert asyncio.run(mgr.revoke("gmail")) is False


def test_linking_a_second_account_from_the_app_keeps_the_first(mgr, monkeypatch):
    """The deck/app OAuth callback (exchange_auth_code) always wrote the default slot, so a
    second Gmail linked from the app replaced the first. It now slots it like the CLI does."""
    from navig.providers import oauth as provider_oauth

    monkeypatch.setattr(ConnectorAuthManager, "get_provider_config", classmethod(lambda cls, cid: object()))
    queue = [_creds("work@example.com"), _creds("me@example.com"), _creds("work@example.com")]

    async def exchange(config, code, verifier):
        return queue.pop(0)

    monkeypatch.setattr(provider_oauth, "exchange_code_for_tokens", exchange)
    for state in ("s1", "s2", "s3"):
        ConnectorAuthManager._pending_auth[state] = ("gmail", "verifier", time.time())
        asyncio.run(mgr.exchange_auth_code(state, "code"))
    # The third link is the first account again: refreshed in place, not duplicated.
    assert mgr.list_accounts("gmail") == ["work@example.com", "me@example.com"]


def _seed_legacy_default_row(mgr, email: str) -> None:
    """A gmail OAuth row under the vault's own ``default`` profile (wizard / old install)."""
    mgr._vault.add(
        provider="gmail",
        credential_type="oauth",
        data=_creds(email).to_dict(),
        profile_id="default",
        label="gmail legacy",
        metadata={"email": email},
    )


def test_a_legacy_fallback_credential_is_migrated_and_disconnect_sticks(mgr):
    _seed_legacy_default_row(mgr, "legacy@example.com")
    loaded = mgr._load_from_vault("gmail")
    assert loaded is not None and loaded.email == "legacy@example.com"  # via the fallback

    mgr._save_to_vault("gmail", loaded)  # a refresh writes the connector slot
    assert asyncio.run(mgr.revoke("gmail")) is True
    assert mgr._load_from_vault("gmail") is None
    assert mgr.is_connected("gmail") is False


def test_disconnect_also_drops_a_fallback_row_left_beside_the_connector_row(mgr):
    _seed_legacy_default_row(mgr, "legacy@example.com")
    mgr._vault.add(  # the duplicate an older save left behind
        provider="gmail",
        credential_type="oauth",
        data=_creds("legacy@example.com").to_dict(),
        profile_id="connector",
        label="gmail connector OAuth",
        metadata={"email": "legacy@example.com"},
    )
    assert asyncio.run(mgr.revoke("gmail")) is True
    assert mgr.is_connected("gmail") is False


def test_a_named_account_lookup_never_returns_the_fallback_row(mgr):
    _seed_legacy_default_row(mgr, "legacy@example.com")
    assert mgr._load_from_vault("gmail", "new@example.com") is None
    with pytest.raises(ConnectorAuthError):
        mgr.resolve_account("gmail", "new@example.com")

