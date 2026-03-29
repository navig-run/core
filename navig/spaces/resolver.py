from __future__ import annotations

import os
from pathlib import Path

from navig.spaces.contracts import SpaceConfig, normalize_space_name


def _find_project_navig_root(cwd: Path) -> Path | None:
    for parent in [cwd, *cwd.parents]:
        navig_dir = parent / ".navig"
        if navig_dir.is_dir():
            return navig_dir
    return None


def resolve_space(name: str, cwd: Path | None = None) -> SpaceConfig:
    current_dir = (cwd or Path.cwd()).resolve()
    canonical = normalize_space_name(name)

    project_navig = _find_project_navig_root(current_dir)
    if project_navig is not None:
        project_space = project_navig / "spaces" / canonical
        if project_space.exists():
            return SpaceConfig(
                requested_name=name,
                canonical_name=canonical,
                path=project_space,
                scope="project",
            )

    global_space = Path.home() / ".navig" / "spaces" / canonical
    return SpaceConfig(
        requested_name=name,
        canonical_name=canonical,
        path=global_space,
        scope="global",
    )


def discover_space_paths(cwd: Path | None = None) -> dict[str, SpaceConfig]:
    current_dir = (cwd or Path.cwd()).resolve()
    discovered: dict[str, SpaceConfig] = {}

    global_spaces = Path.home() / ".navig" / "spaces"
    if global_spaces.exists():
        for entry in sorted(global_spaces.iterdir()):
            if entry.is_dir():
                discovered[entry.name] = SpaceConfig(
                    requested_name=entry.name,
                    canonical_name=normalize_space_name(entry.name),
                    path=entry,
                    scope="global",
                )

    project_navig = _find_project_navig_root(current_dir)
    if project_navig is not None:
        project_spaces = project_navig / "spaces"
        if project_spaces.exists():
            for entry in sorted(project_spaces.iterdir()):
                if entry.is_dir():
                    canonical = normalize_space_name(entry.name)
                    discovered[canonical] = SpaceConfig(
                        requested_name=entry.name,
                        canonical_name=canonical,
                        path=entry,
                        scope="project",
                    )

    return discovered


def get_default_space() -> str:
    env_space = os.environ.get("NAVIG_SPACE", "").strip()
    if env_space:
        return normalize_space_name(env_space)
    return "life"
