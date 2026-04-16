"""
App Initialization Commands

Initialize app-specific .navig/ directory for hierarchical configuration.
"""

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


from navig import console_helper as ch
from navig.core.yaml_io import atomic_write_yaml
from navig.platform.paths import config_dir, onboarding_json_path

_CHAT_ONBOARDING_CANONICAL_STEPS: tuple[tuple[str, str, str], ...] = (
    ("ai-provider", "Choose AI provider", "Open Providers and choose your AI brain"),
    ("first-host", "Connect first host", "Add or confirm your first server host"),
    ("telegram-bot", "Enable Telegram bot", "Verify Telegram bot runtime health"),
)
_CHAT_ONBOARDING_CANONICAL_STEP_IDS = {
    step_id for step_id, _, _ in _CHAT_ONBOARDING_CANONICAL_STEPS
}


def _get_instructions_source_path() -> Path | None:
    """
    Find the NAVIG instructions file source path.

    Search order:
    1. Root of the NAVIG repo (navig.instructions.md)
    2. .github/instructions/ directory
    3. Bundled with the package (navig/resources/)

    Returns:
        Path to the instructions file or None if not found
    """
    # Get the navig package directory
    navig_package_dir = Path(__file__).parent.parent  # navig/commands/ -> navig/
    repo_root = navig_package_dir.parent  # navig/ -> remote-manager/

    # Search paths in order of preference
    search_paths = [
        # Primary location: root of NAVIG repo (user-facing instructions)
        repo_root / "navig.instructions.md",
        # Development mode: .github/instructions/
        repo_root / ".github" / "instructions" / "navig.instructions.md",
        # Installed mode: bundled with package
        navig_package_dir / "resources" / "navig.instructions.md",
    ]

    for path in search_paths:
        if path.exists() and path.is_file():
            return path

    return None


def _copy_instructions_file(project_root: Path) -> bool:
    """
    Copy NAVIG instructions file to .github/instructions/ in the project.

    This provides AI assistants with the NAVIG CLI reference guide,
    following the standard location for AI instruction files.

    Args:
        project_root: Path to the project root directory (where .navig/ is created)

    Returns:
        True if copy succeeded, False otherwise (fails silently)
    """
    try:
        source_path = _get_instructions_source_path()
        if source_path is None:
            return False

        # Create .github/instructions/ directory
        dest_dir = project_root / ".github" / "instructions"
        dest_dir.mkdir(parents=True, exist_ok=True)

        dest_path = dest_dir / "navig.instructions.md"
        shutil.copy2(source_path, dest_path)
        return True
    except Exception:
        # Fail silently - don't break init process
        return False


