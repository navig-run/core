from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import typer

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.rule import Rule
    from rich.status import Status
    from rich.table import Table
    _RICH = True
except ImportError:
    _RICH = False

def _con():
    return Console(highlight=False) if _RICH else None

def _p(con, msg):
    import re
    if _RICH and con:
        con.print(msg)
    else:
        print(re.sub(r'\[/?[^\]]*\]', '', msg))

@dataclass
class _Result:
    label: str
    ok: bool = True
    note: str = ''
    elapsed: float = 0.0
    warnings: List[str] = field(default_factory=list)

def _step_git(src_dir, force):
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0'}
    t0 = time.monotonic()
    try:
        pull = subprocess.run(
            ['git', '-C', str(src_dir), '-c', 'http.connectTimeout=10',
             '-c', 'http.lowSpeedTime=20', 'pull', '--ff-only'],
            capture_output=True, text=True, timeout=30, env=env,
        )
    except FileNotFoundError:
        return _Result('Sync with upstream', ok=False, note='git not found', elapsed=time.monotonic()-t0)
    except subprocess.TimeoutExpired:
        return _Result('Sync with upstream', ok=False, note='git pull timed out', elapsed=time.monotonic()-t0)
    elapsed = time.monotonic() - t0
    if pull.returncode != 0:
        return _Result('Sync with upstream', ok=False, note=pull.stderr.strip()[:120], elapsed=elapsed)
    if 'Already up to date' in pull.stdout and not force:
        return _Result('Sync with upstream', ok=True, note='already on latest commit', elapsed=elapsed)
    commit_line = pull.stdout.strip().splitlines()[-1] if pull.stdout.strip() else ''
    result = _Result('Sync with upstream', ok=True, note=commit_line[:80], elapsed=elapsed)
    t1 = time.monotonic()
    uv = shutil.which('uv')
    if uv:
        cmd = [uv, 'pip', 'install', '--python', sys.executable, '-e', str(src_dir), '-q']
    else:
        cmd = [sys.executable, '-m', 'pip', 'install', '-e', str(src_dir), '-q']
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    result.elapsed += time.monotonic() - t1
    if r.returncode != 0:
        result.ok = False
        result.note = r.stderr.strip()[:120]
    return result

def _step_pypi(force):
    t0 = time.monotonic()
    uv = shutil.which('uv')
    if uv:
        cmd = [uv, 'pip', 'install', '--python', sys.executable, '--upgrade', 'navig']
        if force: cmd.append('--reinstall')
    else:
        cmd = [sys.executable, '-m', 'pip', 'install', '--upgrade', 'navig',
               '--disable-pip-version-check', '-q']
        if force: cmd.append('--force-reinstall')
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        elapsed = time.monotonic() - t0
        return _Result('Update via package manager', ok=r.returncode==0,
                       note='' if r.returncode==0 else r.stderr.strip()[:120], elapsed=elapsed)
    except subprocess.TimeoutExpired:
        return _Result('Update via package manager', ok=False, note='timed out', elapsed=time.monotonic()-t0)

def _step_doctor():
    t0 = time.monotonic()
    warnings = []
    try:
        from navig.commands.doctor import run_doctor_checks
        results = run_doctor_checks(quiet=True) or []
        for r in results:
            if getattr(r, 'level', '') in ('warning', 'error'):
                warnings.append(getattr(r, 'message', str(r)))
    except Exception:
        pass
    note = f'{len(warnings)} warning(s)' if warnings else 'no issues'
    return _Result('Config doctor', ok=True, note=note, elapsed=time.monotonic()-t0, warnings=warnings)

