"""A command that takes `opts` and changes something must consult ``--dry-run``.

``--dry-run`` is a GLOBAL flag: ``main()`` in ``cli/__init__.py`` writes it into
``ctx.obj``, and every command receives that same dict as ``opts``. It is therefore only
as real as the commands that remember to read it, and three did not:

* ``navig --dry-run history clear --yes`` deleted the whole operation history and
  printed "✓ Cleared 2 operations from history" (#755);
* ``navig --dry-run context clear`` deleted ``.navig/config.yaml`` and reported success;
* ``navig --dry-run context set --host web1`` created ``.navig/`` and wrote to it.

Those are the commands someone runs precisely *because* they are unsure. A flag
documented as "show what would be done without executing" that quietly executes is worse
than no flag.

The check is deliberately narrow, so every finding is real:

* only functions that actually take an ``opts`` parameter — that is what carries the
  flag; a function without it cannot be expected to know;
* only functions containing a call that changes state outside the process (a subprocess,
  a remote command, a file unlink/write/mkdir, a config write);
* a function that passes ``opts`` on to another function is exempt — the callee is where
  the check belongs, and that is the normal thin-wrapper shape;
* mentioning ``dry_run`` at all counts as consulting it. This guard asks whether the
  question is asked, not whether the answer is used correctly — that is what the
  behavioural tests in ``tests/commands/test_history_dry_run.py`` are for.

`ALLOWED` is for the case where an `opts` dict is genuinely unrelated to the CLI.
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "navig"
_SKIP = {"__pycache__", "builtin", "scaffold-templates"}

_MUTATORS = {
    "execute_command", "execute_local", "run_command", "upload_file", "download_file",
    "unlink", "rmtree", "remove", "write_text", "write_bytes", "mkdir", "rename",
    "set_global", "set_config", "set_local_config", "clear_history", "soft_delete",
    "purge_deleted",
}
_SUBPROCESS = {"run", "check_call", "check_output", "Popen", "call"}

#: module:function → why its `opts` is not the CLI options dict.
#: Empty on purpose. `tools/proc.py:run_process` was the one candidate — its `opts` is a
#: ProcessOptions dataclass, not the CLI dict — but narrowing the mutator list to calls
#: that reach outside the process (a subprocess kill is the engine doing its job, not a
#: command changing the world) removed it, so no exemption is needed. The stale-entry
#: test below is what surfaced that; an allowlist nobody prunes becomes a rubber stamp.
ALLOWED: dict[str, str] = {}


def _sources() -> list[Path]:
    return [p for p in sorted(CORE.rglob("*.py")) if not (_SKIP & set(p.parts))]


def _takes_opts(fn: ast.AST) -> bool:
    return "opts" in [a.arg for a in fn.args.args + fn.args.kwonlyargs]


def _mentions_dry_run(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and "dry_run" in node.id:
            return True
        if isinstance(node, ast.arg) and "dry_run" in node.arg:
            return True
        if isinstance(node, ast.Attribute) and "dry_run" in node.attr:
            return True
        if isinstance(node, ast.Constant) and node.value == "dry_run":
            return True
    return False


def _delegates_opts(fn: ast.AST) -> bool:
    """Hands `opts` to another function, which is where the check then belongs."""
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        if any(kw.arg == "opts" for kw in node.keywords):
            return True
        if any(isinstance(a, ast.Name) and a.id == "opts" for a in node.args):
            return True
    return False


def _mutations(fn: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        attr = node.func.attr
        recv = node.func.value
        recv_name = recv.id if isinstance(recv, ast.Name) else getattr(recv, "attr", "")
        if attr in _SUBPROCESS and recv_name == "subprocess":
            found.add(f"subprocess.{attr}")
        elif attr in _MUTATORS:
            found.add(attr)
    return found


def _scan() -> list[str]:
    findings: list[str] = []
    for path in _sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = path.relative_to(CORE.parent).as_posix()
        short = path.relative_to(CORE).as_posix()
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            if not _takes_opts(fn) or _mentions_dry_run(fn) or _delegates_opts(fn):
                continue
            muts = _mutations(fn)
            if not muts:
                continue
            if f"{short}:{fn.name}" in ALLOWED:
                continue
            findings.append(
                f"{rel}:{fn.lineno}: {fn.name}() takes `opts` and mutates state "
                f"({', '.join(sorted(muts))}) without ever consulting dry_run"
            )
    return findings


def test_every_mutating_command_consults_dry_run() -> None:
    findings = _scan()
    assert not findings, (
        "`--dry-run` is a global flag delivered in `opts`; these functions change state "
        "without reading it, so `navig --dry-run <cmd>` executes for real. Use "
        "`navig.cli.options.is_dry_run(opts)` and preview instead:\n"
        + "\n".join(f"  {f}" for f in findings)
    )


def test_allowlist_has_no_stale_entries() -> None:
    """An entry that no longer matches must be deleted, so the list cannot rot."""
    live = set()
    for path in _sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        short = path.relative_to(CORE).as_posix()
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            if _takes_opts(fn) and not _mentions_dry_run(fn) and not _delegates_opts(fn):
                if _mutations(fn):
                    live.add(f"{short}:{fn.name}")
    stale = sorted(set(ALLOWED) - live)
    assert not stale, "These allowlist entries no longer match anything — delete them:\n" + "\n".join(
        f"  {s}" for s in stale
    )


def test_guard_detects_the_real_regression() -> None:
    """Teeth: the exact shape that shipped in commands/context.py."""
    src = (
        "def clear_context(opts=None):\n"
        "    opts = opts or {}\n"
        "    config_file = Path.cwd() / '.navig' / 'config.yaml'\n"
        "    config_file.unlink()\n"
    )
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef))
    assert _takes_opts(fn)
    assert not _mentions_dry_run(fn)
    assert not _delegates_opts(fn)
    assert _mutations(fn) == {"unlink"}


def test_guard_accepts_a_command_that_checks() -> None:
    src = (
        "def clear_context(opts=None):\n"
        "    if is_dry_run(opts):\n"
        "        return\n"
        "    Path('x').unlink()\n"
    )
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef))
    assert _mentions_dry_run(fn)


def test_guard_exempts_a_thin_wrapper() -> None:
    """Passing opts on is the normal shape; the callee owns the check."""
    src = "def cmd(opts=None):\n    do_the_thing(opts=opts)\n    Path('x').unlink()\n"
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef))
    assert _delegates_opts(fn)


def test_guard_ignores_read_only_commands() -> None:
    src = "def show(opts=None):\n    print(opts.get('json'))\n"
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef))
    assert _mutations(fn) == set()
