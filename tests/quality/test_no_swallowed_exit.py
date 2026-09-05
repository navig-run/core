"""`typer.Exit` is a RuntimeError, so `except Exception` CATCHES IT.

    try:
        if not key:
            ch.error("--key required")
            raise typer.Exit(1)     # <- deliberate exit code
        ...
    except Exception as e:
        ch.error(f"Error: {e}")     # <- catches it, prints "Error: 1", falls through

That was live in `navig memory knowledge add|search`, and the symptom is unmistakable
once you know it: **`✗ Error: 1` with exit code 0** — the exit code itself str()'d into
the message, and the process still reporting success. Verified before the fix:

    navig memory knowledge add     -> exit 0 | ✗ Error: 1
    navig memory knowledge search  -> exit 0 | ✗ Error: 1

and after:

    navig memory knowledge add     -> exit 1 | ✗ --key and --content required for add

This is the nastiest variant of the exit-honesty class because the FIX looks present.
Someone wrote `raise typer.Exit(1)` — the code reads correct, `test_command_exit_honesty`
sees a raise and is satisfied — and a broad handler three screens away quietly undoes it,
replacing a real message with a bare integer.

THE RULE: a `try` that contains a deliberate `raise typer.Exit(...)` and also has a broad
`except Exception` must shield it:

    except typer.Exit:
        raise
    except Exception as e:
        ch.error(...)
        raise typer.Exit(1) from e

Either handler alone is not enough. Without the shield the code is right and the message
is wrong; without the `raise` in the broad handler, an unexpected exception still exits 0.

A broad handler that RE-RAISES (bare `raise`, or `raise ... from e`) does not swallow, so
it is not matched by THIS check — but it is NOT clean, and saying so here was wrong. It
still catches the deliberate exit first, so it REWRITES the code and prints over the
message: a chosen `Exit(2)` leaves as a 1, under a line holding the exit code itself
("Failed to access remediation engine: 2"). Measured across core: 14 such try-blocks in 8
command modules, every one real. That lesser half is `_rewrites` /
`test_no_deliberate_exit_is_rewritten_by_a_broad_handler`, below. Both are fixed by the
same shield; they are reported separately because "exits 0" and "exits the wrong non-zero
code" are different severities.

`typer.Abort` deliberately caught to print "Cancelled" is fine and not matched by either:
a user declining a prompt is not a failure, and that handler is specific rather than broad.
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "navig"
PLUGINS = CORE.parents[1] / "plugins"

_BROAD = frozenset({"Exception", "BaseException"})
_DELIBERATE = frozenset({"Exit"})


def _handler_names(h: ast.ExceptHandler) -> list[str]:
    if h.type is None:
        return ["<bare>"]
    if isinstance(h.type, ast.Tuple):
        return [ast.unparse(e).split(".")[-1] for e in h.type.elts]
    return [ast.unparse(h.type).split(".")[-1]]


def _reraises(h: ast.ExceptHandler) -> bool:
    return any(isinstance(n, ast.Raise) for n in ast.walk(h))


def _raises_exit(node: ast.AST) -> bool:
    if not isinstance(node, ast.Raise) or node.exc is None:
        return False
    exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
    return ast.unparse(exc).split(".")[-1] in _DELIBERATE


def _unshielded(tree: ast.AST, name: str) -> list[str]:
    """Tries whose body raises Exit while a broad handler swallows it."""
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue

        swallowing = [
            h for h in node.handlers
            if (set(_handler_names(h)) & _BROAD or _handler_names(h) == ["<bare>"])
            and not _reraises(h)
        ]
        if not swallowing:
            continue

        shielded = any(
            set(_handler_names(h)) & _DELIBERATE and _reraises(h) for h in node.handlers
        )
        if shielded:
            continue

        # Only the try BODY matters — a raise inside another handler is not caught here.
        for stmt in node.body:
            for inner in ast.walk(stmt):
                if _raises_exit(inner):
                    out.append(
                        f"{name}:{inner.lineno}  raise typer.Exit(...) is swallowed by "
                        f"the `except {'/'.join(_handler_names(swallowing[0]))}` at line "
                        f"{swallowing[0].lineno}"
                    )
    return out


def _rewrites(tree: ast.AST, name: str) -> list[str]:
    """The LESSER half: a broad handler that re-raises still REWRITES the exit.

    `_unshielded` above only matches a handler that swallows outright (exit 0). But
    a broad handler that prints and re-raises `Exit(1)` has still CAUGHT the
    deliberate exit — so a carefully chosen `Exit(2)` becomes a 1, and the real
    message is buried under one holding the exit CODE:

        ✗ Action not found: op-123
        ✗ Failed to access remediation engine: 2      <- the code, str()'d

    Verified end to end: unguarded, `raise typer.Exit(2)` exits 1; with the shield,
    it exits 2. The same `except typer.Exit: raise` fixes both halves, which is why
    they live in one file — but they are reported apart, because "exits 0" and
    "exits the wrong non-zero code" are different severities and a reader must not
    have to guess which one they are looking at.
    """
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        if any(
            set(_handler_names(h)) & _DELIBERATE and _reraises(h) for h in node.handlers
        ):
            continue  # shielded
        broad = [
            h for h in node.handlers
            if (set(_handler_names(h)) & _BROAD or _handler_names(h) == ["<bare>"])
            and _reraises(h)
        ]
        if not broad:
            continue
        for stmt in node.body:
            for inner in ast.walk(stmt):
                if _raises_exit(inner):
                    out.append(
                        f"{name}:{inner.lineno}  raise typer.Exit(...) is caught and "
                        f"REWRITTEN by the `except {'/'.join(_handler_names(broad[0]))}`"
                        f" at line {broad[0].lineno}"
                    )
    return out


def _python_files() -> list[Path]:
    roots = [CORE]
    if PLUGINS.is_dir():
        roots += sorted(p for p in PLUGINS.glob("navig-*") if p.is_dir())
    files: list[Path] = []
    for root in roots:
        for f in root.rglob("*.py"):
            if {"build", "dist", "tests", "test", "scaffold-templates"} & set(f.parts):
                continue
            files.append(f)
    return files


def test_no_deliberate_exit_is_swallowed_by_a_broad_handler() -> None:
    offenders: list[str] = []
    scanned = 0
    for path in _python_files():
        try:
            # utf-8-sig: a BOM is a SyntaxError to ast.parse, and such a file runs fine
            # while going invisible to every AST guard.
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except SyntaxError:
            continue
        scanned += 1
        offenders += _unshielded(tree, path.name)

    assert scanned > 300, (
        f"only {scanned} files parsed — this guard scans core + the plugins, where there "
        "are far more. It is looking in the wrong place and checking nothing."
    )
    assert not offenders, (
        "a deliberate `raise typer.Exit(...)` is caught by a broad `except Exception`, so "
        "the exit code becomes part of an error message (`Error: 1`) and the command "
        "still exits 0:\n  "
        + "\n  ".join(offenders)
        + "\n\nShield it:\n"
        "    except typer.Exit:\n"
        "        raise\n"
        "    except Exception as e:\n"
        "        ch.error(...)\n"
        "        raise typer.Exit(1) from e"
    )


def test_no_deliberate_exit_is_rewritten_by_a_broad_handler() -> None:
    """The sibling of the check above — see `_rewrites` for why it is separate.

    Measured when added: 14 try-blocks across 8 command modules, every one real.
    The worst was `agent remediation status <bogus-id>`, which printed "Action not
    found: …" and then "Failed to access remediation engine: 1" over the top of it.
    Plus `crash export` with no reports on disk: a deliberate `typer.Exit(0)` — a
    query that succeeded with an empty answer — was caught and reported as
    "✗ Error exporting crash report: 0" at exit 1, i.e. a success rendered as a
    failure whose message is the digit zero.
    """
    offenders: list[str] = []
    scanned = 0
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except SyntaxError:
            continue
        scanned += 1
        offenders += _rewrites(tree, path.name)

    assert scanned > 300, (
        f"only {scanned} files parsed — this guard scans core + the plugins. It is "
        "looking in the wrong place and checking nothing."
    )
    assert not offenders, (
        "a deliberate `raise typer.Exit(...)` is caught by a broad handler that "
        "re-raises a DIFFERENT exit, so the chosen code is lost and the real message "
        "is buried under one containing the exit code:\n  "
        + "\n  ".join(offenders)
        + "\n\nShield it — the same fix as the check above:\n"
        "    except typer.Exit:\n"
        "        raise\n"
        "    except Exception as e:\n"
        "        ch.error(...)\n"
        "        raise typer.Exit(1) from e"
    )


# ── the detector's own boundaries ────────────────────────────────────────────


def _tree(src: str) -> ast.AST:
    return ast.parse(src)


_SWALLOWED = (
    "def cmd(key):\n"
    "    try:\n"
    "        if not key:\n"
    "            ch.error('--key required')\n"
    "            raise typer.Exit(1)\n"
    "    except Exception as e:\n"
    "        ch.error(f'Error: {e}')\n"
)


def test_the_guard_catches_the_shape_it_exists_for() -> None:
    assert len(_unshielded(_tree(_SWALLOWED), "x.py")) == 1


def test_a_shielded_try_is_clean() -> None:
    """The prescribed fix must clear the finding — otherwise the guard is unsatisfiable."""
    shielded = _SWALLOWED.replace(
        "    except Exception as e:\n",
        "    except typer.Exit:\n        raise\n    except Exception as e:\n",
    )
    assert _unshielded(_tree(shielded), "x.py") == []


def test_a_reraising_broad_handler_does_not_SWALLOW() -> None:
    """It is not the exit-0 bug — but it is not "clean" either.

    A broad handler that re-raises still CATCHES the deliberate exit first, so it
    rewrites the code and prints over the message. That is the lesser defect below,
    caught by `_rewrites`, not by this detector. Keep the two apart: this one means
    "the process exits 0", the other means "the process exits with the wrong code".
    """
    reraising = _SWALLOWED.replace(
        "        ch.error(f'Error: {e}')\n",
        "        ch.error(f'Error: {e}')\n        raise typer.Exit(1) from e\n",
    )
    assert _unshielded(_tree(reraising), "x.py") == []
    assert len(_rewrites(_tree(reraising), "x.py")) == 1, "the sibling check must own it"


def test_the_rewrite_detector_catches_its_shape_and_clears_on_the_fix() -> None:
    """Anti-vacuity for `_rewrites`, in both directions."""
    rewriting = _SWALLOWED.replace(
        "        ch.error(f'Error: {e}')\n",
        "        ch.error(f'Error: {e}')\n        raise typer.Exit(1) from e\n",
    )
    assert len(_rewrites(_tree(rewriting), "x.py")) == 1

    shielded = rewriting.replace(
        "    except Exception as e:\n",
        "    except typer.Exit:\n        raise\n    except Exception as e:\n",
    )
    assert _rewrites(_tree(shielded), "x.py") == []


def test_a_try_with_no_deliberate_exit_is_not_flagged() -> None:
    """The broad handler is only a problem when there is an exit for it to rewrite.

    Otherwise every ordinary `try/except Exception: ...; raise typer.Exit(1)` in the
    tree would be flagged, and the guard would mean nothing.
    """
    ordinary = (
        "def cmd():\n"
        "    try:\n"
        "        do_work()\n"
        "    except Exception as e:\n"
        "        ch.error(f'Error: {e}')\n"
        "        raise typer.Exit(1) from e\n"
    )
    assert _rewrites(_tree(ordinary), "x.py") == []


def test_abort_caught_to_print_cancelled_is_not_flagged() -> None:
    """A user declining a prompt is NOT a failure. `except typer.Abort: ch.info(...)` is
    specific, not broad, and deliberately handles rather than swallows — flagging it
    would demand an exit code for pressing 'n'."""
    cancel = (
        "def cmd():\n"
        "    try:\n"
        "        if not typer.confirm('Clear?'):\n"
        "            raise typer.Abort()\n"
        "    except typer.Abort:\n"
        "        ch.info('Cancelled')\n"
    )
    assert _unshielded(_tree(cancel), "x.py") == []
