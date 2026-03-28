"""navig/cli/launcher.py

Single-responsibility: resolve and invoke one command from a domain-scoped list.

Called only when a domain command group is invoked with **no** subcommand.
Never involved during direct subcommand routing (``navig host list``, etc.).
Never renders UI in non-TTY environments.

Environment gate (phases 1–2)
------------------------------
Set ``NAVIG_LAUNCHER=legacy`` to skip the fuzzy launcher and fall through to the
caller's own legacy behaviour.  In phase 3 this gate — and the legacy paths in
each domain — will be removed entirely.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import typer

from navig.cli.selector import CommandEntry, fzf_or_fallback

# ---------------------------------------------------------------------------
# Command introspection
# ---------------------------------------------------------------------------


def get_domain_commands(domain: str, app: "typer.Typer") -> list[CommandEntry]:
    """Introspect *app* and return a sorted flat list of :class:`CommandEntry` objects.

    Includes both leaf commands (``@app.command(...)``) and registered
    sub-groups (``app.add_typer(...)`` / nested ``typer.Typer``).

    Args:
        domain: The CLI domain name, e.g. ``"host"``.
        app:    The ``typer.Typer`` instance for that domain.
    """
    entries: list[CommandEntry] = []

    # ------------------------------------------------------------------
    # Leaf commands registered via @app.command()
    # ------------------------------------------------------------------
    for cmd_info in getattr(app, "registered_commands", []):
        cb = getattr(cmd_info, "callback", None)
        if cb is None:
            continue

        # Prefer the explicit name given to @app.command("name"), otherwise
        # derive from the function name (Typer convention: underscores → dashes)
        name: str = cmd_info.name or cb.__name__.replace("_", "-")

        # Skip hidden commands — they should not appear in the interactive launcher.
        if getattr(cmd_info, "hidden", False):
            continue

        # Prefer the stored help string; fall back to the first line of the docstring.
        help_text: str = ""
        if getattr(cmd_info, "help", None):
            help_text = str(cmd_info.help).strip().splitlines()[0]
        elif getattr(cb, "__doc__", None):
            help_text = cb.__doc__.strip().splitlines()[0]

        entries.append(CommandEntry(name=name, description=help_text, domain=domain))

    # ------------------------------------------------------------------
    # Registered sub-groups (e.g. ``navig host monitor``, ``navig host security``)
    # ------------------------------------------------------------------
    for group_info in getattr(app, "registered_groups", []):
        name = getattr(group_info, "name", None)
        if not name:
            continue

        desc = ""
        typer_instance = getattr(group_info, "typer_instance", None)
        if typer_instance is not None:
            info = getattr(typer_instance, "info", None)
            if info and getattr(info, "help", None):
                desc = str(info.help).strip().splitlines()[0]

        entries.append(CommandEntry(name=name, description=desc, domain=domain))

    return sorted(entries, key=lambda e: e.name)


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def smart_launch(domain: str, app: "typer.Typer") -> None:
    """Open a fuzzy launcher scoped to *domain*'s subcommands.

    Behaviour:
    - **Non-TTY** (pipe / CI) → print hint to stderr, ``SystemExit(0)``.
    - **No commands found** → print error to stderr, ``SystemExit(1)``.
    - **User cancels** (Ctrl+C / ESC / q) → clean ``SystemExit(0)``.
    - **User selects** → re-invoke ``navig <domain> <subcommand>`` via subprocess.

    The ``NAVIG_LAUNCHER=legacy`` environment variable bypasses this function
    entirely; the caller is responsible for falling back to legacy behaviour
    in that case (phases 1–2 only; gate removed in phase 3).

    Args:
        domain: CLI domain name, e.g. ``"host"``.
        app:    The ``typer.Typer`` instance for that domain.
    """
    if not sys.stdin.isatty():
        print(
            f"[navig] Non-TTY detected. Run: navig {domain} --help",
            file=sys.stderr,
        )
        raise SystemExit(0)

    commands = get_domain_commands(domain, app)
    if not commands:
        print(
            f"[navig] No commands registered for domain: {domain!r}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    try:
        prompt = f"navig {domain}> "
        result = fzf_or_fallback(commands, prompt=prompt)

        if result:
            # Re-invoke navig with the chosen subcommand.
            # Using subprocess.run for cross-platform reliability (Windows + Unix).
            proc = subprocess.run(
                [sys.argv[0], domain, result.name],
                check=False,
            )
            raise SystemExit(proc.returncode)

        # User cancelled cleanly (fzf ESC/q, keybinding q, or empty prompt)
        raise SystemExit(0)

    except KeyboardInterrupt:
        print()
        raise SystemExit(0) from None
