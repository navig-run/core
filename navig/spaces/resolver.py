from __future__ import annotations

import os
from pathlib import Path

from navig.platform import paths
from navig.spaces.contracts import SpaceConfig, normalize_space_name


def _find_project_navig_root(cwd: Path) -> Path | None:
    """Walk up to the nearest project ``.navig/``.

    The home ``~/.navig`` (and the global config dir) is the GLOBAL layer — never
    a *project* — so it is skipped: otherwise any cwd under the home directory
    would surface every global space as a bogus "project" space.
    """
    home = Path.home()
    try:
        global_cfg = paths.config_dir().resolve()
    except Exception:  # noqa: BLE001
        global_cfg = None
    for parent in [cwd, *cwd.parents]:
        navig_dir = parent / ".navig"
        if not navig_dir.is_dir():
            continue
        try:
            if parent.resolve() == home.resolve():
                continue
            if global_cfg is not None and navig_dir.resolve() == global_cfg:
                continue
        except OSError:
            pass
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

    global_space = paths.config_dir() / "spaces" / canonical
    return SpaceConfig(
        requested_name=name,
        canonical_name=canonical,
        path=global_space,
        scope="global",
    )


def spaces_roots() -> list[Path]:
    """Roots scanned for spaces: always ``~/.navig/spaces`` + any configured
    ``spaces.roots`` (e.g. ``D:\\spaces``). Read from the raw config dict so it
    works regardless of the typed schema (mirrors ``get_active_space``)."""
    roots: list[Path] = [paths.config_dir() / "spaces"]
    try:
        from navig.config import get_config_manager  # noqa: PLC0415

        cfg = get_config_manager().global_config or {}
        spaces_cfg = cfg.get("spaces", {}) if isinstance(cfg, dict) else {}
        raw = spaces_cfg.get("roots") if isinstance(spaces_cfg, dict) else None
        if isinstance(raw, list):
            for r in raw:
                roots.append(Path(str(r)).expanduser())
    except Exception:  # noqa: BLE001
        pass
    # de-dupe by resolved path, preserve order
    seen: set[str] = set()
    out: list[Path] = []
    for p in roots:
        try:
            key = str(p.resolve())
        except Exception:  # noqa: BLE001
            key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _is_space(entry: Path) -> bool:
    from navig.spaces.space_manifest import is_space_dir  # noqa: PLC0415

    if is_space_dir(entry):
        return True
    # legacy markdown-only spaces (files directly in the dir)
    return any((entry / f).exists() for f in ("VISION.md", "index.md", "CURRENT_PHASE.md"))


def _project_has_content(entry: Path) -> bool:
    """The opened folder counts as a listable space only if it has real content
    (a manifest or plans) — a bare, empty ``.navig/`` is a workshop for cwd/
    capability purposes but is not surfaced in lists/briefings."""
    from navig.spaces.space_manifest import find_manifest_file  # noqa: PLC0415

    nav = entry / ".navig"
    if find_manifest_file(entry) is not None:
        return True
    if (nav / "plans").is_dir():
        return True
    return any(
        (entry / f).exists() or (nav / f).exists()
        for f in ("VISION.md", "CURRENT_PHASE.md", "index.md")
    )


def discover_space_paths(
    cwd: Path | None = None, *, include_disabled: bool = False
) -> dict[str, SpaceConfig]:
    """Discover spaces across all roots + the project. Auto-registers found
    spaces (enabled) and returns only enabled ones unless *include_disabled*."""
    from navig.spaces import registry as _registry  # noqa: PLC0415
    from navig.spaces.space_manifest import load_space_manifest  # noqa: PLC0415

    current_dir = (cwd or Path.cwd()).resolve()
    discovered: dict[str, SpaceConfig] = {}
    seen: set[str] = set()

    def _record(entry: Path, scope: str) -> None:
        try:
            rp = str(entry.resolve())
        except Exception:  # noqa: BLE001
            rp = str(entry)
        if rp in seen:
            return
        man = load_space_manifest(entry)
        sid = man.resolved_id or entry.name
        canonical = normalize_space_name(sid)
        _registry.ensure_registered(
            entry, id=canonical, name=man.resolved_name or entry.name, source=scope
        )
        if not include_disabled and not _registry.is_enabled(entry):
            return
        seen.add(rp)
        discovered[canonical] = SpaceConfig(
            requested_name=entry.name, canonical_name=canonical, path=entry, scope=scope
        )

    def _scan_container(container: Path, scope: str) -> None:
        # Every non-dot child dir of a spaces container is a space (even an empty
        # one); dot-dirs (.git/.backup/…) are skipped.
        if not container.exists():
            return
        for child in sorted(container.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                _record(child, scope)

    # Global + configured roots are containers of spaces.
    for root in spaces_roots():
        _scan_container(root, "global")

    # Project: spaces under <project>/.navig/spaces + the opened folder itself.
    project_navig = _find_project_navig_root(current_dir)
    if project_navig is not None:
        _scan_container(project_navig / "spaces", "project")
        parent = project_navig.parent
        if _project_has_content(parent):  # the opened folder is a workshop with content
            _record(parent, "project")

    return discovered


def get_default_space() -> str:
    env_space = os.environ.get("NAVIG_SPACE", "").strip()
    if env_space:
        return normalize_space_name(env_space)
    return "default"
