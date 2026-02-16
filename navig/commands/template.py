"""Template Management Commands"""
import os
import re
import subprocess
import sys
from navig import console_helper as ch
from typing import Dict, Any, List, Optional, Tuple
from navig.template_manager import TemplateManager
from navig.config import get_config_manager


def list_templates_cmd(options: Dict[str, Any]):
    """List all available templates."""
    templates_payload = None
    try:
        cfg = get_config_manager().global_config
        ttl_cfg = cfg.get("cache_ttl", {})
        ttl_seconds = int(ttl_cfg.get("templates_seconds", ttl_cfg.get("templates", 3600)))
        from navig.cache_store import read_json_cache

        cache = read_json_cache(
            "templates.json",
            ttl_seconds=ttl_seconds,
            no_cache=bool(options.get("no_cache")),
        )
        if cache.hit and not cache.expired and isinstance(cache.data, dict):
            templates_payload = cache.data
    except Exception:
        templates_payload = None

    if templates_payload and isinstance(templates_payload.get("templates"), list):
        templates = templates_payload.get("templates")
    else:
        template_manager = TemplateManager()
        template_manager.discover_templates()

        templates = template_manager.list_templates()

        # Cache a lightweight representation.
        try:
            from navig.cache_store import write_json_cache

            write_json_cache(
                "templates.json",
                {
                    "templates": [
                        {
                            "name": t.metadata.get("name"),
                            "version": t.metadata.get("version"),
                            "description": t.metadata.get("description"),
                            "enabled": bool(t.is_enabled()),
                        }
                        for t in templates
                    ]
                },
            )
        except Exception:
            pass

    if not templates:
        ch.warning("No templates found in templates/ directory")
        ch.dim("Create template directories in ./templates/ with template.yaml files")
        return

    if options.get("json"):
        import json

        # templates is either Template objects or dicts (from cache)
        if templates and isinstance(templates[0], dict):
            items = templates
        else:
            items = [
                {
                    "name": t.metadata.get("name"),
                    "version": t.metadata.get("version"),
                    "description": t.metadata.get("description"),
                    "enabled": bool(t.is_enabled()),
                }
                for t in templates
            ]

        ch.raw_print(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "command": "template.list",
                    "success": True,
                    "templates": items,
                    "count": len(items),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    if options.get('plain'):
        # Plain text output - one template per line for scripting
        for template in templates:
            if isinstance(template, dict):
                ch.raw_print(str(template.get('name', '')))
            else:
                ch.raw_print(template.metadata['name'])
        return

    # Create table
    table = ch.create_table(
        title="📦 Available Templates",
        columns=[
            {"name": "Name", "style": "cyan"},
            {"name": "Version", "style": "yellow"},
            {"name": "Status", "style": "green"},
            {"name": "Description", "style": "white"}
        ],
        show_header=True
    )

    for template in templates:
        if isinstance(template, dict):
            enabled = bool(template.get("enabled"))
            status = ch.status_text("Enabled", "success") if enabled else ch.status_text("Disabled", "dim")
            desc = str(template.get("description") or "")
            table.add_row(
                str(template.get("name") or ""),
                str(template.get("version") or ""),
                status,
                desc[:50] + "..." if len(desc) > 50 else desc,
            )
        else:
            status = ch.status_text("Enabled", "success") if template.is_enabled() else ch.status_text("Disabled", "dim")
            table.add_row(
                template.metadata['name'],
                template.metadata['version'],
                status,
                template.metadata['description'][:50] + "..." if len(template.metadata['description']) > 50 else template.metadata['description']
            )

    ch.print_table(table)


def enable_template_cmd(name: str, options: Dict[str, Any]):
    """Enable an template."""
    template_manager = TemplateManager()
    template_manager.discover_templates()
    
    if options.get('dry_run'):
        ch.dim(f"Would enable template: {name}")
        return
    
    template_manager.enable_template(name)


def disable_template_cmd(name: str, options: Dict[str, Any]):
    """Disable an template."""
    template_manager = TemplateManager()
    template_manager.discover_templates()
    
    if options.get('dry_run'):
        ch.dim(f"Would disable template: {name}")
        return
    
    template_manager.disable_template(name)


def toggle_template_cmd(name: str, options: Dict[str, Any]):
    """Toggle template enabled/disabled state."""
    template_manager = TemplateManager()
    template_manager.discover_templates()
    
    if options.get('dry_run'):
        template = template_manager.get_template(name)
        if template:
            action = "disable" if template.is_enabled() else "enable"
            ch.dim(f"Would {action} template: {name}")
        return
    
    template_manager.toggle_template(name)


def show_template_cmd(name: str, options: Dict[str, Any]):
    """Show detailed information about an template."""
    template_manager = TemplateManager()
    template_manager.discover_templates()
    
    template = template_manager.get_template(name)
    if not template:
        ch.error(f"Template '{name}' not found")
        return
    
    # Header
    status = "Enabled ✓" if template.is_enabled() else "Disabled"
    ch.header(f"{template.metadata['name']} v{template.metadata['version']}")
    ch.info(f"Status: {status}")
    ch.dim(template.metadata['description'])
    ch.newline()
    
    # Metadata
    ch.info(f"Author: {template.metadata['author']}")
    if template.metadata.get('dependencies'):
        ch.info(f"Dependencies: {', '.join(template.metadata['dependencies'])}")
    ch.newline()
    
    # Paths
    paths = template.get_paths()
    if paths:
        ch.step("📁 Paths:")
        for key, value in paths.items():
            ch.dim(f"  {key}: {value}")
        ch.newline()
    
    # Services
    services = template.get_services()
    if services:
        ch.step("🔧 Services:")
        for key, value in services.items():
            ch.dim(f"  {key}: {value}")
        ch.newline()
    
    # Commands
    commands = template.get_commands()
    if commands:
        ch.step("⚡ Commands:")
        for cmd in commands:
            ch.dim(f"  {cmd['name']}: {cmd['description']}")
            ch.dim(f"    → {cmd['command']}", prefix="    ")
        ch.newline()
    
    # Environment Variables
    env_vars = template.get_env_vars()
    if env_vars:
        ch.step("🌍 Environment Variables:")
        for key, value in env_vars.items():
            ch.dim(f"  {key}={value}")

def _render_template_commands(
    template_name: str,
    commands: List[Dict[str, str]],
    options: Dict[str, Any],
) -> None:
    if not commands:
        ch.warning(f"No commands defined for template '{template_name}'")
        return

    if options.get("json"):
        import json

        ch.raw_print(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "command": "template.commands",
                    "success": True,
                    "template": template_name,
                    "commands": commands,
                    "count": len(commands),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    if options.get("plain"):
        for cmd in commands:
            ch.raw_print(cmd.get("name", ""))
        return

    table = ch.create_table(
        title=f"⚙️ Template Commands: {template_name}",
        columns=[
            {"name": "Name", "style": "cyan"},
            {"name": "Description", "style": "white"},
            {"name": "Command", "style": "yellow"},
        ],
        show_header=True,
    )

    for cmd in commands:
        name = cmd.get("name", "")
        desc = cmd.get("description", "")
        command = cmd.get("command", "")
        preview = command if len(command) <= 80 else f"{command[:77]}..."
        table.add_row(name, desc, preview)

    ch.print_table(table)
    ch.dim("Run: navig flow template run <template> <command> [args...]")
    ch.dim("Legacy: navig addon run <template> <command> [args...]")


_PLACEHOLDER_PATTERN = re.compile(r"\b(USER|email|password|EMAIL|PASSWORD)\b")


def _apply_command_args(command: str, args: List[str]) -> Tuple[str, List[str]]:
    if not args:
        return command, []

    placeholders = [match.group(1) for match in _PLACEHOLDER_PATTERN.finditer(command)]
    if not placeholders:
        return f"{command} {' '.join(args)}", []

    remaining = list(args)

    def replace_token(match: re.Match) -> str:
        if remaining:
            return remaining.pop(0)
        return match.group(0)

    updated = _PLACEHOLDER_PATTERN.sub(replace_token, command)

    if remaining:
        updated = f"{updated} {' '.join(remaining)}"

    missing = placeholders[len(args):] if len(args) < len(placeholders) else []
    return updated, missing


def deploy_template_cmd(
    name: str,
    command_name: Optional[str] = None,
    command_args: Optional[List[str]] = None,
    dry_run: bool = False,
    ctx_obj: Optional[Dict[str, Any]] = None,
) -> None:
    """Execute a template command via the remote runner."""
    options = dict(ctx_obj or {})
    options["dry_run"] = dry_run or options.get("dry_run", False)

    template_manager = TemplateManager()
    template_manager.discover_templates()

    template = template_manager.get_template(name)
    if not template:
        ch.error(f"Template '{name}' not found")
        return

    commands = template.get_commands()
    if not command_name:
        _render_template_commands(name, commands, options)
        return

    command_def = next((cmd for cmd in commands if cmd.get("name") == command_name), None)
    if not command_def:
        ch.error(f"Command '{command_name}' not found for template '{name}'")
        if commands:
            available = ", ".join(cmd.get("name", "") for cmd in commands)
            ch.dim(f"Available commands: {available}")
        return

    raw_command = command_def.get("command")
    if not raw_command:
        ch.error(f"Template '{name}' command '{command_name}' has no command string")
        return

    final_command, missing = _apply_command_args(raw_command, command_args or [])
    if missing:
        ch.warning(
            "Missing placeholder values for: " + ", ".join(missing),
            "Provide arguments after the command name.",
        )

    if not options.get("json"):
        ch.info(f"Template: {name}")
        ch.dim(f"Command: {command_name}")

    from navig.commands.remote import run_remote_command

    run_remote_command(final_command, options)


def deploy_template_cmd(name: str, dry_run: bool = False, ctx_obj: Dict[str, Any] = None):
    """Deploy/run a template (enable and surface available commands)."""
    if ctx_obj is None:
        ctx_obj = {}

    want_json = bool(ctx_obj.get("json"))
    template_manager = TemplateManager()
    template_manager.discover_templates()

    template = template_manager.get_template(name)
    if not template:
        if want_json:
            import json

            ch.raw_print(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "command": "template.run",
                        "success": False,
                        "error": f"Template '{name}' not found",
                        "dry_run": dry_run,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            ch.error(f"Template '{name}' not found")
        return

    commands = template.get_commands()

    if dry_run:
        if want_json:
            import json

            ch.raw_print(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "command": "template.run",
                        "success": True,
                        "dry_run": True,
                        "template": {
                            "name": template.metadata.get("name"),
                            "version": template.metadata.get("version"),
                            "enabled": template.is_enabled(),
                        },
                        "commands": commands,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            ch.info(f"Dry run: would enable template '{name}'")
            if commands:
                ch.dim("Commands available after enable:")
                for cmd in commands:
                    cmd_name = cmd.get("name", "command")
                    cmd_desc = cmd.get("description", "")
                    cmd_line = cmd.get("command", "")
                    suffix = f" — {cmd_desc}" if cmd_desc else ""
                    ch.dim(f"  - {cmd_name}: {cmd_line}{suffix}")
        return

    success = template_manager.enable_template(name)
    if not success:
        if want_json:
            import json

            ch.raw_print(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "command": "template.run",
                        "success": False,
                        "error": f"Failed to enable template '{name}'",
                        "dry_run": False,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        return

    if want_json:
        import json

        ch.raw_print(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "command": "template.run",
                    "success": True,
                    "dry_run": False,
                    "template": {
                        "name": template.metadata.get("name"),
                        "version": template.metadata.get("version"),
                        "enabled": template.is_enabled(),
                    },
                    "commands": commands,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    if commands:
        ch.info("Template commands:")
        for cmd in commands:
            cmd_name = cmd.get("name", "command")
            cmd_desc = cmd.get("description", "")
            cmd_line = cmd.get("command", "")
            suffix = f" — {cmd_desc}" if cmd_desc else ""
            ch.dim(f"  - {cmd_name}: {cmd_line}{suffix}")

    ch.dim("Use 'navig run' or relevant service commands to execute template actions.")


def validate_templates_cmd(options: Dict[str, Any]):
    """Validate all template configurations."""
    template_manager = TemplateManager()
    template_manager.discover_templates()
    
    ch.header("Validating Template Configurations")
    
    results = template_manager.validate_all_templates()
    
    all_valid = all(results.values())
    
    if all_valid:
        ch.success(f"✓ All {len(results)} templates passed validation")
    else:
        failed = [name for name, valid in results.items() if not valid]
        ch.error(f"✗ {len(failed)} template(s) failed validation: {', '.join(failed)}")
    
    # Show summary table
    table = ch.create_table(
        title="Validation Results",
        columns=[
            {"name": "Template", "style": "cyan"},
            {"name": "Status", "style": "green"}
        ],
        show_header=True
    )
    
    for name, valid in results.items():
        status = ch.status_text("Valid", "success") if valid else ch.status_text("Invalid", "error")
        table.add_row(name, status)
    
    ch.print_table(table)


def edit_template_cmd(name: str, options: Dict[str, Any]):
    """
    Edit host-specific template override file.
    
    Opens the override file in $EDITOR. Creates the file with a commented 
    skeleton if it doesn't exist.
    """
    config_manager = get_config_manager()
    server = options.get('server') or config_manager.get_active_server()
    
    if not server:
        ch.error("No active server. Specify with --server or use 'navig server use <name>'")
        return
    
    # Check if repo template exists
    template_manager = TemplateManager()
    template_manager.discover_templates()
    template = template_manager.get_template(name)
    
    if not template:
        ch.error(f"Template '{name}' not found in repository")
        ch.dim("Available templates:")
        for t in template_manager.list_templates():
            ch.dim(f"  - {t.name}")
        return
    
    # Get path to host-specific override file
    override_dir = config_manager.apps_dir / server / "templates"
    override_file = override_dir / f"{name}.yaml"
    
    # Create directory if needed
    override_dir.mkdir(parents=True, exist_ok=True)
    
    # Create skeleton file if it doesn't exist
    if not override_file.exists():
        skeleton = _generate_template_skeleton(template)
        override_file.write_text(skeleton, encoding='utf-8')
        ch.success(f"Created new override file: {override_file}")
    
    # Get editor
    editor = os.environ.get('EDITOR') or os.environ.get('VISUAL')
    
    if not editor:
        # Platform-specific defaults
        if sys.platform == 'win32':
            editor = 'notepad'
        elif sys.platform == 'darwin':
            editor = 'nano'
        else:
            editor = 'nano'
    
    ch.info(f"Opening {override_file} in {editor}...")
    
    try:
        if sys.platform == 'win32':
            # Windows: shell=False is sufficient with list args
            subprocess.run([editor, str(override_file)], shell=False, check=True)
        else:
            subprocess.run([editor, str(override_file)], check=True)
    except subprocess.CalledProcessError as e:
        ch.error(f"Editor exited with error: {e}")
    except FileNotFoundError:
        ch.error(f"Editor '{editor}' not found. Set $EDITOR environment variable.")


def _generate_template_skeleton(template) -> str:
    """Generate a commented YAML skeleton for template override."""
    lines = [
        f"# Host-specific overrides for {template.name} template",
        f"# Base template version: {template.metadata.get('version', 'unknown')}",
        "#",
        "# Only add values you want to override from the default template.",
        "# Uncomment and modify the sections you need.",
        "#",
        ""
    ]
    
    # Add path overrides section
    paths = template.get_paths()
    if paths:
        lines.append("# Path overrides:")
        lines.append("# paths:")
        for key, value in paths.items():
            lines.append(f"#   {key}: {value}")
        lines.append("")
    
    # Add service overrides section
    services = template.get_services()
    if services:
        lines.append("# Service overrides:")
        lines.append("# services:")
        for key, value in services.items():
            lines.append(f"#   {key}: {value}")
        lines.append("")
    
    # Add env var overrides section
    env_vars = template.get_env_vars()
    if env_vars:
        lines.append("# Environment variable overrides:")
        lines.append("# env_vars:")
        for key, value in env_vars.items():
            lines.append(f"#   {key}: \"{value}\"")
        lines.append("")
    
    # Add API overrides if present
    if 'api' in template.metadata:
        lines.append("# API configuration overrides:")
        lines.append("# api:")
        for key, value in template.metadata['api'].items():
            lines.append(f"#   {key}: {value}")
        lines.append("")
    
    return "\n".join(lines)


# ============================================================================
# ADDON COMMAND ALIASES (for backward compatibility)
# ============================================================================

def addon_list_deprecated(options: Dict[str, Any]):
    """Alias for 'navig template list'."""
    list_templates_cmd(options)


def addon_enable_deprecated(name: str, options: Dict[str, Any]):
    """Alias for 'navig template enable'."""
    enable_template_cmd(name, options)


def addon_disable_deprecated(name: str, options: Dict[str, Any]):
    """Alias for 'navig template disable'."""
    disable_template_cmd(name, options)


def addon_info_deprecated(name: str, options: Dict[str, Any]):
    """Alias for 'navig template info'."""
    show_template_cmd(name, options)


def addon_run_deprecated(name: str, options: Dict[str, Any], dry_run: bool = False):
    """Alias for 'navig flow template run'."""
    deploy_template_cmd(name, dry_run=dry_run, ctx_obj=options)
