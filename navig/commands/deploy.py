"""
navig/commands/deploy.py — NAVIG Deploy command group.

Provides:
  navig deploy run        — Deploy the active app to the active host
  navig deploy rollback   — Restore from last deploy snapshot
  navig deploy history    — Show recent deploy log
  navig deploy status     — Last deploy state + health check
  navig deploy init       — Scaffold .navig/deploy.yaml interactively
  navig deploy check      — Validate config + test connectivity (no deploy)
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer
import yaml
from rich.console import Console
from rich.table import Table

console = Console()

# ============================================================================
# Typer app
# ============================================================================

deploy_app = typer.Typer(
    name="deploy",
    help="Deploy apps to configured hosts with backup, health check, and rollback.",
    no_args_is_help=True,
)

# ─── phase display metadata ─────────────────────────────────────────────────
_PHASE_LABELS = {
    "pre_check": "Pre-checks",
    "backup": "Backup",
    "push": "Push",
    "apply": "Apply",
    "restart": "Restart",
    "health": "Health check",
    "cleanup": "Cleanup",
}

_STATUS_ICON = {
    "ok": "[green]✓[/green]",
    "fail": "[red]✗[/red]",
    "skip": "[dim]→[/dim]",
    "warn": "[yellow]⚠[/yellow]",
    "start": "[dim]→[/dim]",
}

_PHASE_COL_WIDTH = 12
_MSG_COL_WIDTH = 60


# ============================================================================
# Helpers — config resolution
# ============================================================================


def _load_deploy_yaml(project_root: Path) -> Dict[str, Any]:
    """Load .navig/deploy.yaml from the project root. Returns {} if absent."""
    candidate = project_root / ".navig" / "deploy.yaml"
    if not candidate.exists():
        return {}
    try:
        with open(candidate, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception as exc:
        raise typer.BadParameter(f"Could not parse .navig/deploy.yaml: {exc}") from exc


def _resolve_context(
    host_flag: Optional[str],
    app_flag: Optional[str],
    deploy_yaml: Dict[str, Any],
    config_manager: Any,
) -> tuple[str, str]:
    """Resolve active host and app name. CLI flag > deploy.yaml > active context."""
    host_name = host_flag or deploy_yaml.get("host") or config_manager.get_active_host()
    app_name = (
        app_flag or deploy_yaml.get("app") or config_manager.get_active_app() or "app"
    )
    if not host_name:
        console.print(
            "[red]No host selected.[/red] Run [bold]navig host use <name>[/bold] or set host: in .navig/deploy.yaml"
        )
        raise typer.Exit(1)
    return host_name, app_name


def _get_cache_dir(config_manager: Any) -> Path:
    """Return ~/.navig/cache/ ensuring it exists."""
    d = config_manager.global_config_dir / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ============================================================================
# Progress renderer (live output during deploy)
# ============================================================================


class _ProgressRenderer:
    """
    Thin stateful renderer for deploy phase output.
    Each phase prints one line: icon + phase label + message + elapsed.
    """

    def __init__(self, dry_run: bool = False):
        self._dry_run = dry_run
        self._phases: List[tuple] = []  # (phase, status, msg, elapsed)
        self._phase_start: Dict[str, float] = {}
        self._current_line_len = 0

    def on_progress(self, phase: Any, status: str, msg: str) -> None:
        """Called by DeployEngine with (phase, status, msg)."""
        phase_val = phase.value if hasattr(phase, "value") else str(phase)

        if status == "start":
            self._phase_start[phase_val] = time.perf_counter()
            label = _PHASE_LABELS.get(phase_val, phase_val).ljust(_PHASE_COL_WIDTH)
            icon = "[dim]·[/dim]"
            console.print(f"  {icon}  {label}", end="", highlight=False)
            return

        elapsed = time.perf_counter() - self._phase_start.get(
            phase_val, time.perf_counter()
        )
        icon = _STATUS_ICON.get(status, "·")
        label = _PHASE_LABELS.get(phase_val, phase_val).ljust(_PHASE_COL_WIDTH)

        # Truncate long messages
        display_msg = (msg or "")[:_MSG_COL_WIDTH]

        # Overwrite the pending "·" line
        console.print(
            f"\r  {icon}  {label} {display_msg:<{_MSG_COL_WIDTH}}  [dim]{elapsed:.1f}s[/dim]",
            highlight=False,
        )
        self._phases.append((phase_val, status, msg, elapsed))

    def print_header(self, host: str, app: str) -> None:
        tag = "[dim](DRY RUN)[/dim] " if self._dry_run else ""
        console.print()
        console.print(
            f"[bold]NAVIG Deploy[/bold] — [cyan]{app}[/cyan] → [cyan]{host}[/cyan]  {tag}"
        )
        console.rule(style="dim")

    def print_summary(self, result: Any) -> None:
        console.rule(style="dim")
        if result.dry_run:
            console.print("[dim]Dry run complete. No changes made.[/dim]")
            return

        elapsed = f"{result.elapsed:.1f}s" if result.finished_at else "?"

        if result.rolled_back:
            console.print(
                "\n[yellow]Health check failed. Rolled back to previous state.[/yellow]"
            )
            console.print(f"[red]Deploy failed[/red] in {elapsed}. Exit code 1.")
        elif result.success:
            snap_msg = ""
            if result.snapshot:
                snap_msg = f"\n  Snapshot: [dim]{result.snapshot.path}[/dim]"
                snap_msg += "\n  To rollback: [bold]navig deploy rollback[/bold]"
            if result.git_ref:
                snap_msg += f"\n  Git ref: [dim]{result.git_ref}[/dim]"
            console.print(f"\n[green]Deploy complete[/green] in {elapsed}.{snap_msg}")
        else:
            console.print(
                f"\n[red]Deploy failed[/red] in {elapsed}. Error: {result.error}"
            )
            if result.snapshot:
                console.print("  To rollback: [bold]navig deploy rollback[/bold]")


# ============================================================================
# Commands
# ============================================================================


@deploy_app.command("run")
def deploy_run(
    host: Optional[str] = typer.Option(
        None, "--host", "-H", help="Target host name (overrides active context)"
    ),
    app: Optional[str] = typer.Option(
        None, "--app", "-a", help="App name (overrides active context)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview all steps without executing"
    ),
    skip_backup: bool = typer.Option(
        False, "--skip-backup", help="Skip remote snapshot (faster, unsafe)"
    ),
    no_auto_rollback: bool = typer.Option(
        False, "--no-auto-rollback", help="Do not auto-rollback on health check failure"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Stream full command output"
    ),
):
    """Deploy the active (or specified) app to the target host."""
    from navig.config import get_config_manager
    from navig.deploy.engine import DeployEngine
    from navig.deploy.models import DeployConfig

    cm = get_config_manager()

    project_root = _detect_project_root(cm)
    raw_yaml = _load_deploy_yaml(project_root)

    if not raw_yaml and not dry_run:
        console.print(
            "[yellow]No .navig/deploy.yaml found.[/yellow]  "
            "Run [bold]navig deploy init[/bold] to create one."
        )
        raise typer.Exit(1)

    host_name, app_name = _resolve_context(host, app, raw_yaml, cm)

    # Load and validate host config
    try:
        server_cfg = cm.load_server_config(host_name)
    except Exception as exc:
        console.print(f"[red]Host '{host_name}' not found:[/red] {exc}")
        raise typer.Exit(1) from exc

    # Build deploy config
    deploy_cfg = DeployConfig.from_dict(raw_yaml)
    deploy_cfg.host = host_name
    deploy_cfg.app = app_name

    # Merge global defaults
    try:
        global_raw = cm._load_global_config()
        deploy_cfg.merge_global_defaults(global_raw)
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    # Set up progress renderer
    renderer = _ProgressRenderer(dry_run=dry_run)
    renderer.print_header(host_name, app_name)

    # Build and run the engine
    from navig.remote import RemoteOperations

    remote_ops = RemoteOperations(cm)

    engine = DeployEngine(
        config=deploy_cfg,
        server_config=server_cfg,
        remote_ops=remote_ops,
        cache_dir=_get_cache_dir(cm),
        project_root=project_root,
        verbose=verbose,
    )

    result = engine.run(
        dry_run=dry_run,
        skip_backup=skip_backup,
        auto_rollback=not no_auto_rollback,
        on_progress=renderer.on_progress,
    )

    renderer.print_summary(result)

    if not result.success and not dry_run:
        raise typer.Exit(1)


@deploy_app.command("rollback")
def deploy_rollback(
    host: Optional[str] = typer.Option(None, "--host", "-H", help="Target host name"),
    app: Optional[str] = typer.Option(None, "--app", "-a", help="App name"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview rollback without executing"
    ),
):
    """Restore the previous deploy snapshot."""
    from navig.config import get_config_manager
    from navig.deploy.adapters import build_adapter
    from navig.deploy.health import HealthChecker
    from navig.deploy.models import DeployConfig
    from navig.deploy.rollback import RollbackManager
    from navig.remote import RemoteOperations

    cm = get_config_manager()
    project_root = _detect_project_root(cm)
    raw_yaml = _load_deploy_yaml(project_root)

    host_name, app_name = _resolve_context(host, app, raw_yaml, cm)

    try:
        server_cfg = cm.load_server_config(host_name)
    except Exception as exc:
        console.print(f"[red]Host '{host_name}' not found:[/red] {exc}")
        raise typer.Exit(1) from exc

    deploy_cfg = DeployConfig.from_dict(raw_yaml)
    deploy_cfg.host = host_name
    deploy_cfg.app = app_name

    remote_ops = RemoteOperations(cm)
    cache_dir = _get_cache_dir(cm)

    mgr = RollbackManager(
        backup_cfg=deploy_cfg.backup,
        deploy_target=deploy_cfg.push.target,
        app_name=app_name,
        server_config=server_cfg,
        remote_ops=remote_ops,
        cache_dir=cache_dir,
        dry_run=dry_run,
    )

    snap = mgr.load_state()
    if not snap:
        console.print(
            f"[red]No snapshot found[/red] for app '[cyan]{app_name}[/cyan]' on host '[cyan]{host_name}[/cyan]'."
        )
        console.print("Run [bold]navig deploy run[/bold] first.")
        raise typer.Exit(1)

    console.print(
        f"\n[bold]NAVIG Rollback[/bold] — [cyan]{app_name}[/cyan] → [cyan]{host_name}[/cyan]"
    )
    console.print(f"  Snapshot: [dim]{snap.path}[/dim]")
    if not dry_run:
        console.print()

    t0 = time.perf_counter()
    ok, msg = mgr.restore_snapshot(snap)
    elapsed = time.perf_counter() - t0

    if not ok:
        console.print(f"  [red]✗[/red]  Restore failed: {msg}")
        raise typer.Exit(1)

    console.print(f"  [green]✓[/green]  Restored  {msg}  [dim]{elapsed:.1f}s[/dim]")

    if not dry_run:
        # Restart
        enriched = {**server_cfg, "_deploy_target_root": deploy_cfg.push.target}
        try:
            adapter = build_adapter(deploy_cfg.restart, enriched, remote_ops, dry_run)
            t0 = time.perf_counter()
            r_ok, r_msg = adapter.restart()
            r_elapsed = time.perf_counter() - t0
            icon = "[green]✓[/green]" if r_ok else "[yellow]⚠[/yellow]"
            console.print(f"  {icon}  Restart   {r_msg}  [dim]{r_elapsed:.1f}s[/dim]")
        except Exception as exc:
            console.print(f"  [yellow]⚠[/yellow]  Restart skipped: {exc}")

        # Health check
        checker = HealthChecker(deploy_cfg.health, server_cfg, remote_ops)
        t0 = time.perf_counter()
        h_ok, h_msg = checker.check()
        h_elapsed = time.perf_counter() - t0
        icon = "[green]✓[/green]" if h_ok else "[red]✗[/red]"
        console.print(f"  {icon}  Health    {h_msg}  [dim]{h_elapsed:.1f}s[/dim]")

    console.print()
    console.print(
        "[green]Rollback complete.[/green]" if ok else "[red]Rollback failed.[/red]"
    )


@deploy_app.command("history")
def deploy_history(
    limit: int = typer.Option(10, "--limit", "-n", help="Number of entries to show"),
    host: Optional[str] = typer.Option(None, "--host", "-H", help="Filter by host"),
    app: Optional[str] = typer.Option(None, "--app", "-a", help="Filter by app"),
    json_out: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show recent deploy history."""
    from navig.config import get_config_manager
    from navig.deploy.history import DeployHistory

    cm = get_config_manager()
    cache_dir = _get_cache_dir(cm)
    history = DeployHistory(cache_dir=cache_dir)
    entries = history.read(limit=limit, app=app, host=host)

    if not entries:
        console.print("[dim]No deploy history found.[/dim]")
        return

    if json_out:
        console.print_json(json.dumps(entries, indent=2))
        return

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
    table.add_column("Time", style="dim", min_width=20)
    table.add_column("App", style="cyan", min_width=12)
    table.add_column("Host", style="cyan", min_width=12)
    table.add_column("Status", min_width=10)
    table.add_column("Elapsed", justify="right", min_width=8)
    table.add_column("Ref", style="dim", min_width=8)

    for entry in entries:
        ts = entry.get("started_at", "")[:19].replace("T", " ")
        app_n = entry.get("app", "-")
        host_n = entry.get("host", "-")
        success = entry.get("success", False)
        rolled = entry.get("rolled_back", False)
        elapsed = f"{entry.get('elapsed_seconds', 0):.1f}s"
        ref = entry.get("git_ref") or "-"

        if rolled:
            status_str = "[yellow]rolled back[/yellow]"
        elif success:
            status_str = "[green]ok[/green]"
        else:
            status_str = "[red]failed[/red]"

        table.add_row(ts, app_n, host_n, status_str, elapsed, ref)

    console.print(table)


