from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

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
        print(re.sub(r"\[/?[^\]]*\]", "", msg))


@dataclass
class _Result:
    label: str
    ok: bool = True
    note: str = ""
    elapsed: float = 0.0
    warnings: list[str] = field(default_factory=list)
    changed: bool = False  # True when this step actually applied new code (drives the daemon reload)


def _find_uv() -> str | None:
    """Locate uv, preferring NAVIG's bundled runtime copy.

    The installer keeps uv self-contained at ``~/.navig/runtime/uv`` and off
    PATH, so ``shutil.which`` alone would miss it and fall back to ``pip`` —
    which the uv-built venv has no pip for. We derive the runtime root from
    ``sys.executable`` (``<runtime>/venv/(Scripts|bin)/python``) and only then
    fall back to any uv on PATH (e.g. for dev installs).
    """
    exe = "uv.exe" if os.name == "nt" else "uv"
    candidates = []
    try:
        candidates.append(Path(sys.executable).resolve().parents[2] / exe)
    except Exception:  # noqa: BLE001
        pass  # sys.executable shape unexpected; fall through to other lookups
    candidates.append(Path.home() / ".navig" / "runtime" / exe)
    for c in candidates:
        try:
            if c.exists():
                return str(c)
        except Exception:  # noqa: BLE001
            pass  # unreadable path; try the next candidate
    return shutil.which("uv")


