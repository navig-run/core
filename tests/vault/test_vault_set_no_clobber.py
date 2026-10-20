"""`navig vault set provider/sub_key` must never overwrite the provider's credential.

Found 2026-10-08: `navig vault set schema/architect_seal_key <key>` normalised
`architect_seal_key` to `api_key` and merged it into the EXISTING `schema`
credential — silently replacing an unrelated secret (an SSR HMAC) and its label.
Only a backup of the vault database file got it back.

A slash path naming its own sub-key is a separate secret at that path; a
credential's own fields (api_key/token/password/secret) still update it; and a
rotation keeps the credential's label unless one is asked for.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from navig.commands.vault import vault_app

pytestmark = pytest.mark.integration

runner = CliRunner()


def _vault_with_existing_provider() -> MagicMock:
    v = MagicMock()
    existing = MagicMock()
    existing.id = "cred-1"
    existing.data = {"api_key": "the-other-secret"}
    # Every real Credential carries its profile; `vault set` only updates a
    # credential stored under the requested profile (see the real-vault
    # regression in tests/regression/test_vault_profile_writes_real_vault.py).
    existing.profile_id = "default"
    v.get.return_value = existing
    return v


def test_a_custom_sub_key_never_touches_the_provider_credential():
    v = _vault_with_existing_provider()
    with patch("navig.commands.vault._vault_mod.get_vault", return_value=v):
        res = runner.invoke(vault_app, ["set", "schema/architect_seal_key", "NEW"])
    assert res.exit_code == 0, res.output
    v.update.assert_not_called()
    v.add.assert_not_called()
    v.put.assert_called_once_with(
        "schema/architect_seal_key", json.dumps({"value": "NEW"}).encode()
    )


def test_a_credential_field_still_updates_the_credential_and_keeps_its_label():
    v = _vault_with_existing_provider()
    with patch("navig.commands.vault._vault_mod.get_vault", return_value=v):
        res = runner.invoke(vault_app, ["set", "schema/api_key", "ROTATED"])
    assert res.exit_code == 0, res.output
    v.update.assert_called_once_with("cred-1", data={"api_key": "ROTATED"}, label=None)


def test_an_explicit_label_is_still_applied():
    v = _vault_with_existing_provider()
    with patch("navig.commands.vault._vault_mod.get_vault", return_value=v):
        res = runner.invoke(vault_app, ["set", "schema/api_key", "ROTATED", "--label", "New name"])
    assert res.exit_code == 0, res.output
    v.update.assert_called_once_with("cred-1", data={"api_key": "ROTATED"}, label="New name")


def test_get_reads_a_custom_sub_key_even_when_the_provider_exists():
    """It used to read the provider's (absent) field and exit 1 with nothing."""
    v = _vault_with_existing_provider()
    with (
        patch("navig.commands.vault._vault_mod.get_vault", return_value=v),
        patch("navig.vault.core.reveal_secret", return_value="NEW"),
    ):
        res = runner.invoke(vault_app, ["get", "schema/architect_seal_key", "--raw"])
    assert res.exit_code == 0, res.output
    assert res.output.strip() == "NEW"
