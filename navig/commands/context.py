"""
Context Management Commands for NAVIG

Provides commands for managing host/app context resolution,
including project-local context files and context inspection.
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional

from navig import console_helper as ch
from navig.config import get_config_manager


def show_context(opts: Dict[str, Any]) -> None:
    """
    Show current context resolution with source information.

    Displays:
    - Active host and where it's resolved from
    - Active app and where it's resolved from
    - Project-local context file if present
    - Environment variables if set
    """
    import os

    config = get_config_manager()
    want_json = opts.get("json", False)
    want_plain = opts.get("plain", False)

    # Get active host with source
    host, host_source = config.get_active_host(return_source=True)
    app, app_source = config.get_active_app(return_source=True)

    # Check for environment variables
    env_host = os.environ.get("NAVIG_ACTIVE_HOST", "")
    env_app = os.environ.get("NAVIG_ACTIVE_APP", "")

    # Check for project-local context
    local_context_file = Path.cwd() / ".navig" / "config.yaml"
    local_context = config.get_local_config() if local_context_file.exists() else None

    # Check for legacy .navig file
    legacy_file = Path.cwd() / ".navig"
    legacy_context = None
    if legacy_file.exists() and legacy_file.is_file():
        try:
            legacy_context = legacy_file.read_text().strip()
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

    if want_json:
        result = {
            "host": {
                "name": host,
                "source": host_source,
            },
            "app": {
                "name": app,
                "source": app_source,
            },
            "environment": {
                "NAVIG_ACTIVE_HOST": env_host or None,
                "NAVIG_ACTIVE_APP": env_app or None,
            },
            "local_context_file": str(local_context_file) if local_context else None,
            "local_context": local_context,
            "legacy_file": str(legacy_file) if legacy_context else None,
            "legacy_context": legacy_context,
            "working_directory": str(Path.cwd()),
        }
        print(json.dumps(result, indent=2))
        return

    if want_plain:
        # One-line format for scripting
        print(
            f"host={host or 'none'} source={host_source} app={app or 'none'} app_source={app_source}"
        )
        return

    # Rich output
    ch.header("Context Resolution")

    # Show host resolution
    source_labels = {
        "env": "environment variable (NAVIG_ACTIVE_HOST)",
        "project": "project .navig/config.yaml",
        "legacy": "legacy .navig file",
        "user": "user cache (navig host use)",
        "default": "default host (global config)",
        "none": "not set",
    }

    if host:
        ch.success(f"Host: {host}")
        ch.dim(f"  Source: {source_labels.get(host_source, host_source)}")
    else:
        ch.warning("Host: not set")
        ch.info("  Run 'navig host use <name>' or 'navig context set --host <name>'")

    print()  # blank line

    # Show app resolution
    if app:
        ch.success(f"App: {app}")
        ch.dim(f"  Source: {source_labels.get(app_source, app_source)}")
    else:
        ch.dim("App: not set")

    print()  # blank line

    # Show environment variables
    if env_host or env_app:
        ch.header("Environment Variables")
        if env_host:
            ch.info(f"  NAVIG_ACTIVE_HOST={env_host}")
        if env_app:
            ch.info(f"  NAVIG_ACTIVE_APP={env_app}")
        print()

    # Show local context
    if local_context:
        ch.header("Project Context (.navig/config.yaml)")
        ch.dim(f"  Path: {local_context_file}")
        if local_context.get("active_host"):
            ch.info(f"  active_host: {local_context['active_host']}")
        if local_context.get("active_app"):
            ch.info(f"  active_app: {local_context['active_app']}")
        print()
    elif legacy_context:
        ch.header("Legacy Context (.navig file)")
        ch.dim(f"  Content: {legacy_context}")
        ch.warning("  This format is deprecated. Run 'navig context init' to migrate.")
        print()

    # Show resolution priority
    ch.dim(
        "Resolution priority: --host flag > env var > project > user cache > default"
    )


def set_context(
    host: Optional[str] = None, app: Optional[str] = None, opts: Dict[str, Any] = None
) -> None:
    """
    Set project-local context in .navig/config.yaml.

    Creates the .navig directory if it doesn't exist.
    """
    opts = opts or {}
    config = get_config_manager()

    if not host and not app:
        ch.error("Please specify --host and/or --app to set context")
        return

    # Validate host exists
    if host and not config.host_exists(host):
        ch.error(f"Host '{host}' not found")
        ch.info("Available hosts:", ", ".join(config.list_hosts()))
        return

    # Validate app exists on host
    if app:
        target_host = host or config.get_active_host()
        if not target_host:
            ch.error(
                "Cannot set app without a host. Specify --host or set an active host first."
            )
            return
        if not config.app_exists(target_host, app):
            ch.error(f"App '{app}' not found on host '{target_host}'")
            apps = config.list_apps(target_host)
            if apps:
                ch.info("Available apps:", ", ".join(apps))
            return

    # Create .navig directory
    navig_dir = Path.cwd() / ".navig"
    navig_dir.mkdir(parents=True, exist_ok=True)

    # Load or create config.yaml
    config_file = navig_dir / "config.yaml"
    local_config = config.get_local_config()

    # Update config
    if host:
        local_config["active_host"] = host
    if app:
        local_config["active_app"] = app

    # Save config
    config.set_local_config(local_config)

    ch.success(f"Project context set in {config_file}")
    if host:
        ch.info(f"  Host: {host}")
    if app:
        ch.info(f"  App: {app}")


def clear_context(opts: Dict[str, Any] = None) -> None:
    """
    Clear project-local context (remove active_host/active_app from .navig/config.yaml).
    """
    opts = opts or {}

    config_file = Path.cwd() / ".navig" / "config.yaml"
    config = get_config_manager()

    if not config_file.exists():
        ch.info("No project context to clear")
        return

    local_config = config.get_local_config()

    # Remove context keys
    changed = False
    if "active_host" in local_config:
        del local_config["active_host"]
        changed = True
    if "active_app" in local_config:
        del local_config["active_app"]
        changed = True

    if not changed:
        ch.info("No project context was set")
        return

    # Save updated config (or delete if empty)
    if local_config:
        config.set_local_config(local_config)
        ch.success("Project context cleared")
    else:
        config_file.unlink()
        ch.success("Project context cleared (config file removed)")

    # Show what context will now resolve to
    config = get_config_manager(force_new=True)
    host, source = config.get_active_host(return_source=True)
    ch.dim(f"Context will now resolve from: {source}")


def init_context(opts: Dict[str, Any] = None) -> None:
    """
    Initialize .navig directory in current project.

    Creates:
    - .navig/config.yaml with active_host from current global context
    - Migrates legacy .navig file if present
    """
    opts = opts or {}
    config = get_config_manager()

    navig_dir = Path.cwd() / ".navig"
    config_file = navig_dir / "config.yaml"
    legacy_file = Path.cwd() / ".navig"

    # Check for legacy file to migrate
    migrate_from_legacy = False
    legacy_host = None
    legacy_app = None

    if legacy_file.exists() and legacy_file.is_file():
        try:
            content = legacy_file.read_text().strip()
            if ":" in content:
                legacy_host, legacy_app = content.split(":", 1)
            else:
                legacy_host = content
            migrate_from_legacy = True
            ch.info(f"Found legacy .navig file: {content}")
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

    # Check if already initialized
    if navig_dir.exists() and navig_dir.is_dir() and config_file.exists():
        ch.info("Project already has .navig/config.yaml")

        if migrate_from_legacy:
            ch.warning("Legacy .navig file exists alongside new format")
            ch.info("Consider removing the legacy .navig file")

        show_context(opts)
        return

    # Create directory
    navig_dir.mkdir(parents=True, exist_ok=True)

    # Build initial config
    local_config = {}

    if migrate_from_legacy:
        # Migrate from legacy file
        if legacy_host:
            local_config["active_host"] = legacy_host
        if legacy_app:
            local_config["active_app"] = legacy_app
        ch.success(f"Migrated legacy context: host={legacy_host}, app={legacy_app}")

        # Rename legacy file
        backup_path = legacy_file.parent / ".navig.legacy.bak"
        legacy_file.rename(backup_path)
        ch.dim(f"Legacy file backed up to: {backup_path}")
    else:
        # Use current global context as starting point
        host = config.get_active_host()
        if host:
            local_config["active_host"] = host
            ch.info(f"Using current active host: {host}")

    # Save config
    config.set_local_config(local_config)

    ch.success(f"Initialized project context at {navig_dir}")

    # Add .navig to .gitignore if git repo exists
    gitignore = Path.cwd() / ".gitignore"
    if (Path.cwd() / ".git").exists():
        should_add = True
        if gitignore.exists():
            content = gitignore.read_text()
            if ".navig" in content or ".navig/" in content:
                should_add = False

        if should_add:
            with open(gitignore, "a", encoding="utf-8") as f:
                f.write("\n# NAVIG project context\n.navig/\n")
            ch.dim("Added .navig/ to .gitignore")
