"""navig paths — inspect NAVIG system paths and MCP server registration."""
from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from navig.console_helper import get_console

paths_app = typer.Typer(help="Inspect NAVIG system paths and MCP server registration", no_args_is_help=False)
console = get_console()


def _path_rows() -> list[tuple[str, Path]]:
    """The (label, path) rows `navig paths` shows — from the canonical resolvers.

    These MUST come from ``navig.platform.paths``, not hand-built ``~/.navig/<x>``:
      * ``logs`` is NOT under ``~/.navig`` — ``log_dir()`` is OS-idiomatic
        (``%LOCALAPPDATA%\\navig\\logs`` on Windows, ``~/Library/Logs/navig`` on macOS,
        ``~/.local/state/navig/logs`` on Linux). The old hand-built ``~/.navig/logs`` sent
        anyone hunting the log to an empty directory — the same wrong-path class as the
        #192 incident, where every diagnostic read a debug.log the logger never writes.
      * The resolvers honour ``NAVIG_CONFIG_DIR`` / ``NAVIG_DATA_DIR`` / ``NAVIG_LOG_DIR`` /
        ``NAVIG_STORE_DIR``; the hand-built paths ignored every override, so `navig paths`
        lied under an isolated config dir.
      * ``debug log`` is surfaced explicitly — it is the file agents/operators actually
        reach for, and a docs reference can finally point at `navig paths` to find it.
      * ``logs (config)`` is a SECOND, distinct log directory. The tree is genuinely split:
        ~half the code writes to ``log_dir()`` (daemon.log, debug.log, crash reports) and
        ~half to ``config_dir()/logs`` (agent.log, tray.log, router traces, remediation).
        ``navig doctor``/``navig debug`` already read both; this row is shown so a log
        the operator is hunting isn't hidden in the one `navig paths` used to omit.
        (The two should eventually be consolidated — a design call, not done here.)
    """
    from navig.platform import paths as p

    cfg = p.config_dir()
    rows = [
        ("config",     p.global_config_path()),
        ("data",       p.data_dir()),
        ("logs",       p.log_dir()),
        ("debug log",  p.debug_log_path()),
    ]
    config_logs = cfg / "logs"
    if config_logs != p.log_dir():  # nearly always distinct; skip the redundant row if not
        rows.append(("logs (config)", config_logs))
    rows += [
        ("plugins",    p.plugins_dir()),
        ("store",      p.store_dir()),
        ("wiki",       p.wiki_dir()),
        # "spaces" (PLURAL) — installed spaces live in config_dir()/spaces. The row used to
        # hand-build cfg/"space" (singular), a directory nothing creates, so `navig paths`
        # sent anyone hunting their spaces to an empty/non-existent path (the #297 packs class).
        ("spaces",     p.spaces_dir()),
        # packs MUST come from packages_dir() (honours NAVIG_PACKAGES_DIR + the legacy
        # config_dir()/packages fallback) — the hand-built cfg/"packs" lied under both, the
        # same wrong-path class this docstring calls out for logs.
        ("packs",      p.packages_dir()),
        # The user-content dirs each have exactly one canonical home (see #276/#281/#285/#294);
        # surface them so `navig paths` can tell an operator where an evolved script/skill/
        # workflow actually lands, and `navig ahk`/`navig mount`/`navig evolve *` all agree.
        ("scripts",    p.scripts_dir()),
        ("skills",     p.skills_dir()),
        ("workflows",  p.workflows_dir()),
    ]
    return rows


@paths_app.callback(invoke_without_command=True)
def paths_default(ctx: typer.Context):
    """Show key NAVIG directory paths."""
    if ctx.invoked_subcommand:
        return

    table = Table(title="NAVIG Paths")
    table.add_column("Key", style="cyan")
    table.add_column("Path")
    table.add_column("Exists", style="green")

    for key, path in _path_rows():
        table.add_row(key, str(path), "✓" if path.exists() else "–")

    console.print(table)