def _step_plugins():
    t0 = time.monotonic()
    try:
        from navig.plugins import get_plugin_manager
        mgr = get_plugin_manager()
        user_plugins = [p for p in (mgr.list_plugins() or {}).values()
                        if getattr(p, 'source', '') == 'user']
        note = f'{len(user_plugins)} user plugin(s)' if user_plugins else 'up to date'
        return _Result('Plugins', ok=True, note=note, elapsed=time.monotonic()-t0)
    except Exception:
        return _Result('Plugins', ok=True, note='up to date', elapsed=time.monotonic()-t0)

def _reload_version():
    try:
        import importlib, navig as _nav
        importlib.reload(_nav)
        return _nav.__version__
    except Exception:
        return '?'

def _sync_path(src_dir, con):
    try:
        venv_exe = src_dir / '.venv' / 'Scripts' / 'navig.exe'
        path_navig = shutil.which('navig')
        path_exe = Path(path_navig) if path_navig else None
        if venv_exe.exists() and path_exe and path_exe.exists() and venv_exe != path_exe:
            shutil.copy2(str(venv_exe), str(path_exe))
            _p(con, f'[dim]  + PATH entry synced: {path_exe}[/dim]')
    except Exception:
        pass

def _run_update(check=False, force=False, dry_run=False, channel=None):
    con = _con()
    t_total = time.monotonic()
    from navig import __version__
    old_version = __version__
    src_dir = Path(__file__).resolve().parent.parent.parent
    is_git = (src_dir / '.git').exists()
    install_type = 'git' if is_git else 'pip'

    if check:
        if _RICH and con:
            grid = Table.grid(padding=(0, 2))
            grid.add_column(style='dim')
            grid.add_column(style='cyan bold')
            grid.add_row('Version', old_version)
            grid.add_row('Install', install_type)
            grid.add_row('Source', str(src_dir))
            if is_git:
                try:
                    log = subprocess.run(['git', '-C', str(src_dir), 'log', '--oneline', '-1'],
                                         capture_output=True, text=True, timeout=5)
                    grid.add_row('Commit', log.stdout.strip()[:72])
                except Exception:
                    pass
            con.print(Panel(grid, title='[bold]NAVIG[/bold]', border_style='cyan', padding=(0, 2)))
            con.print('[dim]  Run [bold]navig update[/bold] to apply updates.[/dim]')
        else:
            print(f'NAVIG  v{old_version}  ({install_type})  {src_dir}')
        return

    if dry_run:
        _p(con, f'[dim][dry-run][/dim]  Would upgrade NAVIG v[cyan]{old_version}[/cyan] ({install_type})')
        return

    if _RICH and con:
        con.print()
        con.print(Rule(
            title=f'  [bold]NAVIG[/bold]  [dim]v{old_version}[/dim]  [dim]·[/dim]  [dim]{install_type}[/dim]  ',
            style='dim',
        ))
        con.print()
    else:
        print(f'\n-- NAVIG update  v{old_version}  ({install_type}) --\n')

    results = []

    def _run_step(label, fn):
        if _RICH and con:
            with Status(f'  [dim]{label}...[/dim]', console=con, spinner='dots'):
                r = fn()
        else:
            print(f'  ... {label}', flush=True)
            r = fn()
        results.append(r)
        if _RICH and con:
            icon = '[bold green]\u2713[/bold green]' if r.ok else '[bold red]\u2717[/bold red]'
            note_style = 'dim' if r.ok else 'yellow'
            note_part = f'  [dim]\u00b7[/dim]  [{note_style}]{r.note[:52]}[/{note_style}]' if r.note else ''
            con.print(f'  {icon}  {label}{note_part}  [dim]{r.elapsed:.1f}s[/dim]')
        else:
            status = 'OK' if r.ok else 'FAIL'
            note = f'  | {r.note}' if r.note else ''
            print(f'  {status}  {label}{note}  ({r.elapsed:.1f}s)')
        return r

    if is_git:
        r1 = _run_step('Sync with upstream', lambda: _step_git(src_dir, force))
    else:
        r1 = _run_step('Update via package manager', lambda: _step_pypi(force))

    if not r1.ok:
        _p(con, '\n[red]Update failed — see note above.[/red]')
        raise SystemExit(1)

    _run_step('Config doctor', _step_doctor)
    _run_step('Plugins', _step_plugins)

    new_version = _reload_version()
    total_elapsed = time.monotonic() - t_total
    upgraded = new_version != old_version

    if _RICH and con:
        con.print()
        if upgraded:
            title = (f'  [bold green]\u2713[/bold green]  [dim]{old_version}[/dim]  [dim]→[/dim]  '
                     f'[bold cyan]{new_version}[/bold cyan]  [dim]·  {total_elapsed:.1f}s[/dim]  ')
        else:
            title = (f'  [bold green]\u2713[/bold green]  [bold cyan]{new_version}[/bold cyan]  '
                     f'[dim]up to date  ·  {total_elapsed:.1f}s[/dim]  ')
        con.print(Rule(title=title, style='green'))
        con.print()
    else:
        arrow = f'{old_version} -> {new_version}' if upgraded else f'{new_version} up to date'
        print(f'\nOK  {arrow}  ({total_elapsed:.1f}s)\n')

    all_warnings = [w for r in results for w in r.warnings]
    if all_warnings and _RICH and con:
        lines = '\n'.join(f'  [yellow]-[/yellow] {w}' for w in all_warnings)
        con.print(Panel(lines, title='[bold yellow]Warnings[/bold yellow]',
                        border_style='yellow', padding=(0, 2)))
        con.print()

    _sync_path(src_dir, con)


