"""
NAVIG Flow CLI Commands

Commands for managing and executing reusable command flows (workflows).
"""

from typing import Any, Dict, List, Optional

import typer

from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")

flow_app = typer.Typer(
    name="flow",
    help="Manage and execute reusable command flows (workflows)",
    no_args_is_help=True,
)


@flow_app.command("list")
def flow_list():
    """List all available flows."""
    from navig.commands.workflow import list_workflows

    list_workflows()


@flow_app.command("show")
def flow_show(name: str = typer.Argument(..., help="Flow name")):
    """Display flow definition and steps."""
    from navig.commands.workflow import show_workflow

    show_workflow(name)


@flow_app.command("run")
def flow_run(
    name: str = typer.Argument(..., help="Flow name"),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Preview without executing"
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip all confirmation prompts"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
    var: Optional[List[str]] = typer.Option(
        None, "--var", "-V", help="Variable override (name=value)"
    ),
):
    """Execute a flow."""
    from navig.commands.workflow import run_workflow

    run_workflow(name, dry_run=dry_run, yes=yes, verbose=verbose, var=var or [])


@flow_app.command("test")
def flow_test(name: str = typer.Argument(..., help="Flow name")):
    """Test/validate flow syntax and structure."""
    from navig.commands.workflow import validate_workflow

    validate_workflow(name)


@flow_app.command("add")
def flow_add(
    name: str = typer.Argument(..., help="New flow name"),
    global_scope: bool = typer.Option(
        False, "--global", "-g", help="Create in global directory"
    ),
):
    """Create a new flow."""
    from navig.commands.workflow import create_workflow

    create_workflow(name, global_scope=global_scope)


@flow_app.command("edit")
def flow_edit(name: str = typer.Argument(..., help="Flow name")):
    """Open flow in default editor."""
    from navig.commands.workflow import edit_workflow

    edit_workflow(name)


@flow_app.command("remove")
def flow_remove(
    name: str = typer.Argument(..., help="Flow name"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Remove a flow."""
    from navig.commands.workflow import delete_workflow

    delete_workflow(name, force=force)


# ============================================================================
# Interactive Menu Wrapper Functions
# ============================================================================
# These functions provide a consistent interface for the interactive menu system.
# Each wrapper calls the underlying Typer command with appropriate defaults.


def list_flows_cmd(ctx: Dict[str, Any]) -> None:
    """Wrapper for flow list command (interactive menu)."""
    flow_list()


def show_flow_cmd(name: str, ctx: Dict[str, Any]) -> None:
    """Wrapper for flow show command (interactive menu)."""
    flow_show(name)


def run_flow_cmd(name: str, ctx: Dict[str, Any]) -> None:
    """Wrapper for flow run command (interactive menu)."""
    flow_run(name, dry_run=False, yes=False, verbose=True, var=None)


def test_flow_cmd(name: str, ctx: Dict[str, Any]) -> None:
    """Wrapper for flow test command (interactive menu)."""
    flow_test(name)


def add_flow_cmd(name: str, ctx: Dict[str, Any]) -> None:
    """Wrapper for flow add command (interactive menu)."""
    flow_add(name, global_scope=False)


def edit_flow_cmd(name: str, ctx: Dict[str, Any]) -> None:
    """Wrapper for flow edit command (interactive menu)."""
    flow_edit(name)


def remove_flow_cmd(name: str, ctx: Dict[str, Any]) -> None:
    """Wrapper for flow remove command (interactive menu)."""
    flow_remove(name, force=True)  # Force=True since menu already confirms
