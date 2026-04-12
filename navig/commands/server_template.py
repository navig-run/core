"""
Server Template CLI Commands

Commands for managing per-server template configurations.
"""

from typing import Any

from navig import console_helper as ch
from navig.config import get_config_manager
from navig.server_template_manager import ServerTemplateManager

config_manager = get_config_manager()
template_manager = ServerTemplateManager(config_manager)


def list_server_templates_cmd(options: dict[str, Any]):
    """List template configurations for a server."""
    from navig.cli.recovery import require_active_server  # noqa: PLC0415
    server = require_active_server(options, config_manager)

    enabled_only = options.get("enabled_only", False)
    templates = template_manager.list_server_templates(server, enabled_only=enabled_only)

    if not templates:
        if enabled_only:
            ch.warning(f"No enabled templates for server '{server}'")
        else:
            ch.warning(
                f"No templates initialized for server '{server}'",
                "Run 'navig server inspect' to auto-detect templates",
            )
        return

    if options.get("raw") or options.get("plain"):
        # Plain text output - one template per line for scripting
        for template in templates:
            ch.raw_print(template["name"])
        return

    # Create rich table
    table = ch.create_table(
        title=f"Templates for Server: {server}",
        columns=[
            {"name": "Template", "style": "cyan"},
            {"name": "Status", "style": "green"},
            {"name": "Version", "style": "yellow"},
            {"name": "Source", "style": "blue"},
            {"name": "Custom", "style": "magenta"},
        ],
    )

    for template in templates:
        status = "✓ Enabled" if template["enabled"] else "○ Disabled"
        source = "Auto-detected" if template.get("auto_detected") else "Manual"
        customized = "Yes" if template.get("customized") else "No"

        table.add_row(
            template["name"],
            ch.status_text(status, template["enabled"]),
            template.get("template_version", "N/A"),
            source,
            customized,
        )

    ch.print_table(table)


def show_template_config_cmd(template_name: str, options: dict[str, Any]):
    """Show merged configuration for a server template."""
    from navig.cli.recovery import require_active_server  # noqa: PLC0415
    server = require_active_server(options, config_manager)

    config = template_manager.get_template_config(server, template_name, include_template=True)

    if config is None:
        ch.error(
            f"Template '{template_name}' not initialized for server '{server}'",
            "Run 'navig server inspect' to auto-detect templates",
        )
        return

    if options.get("raw"):
        import json

        ch.raw_print(json.dumps(config, indent=2))
        return

    ch.header(f"Template Configuration: {template_name} (Server: {server})")

    # Show paths
    if config.get("paths"):
        ch.info("\n[bold cyan]Paths:[/bold cyan]")
        for key, value in config["paths"].items():
            ch.dim(f"  {key}: {value}")

    # Show services
    if config.get("services"):
        ch.info("\n[bold cyan]Services:[/bold cyan]")
        for key, value in config["services"].items():
            ch.dim(f"  {key}: {value}")

    # Show environment variables
    if config.get("env_vars"):
        ch.info("\n[bold cyan]Environment Variables:[/bold cyan]")
        for key, value in config["env_vars"].items():
            ch.dim(f"  {key}: {value}")

    # Show API config
    if config.get("api"):
        ch.info("\n[bold cyan]API Configuration:[/bold cyan]")
        for key, value in config["api"].items():
            ch.dim(f"  {key}: {value}")

    # Show commands count
    if config.get("commands"):
        ch.info(f"\n[bold cyan]Commands:[/bold cyan] {len(config['commands'])} available")


def enable_server_template_cmd(template_name: str, options: dict[str, Any]):
    """Enable an template for a server."""
    from navig.cli.recovery import require_active_server  # noqa: PLC0415
    server = require_active_server(options, config_manager)

    if options.get("dry_run"):
        ch.dim(f"Would enable template '{template_name}' for server '{server}'")
        return

    success = template_manager.enable_template(server, template_name)
    if not success and options.get("verbose"):
        ch.dim("Check if template is initialized: navig server template list")


def disable_server_template_cmd(template_name: str, options: dict[str, Any]):
    """Disable an template for a server."""
    from navig.cli.recovery import require_active_server  # noqa: PLC0415
    server = require_active_server(options, config_manager)

    if options.get("dry_run"):
        ch.dim(f"Would disable template '{template_name}' for server '{server}'")
        return

    template_manager.disable_template(server, template_name)


def set_template_value_cmd(template_name: str, key_path: str, value: str, options: dict[str, Any]):
    """Set a custom value for a server template configuration."""
    from navig.cli.recovery import require_active_server  # noqa: PLC0415
    server = require_active_server(options, config_manager)

    if options.get("dry_run"):
        ch.dim(
            f"Would set {key_path} = {value} for template '{template_name}' on server '{server}'"
        )
        return

    # Try to parse value as JSON for complex types
    import json

    try:
        parsed_value = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        # Keep as string if not valid JSON
        parsed_value = value

    success = template_manager.set_template_custom_value(
        server, template_name, key_path, parsed_value
    )

    if success and options.get("verbose"):
        ch.dim(
            f"Updated configuration saved to: ~/.navig/apps/{server}/templates/{template_name}.yaml"
        )