@deploy_app.command("status")
def deploy_status(
    host: Optional[str] = typer.Option(None, "--host", "-H", help="Target host"),
    app: Optional[str] = typer.Option(None, "--app", "-a", help="App name"),
):
    """Show the status of the last deploy."""
    from navig.config import get_config_manager
    from navig.deploy.history import DeployHistory

    cm = get_config_manager()
    project_root = _detect_project_root(cm)
    raw_yaml = _load_deploy_yaml(project_root)
    cache_dir = _get_cache_dir(cm)

    host_name = host or raw_yaml.get("host") or cm.get_active_host()
    app_name = app or raw_yaml.get("app") or cm.get_active_app() or "app"

    history = DeployHistory(cache_dir=cache_dir)
    entries = history.read(
        limit=1,
        app=app_name if (app or raw_yaml.get("app")) else None,
        host=host_name if (host or raw_yaml.get("host")) else None,
    )

    if not entries:
        console.print("[dim]No deploy history found.[/dim]")
        return

    entry = entries[0]
    ts = entry.get("started_at", "")[:19].replace("T", " ")
    success = entry.get("success", False)
    rolled = entry.get("rolled_back", False)
    elapsed = f"{entry.get('elapsed_seconds', 0):.1f}s"
    snap = (entry.get("snapshot") or {}).get("path", "-")
    ref = entry.get("git_ref") or "-"
    err = entry.get("error", "")

    if rolled:
        status_label = "[yellow]rolled back[/yellow]"
    elif success:
        status_label = "[green]success[/green]"
    else:
        status_label = "[red]failed[/red]"

    console.print(f"\n  [bold]App[/bold]      {entry.get('app', '-')}")
    console.print(f"  [bold]Host[/bold]     {entry.get('host', '-')}")
    console.print(f"  [bold]Status[/bold]   {status_label}")
    console.print(f"  [bold]Time[/bold]     {ts}")
    console.print(f"  [bold]Elapsed[/bold]  {elapsed}")
    console.print(f"  [bold]Git ref[/bold]  {ref}")
    console.print(f"  [bold]Snapshot[/bold] {snap}")
    if err:
        console.print(f"  [bold]Error[/bold]    [red]{err}[/red]")
    console.print()


