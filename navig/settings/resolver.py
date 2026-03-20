"""
navig.settings.resolver — VSCode-style layered settings resolver.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("navig.settings")

# ── Default navig.* settings ──────────────────────────────────

DEFAULTS: Dict[str, Any] = {
    "navig.ai.provider": "openai",
    "navig.ai.model": "gpt-4o-mini",
    "navig.ai.temperature": 0.4,
    "navig.ai.max_tokens": 4096,
    "navig.daemon.port": 8765,
    "navig.daemon.host": "127.0.0.1",
    "navig.daemon.auto_start": True,
    "navig.inbox.mode": "copy",
    "navig.inbox.conflict": "rename",
    "navig.inbox.min_confidence": 0.30,
    "navig.inbox.watch_interval": 3.0,
    "navig.mesh.enabled": False,
    "navig.mesh.port": 5354,
    "navig.memory.tier_default": "project",
    "navig.memory.search_tiers": ["project", "layer", "global"],
    "navig.safety.mode": "standard",
    "navig.safety.require_confirmation": True,
    "navig.vault.backend": "local",
    "navig.isolation": False,
    "navig.telemetry.enabled": False,
    "navig.ui.theme": "dark",
    "navig.ui.compact": False,
}

# ── Secret reference pattern ──────────────────────────────────

_SECRET_RE = re.compile(r"\$\{BLACKBOX:([^}]+)\}")


def _global_settings_dir() -> Path:
    try:
        from navig.platform.paths import navig_config_dir
        return navig_config_dir()
    except Exception:
        return Path.home() / ".navig"


def _layers_dir() -> Path:
    return _global_settings_dir() / "layers"


# ── Deep merge ────────────────────────────────────────────────

def _deep_merge(base: Dict, override: Dict) -> Dict:
    """Recursively merge *override* into a copy of *base*."""
    result = copy.deepcopy(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = copy.deepcopy(v)
    return result


# ── Flat key helpers ──────────────────────────────────────────

def _flatten(d: Dict, prefix: str = "") -> Dict[str, Any]:
    """Flatten nested dict to dot-separated keys."""
    out: Dict[str, Any] = {}
    for k, v in d.items():
        full_key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, full_key))
        else:
            out[full_key] = v
    return out


def _unflatten(flat: Dict[str, Any]) -> Dict:
    """Build nested dict from dot-separated keys."""
    result: Dict = {}
    for key, value in flat.items():
        parts = key.split(".")
        node = result
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return result


# ── Resolver ──────────────────────────────────────────────────

class SettingsResolver:
    """
    Resolve settings from the layered chain and expose a flat key API.

    Layers (lowest → highest priority):
      defaults → global → layer → project → local

    Parameters
    ----------
    project_root:
        Directory containing ``.navig/``.  Auto-detected if None.
    layer:
        Named layer directory under ``~/.navig/layers/<layer>/``.
        Used for formation-specific or host-specific overrides.
    resolve_secrets:
        Resolve ``${BLACKBOX:key}`` references against the vault.
    """

    def __init__(
        self,
        project_root: Optional[Path] = None,
        layer: Optional[str] = None,
        resolve_secrets: bool = True,
    ) -> None:
        self.project_root = project_root or _detect_project_root()
        self.layer = layer
        self._resolve_secrets = resolve_secrets
        self._cache: Optional[Dict[str, Any]] = None

    def resolve(self, refresh: bool = False) -> Dict[str, Any]:
        """
        Return the fully merged flat settings dict.

        Result is cached; pass ``refresh=True`` to re-read from disk.
        """
        if self._cache is None or refresh:
            self._cache = self._build()
        return self._cache

    def get(self, key: str, default: Any = None) -> Any:
        """Get a single setting by dot-separated key."""
        return self.resolve().get(key, default)

    def set(
        self,
        key: str,
        value: Any,
        layer: str = "project",
    ) -> None:
        """
        Persist a setting to a specific layer file.

        Parameters
        ----------
        layer:
            "global"  → ``~/.navig/settings.json``
            "local"   → ``<project_root>/.navig/settings.local.json``
            "project" → ``<project_root>/.navig/settings.json``
            str       → ``~/.navig/layers/<layer>/settings.json``
        """
        file_path = self._layer_path(layer)
        file_path.parent.mkdir(parents=True, exist_ok=True)

        existing: Dict = {}
        if file_path.is_file():
            try:
                existing = json.loads(file_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        # Support nested keys
        parts = key.split(".")
        node = existing
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

        file_path.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        self._cache = None  # invalidate cache

    def all_sources(self) -> List[Tuple[str, Path, bool]]:
        """
        Return list of (layer_name, path, exists) for each settings file
        in resolution order (lowest priority first).
        """
        sources = [
            ("global",  _global_settings_dir() / "settings.json", False),
        ]
        if self.layer:
            sources.append((
                f"layer:{self.layer}",
                _layers_dir() / self.layer / "settings.json",
                False,
            ))
        if self.project_root:
            sources.append(("project", self.project_root / ".navig" / "settings.json", False))
            sources.append(("local",   self.project_root / ".navig" / "settings.local.json", False))

        return [(name, path, path.is_file()) for name, path, _ in sources]

    # ── Internal ──────────────────────────────────────────

    def _build(self) -> Dict[str, Any]:
        merged = copy.deepcopy(DEFAULTS)

        for _name, path, exists in self.all_sources():
            if not exists:
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                flat = _flatten(raw)
                merged = _deep_merge(merged, flat)
            except Exception as exc:
                logger.warning("Could not read settings from %s: %s", path, exc)

        if self._resolve_secrets:
            merged = self._expand_secrets(merged)

        return merged

    def _expand_secrets(self, flat: Dict[str, Any]) -> Dict[str, Any]:
        """Replace ${BLACKBOX:key} references with vault values."""
        result: Dict[str, Any] = {}
        for k, v in flat.items():
            if isinstance(v, str) and _SECRET_RE.search(v):
                v = _SECRET_RE.sub(lambda m: self._vault_get(m.group(1)), v)
            result[k] = v
        return result

    @staticmethod
    def _vault_get(key: str) -> str:
        try:
            from navig.vault.manager import VaultManager
            manager = VaultManager()
            secret = manager.get(key)
            return secret if secret is not None else f"${{BLACKBOX:{key}}}"
        except Exception:
            return f"${{BLACKBOX:{key}}}"

    def _layer_path(self, layer: str) -> Path:
        if layer == "global":
            return _global_settings_dir() / "settings.json"
        elif layer == "local":
            return (self.project_root or Path.cwd()) / ".navig" / "settings.local.json"
        elif layer == "project":
            return (self.project_root or Path.cwd()) / ".navig" / "settings.json"
        else:
            return _layers_dir() / layer / "settings.json"


# ── Module-level convenience API ──────────────────────────────

_default_resolver: Optional[SettingsResolver] = None


def _get_resolver() -> SettingsResolver:
    global _default_resolver
    if _default_resolver is None:
        _default_resolver = SettingsResolver()
    return _default_resolver


def get(key: str, default: Any = None) -> Any:
    """Get a setting by dot-separated key using auto-detected project root."""
    return _get_resolver().get(key, default)


def get_all() -> Dict[str, Any]:
    """Return all resolved settings as a flat dict."""
    return _get_resolver().resolve()


def set_setting(key: str, value: Any, layer: str = "project") -> None:
    """Persist a setting to the specified layer."""
    _get_resolver().set(key, value, layer=layer)
    # Reset module resolver so next get() sees the update
    global _default_resolver
    _default_resolver = None


# ── Project root detection ────────────────────────────────────

def _detect_project_root() -> Optional[Path]:
    """Walk up from cwd looking for a .navig/ directory."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".navig").is_dir():
            return parent
    return None