def init_app(options: dict[str, Any]) -> None:
    """
    Initialize app-specific .navig/ directory.

    Creates a .navig/ directory in the current working directory with:
    - hosts/ subdirectory for app-specific host configs
    - apps/ subdirectory for app-specific app configs
    - config.yaml with app metadata

    Args:
        options: Command options (quiet, yes, copy_global, etc.)
    """
    quiet = options.get("quiet", False)
    copy_global = options.get("copy_global", False)
    auto_yes = options.get("yes", False)

    # Check if .navig/ already exists
    navig_dir = Path.cwd() / ".navig"

    if navig_dir.exists():
        ch.error(
            "App already initialized",
            f".navig/ directory already exists in {Path.cwd()}",
        )
        return

    if not quiet:
        ch.header("Initializing NAVIG App")
        ch.info(f"Location: {Path.cwd()}")
        ch.newline()

    try:
        # Create .navig/ directory structure with proper permissions
        import os
        import stat

        # Create main directory
        navig_dir.mkdir(parents=True, exist_ok=True)

        # Set permissions on Windows (full control for current user)
        if os.name == "nt":
            try:
                import getpass
                import subprocess

                username = getpass.getuser()
                # Grant full control to current user
                subprocess.run(
                    [
                        "icacls",
                        str(navig_dir),
                        "/grant",
                        f"{username}:(OI)(CI)F",
                        "/T",
                        "/Q",
                    ],
                    capture_output=True,
                    check=False,
                )
            except Exception as e:
                if not quiet:
                    ch.warning(f"Could not set Windows permissions: {e}")
        else:
            # Unix-like systems - set rwx for user
            try:
                try:
                    os.chmod(
                        navig_dir,
                        stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH,
                    )
                except (OSError, PermissionError):
                    pass  # best-effort chmod; non-fatal on restricted filesystems
            except Exception as e:
                if not quiet:
                    ch.warning(f"Could not set permissions: {e}")

        # Create subdirectories
        hosts_dir = navig_dir / "hosts"
        apps_dir = navig_dir / "apps"
        cache_dir = navig_dir / "cache"
        backups_dir = navig_dir / "backups"

        hosts_dir.mkdir(exist_ok=True)
        apps_dir.mkdir(exist_ok=True)
        cache_dir.mkdir(exist_ok=True)
        backups_dir.mkdir(exist_ok=True)

        # Verify accessibility
        try:
            # Try to list directory contents
            list(navig_dir.iterdir())
            if not quiet:
                ch.success("✓ Directory is accessible")
        except (PermissionError, OSError) as e:
            ch.error(f"WARNING: Created directory but cannot access it: {e}")
            ch.info("You may need to fix permissions manually.")
            ch.info("Run: scripts/fix-navig-permissions.ps1 -Fix")

        # Create config.yaml with app metadata
        app_name = Path.cwd().name
        config_data = {
            "app": {
                "name": app_name,
                "initialized": datetime.now().isoformat(),
                "version": "1.0",
            }
        }

        config_file = navig_dir / "config.yaml"
        atomic_write_yaml(config_data, config_file)

        # Copy NAVIG instructions file to .github/instructions/ (silent operation)
        project_root = Path.cwd()
        instructions_copied = _copy_instructions_file(project_root)

        if not quiet:
            ch.success("✓ Created .navig/ directory structure\n")
            ch.header("Directory Structure")
            ch.info(f"  {navig_dir}/")
            ch.info("  ├── config.yaml       (app metadata)")
            ch.info("  ├── hosts/            (app-specific host configs)")
            ch.info("  ├── apps/             (app-specific app configs)")
            ch.info("  ├── cache/            (runtime state)")
            ch.info("  └── backups/          (database backups)")
            if instructions_copied:
                ch.newline()
                ch.info("  .github/instructions/")
                ch.info("  └── navig.instructions.md  (AI assistant guide)")
            ch.newline()

        # Optionally copy global configs to app
        # Only prompt if there are actual configs to copy
        if copy_global:
            # User explicitly requested copy via --copy-global flag
            _copy_global_configs(navig_dir, quiet)
        elif not quiet and not auto_yes:
            # Interactive mode - check if there are configs to copy
            host_count, app_count = _count_global_configs()
            total_count = host_count + app_count

            if total_count > 0:
                # Build informative prompt message
                config_summary = []
                if host_count > 0:
                    config_summary.append(f"{host_count} host{'s' if host_count != 1 else ''}")
                if app_count > 0:
                    config_summary.append(
                        f"{app_count} legacy config{'s' if app_count != 1 else ''}"
                    )

                prompt_msg = (
                    f"Found {' and '.join(config_summary)} in global config. Copy to this app?"
                )

                if ch.confirm_action(prompt_msg, default=False):
                    _copy_global_configs(navig_dir, quiet)
            else:
                # No configs to copy - skip prompt entirely
                if not quiet:
                    ch.dim("No global configurations found to copy")

        # Prompt for local discovery if no hosts are configured
        if not quiet and not auto_yes:
            _prompt_local_discovery(navig_dir)

        if not quiet:
            ch.success("✓ App initialized successfully!")
            ch.newline()
            ch.header("Next Steps")
            ch.info("  1. Add app-specific hosts: navig host add <name>")
            ch.info("  2. Add apps to hosts: navig app add <name>")
            ch.info("  3. All configs will be stored in .navig/ (not ~/.navig/)")
            ch.info("  4. Add .navig/ to .gitignore if it contains sensitive data")
            ch.newline()
            ch.dim("Note: App-specific configs take precedence over global configs")

    except Exception as e:
        ch.error("Failed to initialize app", str(e))
        # Clean up partial creation
        if navig_dir.exists():
            import shutil

            shutil.rmtree(navig_dir)


def _count_global_configs() -> tuple[int, int]:
    """
    Count available global host and app configurations.

    Returns:
        Tuple of (host_count, app_count)
    """
    global_config_dir = config_dir()
    host_count = 0
    app_count = 0

    try:
        # Count host configs
        global_hosts_dir = global_config_dir / "hosts"
        if global_hosts_dir.exists() and global_hosts_dir.is_dir():
            try:
                host_count = len(list(global_hosts_dir.glob("*.yaml")))
            except (PermissionError, OSError):
                pass  # best-effort cleanup; ignore access/IO errors

        # Count legacy app configs
        global_apps_dir = global_config_dir / "apps"
        if global_apps_dir.exists() and global_apps_dir.is_dir():
            try:
                # Exclude backup files
                app_count = len(
                    [f for f in global_apps_dir.glob("*.yaml") if ".backup." not in f.name]
                )
            except (PermissionError, OSError):
                pass  # best-effort cleanup; ignore access/IO errors
    except (PermissionError, OSError):
        pass  # best-effort cleanup; ignore access/IO errors

    return host_count, app_count


