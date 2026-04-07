"""
Tests for vault CLI commands.
"""

import pytest
from typer.testing import CliRunner

from navig.cli import _register_external_commands, app
from navig.vault import get_vault

# Register external commands (including cred/profile) for test discovery
_register_external_commands(register_all=True)

runner = CliRunner()


@pytest.fixture(scope="module")
def vault():
    return get_vault()


def test_vault_cli_list(vault):
    result = runner.invoke(app, ["cred", "list"])
    assert result.exit_code == 0
    assert "NAVIG Credentials Vault" in result.stdout or "Vault is empty" in result.stdout


def test_vault_cli_add_delete(vault):
    # Clean up any leftover test credentials from previous runs
    for c in vault.list_creds():
        if c.label == "Test CLI Key":
            vault.delete(c.id)

    # Add
    result = runner.invoke(
        app,
        [
            "cred",
            "add",
            "openai",
            "--key",
            "sk-test-cli",
            "--label",
            "Test CLI Key",
            "--no-interactive",
        ],
    )
    assert result.exit_code == 0
    assert "Credential added successfully" in result.stdout

    # Get ID from output (or assume it's last created if using vault object)
    creds = vault.list_creds()
    cred = next((c for c in creds if c.label == "Test CLI Key"), None)
    assert cred is not None

    # List filtered — Rich wraps long labels; check that the table rendered and exit was clean
    result = runner.invoke(app, ["cred", "list", "--provider", "openai"])
    assert result.exit_code == 0
    assert "NAVIG Credentials Vault" in result.stdout

    # Delete
    result = runner.invoke(app, ["cred", "delete", cred.id, "--force"])
    assert result.exit_code == 0
    assert f"Credential {cred.id} deleted" in result.stdout

    # Verify gone
    creds = vault.list()
    assert not any(c.id == cred.id for c in creds)


def test_vault_cli_profile_list():
    result = runner.invoke(app, ["cred-profile", "list"])
    assert result.exit_code == 0
    assert "Available Profiles" in result.stdout
    # Use the actual active profile name to be environment-agnostic
    active = get_vault().get_active_profile()
    assert active in result.stdout


def test_vault_cli_show_nonexistent():
    result = runner.invoke(app, ["cred", "show", "nonexistent"])
    assert result.exit_code != 0
    assert "not found" in result.stdout


def test_vault_cli_activate(vault):
    """activate sets the active flag; short-ID lookup works."""
    runner.invoke(
        app, ["cred", "add", "openai", "--key", "sk-aaa", "--profile", "work", "--no-interactive"]
    )
    runner.invoke(
        app,
        ["cred", "add", "openai", "--key", "sk-bbb", "--profile", "personal", "--no-interactive"],
    )

    creds = vault.list_creds(provider="openai")
    work = next((c for c in creds if c.profile_id == "work"), None)
    assert work is not None

    result = runner.invoke(app, ["cred", "activate", work.id[:8]])
    assert result.exit_code == 0, result.output
    assert "active" in result.output.lower() or work.id[:8] in result.output

    items = {i.id: i for i in vault.list(provider="openai")}
    work_item = next((v for v in items.values() if v.metadata.get("profile_id") == "work"), None)
    assert work_item is not None
    assert work_item.metadata.get("active") is True

    # Cleanup
    for c in vault.list_creds(provider="openai"):
        vault.delete(c.id)


def test_vault_cli_add_firecrawl_rejects_invalid_key(monkeypatch):
    class _BadClient:
        def validate_api_key(self, api_key: str):
            return False, "Invalid Firecrawl API key"

    monkeypatch.setattr("navig.integrations.firecrawl.FirecrawlClient", _BadClient)

    result = runner.invoke(
        app,
        [
            "cred",
            "add",
            "firecrawl",
            "--key",
            "fc-invalid",
            "--label",
            "Bad Firecrawl Key",
            "--no-interactive",
        ],
    )

    assert result.exit_code == 1
    assert "Firecrawl API key rejected" in result.output
