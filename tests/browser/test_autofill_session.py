"""auto_login session-first behaviour — restore a vaulted session *before*
requiring a stored credential, so a session-only login (e.g. games/Epic: a
captured session but no saved password) authenticates instead of bailing at
``no_credential``. Uses an isolated vault; the controller is a fake."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from navig.browser import autofill
from navig.vault import save_session
from navig.vault.core import Vault

pytestmark = pytest.mark.integration


@pytest.fixture
def vault():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        v = Vault(vault_dir=Path(tmp))
        try:
            yield v
        finally:
            v.store().close()


class _FakeController:
    """Minimal BrowserController stand-in for the session-restore path."""

    def __init__(self, url: str):
        self._url = url
        self.restored = None
        self.reloaded = False

    async def get_url(self) -> str:
        return self._url

    async def restore_storage_state(self, state) -> None:
        self.restored = state

    async def reload(self) -> None:
        self.reloaded = True

    async def wait_for_stable(self) -> None:
        pass


async def test_session_first_restores_without_a_credential(vault, monkeypatch):
    save_session(
        "epicgames.com",
        {"cookies": [{"name": "EPIC_SSO", "value": "x", "domain": "epicgames.com"}]},
        username="nevahudo",
        vault=vault,
    )

    async def _yes(_controller):
        return True

    monkeypatch.setattr(autofill, "_is_logged_in", _yes)
    ctrl = _FakeController("https://epicgames.com/")
    res = await autofill.auto_login(ctrl, domain="epicgames.com", vault=vault)

    assert res["status"] == "session_restored"
    assert res["username"] == "nevahudo"
    assert ctrl.restored is not None and ctrl.reloaded is True  # session injected + reloaded


async def test_no_session_no_credential_still_bails(vault, monkeypatch):
    async def _no(_controller):
        return False

    monkeypatch.setattr(autofill, "_is_logged_in", _no)
    ctrl = _FakeController("https://epicgames.com/")
    res = await autofill.auto_login(ctrl, domain="epicgames.com", vault=vault)

    assert res["status"] == "no_credential"
    assert ctrl.restored is None  # nothing to restore


async def test_session_restore_attempted_but_falls_through_when_not_authed(vault, monkeypatch):
    # A session exists but doesn't authenticate (_is_logged_in False): we must
    # ATTEMPT the restore yet fall THROUGH — never silently return session_restored.
    save_session("shop.com", {"cookies": [{"name": "sid", "value": "x"}]},
                 username="alice", vault=vault)

    async def _no(_controller):
        return False

    monkeypatch.setattr(autofill, "_is_logged_in", _no)
    ctrl = _FakeController("https://shop.com/login")
    res = await autofill.auto_login(ctrl, domain="shop.com", vault=vault)

    assert res["status"] != "session_restored"
    assert res["status"] == "no_credential"  # fell through (no login form-fill possible)
    assert ctrl.restored is not None  # the restore WAS attempted
