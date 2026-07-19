"""Coverage for the vault auto-login store (logins.py + sessions.py).

Security-relevant and previously untested. The two things that MUST hold:

  * ``normalize_host`` keys a credential to the *real* host — a confusable input
    (``github.com@evil.com``, ``evil.com/github.com``) must never resolve to the
    victim host, or a credential leaks cross-origin.
  * a *pinned* username returns only that account's login/session — never a
    different account's — for both ``resolve_login`` and ``get_session``.
"""

from __future__ import annotations

import pytest

from navig.vault.core import Vault
from navig.vault.logins import (
    WebLogin,
    add_login,
    find_logins_for_domain,
    get_login,
    login_label,
    normalize_host,
    remove_login,
    resolve_login,
)
from navig.vault.sessions import (
    get_session,
    remove_session,
    save_session,
    session_label,
)


@pytest.fixture
def vault(tmp_path):
    v = Vault(tmp_path)
    try:
        yield v
    finally:
        v._store.close()


# ── normalize_host — the credential-keying floor ─────────────────────────────


@pytest.mark.parametrize(
    "raw, expect",
    [
        ("github.com", "github.com"),
        ("https://GitHub.com/login", "github.com"),      # scheme + path + case
        ("github.com:8080", "github.com"),               # bare host + port
        ("user:pass@github.com", "github.com"),          # embedded credentials
        ("http://u:p@GitHub.com:443/x?y=1", "github.com"),  # everything at once
        ("  github.com  ", "github.com"),                # whitespace
        ("", ""),
    ],
)
def test_normalize_host_canonicalises(raw, expect):
    assert normalize_host(raw) == expect


@pytest.mark.parametrize(
    "raw, expect",
    [
        ("github.com@evil.com", "evil.com"),     # '@' → the host is what FOLLOWS it
        ("evil.com/github.com", "evil.com"),     # path segment is not the host
        ("github.com.evil.com", "github.com.evil.com"),  # a subdomain of evil.com
        ("https://github.com@evil.com/login", "evil.com"),
    ],
)
def test_normalize_host_is_not_fooled_by_confusable_inputs(raw, expect):
    # A regression here (dropping the '@'/path handling) would key a credential to
    # the victim host and hand it to the attacker's page — this locks that door.
    assert normalize_host(raw) == expect


# ── logins: CRUD + host normalisation on store ───────────────────────────────


def _login(domain, username="alice", password="pw", **kw):
    return WebLogin(domain=domain, username=username, password=password, **kw)


def test_add_get_roundtrip_keeps_secrets(vault):
    add_login(_login("github.com", totp_secret="JBSWY3DPEHPK3PXP"), vault=vault)
    got = get_login("github.com", "alice", vault=vault)
    assert got is not None
    assert got.password == "pw" and got.totp_secret == "JBSWY3DPEHPK3PXP"


def test_store_normalises_host_so_lookup_is_stable(vault):
    # Stored via a full URL; retrievable by the bare host (same label).
    add_login(_login("https://GitHub.com/login"), vault=vault)
    assert get_login("github.com", "alice", vault=vault) is not None
    assert login_label("HTTPS://GitHub.com:443/x", "alice") == "web/github.com/alice"


def test_get_login_wrong_username_is_none(vault):
    add_login(_login("github.com", username="alice"), vault=vault)
    assert get_login("github.com", "bob", vault=vault) is None


def test_remove_login(vault):
    add_login(_login("github.com"), vault=vault)
    assert remove_login("github.com", "alice", vault=vault) is True
    assert get_login("github.com", "alice", vault=vault) is None


# ── resolve_login — disambiguation + pinned-username exactness ────────────────


def test_resolve_single_credential_is_ok(vault):
    add_login(_login("github.com", username="alice"), vault=vault)
    login, status = resolve_login("github.com", vault=vault)
    assert status == "ok" and login is not None and login.username == "alice"


def test_resolve_two_accounts_no_username_needs_disambiguation(vault):
    add_login(_login("github.com", username="alice"), vault=vault)
    add_login(_login("github.com", username="bob"), vault=vault)
    login, status = resolve_login("github.com", vault=vault)
    assert status == "needs_disambiguation" and login is None


def test_resolve_pinned_username_returns_that_account(vault):
    add_login(_login("github.com", username="alice", password="a-pw"), vault=vault)
    add_login(_login("github.com", username="bob", password="b-pw"), vault=vault)
    login, status = resolve_login("github.com", "bob", vault=vault)
    assert status == "ok" and login is not None and login.password == "b-pw"


def test_resolve_pinned_username_no_match_is_no_credential(vault):
    add_login(_login("github.com", username="alice"), vault=vault)
    login, status = resolve_login("github.com", "carol", vault=vault)
    assert status == "no_credential" and login is None


def test_resolve_nothing_stored_is_no_credential(vault):
    login, status = resolve_login("nothing.example", vault=vault)
    assert status == "no_credential" and login is None


def test_find_logins_for_domain_matches_exact_host(vault):
    add_login(_login("github.com", username="alice"), vault=vault)
    add_login(_login("gitlab.com", username="alice"), vault=vault)
    hits = find_logins_for_domain("github.com", vault=vault)
    assert {h.domain for h in hits} == {"github.com"}


# ── sessions: account isolation + username-less fallback ──────────────────────


def test_save_and_get_session_roundtrip(vault):
    state = {"cookies": [{"name": "sid", "value": "secret"}], "origins": []}
    save_session("github.com", state, username="alice", vault=vault)
    got = get_session("github.com", "alice", vault=vault)
    assert got is not None and got.storage_state == state


def test_pinned_username_never_returns_a_different_account(vault):
    # Only 'alice' has a session; a lookup pinned to 'bob' must NOT fall back to
    # alice's — a pinned username is exact (line: "no fallback").
    save_session("github.com", {"cookies": [], "origins": []}, username="alice", vault=vault)
    assert get_session("github.com", "bob", vault=vault) is None
    assert get_session("github.com", "alice", vault=vault) is not None


def test_username_less_lookup_falls_back_to_an_account_session(vault):
    # Saved WITH an account (web-session/host/alice); a username-less probe hits
    # web-session/host/default first (miss) then falls back to the host's session.
    save_session("mail.example", {"cookies": [{"name": "s", "value": "x"}], "origins": []},
                 username="alice", vault=vault)
    got = get_session("mail.example", vault=vault)
    assert got is not None and got.username == "alice"


def test_remove_session(vault):
    save_session("github.com", {"cookies": [], "origins": []}, username="alice", vault=vault)
    assert remove_session("github.com", "alice", vault=vault) is True
    assert get_session("github.com", "alice", vault=vault) is None


def test_session_label_normalises_host():
    assert session_label("https://GitHub.com:443/x", "alice") == "web-session/github.com/alice"
