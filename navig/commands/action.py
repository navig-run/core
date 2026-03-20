"""navig action — Quick-action management (list, add, run, remove).

Actions are lightweight named shortcuts stored as YAML files in store/actions/.

Scan roots (in priority order):
  1. navig-core/store/actions/   (built-in actions)
  2. ~/.navig/store/actions/     (user actions)
  3. packages/*/actions/         (package-provided actions)
  4. ~/.navig/packages/*/actions/ (user package actions)
  5. ~/.navig/quick_actions.yaml  (legacy — read-only)

New actions are written to ~/.navig/store/actions/user.yaml.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import typer

from navig import console_helper as ch

action_app = typer.Typer(
    name="action",
    help="Manage quick actions (stored shortcuts)",
    no_args_is_help=True,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_user_actions_file() -> Path:
    """Path to the user's writable actions file."""
    try:
        from navig.platform.paths import store_dir
        actions_dir = store_dir() / "actions"
    except Exception:
        from navig.platform.paths import config_dir
        actions_dir = config_dir() / "store" / "actions"
    actions_dir.mkdir(parents=True, exist_ok=True)
    return actions_dir / "user.yaml"


def _load_all_actions() -> list[Dict[str, Any]]:
    """Aggregate actions from all sources; deduplicated by name (first wins)."""
    import yaml

    results: list[Dict[str, Any]] = []
    seen: set[str] = set()

    def _absorb_file(yaml_file: Path) -> None:
        if not yaml_file.exists():
            return
        try:
            data = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
        except Exception:
            return
        if isinstance(data, dict):
            for name, entry in data.items():
                if isinstance(entry, dict) and name not in seen:
                    seen.add(name)
                    results.append({"name": name, **entry})
        elif isinstance(data, list):
            for entry in data:
                if isinstance(entry, dict):
                    name = entry.get("name", "")
                    if name and name not in seen:
                        seen.add(name)
                        results.append(entry)

    def _absorb_dir(actions_dir: Path) -> None:
        if not actions_dir.is_dir():
            return
        for yaml_file in sorted(actions_dir.glob("*.yaml")):
            _absorb_file(yaml_file)

    try:
        from navig.platform.paths import (
            builtin_packages_dir,
            builtin_store_dir,
            packages_dir,
            store_dir,
        )
        _absorb_dir(builtin_store_dir() / "actions")
        _absorb_dir(store_dir() / "actions")
        for root in (builtin_packages_dir(), packages_dir()):
            if root.exists():
                for pkg in root.iterdir():
                    _absorb_dir(pkg / "actions")
    except Exception:
        pass

    # Legacy: ~/.navig/quick_actions.yaml
    try:
        from navig.config import get_config_manager
        config_dir = Path(get_config_manager().global_config_dir)
        _absorb_file(config_dir / "quick_actions.yaml")
    except Exception:
        pass

    return results


# ── Commands ──────────────────────────────────────────────────────────────────

@action_app.command("list")
def action_list(
    json_out: bool = typer.Option(False, "--json", help="Output as JSON"),
    plain: bool = typer.Option(False, "--plain", help="Plain text (one name per line)"),
):
    """List all quick actions."""
    actions = _load_all_actions()

    if not actions:
        ch.info("No quick actions defined.")
        ch.dim("Add one: navig action add <name> <command>")
        return

    if json_out:
        import json
        sys.stdout.write(json.dumps(actions, indent=2) + "\n")
        return

    if plain:
        for a in actions:
            print(a.get("name", ""))
        return

    from rich.console import Console
    from rich.table import Table
    console = Console()
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Command")
    table.add_column("Description", style="dim")

    for a in actions:
        table.add_row(
            a.get("name", ""),
            a.get("command", ""),
            a.get("description", ""),
        )
    console.print(table)


@action_app.command("show")
def action_show(
    name: str = typer.Argument(..., help="Action name"),
):
    """Show details of a specific action."""
    actions = _load_all_actions()
    action = next((a for a in actions if a.get("name") == name), None)
    if action is None:
        ch.error(f"Action '{name}' not found. Use 'navig action list' to see available actions.")
        raise typer.Exit(1)
    ch.header(f"Action: {name}")
    ch.kv("Command", action.get("command", "—"))
    ch.kv("Description", action.get("description", "—"))
    if action.get("created"):
        ch.kv("Created", action["created"])


@action_app.command("add")
def action_add(
    name: str = typer.Argument(..., help="Action name (e.g. 'deploy-prod')"),
    command: str = typer.Argument(..., help="Command to execute"),
    description: str = typer.Option("", "--desc", "-d", help="Short description"),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite if name exists"),
):
    """Add a new quick action."""
    import yaml

    # Check name collision
    existing = _load_all_actions()
    if any(a.get("name") == name for a in existing):
        if not force:
            ch.error(f"Action '{name}' already exists. Use --force to overwrite.")
            raise typer.Exit(1)

    user_file = _get_user_actions_file()
    if user_file.exists():
        try:
            data: dict = yaml.safe_load(user_file.read_text(encoding="utf-8")) or {}
        except Exception:
            data = {}
    else:
        data = {}

    data[name] = {
        "command": command,
        "description": description,
        "created": datetime.now().isoformat(timespec="seconds"),
    }

    user_file.write_text(yaml.safe_dump(data, default_flow_style=False, allow_unicode=True), encoding="utf-8")
    ch.success(f"Action '{name}' added → {user_file}")


@action_app.command("remove")
def action_remove(
    name: str = typer.Argument(..., help="Action name to remove"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Remove a quick action from the user actions file."""
    import yaml

    user_file = _get_user_actions_file()
    if not user_file.exists():
        ch.error(f"Action '{name}' not found in user actions.")
        raise typer.Exit(1)

    try:
        data: dict = yaml.safe_load(user_file.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}

    if name not in data:
        ch.error(f"Action '{name}' not found in {user_file}.")
        ch.dim("Note: built-in or package actions cannot be removed here.")
        raise typer.Exit(1)

    if not yes:
        confirmed = typer.confirm(f"Remove action '{name}'?")
        if not confirmed:
            raise typer.Abort()

    del data[name]
    user_file.write_text(yaml.safe_dump(data, default_flow_style=False, allow_unicode=True), encoding="utf-8")
    ch.success(f"Action '{name}' removed.")


@action_app.command("run")
def action_run(
    name: str = typer.Argument(..., help="Action name to execute"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print command without executing"),
):
    """Run a quick action by name."""
    actions = _load_all_actions()
    action = next((a for a in actions if a.get("name") == name), None)
    if action is None:
        ch.error(f"Action '{name}' not found.")
        ch.dim("Use 'navig action list' to see available actions.")
        raise typer.Exit(1)

    command = action.get("command", "")
    if not command:
        ch.error(f"Action '{name}' has no command defined.")
        raise typer.Exit(1)

    ch.dim(f"→ {command}")
    if dry_run:
        ch.info("[dry-run] Command not executed.")
        return

    import subprocess
    try:
        result = subprocess.run(
            command,
            shell=True,
            check=False,
        )
        if result.returncode != 0:
            ch.dim(f"Exit code: {result.returncode}")
    except Exception as e:
        ch.error(f"Failed to run action: {e}")
        raise typer.Exit(1) from e
