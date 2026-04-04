"""
NAVIG Flow CLI Commands

Commands for managing and executing reusable command flows (workflows).
"""

from typing import Any

import typer

from navig.cli._callbacks import show_subcommand_help
from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")

flow_app = typer.Typer(
    name="flow",
    help="Manage and execute reusable command flows (workflows)",
    invoke_without_command=True,
    no_args_is_help=False,
)


@flow_app.callback()
def flow_callback(ctx: typer.Context):
    """Flow management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("flow", ctx)
        raise typer.Exit()


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
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Preview without executing"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip all confirmation prompts"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
    var: list[str] | None = typer.Option(
        None,
        "--var",
        "-V",
        help="Variable override(s) as name=value; can be used multiple times, e.g. --var VAR1=foo --var VAR2=bar",
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
    global_scope: bool = typer.Option(False, "--global", "-g", help="Create in global directory"),
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


# Nested: flow template (consolidates template + addon)
flow_template_app = typer.Typer(
    help="Manage server templates and extensions",
    invoke_without_command=True,
    no_args_is_help=False,
)
flow_app.add_typer(flow_template_app, name="template")


@flow_template_app.callback()
def flow_template_callback(ctx: typer.Context):
    """Template management - run without subcommand for interactive menu."""
    if ctx.invoked_subcommand is None:
        from navig.commands.interactive import launch_template_menu

        launch_template_menu()
        raise typer.Exit()


@flow_template_app.command("list")
def flow_template_list(
    ctx: typer.Context,
    plain: bool = typer.Option(False, "--plain", help="Output plain text for scripting"),
):
    """List all available templates."""
    from navig.commands.template import list_templates_cmd

    ctx.obj["plain"] = plain
    list_templates_cmd(ctx.obj)


@flow_template_app.command("show")
def flow_template_show(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name"),
):
    """Show template details."""
    from navig.commands.template import show_template_cmd

    show_template_cmd(name, ctx.obj)


@flow_template_app.command("add")
def flow_template_add(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to enable"),
):
    """Enable/add a template."""
    from navig.commands.template import enable_template_cmd

    enable_template_cmd(name, ctx.obj)


@flow_template_app.command("remove")
def flow_template_remove(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to disable"),
):
    """Disable/remove a template."""
    from navig.commands.template import disable_template_cmd

    disable_template_cmd(name, ctx.obj)


@flow_template_app.command("run")
def flow_template_run(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Template name to deploy"),
    command: str | None = typer.Argument(None, help="Template command to run"),
    args: list[str] | None = typer.Argument(None, help="Arguments for the template command"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Preview without changes"),
):
    """Deploy/run a template."""
    from navig.commands.template import deploy_template_cmd

    deploy_template_cmd(
        name,
        command_name=command,
        command_args=args or [],
        dry_run=dry_run,
        ctx_obj=ctx.obj,
    )


# ============================================================================
# Interactive Menu Wrapper Functions
# ============================================================================
# These functions provide a consistent interface for the interactive menu system.
# Each wrapper calls the underlying Typer command with appropriate defaults.


def list_flows_cmd(ctx: dict[str, Any]) -> None:
    """Wrapper for flow list command (interactive menu)."""
    flow_list()


def show_flow_cmd(name: str, ctx: dict[str, Any]) -> None:
    """Wrapper for flow show command (interactive menu)."""
    flow_show(name)


def run_flow_cmd(name: str, ctx: dict[str, Any]) -> None:
    """Wrapper for flow run command (interactive menu)."""
    flow_run(name, dry_run=False, yes=False, verbose=True, var=None)


def test_flow_cmd(name: str, ctx: dict[str, Any]) -> None:
    """Wrapper for flow test command (interactive menu)."""
    flow_test(name)


def add_flow_cmd(name: str, ctx: dict[str, Any]) -> None:
    """Wrapper for flow add command (interactive menu)."""
    flow_add(name, global_scope=False)


def edit_flow_cmd(name: str, ctx: dict[str, Any]) -> None:
    """Wrapper for flow edit command (interactive menu)."""
    flow_edit(name)


def remove_flow_cmd(name: str, ctx: dict[str, Any]) -> None:
    """Wrapper for flow remove command (interactive menu)."""
    force = ctx.get("confirmed", True)
    flow_remove(name, force=force)