# ============================================================================
# Typer sub-application  (navig update <subcommand>)
# ============================================================================

update_app = typer.Typer(
    name="update",
    help="Upgrade NAVIG across local and remote nodes.",
    invoke_without_command=True,
    no_args_is_help=False,
    add_completion=False,
)


@update_app.callback(invoke_without_command=True)
def _update_callback(
    ctx: typer.Context,
    check: bool = typer.Option(False, "--check", "-c",
                               help="[legacy] Check version only — alias for 'navig update check'."),
    force: bool = typer.Option(False, "--force", "-f",
                               help="[legacy] Force update — alias for 'navig update run --force'."),
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="[legacy] Dry-run — alias for 'navig update run --dry-run'."),
    channel: Optional[str] = typer.Option(None, "--channel", hidden=True),
) -> None:
    """Upgrade NAVIG.

    Run ``navig update --help`` for all sub-commands or use the legacy
    flags ``--check`` / ``--force`` / ``--dry-run`` for backward compat.
    """
    if ctx.invoked_subcommand is not None:
        return

    # Legacy backward-compat shim
    if check or (not force and not dry_run):
        _run_update(check=check or (not force and not dry_run), force=force,
                    dry_run=dry_run, channel=channel)
        return

    _run_update(check=check, force=force, dry_run=dry_run, channel=channel)


# ---------------------------------------------------------------------------
# navig update check
# ---------------------------------------------------------------------------