@deploy_app.command("init")
def deploy_init(
    force: bool = typer.Option(
        False, "--force", help="Overwrite existing .navig/deploy.yaml"
    ),
):
    """Scaffold a .navig/deploy.yaml config interactively."""
    from navig.config import get_config_manager

    cm = get_config_manager()
    project_root = _detect_project_root(cm)
    config_dir = project_root / ".navig"
    config_path = config_dir / "deploy.yaml"

    if config_path.exists() and not force:
        console.print(
            f"[yellow]{config_path}[/yellow] already exists. Use --force to overwrite."
        )
        raise typer.Exit(0)

    # Detect runtime hints
    hints = _detect_runtime_hints(project_root)

    # Prompt for key values
    console.print("\n[bold]NAVIG Deploy — Init[/bold]")
    console.print("Scaffolding [cyan].navig/deploy.yaml[/cyan]\n")

    active_host = cm.get_active_host() or ""
    active_app = cm.get_active_app() or ""

    host_in = (
        typer.prompt("Target host name", default=active_host)
        if not active_host
        else active_host
    )
    app_in = (
        typer.prompt("App name", default=active_app) if not active_app else active_app
    )

    # Gather push source
    default_source = hints.get("build_dir", "./dist/")
    source = typer.prompt(
        "Local source directory (relative to project root)", default=default_source
    )
    if not source.endswith("/"):
        source += "/"

    # Gather target path
    target = typer.prompt("Remote target path", default=f"/var/www/{app_in}/")
    if not target.endswith("/"):
        target += "/"

    # Adapter
    default_adapter = hints.get("adapter", "systemd")
    console.print(
        "\nRestart adapters: [bold]systemd[/bold] | docker-compose | pm2 | command"
    )
    adapter = typer.prompt("Service restart adapter", default=default_adapter)

    service = ""
    compose_file = "docker-compose.yml"
    restart_cmd = ""
    if adapter in ("systemd", "pm2"):
        service = typer.prompt("Service/process name", default=app_in)
    elif adapter == "docker-compose":
        compose_file = typer.prompt("docker-compose file", default="docker-compose.yml")
    elif adapter == "command":
        restart_cmd = typer.prompt("Restart command")

    # Health check URL
    health_url = typer.prompt(
        "Health check URL (leave empty to skip)", default="http://localhost/health"
    )

    # Apply commands
    default_apply = hints.get("apply_commands", [])
    apply_cmds: List[str] = []
    if default_apply:
        console.print(f"\nDetected apply commands: {default_apply}")
        if typer.confirm("Use these apply commands?", default=True):
            apply_cmds = default_apply
    if not apply_cmds:
        raw = typer.prompt(
            "Post-push commands (comma-separated, leave empty to skip)",
            default="",
        )
        apply_cmds = [c.strip() for c in raw.split(",") if c.strip()]

    # Build YAML structure
    doc: Dict[str, Any] = {"version": "1"}

    doc["push"] = {"source": source, "target": target}

    if apply_cmds:
        doc["apply"] = {"commands": apply_cmds}

    restart_blk: Dict[str, Any] = {"adapter": adapter}
    if service:
        restart_blk["service"] = service
    if adapter == "docker-compose":
        restart_blk["compose_file"] = compose_file
    if restart_cmd:
        restart_blk["command"] = restart_cmd
    doc["restart"] = restart_blk

    if health_url:
        doc["health_check"] = {"url": health_url}

    doc["backup"] = {
        "enabled": True,
        "remote_path": f"/var/backups/{app_in}",
        "keep_last": 5,
    }

    if host_in:
        doc["host"] = host_in
    if app_in:
        doc["app"] = app_in

    # Write to disk
    config_dir.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as fh:
        yaml.dump(
            doc, fh, default_flow_style=False, allow_unicode=True, sort_keys=False
        )

    console.print(
        f"\n[green]✓[/green]  Created [cyan]{config_path.relative_to(project_root)}[/cyan]"
    )
    console.print("\nNext steps:")
    console.print("  1. Review and edit the generated file")
    console.print(
        "  2. [bold]navig deploy check[/bold]   — validate config + test connectivity"
    )
    console.print(
        "  3. [bold]navig deploy run --dry-run[/bold]   — preview deploy steps"
    )
    console.print("  4. [bold]navig deploy run[/bold]   — deploy")


