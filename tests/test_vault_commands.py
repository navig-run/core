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


def test_vault_cli_info_basic(vault):
    """info command shows identity panel and masked secret keys."""
    runner.invoke(
        app,
        [
            "cred",
            "add",
            "openai",
            "--key",
            "sk-test-info-key",
            "--profile",
            "info-test",
            "--no-interactive",
        ],
    )

    creds = vault.list_creds(provider="openai")
    target = next((c for c in creds if c.profile_id == "info-test"), None)
    assert target is not None

    result = runner.invoke(app, ["cred", "info", target.id[:8]])
    assert result.exit_code == 0, result.output
    # Identity section
    assert "openai" in result.output
    assert "info-test" in result.output
    # Secret keys masked
    assert "api_key" in result.output
    assert "sk-test-info-key" not in result.output  # not revealed
    # Validation section present
    assert "Validation" in result.output or "validation" in result.output.lower()
    # Audit section present
    assert "Audit" in result.output or "created" in result.output

    # Cleanup
    vault.delete(target.id)


def test_vault_cli_info_reveal(vault):
    """info --reveal exposes actual secret value."""
    runner.invoke(
        app,
        [
            "cred",
            "add",
            "openai",
            "--key",
            "sk-reveal-me",
            "--profile",
            "reveal-test",
            "--no-interactive",
        ],
    )

    creds = vault.list_creds(provider="openai")
    target = next((c for c in creds if c.profile_id == "reveal-test"), None)
    assert target is not None

    result = runner.invoke(app, ["cred", "info", target.id[:8], "--reveal"])
    assert result.exit_code == 0, result.output
    assert "sk-reveal-me" in result.output

    vault.delete(target.id)


def test_vault_cli_info_unknown_id():
    """info on a non-existent ID exits 1 with 'not found' message."""
    result = runner.invoke(app, ["cred", "info", "00000000"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower() or "no credential" in result.output.lower()


def test_vault_list_shows_short_id(vault):
    """`navig vault list` should display 8-char IDs, not full UUIDs."""
    add_result = runner.invoke(
        app,
        [
            "cred",
            "add",
            "openai",
            "--key",
            "sk-short-id-check",
            "--label",
            "Vault List Short ID",
            "--profile",
            "short-id",
            "--no-interactive",
        ],
    )
    assert add_result.exit_code == 0, add_result.output

    creds = vault.list_creds(provider="openai")
    created = next((c for c in creds if c.profile_id == "short-id"), None)
    assert created is not None

    list_result = runner.invoke(app, ["vault", "list", "--provider", "openai"])
    assert list_result.exit_code == 0, list_result.output
    assert created.id[:8] in list_result.output
    assert created.id not in list_result.output

    vault.delete(created.id)


def test_vault_cli_edit(vault):
    """vault edit updates the label of an existing credential."""
    runner.invoke(
        app,
        [
            "vault", "add", "openai", "--key", "sk-edit-me",
            "--label", "Edit Before", "--profile", "edit-test", "--no-interactive",
        ],
    )
    creds = vault.list_creds(provider="openai")
    target = next((c for c in creds if c.profile_id == "edit-test"), None)
    assert target is not None

    result = runner.invoke(app, ["vault", "edit", target.id, "--label", "Edit After"])
    assert result.exit_code == 0, result.output
    assert "updated" in result.output.lower()

    updated = vault.get_by_id(target.id)
    assert updated is not None
    assert updated.label == "Edit After"

    vault.delete(target.id)


def test_vault_cli_disable_enable(vault):
    """disable/enable toggle the enabled flag on a credential."""
    runner.invoke(
        app,
        [
            "vault", "add", "openai", "--key", "sk-toggle",
            "--profile", "toggle-test", "--no-interactive",
        ],
    )
    creds = vault.list_creds(provider="openai")
    target = next((c for c in creds if c.profile_id == "toggle-test"), None)
    assert target is not None

    result = runner.invoke(app, ["vault", "disable", target.id])
    assert result.exit_code == 0, result.output
    assert "disabled" in result.output.lower()
    assert vault.get_by_id(target.id).enabled is False

    result = runner.invoke(app, ["vault", "enable", target.id])
    assert result.exit_code == 0, result.output
    assert "enabled" in result.output.lower()
    assert vault.get_by_id(target.id).enabled is True

    vault.delete(target.id)


def test_vault_cli_disable_nonexistent():
    """disable on an unknown ID exits with an error."""
    result = runner.invoke(app, ["vault", "disable", "00000000000000000000000000000000"])
    assert result.exit_code != 0 or "not found" in result.output.lower()


def test_vault_cli_remove(vault):
    """vault remove deletes by provider path (not credential ID)."""
    runner.invoke(
        app,
        [
            "vault", "add", "openai", "--key", "sk-remove-path",
            "--profile", "remove-test", "--no-interactive",
        ],
    )
    creds = vault.list_creds(provider="openai")
    target = next((c for c in creds if c.profile_id == "remove-test"), None)
    assert target is not None

    result = runner.invoke(
        app, ["vault", "remove", "openai", "--profile", "remove-test", "--force"]
    )
    assert result.exit_code == 0, result.output
    assert "deleted" in result.output.lower()
    assert vault.get_by_id(target.id) is None


def test_vault_cli_remove_nonexistent():
    """vault remove on a missing provider/profile exits with error."""
    result = runner.invoke(
        app, ["vault", "remove", "openai", "--profile", "no-such-profile-xyz", "--force"]
    )
    assert result.exit_code != 0


def test_vault_cli_providers():
    """vault providers lists at least one known supported provider."""
    result = runner.invoke(app, ["vault", "providers"])
    assert result.exit_code == 0, result.output
    assert len(result.output.strip()) > 0


def test_vault_cli_show_legacy(vault):
    """vault show (legacy) works and prints basic credential fields."""
    runner.invoke(
        app,
        [
            "vault", "add", "openai", "--key", "sk-show-legacy",
            "--profile", "show-legacy-test", "--no-interactive",
        ],
    )
    creds = vault.list_creds(provider="openai")
    target = next((c for c in creds if c.profile_id == "show-legacy-test"), None)
    assert target is not None

    result = runner.invoke(app, ["vault", "show", target.id])
    assert result.exit_code == 0, result.output
    assert "openai" in result.output
    assert "show-legacy-test" in result.output
    assert "sk-show-legacy" not in result.output  # masked by default
    assert "vault info" in result.output  # upgrade hint present

    vault.delete(target.id)


def test_vault_cli_show_legacy_nonexistent():
    """vault show on an unknown ID exits with error."""
    result = runner.invoke(app, ["vault", "show", "00000000000000000000000000000000"])
    assert result.exit_code != 0
    assert "not found" in result.output.lower()
