"""A line-reading subprocess spawn must pass an explicit ``limit=``.

``asyncio.create_subprocess_exec(..., stdout=PIPE)`` gives the pipe a ``StreamReader`` whose
default line limit is **64 KiB**. A single output line longer than that makes ``readline()``
(or ``readuntil()``) **RAISE** ``LimitOverrunError``/``ValueError`` instead of returning the
line — which kills the whole reading loop, not just that one line. 64 KiB is small in normal
use: one ``navig … --json`` payload, one verbose log line, one big JSON-RPC response.

This is a proven, recurring class, not a hypothetical:

* **#692 / #695** — MCP stdio clients bricked permanently: the reader died, in-flight requests
  hung to the request timeout, and ``is_connected`` still reported True so nothing reconnected.
* The deck CLI console (``gateway/deck/routes/cli.py``) streams arbitrary ``navig`` CLI output
  and caps it at ``_MAX_BYTES`` (2 MiB) — an unset line limit defeated that intent, turning a
  long ``--json`` line into a cryptic "stream error" instead of the designed truncation.

The fix is one keyword: ``limit=STREAM_LIMIT`` (``navig.core.aio_subprocess``). This guard
fails the build if a file that spawns a subprocess and reads a stream line-wise omits it.

Detection is per-file (a file that spawns AND line-reads must pass ``limit=`` on its spawns) —
deliberately coarse so it cannot be defeated by moving the read into a helper method, which is
exactly how these readers are written. A file that spawns but never line-reads (``communicate()``,
``DEVNULL``, ``readexactly`` only) is not flagged: the limit is irrelevant there.
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "navig"
REPO = CORE.parents[1]

# Keyed repo-relative -> reason. A file here spawns + line-reads but legitimately does not pass
# limit=. Ratcheted by test_allowlist_entries_still_match: when a site is fixed, its entry must
# be deleted, so the allowlist can never hide a NEW offender in the same file.
_ALLOWLIST: dict[str, str] = {}

_SPAWN_ATTRS = {"create_subprocess_exec", "create_subprocess_shell"}
_LINE_READ_ATTRS = {"readline", "readuntil"}


def _spawn_calls(tree: ast.AST) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in _SPAWN_ATTRS
    ]


def _has_line_read(tree: ast.AST) -> bool:
    """Any ``.readline()`` / ``.readuntil()`` call — the reads the 64 KiB limit can break.

    ``readexactly()`` is deliberately NOT counted: CPython resumes a paused transport inside
    ``_wait_for_data`` specifically so ``readexactly(n)`` works for ``n > limit``.
    """
    return any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in _LINE_READ_ATTRS
        for n in ast.walk(tree)
    )


def _is_offender(path: Path) -> bool:
    """Spawns a subprocess AND line-reads a stream, but some spawn passes no ``limit=``."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return False
    spawns = _spawn_calls(tree)
    if not spawns or not _has_line_read(tree):
        return False
    return any(not any(kw.arg == "limit" for kw in call.keywords) for call in spawns)


def _scanned_roots() -> list[Path]:
    """core/navig AND every plugin package.

    Scoped to `core/navig` alone until 2026-08-06, while four sibling guards in this directory
    already covered plugins. Plugins spawn subprocesses constantly (navig-download's engine,
    navig-github's, the antivirus scanner), and the 64 KiB default is a property of *asyncio*,
    not of core — so the scope belongs at "code that spawns and line-reads", not at a directory.
    Widening it found no offenders, which is the point: it stays that way now.
    """
    roots = [CORE]
    plugins = REPO / "plugins"
    if plugins.is_dir():
        roots += sorted(p for p in plugins.glob("navig-*") if p.is_dir())
    return roots


def _python_files() -> list[Path]:
    files: list[Path] = []
    for root in _scanned_roots():
        for f in root.rglob("*.py"):
            parts = set(f.parts)
            if {"tests", "test", "scaffold-templates", "build", "dist"} & parts:
                continue
            files.append(f)
    return files


# The scan must actually READ the tree. A guard whose scope silently breaks reports "no
# offenders" exactly like a clean repo — the failure mode this repo has already hit twice.
_MIN_FILES_SCANNED = 1200


