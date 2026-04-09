from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from navig.console_helper import get_console
from navig.spaces import get_default_space, normalize_space_name
from navig.spaces.briefing import build_spaces_briefing_lines
from navig.spaces.next_action import get_space_next_action, select_best_next_action
from navig.spaces.progress import collect_spaces_progress

plans_app = typer.Typer(
    name="plans",
    help="Plans and progress across global + project spaces",
    invoke_without_command=True,
    no_args_is_help=False,
)

_console = get_console()
_FRONTMATTER_RE = re.compile(r"^---\n([\s\S]*?)\n---\n?", re.MULTILINE)
_CHECKBOX_RE = re.compile(r"^\s*-\s*\[([ xX])\]", re.MULTILINE)


def _find_project_root(start: Path | None = None) -> Path:
    base = (start or Path.cwd()).resolve()
    for parent in [base, *base.parents]:
        if (parent / ".navig").is_dir():
            return parent
    return base


def _plans_dir(path: str | None = None) -> Path:
    base = Path(path).resolve() if path else Path.cwd()
    project_root = _find_project_root(base)
    plans = project_root / ".navig" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    return plans


def _ensure_baseline_files(plans_dir: Path) -> None:
    inbox_dir = plans_dir / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)

    dev_plan = plans_dir / "DEV_PLAN.md"
    if not dev_plan.exists():
        dev_plan.write_text("# DEV Plan\n\n## Added Goals\n", encoding="utf-8")

    current_phase = plans_dir / "CURRENT_PHASE.md"
    if not current_phase.exists():
        current_phase.write_text(
            "---\ncompletion_pct: 0.0\nlast_updated: n/a\n---\n\n# Current Phase\n\n- [ ] Define initial milestone\n",
            encoding="utf-8",
        )


def _target_plan_file(plans_dir: Path, file: str) -> Path:
    target = Path(file)
    if target.is_absolute():
        return target
    if target.parts and target.parts[0] == ".navig":
        return _find_project_root(plans_dir).joinpath(*target.parts)
    return plans_dir / target


def _insert_under_section(content: str, section: str, line: str) -> str:
    normalized = content if content.endswith("\n") else content + "\n"
    if section not in normalized:
        return f"{normalized}\n{section}\n{line}\n"

    lines = normalized.splitlines()
    section_idx = -1
    for idx, raw in enumerate(lines):
        if raw.strip() == section:
            section_idx = idx
            break

    if section_idx == -1:
        return f"{normalized}\n{section}\n{line}\n"

    insert_at = section_idx + 1
    while insert_at < len(lines) and not lines[insert_at].startswith("## "):
        insert_at += 1

    lines.insert(insert_at, line)
    return "\n".join(lines).rstrip() + "\n"


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    values: dict[str, str] = {}
    for raw in match.group(1).splitlines():
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        values[key.strip()] = value.strip()

    return values, text[match.end() :]


def _render_frontmatter(values: dict[str, str]) -> str:
    lines = ["---"]
    for key, value in values.items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def _completion_from_markdown(text: str) -> float:
    checks = _CHECKBOX_RE.findall(text)
    if not checks:
        return 0.0
    done = sum(1 for item in checks if item.lower() == "x")
    return round((done / len(checks)) * 100.0, 1)


