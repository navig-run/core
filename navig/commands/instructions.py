"""navig instructions — audit the instruction set a session actually loads."""

from __future__ import annotations

from typing import Optional

import typer

instructions_app = typer.Typer(
    help="Audit the instruction set a Claude session loads (budget, duplicates, links)",
    no_args_is_help=True,
)


@instructions_app.command("audit")
def audit_cmd(
    repo: Optional[str] = typer.Option(
        None, "--repo", help="Repository root (default: nearest folder with CLAUDE.md)"
    ),
    global_file: Optional[str] = typer.Option(
        None, "--global-file", help="Override the global ~/.claude/CLAUDE.md path"
    ),
    budget_bytes: int = typer.Option(
        40_000, "--budget-bytes", help="Budget for the repository's loaded set"
    ),
    file_budget_bytes: int = typer.Option(
        24_000, "--file-budget-bytes", help="Budget for any single loaded file"
    ),
    max_duplicate_bytes: int = typer.Option(
        0, "--max-duplicate-bytes", help="Duplicated bytes allowed across the set"
    ),
    check: bool = typer.Option(False, "--check", help="Exit 1 when a budget or check fails"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
    quiet: bool = typer.Option(
        False, "--quiet", help="Print only when something is violated"
    ),
) -> None:
    """Report the bytes, duplicates and broken links in the loaded instruction set."""
    import json

    from navig.core.instruction_audit import audit, find_repo, render

    # `resolve_user_path` anchors a relative value to where the operator STOOD:
    # main.py chdirs into the active space before the command runs, so a plain
    # Path("..") would resolve there instead of the checkout they typed it in.
    from navig.platform.paths import resolve_user_path

    root = resolve_user_path(repo) if repo else find_repo(resolve_user_path("."))
    global_path = resolve_user_path(global_file) if global_file else None
    result = audit(
        root,
        global_path=global_path,
        budget_bytes=budget_bytes,
        file_budget_bytes=file_budget_bytes,
        max_duplicate_bytes=max_duplicate_bytes,
    )

    if as_json:
        typer.echo(json.dumps(result.as_dict(), indent=2))
    elif not quiet or result.violations:
        typer.echo(render(result))

    if check and result.violations:
        raise typer.Exit(1)