def test_the_scan_actually_reads_the_tree():
    n = len(_python_files())
    assert n >= _MIN_FILES_SCANNED, (
        f"scanned only {n} files (expected >= {_MIN_FILES_SCANNED}) — the scope is broken, so "
        "this guard is passing without having looked"
    )
    roots = [r.name for r in _scanned_roots()]
    assert any(r.startswith("navig-") for r in roots), f"plugins are not in scope: {roots}"


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO)).replace("\\", "/")


def test_line_reading_spawns_set_an_explicit_limit():
    offenders = [_rel(p) for p in _python_files() if _rel(p) not in _ALLOWLIST and _is_offender(p)]
    assert not offenders, (
        "these files spawn a subprocess and read its stream with readline()/readuntil() but "
        "pass no `limit=` — asyncio's default is 64 KiB, so ONE longer line RAISES and kills the "
        "reader loop (this bricked MCP in #692/#695). Pass `limit=STREAM_LIMIT` from "
        "navig.core.aio_subprocess on every spawn in these files:\n  " + "\n  ".join(offenders)
    )


def test_allowlist_entries_still_match():
    """Ratchet: an allowlisted file must STILL be an offender. When one is fixed its entry has
    to go, otherwise the file-level allowlist would mask a newly-added unlimited spawn."""
    stale = []
    for rel in _ALLOWLIST:
        path = REPO / rel
        if not path.exists():
            stale.append(f"{rel} (file gone)")
        elif not _is_offender(path):
            stale.append(f"{rel} (no longer matches — remove the allowlist entry)")
    assert not stale, "stale allowlist entries: " + ", ".join(stale)


def test_the_rule_catches_a_regression(tmp_path):
    """Guard the guard: flag spawn+readline without limit; accept it with limit; never flag a
    spawn that does not line-read, nor a line-read with no spawn."""

    def offender(src: str) -> bool:
        p = tmp_path / "m.py"
        p.write_text(src, encoding="utf-8")
        return _is_offender(p)

    bad = (
        "import asyncio\n"
        "async def f():\n"
        "    p = await asyncio.create_subprocess_exec('x', stdout=asyncio.subprocess.PIPE)\n"
        "    return await p.stdout.readline()\n"
    )
    assert offender(bad), "missed a spawn+readline with no limit"

    good = (
        "import asyncio\n"
        "async def f():\n"
        "    p = await asyncio.create_subprocess_exec('x', stdout=asyncio.subprocess.PIPE,\n"
        "                                             limit=8 * 1024 * 1024)\n"
        "    return await p.stdout.readline()\n"
    )
    assert not offender(good), "flagged a spawn that passes limit="

    no_read = (
        "import asyncio\n"
        "async def f():\n"
        "    p = await asyncio.create_subprocess_exec('x', stdout=asyncio.subprocess.PIPE)\n"
        "    return await p.communicate()\n"
    )
    assert not offender(no_read), "flagged a spawn that never line-reads"

    readexactly_only = (
        "import asyncio\n"
        "async def f():\n"
        "    p = await asyncio.create_subprocess_exec('x', stdout=asyncio.subprocess.PIPE)\n"
        "    return await p.stdout.readexactly(1024)\n"
    )
    assert not offender(readexactly_only), "readexactly is limit-safe — must not be flagged"

    assert not offender("async def f(r):\n    return await r.readline()\n"), "flagged a non-spawn"


def test_the_known_line_readers_are_covered():
    """Positive ratchet for the files this class was actually found in — if any loses its
    `limit=`, the class is back even if the negative scan were weakened."""
    known = [
        "core/navig/mcp/transport.py",            # #692
        "core/navig/agent/mcp_client.py",         # #695
        "core/navig/gateway/deck/routes/cli.py",  # deck console — arbitrary navig CLI output
        "core/navig/gateway/deck/routes/vault.py",
        "core/navig/cloud/manager.py",            # cloudflared URL scraper
        "core/navig/agent/lsp_client.py",
    ]
    regressed = [rel for rel in known if (REPO / rel).exists() and _is_offender(REPO / rel)]
    assert not regressed, "these line-readers lost their limit=: " + ", ".join(regressed)