def _copy_global_configs(navig_dir: Path, quiet: bool = False) -> None:
    """
    Copy (not move) global host/app configurations to app-specific directory.

    This operation COPIES configurations, leaving the originals in ~/.navig/ intact.
    This allows the same host configs to be used across multiple apps.

    Args:
        navig_dir: Path to app .navig/ directory
        quiet: Suppress output if True
    """
    global_config_dir = config_dir()

    if not global_config_dir.exists():
        if not quiet:
            ch.dim("No global configuration directory found (~/.navig/)")
        return

    copied_hosts = 0
    copied_apps = 0
    failed_hosts = 0
    failed_apps = 0

    # Copy host configs
    global_hosts_dir = global_config_dir / "hosts"
    if global_hosts_dir.exists():
        try:
            app_hosts_dir = navig_dir / "hosts"
            app_hosts_dir.mkdir(exist_ok=True)

            for host_file in global_hosts_dir.glob("*.yaml"):
                try:
                    dest_file = app_hosts_dir / host_file.name
                    import shutil

                    shutil.copy2(host_file, dest_file)
                    copied_hosts += 1
                except (PermissionError, OSError) as e:
                    failed_hosts += 1
                    if not quiet:
                        ch.warning(f"Failed to copy {host_file.name}: {e}")
        except (PermissionError, OSError) as e:
            if not quiet:
                ch.warning(f"Cannot access global hosts directory: {e}")

    # Copy legacy app configs (if any)
    global_apps_dir = global_config_dir / "apps"
    if global_apps_dir.exists():
        try:
            app_apps_dir = navig_dir / "apps"
            app_apps_dir.mkdir(exist_ok=True)

            for app_file in global_apps_dir.glob("*.yaml"):
                # Skip backup files
                if ".backup." not in app_file.name:
                    try:
                        dest_file = app_apps_dir / app_file.name
                        import shutil

                        shutil.copy2(app_file, dest_file)
                        copied_apps += 1
                    except (PermissionError, OSError) as e:
                        failed_apps += 1
                        if not quiet:
                            ch.warning(f"Failed to copy {app_file.name}: {e}")
        except (PermissionError, OSError) as e:
            if not quiet:
                ch.warning(f"Cannot access global apps directory: {e}")

    # Show results
    if not quiet:
        total_copied = copied_hosts + copied_apps
        total_failed = failed_hosts + failed_apps

        if total_copied > 0:
            # Build success message
            parts = []
            if copied_hosts > 0:
                parts.append(f"{copied_hosts} host{'s' if copied_hosts != 1 else ''}")
            if copied_apps > 0:
                parts.append(f"{copied_apps} legacy config{'s' if copied_apps != 1 else ''}")

            ch.success(f"✓ Copied {' and '.join(parts)} to .navig/")
            ch.dim("  (Originals remain in ~/.navig/)")
        else:
            ch.dim("No configurations found to copy")

        if total_failed > 0:
            ch.warning(f"Failed to copy {total_failed} file(s) due to permission errors")


def _prompt_local_discovery(navig_dir: Path) -> None:
    """
    Prompt user to discover local development environment if no hosts are configured.

    Args:
        navig_dir: Path to the .navig/ directory
    """
    from navig.config import get_config_manager

    config_manager = get_config_manager()
    hosts = config_manager.list_hosts()

    # Check if there are any hosts (excluding 'localhost' which might already exist)
    non_local_hosts = [h for h in hosts if h not in ("localhost", "local", "local-dev")]

    if len(hosts) == 0:
        # No hosts at all - definitely offer local discovery
        ch.newline()
        ch.header("Local Development Setup")
        ch.info("No hosts configured. Would you like to auto-discover your local environment?")
        ch.dim("This will detect installed databases, web servers, PHP, Node.js, etc.")
        ch.newline()

        if ch.confirm_action("Discover local environment?", default=True):
            from navig.commands.local_discovery import discover_local_host

            discover_local_host(name="localhost", auto_confirm=True, set_active=True, progress=True)
    elif len(non_local_hosts) == 0 and "localhost" not in hosts:
        # Only has generic hosts, offer local discovery
        ch.newline()
        if ch.confirm_action("Would you like to add your local machine as a host?", default=False):
            from navig.commands.local_discovery import discover_local_host

            discover_local_host(
                name="localhost", auto_confirm=True, set_active=False, progress=True
            )


# =============================================================================
# Global directory migration helpers
# =============================================================================

_DEFAULT_NAVIG_DIR: Path = config_dir()


def _legacy_documents_config_dir() -> Path:
    """Return the legacy Documents/.navig config directory (pre-migration path)."""
    return Path.home() / "Documents" / ".navig"


def _local_log_dir() -> Path:
    """Return the canonical per-user log directory for NAVIG."""
    try:
        import platformdirs

        return Path(platformdirs.user_log_dir("navig", "navig"))
    except ImportError:
        return config_dir() / "logs"


def _local_state_dir() -> Path:
    """Return the canonical per-user state directory for NAVIG."""
    try:
        import platformdirs

        return Path(platformdirs.user_state_dir("navig", "navig"))
    except ImportError:
        return config_dir() / "state"


def _cache_dir() -> Path:
    """Return the canonical per-user cache directory for NAVIG."""
    try:
        import platformdirs

        return Path(platformdirs.user_cache_dir("navig", "navig"))
    except ImportError:
        return config_dir() / "cache"


def _legacy_windows_platformdirs_root() -> Path:
    """Return the nested legacy Windows platformdirs NAVIG root (pre-migration)."""
    try:
        import platformdirs

        return Path(platformdirs.user_data_dir("NAVIG", "navig"))
    except ImportError:
        import os

        local = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        return Path(local) / "navig" / "NAVIG"


def _write_init_log(message: str) -> None:
    """Append *message* to the NAVIG init log file."""
    log_dir = _local_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "init.log"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(message + "\n")


def _ensure_dirs() -> None:
    """Create all required NAVIG runtime directories."""
    _DEFAULT_NAVIG_DIR.mkdir(parents=True, exist_ok=True)
    _local_log_dir().mkdir(parents=True, exist_ok=True)