@deploy_app.command("check")
def deploy_check(
    host: Optional[str] = typer.Option(None, "--host", "-H", help="Target host"),
    app: Optional[str] = typer.Option(None, "--app", "-a", help="App name"),
):
    """Validate .navig/deploy.yaml and test connectivity — no deploy executed."""
    from navig.config import get_config_manager
    from navig.deploy.models import DeployConfig
    from navig.remote import RemoteOperations

    cm = get_config_manager()
    project_root = _detect_project_root(cm)
    raw_yaml = _load_deploy_yaml(project_root)

    host_name, app_name = _resolve_context(host, app, raw_yaml, cm)

    console.print(f"\n[bold]NAVIG Deploy Check[/bold] — {app_name} → {host_name}\n")

    ok_count = 0
    fail_count = 0

    def _check(label: str, success: bool, detail: str = "") -> None:
        nonlocal ok_count, fail_count
        icon = "[green]✓[/green]" if success else "[red]✗[/red]"
        console.print(f"  {icon}  {label:<28}{detail}")
        if success:
            ok_count += 1
        else:
            fail_count += 1

    # 1. deploy.yaml
    if raw_yaml:
        _check(".navig/deploy.yaml", True, "found")
    else:
        _check(".navig/deploy.yaml", False, "missing — run navig deploy init")

    # 2. Host config
    try:
        server_cfg = cm.load_server_config(host_name)
        _check(
            "Host config",
            True,
            f"{server_cfg.get('host', '?')}:{server_cfg.get('port', 22)}",
        )
    except Exception as exc:
        _check("Host config", False, str(exc))
        server_cfg = None

    # 3. Deploy config parse
    deploy_cfg = None
    if raw_yaml:
        try:
            deploy_cfg = DeployConfig.from_dict(raw_yaml)
            _check("Deploy config parse", True, "valid")
        except Exception as exc:
            _check("Deploy config parse", False, str(exc))

    # 4. Local source
    if deploy_cfg:
        source = project_root / deploy_cfg.push.source.lstrip("./")
        _check("Local source exists", source.exists(), str(source))

    # 5. SSH connectivity
    if server_cfg:
        remote_ops = RemoteOperations(cm)
        try:
            t0 = time.perf_counter()
            r = remote_ops.execute_command("echo ok", server_cfg)
            elapsed = time.perf_counter() - t0
            if r.returncode == 0:
                _check("SSH connectivity", True, f"{elapsed:.1f}s")
            else:
                _check("SSH connectivity", False, r.stderr.strip()[:60])
        except Exception as exc:
            _check("SSH connectivity", False, str(exc)[:60])

    # 6. rsync available locally
    import shutil

    rsync_path = shutil.which("rsync")
    _check(
        "rsync available (local)",
        rsync_path is not None,
        rsync_path or "not found in PATH",
    )

    # 7. Restart adapter
    if deploy_cfg:
        adapter = deploy_cfg.restart.adapter
        if (
            deploy_cfg.restart.service
            or deploy_cfg.restart.command
            or adapter == "docker-compose"
        ):
            _check("Restart adapter configured", True, adapter)
        else:
            _check(
                "Restart adapter configured",
                False,
                f"adapter={adapter} but no service/command set",
            )

    console.print()
    if fail_count == 0:
        console.print(f"[green]All {ok_count} checks passed.[/green]  Ready to deploy.")
    else:
        console.print(
            f"[yellow]{ok_count} passed[/yellow], [red]{fail_count} failed[/red].  Fix the issues above before deploying."
        )
        raise typer.Exit(1)


