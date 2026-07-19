"""There is exactly ONE debug log, and one function that names it.

`navig.platform.paths.debug_log_path()` is documented as *"Canonical path to the debug log
file"* — and eight call sites ignored it, each inventing its own spelling:

    paths.debug_log_path()          log_dir()/debug.log     <- the WRITER. canonical.
    commands/service.py             ~/.navig/debug.log      <- `navig service logs`
    commands/debug_cmd.py  (x3)     config_dir()/debug.log  <- `navig debug`, `tail`, `clear`
    commands/agent.py               config_dir()/logs/…     <- `navig agent learn`
    mcp/tools/agent.py              config_dir()/logs/…     <- its MCP twin
    core/shared_config.py           config_dir()/debug.log  <- EXPORTED to other surfaces
    debug_logger.py (fallback)      base_dir/debug.log      <- a second writer

On Windows `log_dir()` is ``%LOCALAPPDATA%/navig/logs`` — it is **never** ``~/.navig``. So
every one of those readers pointed at a file the logger does not write. Measured on a live
machine: the real ``debug.log`` was **1.7 MB**, and the file the diagnostics read was **0
bytes** — `navig service logs` even ``touch()``ed it into existence, so the tail streamed an
empty file forever and `navig debug clear` truncated it and reported success.

This test fails on any new module that builds a debug-log path by hand instead of calling
`debug_log_path()`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import navig

PKG = Path(navig.__file__).resolve().parent

# Where the literal "debug.log" may legitimately appear.
ALLOWED: dict[str, str] = {
    "platform/paths.py": "debug_log_path() — the definition itself",
    "debug_logger.py": (
        "the writer's last-resort fallback chain, used only when paths.* AND the config "
        "manager both raise; the logger must never be the thing that fails"
    ),
    "gateway/deck/routes/logs.py": (
        "the deck log browser maps every log file by name from log_dir()/config_dir() — it "
        "is the reference implementation of the split (debug.log→log_dir, navig.log→config_dir)"
    ),
}


def _string_constants(tree: ast.AST) -> list[int]:
    """Line numbers of `"debug.log"` string literals — comments/docstrings excluded."""
    return sorted({
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value == "debug.log"
    })


def _offenders() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for py in sorted(PKG.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except (OSError, UnicodeDecodeError, SyntaxError):  # pragma: no cover
            continue
        hits = _string_constants(tree)
        if hits:
            found[py.relative_to(PKG).as_posix()] = hits
    return found


def test_no_module_reinvents_the_debug_log_path() -> None:
    found = _offenders()
    unexpected = {m: h for m, h in found.items() if m not in ALLOWED}
    assert not unexpected, (
        'These modules build a debug-log path from the literal "debug.log" instead of calling\n'
        "navig.platform.paths.debug_log_path(). Every one that did so pointed at a file the\n"
        "logger does not write — on Windows log_dir() is %LOCALAPPDATA%/navig/logs, never\n"
        "~/.navig — so the diagnostic silently showed an empty file.\n\n"
        + "\n".join(f"  {m}: line(s) {h}" for m, h in sorted(unexpected.items()))
    )


def test_allowlist_has_no_stale_entries() -> None:
    stale = sorted(set(ALLOWED) - set(_offenders()))
    assert not stale, (
        "These modules no longer contain the literal — delete their ALLOWED entries:\n"
        + "\n".join(f"  {m}" for m in stale)
    )


def test_every_reader_resolves_to_the_writers_file(tmp_path, monkeypatch) -> None:
    """The property that actually matters: reader and writer agree, on any platform.

    Pinning NAVIG_LOG_DIR proves the readers follow `log_dir()` rather than a hardcoded
    home — the exact divergence that made `navig service logs` stream an empty file.
    """
    monkeypatch.setenv("NAVIG_LOG_DIR", str(tmp_path / "logs"))

    from navig.platform.paths import debug_log_path

    canonical = debug_log_path()
    assert canonical == tmp_path / "logs" / "debug.log"

    # The blackbox diagnostics bundle must collect the file the logger actually writes.
    from navig.blackbox.bundle import _default_log_files

    assert canonical in _default_log_files(), (
        "the diagnostics bundle does not collect the real debug.log"
    )