def _git_head(src_dir) -> str:
    """Current HEAD sha of *src_dir*, or "" when it can't be read."""
    try:
        r = subprocess.run(
            ["git", "-C", str(src_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5, encoding="utf-8", errors="replace",
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def _is_navig_git_checkout(src_dir) -> bool:
    """True when *src_dir* is a NAVIG source checkout under git that we can ``git pull``.

    NOT ``(src_dir / ".git").exists()``: in this monorepo ``.git`` lives at the repo ROOT,
    not inside the editable ``core/`` src dir — so that check is False on the operator's own
    editable install, and ``navig update`` silently took the pip path and NEVER pulled. And
    NOT a bare ``git rev-parse``: that would false-positive on a wheel installed into a venv
    that merely sits inside some unrelated git repo. Instead confirm git actually TRACKS
    navig's own source at this path (``ls-files --error-unmatch``), which is true for an
    editable checkout and false for a wheel in site-packages.
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(src_dir), "ls-files", "--error-unmatch", "navig/__init__.py"],
            capture_output=True,
            text=True,
            timeout=5, encoding="utf-8", errors="replace",
        )
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _step_git(src_dir, force):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    t0 = time.monotonic()
    before = _git_head(src_dir)
    try:
        pull = subprocess.run(
            [
                "git",
                "-C",
                str(src_dir),
                "-c",
                "http.connectTimeout=10",
                "-c",
                "http.lowSpeedTime=20",
                "pull",
                "--ff-only",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=env, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError:
        return _Result(
            "Sync with upstream",
            ok=False,
            note="git not found",
            elapsed=time.monotonic() - t0,
        )
    except subprocess.TimeoutExpired:
        return _Result(
            "Sync with upstream",
            ok=False,
            note="git pull timed out",
            elapsed=time.monotonic() - t0,
        )
    elapsed = time.monotonic() - t0
    if pull.returncode != 0:
        return _Result(
            "Sync with upstream",
            ok=False,
            note=pull.stderr.strip()[:120],
            elapsed=elapsed,
        )
    after = _git_head(src_dir)
    moved = bool(before and after and before != after)
    # HEAD delta — NOT __version__ — is the truthful "did anything change" signal on a
    # git checkout: the version string only bumps on a release tag, so a pull of N real
    # feature commits leaves it identical and the old code reported "up to date". Count
    # the commits so the summary says "+N commits" instead of lying.
    n_commits = 0
    if moved:
        try:
            rc = subprocess.run(
                ["git", "-C", str(src_dir), "rev-list", "--count", f"{before}..{after}"],
                capture_output=True,
                text=True,
                timeout=5, encoding="utf-8", errors="replace",
            )
            n_commits = int((rc.stdout or "").strip() or 0) if rc.returncode == 0 else 0
        except Exception:  # noqa: BLE001
            n_commits = 0
    if not moved and not force:
        return _Result(
            "Sync with upstream",
            ok=True,
            note="already on latest commit",
            elapsed=elapsed,
        )
    if moved:
        note = f"+{n_commits} commit{'s' if n_commits != 1 else ''} → {after[:8]}"
    else:
        note = (pull.stdout.strip().splitlines()[-1] if pull.stdout.strip() else "")[:80]
    result = _Result(
        "Sync with upstream", ok=True, note=note, elapsed=elapsed, changed=moved or force
    )
    t1 = time.monotonic()
    uv = _find_uv()
    if uv:
        cmd = [
            uv,
            "pip",
            "install",
            "--python",
            sys.executable,
            "-e",
            str(src_dir),
            "-q",
        ]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "-e", str(src_dir), "-q"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    result.elapsed += time.monotonic() - t1
    if r.returncode != 0:
        result.ok = False
        result.note = r.stderr.strip()[:120]
    return result


def _step_pypi(force):
    t0 = time.monotonic()
    uv = _find_uv()
    if uv:
        cmd = [uv, "pip", "install", "--python", sys.executable, "--upgrade", "navig"]
        if force:
            cmd.append("--reinstall")
    else:
        cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "navig",
            "--disable-pip-version-check",
            "-q",
        ]
        if force:
            cmd.append("--force-reinstall")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        elapsed = time.monotonic() - t0
        return _Result(
            "Update via package manager",
            ok=r.returncode == 0,
            note="" if r.returncode == 0 else r.stderr.strip()[:120],
            elapsed=elapsed,
        )
    except subprocess.TimeoutExpired:
        return _Result(
            "Update via package manager",
            ok=False,
            note="timed out",
            elapsed=time.monotonic() - t0,
        )


def _step_doctor(skip_sections: frozenset[str] | set[str] = frozenset()):
    t0 = time.monotonic()
    warnings = []
    try:
        # collect_report is the canonical programmatic doctor seam — the exact
        # checks `navig doctor --json` runs. skip_deps: the package refresh ran
        # as the update step just before this, so re-probing pip here is
        # redundant (and slow). Any non-ok row (⚠ warn or ✗ fail) is surfaced.
        # skip_sections lets the caller drop rows that are transiently misleading
        # mid-update (e.g. "Daemon" freshness right before the reload step).
        from navig.commands.doctor import collect_report

        report = collect_report(quiet=True, skip_deps=True)
        for section in report.get("sections", []):
            if section.get("name") in skip_sections:
                continue
            for check in section.get("checks", []):
                if not check.get("ok", True):
                    label = str(check.get("label", "")).strip()
                    detail = str(check.get("detail", "")).strip()
                    warnings.append(f"{label}: {detail}" if detail else (label or "issue"))
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical
    note = f"{len(warnings)} warning(s)" if warnings else "no issues"
    return _Result(
        "Config doctor",
        ok=True,
        note=note,
        elapsed=time.monotonic() - t0,
        warnings=warnings,
    )


def _step_plugins():
    t0 = time.monotonic()
    try:
        from navig.plugins import get_plugin_manager

        mgr = get_plugin_manager()
        user_plugins = [
            p for p in (mgr.list_plugins() or {}).values() if getattr(p, "source", "") == "user"
        ]
        note = f"{len(user_plugins)} user plugin(s)" if user_plugins else "up to date"
        return _Result("Plugins", ok=True, note=note, elapsed=time.monotonic() - t0)
    except Exception:
        return _Result("Plugins", ok=True, note="up to date", elapsed=time.monotonic() - t0)


def _reload_version():
    try:
        import importlib

        import navig as _nav

        importlib.reload(_nav)
        return _nav.__version__
    except Exception:
        return "?"


def _step_reload_daemon(interactive: bool):
    """Reload the running daemon so freshly-installed code goes LIVE.

    An editable/pip install loads its source into memory once at boot, so a pull or
    upgrade only changes files on disk — the running daemon (the Telegram bot, gateway
    and scheduler) keeps executing the OLD code until the process restarts. This is the
    #1 "merged but not live" gap: ``update`` said ✓ while the bot ran stale code. This
    step closes it by restarting the daemon after a successful, change-applying update.

    Elevation-aware: a daemon at High integrity (Administrator) can't be stopped from a
    normal terminal ("Access denied"). When interactive we relaunch
    ``navig service restart --admin`` (one UAC prompt); when NOT interactive we refuse
    to pop a blocking UAC dialog and instead report the exact command — so an unattended
    ``navig update`` never hangs on a prompt.
    """
    import subprocess

    t0 = time.monotonic()
    try:
        from navig.daemon.supervisor import NavigDaemon
    except Exception:  # noqa: BLE001
        return _Result(
            "Reload daemon (live code)",
            ok=True,
            note="daemon module unavailable — skipped",
            elapsed=time.monotonic() - t0,
        )

    try:
        running = NavigDaemon.is_running()
    except Exception:  # noqa: BLE001
        running = False
    if not running:
        return _Result(
            "Reload daemon (live code)",
            ok=True,
            note="daemon not running — nothing to reload",
            elapsed=time.monotonic() - t0,
        )

    pid = None
    try:
        pid = NavigDaemon.read_pid()
    except Exception:  # noqa: BLE001
        pass

    need_admin = False
    try:
        from navig.commands.service import _is_elevated, _process_is_elevated

        need_admin = bool(_process_is_elevated(pid)) and not _is_elevated()
    except Exception:  # noqa: BLE001
        need_admin = False

    if need_admin and not interactive:
        return _Result(
            "Reload daemon (live code)",
            ok=False,
            note="daemon runs elevated — run:  navig service restart --admin",
            elapsed=time.monotonic() - t0,
        )

    cmd = [sys.executable, "-m", "navig", "service", "restart"]
    if need_admin:
        cmd.append("--admin")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return _Result(
            "Reload daemon (live code)",
            ok=False,
            note="restart timed out",
            elapsed=time.monotonic() - t0,
        )
    if r.returncode == 0:
        new_pid = None
        try:
            new_pid = NavigDaemon.read_pid()
        except Exception:  # noqa: BLE001
            pass
        note = f"restarted (pid {new_pid})" if new_pid else "restarted — new code live"
        return _Result(
            "Reload daemon (live code)", ok=True, note=note, elapsed=time.monotonic() - t0
        )
    err = (r.stderr.strip() or r.stdout.strip() or "restart failed")[:120]
    return _Result(
        "Reload daemon (live code)", ok=False, note=err, elapsed=time.monotonic() - t0
    )


def _sync_path(src_dir, con):
    try:
        venv_exe = src_dir / ".venv" / "Scripts" / "navig.exe"
        path_navig = shutil.which("navig")
        path_exe = Path(path_navig) if path_navig else None
        if venv_exe.exists() and path_exe and path_exe.exists() and venv_exe != path_exe:
            shutil.copy2(str(venv_exe), str(path_exe))
            _p(con, f"[dim]  + PATH entry synced: {path_exe}[/dim]")
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical


def _offer_redeploys(con):
    """After a successful local update, offer to refresh the deployed Lighthouse
    edge + deck when they're behind what this (now-updated) navig ships.

    This is the "automatic, user-friendly" half: the user runs ``navig update``
    and is prompted to push the matching edge/deck — nothing to remember.
    """
    try:
        from navig.core import Config

        cfg = Config()
    except Exception:  # noqa: BLE001
        return
    import navig as _nav

    cur = str(getattr(_nav, "__version__", "") or "")

    # Lighthouse edge — bundled worker version vs the live one.
    try:
        from navig.commands.lighthouse import (
            _bundled_worker_version,
            _deployed_worker_version,
        )

        url = str(cfg.get("cloud.lighthouse_url", "") or "").strip()
        if url:
            latest = _bundled_worker_version()
            deployed = _deployed_worker_version(url)
            if latest and deployed and latest != deployed:
                _p(con, f"[yellow]  Lighthouse edge behind:[/yellow] deployed v{deployed} · bundled v{latest}")
                if typer.confirm("  Redeploy Lighthouse now?", default=True):
                    subprocess.run(
                        [sys.executable, "-m", "navig", "lighthouse", "redeploy"], check=False
                    )
    except Exception:  # noqa: BLE001
        pass  # best-effort; never fail an update on the redeploy nudge

    # Deck — navig version it was deployed from vs this one.
    try:
        url = str(cfg.get("deck.public_url", "") or "").strip()
        deployed = str(cfg.get("deck.deployed_version", "") or "").strip()
        if url and deployed and deployed != cur:
            _p(con, f"[yellow]  Deck behind:[/yellow] deployed v{deployed} · this navig v{cur}")
            if typer.confirm("  Redeploy the deck now?", default=True):
                subprocess.run([sys.executable, "-m", "navig", "miniapp", "deploy"], check=False)
    except Exception:  # noqa: BLE001
        pass


def _run_update(check=False, force=False, dry_run=False, channel=None, restart=True):
    con = _con()
    t_total = time.monotonic()
    from navig import __version__

    old_version = __version__
    src_dir = Path(__file__).resolve().parent.parent.parent
    is_git = _is_navig_git_checkout(src_dir)
    install_type = "git" if is_git else "pip"

    if check:
        if _RICH and con:
            grid = Table.grid(padding=(0, 2))
            grid.add_column(style="dim")
            grid.add_column(style="cyan bold")
            grid.add_row("Version", old_version)
            grid.add_row("Install", install_type)
            grid.add_row("Source", str(src_dir))
            if is_git:
                try:
                    log = subprocess.run(
                        ["git", "-C", str(src_dir), "log", "--oneline", "-1"],
                        capture_output=True,
                        text=True,
                        timeout=5, encoding="utf-8", errors="replace",
                    )
                    grid.add_row("Commit", log.stdout.strip()[:72])
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical
            con.print(
                Panel(
                    grid,
                    title="[bold]NAVIG[/bold]",
                    border_style="cyan",
                    padding=(0, 2),
                )
            )
            con.print("[dim]  Run [bold]navig update[/bold] to apply updates.[/dim]")
        else:
            print(f"NAVIG  v{old_version}  ({install_type})  {src_dir}")
        return

    if dry_run:
        _p(
            con,
            f"[dim]DRY RUN:[/dim]  Would upgrade NAVIG v[cyan]{old_version}[/cyan] ({install_type})",
        )
        return

    if _RICH and con:
        con.print()
        con.print(
            Rule(
                title=f"  [bold]NAVIG[/bold]  [dim]v{old_version}[/dim]  [dim]·[/dim]  [dim]{install_type}[/dim]  ",
                style="dim",
            )
        )
        con.print()
    else:
        print(f"\n-- NAVIG update  v{old_version}  ({install_type}) --\n")

    results = []

    def _run_step(label, fn):
        if _RICH and con:
            with Status(f"  [dim]{label}...[/dim]", console=con, spinner="dots"):
                r = fn()
        else:
            print(f"  ... {label}", flush=True)
            r = fn()
        results.append(r)
        if _RICH and con:
            icon = "[bold green]\u2713[/bold green]" if r.ok else "[bold red]\u2717[/bold red]"
            note_style = "dim" if r.ok else "yellow"
            note_part = (
                f"  [dim]\u00b7[/dim]  [{note_style}]{r.note[:52]}[/{note_style}]" if r.note else ""
            )
            con.print(f"  {icon}  {label}{note_part}  [dim]{r.elapsed:.1f}s[/dim]")
        else:
            status = "OK" if r.ok else "FAIL"
            note = f"  | {r.note}" if r.note else ""
            print(f"  {status}  {label}{note}  ({r.elapsed:.1f}s)")
        return r

    if is_git:
        r1 = _run_step("Sync with upstream", lambda: _step_git(src_dir, force))
    else:
        r1 = _run_step("Update via package manager", lambda: _step_pypi(force))

    if not r1.ok:
        _p(con, "\n[red]Update failed — see note above.[/red]")
        raise SystemExit(1)

    new_version = _reload_version()
    # Did we actually apply new code? On a git checkout the version string is unreliable
    # (it only bumps on a release tag), so trust the pull's HEAD delta; on a pip install
    # the version change IS the signal.
    applied = bool(r1.changed) if is_git else (new_version != old_version)
    will_reload = restart and applied

    # Skip the "Daemon" freshness row in the doctor summary when we're ABOUT to reload:
    # post-pull the running daemon is transiently "stale" (disk moved ahead of it) until the
    # reload step below restarts it, so printing "STALE — Restart to load: navig update"
    # DURING a navig update is self-contradictory. Under --no-restart (will_reload False) we
    # KEEP it — a daemon deliberately left on the old code IS worth flagging.
    _run_step(
        "Config doctor",
        lambda: _step_doctor(skip_sections={"Daemon"} if will_reload else frozenset()),
    )
    _run_step("Plugins", _step_plugins)

    # Make the new code LIVE: restart the running daemon. Without this, 'update' refreshes
    # disk but the running bot keeps executing the code it loaded at boot — the exact gap
    # this change closes. Only when something changed and the user didn't pass --no-restart.
    if will_reload:
        r_reload = _run_step(
            "Reload daemon (live code)",
            lambda: _step_reload_daemon(sys.stdin.isatty()),
        )
        if not r_reload.ok:
            _p(
                con,
                "[yellow]  Code is updated on disk, but the live daemon was NOT reloaded — "
                "it still runs the old code.[/yellow]",
            )

    total_elapsed = time.monotonic() - t_total

    if _RICH and con:
        con.print()
        if applied and is_git:
            title = (
                f"  [bold green]✓[/bold green]  [bold cyan]{r1.note}[/bold cyan]  "
                f"[dim]·  {total_elapsed:.1f}s[/dim]  "
            )
        elif applied:
            title = (
                f"  [bold green]\u2713[/bold green]  [dim]{old_version}[/dim]  [dim]→[/dim]  "
                f"[bold cyan]{new_version}[/bold cyan]  [dim]·  {total_elapsed:.1f}s[/dim]  "
            )
        else:
            title = (
                f"  [bold green]\u2713[/bold green]  [bold cyan]{new_version}[/bold cyan]  "
                f"[dim]up to date  ·  {total_elapsed:.1f}s[/dim]  "
            )
        con.print(Rule(title=title, style="green"))
        con.print()
    else:
        if applied and is_git:
            arrow = r1.note
        elif applied:
            arrow = f"{old_version} -> {new_version}"
        else:
            arrow = f"{new_version} up to date"
        print(f"\nOK  {arrow}  ({total_elapsed:.1f}s)\n")

    all_warnings = [w for r in results for w in r.warnings]
    if all_warnings and _RICH and con:
        lines = "\n".join(f"  [yellow]-[/yellow] {w}" for w in all_warnings)
        con.print(
            Panel(
                lines,
                title="[bold yellow]Warnings[/bold yellow]",
                border_style="yellow",
                padding=(0, 2),
            )
        )
        con.print()

    _sync_path(src_dir, con)

    # After a successful local update, offer to refresh the deployed edge + deck.
    _offer_redeploys(con)


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
    check: bool = typer.Option(
        False,
        "--check",
        "-c",
        help="[legacy] Check version only — alias for 'navig update check'.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="[legacy] Force update — alias for 'navig update run --force'.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="[legacy] Dry-run — alias for 'navig update run --dry-run'.",
    ),
    no_restart: bool = typer.Option(
        False,
        "--no-restart",
        help="Don't reload the running daemon after updating (leave the live bot on the old code).",
    ),
    channel: str | None = typer.Option(None, "--channel", hidden=True),
) -> None:
    """Upgrade NAVIG.

    Run ``navig update --help`` for all sub-commands or use the legacy
    flags ``--check`` / ``--force`` / ``--dry-run`` for backward compat.

    After a successful update the running daemon is restarted so the new code goes
    live immediately — pass ``--no-restart`` to skip that.
    """
    if ctx.invoked_subcommand is not None:
        return

    # Legacy backward-compat shim
    if check or (not force and not dry_run):
        _run_update(
            check=check or (not force and not dry_run),
            force=force,
            dry_run=dry_run,
            channel=channel,
            restart=not no_restart,
        )
        return

    _run_update(
        check=check, force=force, dry_run=dry_run, channel=channel, restart=not no_restart
    )


# ---------------------------------------------------------------------------
# navig update check
# ---------------------------------------------------------------------------


@update_app.command("check")
def update_check(
    host: str | None = typer.Option(None, "--host", "-H", help="Target host (default: local)."),
    group: str | None = typer.Option(None, "--group", "-g", help="Host group name."),
    all_hosts: bool = typer.Option(False, "--all", "-a", help="Check all configured hosts."),
    channel: str = typer.Option("stable", "--channel", help="Channel: stable, beta, nightly."),
    json_out: bool = typer.Option(False, "--json", help="Output JSON."),
) -> None:
    """Check current vs. latest available version (no changes made)."""
    from navig.config import get_config_manager
    from navig.update.lifecycle import UpdateEngine
    from navig.update.sources import build_source
    from navig.update.targets import TargetResolver

    cm = get_config_manager()
    src_cfg = cm.get("update.source", {"type": "pypi", "package": "navig"}) or {}
    if isinstance(src_cfg, str):
        src_cfg = {"type": src_cfg}

    try:
        source = build_source(src_cfg, channel)
    except Exception as exc:
        typer.echo(f"Error building source: {exc}", err=True)
        raise typer.Exit(1) from exc

    try:
        targets = TargetResolver(cm).resolve(host=host, group=group, all_hosts=all_hosts)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

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
            _p(
                con,
                f"[yellow][!][/yellow]  [bold]{vi.node_id}[/bold]  "
                f"[dim]{vi.current}[/dim] → [cyan bold]{vi.latest}[/cyan bold]  "
                f"[dim]({vi.source_name})[/dim]",
            )
        elif vi.error:
            _p(
                con,
                f"[red][✗][/red]  [bold]{vi.node_id}[/bold]  [red]{vi.error}[/red]",
            )
        else:
            _p(
                con,
                f"[green][✓][/green]  [bold]{vi.node_id}[/bold]  "
                f"[cyan]{vi.current}[/cyan]  [dim]up to date[/dim]",
            )


# ---------------------------------------------------------------------------
# navig update run
# ---------------------------------------------------------------------------


@update_app.command("run")
def update_run(
    host: str | None = typer.Option(None, "--host", "-H", help="Target host (default: local)."),
    group: str | None = typer.Option(None, "--group", "-g", help="Host group."),
    all_hosts: bool = typer.Option(False, "--all", "-a", help="Update all configured hosts."),
    channel: str = typer.Option("stable", "--channel", help="Channel: stable, beta, nightly."),
    force: bool = typer.Option(False, "--force", "-f", help="Update even if already on latest."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show plan without applying."),
    no_rollback: bool = typer.Option(
        False, "--no-rollback", help="Disable auto-rollback on failure."
    ),
    no_restart: bool = typer.Option(
        False,
        "--no-restart",
        help="Don't reload the local daemon after updating (leave the live bot on the old code).",
    ),
    skip_backup: bool = typer.Option(False, "--skip-backup", help="Skip pre-update version pin."),
    json_out: bool = typer.Option(False, "--json", help="Output JSON result."),
) -> None:
    """Apply updates to one or more nodes.

    After a verified local install the running daemon is restarted so the new code goes
    live immediately — pass ``--no-restart`` to skip that.
    """
    from navig.config import get_config_manager
    from navig.update.lifecycle import UpdateEngine
    from navig.update.sources import build_source
    from navig.update.targets import TargetResolver

    cm = get_config_manager()
    src_cfg = cm.get("update.source", {"type": "pypi", "package": "navig"}) or {}
    if isinstance(src_cfg, str):
        src_cfg = {"type": src_cfg}

    try:
        source = build_source(src_cfg, channel)
    except Exception as exc:
        typer.echo(f"Error building source: {exc}", err=True)
        raise typer.Exit(1) from exc

    try:
        targets = TargetResolver(cm).resolve(host=host, group=group, all_hosts=all_hosts)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    con = _con()
    if dry_run:
        _p(con, "[dim]DRY RUN: Showing plan — no changes will be made.[/dim]")

    def _progress(node_id: str, step: str, status: str, message: str) -> None:
        icons = {
            "ok": "[green]✓[/green]",
            "fail": "[red]✗[/red]",
            "running": "[dim]…[/dim]",
            "skip": "[dim]–[/dim]",
        }
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
        restart=not no_restart,
    )

    if json_out:
        import json as _json

        typer.echo(_json.dumps(result.to_dict(), indent=2))
    else:
        _p(con, "")
        if result.success:
            _p(
                con,
                f"[green][✓][/green]  All nodes updated  [dim]{result.total_elapsed_seconds:.1f}s[/dim]",
            )
        else:
            for nr in result.failed_nodes:
                _p(con, f"[red][✗][/red]  [bold]{nr.node_id}[/bold]  {nr.error}")

    if not result.success:
        raise typer.Exit(1)

    # Local update succeeded → offer to refresh the deployed Lighthouse edge + deck.
    if not host and not group and not all_hosts and not dry_run:
        _offer_redeploys(con)


# ---------------------------------------------------------------------------
# navig update rollback
# ---------------------------------------------------------------------------


@update_app.command("rollback")
def update_rollback(
    version: str = typer.Argument(..., help="Version to roll back to, e.g. 2.4.15"),
    host: str | None = typer.Option(None, "--host", "-H", help="Target host (default: local)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Roll back NAVIG to a specific version."""
    from navig.config import get_config_manager
    from navig.update.targets import TargetResolver

    cm = get_config_manager()
    try:
        targets = TargetResolver(cm).resolve(host=host)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    target = targets[0]
    con = _con()

    if not yes:
        _p(
            con,
            f"[yellow]Roll back [bold]{target.node_id}[/bold] to v[bold]{version}[/bold]?[/yellow]",
        )
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
        raise typer.Exit(1) from exc

    _p(con, f"[green]✓[/green]  Rolled back to v[cyan]{version}[/cyan]")


# ---------------------------------------------------------------------------
# navig update status
# ---------------------------------------------------------------------------


@update_app.command("status")
def update_status(
    host: str | None = typer.Option(None, "--host", "-H", help="Target host (default: local)."),
    json_out: bool = typer.Option(False, "--json", help="Output JSON."),
) -> None:
    """Show current installed version and update source config."""
    from navig.config import get_config_manager

    cm = get_config_manager()

    import navig as _nav

    current = getattr(_nav, "__version__", "?")
    src_dir = Path(__file__).resolve().parent.parent.parent
    install_type = "git" if _is_navig_git_checkout(src_dir) else "pip"
    src_cfg = cm.get("update.source", {"type": "pypi", "package": "navig"}) or {}
    channel = cm.get("update.channel", "stable") or "stable"

    if host and host not in ("local", "localhost"):
        from navig.update.sources import build_source
        from navig.update.targets import TargetResolver

        try:
            targets = TargetResolver(cm).resolve(host=host)
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1) from exc
        target = targets[0]
        try:
            source = build_source(
                src_cfg if isinstance(src_cfg, dict) else {"type": str(src_cfg)},
                channel,
            )
        except Exception as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1) from exc
        from navig.update.checker import VersionChecker

        vi = VersionChecker(source).check_ssh(target.node_id, target.server_config or {})
        if json_out:
            typer.echo(json.dumps(vi.to_dict(), indent=2))
        else:
            con = _con()
            _p(
                con,
                f"[bold]{vi.node_id}[/bold]  v[cyan]{vi.current}[/cyan]  [dim]({vi.install_type})[/dim]",
            )
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
        grid.add_row(
            "Source",
            src_cfg.get("type", "pypi") if isinstance(src_cfg, dict) else str(src_cfg),
        )
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
    node_id: str | None = typer.Option(None, "--node", help="Filter by node ID."),
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
            t.add_row(
                ts,
                e.get("node_id", "?"),
                e.get("old_version", "?"),
                e.get("new_version") or "—",
                e.get("channel", "?"),
                ok_str,
            )
        con.print(t)
    else:
        for e in entries:
            ts = (e.get("timestamp") or "")[:16]
            ok = "ok" if e.get("ok") else "fail"
            print(
                f"{ts}  {e.get('node_id', '?'):20}  "
                f"{e.get('old_version', '?'):12} → {e.get('new_version') or '?':12}  {ok}"
            )


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
        for name in cm.list_hosts() or []:
            nodes.append({"node_id": name, "type": "ssh"})
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

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
