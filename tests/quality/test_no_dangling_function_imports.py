"""Function-local / try-wrapped intra-package imports must resolve — or be in the baseline.

Companion to ``test_no_dangling_module_imports.py``. That guard catches the *module-level,
un-guarded* `from navig.X import Y` where Y is missing — the crash-on-import class. This one
catches the other half: imports inside a **function** or a **protective try/except**. Those
don't crash on import; they fall into the ``except`` and the code **silently degrades** — a
whole feature quietly does nothing. That is how `/mode` (removed soul API), the Block secret
resolver (`navig.vault.get_secret` never existed), and `navig.config.get` all shipped broken:
an import of a name that does not exist, hidden behind a fallback.

Ruff can't see this — it never resolves names across modules. So this walks every such import
and confirms the name resolves in its target, with an explicit **baseline allowlist** of the
sites that are already broken or intentionally optional. The allowlist is a ratchet: NEW
dangling imports fail the build immediately (the check that would have prevented this class);
the known ones are documented, and each is re-checked so it can't rot silently.

Every allowlist entry is one of:
  * ``intentional`` — the target is a deliberate optional (a feature not built yet), so the
    ``except`` branch IS the designed behaviour; or
  * ``api-drift`` — the name was removed/renamed and the caller silently degrades. Tracked
    for fix: each needs the correct replacement AND a call-signature check (they are NOT
    mechanical swaps — e.g. `backup_all_databases_cmd({})` → `backup_all_databases(name,
    compress, options)` changed shape). **Fix the site, then delete its entry here.**
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

CORE = Path(__file__).resolve().parents[2]
NAVIG = CORE / "navig"

# (relative posix path, target module, imported name) -> short reason.
# See the module docstring for the two categories. Shrinking this list is the goal.
ALLOWLIST: dict[tuple[str, str, str], str] = {
    ("navig/commands/agents.py", "navig.agents", "list_agents"):
        "intentional: `navig agents` is not built yet; the except prints 'not implemented'",
    ("navig/commands/matrix.py", "navig.vault.core", "CredentialsVault"):
        "api-drift: CredentialsVault moved to the navig.vault package AND Vault.get is now (provider, profile_id)-based, not id-based — auth-lookup rewrite",
    ("navig/gateway/channels/telegram_autoheal.py", "navig.proactive.error_resolution", "analyze_error"):
        "api-drift: error_resolution exposes ErrorResolution class, not an analyze_error function",
}

_CATCH = {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}
_import_cache: dict[str, object] = {}
_conditional_cache: dict[str, set[str]] = {}


def _try_import(mod_name: str):
    if mod_name in _import_cache:
        return _import_cache[mod_name]
    try:
        mod = importlib.import_module(mod_name)
    except Exception:  # noqa: BLE001 — optional dep missing / target itself broken
        mod = None
    _import_cache[mod_name] = mod
    return mod


def _conditionally_defined_names(mod_name: str) -> set[str]:
    """Names a module defines inside a try/except — present only under some condition."""
    if mod_name in _conditional_cache:
        return _conditional_cache[mod_name]
    names: set[str] = set()
    mod = _try_import(mod_name)
    src = getattr(mod, "__file__", None)
    if src:
        try:
            tree = ast.parse(Path(src).read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Try):
                    continue
                for stmt in ast.walk(node):
                    if isinstance(stmt, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                        names.add(stmt.name)
                    elif isinstance(stmt, ast.Assign):
                        names.update(t.id for t in stmt.targets if isinstance(t, ast.Name))
                    elif isinstance(stmt, ast.ImportFrom):
                        names.update(a.asname or a.name for a in stmt.names)
        except (SyntaxError, OSError):
            pass
    _conditional_cache[mod_name] = names
    return names


def _resolve_relative(file_path: Path, level: int, module: str | None) -> str | None:
    rel = file_path.relative_to(CORE).with_suffix("")
    pkg_parts = list(rel.parts[:-1])
    if level > len(pkg_parts):
        return None
    base = pkg_parts[: len(pkg_parts) - (level - 1)]
    if module:
        base = base + module.split(".")
    return ".".join(base) if base else None


class _Context(ast.NodeVisitor):
    """Collect ImportFrom nodes that are inside a FUNCTION or a protective try/except —
    the complement of what the module-level guard checks."""

    def __init__(self) -> None:
        self._func_depth = 0
        self._protective_try_depth = 0
        self.guarded: list[ast.ImportFrom] = []

    def visit_FunctionDef(self, node):  # noqa: N802
        self._func_depth += 1
        self.generic_visit(node)
        self._func_depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Try(self, node):  # noqa: N802
        protective = any(
            h.type is None
            or (isinstance(h.type, ast.Name) and h.type.id in _CATCH)
            or (
                isinstance(h.type, ast.Tuple)
                and any(isinstance(e, ast.Name) and e.id in _CATCH for e in h.type.elts)
            )
            for h in node.handlers
        )
        if protective:
            self._protective_try_depth += 1
        for child in node.body:
            self.visit(child)
        if protective:
            self._protective_try_depth -= 1
        for h in node.handlers:
            for child in h.body:
                self.visit(child)
        for child in node.orelse + node.finalbody:
            self.visit(child)

    def visit_ImportFrom(self, node):  # noqa: N802
        if self._func_depth > 0 or self._protective_try_depth > 0:
            self.guarded.append(node)


def _dangling_sites() -> dict[tuple[str, str, str], int]:
    """Every guarded intra-package `from navig.X import Y` where Y does not resolve.
    Keyed by (relpath, module, name) → first line seen."""
    found: dict[tuple[str, str, str], int] = {}
    for f in sorted(NAVIG.rglob("*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8-sig"))
        except SyntaxError:
            continue
        ctx = _Context()
        ctx.visit(tree)
        rel = f.relative_to(CORE).as_posix()
        for node in ctx.guarded:
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
                continue  # optional dep missing / unimportable — cannot verify
            conditional = _conditionally_defined_names(target)
            for name in names:
                if hasattr(mod, name):
                    continue
                if _try_import(f"{target}.{name}") is not None:
                    continue  # submodule, not an attribute
                if name in conditional:
                    continue  # defined under a try/except in the target
                key = (rel, target, name)
                found.setdefault(key, node.lineno)
    return found


def test_no_new_dangling_function_import() -> None:
    sites = _dangling_sites()
    unlisted = {k: ln for k, ln in sites.items() if k not in ALLOWLIST}
    assert not unlisted, (
        "a function-local / try-wrapped `from navig.X import Y` imports a name that does not "
        "exist — it will fall into its `except` and the feature silently does nothing. Fix "
        "the name (verify the call shape too — these are rarely mechanical), or add a baseline "
        "entry with a reason if it is genuinely intentional:\n  "
        + "\n  ".join(f"{r}:{ln}: from {m} import {n}" for (r, m, n), ln in sorted(unlisted.items()))
    )


def test_allowlist_has_no_stale_entries() -> None:
    """Every baseline entry must still be genuinely dangling. When a site is fixed (its name
    resolves again), its entry must be REMOVED — the ratchet only tightens."""
    sites = _dangling_sites()
    stale = [k for k in ALLOWLIST if k not in sites]
    assert not stale, (
        "these baseline entries no longer dangle — the site was fixed or the file moved. "
        "Delete them from ALLOWLIST:\n  " + "\n  ".join(f"{r}: from {m} import {n}" for r, m, n in stale)
    )


def test_the_guard_catches_a_synthetic_dangling_import(tmp_path) -> None:
    """Prove the detection fires on the exact pattern — a function-local import of a missing
    name — without depending on any real site."""
    src = (
        "def f():\n"
        "    try:\n"
        "        from navig.config import THIS_DOES_NOT_EXIST\n"
        "    except Exception:\n"
        "        pass\n"
    )
    tree = ast.parse(src)
    ctx = _Context()
    ctx.visit(tree)
    assert len(ctx.guarded) == 1
    node = ctx.guarded[0]
    mod = _try_import("navig.config")
    assert mod is not None and not hasattr(mod, "THIS_DOES_NOT_EXIST")