def _migrate_legacy_documents_dir(target_dir: Path) -> None:
    """Move config files from the legacy Documents/.navig path to *target_dir*.

    Raises :class:`click.exceptions.Exit` if a path conflict is detected so
    that the caller can abort without clobbering existing data.
    """
    import click

    source_dir = _legacy_documents_config_dir()
    if not source_dir.exists():
        return

    # Detect conflicts: any item that already exists in the target
    conflicts = [item.name for item in source_dir.iterdir() if (target_dir / item.name).exists()]
    if conflicts:
        _write_init_log(f"legacy migration failed: conflict detected in {', '.join(conflicts)}")
        raise click.exceptions.Exit(1)

    # Move each top-level item from source to target
    for item in source_dir.iterdir():
        item.rename(target_dir / item.name)

    # Remove the now-empty source directory (and parent if also empty)
    try:
        source_dir.rmdir()
        parent = source_dir.parent
        if not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass  # best-effort cleanup

    _write_init_log(f"legacy migration: moved {source_dir} -> {target_dir}")


def _migrate_legacy_windows_runtime_layout() -> None:
    """Flatten the nested legacy Windows platformdirs layout into canonical dirs.

    Old layout (created by old platformdirs):
        AppData/Local/navig/NAVIG/Logs/…
        AppData/Local/navig/NAVIG/memory/…
        AppData/Local/navig/NAVIG/Cache/…

    New canonical layout:
        user_log_dir/…
        user_state_dir/memory/…
        user_cache_dir/…
    """
    legacy_root = _legacy_windows_platformdirs_root()
    if not legacy_root.exists():
        return

    log_dir = _local_log_dir()
    state_dir = _local_state_dir()
    cache_dir = _cache_dir()

    # Map legacy sub-dir name → (canonical destination root, keep subdir name?)
    MOVES = [
        ("Logs", log_dir, False),  # Logs/*   → log_dir/*
        ("memory", state_dir, True),  # memory/* → state_dir/memory/*
        ("Cache", cache_dir, False),  # Cache/*  → cache_dir/*
    ]

    for sub_name, dest_root, keep_subdir in MOVES:
        src_sub = legacy_root / sub_name
        if not src_sub.exists():
            continue

        actual_dest = dest_root / sub_name if keep_subdir else dest_root
        actual_dest.mkdir(parents=True, exist_ok=True)

        for item in list(src_sub.iterdir()):
            dest = actual_dest / item.name
            if dest.exists() and item.is_file():
                # Append conflicting files (e.g. rolling log files)
                try:
                    existing = dest.read_text(encoding="utf-8")
                    new_content = item.read_text(encoding="utf-8")
                    dest.write_text(existing + "\n" + new_content, encoding="utf-8")
                    item.unlink()
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical
            else:
                item.rename(dest)

        try:
            src_sub.rmdir()
        except OSError:
            pass  # best-effort cleanup

    # Remove legacy root if now empty
    try:
        legacy_root.rmdir()
    except OSError:
        pass  # best-effort cleanup


def _maybe_send_first_run_ping() -> None:
    """Best-effort first-run telemetry ping used by `navig init`."""
    try:
        from navig.onboarding.telemetry import ping_install_if_first_time

        ping_install_if_first_time()
    except Exception:  # noqa: BLE001
        pass


def _read_marker_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return ""


