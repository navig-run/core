"""navig plugin — manage and scaffold NAVIG plugins.

Plugins are self-registering Typer apps discovered from ``~/.navig/plugins/`` (and
the built-in / project dirs). This command group lists them and scaffolds new ones.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import typer

from navig import console_helper as ch

plugin_app = typer.Typer(
    name="plugin",
    help="Manage and scaffold NAVIG plugins.",
    invoke_without_command=True,
    no_args_is_help=False,
)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")


def _user_plugins_dir() -> Path:
    from navig.platform.paths import config_dir

    return config_dir() / "plugins"


@plugin_app.callback()
def _plugin_callback(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        _plugin_list()
        raise typer.Exit()


@plugin_app.command("list")
def _plugin_list(plain: bool = typer.Option(False, "--plain", help="Plain output for scripting.")):
    """List discovered plugins."""
    try:
        from navig.plugins import get_plugin_manager

        mgr = get_plugin_manager()
        mgr.discover_plugins()
        plugins = mgr.list_plugins() or {}
    except Exception as exc:  # noqa: BLE001
        ch.error("Could not load plugins.", details=str(exc))
        raise typer.Exit(1) from exc

    if not plugins:
        ch.info("No plugins installed. Scaffold one with: navig plugin new <name>")
        return

    for name, info in sorted(plugins.items()):
        ver = getattr(info, "version", "") or ""
        src = getattr(info, "source", "") or ""
        if plain:
            print(f"{name}\t{ver}\t{src}")
        else:
            ch.info(f"  {name}  {ver}  ({src})")


_PLUGIN_PY = '''\
"""NAVIG plugin: {name}."""
from __future__ import annotations

import typer

name = "{name}"
version = "0.1.0"
description = "{description}"

# Self-registered as `navig {name} ...` when discovered.
app = typer.Typer(help=description, no_args_is_help=True)


@app.command("hello")
def hello() -> None:
    """Example command — run: navig {name} hello"""
    typer.echo("Hello from the {name} plugin!")


def check_dependencies() -> tuple[bool, list[str]]:
    """Return (ok, missing_packages). Keep checks cross-platform."""
    return True, []
'''

_PLUGIN_YAML = """\
name: {name}
version: 0.1.0
description: {description}
permissions: []
"""


@plugin_app.command("new")
def _plugin_new(
    name: str = typer.Argument(..., help="Plugin name (snake/kebab case)"),
    description: str = typer.Option("", "--description", "-d", help="One-line description."),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite an existing plugin."),
):
    """Scaffold a new plugin skeleton into ~/.navig/plugins/<name>/."""
    slug = _slug(name)
    if not slug:
        ch.error("Invalid plugin name.")
        raise typer.Exit(1)

    target = _user_plugins_dir() / slug
    pyfile = target / "plugin.py"
    if pyfile.exists() and not force:
        ch.warning(f"Plugin '{slug}' already exists.", details=str(pyfile))
        raise typer.Exit(1)

    target.mkdir(parents=True, exist_ok=True)
    desc = description or f"The {slug} plugin"
    pyfile.write_text(_PLUGIN_PY.format(name=slug, description=desc), encoding="utf-8")
    (target / "plugin.yaml").write_text(_PLUGIN_YAML.format(name=slug, description=desc), encoding="utf-8")
    (target / "requirements.txt").write_text("", encoding="utf-8")

    ch.success(f"Created plugin '{slug}'.", details=str(target))
    ch.info(f"Try it: navig {slug} hello")


@plugin_app.command("remove")
def _plugin_remove(
    name: str = typer.Argument(..., help="Plugin name to remove"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation."),
):
    """Remove a user plugin (~/.navig/plugins/<name>/)."""
    slug = _slug(name)
    target = _user_plugins_dir() / slug
    if not target.exists():
        ch.warning(f"Plugin '{slug}' is not installed.")
        return
    shutil.rmtree(target)
    ch.success(f"Removed plugin '{slug}'.")
