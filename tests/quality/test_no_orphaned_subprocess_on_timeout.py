"""A bounded subprocess wait must KILL the child on timeout — it must never orphan it.

``await asyncio.wait_for(proc.communicate(), timeout=…)`` cancels only the wait coroutine;
the child process keeps running **orphaned**. This is a real, recurring class here — it
leaked headful browsers (navig-games/navig-audio) and left ~18 core sites (SSH, tailscale,
ffmpeg, docker, nettools ping/dns/weather, calendar, social deploy) leaking a child on every
timeout. All were routed through ``navig.core.aio_subprocess.communicate_or_kill`` /
``wait_or_kill``, which kill the child before re-raising.

This guard is the lock on that door: it fails the build if a function CREATES an asyncio
subprocess (``create_subprocess_exec``/``create_subprocess_shell``) and awaits its
``communicate()``/``wait()`` inside ``asyncio.wait_for(...)`` but never calls ``.kill()`` /
``.terminate()`` on it.

Precision — a CI guard must be false-positive-free:
  * the var must be a CONFIRMED subprocess (assigned from ``create_subprocess_*`` in the SAME
    function), so the extremely common ``await asyncio.wait_for(event.wait(), …)`` on an
    asyncio.Event/Condition is never matched;
  * a function that delegates the wait to a helper (``communicate_or_kill(proc, …)``) has no
    inline ``wait_for(proc.communicate())``, so it is naturally exempt — and the helper itself
    takes ``proc`` as a PARAMETER (not created in-scope), so it is never flagged either.
"""

from __future__ import annotations

import ast
from pathlib import Path

_CORE_ROOT = Path(__file__).resolve().parents[2] / "navig"
_PLUGINS_ROOT = Path(__file__).resolve().parents[3] / "plugins"

_EXCLUDE_DIRS = {"tests", "test", ".venv", "node_modules", ".dev", "build", "dist", "__pycache__"}

_SUBPROC_FACTORIES = {"create_subprocess_exec", "create_subprocess_shell"}
_KILLERS = {"kill", "terminate"}
# Helpers that take the process as an ARGUMENT and kill it (tree and all).
_TREE_KILLERS = {"kill_process_tree", "terminate_process_tree", "terminate_process_tree_sync"}
_WAITABLES = {"communicate", "wait"}


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


def _subprocess_vars(fn: ast.AST) -> set[str]:
    """Names assigned from ``[await] create_subprocess_exec/shell(...)`` in fn's own scope."""
    out: set[str] = set()
    for node in _own_nodes(fn):
        if isinstance(node, ast.Assign):
            val = _unwrap_await(node.value)
            if isinstance(val, ast.Call) and _call_name(val) in _SUBPROC_FACTORIES:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        out.add(target.id)
    return out


def _killed_vars(fn: ast.AST) -> set[str]:
    """Names V killed in fn's own scope — as ``V.kill()``/``V.terminate()`` OR by being handed
    to a canonical tree-killer, ``kill_process_tree(V)`` / ``terminate_process_tree(V, …)``.

    The helper form has to count. A caller that fixed the *deeper* bug — a shell spawn whose
    grandchildren a bare ``V.kill()`` leaves running — no longer has a literal ``V.kill()``
    anywhere, and without this the guard would flag the CORRECT code and push the author back
    to the very pattern that orphans the real process.
    """
    out: set[str] = set()
    for node in _own_nodes(fn):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name in _KILLERS:
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                out.add(func.value.id)
        elif name in _TREE_KILLERS:
            for arg in node.args:
                if isinstance(arg, ast.Name):
                    out.add(arg.id)
    return out


def _waited_procs(fn: ast.AST, subproc_vars: set[str]) -> dict[str, int]:
    """Subprocess vars awaited via ``asyncio.wait_for(V.communicate()/V.wait())``."""
    hits: dict[str, int] = {}
    for node in _own_nodes(fn):
        if not (isinstance(node, ast.Call) and _call_name(node) == "wait_for" and node.args):
            continue
        inner = node.args[0]
        if isinstance(inner, ast.Call) and _call_name(inner) in _WAITABLES:
            func = inner.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                v = func.value.id
                if v in subproc_vars:
                    hits.setdefault(v, node.lineno)
    return hits


def _find_violations(tree: ast.AST, rel: str) -> list[str]:
    out: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        sv = _subprocess_vars(fn)
        if not sv:
            continue
        killed = _killed_vars(fn)
        for v, lineno in _waited_procs(fn, sv).items():
            if v not in killed:
                out.append(
                    f"{rel}:{lineno} — {fn.name}() wraps {v}.communicate()/wait() in "
                    f"asyncio.wait_for but never kills {v}: a timeout orphans the child. "
                    f"Use navig.core.aio_subprocess.communicate_or_kill / wait_or_kill."
                )
    return out


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


