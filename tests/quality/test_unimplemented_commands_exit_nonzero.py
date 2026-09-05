"""A command that says it is NOT IMPLEMENTED must not exit 0.

Companion to `test_command_exit_honesty.py`, which bans `ch.error(...)` + exit 0 across
the whole tree. That ban keys on **`ch.error`** — and every stub in this repo announces
itself with **`ch.warning`**, the one escape hatch that guard cannot see. So twenty stub
commands printed "navig X is not yet implemented in this build." and returned SUCCESS.

Measured before the fix: `navig origin`, `navig node list`, `navig radar list`,
`navig sync status`, `navig watch list`, `navig replay list` and
`navig portable validate` all exited **0**. A script branching on `$?`, or an agent asked
to "check sync status", reads that as done. It is the phantom-success shape the exit-honesty
sweep exists to kill, wearing a different helper.

The rule is narrow on purpose: it fires only on the project's own stub marker, so it makes
no judgement about `ch.warning` in general (a warning followed by real work is fine, and
`test_command_exit_honesty.py`'s exemption list documents why a blanket ban would not
work). Implementing a command removes the marker and the rule stops applying — the guard
cannot become a reason to keep a stub.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

#: The project's own stub marker, plus the phrasings a stub reaches for when someone
#: writes one by hand. Scoped to *announcement* wording so an ordinary warning is untouched.
_MARKERS = (
    "not yet implemented",
    "not implemented",
    "coming soon",
)
#: The exact sentence `navig/commands/` stubs use — kept for the "is this guard still
#: needed?" check, which must key on the real marker rather than the loose family above.
MARKER = "not yet implemented in this build"


def _repo_root() -> Path:
    # core/tests/quality/<this file> → repo root
    return Path(__file__).resolve().parents[3]


def _command_modules() -> list[Path]:
    """Every module that can define a CLI command — core AND plugins.

    Scoped by SURFACE, not by address. The first version of this guard scanned
    `navig/commands/` alone and missed `navig email search`, which printed
    "Searching for: <query>" and then "coming soon" and exited 0 — a plugin command,
    outside the folder, doing exactly what the guard was written to ban. An
    address-scoped guard has hidden a class three separate times in this repo
    (`private/harbor` is the worst case), so plugins and the closed plugin are in scope.
    """
    import navig.commands as pkg

    out = sorted(p for p in Path(pkg.__file__).parent.glob("*.py") if p.name != "__init__.py")
    for root in (_repo_root() / "plugins", _repo_root() / "private" / "harbor"):
        if not root.exists():
            continue
        out += sorted(
            p
            for p in root.rglob("*.py")
            if "__pycache__" not in p.parts and "tests" not in p.parts
        )
    return out


def _stub_functions(path: Path):
    """Functions whose LAST statement is the not-implemented warning.

    Last-statement only, deliberately: a warning mid-function may be followed by real
    work, and flagging that would be wrong. This shape — announce, then fall off the end —
    is exactly the one that returns 0.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or not fn.body:
            continue
        last = fn.body[-1]
        if not (isinstance(last, ast.Expr) and isinstance(last.value, ast.Call)):
            continue
        call = last.value
        if getattr(call.func, "attr", None) != "warning":
            continue
        first = call.args[0] if call.args else None
        # An f-string announcement counts too — `navig email search` used one.
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            text = first.value
        elif isinstance(first, ast.JoinedStr):
            text = "".join(
                v.value for v in first.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            )
        else:
            text = ""
        if any(m in text.lower() for m in _MARKERS):
            yield fn.name, last.lineno


def test_the_marker_still_exists_somewhere():
    """If every stub is implemented this guard is vacuous — and that is worth noticing
    rather than silently passing forever. Delete it when the count reaches zero."""
    found = any(MARKER in p.read_text(encoding="utf-8") for p in _command_modules())
    assert found, (
        f"no command still carries {MARKER!r} — every stub is implemented, so this guard "
        "has nothing left to protect and should be deleted"
    )


def test_no_unimplemented_command_returns_success() -> None:
    offenders = []
    for path in _command_modules():
        for name, lineno in _stub_functions(path):
            offenders.append(f"{path.name}:{lineno} {name}() announces 'not implemented' then returns 0")
    assert not offenders, (
        "these commands tell the user they do nothing and tell the SHELL they succeeded:\n  "
        + "\n  ".join(offenders)
        + "\n\nEnd the stub with `raise typer.Exit(1)`."
    )


def test_the_guard_actually_detects_the_shape(tmp_path: Path) -> None:
    """Anti-vacuity: prove the AST walk finds the shape it bans, so a zero above means
    'clean' rather than 'the detector is broken'."""
    sample = tmp_path / "stub_cmd.py"
    sample.write_text(
        "import typer\n"
        "app = typer.Typer()\n"
        "@app.command()\n"
        "def thing():\n"
        "    from navig import console_helper as ch\n"
        f"    ch.warning('navig thing is {MARKER}.')\n",
        encoding="utf-8",
    )
    assert [n for n, _ in _stub_functions(sample)] == ["thing"]

    fixed = tmp_path / "fixed_cmd.py"
    fixed.write_text(
        "import typer\n"
        "app = typer.Typer()\n"
        "@app.command()\n"
        "def thing():\n"
        "    from navig import console_helper as ch\n"
        f"    ch.warning('navig thing is {MARKER}.')\n"
        "    raise typer.Exit(1)\n",
        encoding="utf-8",
    )
    assert list(_stub_functions(fixed)) == []


# ── a stub that REDIRECTS must redirect somewhere real ───────────────────────
#
# Several stubs now name the live command that does the job — `navig agents run` points at
# `navig agent run`, `node` at `mesh peers`, `replay` at `history list`/`history replay`,
# `blueprint` at `block list`/`apply`. That is the right fix for a dead duplicate (this
# repo bans parallel systems), but it creates the exact hazard the rest of this session
# was spent closing: printed advice naming a command, which rots when a verb is renamed.
# So the advice is resolved through the real Click tree, flags included.

_ADVICE = re.compile(r"navig ((?:[a-z][a-z0-9-]*)(?: [a-z][a-z0-9-]*){0,2})")


def _redirect_targets():
    """(file, line, command words) for every `navig …` named inside a stub's message."""
    for path in _command_modules():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if not any(m in node.value.lower() for m in _MARKERS):
                continue
            for match in _ADVICE.finditer(node.value):
                words = match.group(1).split()
                # The stub naming ITSELF is not advice — skip the leading self-reference.
                if words and words[0] == path.stem.replace("_cmd", ""):
                    continue
                if words:
                    yield path.name, node.lineno, words


def test_stub_redirects_name_commands_that_exist() -> None:
    import click
    from typer.main import get_command

    from navig.cli import app
    from navig.cli.registration import _register_external_commands

    _register_external_commands(register_all=True, target_app=app)
    root = get_command(app)

    def resolve(words):
        cur = root
        for i, word in enumerate(words):
            if not isinstance(cur, click.Group):
                return cur, []  # leaf command — the rest are arguments
            nxt = cur.get_command(click.Context(cur), word)
            if nxt is None:
                return None, words[i:]
            cur = nxt
        return cur, []

    broken = []
    for name, lineno, words in _redirect_targets():
        if resolve(words[:1])[0] is None:
            continue  # a group absent from THIS interpreter (an uninstalled plugin)
        command, tail = resolve(words)
        if command is None:
            broken.append(f"{name}:{lineno} points at `navig {' '.join(words)}` — no verb `{tail[0]}`")
    assert not broken, (
        "a stub redirects the user to a command that does not exist:\n  " + "\n  ".join(broken)
    )
