"""Every "run `navig …`" we PRINT must name a command that actually exists.

This bug class has shipped three times now:

  * `navig prompts list` (empty state): "Add one with **navig prompts new**" —
    there is no `prompts new` (#150)
  * the Finance FX card: "Run **navig finance fx** to fetch rates" — no such
    command
  * and, found by this scan: "Use **navig server use <name>**" (it is
    `navig host use`), "Check tunnel status: **navig tunnel status**" (it is
    `tunnel show`), "**navig daemon restart**" (it is `service restart`), …

Every one of them lands on a user who is already stuck — an empty list, a
failure, a missing config — and sends them to a dead end. So: scan the strings
we actually PRINT (console.print / echo / ch.info …) for an instruction to run
`navig <group> <sub>`, resolve it against the REAL CLI surface, and fail if it
does not exist.

Docstrings and log lines are not scanned — only what a user is shown.
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import click
import pytest
import typer

CORE = Path(__file__).resolve().parents[2] / "navig"
REPO = CORE.parents[1]
PLUGINS = REPO / "plugins"

# The desktop OS and the deck print `navig …` hints too — that is where
# "Run navig finance fx to fetch rates" came from, a command that never existed.
UI_ROOTS = [
    REPO / "apps" / "os" / "apps" / "webui" / "src",
    REPO / "apps" / "deck" / "components",
    REPO / "apps" / "deck" / "lib",
]

# Functions whose string arguments reach the user's terminal.
PRINTERS = {"print", "echo", "secho", "success", "error", "warning", "info", "panel"}

# The string must be TELLING the user to run something — prose that merely
# mentions a command name is not a broken promise.
INSTRUCTION = re.compile(
    r"\b(run|use|try|with|activate|enable|fix|check)\b[^.]{0,40}navig\s|`navig|'navig|\"navig",
    re.IGNORECASE,
)
COMMAND = re.compile(r"navig\s+([a-z][a-z0-9-]*)\s+([a-z][a-z0-9-]*)")

# ── Known dead ends, kept VISIBLE instead of silently passing ────────────────
#
# EMPTY — and it should stay that way. Every hint we print now names a command
# that exists. How the 20 that were here got resolved, so nobody re-adds one
# "temporarily":
#
#   * `navig security …` / `navig monitor …` — came from cli/host_infra.py and
#     cli/legacy_flat_commands.py, two modules NOTHING registered. Every command
#     and deprecation shim in them was dead, so the old name AND the replacement
#     they pointed at were both dead. Deleted (the live homes are
#     `navig host security|monitor`).
#   * `navig deploy init`, `navig action list` — finished Typer apps that were
#     never added to the command map. Registered; they work.
#   * `navig mcp install|search|enable` — commands/mcp.py implements a whole
#     external-server manager that only the legacy interactive shell could
#     reach. Wired into `navig mcp`, which already drove the same MCPManager.
#   * `navig mode pin-set` -> `navig profile pin-set` (the PIN feature is real,
#     the hint just named the wrong group).
#   * `navig task kill`, `navig server inspect`, `navig assistant analyze|
#     feedback` — promised commands that do not exist and never did. The
#     messages now say what is true (`navig doctor` for the system check) rather
#     than inventing an escape hatch.
KNOWN_ORPHANED: set[str] = set()


def _cli_surface() -> tuple[set[str], dict[str, set[str]]]:
    """(top-level commands, {group: subcommands}) — the CLI as a user sees it.

    Commands register lazily (so `navig help` stays under 50ms), which is why
    importing the app alone shows barely a dozen of them: the real surface is
    the inline commands PLUS the external map PLUS entry-point plugins.
    """
    from navig.cli import app
    from navig.cli import registration as reg

    root = typer.main.get_command(app)
    top = set(root.commands) | set(reg._EXTERNAL_CMD_MAP) | {"wire", "apply", "ahk"}
    try:
        top |= set(reg._entry_point_commands())
    except Exception:  # pragma: no cover — plugins are optional
        pass

    subs: dict[str, set[str]] = {}
    for name, (mod_path, attr) in reg._EXTERNAL_CMD_MAP.items():
        try:
            group = typer.main.get_command(getattr(importlib.import_module(mod_path), attr))
        except Exception:  # pragma: no cover — an uninstalled optional dep
            continue
        if isinstance(group, click.Group):
            subs[name] = set(group.commands)
    return top, subs


def _plugin_names() -> set[str]:
    """Command groups provided by PLUGINS (`plugins/navig-<name>` → `navig <name>`).

    A plugin need not be installed in the test environment, so its command
    legitimately will not resolve here — a hint naming it is not a dead end for
    a user who has it installed. Skip those rather than fail on the machine's
    install state.
    """
    if not PLUGINS.is_dir():
        return set()
    return {p.name.removeprefix("navig-") for p in PLUGINS.glob("navig-*") if p.is_dir()}


def _printed_strings() -> list[tuple[Path, int, str]]:
    out: list[tuple[Path, int, str]] = []
    roots = [CORE] + (sorted(p for p in PLUGINS.glob("navig-*") if p.is_dir()) if PLUGINS.is_dir() else [])
    for root in roots:
        for path in root.rglob("*.py"):
            parts = set(path.parts)
            # `build/lib/...` is a stale copy of the package — scanning it would
            # report bugs that no longer exist in the source.
            if parts & {"tests", "test", "build", "dist", "scaffold-templates"}:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                if name not in PRINTERS:
                    continue
                for arg in node.args:
                    for s in ast.walk(arg):
                        if isinstance(s, ast.Constant) and isinstance(s.value, str):
                            out.append((path, s.lineno, s.value))
    return out


def test_printed_hints_name_commands_that_exist():
    top, subs = _cli_surface()
    assert len(top) > 50, "CLI surface introspection is broken — the guard would pass vacuously"
    plugin_groups = _plugin_names()

    phantom: list[str] = []
    for path, line, text in _printed_strings():
        if not INSTRUCTION.search(text):
            continue
        for group, sub in COMMAND.findall(text):
            hint = f"navig {group} {sub}"
            if hint in KNOWN_ORPHANED or group in plugin_groups:
                continue
            if group not in top:
                phantom.append(f"{path.name}:{line}  '{hint}' — no `navig {group}` command")
            elif group in subs and sub not in subs[group]:
                phantom.append(
                    f"{path.name}:{line}  '{hint}' — `{group}` has no `{sub}` "
                    f"(it has: {', '.join(sorted(subs[group]))})"
                )

    assert not phantom, (
        "printed a hint telling the user to run a command that does NOT exist. "
        "They are already stuck when they read it — do not send them to a dead end.\n  "
        + "\n  ".join(sorted(set(phantom)))
    )


def test_ui_hints_name_commands_that_exist():
    """The desktop OS and the deck tell users to run `navig …` too.

    That is where "Run `navig finance fx` to fetch rates" came from — a command
    that never existed, printed on the card a user reads when their totals are
    already wrong.

    The rule here is narrower than the CLI one on purpose: TSX prose is full of
    the word "navig" ("navig asks me…", "navig handled this"), so an unknown
    GROUP is ignored — only a REAL group with a subcommand it does not have is
    flagged. That is exactly the shape of every UI instance of this bug
    (`navig finance fx`, `navig prompts new`), with no prose false positives.
    """
    top, subs = _cli_surface()
    plugin_groups = _plugin_names()
    hint_re = re.compile(r"navig\s+([a-z][a-z0-9-]*)\s+([a-z][a-z0-9-]*)")

    phantom: list[str] = []
    for root in UI_ROOTS:
        if not root.is_dir():
            continue
        for path in [*root.rglob("*.tsx"), *root.rglob("*.ts")]:
            if path.name.endswith(".d.ts"):
                continue
            for lineno, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for group, sub in hint_re.findall(text):
                    if group in plugin_groups or f"navig {group} {sub}" in KNOWN_ORPHANED:
                        continue
                    if group in top and group in subs and sub not in subs[group]:
                        phantom.append(
                            f"{path.name}:{lineno}  'navig {group} {sub}' — `{group}` has no "
                            f"`{sub}` (it has: {', '.join(sorted(subs[group]))})"
                        )

    assert not phantom, (
        "a UI surface tells the user to run a command that does NOT exist:\n  "
        + "\n  ".join(sorted(set(phantom)))
    )


@pytest.mark.parametrize("hint", sorted(KNOWN_ORPHANED))
def test_known_orphans_are_still_orphans(hint: str):
    """If someone WIRES one of these up, this fails — delete it from the list.

    That keeps the allowlist from quietly becoming a graveyard of hints that
    could have been fixed years ago.
    """
    top, subs = _cli_surface()
    _, group, sub = hint.split(" ", 2)
    exists = group in top and (group not in subs or sub in subs[group])
    assert not exists, (
        f"'{hint}' resolves now — the feature was wired up. "
        "Remove it from KNOWN_ORPHANED so the guard covers it."
    )
