"""Persona resolver — 3-level fallback chain.

Search order (first match wins):
  1. <cwd>/.navig/personas/<name>/           project-local override
  2. ~/.navig/personas/<name>/               user home
  3. navig/resources/personas/<name>/        package defaults
"""
from __future__ import annotations

from pathlib import Path

from navig.platform.paths import config_dir


def _find_project_navig_root(cwd: Path) -> Path | None:
    for parent in [cwd, *cwd.parents]:
        navig_dir = parent / ".navig"
        if navig_dir.is_dir():
            return navig_dir
    return None


def resolve_persona(name: str, cwd: Path | None = None) -> Path | None:
    """Return the directory for *name* following the 3-level chain.

    Returns ``None`` if the persona does not exist anywhere.
    """
    current_dir = (cwd or Path.cwd()).resolve()
    slug = name.strip().lower()

    # 1. Project-local
    project_navig = _find_project_navig_root(current_dir)
    if project_navig is not None:
        candidate = project_navig / "personas" / slug
        if candidate.is_dir():
            return candidate

    # 2. User home
    user_candidate = config_dir() / "personas" / slug
    if user_candidate.is_dir():
        return user_candidate

    # 3. Package defaults
    pkg_candidate = Path(__file__).parent.parent / "resources" / "personas" / slug
    if pkg_candidate.is_dir():
        return pkg_candidate

    return None


def discover_persona_paths(cwd: Path | None = None) -> dict[str, Path]:
    """Return all available personas as {name: directory}.

    Project personas override user-home; user-home overrides package defaults.
    """
    current_dir = (cwd or Path.cwd()).resolve()
    discovered: dict[str, Path] = {}

    # Package defaults first (lowest priority)
    pkg_root = Path(__file__).parent.parent / "resources" / "personas"
    if pkg_root.exists():
        for entry in sorted(pkg_root.iterdir()):
            if entry.is_dir():
                discovered[entry.name] = entry

    # User home overrides package
    user_root = config_dir() / "personas"
    if user_root.exists():
        for entry in sorted(user_root.iterdir()):
            if entry.is_dir():
                discovered[entry.name] = entry

    # Project-local overrides user home
    project_navig = _find_project_navig_root(current_dir)
    if project_navig is not None:
        project_root = project_navig / "personas"
        if project_root.exists():
            for entry in sorted(project_root.iterdir()):
                if entry.is_dir():
                    discovered[entry.name] = entry

    return discovered
