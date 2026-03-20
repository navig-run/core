
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer

from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")

script_app = typer.Typer(
    name="script",
    help="Manage and run automation scripts",
    no_args_is_help=True,
)

def _get_scripts_dir() -> Path:
    # Use the same directory as ScriptEvolver
    return Path(__file__).parent.parent / "scripts"

@script_app.command("list")
def list_scripts():
    """List available scripts."""
    scripts_dir = _get_scripts_dir()
    if not scripts_dir.exists():
        ch.info("No scripts directory found.")
        return

    scripts = list(scripts_dir.glob("*.py"))
    if not scripts:
        ch.info("No scripts found.")
        return

    from rich.table import Table
    table = Table(title="Available Scripts")
    table.add_column("Name", style="cyan")
    table.add_column("Path", style="dim")

    for s in scripts:
        table.add_row(s.stem, str(s))

    ch.console.print(table)

@script_app.command("run")
def run_script(
    name: str = typer.Argument(..., help="Script name (without extension)"),
    args: Optional[list[str]] = typer.Argument(None, help="Arguments to pass to script"),
):
    """Run a Python script."""
    scripts_dir = _get_scripts_dir()
    script_path = scripts_dir / f"{name}.py"

    if not script_path.exists():
        # Try local path?
        local_path = Path(name)
        if local_path.exists() and local_path.suffix == '.py':
            script_path = local_path
        else:
            ch.error(f"Script not found: {name}")
            return

    ch.info(f"Running script: {script_path.name}")

    cmd = [sys.executable, str(script_path)]
    if args:
        cmd.extend(args)

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        ch.error(f"Script failed with exit code {e.returncode}")
    except Exception as e:
        ch.error(f"Failed to run script: {e}")

@script_app.command("edit")
def edit_script(
    name: str = typer.Argument(..., help="Script name"),
):
    """Open script in editor."""
    scripts_dir = _get_scripts_dir()
    script_path = scripts_dir / f"{name}.py"

    if not script_path.exists():
        ch.error(f"Script not found: {name}")
        return

    editor = os.environ.get('EDITOR', 'notepad' if sys.platform == 'win32' else 'nano')
    subprocess.run([editor, str(script_path)])

@script_app.command("new")
def new_script(
    name: str = typer.Argument(..., help="Script name"),
    template: str = typer.Option("basic", "--template", "-t", help="Template to use"),
):
    """Create a new manual script."""
    scripts_dir = _get_scripts_dir()
    scripts_dir.mkdir(parents=True, exist_ok=True)

    script_path = scripts_dir / f"{name}.py"
    if script_path.exists():
        ch.error(f"Script already exists: {name}")
        return

    content = ""
    if template == "basic":
        content = """#!/usr/bin/env python3
import sys
import os

def main():
    print("Hello from Navig Script!")

if __name__ == "__main__":
    main()
"""
    elif template == "automation":
        content = """#!/usr/bin/env python3
from navig.adapters.automation.ahk import AHKAdapter

def main():
    ahk = AHKAdapter()
    if not ahk.is_available():
        print("AHK not found")
        return
        
    print("Automating...")
    # Add your automation code here
    
if __name__ == "__main__":
    main()
"""

    with open(script_path, 'w') as f:
        f.write(content)

    ch.success(f"Created script: {script_path}")
    ch.info(f"Edit with: navig script edit {name}")