def show_init_status(*, render: bool = True) -> dict[str, Any]:
    """Display a compact `navig init` status summary and return payload."""
    from navig import __version__ as navig_version
    from navig.config import ConfigManager

    env_cfg_dir = os.environ.get("NAVIG_CONFIG_DIR", "").strip()
    home_navig_dir = config_dir()
    project_navig_dir = Path.cwd() / ".navig"
    if project_navig_dir.exists():
        navig_dir = project_navig_dir
    elif env_cfg_dir:
        navig_dir = Path(env_cfg_dir)
    else:
        navig_dir = home_navig_dir

    provider_marker = _read_marker_text(navig_dir / ".ai_provider_configured")
    env_provider = os.environ.get("NAVIG_LLM_PROVIDER", "").strip()
    active_provider = env_provider or provider_marker or "not configured"

    cfg = ConfigManager(config_dir=navig_dir)
    hosts_count = len(cfg.list_hosts())

    vault_status = "empty"
    try:
        from navig.vault.core import CredentialsVault

        vault = CredentialsVault(
            vault_path=navig_dir / "credentials" / "vault.db",
            auto_migrate=False,
        )
        creds = vault.list()
        vault_status = "initialized" if creds else "empty"
    except (ImportError, RuntimeError, ValueError, OSError):
        vault_status = "empty"

    telegram_active = (navig_dir / ".telegram_configured").exists() or bool(
        cfg.global_config.get("telegram", {}).get("bot_token")
    )
    matrix_active = (navig_dir / ".matrix_configured").exists() or bool(
        cfg.global_config.get("matrix", {}).get("homeserver_url")
    )
    email_active = (navig_dir / ".email_configured").exists() or bool(
        cfg.global_config.get("email", {}).get("smtp_host")
    )

    web_cfg = cfg.global_config.get("web", {}) if isinstance(cfg.global_config, dict) else {}
    web_search_cfg = web_cfg.get("search", {}) if isinstance(web_cfg, dict) else {}
    web_provider = (
        str(web_search_cfg.get("provider") or os.environ.get("NAVIG_WEB_SEARCH_PROVIDER") or "auto")
        .strip()
        .lower()
    )
    if not web_provider:
        web_provider = "auto"

    def _web_key_from_vault(provider_name: str) -> str:
        label_map = {
            "brave": ("web/brave_api_key", "brave/api_key", "brave_api_key"),
            "perplexity": ("web/perplexity_api_key", "perplexity/api_key", "pplx/api_key"),
            "gemini": ("web/gemini_api_key", "google/api_key", "google_api_key"),
            "grok": ("web/grok_api_key", "xai/api_key", "xai_api_key"),
            "kimi": ("web/kimi_api_key", "moonshot/api_key", "moonshot_api_key"),
        }
        try:
            from navig.vault.core import get_vault

            vault = get_vault()
            for label in label_map.get(provider_name, ()):
                try:
                    value = (vault.get_secret(label) or "").strip()
                except Exception:
                    continue
                if value:
                    return value
        except Exception:
            pass  # best-effort: vault unavailable; fall back to empty string
        return ""

    web_key = ""
    if web_provider and web_provider != "auto":
        web_key = _web_key_from_vault(web_provider)
    if not web_key and web_provider == "brave":
        web_key = str(
            web_search_cfg.get("api_key") or os.environ.get("BRAVE_API_KEY") or ""
        ).strip()
    if not web_key:
        api_keys = web_search_cfg.get("api_keys", {}) if isinstance(web_search_cfg, dict) else {}
        if isinstance(api_keys, dict):
            web_key = str(api_keys.get(web_provider) or "").strip()
    web_ready = web_provider in {"auto", "duckduckgo"} or bool(web_key)

    try:
        from navig.providers.source_scan import scan_enabled_provider_sources

        detected_provider_sources = scan_enabled_provider_sources(
            navig_dir=navig_dir,
            cfg=cfg.global_config if isinstance(cfg.global_config, dict) else {},
        )
    except Exception:
        detected_provider_sources = {}

    providers_detected = sorted(detected_provider_sources.keys())

    next_actions: list[str] = []
    readiness_issues: list[dict[str, str]] = []
    if active_provider == "not configured":
        readiness_issues.append(
            {
                "code": "ai-provider-missing",
                "summary": "AI provider is not configured",
                "command": "navig init --provider",
            }
        )
        next_actions.append("navig init --provider")
    if hosts_count == 0 or active_provider == "not configured":
        readiness_issues.append(
            {
                "code": "host-missing",
                "summary": "No remote hosts connected",
                "command": "navig host add <name>",
            }
        )
        next_actions.append("navig host add <name>")
    if not web_ready:
        readiness_issues.append(
            {
                "code": "web-search-not-ready",
                "summary": f"Web search provider '{web_provider}' needs an API key",
                "command": "navig init --reconfigure",
            }
        )
        next_actions.append("navig init --reconfigure")

    next_actions = list(dict.fromkeys(next_actions))

    readiness_total_checks = 3
    readiness_failures = len(readiness_issues)
    readiness_score = max(
        0,
        int(((readiness_total_checks - readiness_failures) / readiness_total_checks) * 100),
    )
    readiness_state = "ready" if readiness_failures == 0 else "needs-attention"

    payload = {
        "provider": active_provider,
        "providers_detected": providers_detected,
        "provider_sources": {
            provider: sorted(sources)
            for provider, sources in sorted(detected_provider_sources.items())
        },
        "hosts_count": hosts_count,
        "vault": vault_status,
        "integrations": {
            "telegram": telegram_active,
            "matrix": matrix_active,
            "email": email_active,
        },
        "web_search": {
            "provider": web_provider,
            "ready": web_ready,
        },
        "readiness": {
            "state": readiness_state,
            "score": readiness_score,
            "issues": readiness_issues,
        },
        "next_actions": next_actions,
        "python_version": sys.version.split()[0],
        "navig_version": navig_version,
    }

    if render:
        ch.header("NAVIG Init Status")

        # ── Configuration ────────────────────────────────────────────────
        ch.subheader("Configuration")

        # AI provider
        ai_provider = payload["provider"]
        if providers_detected:
            cred_parts = []
            for prov in providers_detected:
                src = "/".join(payload["provider_sources"].get(prov, []))
                cred_parts.append(f"{prov} [dim]({src})[/dim]" if src else prov)
            cred_hint = "  [dim](" + ", ".join(cred_parts) + ")[/dim]"
        else:
            cred_hint = ""
        if ai_provider and ai_provider != "not configured":
            ch.success(f"AI provider: {ai_provider}{cred_hint}")
        else:
            ch.error(f"AI provider: not configured{cred_hint}")

        # Connected hosts
        hosts = payload["hosts_count"]
        if hosts > 0:
            ch.success(f"Connected hosts: {hosts}")
        else:
            ch.warning("Connected hosts: none configured")

        # Vault
        vault_state = payload["vault"]
        if vault_state == "initialized":
            ch.success(f"Vault: {vault_state}")
        else:
            ch.warning(f"Vault: {vault_state}")

        # ── Services ─────────────────────────────────────────────────────
        ch.subheader("Services")

        # Integrations — colorize on/off inline
        integrations = payload["integrations"]
        int_parts = []
        for name in ("telegram", "matrix", "email"):
            if integrations[name]:
                int_parts.append(f"{name}=[green]on[/green]")
            else:
                int_parts.append(f"{name}=[dim]off[/dim]")
        ch.info("Integrations: " + ", ".join(int_parts))

        # Web search
        ws = payload["web_search"]
        ws_label = f"{ws['provider']} ({'ready' if ws['ready'] else 'needs key'})"
        if ws["ready"]:
            ch.success(f"Web search: {ws_label}")
        else:
            ch.warning(f"Web search: {ws_label}")

        # ── Readiness ────────────────────────────────────────────────────
        ch.subheader("Readiness")
        ch.info(f"Readiness: {payload['readiness']['state']} ({payload['readiness']['score']}%)")

        score = payload["readiness"]["score"]
        state = payload["readiness"]["state"]
        readiness_label = f"{state} ({score}%)"
        if score == 100:
            ch.success(f"Overall: {readiness_label}")
        elif score >= 50:
            ch.warning(f"Overall: {readiness_label}")
        else:
            ch.error(f"Overall: {readiness_label}")

        if payload["readiness"]["issues"]:
            ch.info("Readiness issues:")
            for issue in payload["readiness"]["issues"]:
                ch.warning(f"  {issue['summary']}")
                ch.dim(f"    → {issue['command']}")

        # ── Next Actions ─────────────────────────────────────────────────
        if payload["next_actions"]:
            ch.subheader("Next Actions")
            ch.info("Next actions:")
            for action in payload["next_actions"]:
                ch.step(action)

        # ── Environment (dim metadata) ───────────────────────────────────
        ch.dim(f"Python {payload['python_version']}  ·  NAVIG {payload['navig_version']}")

    return payload