# ============================================================================
# Internal helpers
# ============================================================================


def _detect_project_root(config_manager: Any) -> Path:
    """Return the project root dir (.navig/ parent), falling back to cwd."""
    if config_manager.app_config_dir:
        return config_manager.app_config_dir.parent
    return Path.cwd()


def _detect_runtime_hints(project_root: Path) -> Dict[str, Any]:
    """
    Inspect the project to suggest sane defaults for init.

    Returns a dict with optional keys:
      build_dir, adapter, apply_commands
    """
    hints: Dict[str, Any] = {}

    if (project_root / "package.json").exists():
        hints["adapter"] = "pm2"
        hints["build_dir"] = "./dist/"
        # Check for common build script names
        try:
            with open(project_root / "package.json", encoding="utf-8") as fh:
                pkg = json.load(fh)
            scripts = pkg.get("scripts", {})
            if "build" in scripts:
                hints["apply_commands"] = (
                    []
                )  # build is local; apply = nothing by default
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

    elif (project_root / "requirements.txt").exists() or (
        project_root / "pyproject.toml"
    ).exists():
        hints["adapter"] = "systemd"
        hints["build_dir"] = "./"
        hints["apply_commands"] = (
            ["pip install -r requirements.txt --quiet"]
            if (project_root / "requirements.txt").exists()
            else []
        )

    elif (project_root / "artisan").exists() or (
        project_root / "composer.json"
    ).exists():
        # Laravel
        hints["adapter"] = "systemd"
        hints["build_dir"] = "./"
        hints["apply_commands"] = [
            "composer install --no-dev --optimize-autoloader",
            "php artisan migrate --force",
            "php artisan config:cache",
        ]

    elif (project_root / "docker-compose.yml").exists() or (
        project_root / "compose.yml"
    ).exists():
        hints["adapter"] = "docker-compose"
        hints["build_dir"] = "./"

    else:
        hints["adapter"] = "systemd"
        hints["build_dir"] = "./dist/"

    return hints
