"""Writing to one vault profile must never touch another — against a REAL vault.

Found 2026-10-10: ``navig vault set stripe <sandbox key> --profile sandbox`` printed
"Updated credential for stripe" and replaced the DEFAULT stripe credential (a live
org key) with the sandbox key. ``vault.get()`` falls back to the ``default`` profile
when the requested one is empty — correct for a read, and exactly wrong for the
existence check of a write. The same lookup made ``vault add --profile X`` refuse
("already exists") and made ``--force`` delete by provider name, which is always
the default profile's label. Mocked-vault tests could not see any of it: the
fallback lives in the real engine.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from navig.commands.vault import vault_app
from navig.vault.core import Vault

runner = CliRunner()

DEFAULT_KEY = "sk_org_live_DEFAULT"
SANDBOX_KEY = "sk_test_SANDBOX"


@pytest.fixture
def vault(tmp_path):
    v = Vault(tmp_path)
    v.add("stripe", "api_key", {"api_key": DEFAULT_KEY}, profile_id="default", label="Navig")
    try:
        with patch("navig.commands.vault._vault_mod.get_vault", return_value=v):
            yield v
    finally:
        v._store.close()


def _key(v: Vault, profile: str) -> str | None:
    cred = v.get("stripe", profile_id=profile)
    if cred is None or cred.profile_id != profile:
        return None
    return cred.data.get("api_key")


def test_set_into_a_new_profile_leaves_the_default_alone(vault):
    res = runner.invoke(vault_app, ["set", "stripe", SANDBOX_KEY, "--profile", "sandbox"])
    assert res.exit_code == 0, res.output
    assert _key(vault, "default") == DEFAULT_KEY
    assert _key(vault, "sandbox") == SANDBOX_KEY


def test_set_into_an_existing_profile_still_rotates_it(vault):
    res = runner.invoke(vault_app, ["set", "stripe", "sk_org_live_ROTATED"])
    assert res.exit_code == 0, res.output
    assert _key(vault, "default") == "sk_org_live_ROTATED"


def test_add_into_a_new_profile_is_not_refused(vault):
    res = runner.invoke(
        vault_app,
        ["add", "stripe", "--profile", "sandbox", "--key", SANDBOX_KEY, "--no-interactive"],
    )
    assert res.exit_code == 0, res.output
    assert _key(vault, "default") == DEFAULT_KEY
    assert _key(vault, "sandbox") == SANDBOX_KEY


def test_add_force_replaces_only_its_own_profile(vault):
    vault.add("stripe", "api_key", {"api_key": SANDBOX_KEY}, profile_id="sandbox")
    res = runner.invoke(
        vault_app,
        ["add", "stripe", "--profile", "sandbox", "--key", "sk_test_NEW", "--force", "--no-interactive"],
    )
    assert res.exit_code == 0, res.output
    assert _key(vault, "sandbox") == "sk_test_NEW"
    assert _key(vault, "default") == DEFAULT_KEY


def test_delete_of_an_absent_profile_never_deletes_the_default(vault):
    res = runner.invoke(vault_app, ["remove", "stripe", "--profile", "sandbox", "--force"])
    assert res.exit_code == 1, res.output
    assert _key(vault, "default") == DEFAULT_KEY


def test_reads_keep_falling_back_to_the_default(vault):
    """The fallback is right for reads (keys stored before a profile switch) — keep it."""
    cred = vault.get("stripe", profile_id="work")
    assert cred is not None and cred.data["api_key"] == DEFAULT_KEY


def test_get_says_when_it_answers_from_another_profile(vault):
    """The read fallback stays, but is never silent: a caller took the default
    stripe key for the sandbox one because `vault get --profile sandbox` gave no hint."""
    res = runner.invoke(vault_app, ["get", "stripe", "--profile", "sandbox", "--raw"])
    assert res.exit_code == 0, res.output
    assert res.stdout.strip() == DEFAULT_KEY, "--raw stdout stays the bare secret"
    assert "has no 'stripe' credential" in res.stderr
    assert "'default' profile" in res.stderr


def test_get_is_quiet_when_the_profile_has_its_own_key(vault):
    vault.add("stripe", "api_key", {"api_key": SANDBOX_KEY}, profile_id="sandbox")
    res = runner.invoke(vault_app, ["get", "stripe", "--profile", "sandbox", "--raw"])
    assert res.exit_code == 0, res.output
    assert res.stdout.strip() == SANDBOX_KEY
    assert res.stderr == ""