def get_init_status_payload() -> dict[str, Any]:
    """Build `navig init` status payload without printing output."""
    return show_init_status(render=False)


def _resolve_navig_base_dir(navig_dir: Path | None = None) -> Path:
    if navig_dir is not None:
        return navig_dir
    return config_dir()


def _chat_onboarding_handoff_file(navig_dir: Path | None = None) -> Path:
    base = _resolve_navig_base_dir(navig_dir)
    return base / "state" / "chat_onboarding_handoff.json"


def _onboarding_artifact_file(navig_dir: Path | None = None) -> Path:
    if navig_dir is not None:
        state_candidate = navig_dir / "state" / "onboarding.json"
        legacy_candidate = navig_dir / "onboarding.json"
        if not state_candidate.exists() and legacy_candidate.exists():
            return legacy_candidate
        return state_candidate
    home_navig_dir = _resolve_navig_base_dir(None)
    home_state_candidate = home_navig_dir / "state" / "onboarding.json"
    home_legacy_candidate = home_navig_dir / "onboarding.json"
    if not home_state_candidate.exists() and home_legacy_candidate.exists():
        return home_legacy_candidate
    if home_state_candidate.exists():
        return home_state_candidate
    return onboarding_json_path()


def get_chat_onboarding_step_progress(
    navig_dir: Path | None = None,
) -> list[dict[str, Any]]:
    completed_ids: set[str] = set()
    artifact_path = _onboarding_artifact_file(navig_dir)
    if artifact_path.exists():
        try:
            payload = json.loads(artifact_path.read_text(encoding="utf-8") or "{}")
            completed_ids = {
                str(step.get("id") or "")
                for step in payload.get("steps", [])
                if step.get("status") == "completed" and step.get("id")
            }
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            completed_ids = set()

    progress: list[dict[str, Any]] = []
    for step_id, label, hint in _CHAT_ONBOARDING_CANONICAL_STEPS:
        progress.append(
            {
                "id": step_id,
                "label": label,
                "hint": hint,
                "completed": step_id in completed_ids,
            }
        )
    return progress


def mark_chat_onboarding_step_completed(step_id: str, navig_dir: Path | None = None) -> bool:
    step_id = str(step_id or "").strip()
    if step_id not in _CHAT_ONBOARDING_CANONICAL_STEP_IDS:
        return False

    artifact_path = _onboarding_artifact_file(navig_dir)
    payload: dict[str, Any] = {}
    if artifact_path.exists():
        try:
            loaded = json.loads(artifact_path.read_text(encoding="utf-8") or "{}")
            if isinstance(loaded, dict):
                payload = loaded
        except (json.JSONDecodeError, OSError, ValueError, TypeError):
            return False

    steps = payload.get("steps")
    if not isinstance(steps, list):
        steps = []

    updated = False
    found = False
    for entry in steps:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("id") or "") != step_id:
            continue
        found = True
        if entry.get("status") != "completed":
            entry["status"] = "completed"
            updated = True

    if not found:
        steps.append({"id": step_id, "status": "completed"})
        updated = True

    if not updated:
        return True

    payload["steps"] = steps
    try:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


