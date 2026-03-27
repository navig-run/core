"""
NAVIG Vault Commands

CLI commands for managing the unified credentials vault.
Supports adding, listing, editing, deleting, testing, and cloning credentials.
"""

import json
import sys

import typer

from navig.lazy_loader import lazy_import

# Lazy imports – heavy deps (rich, cryptography) deferred until command runs
_ch = lazy_import("navig.console_helper")
_vault_mod = lazy_import("navig.vault")
_validators_mod = lazy_import("navig.vault.validators")

cred_app = typer.Typer(name="cred", help="Manage credentials in the vault")
profile_app = typer.Typer(name="profile", help="Manage credential profiles")

# ── Provider shortcuts: auto-detect type, data key, and label ─────────────
# Maps provider aliases → (canonical_provider, credential_type, data_key, default_label)
PROVIDER_DEFAULTS = {
    # AI providers
    "openai": ("openai", "api_key", "api_key", "OpenAI"),
    "openrouter": ("openrouter", "api_key", "api_key", "OpenRouter"),
    "anthropic": ("anthropic", "api_key", "api_key", "Anthropic"),
    "groq": ("groq", "api_key", "api_key", "Groq"),
    "github_models": ("github_models", "token", "token", "GitHub Models"),
    "github-models": ("github_models", "token", "token", "GitHub Models"),
    "copilot": ("github_models", "token", "token", "GitHub Copilot Models"),
    # VCS
    "github": ("github", "token", "token", "GitHub"),
    "gitlab": ("gitlab", "token", "token", "GitLab"),
    # Generic
    "telegram": ("telegram", "token", "token", "Telegram Bot"),
}


def _console():
    """Return a Rich Console instance (created on first call)."""
    from rich.console import Console

    return Console()


def _rprint(*args, **kwargs):
    """Rich print (loaded on first call)."""
    from rich import print as _rp

    _rp(*args, **kwargs)


def _Table(*args, **kwargs):
    """Rich Table constructor (loaded on first call)."""
    from rich.table import Table

    return Table(*args, **kwargs)


# ============================================================================
# CREDENTIAL COMMANDS
# ============================================================================


@cred_app.command("list")
def list_credentials(
    provider: str | None = typer.Option(None, "--provider", "-p", help="Filter by provider"),
    profile: str | None = typer.Option(None, "--profile", "-P", help="Filter by profile ID"),
    show_disabled: bool = typer.Option(False, "--disabled", "-d", help="Show disabled credentials"),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format"),
):
    """List credentials in the vault."""
    vault = _vault_mod.get_vault()
    creds = vault.list(provider=provider, profile_id=profile)

    if not show_disabled:
        creds = [c for c in creds if c.enabled]

    if json_output:
        import dataclasses

        _rprint(json.dumps([dataclasses.asdict(c) for c in creds], default=str))
        return

    if not creds:
        if provider or profile:
            _ch.warning("No credentials found matching filters.")
        else:
            _ch.warning("Vault is empty.")
            _ch.info("Use 'navig cred add' to add a credential.")
        return

    table = _Table(title="NAVIG Credentials Vault")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Enabled", justify="center")
    table.add_column("Provider", style="green")
    table.add_column("Profile", style="magenta")
    table.add_column("Type", style="yellow")
    table.add_column("Label")
    table.add_column("Last Used", style="dim")

    for c in creds:
        status = "✅" if c.enabled else "❌"
        last_used = c.last_used_at.strftime("%Y-%m-%d %H:%M") if c.last_used_at else "-"

        table.add_row(
            c.id,
            status,
            c.provider,
            c.profile_id,
            c.credential_type.value,
            c.label,
            last_used,
        )

    con = _console()
    con.print(table)

    active_profile = vault.get_active_profile()
    con.print(f"[dim]Active Profile: [bold]{active_profile}[/bold][/dim]")


