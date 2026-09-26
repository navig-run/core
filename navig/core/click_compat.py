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
