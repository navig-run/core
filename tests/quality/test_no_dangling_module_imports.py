"""No module-level import may reference a name that does not exist in its target.

`from navig.X import Y` at module top level, where `Y` is missing from `navig.X`, is an
`ImportError` the instant `navig.X`'s importer is loaded — it takes the whole importing
module down. That is not hypothetical: `navig/tui/screens/review.py` did exactly this with
`DEFAULT_CONFIG_FILE` (a constant `navig.tui.config_model` never defined), and because
`navig.tui.__init__` pulls `review.py` in eagerly, `import navig.tui` crashed on every
install that had textual — the ENTIRE TUI was dead, and nothing caught it because those
screens had no test coverage.

Ruff does not catch this: it resolves names within a file, never across modules. So this
guard walks every module-level intra-package `from navig.X import Y` and confirms `Y`
actually resolves in `navig.X` — the mechanical net that would have caught DEFAULT_CONFIG_FILE.

Deliberately scoped to MODULE-LEVEL BARE imports (not inside a function, not inside a
protective try/except), because that is the class that crashes on import. Function-local
imports (`FUNC_BARE`) fail only when the function runs and are far noisier — several are
intentional "feature not built yet" fallbacks — so they are out of scope here; drive them
with integration tests instead.

Two kinds of legitimate absence are skipped:
  * the target needs an optional dependency that is not installed (it will not import), and
  * the name is defined CONDITIONALLY in the target (inside its own try/except — e.g.
    `navig.tui.messages.SettingsSaved`, a textual-gated class), so it exists when the
    optional dep is present, which is the only time the importer runs anyway.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

CORE = Path(__file__).resolve().parents[2]
NAVIG = CORE / "navig"

# target module name -> the imported module object, or None if it could not be imported
_import_cache: dict[str, object] = {}
# target module name -> set of names it defines inside a try/except (conditional exports)
_conditional_cache: dict[str, set[str]] = {}


def _try_import(mod_name: str):
    if mod_name in _import_cache:
        return _import_cache[mod_name]
    try:
        mod = importlib.import_module(mod_name)
    except Exception:  # noqa: BLE001 — optional dep missing, or the target itself is broken
        mod = None
    _import_cache[mod_name] = mod
    return mod


def _conditionally_defined_names(mod_name: str) -> set[str]:
    """Names a module defines inside a try/except — i.e. present only under some condition
    (typically an optional import). We must not flag an importer for these."""
    if mod_name in _conditional_cache:
        return _conditional_cache[mod_name]
    names: set[str] = set()
    mod = _try_import(mod_name)
    src_file = getattr(mod, "__file__", None)
    if src_file:
        try:
            tree = ast.parse(Path(src_file).read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Try):
                    continue
                for stmt in ast.walk(node):
                    if isinstance(stmt, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                        names.add(stmt.name)
                    elif isinstance(stmt, ast.Assign):
                        for t in stmt.targets:
                            if isinstance(t, ast.Name):
                                names.add(t.id)
                    elif isinstance(stmt, ast.ImportFrom):
                        for a in stmt.names:
                            names.add(a.asname or a.name)
        except (SyntaxError, OSError):
            pass
    _conditional_cache[mod_name] = names
    return names


def _resolve_relative(file_path: Path, level: int, module: str | None) -> str | None:
    rel = file_path.relative_to(CORE).with_suffix("")
    pkg_parts = list(rel.parts[:-1])  # containing package of the file
    if level > len(pkg_parts):
        return None
    base = pkg_parts[: len(pkg_parts) - (level - 1)]
    if module:
        base = base + module.split(".")
    return ".".join(base) if base else None


class _Context(ast.NodeVisitor):
    """Collect ImportFrom nodes that are at MODULE level and NOT inside a protective try."""

    def __init__(self) -> None:
        self._func_depth = 0
        self._protective_try_depth = 0
        self.module_bare: list[ast.ImportFrom] = []

    def visit_FunctionDef(self, node):  # noqa: N802
        self._func_depth += 1
        self.generic_visit(node)
        self._func_depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Try(self, node):  # noqa: N802
        handlers = node.handlers
        protective = any(
            h.type is None
            or (isinstance(h.type, ast.Name) and h.type.id in _CATCH)
            or (
                isinstance(h.type, ast.Tuple)
                and any(isinstance(e, ast.Name) and e.id in _CATCH for e in h.type.elts)
            )
            for h in handlers
        )
        if protective:
            self._protective_try_depth += 1
        for child in node.body:
            self.visit(child)
        if protective:
            self._protective_try_depth -= 1
        for h in handlers:
            for child in h.body:
                self.visit(child)
        for child in node.orelse + node.finalbody:
            self.visit(child)

    def visit_ImportFrom(self, node):  # noqa: N802
        if self._func_depth == 0 and self._protective_try_depth == 0:
            self.module_bare.append(node)


_CATCH = {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}


def test_no_module_level_import_references_a_missing_name() -> None:
    offenders: list[str] = []
    scanned = 0

    for f in sorted(NAVIG.rglob("*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8-sig"))
        except SyntaxError:
            continue
        ctx = _Context()
        ctx.visit(tree)
        for node in ctx.module_bare:
            target = (
                _resolve_relative(f, node.level, node.module) if node.level else node.module
            )
            if not target or not (target == "navig" or target.startswith("navig.")):
                continue
            names = [a.name for a in node.names]
            if "*" in names:
                continue
            mod = _try_import(target)
            if mod is None:
                continue  # optional dep missing / target unimportable — cannot verify
            conditional = _conditionally_defined_names(target)
            for name in names:
                scanned += 1
                if hasattr(mod, name):
                    continue
                if _try_import(f"{target}.{name}") is not None:
                    continue  # it's a submodule, not an attribute
                if name in conditional:
                    continue  # defined under a try/except in the target (optional-dep-gated)
                rel = f.relative_to(CORE).as_posix()
                offenders.append(f"{rel}:{node.lineno}: from {target} import {name}")

    assert scanned > 200, (
        f"only {scanned} module-level intra-package imports were checked — the walk is "
        "looking in the wrong place, so this guard is verifying almost nothing."
    )
    assert not offenders, (
        "module-level `from navig.X import Y` where Y does not exist in navig.X — an "
        "ImportError that crashes the importing module the moment it loads (the "
        "DEFAULT_CONFIG_FILE class). Fix the name or the target:\n  "
        + "\n  ".join(sorted(set(offenders)))
    )


def test_guard_machinery_flags_a_genuinely_missing_name() -> None:
    """Exercise the real helpers on the exact name that caused the `navig rollback` crash:
    `navig.platform.paths.navig_config_dir` does not exist (the function is `config_dir`)."""
    mod = _try_import("navig.platform.paths")
    assert mod is not None
    assert hasattr(mod, "config_dir"), "the real name must resolve"
    # The removed name is not an attribute, not a submodule, and not conditionally defined —
    # exactly the three escape hatches the guard checks — so a module-level bare import of it
    # would be flagged.
    assert not hasattr(mod, "navig_config_dir")
    assert _try_import("navig.platform.paths.navig_config_dir") is None
    assert "navig_config_dir" not in _conditionally_defined_names("navig.platform.paths")


def test_guard_skips_optional_dep_conditional_exports() -> None:
    """`SettingsSaved` is a textual-gated class defined inside a try/except in
    navig.tui.messages, so five module-level importers must NOT be flagged when textual is
    absent. The conditional-name detection is what makes that skip correct rather than a
    blanket exemption."""
    assert "SettingsSaved" in _conditionally_defined_names("navig.tui.messages")
