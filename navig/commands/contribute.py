"""navig contribute — Self-Heal & Hive Mind Protocol CLI command.

Orchestrates the full contribution pipeline:

    navig contribute scan          # Run a full scan + approval + PR flow
    navig contribute scan --dry-run  # Scan only, print report, no PR
    navig contribute status          # Show config, fork URL, last scan

All operations are opt-in.  If ``contribute.enabled`` is not ``true`` in
``navig.yaml``, the command exits with an informative message.

Users must explicitly approve every submission — no silent auto-PRs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

# ---------------------------------------------------------------------------
# Typer app
# ---------------------------------------------------------------------------

contribute_app = typer.Typer(
    name="contribute",
    help="Self-Heal & Hive Mind Protocol — scan, review, and contribute fixes to navig-run/core.",
    no_args_is_help=True,
)

_console = Console()


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _get_config() -> dict[str, Any]:
    """Return the full ``contribute`` config block from navig.yaml.

    Returns:
        ``contribute`` sub-dict, defaulting to an empty dict if absent.
    """
    # ConfigManager is deferred to keep startup fast (<50 ms rule).
    from navig.config import get_config_manager  # noqa: PLC0415

    cm = get_config_manager()
    return cm.get("contribute", {}) or {}


def _get_contribute_config(raw: dict[str, Any]) -> ContributeConfig:
    """Construct a typed :class:`ContributeConfig` from a raw config dict.

    Centralises all dict-key access so contribute.py never references raw
    string keys directly (avoids key-name drift vs. init.py).

    Args:
        raw: Raw ``contribute`` sub-dict from navig.yaml.

    Returns:
        Populated :class:`~navig.selfheal.ContributeConfig` instance.
    """
    from navig.selfheal import ContributeConfig  # noqa: PLC0415

    return ContributeConfig.from_dict(raw)


def _assert_enabled(cfg: dict[str, Any]) -> None:
    """Exit with an informative message when Contribution Mode is disabled.

    Args:
        cfg: ``contribute`` config dict.

    Raises:
        typer.Exit: When ``contribute.enabled`` is not ``True``.
    """
    if not cfg.get("enabled", False):
        _console.print(
            "[yellow]Contribution Mode is disabled.[/yellow]\n"
            "Enable it by running [cyan]navig init[/cyan] and choosing "
            "Contribution Mode, or manually add to navig.yaml:\n\n"
            "  [green]contribute:\\n    enabled: true[/green]"
        )
        raise typer.Exit(0)


def _get_install_path(repo_path: Path) -> Path:
    """Return the ``navig/`` package directory inside the cloned repo.

    Args:
        repo_path: Root of the cloned ``~/.navig/core-repo/`` directory.

    Returns:
        Path to the ``navig/`` sub-package.
    """
    pkg_dir = repo_path / "navig"
    if pkg_dir.is_dir():
        return pkg_dir
    # Fallback: scan repo root if package layout differs.
    return repo_path


# ---------------------------------------------------------------------------
# ``navig contribute scan``
# ---------------------------------------------------------------------------


@contribute_app.command("scan")
def scan_cmd(
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Override the directory to scan (default: cloned navig-run/core package).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print the scan report without applying patches or opening a PR.",
    ),
) -> None:
    """Scan local NAVIG source, review findings, and (optionally) submit a PR.

    Full automated flow:

    \\b
    1. Sync fork with upstream navig-run/core
    2. LLM scan of Python source files
    3. Approval via Telegram bot or CLI
    4. Apply patch, commit, push
    5. Open Pull Request on navig-run/core
    """
    cfg = _get_config()
    _assert_enabled(cfg)
    cfg_typed = _get_contribute_config(cfg)

    from navig.selfheal.git_manager import (  # noqa: PLC0415
        _CORE_REPO_DIR,
        clone_or_update,
        create_branch,
        fork_repo,
        get_github_username,
        sync_fork,
    )
    from navig.selfheal.scanner import scan_files  # noqa: PLC0415

    # ------------------------------------------------------------------
    # Resolve GitHub token + username
    # ------------------------------------------------------------------
    try:
        from navig.selfheal.pr_builder import _resolve_token  # noqa: PLC0415

        token = _resolve_token(cfg_typed.to_dict())
        username = get_github_username(token)
    except ValueError as exc:
        _console.print(f"[red]GitHub token error:[/red] {exc}")
        _console.print(
            "Set up your token with:\n"
            "  [cyan]navig vault provider set github_contribute[/cyan]\n"
            "  or export NAVIG_GITHUB_TOKEN=<your-pat>"
        )
        raise typer.Exit(1) from exc

    # ------------------------------------------------------------------
    # Fork + clone/sync
    # ------------------------------------------------------------------
    if not dry_run:
        with _console.status("Forking navig-run/core…"):
            try:
                fork_url = fork_repo(token, username)
                fork_clone_url = f"https://github.com/{username}/navig.git"
                repo_path = clone_or_update(fork_clone_url)
                sync_fork(repo_path)
            except Exception as exc:  # noqa: BLE001
                _console.print(f"[red]Git setup failed:[/red] {exc}")
                raise typer.Exit(1) from exc
    else:
        repo_path = _CORE_REPO_DIR
        _console.print("[dim]Dry-run mode: skipping fork/sync.[/dim]")

    # ------------------------------------------------------------------
    # Determine scan target
    # ------------------------------------------------------------------
    scan_target = path or _get_install_path(repo_path)
    if not scan_target.exists():
        _console.print(f"[red]Scan path not found:[/red] {scan_target}")
        raise typer.Exit(1)

    _console.print(f"[bold]Scanning:[/bold] {scan_target}")

    # ------------------------------------------------------------------
    # LLM scan
    # ------------------------------------------------------------------
    with _console.status("Running LLM scan (this may take a minute)…"):
        try:
            findings = scan_files(scan_target, cfg_typed.to_dict())
        except Exception as exc:  # noqa: BLE001
            _console.print(f"[red]Scan failed:[/red] {exc}")
            raise typer.Exit(1) from exc

    if not findings:
        _console.print("[green]✓ No issues found above the confidence threshold.[/green]")
        raise typer.Exit(0)

    # ------------------------------------------------------------------
    # Print report
    # ------------------------------------------------------------------
    _print_scan_report(findings)

    if dry_run:
        _console.print("[dim]Dry-run mode: no patch or PR submitted.[/dim]")
        raise typer.Exit(0)

    # ------------------------------------------------------------------
    # Branch + approval flow + PR
    # ------------------------------------------------------------------
    with _console.status("Creating branch…"):
        branch = create_branch(repo_path)

    _console.print(f"[bold]Branch:[/bold] {branch}")

    alias: str = cfg_typed.alias
    version: str | None = None
    try:
        from navig import __version__ as _v  # noqa: PLC0415

        version = _v
    except Exception:  # noqa: BLE001
        pass

    from navig.bot.contribute_flow import run_approval_flow  # noqa: PLC0415

    pr_url = run_approval_flow(
        findings=findings,
        branch=branch,
        config=cfg_typed.to_dict(),
        alias=alias,
        version=version,
    )

    if pr_url:
        _console.print(
            f"\n[bold green]✅ PR submitted:[/bold green] [link={pr_url}]{pr_url}[/link]"
        )
    else:
        _console.print("[dim]No PR submitted (Telegram flow pending or cancelled).[/dim]")


# ---------------------------------------------------------------------------
# ``navig contribute status``
# ---------------------------------------------------------------------------


@contribute_app.command("status")
def status_cmd() -> None:
    """Show Contribution Mode configuration and fork status."""
    cfg = _get_config()
    cfg_typed = _get_contribute_config(cfg)

    table = Table(title="Contribution Mode Status", show_header=False, padding=(0, 1))
    table.add_column("Key", style="cyan")
    table.add_column("Value")

    table.add_row("Enabled", "[green]yes[/green]" if cfg_typed.enabled else "[red]no[/red]")
    table.add_row("Min confidence", str(cfg_typed.min_confidence))
    table.add_row("Token env var", cfg_typed.github_token_env)
    table.add_row("Target repo", cfg_typed.upstream_repo)
    table.add_row("Contributor alias", cfg_typed.alias or "—")

    from navig.selfheal.git_manager import _CORE_REPO_DIR  # noqa: PLC0415

    table.add_row("Local clone", str(_CORE_REPO_DIR))
    clone_exists = (_CORE_REPO_DIR / ".git").exists()
    table.add_row("Clone present", "[green]yes[/green]" if clone_exists else "[dim]no[/dim]")

    _console.print(table)

    if not cfg_typed.enabled:
        _console.print(
            "\nRun [cyan]navig init[/cyan] to enable Contribution Mode, "
            "or add [green]contribute:\\n  enabled: true[/green] to navig.yaml."
        )


# ---------------------------------------------------------------------------
# Report helper
# ---------------------------------------------------------------------------


def _print_scan_report(findings: list) -> None:
    """Print a formatted scan report to the console.

    Args:
        findings: List of :class:`~navig.selfheal.scanner.ScanFinding` objects.
    """
    from rich.text import Text  # noqa: PLC0415

    sev_colors = {
        "critical": "red",
        "high": "orange3",
        "medium": "yellow",
        "low": "blue",
    }

    table = Table(
        title=f"Scan Report — {len(findings)} finding(s)",
        show_lines=False,
    )
    table.add_column("Severity", width=9)
    table.add_column("Confidence", justify="right", width=10)
    table.add_column("File:Line", width=40)
    table.add_column("Description")

    for f in findings:
        color = sev_colors.get(f.severity, "white")
        table.add_row(
            Text(f.severity, style=color),
            f"{f.confidence:.2f}",
            f"{f.file}:{f.line}",
            f.description[:80],
        )

    _console.print(table)