def sync_template_cmd(template_name: str, options: dict[str, Any]):
    """Sync template configuration from template."""
    from navig.cli.recovery import require_active_server  # noqa: PLC0415
    server = require_active_server(options, config_manager)

    preserve_custom = not options.get("force", False)

    if options.get("dry_run"):
        action = "overwrite custom settings" if not preserve_custom else "preserve custom settings"
        ch.dim(f"Would sync template '{template_name}' from template ({action})")
        return

    if not preserve_custom:
        if not ch.confirm_action(
            f"This will overwrite ALL custom settings for '{template_name}'. Continue?",
            default=False,
        ):
            ch.warning("Sync cancelled")
            return

    success = template_manager.sync_template_from_template(
        server, template_name, preserve_custom=preserve_custom
    )

    if success and options.get("verbose"):
        if preserve_custom:
            ch.dim("Custom overrides were preserved during sync")
        else:
            ch.dim("All custom settings were reset to template defaults")


def init_template_cmd(template_name: str, options: dict[str, Any]):
    """Manually initialize an template for a server."""
    from navig.cli.recovery import require_active_server  # noqa: PLC0415
    server = require_active_server(options, config_manager)

    enabled = options.get("enable", False)

    if options.get("dry_run"):
        status = "enabled" if enabled else "disabled"
        ch.dim(f"Would initialize template '{template_name}' for server '{server}' ({status})")
        return

    success = template_manager.initialize_template_manually(server, template_name, enabled=enabled)

    if success and options.get("verbose"):
        ch.dim(
            f"Template configuration initialized from template: templates/{template_name}/template.yaml"
        )


# ============================================================================
# TYPER SUB-APP — extracted from navig/cli/__init__.py
# ============================================================================

import typer  # noqa: E402

server_template_app = typer.Typer(help="Manage per-server template configurations")


@server_template_app.command("list")
def server_template_list_cmd(
    ctx: typer.Context,
    server: str | None = typer.Option(
        None, "--server", "-s", help="Server name (uses active if omitted)"
    ),
    enabled_only: bool = typer.Option(False, "--enabled", "-e", help="Show only enabled templates"),
    plain: bool = typer.Option(
        False, "--plain", help="Output plain text (one template per line) for scripting"
    ),
):
    """List template configurations for a server."""
    ctx.obj["server"] = server
    ctx.obj["enabled_only"] = enabled_only
    ctx.obj["plain"] = plain
    list_server_templates_cmd(ctx.obj)


@server_template_app.command("show")
def server_template_show_cmd(
    ctx: typer.Context,
    template_name: str = typer.Argument(..., help="Template name to show"),
    server: str | None = typer.Option(
        None, "--server", "-s", help="Server name (uses active if omitted)"
    ),
):
    """Show merged configuration for a server template."""
    ctx.obj["server"] = server
    show_template_config_cmd(template_name, ctx.obj)


@server_template_app.command("enable")
def server_template_enable_cmd(
    ctx: typer.Context,
    template_name: str = typer.Argument(..., help="Template name to enable"),
    server: str | None = typer.Option(
        None, "--server", "-s", help="Server name (uses active if omitted)"
    ),
):
    """Enable an template for a specific server."""
    ctx.obj["server"] = server
    enable_server_template_cmd(template_name, ctx.obj)


@server_template_app.command("disable")
def server_template_disable_cmd(
    ctx: typer.Context,
    template_name: str = typer.Argument(..., help="Template name to disable"),
    server: str | None = typer.Option(
        None, "--server", "-s", help="Server name (uses active if omitted)"
    ),
):
    """Disable an template for a specific server."""
    ctx.obj["server"] = server
    disable_server_template_cmd(template_name, ctx.obj)


@server_template_app.command("set")
def server_template_set_cmd(
    ctx: typer.Context,
    template_name: str = typer.Argument(..., help="Template name"),
    key_path: str = typer.Argument(..., help="Dot-separated config path (e.g., 'paths.web_root')"),
    value: str = typer.Argument(..., help="Value to set (JSON-parseable)"),
    server: str | None = typer.Option(
        None, "--server", "-s", help="Server name (uses active if omitted)"
    ),
):
    """Set a custom value for a server template configuration."""
    ctx.obj["server"] = server
    set_template_value_cmd(template_name, key_path, value, ctx.obj)


@server_template_app.command("sync")
def server_template_sync_cmd(
    ctx: typer.Context,
    template_name: str = typer.Argument(..., help="Template name to sync"),
    server: str | None = typer.Option(
        None, "--server", "-s", help="Server name (uses active if omitted)"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite all custom settings"),
):
    """Sync template configuration from template."""
    ctx.obj["server"] = server
    ctx.obj["force"] = force
    sync_template_cmd(template_name, ctx.obj)


@server_template_app.command("init")
def server_template_init_cmd(
    ctx: typer.Context,
    template_name: str = typer.Argument(..., help="Template name to initialize"),
    server: str | None = typer.Option(
        None, "--server", "-s", help="Server name (uses active if omitted)"
    ),
    enable: bool = typer.Option(
        False, "--enable", "-e", help="Enable template after initialization"
    ),
):
    """Manually initialize an template for a server."""
    ctx.obj["server"] = server
    ctx.obj["enable"] = enable
    init_template_cmd(template_name, ctx.obj)
