"""
NAVIG Vault Commands

CLI commands for managing the unified credentials vault.
Supports adding, listing, editing, deleting, testing, and cloning credentials.
"""

import json
import re
import sys

import typer

from navig.lazy_loader import lazy_import

# Lazy imports – heavy deps (rich, cryptography) deferred until command runs
_ch = lazy_import("navig.console_helper")
_vault_mod = lazy_import("navig.vault")
_validators_mod = lazy_import("navig.vault.validators")

cred_app = typer.Typer(name="cred", help="Manage credentials in the vault")
profile_app = typer.Typer(name="profile", help="Manage credential profiles")


def _resolve_test_target_mode(vault, target: str, provider: str | None, credential_id: str | None):
    if credential_id and provider:
        raise ValueError("Use either --id or --provider, not both.")

    normalized_target = (target or "").strip()
    normalized_provider = (provider or "").strip()
    normalized_id = (credential_id or "").strip()

    if not normalized_target and not normalized_provider and not normalized_id:
        raise ValueError("Provide a target, --provider, or --id.")

    if normalized_id:
        return "id", normalized_id

    if normalized_provider:
        return "provider", normalized_provider

    if (
        re.fullmatch(r"[0-9a-f]{8}", normalized_target.lower())
        and vault.get_by_id(normalized_target) is not None
    ):
        return "id", normalized_target

    return "provider", normalized_target


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
    # Voice providers
    "deepgram": ("deepgram", "api_key", "api_key", "Deepgram"),
    "elevenlabs": ("elevenlabs", "api_key", "api_key", "ElevenLabs"),
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
    creds = vault.list_creds(provider=provider, profile_id=profile)

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
    table.add_column("", justify="center", no_wrap=True)  # ⭐ active indicator
    table.add_column("Provider", style="green")
    table.add_column("Profile", style="magenta")
    table.add_column("Type", style="yellow")
    table.add_column("Label")
    table.add_column("Last Used", style="dim")

    # Pull raw VaultItems to read the active flag from metadata
    vault_items = {i.id: i for i in vault.list()}

    for c in creds:
        short_id = c.id[:8]
        raw = vault_items.get(c.id)
        is_active = bool(raw and raw.metadata.get("active", False))
        star = "⭐" if is_active else ""
        last_used = c.last_used_at.strftime("%Y-%m-%d %H:%M") if c.last_used_at else "-"

        table.add_row(
            short_id,
            star,
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
    target: str | None = typer.Argument(None, help="Credential ID OR Provider Name"),
    profile: str | None = typer.Option(
        None, "--profile", "-P", help="Profile (if target is provider)"
    ),
    provider: str | None = typer.Option(
        None,
        "--provider",
        help="Force provider mode (fixes ambiguous short names)",
    ),
    credential_id: str | None = typer.Option(
        None,
        "--id",
        help="Force credential ID mode",
    ),
):
    """Test a credential against the provider API."""
    vault = _vault_mod.get_vault()
    try:
        mode, resolved = _resolve_test_target_mode(vault, target or "", provider, credential_id)
    except ValueError as exc:
        _ch.error(str(exc))
        raise typer.Exit(1) from None

    con = _console()
    con.print(f"Running validation for [cyan]{resolved}[/cyan]...")

    try:
        if mode == "id":
            result = vault.test(resolved)
            credential = vault.get_by_id(resolved, caller="vault.test")
        else:
            result = vault.test_provider(resolved, profile_id=profile)
            credential = vault.get(resolved, profile_id=profile, caller="vault.test")
    except Exception as exc:
        exc_name = exc.__class__.__name__
        if exc_name == "VaultEncryptionError":
            _ch.error(
                "Vault decryption failed for this credential. Re-import the key (or reset broken entry) and try again."
            )
            con.print(
                "[yellow]Tip:[/yellow] Use `navig cred list`, delete the broken provider entry, then add it again from your backup .env."
            )
            raise typer.Exit(1) from None
        raise

    if credential is not None:
        tested_at = getattr(result, "tested_at", None)
        tested_at_iso = tested_at.isoformat() if tested_at else None
        update_meta = {
            "validation_success": bool(result.success),
            "validation_message": str(result.message or ""),
        }
        if tested_at_iso:
            update_meta["validation_tested_at"] = tested_at_iso
        if isinstance(getattr(result, "details", None), dict):
            validation_mode = result.details.get("validation_mode")
            if validation_mode:
                update_meta["validation_mode"] = validation_mode
        vault.update(credential.id, metadata=update_meta)

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


