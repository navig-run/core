"""Production code must never bind the import-time-frozen ``KEY_FACTS_DB_PATH``.

``navig/memory/paths.py`` resolves that constant ONCE, at import. Anything that binds
it captures whatever ``NAVIG_HOME`` / ``NAVIG_CONFIG_DIR`` happened to be set when the
module was first imported — but imports run at module load while the env is configured
at CLI/daemon startup. ``mcp_server`` did exactly this, so every MCP memory tool
silently read and wrote a DIFFERENT key-facts database than the rest of NAVIG (fixed in
#141; proven: with ``NAVIG_HOME=X:/isolated`` the constant still resolved to
``~/.navig/memory/key_facts.db``).

This guards the CLASS of bug, not that one instance: no module under ``navig/`` may
REFERENCE the constant. ``get_key_facts_db_path()`` is the lazy, override-respecting
resolver the module already tells new code to use. Tests may reference the constant
(several assert its shape).

Detection is AST-based on purpose — a raw text scan also matches prose, and the very
docstring in ``mcp_server`` that explains this fix names the constant.
"""

from __future__ import annotations

import ast
from pathlib import Path

NAVIG_PKG = Path(__file__).resolve().parents[2] / "navig"

CONSTANT = "KEY_FACTS_DB_PATH"

# The one legitimate site: the module that DEFINES the deprecated constant.
_ALLOWED = {NAVIG_PKG / "memory" / "paths.py"}


def _references_constant(source: str) -> bool:
    """True if the module actually binds/uses the name (not merely mentions it)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover — not our problem to police here
        return False
    for node in ast.walk(tree):
        # from navig.memory.paths import KEY_FACTS_DB_PATH [as _X]
        if isinstance(node, ast.ImportFrom) and any(a.name == CONSTANT for a in node.names):
            return True
        # paths.KEY_FACTS_DB_PATH
        if isinstance(node, ast.Attribute) and node.attr == CONSTANT:
            return True
        # a bare KEY_FACTS_DB_PATH reference
        if isinstance(node, ast.Name) and node.id == CONSTANT:
            return True
    return False


def _offenders() -> list[str]:
    hits: list[str] = []
    for py in NAVIG_PKG.rglob("*.py"):
        if py in _ALLOWED:
            continue
        try:
            src = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # pragma: no cover
            continue
        if _references_constant(src):
            hits.append(str(py.relative_to(NAVIG_PKG.parent)).replace("\\", "/"))
    return sorted(hits)


def test_scan_root_is_real() -> None:
    """A guard that scans nothing always passes — fail loudly if the root moves."""
    assert NAVIG_PKG.is_dir(), f"expected the navig package at {NAVIG_PKG}"
    assert any(NAVIG_PKG.rglob("*.py")), "scanned zero python files"


def test_detector_sees_a_real_reference() -> None:
    """The detector must actually fire (and must ignore mere prose)."""
    assert _references_constant(f"from navig.memory.paths import {CONSTANT}\n")
    assert _references_constant(f"import navig.memory.paths as p\nx = p.{CONSTANT}\n")
    assert not _references_constant(f'"""A docstring naming {CONSTANT} is not a use."""\n')


def test_no_production_module_binds_the_frozen_constant() -> None:
    offenders = _offenders()
    assert not offenders, (
        f"{CONSTANT} is frozen at import time and must not be used in production: "
        f"{offenders}. Call get_key_facts_db_path() instead — it re-resolves per call "
        "so NAVIG_HOME / NAVIG_CONFIG_DIR overrides are honoured."
    )
