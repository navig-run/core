"""
`navig vault audit` must render whichever store wrote the log.

The v2 vault store returns ``ts`` / ``item_id`` / ``actor``; the command read
only the legacy ``timestamp`` / ``credential_id`` / ``accessed_by`` names and
crashed with ``KeyError: 'timestamp'`` on every v2 vault.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from navig.commands.vault import _audit_row, vault_app

runner = CliRunner()

V2_ROW = {
    "id": 7,
    "ts": "2026-10-10T05:05:00+00:00",
    "item_id": "cb448419",
    "action": "accessed",
    "actor": "user",
    "detail": None,
}
LEGACY_ROW = {
    "timestamp": "2026-03-29T20:05:04",
    "credential_id": "b99b81af",
    "action": "created",
    "accessed_by": "cli",
}


def test_audit_row_reads_v2_columns():
    assert _audit_row(V2_ROW) == {
        "timestamp": "2026-10-10T05:05:00+00:00",
        "credential_id": "cb448419",
        "action": "accessed",
        "accessed_by": "user",
    }


def test_audit_row_reads_legacy_columns():
    assert _audit_row(LEGACY_ROW)["accessed_by"] == "cli"
    assert _audit_row(LEGACY_ROW)["credential_id"] == "b99b81af"


def test_audit_row_tolerates_missing_fields():
    assert _audit_row({"action": "deleted"}) == {
        "timestamp": "",
        "credential_id": "",
        "action": "deleted",
        "accessed_by": "",
    }


def test_audit_command_renders_v2_log():
    vault = MagicMock()
    vault.get_audit_log.return_value = [V2_ROW, LEGACY_ROW]
    vault_mod = MagicMock()
    vault_mod.get_vault.return_value = vault
    with patch("navig.commands.vault._vault_mod", vault_mod):
        result = runner.invoke(vault_app, ["audit"])
    assert result.exit_code == 0, result.output
    assert "cb448419" in result.output
    assert "b99b81af" in result.output