@plans_app.callback()
def _plans_callback(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        import os as _os

        if _os.environ.get("NAVIG_LAUNCHER", "fuzzy") == "legacy":
            plans_status()
            raise typer.Exit()
        from navig.cli.launcher import smart_launch

        smart_launch("plans", plans_app)


@plans_app.command("status")
def plans_status(
    path: str | None = typer.Option(None, "--path", "-p", help="Workspace path"),
) -> None:
    """Show current progress by resolved space."""
    cwd = Path(path).resolve() if path else Path.cwd()
    rows = collect_spaces_progress(cwd=cwd)

    if not rows:
        typer.secho("No spaces discovered in project or global scope.", fg=typer.colors.YELLOW)
        raise typer.Exit(0)

    table = Table(title="NAVIG Spaces Progress")
    table.add_column("Space", style="cyan", no_wrap=True)
    table.add_column("Scope", style="magenta", no_wrap=True)
    table.add_column("Progress", justify="right", no_wrap=True)
    table.add_column("Last Updated", no_wrap=True)
    table.add_column("Goal", overflow="fold")

    for row in rows:
        table.add_row(
            row.name,
            row.scope,
            f"{row.completion_pct:.1f}%",
            row.last_updated,
            row.goal,
        )

    _console.print(table)


@plans_app.command("run")
def plans_run(
    goal: str = typer.Argument(..., help="Goal to add to plan"),
    file: str = typer.Option("DEV_PLAN.md", "--file", help="Target plan file"),
    space: str | None = typer.Option(None, "--space", help="Space tag for the entry"),
    path: str | None = typer.Option(None, "--path", "-p", help="Workspace path"),
) -> None:
    typer.secho("`plans run` is deprecated. Use `plans add`.", fg=typer.colors.YELLOW)
    plans_add(goal=goal, file=file, space=space, path=path)


@plans_app.command("add")
def plans_add(
    goal: str = typer.Argument(..., help="Goal to add to plan"),
    file: str = typer.Option("DEV_PLAN.md", "--file", help="Target plan file"),
    space: str | None = typer.Option(None, "--space", help="Space tag for the entry"),
    path: str | None = typer.Option(None, "--path", "-p", help="Workspace path"),
) -> None:
    plans_dir = _plans_dir(path)
    _ensure_baseline_files(plans_dir)

    resolved_space = normalize_space_name(space or get_default_space())
    target_file = _target_plan_file(plans_dir, file)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    if not target_file.exists():
        target_file.write_text("# Plan\n\n## Added Goals\n", encoding="utf-8")

    entry = f"- [ ] [{resolved_space}] {goal.strip()}"
    text = target_file.read_text(encoding="utf-8")
    if entry in text:
        typer.secho("Goal already present in plan; skipping duplicate entry.", fg=typer.colors.YELLOW)
        raise typer.Exit(0)

    updated = _insert_under_section(text, "## Added Goals", entry)
    target_file.write_text(updated, encoding="utf-8")
    typer.secho(f"Added goal to {target_file}", fg=typer.colors.GREEN)


@plans_app.command("briefing")
def plans_briefing(
    path: str | None = typer.Option(None, "--path", "-p", help="Workspace path"),
) -> None:
    cwd = Path(path).resolve() if path else Path.cwd()
    lines = ["Daily spaces briefing:"]
    lines.extend(build_spaces_briefing_lines(cwd=cwd, max_items=5))
    typer.echo("\n".join(lines))


@plans_app.command("next")
def plans_next(
    space: str | None = typer.Option(None, "--space", help="Specific space to inspect"),
    path: str | None = typer.Option(None, "--path", "-p", help="Workspace path"),
) -> None:
    cwd = Path(path).resolve() if path else Path.cwd()
    action = (
        get_space_next_action(normalize_space_name(space), cwd=cwd)
        if space
        else select_best_next_action(cwd=cwd)
    )

    if not action:
        typer.secho("No spaces or actionable tasks found.", fg=typer.colors.YELLOW)
        raise typer.Exit(0)

    typer.echo(f"Space: {action.space} ({action.scope})")
    typer.echo(f"Goal : {action.goal}")
    typer.echo(f"Done : {action.completion_pct:.1f}%")
    typer.echo(f"Next : {action.next_task or 'Define next concrete task in CURRENT_PHASE.md'}")


@plans_app.command("sync")
def plans_sync(
    no_llm: bool = typer.Option(False, "--no-llm", help="Use heuristic only (no LLM)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview routing only"),
    no_move: bool = typer.Option(False, "--no-move", help="Do not move processed source files"),
    space: str | None = typer.Option(None, "--space", help="Manual space override"),
    backend: str = typer.Option("cli_llm", "--backend", "-b", help="Backend caller metadata"),
    path: str | None = typer.Option(None, "--path", "-p", help="Workspace path"),
) -> None:
    from navig.agents.inbox_router import InboxRouterAgent, execute_plan, list_inbox_files

    plans_dir = _plans_dir(path)
    project_root = _find_project_root(plans_dir)
    files = list_inbox_files(project_root)

    if not files:
        typer.secho("No inbox files found in .navig/plans/inbox/", fg=typer.colors.YELLOW)
        raise typer.Exit(0)

    agent = InboxRouterAgent(project_root, use_llm=not no_llm, backend=backend)
    plans = agent.process_batch(files, dry_run=dry_run, manual_space=space)

    results = []
    for plan in plans:
        result = execute_plan(
            project_root,
            plan,
            dry_run=dry_run,
            move_source=not no_move,
        )
        results.append(result)

    written = sum(1 for item in results if item.get("status") == "written")
    kept = sum(1 for item in results if item.get("status") == "kept_in_inbox")
    previews = sum(1 for item in results if item.get("status") == "dry_run")
    errors = sum(1 for item in results if item.get("status") == "error")

    if dry_run:
        typer.echo(f"Dry-run summary: {previews} previews, {kept} kept in inbox, {errors} errors")
    else:
        typer.echo(f"Sync summary: {written} routed, {kept} kept in inbox, {errors} errors")


@plans_app.command("summary")
def plans_summary(
    path: str | None = typer.Option(None, "--path", "-p", help="Workspace path"),
) -> None:
    """Show cross-space phase rollup table.

    Columns: Space | Phase | Status | Completion | Last Updated.
    """
    from navig.spaces.resolver import discover_space_paths

    def _first_non_frontmatter_line(text: str) -> str:
        _, body = _split_frontmatter(text)
        for line in body.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped.lstrip("# ").strip()
        return "—"

    cwd = Path(path).resolve() if path else Path.cwd()
    discovered = discover_space_paths(cwd=cwd)

    if not discovered:
        typer.secho("No spaces discovered in project or global scope.", fg=typer.colors.YELLOW)
        raise typer.Exit(0)

    rows: list[tuple[str, str, str, str, str, bool]] = []
    for space_name, cfg in discovered.items():
        phase_file = cfg.path / "CURRENT_PHASE.md"
        if not phase_file.is_file():
            rows.append((space_name, "—", "—", "—", "—", True))
            continue

        try:
            text = phase_file.read_text(encoding="utf-8", errors="replace")
            fm, _ = _split_frontmatter(text)
            phase_title = _first_non_frontmatter_line(text)
            status = fm.get("status", "—") or "—"
            completion_raw = fm.get("completion_pct", "—") or "—"
            if completion_raw != "—":
                try:
                    completion = f"{float(completion_raw):.0f}%"
                except ValueError:
                    completion = str(completion_raw)
            else:
                completion = "—"
            last_updated = fm.get("last_updated", "—") or "—"
            rows.append((space_name, phase_title, status, completion, last_updated, False))
        except Exception:
            rows.append((space_name, "—", "—", "—", "—", True))

    # Sort by last_updated desc; missing values last.
    rows.sort(key=lambda row: (row[4] != "—", row[4]), reverse=True)

    table = Table()
    table.add_column("Space", style="cyan", no_wrap=True)
    table.add_column("Phase", overflow="fold")
    table.add_column("Status", no_wrap=True)
    table.add_column("Completion", justify="right", no_wrap=True)
    table.add_column("Last Updated", no_wrap=True)

    for space_name, phase_title, status, completion, last_updated, dimmed in rows:
        table.add_row(
            space_name,
            phase_title,
            status,
            completion,
            last_updated,
            style="dim" if dimmed else None,
        )

    _console.print(table)


@plans_app.command("update")
def plans_update(
    file: str = typer.Argument("CURRENT_PHASE.md", help="Plan markdown file to update"),
    path: str | None = typer.Option(None, "--path", "-p", help="Workspace path"),
) -> None:
    plans_dir = _plans_dir(path)
    _ensure_baseline_files(plans_dir)
    target = _target_plan_file(plans_dir, file)

    if not target.exists():
        typer.secho(f"Plan file not found: {target}", fg=typer.colors.RED)
        raise typer.Exit(1)

    text = target.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text)
    content = body if body.strip() else text
    completion_pct = _completion_from_markdown(content)

    if "goal" not in frontmatter:
        frontmatter["goal"] = target.stem.replace("_", " ")
    frontmatter["completion_pct"] = f"{completion_pct:.1f}"
    frontmatter["last_updated"] = datetime.now().strftime("%Y-%m-%d")

    updated = _render_frontmatter(frontmatter) + content.lstrip("\n")
    target.write_text(updated, encoding="utf-8")
    typer.secho(
        f"Updated {target.name}: completion_pct={completion_pct:.1f}%",
        fg=typer.colors.GREEN,
    )
