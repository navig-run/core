"""Interactive recovery flows for dead-end CLI exits.

Provides guard helpers that replace silent ``ch.error(); return`` patterns with
interactive host/server pickers (TTY) or a clear printed hint (non-TTY).
"""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

import typer

from navig import console_helper as ch

if TYPE_CHECKING:
    from navig.config import ConfigManager


# ── Levenshtein "Did you mean?" ──────────────────────────────────────────────


def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    # Rolling two-row DP — no extra dependencies
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        curr = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[lb]


def did_you_mean(unknown: str, candidates: list[str], threshold: int = 2) -> list[str]:
    """Return *candidates* within *threshold* edit-distance of *unknown*.

    Results are sorted by distance then alphabetically.
    """
    scored = [
        (d, c)
        for c in candidates
        if (d := _levenshtein(unknown.lower(), c.lower())) <= threshold
    ]
    scored.sort()
    return [c for _, c in scored]


# ── TTY detection ────────────────────────────────────────────────────────────


def _is_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


# ── Empty list recovery (no resources configured at all) ────────────────────


def empty_list_recovery(resource: str, add_cmd: str) -> None:
    """Handle the case where *no* resources of *resource* type exist.

    Non-TTY: prints the add command and exits 0.
    TTY: offers an interactive choice to run the add wizard or exit.
    Never returns normally — always raises ``typer.Exit``.
    """
    if not _is_tty():
        ch.warning(f"No {resource}s configured.", f"Run: navig {add_cmd}")
        raise typer.Exit(0)

    # TTY: offer to add one now
    try:
        from navig.cli.selector import CommandEntry, fzf_or_fallback
    except ImportError:
        ch.warning(f"No {resource}s configured.", f"Run: navig {add_cmd}")
        raise typer.Exit(0)

    options = [
        CommandEntry(name=f"➕  Add {resource} now", description=f"navig {add_cmd}", domain=""),
        CommandEntry(name="✕  Exit", description="Do nothing", domain=""),
    ]
    choice = fzf_or_fallback(options, prompt=f"No {resource}s configured. What would you like to do?")

    if choice is None or choice.name.startswith("✕"):
        raise typer.Exit(0)

    # Launch the add wizard as a subprocess so it owns the TTY cleanly
    navig_exe = sys.argv[0]
    cmd_args = add_cmd.split()
    try:
        subprocess.run([navig_exe] + cmd_args, check=False)
    except FileNotFoundError:
        ch.error("Could not find navig executable.", f"Run manually: navig {add_cmd}")
    raise typer.Exit(0)


# ── require_active_host ──────────────────────────────────────────────────────


def require_active_host(options: dict, cfg: "ConfigManager") -> str:  # type: ignore[return]
    """Return the active host name, or prompt the user to pick/add one.

    Callers should treat the return value as always valid — this function either
    returns a non-empty string or raises ``typer.Exit``.

    Replace patterns like::

        host_name = options.get("host") or config_manager.get_active_host()
        if not host_name:
            ch.error("No active host.", ...)
            return

    with a single line::

        host_name = require_active_host(options, config_manager)
    """
    host_name: str | None = options.get("host") or cfg.get_active_host()
    if host_name:
        return host_name

    # No active host — gather the list
    hosts: list[str] = cfg.list_hosts() if hasattr(cfg, "list_hosts") else []

    if not hosts:
        empty_list_recovery("host", "host add")
        raise typer.Exit(0)  # unreachable but keeps type checker happy

    if not _is_tty():
        ch.warning(
            "No active host configured.",
            "Use 'navig host use <name>' to set one.",
        )
        raise typer.Exit(0)

    # TTY: let the user pick from configured hosts
    try:
        from navig.cli.selector import CommandEntry, fzf_or_fallback
    except ImportError:
        ch.warning(
            "No active host configured.",
            "Use 'navig host use <name>' to set one.",
        )
        raise typer.Exit(0)

    entries = [CommandEntry(name=h, description="", domain="") for h in hosts]
    ch.info("No active host. Pick one:")
    choice = fzf_or_fallback(entries, prompt="Select host")

    if choice is None:
        raise typer.Exit(0)

    chosen = choice.name
    if hasattr(cfg, "set_active_host"):
        cfg.set_active_host(chosen)
        ch.success(f"Active host set to '{chosen}'")
    return chosen


# ── require_active_server ────────────────────────────────────────────────────


def require_active_server(options: dict, cfg: "ConfigManager") -> str:  # type: ignore[return]
    """Return the active server name (legacy API), or prompt the user.

    Used by commands that call ``config_manager.get_active_server()``
    (tunnel, database, ai).

    Replace patterns like::

        server_name = options.get("app") or config_manager.get_active_server()
        if not server_name:
            ch.error("No active server.")
            return

    with::

        server_name = require_active_server(options, config_manager)
    """
    server_name: str | None = options.get("app") or options.get("host") or cfg.get_active_server()
    if server_name:
        return server_name

    # Gather server/host list — fall back gracefully if the API differs
    servers: list[str] = []
    if hasattr(cfg, "list_servers"):
        servers = cfg.list_servers()
    elif hasattr(cfg, "list_hosts"):
        servers = cfg.list_hosts()

    if not servers:
        empty_list_recovery("server", "host add")
        raise typer.Exit(0)

    if not _is_tty():
        ch.warning(
            "No active server configured.",
            "Use 'navig host use <name>' to set one.",
        )
        raise typer.Exit(0)

    try:
        from navig.cli.selector import CommandEntry, fzf_or_fallback
    except ImportError:
        ch.warning(
            "No active server configured.",
            "Use 'navig host use <name>' to set one.",
        )
        raise typer.Exit(0)

    entries = [CommandEntry(name=s, description="", domain="") for s in servers]
    ch.info("No active server. Pick one:")
    choice = fzf_or_fallback(entries, prompt="Select server")

    if choice is None:
        raise typer.Exit(0)

    chosen = choice.name
    if hasattr(cfg, "set_active_server"):
        cfg.set_active_server(chosen)
        ch.success(f"Active server set to '{chosen}'")
    elif hasattr(cfg, "set_active_host"):
        cfg.set_active_host(chosen)
        ch.success(f"Active host set to '{chosen}'")
    return chosen
