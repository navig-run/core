"""The click that Typer actually runs on.

Typer < 0.27 depended on the separate ``click`` distribution. Typer 0.27 vendors it as
``typer._click`` and no longer installs ``click`` at all — so a bare ``import click``
fails on a fresh ``pip install navig`` (measured 2026-09-14: a clean venv resolves
Typer 0.27.2, ``import click`` → ``ModuleNotFoundError``). The maintainer's box never
saw it because ``requirements.lock`` pins Typer 0.24 + click 8.3.

Even where a separate click happens to be installed it is the WRONG click: its context
stack is not the one Typer's commands execute in, so ``click.get_current_context()``
answers ``None`` from inside a running command. ``claim_cli_operation`` swallowed
exactly that and fell back to a standalone ledger record — every enriched command
(``config set``, ``host use``, ``env set``) wrote TWO ledger lines on a fresh install.

Resolve the module from Typer's own ``Context`` class instead: its first base is the
click ``Context``, whichever package that lives in. ``typer.Exit`` and ``typer.Abort``
are public on every Typer version and are the right spelling for the exceptions.

⚠ ``click.Group`` cannot be resolved this way, because it does not exist to resolve in
Typer 0.27: the vendored click has no ``Group`` class at all, and ``typer.core.TyperGroup``
derives from ``Command`` and implements dispatch itself. ``isinstance(cmd, click.Group)``
is therefore False for EVERY command there — including the real groups — and, where a
separate click is also installed, False for everything at once: a tree walk keyed on it
sees an empty CLI and reports the whole documented surface as nonexistent (measured
2026-10-27: 1,453 such findings across ``tests/quality``). Use :func:`is_group`, which
asks the structural question both generations answer the same way.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any


def _click_package() -> str:
    import typer

    base = typer.Context.__mro__[1]  # click's Context, from whichever package Typer uses
    return base.__module__.rsplit(".", 1)[0]  # "click" or "typer._click"


def click_module() -> ModuleType:
    """Return the click package Typer is built on (``click`` or ``typer._click``).

    Prefer :func:`click_submodule` for anything you need from it: the vendored copy's
    ``__init__`` re-exports less than the real package (``typer._click`` has no
    ``get_current_context`` attribute, for one), while the submodules are intact.
    """
    return importlib.import_module(_click_package())


def click_submodule(name: str) -> ModuleType:
    """``click.<name>`` from the click Typer runs on — ``exceptions``, ``globals``, ``core``…"""
    return importlib.import_module(f"{_click_package()}.{name}")


def get_current_context(silent: bool = True) -> Any:
    """``click.get_current_context`` for the click Typer's commands run in."""
    return click_submodule("globals").get_current_context(silent=silent)


def is_option(param: Any) -> bool:
    """True when *param* is an option (a flag), not a positional argument.

    Same problem one level down: Typer 0.27's click has no ``Option``/``Argument``
    classes either — ``TyperOption``/``TyperArgument`` derive from ``Parameter`` — so
    ``isinstance(p, click.Option)`` matches nothing there. An option is the one that
    carries ``is_flag``; an argument does not.
    """
    return hasattr(param, "is_flag")


def abort_class() -> type:
    """The class a cancelled prompt raises — ``click.Abort`` or ``typer.Abort``.

    Typer 0.27's vendored click carries no ``Abort`` at all (measured: ``ClickException``,
    ``UsageError``, ``BadParameter`` … but not ``Abort``); Typer defines
    ``typer.exceptions.Abort`` instead. Cancel detection keyed on the click class
    therefore raised ``AttributeError`` from ``navig.main._ledger_exit_code`` on every
    ``SystemExit`` that carried a context. Ask for it rather than assuming the location.
    """
    import typer

    return getattr(click_submodule("exceptions"), "Abort", None) or typer.Abort


def is_group(command: Any) -> bool:
    """True when *command* dispatches to subcommands — on either Click generation.

    ``isinstance(command, click.Group)`` is not portable: Typer 0.27's vendored click has
    no ``Group`` class, its ``TyperGroup`` derives from ``Command``, and a separately
    installed click makes the check False for its own different hierarchy. Both
    generations agree on the structural fact — a group carries a ``.commands`` mapping and
    a leaf command does not — so that is what this asks.
    """
    return getattr(command, "commands", None) is not None
