"""
Task/workflow commands — RETIRED engine, thin shims to Blocks.

The legacy **System-A** workflow engine (``WorkflowManager``: reusable *command
sequences* defined as YAML, run via ``navig task``/``navig flow``) has been
retired. Its builtin content migrated to **Blocks** — installable, verifiable
outcomes run with ``navig apply`` (shipped via the community registry). See
``docs/blocks-vs-workflows.md``.

These thin shims keep the ``navig task`` / ``navig flow`` / ``navig job`` verbs
alive (no CLI break): ``run <name>`` redirects to ``navig apply`` when a Block of
that name exists; authoring points at ``navig block new``. ``navig task complete``
(recording a completed *planning* task) is unrelated and unchanged.

Do NOT confuse this with **System B** (``navig/core/automation_engine.py``),
desktop GUI automation that shares class names but is a different thing and is
NOT retired.
"""

import os
import subprocess
import sys
from pathlib import Path

import typer

from navig import console_helper as ch

# One-liner reused across the shims.
_RETIRED = (
    "The YAML workflow engine is retired — workflows are now Blocks. "
    "Author: navig block new <id> · run: navig apply <id> · list: navig block list."
)


def _redirect_if_block(name: str) -> bool:
    """If a Block named ``name`` exists, print the ``navig apply`` redirect and
    return True; otherwise return False. Block discovery is import-lazy so the
    cold CLI stays fast."""
    try:
        from navig.blocks import find_block

        if find_block(name) is not None:
            ch.info(f"'{name}' is a Block. Run it with:  navig apply {name}")
            return True
    except Exception:  # noqa: BLE001 — discovery is best-effort here
        pass
    return False


# ============================================================================
# Shim functions (kept for `navig flow`/`job` which import these by name)
# ============================================================================


def list_workflows() -> None:
    """Deprecation shim — workflows became Blocks."""
    ch.warning("No flows — the workflow engine is retired.")
    ch.info("Blocks replaced them:  navig block list  ·  navig apply <id>")


def show_workflow(name: str) -> None:
    """Deprecation shim — redirect to `navig block show`/`navig apply`."""
    if _redirect_if_block(name):
        ch.dim(f"  inspect it:  navig block show {name}")
        return
    ch.warning(f"'{name}' not found. {_RETIRED}")


def run_workflow(
    name: str,
    dry_run: bool = False,
    yes: bool = False,
    verbose: bool = False,
    var: list[str] | None = None,
) -> None:
    """Deprecation shim — redirect to `navig apply` when the name is a Block."""
    if _redirect_if_block(name):
        return
    ch.error(f"'{name}' is not a Block. {_RETIRED}")
    raise SystemExit(1)


def validate_workflow(name: str) -> None:
    """Deprecation shim — redirect to `navig block verify`."""
    if _redirect_if_block(name):
        ch.dim(f"  validate it:  navig block verify {name}")
        return
    ch.warning(f"'{name}' not found. {_RETIRED}")


def create_workflow(name: str, global_scope: bool = False) -> None:
    """Deprecation shim — author a Block instead."""
    ch.warning(_RETIRED)
    ch.info(f"Create a Block:  navig block new {name}")


def delete_workflow(name: str, force: bool = False) -> None:
    """Deprecation shim — nothing to delete; the engine is retired."""
    ch.warning(_RETIRED)


def edit_workflow(name: str) -> None:
    """Deprecation shim — edit the Block's BLOCK.md instead."""
    ch.warning(_RETIRED)
    ch.info(f"Edit the Block:  navig block show {name}  (then edit its BLOCK.md)")


# ============================================================================
# task_app — Typer CLI group (verbs preserved; engine retired)
# ============================================================================


task_app = typer.Typer(
    help="Task/workflow management — retired, superseded by Blocks (see `navig apply`).",
    invoke_without_command=True,
    no_args_is_help=False,
)


@task_app.callback()
def task_callback(ctx: typer.Context):
    """Task management - run without subcommand to list tasks."""
    if ctx.invoked_subcommand is None:
        list_workflows()


@task_app.command("list")
def task_list():
    """List all available tasks/workflows (retired — see `navig block list`)."""
    list_workflows()


@task_app.command("show")
def task_show(name: str = typer.Argument(..., help="Task name")):
    """Display task definition (retired — redirects to `navig block show`)."""
    show_workflow(name)


@task_app.command("run")
def task_run(
    name: str = typer.Argument(..., help="Task name"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Preview without executing"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmations"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Detailed output"),
    var: list[str] | None = typer.Option(None, "--var", "-V", help="Variable (name=value)"),
):
    """Execute a task/workflow (retired). If the name is a Block, use `navig apply`."""
    run_workflow(name, dry_run=dry_run, yes=yes, verbose=verbose, var=var or [])


@task_app.command("test")
def task_test(name: str = typer.Argument(..., help="Task name")):
    """Validate task syntax (retired — redirects to `navig block verify`)."""
    validate_workflow(name)


@task_app.command("add")
def task_add(
    name: str = typer.Argument(..., help="New task name"),
    global_scope: bool = typer.Option(False, "--global", "-g", help="Create globally"),
):
    """Create a new task from template (retired — use `navig block new`)."""
    create_workflow(name, global_scope=global_scope)


@task_app.command("remove")
def task_remove(
    name: str = typer.Argument(..., help="Task name"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Delete a task (retired)."""
    delete_workflow(name, force=force)


@task_app.command("edit")
def task_edit(name: str = typer.Argument(..., help="Task name")):
    """Open task in default editor (retired — edit the Block's BLOCK.md)."""
    edit_workflow(name)


@task_app.command("complete")
def task_complete(
    task_title: str = typer.Argument(..., help="Human-readable task title"),
    task_slug: str = typer.Argument(..., help="kebab-case unique slug"),
    summary: str = typer.Argument(..., help="One-sentence completion summary"),
    phase_name: str = typer.Argument(..., help="Phase name (e.g. phase-1)"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Validate; skip all writes"),
    now_date: str | None = typer.Option(None, "--date", "-d", help="Override date YYYY-MM-DD"),
) -> None:
    """Record a completed task — runs complete-task.sh (Unix) or complete-task.ps1 (Windows)."""
    # Locate complete-task script by walking up from cwd
    cwd = Path.cwd()
    project_root: Path | None = None
    for parent in [cwd, *cwd.parents]:
        if (parent / ".navig").is_dir():
            project_root = parent
            break

    if project_root is None:
        typer.secho(
            "ERROR: could not find .navig/ directory within cwd ancestry",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    scripts_dir = project_root / ".navig" / "scripts"
    is_windows = sys.platform == "win32"
    script = scripts_dir / ("complete-task.ps1" if is_windows else "complete-task.sh")

    if not script.exists():
        typer.secho(f"ERROR: script not found at {script}", fg=typer.colors.RED)
        raise typer.Exit(1)

    env = os.environ.copy()
    if dry_run:
        env["NAVIG_DRY_RUN"] = "1"
    if now_date:
        env["NAVIG_NOW"] = now_date

    if is_windows:
        cmd = [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            task_title,
            task_slug,
            summary,
            phase_name,
        ]
    else:
        cmd = ["bash", str(script), task_title, task_slug, summary, phase_name]

    result = subprocess.run(cmd, env=env, cwd=str(cwd))
    raise typer.Exit(result.returncode)
