"""A CLI option with a help string that the command never reads.

The shape
---------
::

    @app.command("list")
    def queue_list(
        limit: int = typer.Option(20, "--limit", "-n", help="Max tasks to show"),
    ):
        tasks = fetch()
        for task in tasks:          # <- every task, whatever --limit said
            ...

Typer parses the flag, validates its type, renders it in ``--help`` with its promise -- and the
body never looks at it. The user passes ``-n 10``, gets 500 rows, and has no way to tell the
flag is decorative. Nothing fails: not at import, not at parse, not at run.

Ruff's ARG001 cannot be the gate: enabled on ``commands/`` it reports **165** findings, because
it counts every callback, hook and context parameter. Scoped to Typer command functions and
excluding honest stubs it is **28**, of which **12** are in real commands and 12 of 12 checked
were genuinely dead. Gate the ratio, not the count.

Honest stubs are excluded on purpose: a body that says "not yet implemented" and exits is
telling the user the truth, and its unused parameters are expected. The stub test is on the
BODY TEXT and its length, not on any annotation the author has to remember to add.

Why a baseline
--------------
Three remain recorded below with what each would take (six more were paid in the
change that wired them; the guard forced their removal). They are debts, not exemptions -- a
``--headless`` that does not go headless is a bug -- but each needs its command's intent read,
and two of them are not even fixable in core (``blackbox capture --limit`` needs a new
parameter on ``create_bundle`` in the navig-blackbox plugin, a published package). The one
with a single possible meaning (``queue list --limit``) was fixed in the same change.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

COMMANDS = Path(__file__).resolve().parents[2] / "navig" / "commands"

_STUB = re.compile(r"not yet implemented|not implemented|planned for|coming soon|placeholder", re.I)
_MIN_REAL_BODY_LINES = 8

#: (file, function, parameter) -> what the user loses, and what wiring it takes.
_KNOWN_DEAD: dict[tuple[str, str, str], str] = {
    ("crash.py", "export_crash_report", "limit"):
        "documented in its own help text as 'currently only 1 supported', so the user is told. "
        "Recorded, not a defect, until multi-crash export exists.",
    ("host.py", "host_maintenance_show", "info"):
        "REDUNDANT, not dead. --info selects the default view, so passing it changes nothing, "
        "and Typer exposes no --no-info for it (verified in --help). Nothing to fix; removing "
        "the flag would be a CLI break. Kept here so the guard does not report it as new.",
    ("local.py", "local_show_cmd", "info"):
        "REDUNDANT, not dead. Same as host_maintenance_show: --info is the default branch and "
        "no --no-info form exists. Nothing to fix without a CLI break.",
}


def _is_command(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "command"
        for d in fn.decorator_list
    )


def _scan() -> tuple[list[tuple[str, str, str]], int]:
    """([(file, function, unused parameter), ...], commands examined)."""
    dead: list[tuple[str, str, str]] = []
    examined = 0
    for f in sorted(COMMANDS.rglob("*.py")):
        try:
            src = f.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src)
        except (SyntaxError, UnicodeDecodeError):
            continue
        lines = src.splitlines()
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or not _is_command(fn):
                continue
            examined += 1
            end = fn.end_lineno or fn.lineno
            body = "\n".join(lines[fn.lineno - 1:end])
            if _STUB.search(body) or (end - fn.lineno) < _MIN_REAL_BODY_LINES:
                continue                      # an honest stub; unused params are expected
            # `locals()` / `**kwargs` forwarding consumes every parameter
            if any(isinstance(n, ast.Name) and n.id in ("locals", "kwargs") for n in ast.walk(fn)):
                continue
            params = [a.arg for a in fn.args.args + fn.args.kwonlyargs
                      if a.arg not in ("self", "ctx") and not a.arg.startswith("_")]
            used = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
            for p in params:
                if p not in used:
                    dead.append((f.name, fn.name, p))
    return dead, examined


def test_the_scan_actually_reads_the_commands() -> None:
    """Anti-vacuity, as a presence."""
    _, examined = _scan()
    assert examined >= 300, f"only {examined} Typer commands found under {COMMANDS} — mis-rooted"


def test_no_new_cli_option_that_does_nothing() -> None:
    dead, _ = _scan()
    new = [d for d in dead if d not in _KNOWN_DEAD]
    detail = "\n".join(f"    {f}  {fn}(... {p} ...)  --{p.replace('_', '-')}" for f, fn, p in new)
    assert not new, (
        "these options are parsed, shown in --help, and never read by the command, so passing "
        "them does nothing:\n" + detail + "\n\n"
        "Read the flag where the behaviour it promises is decided, or remove it. If the command "
        "is an honest stub, say 'not yet implemented' in its body."
    )


def test_the_baseline_does_not_rot() -> None:
    """A fixed entry must leave the list, or the list stops describing reality."""
    dead, _ = _scan()
    fixed = sorted(k for k in _KNOWN_DEAD if k not in dead)
    assert not fixed, f"{fixed} now read their option — remove them from _KNOWN_DEAD"


def test_every_baselined_entry_says_what_the_user_loses() -> None:
    for key, why in _KNOWN_DEAD.items():
        assert len(why.strip()) > 60, f"{key} has no usable description"
