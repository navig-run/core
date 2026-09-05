"""
Context Management Commands for NAVIG

Provides commands for managing host/app context resolution,
including project-local context files and context inspection.
"""

import json
from pathlib import Path
from typing import Any

import typer

from navig import console_helper as ch
from navig.cli.options import is_dry_run
from navig.config import get_config_manager


def show_context(opts: dict[str, Any]) -> None:
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
            legacy_context = legacy_file.read_text(encoding="utf-8").strip()
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
    ch.dim("Resolution priority: --host flag > env var > project > user cache > default")


def set_context(
    host: str | None = None, app: str | None = None, opts: dict[str, Any] = None
) -> None:
    """
    Set project-local context in .navig/config.yaml.

    Creates the .navig directory if it doesn't exist.
    """
    opts = opts or {}
    config = get_config_manager()

    if not host and not app:
        ch.error("Please specify --host and/or --app to set context")
        raise typer.Exit(2)  # malformed invocation, not a lookup failure

    # Validate host exists
    if host and not config.host_exists(host):
        ch.error(f"Host '{host}' not found")
        ch.info("Available hosts:", ", ".join(config.list_hosts()))
        raise typer.Exit(1)

    # Validate app exists on host
    if app:
        target_host = host or config.get_active_host()
        if not target_host:
            ch.error("Cannot set app without a host. Specify --host or set an active host first.")
            raise typer.Exit(2)
        if not config.app_exists(target_host, app):
            ch.error(f"App '{app}' not found on host '{target_host}'")
            apps = config.list_apps(target_host)
            if apps:
                ch.info("Available apps:", ", ".join(apps))
            return

    navig_dir = Path.cwd() / ".navig"
    config_file = navig_dir / "config.yaml"

    # `--dry-run` is a GLOBAL flag and arrives here in `opts` — this used to create
    # .navig/ and write config.yaml regardless, then report success.
    if is_dry_run(opts):
        parts = ", ".join(p for p in (f"host {host}" if host else "", f"app {app}" if app else "") if p)
        ch.info(f"[yellow]DRY RUN:[/yellow] Would set project context ({parts}) in {config_file}")
        ch.dim("Nothing was written.")
        return

    # Create .navig directory
    navig_dir.mkdir(parents=True, exist_ok=True)

    # Load or create config.yaml
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


def clear_context(opts: dict[str, Any] = None) -> None:
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

    # Same global `--dry-run`: this used to unlink config.yaml and report success.
    if is_dry_run(opts):
        what = "rewrite" if local_config else "remove"
        ch.info(f"[yellow]DRY RUN:[/yellow] Would {what} {config_file} to clear project context")
        ch.dim("Nothing was changed.")
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


def _git_toplevel(path: Path) -> Path | None:
    """The git work-tree root containing *path*, or None when it isn't inside a repo.

    Uses ``git rev-parse --show-toplevel`` (which walks UP), NOT ``(path/".git").exists()``
    — the latter is False whenever `navig context init` runs from a repo SUBDIRECTORY, and
    then `.navig/` never got added to `.gitignore` (it could be committed by accident).
    """
    import subprocess

    try:
        r = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5, encoding="utf-8", errors="replace",
        )
        if r.returncode == 0 and r.stdout.strip():
            return Path(r.stdout.strip())
    except Exception:  # noqa: BLE001 — git missing/timeout → treat as "not a repo"
        pass
    return None


def _ensure_navig_gitignored(cwd: Path) -> Path | None:
    """Add ``.navig/`` to the repo's ROOT ``.gitignore`` when *cwd* is inside a git work
    tree and it isn't already ignored. Returns the ``.gitignore`` path written, or None
    (not a repo, already present, or the write failed).

    Writes to the repo ROOT — the ``.navig/`` pattern (no leading slash) matches a ``.navig``
    dir at ANY depth, so one entry covers a ``.navig`` created in a subdirectory too.
    """
    repo_root = _git_toplevel(cwd)
    if repo_root is None:
        return None
    gitignore = repo_root / ".gitignore"
    if gitignore.exists():
        try:
            content = gitignore.read_text(encoding="utf-8")
        except OSError:
            return None
        if ".navig" in content or ".navig/" in content:
            return None
    try:
        with open(gitignore, "a", encoding="utf-8") as f:
            f.write("\n# NAVIG project context\n.navig/\n")
    except OSError:
        return None  # read-only / permission — best-effort, never crash `context init`
    return gitignore


def init_context(opts: dict[str, Any] = None) -> None:
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
            content = legacy_file.read_text(encoding="utf-8").strip()
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

    # Add .navig/ to the repo's .gitignore. Detects the repo via `git rev-parse` so it also
    # works when `navig context init` is run from a SUBDIRECTORY (the old `(cwd/".git")`
    # check silently skipped there, leaving `.navig/` un-ignored).
    written = _ensure_navig_gitignored(Path.cwd())
    if written is not None:
        ch.dim(f"Added .navig/ to {written}")


context_app = typer.Typer(
    help="Manage host/app context for current project",
    invoke_without_command=True,
    no_args_is_help=False,
)


@context_app.callback()
def context_callback(ctx: typer.Context):
    """Context management - shows current context if no subcommand."""
    # Nine sibling modules already do this. The root `navig` callback ensures the dict,
    # so through the real CLI this is a no-op; it matters when the sub-app is reached
    # directly (a test, a programmatic invoke), where `ctx.obj[...]` would otherwise
    # die with "'NoneType' object does not support item assignment" — a crash that is
    # also non-zero, so an exit-code assertion can pass for entirely the wrong reason.
    ctx.ensure_object(dict)
    if ctx.invoked_subcommand is None:
        show_context(ctx.obj)
        raise typer.Exit()


@context_app.command("show")
def context_show(
    ctx: typer.Context,
    plain: bool = typer.Option(False, "--plain", help="One-line output for scripting"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    Show current context resolution.

    Displays which host/app is active and where the context is resolved from
    (environment variable, project config, user cache, or default).

    Examples:
        navig context show
        navig context show --json
        navig context --plain
    """
    ctx.obj["plain"] = plain
    if json_out:
        ctx.obj["json"] = True
    show_context(ctx.obj)


@context_app.command("set")
def context_set(
    ctx: typer.Context,
    host: str | None = typer.Option(None, "--host", "-h", help="Host to set as project default"),
    app_name: str | None = typer.Option(None, "--app", "-a", help="App to set as project default"),
):
    """
    Set project-local context in .navig/config.yaml.

    This creates a project-specific context that takes precedence over
    the global user context (set with 'navig host use').

    Examples:
        navig context set --host production
        navig context set --host staging --app myapp
        navig context set --app backend
    """
    set_context(host=host, app=app_name, opts=ctx.obj)


@context_app.command("clear")
def context_clear(ctx: typer.Context):
    """
    Clear project-local context.

    Removes active_host and active_app from .navig/config.yaml.
    After clearing, context will resolve from global user settings.

    Examples:
        navig context clear
    """
    clear_context(ctx.obj)


@context_app.command("init")
def context_init(ctx: typer.Context):
    """
    Initialize .navig directory in current project.

    Creates .navig/config.yaml with the current active host.
    If a legacy .navig file exists, it will be migrated.
    Also adds .navig/ to .gitignore if in a git repository.

    Examples:
        navig context init
    """
    init_context(ctx.obj)
