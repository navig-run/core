"""A shell spawn may not be stopped with a bare ``terminate()``/``kill()``.

``create_subprocess_shell`` does not run your command — it runs a SHELL that runs your command.
On Windows that shell is always ``cmd.exe`` with the real command as a separate child, and
``terminate()`` is ``TerminateProcess``: the shell dies instantly, the command is orphaned, and
the graceful ``wait()`` returns promptly so the caller reports a successful kill. That is how
``navig task kill`` returned ``True`` over a process that ran forever holding its log file open.

Escalating afterwards does not help — reaching descendants means walking a tree from the parent
pid, and by escalation time the parent has been reaped. The descendants must be snapshotted
BEFORE the parent is signalled, which is what ``aio_subprocess.terminate_process_tree`` (and its
``_sync`` twin, for ``subprocess.Popen``) do.

Precision — a CI guard must be false-positive-free, so this flags ONLY the unambiguous case:
  * the variable is a CONFIRMED shell spawn — assigned from ``create_subprocess_shell`` in the
    SAME function — so an ``exec`` spawn of a leaf binary is never matched;
  * a function that hands the process to a tree helper is exempt, however it stops it.

Scope it does NOT cover (stated so nobody reads green here as "no orphans anywhere"): a
``Popen``/``create_subprocess_exec`` of something that is *itself* a launcher — on Windows any
``.cmd``/``.bat``, e.g. ``npx`` resolving to ``npx.CMD``, which CreateProcess runs through
``cmd.exe``. That is not statically decidable from argv, so it stays a review concern; the MCP
paths that hit it are fixed and covered by ``tests/core/test_aio_subprocess.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

pytest_plugins: list[str] = []

_CORE_ROOT = Path(__file__).resolve().parents[2] / "navig"
_PLUGINS_ROOT = Path(__file__).resolve().parents[3] / "plugins"

_EXCLUDE_DIRS = {"tests", "test", ".venv", "node_modules", ".dev", "build", "dist", "__pycache__"}

_SHELL_FACTORY = "create_subprocess_shell"
_BARE_STOPPERS = {"terminate", "kill"}
_TREE_HELPERS = {
    "terminate_process_tree",
    "terminate_process_tree_sync",
    "kill_process_tree",
    "communicate_or_kill",
    "wait_or_kill",
    # Sweeps the descendants and leaves the parent to the caller — for a function that already
    # owns its own draining/waiting and just needs to stop orphaning the real command.
    "kill_descendants",
}


def _scan_roots() -> list[Path]:
    roots = [_CORE_ROOT]
    if _PLUGINS_ROOT.is_dir():
        roots.append(_PLUGINS_ROOT)
    return roots


def _name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _call_name(node: ast.AST) -> str | None:
    return _name(node.func) if isinstance(node, ast.Call) else None


def _unwrap_await(value: ast.AST) -> ast.AST:
    return value.value if isinstance(value, ast.Await) else value


def _own_nodes(fn: ast.AST):
    """Nodes in fn's body, NOT descending into nested function/lambda scopes."""

    def walk(node: ast.AST):
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            yield from walk(child)

    for stmt in getattr(fn, "body", []):
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        yield from walk(stmt)


def _target_names(target: ast.AST) -> list[str]:
    """Names bound by an assignment target, including ``self._proc`` style attributes."""
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Attribute):
        return [target.attr]
    return []


def _find_violations(tree: ast.AST, rel: str) -> list[str]:
    violations: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # 1. Which names in this function hold a confirmed SHELL spawn?
        shell_vars: set[str] = set()
        for node in _own_nodes(fn):
            if isinstance(node, ast.Assign):
                if _call_name(_unwrap_await(node.value)) == _SHELL_FACTORY:
                    for tgt in node.targets:
                        shell_vars.update(_target_names(tgt))
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                if _call_name(_unwrap_await(node.value)) == _SHELL_FACTORY:
                    shell_vars.update(_target_names(node.target))
        if not shell_vars:
            continue

        # 2. Does the function already route through a tree helper? Then it is fine however
        #    it stops the process — the helper owns the descendants.
        uses_helper = any(
            _call_name(_unwrap_await(node)) in _TREE_HELPERS
            for node in _own_nodes(fn)
            if isinstance(_unwrap_await(node), ast.Call)
        )
        if uses_helper:
            continue

        # 3. A bare terminate()/kill() on one of those shell vars is the bug.
        for node in _own_nodes(fn):
            call = _unwrap_await(node)
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                continue
            if call.func.attr not in _BARE_STOPPERS:
                continue
            owner = _name(call.func.value)
            if owner in shell_vars:
                violations.append(
                    f"{rel}:{call.lineno} {fn.name}(): {owner}.{call.func.attr}() on a "
                    f"create_subprocess_shell spawn — kills the shell, orphans the command"
                )
    return violations


def _scan(root: Path) -> list[str]:
    violations: list[str] = []
    for py in root.rglob("*.py"):
        rel = py.relative_to(root)
        if set(rel.parts) & _EXCLUDE_DIRS:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        violations += _find_violations(tree, f"{root.name}/{rel.as_posix()}")
    return violations


# ── the guard ────────────────────────────────────────────────────────────────


def test_no_bare_terminate_on_a_shell_spawn():
    """A create_subprocess_shell process must be stopped as a TREE, not with terminate()."""
    violations: list[str] = []
    for root in _scan_roots():
        violations += _scan(root)
    assert not violations, (
        "a shell spawn is being stopped with a bare terminate()/kill() — that kills cmd.exe "
        "and leaves the real command running. Use "
        "navig.core.aio_subprocess.terminate_process_tree (or terminate_process_tree_sync for "
        "subprocess.Popen), which snapshots the descendants before signalling:\n  "
        + "\n  ".join(violations)
    )


# ── the guard must actually be able to see the pattern ───────────────────────


def test_the_guard_detects_the_pattern_it_claims_to():
    """Anti-vacuity: a guard nobody has seen fire is a guard that may match nothing."""
    src = """
async def stop_it():
    proc = await asyncio.create_subprocess_shell("sleep 30")
    proc.terminate()
    await proc.wait()
"""
    found = _find_violations(ast.parse(src), "synthetic.py")
    assert len(found) == 1, f"guard failed to flag the exact known bug: {found}"
    assert "orphans the command" in found[0]


def test_the_guard_exempts_a_function_that_uses_a_tree_helper():
    """No false positive on the fixed shape — otherwise the guard is unfixable and gets deleted."""
    src = """
async def stop_it():
    proc = await asyncio.create_subprocess_shell("sleep 30")
    await terminate_process_tree(proc, grace=5.0)
"""
    assert _find_violations(ast.parse(src), "synthetic.py") == []


def test_the_guard_ignores_an_exec_spawn():
    """create_subprocess_exec of a leaf binary is not this bug — flagging it would be noise."""
    src = """
async def stop_it():
    proc = await asyncio.create_subprocess_exec("sleep", "30")
    proc.terminate()
"""
    assert _find_violations(ast.parse(src), "synthetic.py") == []


def test_the_scan_covers_plugins_not_just_core():
    roots = _scan_roots()
    assert _CORE_ROOT in roots
    if _PLUGINS_ROOT.is_dir():
        assert _PLUGINS_ROOT in roots, "plugins/ present but not scanned — green over an unchecked tree"