def test_no_orphaned_subprocess_on_timeout_survives_in_the_repo():
    """No function may await a subprocess's communicate()/wait() under asyncio.wait_for
    without killing the child on timeout — that orphans the process."""
    violations: list[str] = []
    for root in _scan_roots():
        violations += _scan(root)
    assert not violations, (
        "orphaned-subprocess-on-timeout pattern reintroduced — route the wait through "
        "navig.core.aio_subprocess.communicate_or_kill / wait_or_kill (they kill the child "
        "before re-raising the timeout):\n  " + "\n  ".join(violations)
    )


# ── the guard must actually be able to see the pattern ───────────────────────


def test_the_scan_covers_plugins_not_just_core():
    roots = _scan_roots()
    assert _CORE_ROOT in roots
    if _PLUGINS_ROOT.is_dir():
        assert _PLUGINS_ROOT in roots, "plugins/ present but not scanned — green over an unchecked tree"


def test_guard_flags_wait_for_communicate_without_kill():
    src = (
        "async def run():\n"
        "    proc = await asyncio.create_subprocess_exec('x')\n"
        "    out, err = await asyncio.wait_for(proc.communicate(), timeout=5)\n"
        "    return out\n"
    )
    assert len(_find_violations(ast.parse(src), "synthetic.py")) == 1


def test_guard_flags_wait_for_wait_without_kill():
    src = (
        "async def run():\n"
        "    proc = await asyncio.create_subprocess_shell('x')\n"
        "    await asyncio.wait_for(proc.wait(), timeout=5)\n"
    )
    assert len(_find_violations(ast.parse(src), "synthetic.py")) == 1


def test_guard_accepts_a_kill_on_the_timeout_path():
    src = (
        "async def run():\n"
        "    proc = await asyncio.create_subprocess_exec('x')\n"
        "    try:\n"
        "        out, err = await asyncio.wait_for(proc.communicate(), timeout=5)\n"
        "    except asyncio.TimeoutError:\n"
        "        proc.kill()\n"
        "        await proc.wait()\n"
        "        raise\n"
    )
    assert _find_violations(ast.parse(src), "synthetic.py") == []


def test_guard_ignores_wait_for_on_an_asyncio_event():
    """`await asyncio.wait_for(event.wait(), timeout=…)` is the common event-wait pattern —
    `event` is NOT a subprocess, so it must never be flagged (killing an Event is nonsense)."""
    src = (
        "async def run(event):\n"
        "    await asyncio.wait_for(event.wait(), timeout=5)\n"
    )
    assert _find_violations(ast.parse(src), "synthetic.py") == []


def test_guard_ignores_delegation_to_a_helper():
    """A function that hands its proc to a kill-on-timeout helper has no inline wait_for and
    the helper takes proc as a PARAM (not created in-scope) — neither is flagged."""
    caller = (
        "async def run():\n"
        "    proc = await asyncio.create_subprocess_exec('x')\n"
        "    out, err = await communicate_or_kill(proc, 5)\n"
    )
    helper = (
        "async def communicate_or_kill(proc, timeout):\n"
        "    try:\n"
        "        return await asyncio.wait_for(proc.communicate(), timeout=timeout)\n"
        "    except asyncio.TimeoutError:\n"
        "        proc.kill()\n"
        "        raise\n"
    )
    assert _find_violations(ast.parse(caller), "synthetic.py") == []
    assert _find_violations(ast.parse(helper), "synthetic.py") == []


# ── widening the guard must not blunt it ─────────────────────────────────────


def test_the_guard_still_fires_on_a_genuinely_unkilled_process():
    """Anti-vacuity for the _TREE_KILLERS widening: the original bug must still be caught."""
    src = """
async def run():
    proc = await asyncio.create_subprocess_exec("sleep", "30")
    out, err = await asyncio.wait_for(proc.communicate(), timeout=5)
"""
    found = _find_violations(ast.parse(src), "synthetic.py")
    assert len(found) == 1, f"the guard stopped catching the unkilled case: {found}"


def test_a_tree_helper_counts_as_killing_the_process():
    """The correct fix for a shell spawn has no literal proc.kill() — it must not be flagged."""
    src = """
async def run():
    proc = await asyncio.create_subprocess_shell("sleep 30")
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=5)
    except asyncio.TimeoutError:
        await kill_process_tree(proc)
"""
    assert _find_violations(ast.parse(src), "synthetic.py") == []


def test_killing_a_DIFFERENT_process_does_not_exempt_this_one():
    """The widening keys on the argument name, so it must not exempt an unrelated variable."""
    src = """
async def run():
    proc = await asyncio.create_subprocess_exec("sleep", "30")
    await kill_process_tree(other)
    out, err = await asyncio.wait_for(proc.communicate(), timeout=5)
"""
    assert len(_find_violations(ast.parse(src), "synthetic.py")) == 1