def write_chat_onboarding_handoff_state(
    *,
    profile: str,
    token_configured: bool,
    auto_started: bool,
    pending: bool = True,
    navig_dir: Path | None = None,
) -> None:
    try:
        path = _chat_onboarding_handoff_file(navig_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pending": bool(pending),
            "profile": str(profile or "quickstart"),
            "token_configured": bool(token_configured),
            "auto_started": bool(auto_started),
            "steps": get_chat_onboarding_step_progress(navig_dir),
            "created_at": datetime.now().isoformat() + "Z",  # utcnow() deprecated in Py3.12+
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass  # best-effort: handoff state file unwritable; non-critical


def consume_chat_onboarding_handoff_state(
    navig_dir: Path | None = None,
) -> dict[str, Any] | None:
    path = _chat_onboarding_handoff_file(navig_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
        if not bool(payload.get("pending")):
            return None
        payload["pending"] = False
        payload["consumed_at"] = datetime.now().isoformat() + "Z"  # utcnow() deprecated in Py3.12+
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload
    except Exception:
        return None


def _persist_telegram_bootstrap_token(token: str, navig_dir: Path | None = None) -> bool:
    token = (token or "").strip()
    if not token:
        return False

    base = navig_dir or (config_dir())
    base.mkdir(parents=True, exist_ok=True)
    wrote = False

    try:
        from navig.vault.core import get_vault

        vault = get_vault()
        if vault is not None:
            vault.put("telegram_bot_token", json.dumps({"value": token}).encode())
            wrote = True
    except Exception:
        pass  # best-effort: vault unavailable; try next storage path

    try:
        env_path = base / ".env"
        existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        lines = [
            ln
            for ln in existing.splitlines()
            if not ln.startswith("TELEGRAM_BOT_TOKEN=")
            and not ln.startswith("NAVIG_TELEGRAM_BOT_TOKEN=")
        ]
        lines.append(f"NAVIG_TELEGRAM_BOT_TOKEN={token}")
        lines.append(f"TELEGRAM_BOT_TOKEN={token}")
        _tmp_path: Path | None = None
        try:
            _fd, _tmp = tempfile.mkstemp(dir=env_path.parent, suffix=".tmp")
            _tmp_path = Path(_tmp)
            with os.fdopen(_fd, "w", encoding="utf-8") as _fh:
                _fh.write("\n".join(lines) + "\n")
            os.replace(_tmp_path, env_path)
            _tmp_path = None
        finally:
            if _tmp_path is not None:
                _tmp_path.unlink(missing_ok=True)
        wrote = True
    except Exception:
        pass  # best-effort: .env file unwritable; try next storage path

    try:
        from navig.config import get_config_manager

        cfg = get_config_manager(config_dir=base)
        telegram_cfg = dict(cfg.global_config.get("telegram") or {})
        telegram_cfg["bot_token"] = token
        cfg.update_global_config({"telegram": telegram_cfg})
        wrote = True
    except Exception:
        pass  # best-effort: config manager unavailable; try next storage path

    try:
        (base / ".telegram_configured").write_text("1", encoding="utf-8")
        wrote = True
    except Exception:
        pass  # best-effort: flag file unwritable; token still set in environment

    os.environ["NAVIG_TELEGRAM_BOT_TOKEN"] = token
    os.environ["TELEGRAM_BOT_TOKEN"] = token
    return wrote


def _auto_start_chat_runtime() -> bool:
    try:
        from navig.daemon.entry import save_default_config

        config_path = save_default_config()
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        cfg["telegram_bot"] = True
        cfg["gateway"] = True
        _tmp_path: Path | None = None
        try:
            _fd, _tmp = tempfile.mkstemp(dir=config_path.parent, suffix=".tmp")
            _tmp_path = Path(_tmp)
            with os.fdopen(_fd, "w", encoding="utf-8") as _fh:
                _fh.write(json.dumps(cfg, indent=2))
            os.replace(_tmp_path, config_path)
            _tmp_path = None
        finally:
            if _tmp_path is not None:
                _tmp_path.unlink(missing_ok=True)
    except Exception:
        pass  # best-effort: daemon config unavailable; still attempt service_start

    try:
        from navig.commands.service import service_start

        service_start(foreground=False)
        return True
    except (SystemExit, Exception):
        return False


def run_chat_first_handoff(
    *,
    profile: str = "quickstart",
    dry_run: bool = False,
    quiet: bool = False,
) -> None:
    if dry_run:
        return

    from navig.messaging.secrets import resolve_telegram_bot_token

    token = (resolve_telegram_bot_token({}) or "").strip()

    if not quiet:
        ch.newline()
        ch.header("Chat-First Onboarding")
        ch.info("For the best experience, configure NAVIG in Telegram now.")

    if not token and sys.stdin.isatty() and not quiet:
        ch.info("1) Open Telegram and talk to @BotFather")
        ch.info("2) Run /newbot and copy your bot token")
        ch.info("3) Paste token below to finish auto-setup")
        try:
            import typer

            entered = typer.prompt("Telegram bot token", default="", show_default=False).strip()
        except Exception:
            entered = ""
        if entered:
            _persist_telegram_bootstrap_token(entered)
            token = entered
            if not quiet:
                ch.success("Telegram token saved")

    if not token:
        write_chat_onboarding_handoff_state(
            profile=profile,
            token_configured=False,
            auto_started=False,
            pending=False,
        )
        if not quiet:
            ch.warning("Telegram token not configured yet.")
            ch.info("Run: navig vault set telegram_bot_token <token>")
            ch.info("Then run: navig start")
        return

    auto_started = _auto_start_chat_runtime()
    if auto_started:
        mark_chat_onboarding_step_completed("telegram-bot")
    write_chat_onboarding_handoff_state(
        profile=profile,
        token_configured=True,
        auto_started=auto_started,
        pending=True,
    )

    if not quiet:
        if auto_started:
            ch.success("NAVIG is now alive.")
            ch.info("Open Telegram and say 'hello' to your bot to continue setup.")
        else:
            ch.warning("Auto-start could not be verified.")
            ch.info("Run: navig start")
            ch.info("Then open Telegram and say 'hello' to your bot.")


def run_init(dry_run: bool = False, no_genesis: bool = False, name: str = "") -> None:
    """Initialize NAVIG global directories and run first-time setup."""
    import click

    try:
        _ensure_dirs()
        # Seed the default space so `navig space list` always shows something
        (_DEFAULT_NAVIG_DIR / "spaces" / "default").mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        _write_init_log(f"init failed: {e}")
        raise click.exceptions.Exit(1) from e


# ── CLI gateway functions (called by navig/cli/__init__.py thin wrappers) ────


def run_init_command(
    ctx: Any,
    *,
    reconfigure: bool = False,
    provider: bool = False,
    settings: bool = False,
    status: bool = False,
    profile: str = "",
    dry_run: bool = False,
    quiet: bool = False,
    tui: bool = False,
    tui_capable: bool | None = None,
) -> None:
    """State-aware NAVIG setup gateway — routes to installer pipeline or interactive flow."""
    import typer

    # ── Installer pipeline (non-interactive) ─────────────────────────────────
    if profile:
        from navig.installer import run_install
        from navig.installer.profiles import VALID_PROFILES

        valid_profiles = set(VALID_PROFILES) | {"quickstart"}
        effective_profile = "operator" if profile == "quickstart" else profile

        if profile not in valid_profiles:
            typer.echo(
                f"Unknown profile '{profile}'. Valid: {', '.join(sorted(valid_profiles))}",
                err=True,
            )
            raise SystemExit(1)

        run_install(profile=effective_profile, dry_run=dry_run, quiet=quiet)
        if profile == "quickstart":
            run_chat_first_handoff(profile=profile, dry_run=dry_run, quiet=quiet)
        return

    # ── Interactive onboarding ────────────────────────────────────────────────
    from navig.commands.onboard import run_onboard
    from navig.onboarding.runner import run_engine_onboarding

    want_json = bool(getattr(ctx, "obj", {}) and ctx.obj.get("json"))

    if status:
        import json as _json

        payload = show_init_status(render=not want_json)
        if want_json:
            typer.echo(_json.dumps(payload, indent=2, ensure_ascii=False))
        return

    if settings:
        from navig.commands.status import show_status

        show_status({"all": True, "plain": False})
        return

    ui_mode = os.getenv("NAVIG_INIT_UI", "auto").strip().lower()
    use_tui = tui or ui_mode == "tui"
    if ui_mode == "cli":
        use_tui = False

    if use_tui:
        _tui_capable = (
            bool(tui_capable)
            if tui_capable is not None
            else bool(sys.stdin.isatty() and sys.stdout.isatty())
        )
        if not _tui_capable:
            from rich.console import Console as _C

            _C().print(
                "[yellow]TUI unavailable in this terminal; falling back to CLI onboarding.[/yellow]"
            )
        else:
            flow = "manual" if (reconfigure or provider) else "auto"
            try:
                run_onboard(flow=flow)
                try:
                    _maybe_send_first_run_ping()
                except Exception:  # noqa: BLE001
                    pass
                return
            except Exception:
                from rich.console import Console as _C

                _C().print("[yellow]TUI launch failed; falling back to CLI onboarding.[/yellow]")

    jump_to_step = "ai-provider" if provider else None
    state = run_engine_onboarding(
        force=reconfigure,
        jump_to_step=jump_to_step,
        show_banner=True,
        respect_skip_env=False,
        skip_if_configured=True,
    )

    if state is None and not reconfigure and not provider:
        import json as _json

        from rich.console import Console as _C

        payload = show_init_status(render=not want_json)
        if want_json:
            payload = {**payload, "already_configured": True}
            typer.echo(_json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            _C().print(
                "\n[green]✓ NAVIG is already configured.[/green] "
                "Use [bold]navig init --reconfigure[/bold] to run setup again."
            )
        return

    try:
        _maybe_send_first_run_ping()
    except Exception:  # noqa: BLE001
        pass


def run_init_rollback(
    last: bool = True,
    profile: str = "",
    dry_run: bool = False,
) -> None:
    """Roll back the most recent installer run."""
    from navig.installer.contracts import Action, InstallerContext, ModuleState, Result
    from navig.installer.runner import rollback as run_rollback
    from navig.installer.state import load_last

    try:
        from navig.platform.paths import navig_config_dir

        config_dir = navig_config_dir()
    except Exception:
        import pathlib

        config_dir = pathlib.config_dir()

    records = load_last(config_dir=config_dir, profile=profile or None)
    if not records:
        ch.warning("No installer history found — nothing to roll back.")
        return

    actions: list[Action] = []
    results: list[Result] = []
    for rec in records:
        a = Action(
            id=rec.get("action_id", "?"),
            description=rec.get("description", ""),
            module=rec.get("module", "?"),
            reversible=rec.get("reversible", False),
        )
        r = Result(
            action_id=a.id,
            state=ModuleState(rec.get("state", "skipped")),
            message=rec.get("message", ""),
            undo_data=rec.get("undo_data") or {},
        )
        actions.append(a)
        results.append(r)

    reversible = [
        (a, r) for a, r in zip(actions, results) if a.reversible and r.state == ModuleState.APPLIED
    ]
    if not reversible:
        ch.info("No reversible applied actions found in the last run.")
        return

    ch.info(f"Rolling back {len(reversible)} action(s):")
    for a, _ in reversible:
        ch.dim(f"  ↩  {a.description}")

    if dry_run:
        ch.warning("[dry-run] No changes made.")
        return

    ctx = InstallerContext(config_dir=config_dir, profile=profile or "?")
    run_rollback(actions=actions, results=results, ctx=ctx)
    ch.success("Rollback complete.")
