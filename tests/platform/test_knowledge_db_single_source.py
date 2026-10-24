"""``knowledge.db`` is named in exactly ONE place: ``paths.knowledge_db_path()``.

The split this guards against is not hypothetical: the gateway wrote
``<config_dir>/knowledge.db``, ``navig memory knowledge`` wrote
``<config_dir>/memory/knowledge.db``, and ``KnowledgeBase``'s documentation pointed at
``<data_dir>/knowledge.db`` — three answers to "where is my knowledge". A row written on
one surface was invisible on the other, both files live on the same machine.

``paths.knowledge_db_path()`` is now the authority. This test fails on any module that
builds a knowledge-db path by hand instead of calling it — the same guard shape as
``test_debug_log_single_source.py`` (which exists because eight call sites invented their
own ``debug.log`` spelling, and every one pointed at a file the logger never writes).

Docstrings are exempt (they may quote a legacy path when explaining the migration);
string CONSTANTS are not.
"""

from __future__ import annotations

import ast
from pathlib import Path

import navig

PKG = Path(navig.__file__).resolve().parent

#: Where the literal "knowledge.db" may legitimately appear as a string constant.
ALLOWED: dict[str, str] = {
    "platform/paths.py": "knowledge_db_path() — the definition itself",
    "memory/knowledge_migration.py": (
        "the migration detects the legacy copies BY NAME (that is its whole job)"
    ),
}


def _docstring_lines(tree: ast.AST) -> set[int]:
    """Line numbers of module/class/function docstrings (their contents are prose)."""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                lines.add(body[0].lineno)
    return lines


def _offenders() -> dict[str, list[int]]:
    """Modules holding a "knowledge.db" string constant outside a docstring."""
    found: dict[str, list[int]] = {}
    for py in sorted(PKG.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except (OSError, UnicodeDecodeError, SyntaxError):  # pragma: no cover - unparsable file
            continue
        docstrings = _docstring_lines(tree)
        hits = sorted(
            {
                node.lineno
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant)
                and node.value == "knowledge.db"
                and node.lineno not in docstrings
            }
        )
        if hits:
            found[py.relative_to(PKG).as_posix()] = hits
    return found


def test_only_the_resolver_names_the_knowledge_db() -> None:
    unexpected = {m: h for m, h in _offenders().items() if m not in ALLOWED}
    assert not unexpected, (
        "These modules build a knowledge.db path from a literal instead of calling\n"
        "navig.platform.paths.knowledge_db_path(). Every historical one pointed at a DIFFERENT\n"
        "file than the other surfaces (gateway storage_dir vs config_dir()/memory vs data_dir),\n"
        "so knowledge written through one surface was invisible through another.\n\n"
        + "\n".join(f"  {m}: line(s) {h}" for m, h in sorted(unexpected.items()))
    )


def test_allowlist_has_no_stale_entries() -> None:
    stale = sorted(set(ALLOWED) - set(_offenders()))
    assert not stale, (
        "These modules no longer contain the literal — delete their ALLOWED entries:\n"
        + "\n".join(f"  {m}" for m in stale)
    )