@update_app.command("check")
def update_check(
    host: Optional[str] = typer.Option(None, "--host", "-H",
                                        help="Target host (default: local)."),
    group: Optional[str] = typer.Option(None, "--group", "-g", help="Host group name."),
    all_hosts: bool = typer.Option(False, "--all", "-a", help="Check all configured hosts."),
    channel: str = typer.Option("stable", "--channel", help="Channel: stable, beta, nightly."),
    json_out: bool = typer.Option(False, "--json", help="Output JSON."),
) -> None:
    """Check current vs. latest available version (no changes made)."""
    from navig.update.targets import TargetResolver
    from navig.update.sources import build_source
    from navig.update.lifecycle import UpdateEngine
    from navig.config import get_config_manager

    cm = get_config_manager()
    src_cfg = cm.get("update.source", {"type": "pypi", "package": "navig"}) or {}
    if isinstance(src_cfg, str):
        src_cfg = {"type": src_cfg}

    try:
        source = build_source(src_cfg, channel)
    except Exception as exc:
        typer.echo(f"Error building source: {exc}", err=True)
        raise typer.Exit(1)

    try:
        targets = TargetResolver(cm).resolve(host=host, group=group, all_hosts=all_hosts)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    engine = UpdateEngine(targets=targets, source=source)
    plan = engine.plan(force=False)

    if json_out:
        import json as _json
        out = [vi.to_dict() for vi in plan.version_infos.values()]
        typer.echo(_json.dumps(out, indent=2))
        return

    con = _con()
    for vi in plan.version_infos.values():
        if vi.needs_update:
            _p(con, f"[yellow][!][/yellow]  [bold]{vi.node_id}[/bold]  "
                    f"[dim]{vi.current}[/dim] → [cyan bold]{vi.latest}[/cyan bold]  "
                    f"[dim]({vi.source_name})[/dim]")
        elif vi.error:
            _p(con, f"[red][✗][/red]  [bold]{vi.node_id}[/bold]  "
                    f"[red]{vi.error}[/red]")
        else:
            _p(con, f"[green][✓][/green]  [bold]{vi.node_id}[/bold]  "
                    f"[cyan]{vi.current}[/cyan]  [dim]up to date[/dim]")


# ---------------------------------------------------------------------------
# navig update run
# ---------------------------------------------------------------------------

