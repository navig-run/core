"""navig sync — configuration and state synchronisation."""
from __future__ import annotations

from pathlib import Path

import typer

sync_app = typer.Typer(help="Sync NAVIG configuration and state across nodes", no_args_is_help=True)


@sync_app.command("status")
def sync_status():
    """Show sync status."""
    from navig import console_helper as ch

    ch.warning("navig sync is not yet implemented in this build.")


def _resolve_root(repo: str | None) -> Path | None:
    """Repo root holding MASTER: explicit --repo, else git toplevel, else walk up from cwd."""
    from navig.core.instruction_sync import MASTER_REL

    if repo:
        root = Path(repo).expanduser().resolve()
        return root if (root / MASTER_REL).is_file() else None

    from navig.commands.repo import repo_root

    root = repo_root(Path.cwd())
    if root and (root / MASTER_REL).is_file():
        return root
    # Fallback: walk up from cwd (works outside a git checkout / in a worktree).
    for parent in [Path.cwd(), *Path.cwd().parents]:
        if (parent / MASTER_REL).is_file():
            return parent
    return None


@sync_app.command("instructions")
def sync_instructions(
    repo: str = typer.Option(None, "--repo", help="Repo root holding MASTER (default: git toplevel of CWD)"),
    check: bool = typer.Option(False, "--check", help="Verify tracked mirrors match MASTER; write nothing; exit 1 on drift"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would change; write nothing"),
    agents: bool = typer.Option(False, "--agents", help="Also run the cross-project agent sync (scripts/sync-agents.py)"),
):
    """Regenerate every AI tool's instruction mirror from MASTER (cross-platform).

    MASTER (``.github/instructions/MASTER.instructions.md``) is the single source of truth;
    this writes ``.cursorrules``, ``.github/copilot-instructions.md``, ``.cline/memory.md``,
    ``.claude/CLAUDE.md``, ``.codex/AGENTS.md``, ``.gemini/GEMINI.md``, and injects the
    Copilot key into ``.vscode/settings.json`` WITHOUT touching any other setting.

    The Python equivalent of ``scripts/sync-instructions.ps1`` — so it runs on macOS/Linux
    too. ``--check`` is the CI/pre-commit drift gate.
    """
    from navig import console_helper as ch
    from navig.core import instruction_sync as sync

    root = _resolve_root(repo)
    if root is None:
        ch.error(
            "Could not find MASTER instructions.",
            f"Run inside a repo containing {sync.MASTER_REL}, or pass --repo <path>.",
        )
        raise typer.Exit(code=1)

    if check:
        results = sync.check_drift(root)
        drift = [rel for rel, state in results if state != "in sync"]
        for rel, state in results:
            (ch.success if state == "in sync" else ch.error)(f"{state:>8}  {rel}")
        if drift:
            ch.error(
                f"{len(drift)} mirror(s) out of sync with MASTER.",
                "Fix: navig sync instructions",
            )
            raise typer.Exit(code=1)
        ch.info("All tracked instruction mirrors are in sync with MASTER.")
        return

    ch.info(f"Syncing instruction mirrors from MASTER ({'dry run' if dry_run else root.name})")
    for rel, status, chars in sync.sync_all(root, dry_run=dry_run):
        if status == "unparseable":
            ch.warning(f"  SKIP    {rel} — unparseable JSON; backed up to {rel}.bak, left untouched")
        else:
            tag = "DRY" if status == "dry" else "WROTE"
            ch.success(f"  {tag:<5}  {rel} ({chars} chars)")

    if agents and not dry_run:
        _run_agent_sync(root, ch)

    ch.info("Sync complete." + (" Re-run without --dry-run to apply." if dry_run else ""))


def _run_agent_sync(root: Path, ch) -> None:
    """Best-effort cross-project agent sync (scripts/sync-agents.py — already cross-platform)."""
    import subprocess
    import sys

    script = root / "scripts" / "sync-agents.py"
    if not script.is_file():
        ch.dim("  agent sync: scripts/sync-agents.py not found — skipped")
        return
    ch.info("Cross-project agent sync (scripts/sync-agents.py):")
    try:
        subprocess.run([sys.executable, str(script), "--all"], cwd=str(root), check=False)
    except OSError as exc:  # pragma: no cover — never let agent sync break the mirror sync
        ch.warning(f"  agent sync failed to launch: {exc}")
