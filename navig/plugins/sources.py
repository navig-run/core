"""Which editable-declared plugins actually resolve to your source tree.

``core/pyproject.toml``'s ``[tool.uv.sources]`` declares a set of plugins as
``editable = true`` local paths. When one of them resolves to a NON-editable copy in
site-packages instead, the CLI executes code that exists nowhere in the repo — while the
test suites keep reading the repo, because the gate runs ``python -m pytest`` with the
plugin directory as cwd and ``-m`` puts the source on ``sys.path`` first.

Measured 2026-09-06: 4 of 12 declared plugins were shadowed, and the mobile plugin's
``ui`` sub-app answered "No such command" for a group registered unconditionally in the
source — while that plugin's own 39 tests passed and ``navig plugin list`` printed a green
tick over it.

This module is the single source of that answer. ``navig doctor`` renders it as a health
section; ``navig plugin list`` renders it as a per-row state. Two surfaces, one rule — a
second copy of this logic is how they would come to disagree.

⚠ Deliberately NOT cached. A ``@lru_cache`` here would be read once per process and then
be wrong for the rest of it, and the identical pattern in
``adapters/automation/powershell`` leaked a fake binary between tests until it was fixed.
The audit is a handful of ``find_spec`` calls against top-level packages, which resolve
from the file tree without executing any plugin's module body.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["PluginSourceAudit", "audit_plugin_sources", "is_plugin_source_tree"]


@dataclass(frozen=True)
class PluginSourceAudit:
    """The outcome of comparing declared plugin sources against what imports resolve to."""

    declared: int
    verified: tuple[str, ...] = ()
    shadowed: tuple[str, ...] = ()
    #: ``(dist, reason)`` — counted, never guessed at. "Not installed" is not a failure
    #: (an optional extra may simply be absent) but it is also not something to tick.
    unresolved: tuple[tuple[str, str], ...] = ()
    #: Repo-relative paths for the ``pip install -e`` remedy, in ``shadowed`` order.
    fix_paths: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        """True when every declared plugin resolves to a source tree."""
        return not self.shadowed


def is_plugin_source_tree(origin: Path, source_dir_name: str) -> bool:
    """Does ``origin`` live inside a ``plugins/<source_dir_name>/`` checkout?

    Deliberately root-AGNOSTIC. An editable install points at ONE checkout, and a command
    may run from a ``.dev/worktrees/<slug>`` copy of the same repo — comparing against the
    current root reported 11 of 12 plugins shadowed on a machine where the real answer was
    4, because seven correct installs pointed at the main checkout. The question worth
    answering is source-tree vs installed-copy, not which checkout.
    """
    for parent in origin.parents:
        if parent.name == source_dir_name and parent.parent.name == "plugins":
            return True
    return False


def _declared_local_plugins(pyproject: Path) -> dict[str, str] | None:
    """``{dist: relative path}`` from ``[tool.uv.sources]``; None if unreadable."""
    try:
        import tomllib  # noqa: PLC0415 - py3.11+
    except ImportError:
        # No TOML reader: report NOTHING rather than an answer we cannot back.
        return None
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    sources = (data.get("tool", {}).get("uv", {}).get("sources", {}) or {}).items()
    return {
        dist: spec["path"]
        for dist, spec in sources
        if isinstance(spec, dict) and isinstance(spec.get("path"), str)
    }


def _package_dir_name(source: Path) -> str | None:
    """The ``navig_*`` package directory inside a plugin's source checkout."""
    try:
        return next(
            (
                child.name
                for child in sorted(source.iterdir())
                if child.is_dir()
                and child.name.startswith("navig")
                and (child / "__init__.py").is_file()
            ),
            None,
        )
    except OSError:
        return None


def audit_plugin_sources(root: Path | None = None) -> PluginSourceAudit | None:
    """Compare declared editable plugin sources against what imports resolve to.

    Returns None when there is nothing to compare against — no repo, no
    ``core/pyproject.toml``, an unreadable one, or no local sources declared. An end user
    who installed from PyPI is in that case, so every caller renders nothing rather than a
    verdict it cannot support.
    """
    if root is None:
        from navig.commands.repo import resolve_repo_root  # noqa: PLC0415

        root = resolve_repo_root()
    if root is None:
        return None

    pyproject = root / "core" / "pyproject.toml"
    if not pyproject.is_file():
        return None

    declared = _declared_local_plugins(pyproject)
    if not declared:
        return None

    verified: list[str] = []
    shadowed: list[str] = []
    unresolved: list[tuple[str, str]] = []
    fix_paths: list[str] = []

    for dist, rel in sorted(declared.items()):
        source = (pyproject.parent / rel).resolve()
        if not source.is_dir():
            unresolved.append((dist, "declared path missing"))
            continue
        package = _package_dir_name(source)
        if package is None:
            unresolved.append((dist, "no package dir"))
            continue
        try:
            spec = importlib.util.find_spec(package)
        except (ImportError, ValueError):
            spec = None
        origin = getattr(spec, "origin", None) if spec is not None else None
        if not origin:
            unresolved.append((dist, "not installed"))
            continue
        try:
            inside = is_plugin_source_tree(Path(origin).resolve(), source.name)
        except (OSError, ValueError):
            unresolved.append((dist, "unreadable origin"))
            continue
        if inside:
            verified.append(dist)
        else:
            shadowed.append(dist)
            try:
                fix_paths.append(source.relative_to(root).as_posix())
            except ValueError:
                fix_paths.append(str(source))

    return PluginSourceAudit(
        declared=len(declared),
        verified=tuple(verified),
        shadowed=tuple(shadowed),
        unresolved=tuple(unresolved),
        fix_paths=tuple(fix_paths),
    )