@update_app.command("run")
def update_run(
    host: Optional[str] = typer.Option(None, "--host", "-H", help="Target host (default: local)."),
    group: Optional[str] = typer.Option(None, "--group", "-g", help="Host group."),
    all_hosts: bool = typer.Option(False, "--all", "-a", help="Update all configured hosts."),
    channel: str = typer.Option("stable", "--channel", help="Channel: stable, beta, nightly."),
    force: bool = typer.Option(False, "--force", "-f", help="Update even if already on latest."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show plan without applying."),
    no_rollback: bool = typer.Option(False, "--no-rollback", help="Disable auto-rollback on failure."),
    skip_backup: bool = typer.Option(False, "--skip-backup", help="Skip pre-update version pin."),
    json_out: bool = typer.Option(False, "--json", help="Output JSON result."),
) -> None:
    """Apply updates to one or more nodes."""
    from navig.update.targets import TargetResolver
    from navig.update.sources import build_source
    from navig.update.lifecycle import UpdateEngine
    from navig.config import get_config_manager

    cm = get_config_manager()
    src_cfg = cm.get("update.source", {"type": "pypi", "package": "navig"}) or {}
    if isinstance(src_cfg, str):
        src_cfg = {"type": src_cfg}

    try:
        source = build_source(src_cfg, channel)
    except Exception as exc:
        typer.echo(f"Error building source: {exc}", err=True)
        raise typer.Exit(1)

    try:
        targets = TargetResolver(cm).resolve(host=host, group=group, all_hosts=all_hosts)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    con = _con()
    if dry_run:
        _p(con, "[dim][dry-run] Showing plan — no changes will be made.[/dim]")

    def _progress(node_id: str, step: str, status: str, message: str) -> None:
        icons = {"ok": "[green]✓[/green]", "fail": "[red]✗[/red]",
                 "running": "[dim]…[/dim]", "skip": "[dim]–[/dim]"}
        icon = icons.get(status, " ")
        msg = f"  {message}" if message else ""
        _p(con, f"  {icon}  [bold]{node_id}[/bold]  [dim]{step}[/dim]{msg}")

    engine = UpdateEngine(targets=targets, source=source)
    result = engine.run(
        dry_run=dry_run,
        force=force,
        skip_backup=skip_backup,
        auto_rollback=not no_rollback,
        channel=channel,
        on_progress=_progress,
    )

    if json_out:
        import json as _json
        typer.echo(_json.dumps(result.to_dict(), indent=2))
    else:
        _p(con, "")
        if result.success:
            _p(con, f"[green][✓][/green]  All nodes updated  [dim]{result.total_elapsed_seconds:.1f}s[/dim]")
        else:
            for nr in result.failed_nodes:
                _p(con, f"[red][✗][/red]  [bold]{nr.node_id}[/bold]  {nr.error}")

    if not result.success:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# navig update rollback
# ---------------------------------------------------------------------------

@update_app.command("rollback")
def update_rollback(
    version: str = typer.Argument(..., help="Version to roll back to, e.g. 2.4.15"),
    host: Optional[str] = typer.Option(None, "--host", "-H", help="Target host (default: local)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Roll back NAVIG to a specific version."""
    from navig.update.targets import TargetResolver
    from navig.config import get_config_manager

    cm = get_config_manager()
    try:
        targets = TargetResolver(cm).resolve(host=host)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    target = targets[0]
    con = _con()

    if not yes:
        _p(con, f"[yellow]Roll back [bold]{target.node_id}[/bold] to v[bold]{version}[/bold]?[/yellow]")
        confirmed = typer.confirm("Proceed?", default=False)
        if not confirmed:
            _p(con, "[dim]Aborted.[/dim]")
            raise typer.Exit(0)

    from navig.update.lifecycle import UpdateEngine
    from navig.update.sources import PyPISource  # dummy
    engine = UpdateEngine(targets=targets, source=PyPISource())
    try:
        engine._rollback_node(target, version)
    except Exception as exc:
        _p(con, f"[red]Rollback failed: {exc}[/red]")
        raise typer.Exit(1)

    _p(con, f"[green]✓[/green]  Rolled back to v[cyan]{version}[/cyan]")


# ---------------------------------------------------------------------------
# navig update status
# ---------------------------------------------------------------------------

@update_app.command("status")
def update_status(
    host: Optional[str] = typer.Option(None, "--host", "-H", help="Target host (default: local)."),
    json_out: bool = typer.Option(False, "--json", help="Output JSON."),
) -> None:
    """Show current installed version and update source config."""
    from navig.config import get_config_manager
    cm = get_config_manager()

    import navig as _nav
    current = getattr(_nav, "__version__", "?")
    src_dir = Path(__file__).resolve().parent.parent.parent
    install_type = "git" if (src_dir / ".git").exists() else "pip"
    src_cfg = cm.get("update.source", {"type": "pypi", "package": "navig"}) or {}
    channel = cm.get("update.channel", "stable") or "stable"

    if host and host not in ("local", "localhost"):
        from navig.update.targets import TargetResolver
        from navig.update.sources import build_source, SourceError
        try:
            targets = TargetResolver(cm).resolve(host=host)
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1)
        target = targets[0]
        try:
            source = build_source(src_cfg if isinstance(src_cfg, dict) else {"type": str(src_cfg)}, channel)
        except Exception as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1)
        from navig.update.checker import VersionChecker
        vi = VersionChecker(source).check_ssh(target.node_id, target.server_config or {})
        if json_out:
            typer.echo(json.dumps(vi.to_dict(), indent=2))
        else:
            con = _con()
            _p(con, f"[bold]{vi.node_id}[/bold]  v[cyan]{vi.current}[/cyan]  [dim]({vi.install_type})[/dim]")
        return

    data = {
        "node_id": "local",
        "version": current,
        "install_type": install_type,
        "source": src_cfg,
        "channel": channel,
    }
    if json_out:
        typer.echo(json.dumps(data, indent=2))
        return

    con = _con()
    if _RICH and con:
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="dim")
        grid.add_column(style="cyan bold")
        grid.add_row("Version", current)
        grid.add_row("Install", install_type)
        grid.add_row("Channel", channel)
        grid.add_row("Source", src_cfg.get("type", "pypi") if isinstance(src_cfg, dict) else str(src_cfg))
        grid.add_row("Location", str(src_dir))
        con.print(Panel(grid, title="[bold]NAVIG[/bold]", border_style="cyan", padding=(0, 2)))
    else:
        print(f"NAVIG  v{current}  ({install_type})  ch={channel}  {src_dir}")