@cred_app.command("add")
def add_credential(
    provider: str = typer.Argument(
        ..., help="Provider name (openai, github_models, openrouter, etc.)"
    ),
    credential_type: str = typer.Option(
        None, "--type", "-t", help="Credential type (auto-detected if omitted)"
    ),
    profile: str = typer.Option("default", "--profile", "-P", help="Profile namespace"),
    label: str = typer.Option(None, "--label", "-l", help="Human-readable label"),
    api_key: str = typer.Option(None, "--key", help="API key value"),
    token: str = typer.Option(None, "--token", help="Token value"),
    password: str = typer.Option(None, "--password", help="Password value"),
    email: str = typer.Option(None, "--email", help="Email address (for metadata)"),
    from_stdin: bool = typer.Option(
        False, "--stdin", help="Read secret from stdin (pipe-friendly, no history)"
    ),
    interactive: bool = typer.Option(
        True, "--interactive/--no-interactive", "-i/-I", help="Prompt for secrets"
    ),
):
    """Add a new credential to the vault.

    \b
    Smart defaults — just provide the provider name:
      navig cred add github_models   # prompts securely for token
      navig cred add openrouter      # prompts securely for API key
      navig cred add copilot         # alias for github_models

    \b
    Pipe-friendly (no shell history):
      cat ~/token.txt | navig cred add github_models --stdin
      echo $TOKEN | navig cred add openai --stdin
    """
    vault = _vault_mod.get_vault()

    # ── Resolve provider defaults ─────────────────────────────
    defaults = PROVIDER_DEFAULTS.get(provider.lower())
    if defaults:
        canonical, default_type, default_key, default_label = defaults
        provider = canonical
        if credential_type is None:
            credential_type = default_type
        if label is None:
            label = default_label
    else:
        if credential_type is None:
            credential_type = "api_key"

    # ── Read from stdin if requested ──────────────────────────
    if from_stdin:
        if not sys.stdin.isatty():
            secret = sys.stdin.read().strip()
        else:
            _ch.error(
                "--stdin requires piped input (e.g. echo TOKEN | navig cred add provider --stdin)"
            )
            raise typer.Exit(1)
        if not secret:
            _ch.error("No data received from stdin.")
            raise typer.Exit(1)
        # Assign to the correct key based on type
        if credential_type == "token":
            token = secret
        elif credential_type == "api_key":
            api_key = secret
        else:
            password = secret

    # Determine data payload based on type
    data = {}

    if interactive and not from_stdin and not (api_key or token or password):
        # Prompt for secret if not provided — hidden input, no shell history
        if credential_type == "api_key":
            api_key = typer.prompt("Enter API Key", hide_input=True)
        elif credential_type == "token":
            token = typer.prompt("Enter Token", hide_input=True)
        elif credential_type == "email":
            if not email:
                email = typer.prompt("Email Address")
            password = typer.prompt("Enter App Password", hide_input=True)

    if api_key:
        data["api_key"] = api_key
    if token:
        data["token"] = token
    if password:
        data["password"] = password

    if not data:
        _ch.error(
            "No secret data provided. Use --key, --token, --password, --stdin, or interactive mode."
        )
        raise typer.Exit(1)

    # Metadata
    metadata = {}
    if email:
        metadata["email"] = email

    try:
        cred_id = vault.add(
            provider=provider,
            credential_type=credential_type,
            data=data,
            profile_id=profile,
            label=label,
            metadata=metadata,
        )
        _ch.success(f"Credential added successfully! ID: {cred_id}")

        # Ask to test immediately (skip in non-interactive / stdin mode)
        if interactive and not from_stdin and _ch.confirm_action("Test this credential now?"):
            test_credential(cred_id)

    except Exception as e:
        _ch.error(f"Failed to add credential: {e}")
        raise typer.Exit(1) from e


@cred_app.command("show")
def show_credential(
    credential_id: str = typer.Argument(..., help="Credential ID"),
    reveal: bool = typer.Option(False, "--reveal", help="Reveal secret values (DANGER!)"),
):
    """Show details of a credential."""
    vault = _vault_mod.get_vault()
    cred = vault.get_by_id(credential_id)

    if not cred:
        _ch.error(f"Credential {credential_id} not found")
        raise typer.Exit(1)

    con = _console()
    con.print(f"[bold cyan]Credential Details: {cred.id}[/bold cyan]")
    con.print(f"Provider: [green]{cred.provider}[/green]")
    con.print(f"Profile:  [magenta]{cred.profile_id}[/magenta]")
    con.print(f"Type:     {cred.credential_type.value}")
    con.print(f"Label:    {cred.label}")
    con.print(f"Enabled:  {'✅' if cred.enabled else '❌'}")
    con.print(f"Created:  {cred.created_at}")
    con.print(f"Updated:  {cred.updated_at}")
    con.print(f"Used:     {cred.last_used_at or 'Never'}")

    con.print("\n[bold]Metadata:[/bold]")
    _rprint(cred.metadata)

    con.print("\n[bold]Data:[/bold]")
    if reveal:
        _ch.warning("Revealing secrets!")
        _rprint(cred.data)
    else:
        # Show keys but mask values
        masked = dict.fromkeys(cred.data.keys(), "***")
        _rprint(masked)
        _ch.info("Use --reveal to see secret values")


@cred_app.command("edit")
def edit_credential(
    credential_id: str = typer.Argument(..., help="Credential ID"),
    label: str = typer.Option(None, "--label", "-l", help="New label"),
    api_key: str = typer.Option(None, "--key", help="New API key value"),
    token: str = typer.Option(None, "--token", help="New token value"),
    password: str = typer.Option(None, "--password", help="New password value"),
):
    """Edit an existing credential."""
    vault = _vault_mod.get_vault()

    data = {}
    if api_key:
        data["api_key"] = api_key
    if token:
        data["token"] = token
    if password:
        data["password"] = password

    if not (label or data):
        _ch.warning("Nothing to update.")
        return

    if vault.update(credential_id, data=data if data else None, label=label):
        _ch.success(f"Credential {credential_id} updated.")
    else:
        _ch.error(f"Credential {credential_id} not found.")
        raise typer.Exit(1)