@cred_app.command("activate")
def activate_credential(
    credential_id: str = typer.Argument(
        ..., help="Credential ID (or first 8 chars) to mark as active"
    ),
):
    """Set a credential as the active (preferred) one for its provider.

    \b
    When two credentials share a provider (e.g. two openai keys on different
    profiles), this pins which one get_api_key() returns when no profile is
    explicitly specified.

    \b
    Example:
      navig cred activate d08ae821
    """
    vault = _vault_mod.get_vault()

    target_id = credential_id.strip()
    if len(target_id) <= 8:
        all_items = vault.list()
        matches = [i for i in all_items if i.id.startswith(target_id)]
        if not matches:
            _ch.error(f"No credential found with ID starting with '{target_id}'.")
            raise typer.Exit(1)
        if len(matches) > 1:
            _ch.error(
                f"Ambiguous short ID '{target_id}' matches {len(matches)} credentials — use more chars."
            )
            raise typer.Exit(1)
        target_id = matches[0].id

    ok = vault.activate(target_id)
    if not ok:
        _ch.error(f"Credential '{target_id}' not found.")
        raise typer.Exit(1)

    item = next((i for i in vault.list() if i.id == target_id), None)
    provider = item.provider if item else "?"
    _ch.success(f"[{provider}] Credential {target_id[:8]} is now active.")
    _ch.dim("Use 'navig cred list' to confirm  ⭐")


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


# ============================================================================
# VAULT APP — top-level `navig vault` command group
# ============================================================================

vault_app = typer.Typer(
    name="vault",
    help="Manage the NAVIG credentials vault (set, get, list, validate)",
    invoke_without_command=True,
    no_args_is_help=True,
)


def _parse_vault_path(path: str) -> tuple[str, str]:
    """Parse a vault key path into (provider, data_key).

    Supports:
      nvidia/api_key     → ("nvidia", "api_key")
      openai/api_key     → ("openai", "api_key")
      openai             → ("openai", "api_key")  via PROVIDER_DEFAULTS
      openai_api_key     → ("openai", "api_key")  underscore heuristic
    """
    if "/" in path:
        provider, _, data_key = path.partition("/")
        return provider.strip().lower(), data_key.strip().lower()

    lower = path.lower()
    # Known provider shortcut (e.g. "openai", "nvidia")
    if lower in PROVIDER_DEFAULTS:
        canonical, _, default_key, _ = PROVIDER_DEFAULTS[lower]
        return canonical, default_key

    # Heuristic: provider_api_key / provider_token / provider_secret
    for suffix in ("_api_key", "_token", "_secret", "_password"):
        if lower.endswith(suffix):
            provider = lower[: -len(suffix)]
            data_key = suffix.lstrip("_")
            return provider, data_key

    # Fall back: treat whole path as provider with api_key
    return lower, "api_key"