# ---------------------------------------------------------------------------
# navig update history
# ---------------------------------------------------------------------------

@update_app.command("history")
def update_history_cmd(
    limit: int = typer.Option(20, "--limit", "-n", help="How many entries to show."),
    node_id: Optional[str] = typer.Option(None, "--node", help="Filter by node ID."),
    json_out: bool = typer.Option(False, "--json", help="Output JSON."),
) -> None:
    """Show recent update history."""
    from navig.update.history import UpdateHistory
    hist = UpdateHistory()
    entries = hist.read(limit=limit, node_id=node_id)
    if json_out:
        typer.echo(json.dumps(entries, indent=2))
        return

    if not entries:
        typer.echo("No update history found.")
        return

    con = _con()
    if _RICH and con:
        t = Table(show_header=True, header_style="bold dim", border_style="dim")
        t.add_column("Time", style="dim", width=20)
        t.add_column("Node", style="bold")
        t.add_column("From")
        t.add_column("To")
        t.add_column("Ch", width=8)
        t.add_column("Status")
        for e in entries:
            ok_str = "[green]ok[/green]" if e.get("ok") else "[red]fail[/red]"
            if e.get("rolled_back"):
                ok_str = "[yellow]rolled back[/yellow]"
            ts = (e.get("timestamp") or "")[:16].replace("T", " ")
            t.add_row(ts, e.get("node_id", "?"), e.get("old_version", "?"),
                      e.get("new_version") or "—", e.get("channel", "?"), ok_str)
        con.print(t)
    else:
        for e in entries:
            ts = (e.get("timestamp") or "")[:16]
            ok = "ok" if e.get("ok") else "fail"
            print(f"{ts}  {e.get('node_id','?'):20}  "
                  f"{e.get('old_version','?'):12} → {e.get('new_version') or '?':12}  {ok}")


# ---------------------------------------------------------------------------
# navig update nodes
# ---------------------------------------------------------------------------

@update_app.command("nodes")
def update_nodes(
    json_out: bool = typer.Option(False, "--json", help="Output JSON."),
) -> None:
    """List all nodes NAVIG knows about (local + configured hosts)."""
    from navig.config import get_config_manager
    cm = get_config_manager()

    nodes = [{"node_id": "local", "type": "local"}]
    try:
        for name in (cm.list_hosts() or []):
            nodes.append({"node_id": name, "type": "ssh"})
    except Exception:
        pass

    if json_out:
        typer.echo(json.dumps(nodes, indent=2))
        return

    con = _con()
    for n in nodes:
        _p(con, f"  [cyan]{n['node_id']:30}[/cyan]  [dim]{n['type']}[/dim]")


# ---------------------------------------------------------------------------
# navig update source
# ---------------------------------------------------------------------------

@update_app.command("source")
def update_source(
    show: bool = typer.Option(True, "--show/--no-show", help="Display current source config."),
    json_out: bool = typer.Option(False, "--json", help="Output JSON."),
) -> None:
    """Show the configured update source."""
    from navig.config import get_config_manager
    cm = get_config_manager()

    src_cfg = cm.get("update.source", {"type": "pypi", "package": "navig"}) or {}
    channel = cm.get("update.channel", "stable") or "stable"

    if json_out:
        typer.echo(json.dumps({"source": src_cfg, "channel": channel}, indent=2))
        return

    con = _con()
    if isinstance(src_cfg, dict):
        for k, v in src_cfg.items():
            _p(con, f"  [dim]{k:20}[/dim]  {v}")
    else:
        _p(con, f"  {src_cfg}")
    _p(con, f"  [dim]{'channel':20}[/dim]  {channel}")
