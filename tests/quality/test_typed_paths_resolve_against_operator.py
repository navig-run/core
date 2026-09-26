"""A user-typed path must resolve where the operator TYPED it — never `Path.cwd()`.

``main.py`` chdir's into the ACTIVE SPACE before any command body runs, so inside a
command ``Path.cwd()`` is the space, not where the operator is standing. The shape

    root = Path(x) if x else Path.cwd()          # or Path(x).resolve() if x else ...

therefore resolves a typed relative ``--path``/``--root``/argument against the space,
and its default too — silently. It scaffolded 102 items into the wrong tree (#1379),
wrote the editor's ``.vscode/mcp.json`` into the space instead of the project open in
the editor, and indexed the space while claiming "current directory". Two sweeps closed
17 sites across ``space``·``wire``·``index``·``mcp``·``formation``·``block``·``plans``.

The replacement is ``navig.platform.paths``:

    root = resolve_user_path(x) if x else invocation_cwd()

with ``Path.cwd()`` kept as the DEFAULT only where the help text promises the space
(``block --workdir`` says "current space root"; ``plans`` serves space views) — and
never for the typed half. This guard fails by file:line on a new instance, with an
EMPTY baseline; the behavioural cases live in
``tests/regression/test_typed_paths_follow_the_operator.py``.

Whole-tree scanner → wired in ``sourceGuardArgs`` (``scripts/ci-local.mjs``) so it runs
on every change, not only when a regression file happens to be selected.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
COMMANDS = REPO / "core" / "navig" / "commands"

# `Path(x) if x else Path.cwd()` and `Path(x).resolve() if x else Path.cwd()`, with the
# same name on both sides of the `if` — the exact shape every fixed site had.
_SHAPE = re.compile(r"Path\((\w+)\)(?:\.resolve\(\))?\s+if\s+\1\s+else\s+Path\.cwd\(\)")

# Structural anchor: a miscomputed root makes every count below meaningless while still
# looking plausible (see CLAUDE.md on vacuity floors phrased as an absence).
assert (REPO / "core" / "navig").is_dir(), REPO
assert COMMANDS.is_dir(), COMMANDS


def _command_files() -> list[Path]:
    return sorted(p for p in COMMANDS.glob("*.py") if p.name != "__init__.py")


def test_the_scan_actually_covers_the_command_modules() -> None:
    """Scan floor — a walk that finds nothing must not read as a clean tree."""
    files = _command_files()
    assert len(files) > 100, f"only {len(files)} command modules found under {COMMANDS}"
    assert any(p.name == "space.py" for p in files)


def test_no_typed_path_resolves_against_the_process_cwd() -> None:
    hits = [
        f"{p.relative_to(REPO)}:{i}: {line.strip()}"
        for p in _command_files()
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if _SHAPE.search(line)
    ]
    assert not hits, (
        "a user-typed path is resolved against the process cwd — the ACTIVE SPACE once "
        "main.py has chdir'd — not where the operator typed it. Use\n"
        "    resolve_user_path(x) if x else invocation_cwd()\n"
        "from navig.platform.paths (keep Path.cwd() as the DEFAULT only where the help "
        "promises the space, and never for the typed half):\n  " + "\n  ".join(hits)
    )


def test_the_shape_regex_matches_what_it_claims() -> None:
    """Teeth for the pattern itself, both forms and a same-name requirement."""
    assert _SHAPE.search("root = Path(root) if root else Path.cwd()")
    assert _SHAPE.search("cwd = Path(path).resolve() if path else Path.cwd()")
    # different names on the two sides is a different (rarer) shape; not this guard's
    assert not _SHAPE.search("root = Path(a) if b else Path.cwd()")
    # the fixed form must not match
    assert not _SHAPE.search("root = resolve_user_path(root) if root else invocation_cwd()")
