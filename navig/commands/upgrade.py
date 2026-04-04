"""NAVIG upgrade and version command implementations."""
from __future__ import annotations

import sys
from pathlib import Path

from navig import __version__
from navig import console_helper as ch


def run_version(json_output: bool = False) -> None:
    """Show NAVIG version and system info."""
    import json
    import platform
    import random

    from navig.cli._callbacks import _get_hacker_quotes

    if json_output:
        info = {
            "navig_version": __version__,
            "python_version": sys.version.split()[0],
            "platform": platform.system(),
            "platform_release": platform.release(),
            "machine": platform.machine(),
        }
        print(json.dumps(info, indent=2))
    else:
        ch.info(f"NAVIG v{__version__}")
        ch.dim(f"Python {sys.version.split()[0]} on {platform.system()} {platform.release()}")
        quote, author = random.choice(_get_hacker_quotes())
        ch.dim(f"💬 {quote} - {author}")


def run_upgrade(check: bool = False, force: bool = False) -> None:
    """Upgrade NAVIG to the latest version."""
    import shutil
    import subprocess

    from rich.console import Console as _RC

    _con = _RC()
    src_dir = Path(__file__).resolve().parent.parent.parent  # navig/commands/upgrade.py → navig-core/
    is_git = (src_dir / ".git").exists()

    # ------------------------------------------------------------------ check
    if check:
        if is_git:
            try:
                log = subprocess.run(
                    ["git", "-C", str(src_dir), "log", "--oneline", "-1"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                commit = log.stdout.strip()
                _con.print(f"[green]✓[/green] NAVIG v{__version__}  [dim]{commit}[/dim]")
                _con.print("[dim]Run [bold]navig upgrade[/bold] to pull latest commits.[/dim]")
            except Exception as exc:
                _con.print(f"[dim]Could not read git info: {exc}[/dim]")
        else:
            _con.print(f"[green]✓[/green] NAVIG v{__version__}")
            _con.print(
                "[dim]Run [bold]navig upgrade[/bold] to upgrade to the latest release.[/dim]"
            )
        return

    # ---------------------------------------------------------------- upgrade
    old_version = __version__
    success = False

    if is_git:
        _con.print(f"[cyan]▶[/cyan] Pulling latest from git… [dim]({src_dir})[/dim]")
        _git_env = {**__import__("os").environ, "GIT_TERMINAL_PROMPT": "0"}
        try:
            pull = subprocess.run(
                [
                    "git",
                    "-C",
                    str(src_dir),
                    "-c",
                    "http.connectTimeout=10",
                    "-c",
                    "http.lowSpeedLimit=0",
                    "-c",
                    "http.lowSpeedTime=20",
                    "pull",
                    "--ff-only",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                env=_git_env,
            )
            if pull.returncode != 0:
                err = pull.stderr.strip()
                _con.print(f"[red]✗[/red] git pull failed:\n[dim]{err[:300]}[/dim]")
                raise SystemExit(1)
            if "Already up to date" in pull.stdout and not force:
                _con.print(f"[green]✓[/green] Already up-to-date (v{old_version})")
                return
            _con.print(f"[dim]{pull.stdout.strip()}[/dim]")
        except FileNotFoundError as _exc:
            _con.print("[red]✗[/red] git not found — install git and retry")
            raise SystemExit(1) from _exc
        except subprocess.TimeoutExpired:
            _con.print(
                "[yellow]⚠[/yellow] git pull timed out (slow network) — reinstalling from local source"
            )

        _con.print("[cyan]▶[/cyan] Reinstalling package…")
        uv = shutil.which("uv")
        if uv:
            cmd = [uv, "pip", "install", "--python", sys.executable, "-e", str(src_dir), "-q"]
        else:
            cmd = [sys.executable, "-m", "pip", "install", "-e", str(src_dir), "-q"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            _con.print(
                f"[yellow]⚠[/yellow] Reinstall warning:\n[dim]{r.stderr.strip()[:300]}[/dim]"
            )
        success = True

    else:
        uv = shutil.which("uv")
        if uv:
            _con.print("[cyan]▶[/cyan] Upgrading via [bold]uv[/bold]…")
            cmd = [uv, "pip", "install", "--python", sys.executable, "--upgrade", "navig"]
            if force:
                cmd.append("--reinstall")
        else:
            _con.print("[cyan]▶[/cyan] Upgrading via [bold]pip[/bold]…")
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
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                _con.print(f"[red]✗[/red] Upgrade failed:\n[dim]{r.stderr.strip()[:400]}[/dim]")
                raise SystemExit(1)
            success = True
        except subprocess.TimeoutExpired as _exc:
            _con.print("[red]✗[/red] Upgrade timed out — check your network connection")
            raise SystemExit(1) from _exc

    if success:
        try:
            import importlib

            import navig as _nav

            importlib.reload(_nav)
            new_version = _nav.__version__
        except Exception:
            new_version = "?"
        if new_version != old_version:
            _con.print(
                f"[bold green]✓[/bold green] Upgraded [cyan]v{old_version}[/cyan] → [bold cyan]v{new_version}[/bold cyan]"
            )
        else:
            _con.print(f"[bold green]✓[/bold green] NAVIG v{new_version} is ready")

        try:
            _venv_exe = (
                Path(__file__).resolve().parent.parent.parent
                / ".venv"
                / "Scripts"
                / "navig.exe"
            )
            _path_navig = shutil.which("navig")
            _path_exe = Path(_path_navig) if _path_navig else None
            if _venv_exe.exists() and _path_exe and _path_exe.exists() and _venv_exe != _path_exe:
                shutil.copy2(str(_venv_exe), str(_path_exe))
                _con.print(f"[dim]↳ PATH entry point updated: {_path_exe}[/dim]")
        except Exception:
            pass  # Never fail the upgrade over a PATH sync issue
