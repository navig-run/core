"""`navig matrix login` could never read a stored credential — it always saw an empty vault.

``navig.commands.matrix._get_credential`` was four separate faults stacked behind one
``except Exception: return {}``, so it could only ever return ``{}``:

  1. ``from navig.vault.core import CredentialsVault`` — that name is not in
     ``navig.vault.core``; it is an alias exported from ``navig.vault``. The import alone
     raised, so nothing below it had ever run.
  2. ``vault.list_by_provider("matrix")`` — no such method on ``Vault``.
  3. ``vault.get(c.id)`` — ``Vault.get`` takes ``(provider, profile_id)``, not an id.
  4. (the write site) ``vault.update(full)`` — ``update`` takes
     ``(credential_id, data=...)`` and merges; it never accepted a ``Credential``.

User-visible effect: you store Matrix credentials in the vault, `navig matrix login`
ignores them completely and falls back to config, and the access token it prints is never
persisted back. Nothing is logged, because the broad `except` was the only thing standing.

These tests drive a REAL ``Vault``. Mocking it would defend nothing here: every fault was
a wrong call shape, and a mock answers whatever shape you use.
"""

from __future__ import annotations

import pytest

from navig.commands.matrix import _get_credential


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """A real, empty vault inside an isolated config dir."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    import navig.vault.core as vc

    monkeypatch.setattr(vc, "_vault", None)  # force a fresh singleton under tmp
    from navig.vault import get_vault

    v = get_vault()
    try:
        yield v
    finally:
        v._store.close()  # Windows will not delete tmp_path while sqlite holds it


def test_a_stored_credential_is_returned(vault) -> None:
    """THE REGRESSION: this returned ``{}`` for a credential sitting right there."""
    vault.add(
        "matrix",
        credential_type="password",
        data={"homeserver": "https://hs.example", "user_id": "@bot:example", "password": "pw"},
        profile_id="default",
    )

    got = _get_credential("default")

    assert got, "a stored Matrix credential came back empty — login silently ignores the vault"
    assert got["user_id"] == "@bot:example"
    assert got["password"] == "pw"
    assert got["homeserver"] == "https://hs.example"


def test_a_named_profile_resolves_to_its_own_credential(vault) -> None:
    """The profile argument must actually route — it was never passed to the vault at all."""
    vault.add("matrix", credential_type="password",
              data={"user_id": "@default:example"}, profile_id="default")
    vault.add("matrix", credential_type="password",
              data={"user_id": "@work:example"}, profile_id="work")

    assert _get_credential("work")["user_id"] == "@work:example"
    assert _get_credential("default")["user_id"] == "@default:example"


def test_an_empty_vault_yields_no_credential(vault) -> None:
    """Anti-vacuity: ``{}`` must still be the answer when there genuinely is nothing.

    Without this, a `_get_credential` that returned some fixed dict would pass the test
    above — and "always returns {}" is exactly the bug, so the empty case has to be pinned
    as a real answer rather than the default one.
    """
    assert _get_credential("default") == {}


def test_a_lookup_does_not_conjure_a_vault(tmp_path, monkeypatch) -> None:
    """A credential *lookup* must not create a vault on a machine that has none.

    Constructing a ``Vault`` opens sqlite and mkdirs its directory, which is how a
    read-only ``navig init`` status probe wrote vault.db into the operator's project
    (34002599a). ``vault_exists()`` is the pure LOOK that exists for this.
    """
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    import navig.vault.core as vc

    monkeypatch.setattr(vc, "_vault", None)

    assert _get_credential("default") == {}

    from navig.vault.core import VaultStore

    assert not (tmp_path / "vault" / VaultStore.DB_FILE).exists(), (
        "a credential lookup created a vault database"
    )


def test_a_vault_read_failure_is_reported_not_swallowed(vault, capsys) -> None:
    """The broad `except` is what hid all four faults for as long as it did.

    Degrading to config is correct (login can still work), but doing it in silence is how
    a wrong call shape survives: the operator sees a normal login and never learns their
    stored credential was not used.
    """
    from navig.vault.core import Vault

    def _boom(*args, **kwargs):
        raise RuntimeError("vault is locked")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Vault, "get", _boom)
        assert _get_credential("default") == {}

    out = capsys.readouterr().out
    assert "vault is locked" in out, (
        "a failed vault read was swallowed — indistinguishable from 'no credential stored'"
    )


def test_update_merges_so_saving_the_token_keeps_the_password(vault) -> None:
    """The write site relies on ``update`` MERGING rather than replacing.

    ``login`` saves only ``{"access_token": ...}``. If ``update`` ever replaced the payload
    instead, that one call would silently destroy the stored password — a data-loss bug
    with a success message on screen, so the assumption is pinned here rather than trusted.
    """
    vault.add("matrix", credential_type="password",
              data={"user_id": "@bot:example", "password": "pw"}, profile_id="default")
    cred = vault.get("matrix", profile_id="default")

    assert vault.update(cred.id, data={"access_token": "syt_abc"}) is True

    after = _get_credential("default")
    assert after["access_token"] == "syt_abc"
    assert after["password"] == "pw", "saving the token wiped the rest of the credential"


# ─────────────────────────────────────────────────────────────────────────────
# Two MORE sites behind the same broken import — invisible in the guard's report.
#
# `_dangling_sites()` keys by (file, module, name) with `setdefault`, so every
# occurrence in one file collapses into ONE entry showing only the first line. Repairing
# two of the four sites therefore looked *identical* to repairing none: same key, new line
# number. `navig matrix accounts` and `navig matrix use` were only found by re-running the
# guard after the first fix and reading the new line number.


def test_accounts_lists_a_stored_account(vault, capsys) -> None:
    """`navig matrix accounts` could only ever reach its `except`.

    It printed "Vault not available or no Matrix credentials stored" — a listing command
    reporting an empty vault over a full one.
    """
    from navig.commands.matrix import accounts

    vault.add(
        "matrix",
        credential_type="password",
        data={"password": "pw"},
        profile_id="work",
        label="matrix work",
    )

    accounts()

    out = capsys.readouterr().out
    assert "Vault not available" not in out, "accounts still reports an empty vault"
    assert "work" in out, f"the stored account was not listed:\n{out}"


def test_accounts_survives_an_item_with_no_profile_id_attribute(vault, capsys) -> None:
    """The loop read ``c.profile_id``, which a ``VaultItem`` does not have.

    That line sits OUTSIDE the try/except, so merely repairing the import would have
    swapped a silent no-op for an AttributeError crash on the first operator who actually
    had an account stored. This is the "verify the call shape too" case the guard's own
    failure message warns about.
    """
    from navig.commands.matrix import accounts

    vault.add("matrix", credential_type="password", data={"password": "pw"}, profile_id="default")
    items = vault.list(provider="matrix")
    assert items, "fixture did not store anything"
    assert not hasattr(items[0], "profile_id") or items[0].metadata.get("profile_id") is not None

    accounts()  # must not raise

    assert "Traceback" not in capsys.readouterr().out


def test_use_rejects_a_profile_that_is_not_in_the_vault(vault) -> None:
    """THE REGRESSION for `navig matrix use`: the whole check was dead.

    `except ImportError: pass` swallowed the validation, so any typo was accepted and
    written to config as the active account.
    """
    import typer

    from navig.commands.matrix import use_profile

    vault.add("matrix", credential_type="password", data={"password": "pw"}, profile_id="work")

    with pytest.raises(typer.Exit) as ei:
        use_profile("typo-not-a-profile")
    assert ei.value.exit_code == 1


def test_use_accepts_a_profile_that_is_in_the_vault(vault) -> None:
    """Anti-vacuity: the check must not reject a real profile, and it must persist it."""
    from navig.commands.matrix import use_profile
    from navig.config import get_config_manager, reset_config_manager

    vault.add("matrix", credential_type="password", data={"password": "pw"}, profile_id="work")

    reset_config_manager()
    try:
        use_profile("work")  # must not raise
        assert (
            get_config_manager().get_global_config()["comms"]["matrix"]["credential_id"] == "work"
        )
    finally:
        reset_config_manager()


def test_use_still_works_with_no_vault(tmp_path, monkeypatch) -> None:
    """A config-only Matrix setup must keep working.

    `login` merges plain config, so people legitimately run Matrix without a vault. If the
    revived validation rejected profiles whenever it could not enumerate any, the fix would
    break a working setup — so membership is enforced only when the vault actually holds
    Matrix entries.
    """
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    import navig.vault.core as vc

    monkeypatch.setattr(vc, "_vault", None)
    from navig.commands.matrix import use_profile
    from navig.config import reset_config_manager

    reset_config_manager()
    try:
        use_profile("anything")  # no vault → nothing to verify against → allowed
    finally:
        reset_config_manager()
