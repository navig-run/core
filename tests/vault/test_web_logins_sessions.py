"""Stage 1 — website logins + authenticated-session vault model.

Round-trip stability, multi-account resolution/disambiguation, and the
non-secret listing contract. Every test uses an isolated vault directory so it
never touches the real ``~/.navig`` vault.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from navig.vault import (
    SessionState,
    WebLogin,
    add_login,
    find_logins_for_domain,
    get_login,
    get_session,
    list_logins,
    list_sessions,
    remove_login,
    remove_session,
    resolve_login,
    save_session,
)
from navig.vault.core import Vault
from navig.vault.logins import login_label, normalize_host

pytestmark = pytest.mark.integration


@pytest.fixture
def vault():
    # ignore_cleanup_errors: on Windows the SQLite (WAL) file may linger a beat
    # after close(); we still close explicitly to release the handle promptly.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        v = Vault(vault_dir=Path(tmp))
        try:
            yield v
        finally:
            v.store().close()


# ---------------------------------------------------------------------------
# normalize_host
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://GitHub.com/login", "github.com"),
        ("http://example.com:8080/x?y=1", "example.com"),
        ("EXAMPLE.COM", "example.com"),
        ("user@mail.example.com", "mail.example.com"),
        ("github.com", "github.com"),
    ],
)
def test_normalize_host(raw, expected):
    assert normalize_host(raw) == expected


# ---------------------------------------------------------------------------
# WebLogin round-trip
# ---------------------------------------------------------------------------


def test_login_roundtrip(vault):
    login = WebLogin(
        domain="https://GitHub.com",
        username="alice@example.com",
        password="s3cr3t!",
        url="https://github.com/login",
        totp_secret="JBSWY3DPEHPK3PXP",
        notes="work account",
    )
    add_login(login, vault=vault)

    got = get_login("github.com", "alice@example.com", vault=vault)
    assert got is not None
    assert got.password == "s3cr3t!"
    assert got.domain == "github.com"  # normalised on store
    assert got.totp_secret == "JBSWY3DPEHPK3PXP"
    assert got.notes == "work account"


def test_login_overwrite_is_stable(vault):
    add_login(WebLogin(domain="example.com", username="bob", password="one"), vault=vault)
    add_login(WebLogin(domain="example.com", username="bob", password="two"), vault=vault)
    got = get_login("example.com", "bob", vault=vault)
    assert got.password == "two"
    # single item, not duplicated
    assert len(find_logins_for_domain("example.com", vault=vault)) == 1


def test_missing_login_returns_none(vault):
    assert get_login("nope.com", "ghost", vault=vault) is None


# ---------------------------------------------------------------------------
# Listing is non-secret
# ---------------------------------------------------------------------------


def test_list_logins_has_no_password(vault):
    add_login(
        WebLogin(domain="example.com", username="bob", password="hunter2",
                 totp_secret="ABC"),
        vault=vault,
    )
    infos = list_logins(vault=vault)
    assert len(infos) == 1
    info = infos[0]
    assert info.domain == "example.com"
    assert info.username == "bob"
    assert info.has_totp is True
    # LoginInfo has no password field at all
    assert not hasattr(info, "password")


# ---------------------------------------------------------------------------
# resolve_login — multi-account selection
# ---------------------------------------------------------------------------


def test_resolve_single_account_ok(vault):
    add_login(WebLogin(domain="example.com", username="bob", password="p1"), vault=vault)
    login, status = resolve_login("example.com", vault=vault)
    assert status == "ok"
    assert login.username == "bob"


def test_resolve_no_credential(vault):
    login, status = resolve_login("empty.com", vault=vault)
    assert status == "no_credential"
    assert login is None


def test_resolve_multi_account_needs_disambiguation(vault):
    add_login(WebLogin(domain="example.com", username="bob", password="p1"), vault=vault)
    add_login(WebLogin(domain="example.com", username="carol", password="p2"), vault=vault)
    login, status = resolve_login("example.com", vault=vault)
    assert status == "needs_disambiguation"
    assert login is None
    # explicit username resolves it
    login, status = resolve_login("example.com", "carol", vault=vault)
    assert status == "ok"
    assert login.password == "p2"


def test_resolve_matches_by_registrable_domain(vault):
    # login stored for a subdomain must resolve for the site's registrable domain
    add_login(WebLogin(domain="mail.google.com", username="me@gmail.com", password="p"), vault=vault)
    login, status = resolve_login("google.com", vault=vault)
    assert status == "ok" and login.username == "me@gmail.com"
    # and the reverse: stored at the apex, looked up by a subdomain
    add_login(WebLogin(domain="github.com", username="dev", password="q"), vault=vault)
    login2, status2 = resolve_login("gist.github.com", vault=vault)
    assert status2 == "ok" and login2.username == "dev"


def test_resolve_prefers_exact_host_over_registrable(vault):
    # separate creds at the apex AND a subdomain → exact host wins, no disambiguation
    add_login(WebLogin(domain="google.com", username="apex", password="p1"), vault=vault)
    add_login(WebLogin(domain="mail.google.com", username="sub", password="p2"), vault=vault)
    login, status = resolve_login("mail.google.com", vault=vault)
    assert status == "ok" and login.username == "sub"
    login2, status2 = resolve_login("google.com", vault=vault)
    assert status2 == "ok" and login2.username == "apex"


def test_resolve_wrong_username_no_credential(vault):
    add_login(WebLogin(domain="example.com", username="bob", password="p1"), vault=vault)
    login, status = resolve_login("example.com", "nobody", vault=vault)
    assert status == "no_credential"
    assert login is None


def test_remove_login(vault):
    add_login(WebLogin(domain="example.com", username="bob", password="p1"), vault=vault)
    assert remove_login("example.com", "bob", vault=vault) is True
    assert get_login("example.com", "bob", vault=vault) is None


def test_login_label_shape():
    assert login_label("https://Example.com/x", "a@b.com") == "web/example.com/a@b.com"
    assert login_label("example.com", "a/b") == "web/example.com/a_b"


# ---------------------------------------------------------------------------
# SessionState round-trip
# ---------------------------------------------------------------------------


def test_session_roundtrip(vault):
    storage = {
        "cookies": [{"name": "sid", "value": "abc123", "domain": "example.com"}],
        "origins": [
            {"origin": "https://example.com",
             "localStorage": [{"name": "token", "value": "xyz"}]}
        ],
    }
    save_session("https://example.com", storage, username="bob", vault=vault)

    got = get_session("example.com", "bob", vault=vault)
    assert got is not None
    assert isinstance(got, SessionState)
    assert got.storage_state["cookies"][0]["value"] == "abc123"
    assert got.captured_at is not None  # stamped on save
    assert got.domain == "example.com"


def test_session_list_has_no_cookie_values(vault):
    save_session("example.com", {"cookies": [{"name": "sid", "value": "secret"}]},
                 username="bob", vault=vault)
    sessions = list_sessions(vault=vault)
    assert len(sessions) == 1
    # metadata summary carries no cookie values
    assert "secret" not in str(sessions[0])
    assert sessions[0]["domain"] == "example.com"


def test_session_default_account(vault):
    save_session("example.com", {"cookies": []}, vault=vault)
    assert get_session("example.com", vault=vault) is not None
    assert remove_session("example.com", vault=vault) is True
    assert get_session("example.com", vault=vault) is None


def test_get_session_finds_username_labelled_by_domain(vault):
    # Regression: a session saved WITH an account (label web-session/<host>/<user>)
    # used to be invisible to a username-less get_session(domain), which probed
    # web-session/<host>/default. It must now be found by the host alone.
    save_session("epicgames.com", {"cookies": [{"name": "EPIC_SSO", "value": "x"}]},
                 username="nevahudo", vault=vault)
    got = get_session("epicgames.com", vault=vault)  # no username
    assert got is not None
    assert got.username == "nevahudo"


def test_get_session_pinned_username_never_falls_back(vault):
    # A pinned account must resolve exactly — never a different account's session.
    save_session("shop.com", {"cookies": []}, username="alice", vault=vault)
    assert get_session("shop.com", "bob", vault=vault) is None
    assert get_session("shop.com", "alice", vault=vault) is not None


def test_get_session_domain_fallback_ignores_other_hosts(vault):
    save_session("other.com", {"cookies": []}, username="x", vault=vault)
    assert get_session("epicgames.com", vault=vault) is None
