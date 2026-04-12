from __future__ import annotations

import shutil
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import yaml

from navig.core.yaml_io import safe_load_yaml

_SENTINEL = ".workspace_to_spaces.migrated"


def _write_config(config_file: Path, data: dict) -> None:
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )


def _extract_active_space(cfg: dict) -> str:
    if not isinstance(cfg, dict):
        return ""

    space_cfg = cfg.get("space", {})
    if isinstance(space_cfg, dict):
        value = str(space_cfg.get("active", "")).strip()
        if value:
            return value

    value = str(cfg.get("active_space", "")).strip()
    if value:
        return value

    spaces_cfg = cfg.get("spaces", {})
    if isinstance(spaces_cfg, dict):
        value = str(spaces_cfg.get("active", "")).strip()
        if value:
            return value

    return ""


def _normalize_agent_workspace_path(cfg: dict, navig_root: Path, active_space: str) -> bool:
    """Rewrite legacy agents.defaults.workspace path from ~/.navig/workspace → ~/.navig/spaces/<active>."""
    agents = cfg.get("agents", {})
    if not isinstance(agents, dict):
        return False
    defaults = agents.get("defaults", {})
    if not isinstance(defaults, dict):
        return False

    workspace_value = str(defaults.get("workspace", "")).strip()
    if not workspace_value:
        return False

    old_workspace = (navig_root / "workspace").resolve()
    target_space = (navig_root / "spaces" / active_space).resolve()

    try:
        current = Path(workspace_value).expanduser().resolve()
    except Exception:
        return False

    if current == old_workspace:
        defaults["workspace"] = str(target_space)
        agents["defaults"] = defaults
        cfg["agents"] = agents
        return True

    return False


def _move_workspace_payload(old_root: Path, spaces_root: Path) -> tuple[int, Path] | None:
    if not old_root.exists():
        return None

    legacy_target = spaces_root / "default" / "legacy-workspace"
    entries = list(old_root.iterdir())
    moved = 0

    if entries:
        legacy_target.mkdir(parents=True, exist_ok=True)
        for item in entries:
            dst = legacy_target / item.name
            if dst.exists():
                raise RuntimeError(
                    f"Migration conflict: destination already exists: {dst}\n"
                    f"Recovery: move or rename either side, then rerun `navig`.")
            shutil.move(str(item), str(dst))
            moved += 1

    try:
        old_root.rmdir()
    except OSError as exc:
        raise RuntimeError(
            f"Failed to remove legacy workspace directory: {old_root}\n"
            f"Reason: {exc}\n"
            f"Recovery: ensure directory is empty and writable, then rerun `navig`."
        ) from exc

    return moved, legacy_target


def migrate_workspace_to_spaces(
    navig_root: Path,
    *,
    notify: Callable[[str], None] = print,
) -> None:
    """One-time migration: ~/.navig/workspace → ~/.navig/spaces/default/legacy-workspace."""
    sentinel = navig_root / _SENTINEL

    config_file = navig_root / "config.yaml"
    cache_file = navig_root / "cache" / "active_space.txt"
    old_workspace = navig_root / "workspace"
    spaces_root = navig_root / "spaces"

    try:
        cfg = safe_load_yaml(config_file) or {}
        active = _extract_active_space(cfg)
        if not active:
            active = "default"

        config_changed = _normalize_agent_workspace_path(cfg, navig_root, active)

        space_cfg = cfg.get("space", {})
        if not isinstance(space_cfg, dict):
            space_cfg = {}
        space_cfg["active"] = active
        cfg["space"] = space_cfg
        cfg["active_space"] = active

        legacy_spaces = cfg.get("spaces", {})
        if isinstance(legacy_spaces, dict):
            legacy_spaces.pop("active", None)
            if legacy_spaces:
                cfg["spaces"] = legacy_spaces
            else:
                cfg.pop("spaces", None)

        config_changed = True

        if config_changed or not config_file.exists():
            _write_config(config_file, cfg)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(active, encoding="utf-8")

        # Ensure default space files are always scaffolded (idempotent — never overwrites)
        try:
            from navig.commands.space import _ensure_default_space  # noqa: PLC0415

            _ensure_default_space()
        except Exception as _e:  # noqa: BLE001
            # best-effort: scaffolding failure must not block migration
            import logging as _logging
            _logging.getLogger(__name__).debug("_ensure_default_space skipped: %s", _e)

        if not sentinel.exists():
            migration_result = _move_workspace_payload(old_workspace, spaces_root)
            if migration_result is not None:
                moved_count, moved_to = migration_result
                if moved_count:
                    notify(f"[navig] Migrated {moved_count} legacy item(s) to {moved_to}")
                else:
                    notify(f"[navig] Removed empty legacy workspace directory: {old_workspace}")

            sentinel.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Workspace→Spaces migration failed: {exc}\n"
            f"Recovery: fix filesystem/config issue, then rerun `navig`; no destructive rollback was performed."
        ) from exc


def ensure_no_stale_spaces_registration() -> None:
    """Abort startup when legacy `navig.commands.spaces` registration still exists."""
    from navig.cli import _EXTERNAL_CMD_MAP

    stale = sorted(
        command
        for command, (mod_path, _attr) in _EXTERNAL_CMD_MAP.items()
        if mod_path == "navig.commands.spaces"
    )
    if stale:
        joined = ", ".join(stale)
        raise RuntimeError(
            "Stale legacy command registrations detected for removed module "
            f"`navig.commands.spaces`: {joined}. "
            "Update `navig/cli/__init__.py` to route these commands to `navig.commands.space`."
        )
