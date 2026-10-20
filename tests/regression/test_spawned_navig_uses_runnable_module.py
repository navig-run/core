"""Every place that re-launches navig in a subprocess must use a module that RUNS.

`python -m navig.cli` does not: `navig.cli` is a package with no `__main__`, so the
child dies at once with "No module named navig.cli.__main__". Five sites spawned it —
the deck/Telegram/OS "Heal" (engine_ops.run_heal), the POSIX daemon start/stop/restart
routes, the formation route, and the MCP read-only command tool — so each of them
failed every time while the unit tests, which mock the subprocess, stayed green.
The runnable entry is `python -m navig` (navig/__main__.py).
"""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

NAVIG = Path(__file__).resolve().parents[2] / "navig"


def test_the_runnable_entry_exists_and_the_broken_one_does_not():
    assert importlib.util.find_spec("navig.__main__") is not None
    assert importlib.util.find_spec("navig.cli.__main__") is None, (
        "navig.cli grew a __main__ — this guard's premise changed; revisit it"
    )


def test_python_dash_m_navig_actually_runs():
    out = subprocess.run(
        [sys.executable, "-m", "navig", "--help"],
        capture_output=True, timeout=120, cwd=NAVIG.parent,
    )
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")[-400:]


def _spawns_navig_cli(tree: ast.AST) -> list[int]:
    """Lines where a list/call has the adjacent string literals "-m", "navig.cli"."""
    hits: list[int] = []
    for node in ast.walk(tree):
        elts = None
        if isinstance(node, (ast.List, ast.Tuple)):
            elts = node.elts
        elif isinstance(node, ast.Call):
            elts = node.args
        if not elts:
            continue
        for a, b in zip(elts, elts[1:]):
            if (
                isinstance(a, ast.Constant) and a.value == "-m"
                and isinstance(b, ast.Constant) and b.value == "navig.cli"
            ):
                hits.append(b.lineno)
    return hits


def test_no_subprocess_spawns_navig_cli():
    offenders: list[str] = []
    scanned = 0
    for py in NAVIG.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        scanned += 1
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        offenders += [f"{py.relative_to(NAVIG.parent)}:{ln}" for ln in _spawns_navig_cli(tree)]
    assert scanned > 500, f"scan floor: only {scanned} files read — the walk is broken"
    assert not offenders, (
        "these spawn `python -m navig.cli`, which cannot run (no __main__); "
        f"use `-m navig`: {offenders}"
    )