@vault_app.command("set")
def vault_set(
    path: str = typer.Argument(
        ...,
        help="Key path: provider/data_key  (e.g. nvidia/api_key, openai, telegram/token)",
    ),
    value: str = typer.Argument(..., help="Secret value to store"),
    profile: str = typer.Option("default", "--profile", "-P", help="Credential profile"),
    label: str = typer.Option(None, "--label", "-l", help="Human-readable label"),
):
    """Set (add or update) a credential in the vault.

    \b
    Examples:
      navig vault set nvidia/api_key nvapi-xxxx
      navig vault set openai sk-xxxx
      navig vault set telegram/token 123456:ABC
      navig vault set openai_api_key sk-xxxx
    """
    vault = _vault_mod.get_vault()
    provider, data_key = _parse_vault_path(path)

    # Map data_key to the correct field
    if data_key in ("token",):
        cred_type = "token"
    elif data_key in ("password", "secret"):
        cred_type = "password"
    else:
        cred_type = "api_key"
        data_key = "api_key"  # normalise

    data = {data_key: value}

    # Resolve display label from PROVIDER_DEFAULTS if not given
    if label is None:
        defaults = PROVIDER_DEFAULTS.get(provider)
        label = defaults[3] if defaults else provider.title()

    # Check if a credential already exists for this provider+profile → update
    existing = vault.get(provider, profile_id=profile, caller="vault.set")
    if existing is not None:
        vault.update(existing.id, data=data, label=label)
        _ch.success(f"Updated credential for [bold]{provider}[/bold] (ID: {existing.id})")
    else:
        cred_id = vault.add(
            provider=provider,
            credential_type=cred_type,
            data=data,
            profile_id=profile,
            label=label,
        )
        _ch.success(f"Stored credential for [bold]{provider}[/bold] (ID: {cred_id})")

    # Direct label write for dot/slash-notation paths (e.g. telegram.user_id)
    # vault.add() above stores using provider/profile credential model; also store
    # under the exact path label so that get_secret(path) works for path resolvers.
    if "." in path or "/" in path:
        try:
            vault.put(path, json.dumps({"value": value}).encode())
        except Exception:
            pass  # best-effort; the provider/profile record above is the canonical store


@vault_app.command("get")
def vault_get(
    path: str = typer.Argument(..., help="Key path: provider or provider/data_key"),
    reveal: bool = typer.Option(False, "--reveal", "-r", help="Show the actual secret value"),
    profile: str = typer.Option("default", "--profile", "-P", help="Credential profile"),
):
    """Get a credential value from the vault."""
    vault = _vault_mod.get_vault()
    provider, data_key = _parse_vault_path(path)
    cred = vault.get(provider, profile_id=profile, caller="vault.get")

    # For dot-notation or slash paths try a direct label lookup in the unified vault.
    if "." in path or (cred is None and "/" in path):
        try:
            resolved_secret = (vault.get_secret(path) or "").strip()
            if resolved_secret:
                if reveal:
                    _ch.warning("Revealing secret!")
                    _rprint(resolved_secret)
                else:
                    _rprint(
                        f"[dim]{path}:[/dim] {'*' * min(len(resolved_secret), 12)} (use --reveal to show)"
                    )
                return
        except (KeyError, Exception):
            pass  # path not found as label; fall through to provider credential lookup

    if cred is None:
        _ch.error(f"No credential found for provider '{provider}' in profile '{profile}'.")
        _ch.info("Use 'navig vault set <path> <value>' to add one.")
        raise typer.Exit(1)

    secret = cred.data.get(data_key, "")
    if reveal:
        _ch.warning("Revealing secret!")
        _rprint(secret)
    else:
        _rprint(
            f"[dim]{provider}/{data_key}:[/dim] {'*' * min(len(secret), 12)} (use --reveal to show)"
        )


