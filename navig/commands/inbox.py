"""
CLI commands for the Inbox Router Agent.

Provides three commands:
  navig inbox process-current <file>  — process a single inbox file
  navig inbox process-all             — process all inbox .md files
  navig inbox dry-run                 — preview routing without writing

All commands use the InboxRouterAgent and execute_plan from
navig.agents.inbox_router.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import typer

logger = logging.getLogger("navig.commands.inbox")

inbox_app = typer.Typer(
    name="inbox",
    help="Inbox Router — classify and route .navig/plans/inbox/ files",
    invoke_without_command=True,
    no_args_is_help=True,
)


def _find_project_root() -> Path:
    """Walk up from cwd to find a directory containing .navig/."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".navig").is_dir():
            return parent
    # Fallback to cwd
    return cwd


def _print_plan(plan: dict, verbose: bool = False) -> None:
    """Pretty-print a single plan result."""
    source = Path(plan.get("source_file", "?")).name
    ctype = plan.get("content_type", "?")
    confidence = plan.get("confidence", "?")
    target = plan.get("target_path") or "(stays in inbox)"
    rationale = plan.get("rationale", "")

    status_icon = {
        "task_roadmap": "[PLAN]",
        "brief": "[BRIEF]",
        "wiki_knowledge": "[WIKI]",
        "memory_log": "[MEM]",
        "other": "[?]",
    }.get(ctype, "[?]")

    typer.echo(f"  {status_icon} {source}")
    typer.echo(f"    Type: {ctype}  Confidence: {confidence}")
    typer.echo(f"    Target: {target}")
    if rationale:
        typer.echo(f"    Rationale: {rationale}")
    if plan.get("error"):
        typer.secho(f"    Error: {plan['error']}", fg=typer.colors.RED)
    typer.echo("")


def _print_execution_result(result: dict) -> None:
    """Pretty-print an execution result."""
    status = result.get("status", "?")
    source = Path(result.get("source", "?")).name

    colors = {
        "written": typer.colors.GREEN,
        "dry_run": typer.colors.YELLOW,
        "kept_in_inbox": typer.colors.CYAN,
        "error": typer.colors.RED,
        "skipped": typer.colors.WHITE,
    }
    color = colors.get(status, typer.colors.WHITE)

    typer.secho(f"  [{status.upper()}] {source}", fg=color)
    if result.get("target"):
        typer.echo(f"    -> {result['target']}")
    if result.get("would_write"):
        typer.echo(f"    -> (would write) {result['would_write']}")
    if result.get("source_moved"):
        typer.echo(f"    Source moved to: {result['source_moved']}")
    if result.get("reason"):
        typer.echo(f"    Reason: {result['reason']}")
    if result.get("error"):
        typer.secho(f"    Error: {result['error']}", fg=typer.colors.RED)


@inbox_app.command("process-current")
def process_current(
    file: str = typer.Argument(..., help="Path to a single inbox .md file"),
    no_llm: bool = typer.Option(False, "--no-llm", help="Use heuristic only (no LLM)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing"),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
    no_move: bool = typer.Option(False, "--no-move", help="Don't move source after routing"),
    backend: str = typer.Option("cli_llm", "--backend", "-b", help="Backend caller (cli_llm or vscode_copilot)"),
) -> None:
    """Process a single inbox file — classify, transform, and route."""
    from navig.agents.inbox_router import InboxRouterAgent, execute_plan

    file_path = Path(file).resolve()
    if not file_path.exists():
        typer.secho(f"File not found: {file}", fg=typer.colors.RED)
        raise typer.Exit(1)

    project_root = _find_project_root()
    agent = InboxRouterAgent(project_root, use_llm=not no_llm, backend=backend)

    typer.echo(f"Processing: {file_path.name}")
    plan = agent.process_single(file_path, dry_run=dry_run)

    if json_output:
        typer.echo(json.dumps(plan, indent=2, default=str))
        return

    _print_plan(plan)

    if not dry_run and not plan.get("error"):
        result = execute_plan(project_root, plan, dry_run=False, move_source=not no_move)
        _print_execution_result(result)
    elif dry_run:
        result = execute_plan(project_root, plan, dry_run=True)
        _print_execution_result(result)


@inbox_app.command("process-all")
def process_all(
    no_llm: bool = typer.Option(False, "--no-llm", help="Use heuristic only (no LLM)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing"),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
    no_move: bool = typer.Option(False, "--no-move", help="Don't move source after routing"),
    backend: str = typer.Option("cli_llm", "--backend", "-b", help="Backend caller (cli_llm or vscode_copilot)"),
) -> None:
    """Process ALL .md files in .navig/plans/inbox/."""
    from navig.agents.inbox_router import InboxRouterAgent, execute_plan, list_inbox_files

    project_root = _find_project_root()
    files = list_inbox_files(project_root)

    if not files:
        typer.secho("No inbox files found in .navig/plans/inbox/", fg=typer.colors.YELLOW)
        raise typer.Exit(0)

    typer.echo(f"Found {len(files)} inbox file(s)\n")

    agent = InboxRouterAgent(project_root, use_llm=not no_llm, backend=backend)
    plans = agent.process_batch(files, dry_run=dry_run)

    if json_output:
        typer.echo(json.dumps(plans, indent=2, default=str))
        return

    results = []
    for plan in plans:
        _print_plan(plan)
        if not dry_run and not plan.get("error"):
            result = execute_plan(project_root, plan, dry_run=False, move_source=not no_move)
        else:
            result = execute_plan(project_root, plan, dry_run=True)
        results.append(result)
        _print_execution_result(result)

    # Summary
    written = sum(1 for r in results if r.get("status") == "written")
    kept = sum(1 for r in results if r.get("status") == "kept_in_inbox")
    errors = sum(1 for r in results if r.get("status") == "error")
    typer.echo(f"\nSummary: {written} routed, {kept} kept in inbox, {errors} errors")


@inbox_app.command("dry-run")
def dry_run(
    no_llm: bool = typer.Option(False, "--no-llm", help="Use heuristic only (no LLM)"),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
    backend: str = typer.Option("cli_llm", "--backend", "-b", help="Backend caller (cli_llm or vscode_copilot)"),
) -> None:
    """Preview routing for all inbox files (no files written or moved)."""
    from navig.agents.inbox_router import InboxRouterAgent, execute_plan, list_inbox_files

    project_root = _find_project_root()
    files = list_inbox_files(project_root)

    if not files:
        typer.secho("No inbox files found in .navig/plans/inbox/", fg=typer.colors.YELLOW)
        raise typer.Exit(0)

    typer.echo(f"Dry-run preview for {len(files)} inbox file(s)\n")

    agent = InboxRouterAgent(project_root, use_llm=not no_llm, backend=backend)
    plans = agent.process_batch(files, dry_run=True)

    if json_output:
        typer.echo(json.dumps(plans, indent=2, default=str))
        return

    for plan in plans:
        _print_plan(plan, verbose=True)
        result = execute_plan(project_root, plan, dry_run=True)
        _print_execution_result(result)

    # Summary
    types_count = {}
    for p in plans:
        ct = p.get("content_type", "other")
        types_count[ct] = types_count.get(ct, 0) + 1
    typer.echo("\nClassification summary:")
    for ct, count in sorted(types_count.items()):
        typer.echo(f"  {ct}: {count}")
