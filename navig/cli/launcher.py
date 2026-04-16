"""navig/cli/launcher.py
=======================

Single responsibility: resolve and invoke one subcommand from a domain-scoped
interactive selector.

Called **only** when a domain command group is invoked with **no** subcommand.
Never involved during direct sub-command routing (``navig host list``, etc.).
Never renders UI in non-TTY environments.

Environment gate (phases 1–2)
------------------------------
Set ``NAVIG_LAUNCHER=legacy`` to bypass the fuzzy launcher and fall through to
the caller's own legacy behaviour.  In phase 3 this gate will be removed.
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


def get_domain_commands(domain: str, app: typer.Typer) -> list[CommandEntry]:  # type: ignore[name-defined]
    """Introspect *app* and return a sorted list of :class:`CommandEntry` objects.

    Includes both leaf commands (``@app.command(...)``) and registered
    sub-groups (``app.add_typer(...)`` / nested ``typer.Typer``).
    Hidden commands are excluded.

    Args:
        domain: CLI domain name, e.g. ``"host"``.
        app:    The ``typer.Typer`` instance for that domain.
    """
    entries: list[CommandEntry] = []

    # Leaf commands registered via @app.command()
    for cmd_info in getattr(app, "registered_commands", []):
        cb = getattr(cmd_info, "callback", None)
        if cb is None or getattr(cmd_info, "hidden", False):
            continue

        # Prefer the explicit name; fall back to deriving from the function name.
        name: str = cmd_info.name or cb.__name__.replace("_", "-")

        # Prefer the stored help string; fall back to the first docstring line.
        help_text = ""
        stored_help = getattr(cmd_info, "help", None)
        if stored_help:
            help_text = str(stored_help).strip().splitlines()[0]
        elif getattr(cb, "__doc__", None):
            help_text = cb.__doc__.strip().splitlines()[0]

        entries.append(CommandEntry(name=name, description=help_text, domain=domain))

    # Registered sub-groups (e.g. ``navig host monitor``)
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


def smart_launch(domain: str, app: typer.Typer) -> None:  # type: ignore[name-defined]
    """Open a fuzzy launcher scoped to *domain*'s subcommands.

    Behaviour:
    - **``NAVIG_LAUNCHER=legacy``** → caller handles fallback; this function returns immediately.
    - **Non-TTY** (pipe / CI) → print hint to stderr, ``SystemExit(0)``.
    - **No commands found** → print error to stderr, ``SystemExit(1)``.
    - **User cancels** (Ctrl+C / ESC / q) → clean ``SystemExit(0)``.
    - **User selects** → re-invoke ``navig <domain> <subcommand>`` via subprocess.

    Args:
        domain: CLI domain name, e.g. ``"host"``.
        app:    The ``typer.Typer`` instance for that domain.
    """
    if os.environ.get("NAVIG_LAUNCHER") == "legacy":
        return

    if not sys.stdin.isatty() or not sys.stdout.isatty():
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
        result = fzf_or_fallback(commands, prompt=f"navig {domain}> ")
        if result:
            # Re-invoke navig with the chosen subcommand via a child process.
            # subprocess.run is used (not exec) for cross-platform reliability.
            proc = subprocess.run(
                [sys.argv[0], domain, result.name],
                check=False,
            )
            raise SystemExit(proc.returncode)

        # User cancelled (fzf ESC/q or empty prompt)
        raise SystemExit(0)

    except KeyboardInterrupt:
        print()
        raise SystemExit(0) from None