@vault_app.command("list")
def vault_list(
    provider: str = typer.Option(None, "--provider", "-p", help="Filter by provider"),
    profile: str = typer.Option(None, "--profile", "-P", help="Filter by profile"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """List credentials stored in the vault."""
    vault = _vault_mod.get_vault()
    items = vault.list(provider=provider, profile_id=profile)

    if json_output:
        import dataclasses

        _rprint(json.dumps([dataclasses.asdict(i) for i in items], default=str))
        return

    if not items:
        _ch.warning(
            "Vault is empty."
            if not (provider or profile)
            else "No credentials found matching filters."
        )
        _ch.info("Use 'navig vault set <path> <value>' to add a credential.")
        return

    table = _Table(title="NAVIG Vault")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Kind", style="yellow")
    table.add_column("Provider", style="green")
    table.add_column("Label")
    table.add_column("Created", style="dim")
    table.add_column("Last Used", style="dim")

    for item in items:
        created = item.created_at.strftime("%Y-%m-%d") if item.created_at else "-"
        last_used = item.last_used_at.strftime("%Y-%m-%d %H:%M") if item.last_used_at else "-"
        table.add_row(
            item.id, item.kind.value, item.provider or "-", item.label, created, last_used
        )

    _console().print(table)

    # ── Detect AI provider keys that exist outside the vault (#62) ────────────
    if not (provider or profile):
        import os  # noqa: PLC0415

        _AI_SIGNALS: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
            ("openrouter", ("OPENROUTER_API_KEY",), ("openrouter_api_key",)),
            ("openai", ("OPENAI_API_KEY",), ("openai_api_key",)),
            ("anthropic", ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"), ("anthropic_api_key",)),
            ("groq", ("GROQ_API_KEY",), ("groq_api_key",)),
            ("gemini", ("GEMINI_API_KEY", "GOOGLE_API_KEY"), ("google_api_key", "gemini_api_key")),
            ("nvidia", ("NVIDIA_API_KEY", "NIM_API_KEY"), ("nvidia_api_key", "nim_api_key")),
            ("xai", ("XAI_API_KEY", "GROK_KEY"), ("xai_api_key", "grok_key")),
            ("mistral", ("MISTRAL_API_KEY",), ("mistral_api_key",)),
        ]
        vault_providers = {c.provider for c in items}
        try:
            from navig.config import get_config_manager  # noqa: PLC0415

            _gcfg = get_config_manager().global_config
        except Exception:  # noqa: BLE001
            _gcfg = {}

        external: list[tuple[str, str]] = []
        for prov_id, env_keys, cfg_keys in _AI_SIGNALS:
            if prov_id in vault_providers:
                continue
            for env_key in env_keys:
                if os.environ.get(env_key, "").strip():
                    external.append((prov_id, f"env:{env_key}"))
                    break
            else:
                for cfg_key in cfg_keys:
                    if str(_gcfg.get(cfg_key) or "").strip():
                        external.append((prov_id, f"config:{cfg_key}"))
                        break

        if external:
            con = _console()
            con.print("\n[dim]Credentials detected outside vault:[/dim]")
            for prov_id, source in external:
                con.print(
                    f"  [yellow]•[/yellow] [cyan]{prov_id}[/cyan] [dim]({source})[/dim] — not synced to vault"
                )
            con.print("[dim]  → Sync with: navig vault set <provider> <value>[/dim]")

    # ── Detect voice provider keys outside vault ───────────────────────────
    if not (provider or profile):
        import os  # noqa: PLC0415

        _VOICE_SIGNALS: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
            ("deepgram", ("DEEPGRAM_API_KEY", "DEEPGRAM_KEY"), ("deepgram_api_key",)),
            ("elevenlabs", ("ELEVENLABS_API_KEY", "XI_API_KEY"), ("elevenlabs_api_key",)),
        ]
        vault_providers_v = {c.provider for c in items}
        try:
            from navig.config import get_config_manager  # noqa: PLC0415

            _gcfg_v = get_config_manager().global_config
        except Exception:  # noqa: BLE001
            _gcfg_v = {}

        voice_external: list[tuple[str, str]] = []
        for prov_id, env_keys, cfg_keys in _VOICE_SIGNALS:
            if prov_id in vault_providers_v:
                continue
            for env_key in env_keys:
                if os.environ.get(env_key, "").strip():
                    voice_external.append((prov_id, f"env:{env_key}"))
                    break
            else:
                for cfg_key in cfg_keys:
                    if str(_gcfg_v.get(cfg_key) or "").strip():
                        voice_external.append((prov_id, f"config:{cfg_key}"))
                        break

        if voice_external:
            con = _console()
            con.print("\n[dim]Voice credentials detected outside vault:[/dim]")
            for prov_id, source in voice_external:
                con.print(
                    f"  [yellow]•[/yellow] [cyan]{prov_id}[/cyan] [dim]({source})[/dim] — not synced to vault"
                )
            con.print(
                "[dim]  → Sync with: navig cred add deepgram  or  navig cred add elevenlabs[/dim]"
            )


@vault_app.command("validate")
def vault_validate(
    provider: str = typer.Argument(..., help="Provider name (e.g. nvidia, openai)"),
    profile: str = typer.Option(None, "--profile", "-P", help="Credential profile"),
):
    """Validate a credential against the provider's API."""
    vault = _vault_mod.get_vault()
    con = _console()
    con.print(f"Running validation for [cyan]{provider}[/cyan]...")
    try:
        result = vault.test_provider(provider, profile_id=profile)
        cred = vault.get(provider, profile_id=profile, caller="vault.validate")
        if cred is not None:
            tested_at = getattr(result, "tested_at", None)
            meta = {
                "validation_success": bool(result.success),
                "validation_message": str(result.message or ""),
            }
            if tested_at:
                meta["validation_tested_at"] = tested_at.isoformat()
            vault.update(cred.id, metadata=meta)
    except Exception as exc:
        _ch.error(f"Validation error: {exc}")
        raise typer.Exit(1) from exc

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


@vault_app.command("check-all")
def vault_check_all(
    json_output: bool = typer.Option(False, "--json", help="Output results as JSON"),
    profile: str = typer.Option(None, "--profile", "-P", help="Filter by profile"),
) -> None:
    """Validate every stored credential and print a status table.

    Exits with code 1 if any credential fails validation.
    Use --json for machine-readable output (CI-friendly).
    """
    vault = _vault_mod.get_vault()
    creds = vault.list_creds(profile_id=profile)
    creds = [c for c in creds if c.enabled]

    if not creds:
        _ch.warning("Vault is empty — nothing to check.")
        return

    con = _console()
    results: list[dict] = []
    any_failed = False

    for cred in creds:
        validator = _validators_mod.get_validator(cred.provider)
        try:
            result = validator.validate(cred)
        except Exception as exc:  # noqa: BLE001
            from navig.vault.types import TestResult  # noqa: PLC0415

            result = TestResult(success=False, message=f"Unexpected error: {exc}")

        status_icon = "\u2705" if result.success else "\u274c"
        if not result.success:
            any_failed = True

        is_generic = type(validator).__name__ == "GenericValidator"
        validation_mode = "presence" if is_generic else "remote"

        results.append(
            {
                "id": cred.id,
                "provider": cred.provider,
                "label": cred.label,
                "success": result.success,
                "message": result.message or "",
                "details": result.details or {},
                "validation_mode": validation_mode,
            }
        )

    if json_output:
        import json  # noqa: PLC0415

        _rprint(json.dumps(results, default=str))
        if any_failed:
            raise typer.Exit(1)
        return

    table = _Table(title="Vault Key Health Check")
    table.add_column("Provider", style="cyan")
    table.add_column("Label")
    table.add_column("Status", justify="center")
    table.add_column("Mode", style="dim")
    table.add_column("Details")

    for r in results:
        icon = "\u2705 OK" if r["success"] else "\u274c FAIL"
        details_str = ""
        if r["details"]:
            details_str = "  ".join(f"{k}={v}" for k, v in list(r["details"].items())[:3])
        elif r["message"]:
            details_str = r["message"][:60]
        table.add_row(
            r["provider"],
            r["label"],
            icon,
            r["validation_mode"],
            details_str,
        )

    con.print(table)
    total = len(results)
    passed = sum(1 for r in results if r["success"])
    failed = total - passed

    if any_failed:
        con.print(f"[red]\u274c {failed}/{total} credential(s) failed.[/red]")
        raise typer.Exit(1)
    else:
        con.print(f"[green]\u2705 All {total} credential(s) passed.[/green]")


@vault_app.command("delete")
def vault_delete(
    path: str = typer.Argument(..., help="Provider name or provider/data_key"),
    profile: str = typer.Option("default", "--profile", "-P", help="Credential profile"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Delete a credential from the vault."""
    vault = _vault_mod.get_vault()
    provider, _ = _parse_vault_path(path)
    cred = vault.get(provider, profile_id=profile, caller="vault.delete")

    if cred is None:
        _ch.error(f"No credential found for provider '{provider}' in profile '{profile}'.")
        raise typer.Exit(1)

    if not force and not _ch.confirm_action(f"Delete credential for '{provider}' ({cred.id})?"):
        raise typer.Abort()

    if vault.delete(cred.id):
        _ch.success(f"Deleted credential for '{provider}'.")
    else:
        _ch.error("Failed to delete credential.")