@cred_app.command("delete")
def delete_credential(
    credential_id: str = typer.Argument(..., help="Credential ID"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Delete a credential permanently."""
    vault = _vault_mod.get_vault()
    cred = vault.get_by_id(credential_id)

    if not cred:
        _ch.error(f"Credential {credential_id} not found")
        raise typer.Exit(1)

    if not force:
        if not _ch.confirm_action(f"Delete credential {cred.label} ({cred.id})?"):
            raise typer.Abort()

    if vault.delete(credential_id):
        _ch.success(f"Credential {credential_id} deleted.")
    else:
        _ch.error("Failed to delete credential.")


@cred_app.command("test")
def test_credential(
    target: str = typer.Argument(..., help="Credential ID OR Provider Name"),
    profile: str | None = typer.Option(
        None, "--profile", "-P", help="Profile (if target is provider)"
    ),
):
    """Test a credential against the provider API."""
    vault = _vault_mod.get_vault()

    # Check if target looks like a UUID (8 chars)
    is_id = len(target) == 8

    con = _console()
    con.print(f"Running validation for [cyan]{target}[/cyan]...")

    if is_id:
        result = vault.test(target)
    else:
        result = vault.test_provider(target, profile_id=profile)

    if result.success:
        _ch.success("Validation successful!")
        con.print(f"[green]{result.message}[/green]")
        if result.details:
            _rprint(result.details)
    else:
        _ch.error("Validation failed.")
        con.print(f"[red]{result.message}[/red]")
        if result.details:
            _rprint(result.details)
        raise typer.Exit(1)


@cred_app.command("disable")
def disable_credential(credential_id: str = typer.Argument(..., help="Credential ID")):
    """Disable a credential."""
    vault = _vault_mod.get_vault()
    if vault.disable(credential_id):
        _ch.success(f"Credential {credential_id} disabled.")
    else:
        _ch.error(f"Credential {credential_id} not found.")


@cred_app.command("enable")
def enable_credential(credential_id: str = typer.Argument(..., help="Credential ID")):
    """Enable a credential."""
    vault = _vault_mod.get_vault()
    if vault.enable(credential_id):
        _ch.success(f"Credential {credential_id} enabled.")
    else:
        _ch.error(f"Credential {credential_id} not found.")


@cred_app.command("clone")
def clone_credential(
    credential_id: str = typer.Argument(..., help="Source Credential ID"),
    profile: str = typer.Argument(..., help="Target Profile ID"),
    label: str | None = typer.Option(None, "--label", "-l", help="New label"),
):
    """Clone a credential to a different profile."""
    vault = _vault_mod.get_vault()
    new_id = vault.clone(credential_id, profile, label)

    if new_id:
        _ch.success(f"Credential cloned to profile '{profile}'. New ID: {new_id}")
    else:
        _ch.error(f"Source credential {credential_id} not found.")


@cred_app.command("providers")
def list_providers():
    """List supported providers with built-in validation."""
    providers = _validators_mod.list_supported_validators()
    con = _console()
    con.print("Supported Providers (with validation):")
    for p in providers:
        con.print(f"  • {p}")


@cred_app.command("audit")
def show_audit_log(
    credential_id: str | None = typer.Argument(None, help="Optional Credential ID"),
    limit: int = typer.Option(50, "--limit", "-n", help="Number of entries"),
):
    """Show audit log for credentials."""
    vault = _vault_mod.get_vault()
    logs = vault.get_audit_log(credential_id, limit)

    table = _Table(title="Credential Audit Log")
    table.add_column("Time", style="dim")
    table.add_column("Credential", style="cyan")
    table.add_column("Action", style="bold")
    table.add_column("Accessed By")

    for log in logs:
        action_style = (
            "green"
            if log["action"] in ("created", "enabled")
            else (
                "red"
                if log["action"] in ("deleted", "disabled")
                else "yellow"
                if log["action"] == "updated"
                else "white"
            )
        )

        table.add_row(
            log["timestamp"],
            log["credential_id"],
            f"[{action_style}]{log['action']}[/{action_style}]",
            log["accessed_by"],
        )

    _console().print(table)


# ============================================================================
# PROFILE COMMANDS
# ============================================================================


@profile_app.command("list")
def list_profiles():
    """List all credential profiles."""
    vault = _vault_mod.get_vault()
    profiles = vault.list_profiles()
    active = vault.get_active_profile()

    if active not in profiles:
        profiles.append(active)

    con = _console()
    con.print("Available Profiles:")
    for p in profiles:
        is_active = p == active
        marker = "⭐" if is_active else "  "
        style = "bold green" if is_active else "white"
        con.print(f"{marker} [{style}]{p}[/{style}]")


@profile_app.command("use")
def use_profile(profile_id: str = typer.Argument(..., help="Profile ID to activate")):
    """Set the active profile."""
    vault = _vault_mod.get_vault()
    vault.set_active_profile(profile_id)
    _ch.success(f"Active profile set to: {profile_id}")
