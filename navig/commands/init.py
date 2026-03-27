"""
App Initialization Commands

Initialize app-specific .navig/ directory for hierarchical configuration.
"""

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from navig import console_helper as ch


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
                    pass
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
        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f, default_flow_style=False, sort_keys=False)

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
    global_config_dir = Path.home() / ".navig"
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
    global_config_dir = Path.home() / ".navig"

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

_DEFAULT_NAVIG_DIR: Path = Path.home() / ".navig"


def _legacy_documents_config_dir() -> Path:
    """Return the legacy Documents/.navig config directory (pre-migration path)."""
    return Path.home() / "Documents" / ".navig"


def _local_log_dir() -> Path:
    """Return the canonical per-user log directory for NAVIG."""
    try:
        import platformdirs

        return Path(platformdirs.user_log_dir("navig", "navig"))
    except ImportError:
        return Path.home() / ".navig" / "logs"


def _local_state_dir() -> Path:
    """Return the canonical per-user state directory for NAVIG."""
    try:
        import platformdirs

        return Path(platformdirs.user_state_dir("navig", "navig"))
    except ImportError:
        return Path.home() / ".navig" / "state"


def _cache_dir() -> Path:
    """Return the canonical per-user cache directory for NAVIG."""
    try:
        import platformdirs

        return Path(platformdirs.user_cache_dir("navig", "navig"))
    except ImportError:
        return Path.home() / ".navig" / "cache"


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
