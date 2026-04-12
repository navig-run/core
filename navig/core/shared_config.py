"""
NAVIG Shared Configuration Singleton

Thread-safe singleton configuration manager that both core modules and plugins can use.
Provides unified access to global and project-local configuration.

Usage:
    from navig.core import Config

    config = Config()
    active_host = config.get_active_host()
    config.set('plugins.brain.db_path', '~/.navig/brain.db')
    config.save()
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import yaml

from navig.core.yaml_io import atomic_write_yaml
from navig.platform.paths import config_dir


class ConfigSingleton:
    """
    Thread-safe singleton configuration manager.

    Supports:
    - Global config: ~/.navig/config.yaml
    - Project-local config: .navig/config.yaml
    - Dot-notation key access: config.get('plugins.brain.db_path')
    - Thread-safe read/write operations
    """

    _instance: ConfigSingleton | None = None
    _lock = threading.Lock()

    def __new__(cls) -> ConfigSingleton:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialized = False
                    cls._instance = instance
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return

        with self._lock:
            if not getattr(self, "_initialized", False):
                # Global config path
                self.global_config_dir = config_dir()
                self.global_config_path = self.global_config_dir / "config.yaml"

                # Cache directory
                self.cache_dir = self.global_config_dir / "cache"

                # Plugin directory
                self.plugins_dir = self.global_config_dir / "plugins"

                # Data storage
                self._global_data: dict[str, Any] = {}
                self._project_data: dict[str, Any] = {}
                self._project_cache_path: Path | None = None
                self._project_cache_mtime_ns: int | None = None

                # Load configuration
                self._load()
                self._initialized = True

    @property
    def project_config_path(self) -> Path:
        """Project-local config path, resolved lazily against current CWD.

        Resolved each time so that callers who change directory after the
        singleton was first created still see the correct project config.
        """
        return Path.cwd() / ".navig" / "config.yaml"

    def _load(self) -> None:
        """Load configuration from disk (global + project-local)."""
        # Load global config — delegate to ConfigManager so there is only one
        # in-memory YAML copy.  Deferred import avoids import-time cycle.
        from navig.config import get_config_manager  # noqa: PLC0415
        try:
            self._global_data = get_config_manager().global_config
        except Exception:
            # Fallback for rare bootstrap edge-case (ConfigManager unavailable).
            if self.global_config_path.exists():
                try:
                    with open(self.global_config_path, encoding="utf-8") as f:
                        self._global_data = yaml.safe_load(f) or {}
                except Exception:
                    self._global_data = {}
            else:
                self._global_data = self._get_default_config()
                self._ensure_dirs()
                self._save_global()

        # Load project-local config for the current CWD snapshot.
        self._refresh_project_data(force=True)

    def _refresh_project_data(self, force: bool = False) -> None:
        """Reload project-local config when current project path changes."""
        path = self.project_config_path
        try:
            mtime_ns = path.stat().st_mtime_ns if path.exists() else None
        except OSError:
            mtime_ns = None

        if not force and path == self._project_cache_path and mtime_ns == self._project_cache_mtime_ns:
            return

        self._project_cache_path = path
        self._project_cache_mtime_ns = mtime_ns

        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    self._project_data = yaml.safe_load(f) or {}
            except Exception:
                self._project_data = {}
        else:
            self._project_data = {}

    def _get_default_config(self) -> dict[str, Any]:
        """Get default configuration values."""
        return {
            "active_host": None,
            "active_app": None,
            "default_host": None,
            "execution": {"mode": "safe", "confirmation_level": "normal"},
            "plugins": {"enabled": True, "auto_discover": True, "disabled_plugins": []},
            "debug_log": False,
            "debug_log_path": str(self.global_config_dir / "debug.log"),
            "debug_log_max_size_mb": 10,
            "debug_log_max_files": 5,
        }

    def _ensure_dirs(self) -> None:
        """Ensure required directories exist."""
        self.global_config_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.plugins_dir.mkdir(parents=True, exist_ok=True)

    def _save_global(self) -> None:
        """Save global configuration to disk (atomic write)."""
        self._ensure_dirs()
        atomic_write_yaml(self._global_data, self.global_config_path, allow_unicode=True)

    def _save_project(self) -> None:
        """Save project-local configuration to disk."""
        self.project_config_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_yaml(self._project_data, self.project_config_path, allow_unicode=True)
        try:
            self._project_cache_mtime_ns = self.project_config_path.stat().st_mtime_ns
        except OSError:
            self._project_cache_mtime_ns = None

    def _get_nested(self, data: dict[str, Any], key: str, default: Any = None) -> Any:
        """Get value using dot notation (e.g., 'plugins.brain.db_path')."""
        keys = key.split(".")
        value = data
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    return default
            else:
                return default
        return value if value is not None else default

    def _set_nested(self, data: dict[str, Any], key: str, value: Any) -> None:
        """Set value using dot notation."""
        keys = key.split(".")
        current = data
        for k in keys[:-1]:
            if k not in current or not isinstance(current[k], dict):
                current[k] = {}
            current = current[k]
        current[keys[-1]] = value

    def get(self, key: str, default: Any = None, scope: str = "merged") -> Any:
        """
        Get config value using dot notation.

        Args:
            key: Dot-notation key (e.g., 'plugins.brain.db_path')
            default: Default value if key not found
            scope: 'global', 'project', or 'merged' (project overrides global)

        Returns:
            Configuration value or default
        """
        with self._lock:
            if scope in ("project", "merged"):
                self._refresh_project_data()
            if scope == "global":
                return self._get_nested(self._global_data, key, default)
            elif scope == "project":
                return self._get_nested(self._project_data, key, default)
            else:
                # Merged: project overrides global
                project_value = self._get_nested(self._project_data, key)
                if project_value is not None:
                    return project_value
                return self._get_nested(self._global_data, key, default)

    def set(self, key: str, value: Any, scope: str = "global") -> None:
        """
        Set config value using dot notation.

        Args:
            key: Dot-notation key
            value: Value to set
            scope: 'global' or 'project'
        """
        with self._lock:
            if scope == "project":
                self._refresh_project_data()
                self._set_nested(self._project_data, key, value)
            else:
                self._set_nested(self._global_data, key, value)

    def save(self, scope: str = "global") -> None:
        """
        Persist configuration to disk.

        Args:
            scope: 'global', 'project', or 'both'
        """
        with self._lock:
            if scope in ("global", "both"):
                self._save_global()
            if scope in ("project", "both"):
                self._save_project()

    def reload(self) -> None:
        """Reload configuration from disk (discard in-memory changes)."""
        with self._lock:
            # Invalidate ConfigManager's cache so _load() re-reads from disk.
            try:
                from navig.config import get_config_manager  # noqa: PLC0415
                cm = get_config_manager()
                cm._global_config_loaded = False
            except Exception:
                pass
            self._load()

    # =========================================================================
    # Convenience Methods
    # =========================================================================

    def get_active_host(self) -> tuple[str | None, str]:
        """
        Get active host with source indicator.

        Delegates to the canonical ``ContextManager`` via ``ConfigManager``
        so that resolution logic (env → project → legacy → cache → default)
        is maintained in a single place.

        Returns:
            Tuple of (host_name, source) where source is one of:
            'env', 'project', 'cache', 'default', or None if no host found
        """
        try:
            from navig.config import get_config_manager

            cm = get_config_manager()
            result = cm.get_active_host(return_source=True)
            if isinstance(result, tuple):
                return result
            # Fallback: older ConfigManager may return bare str
            return (result, "config") if result else (None, "none")
        except Exception:  # noqa: BLE001
            # Graceful degradation: env-only fallback if ConfigManager unavailable
            env_host = os.environ.get("NAVIG_ACTIVE_HOST")
            if env_host:
                return (env_host, "env")
            return (None, "none")

    def set_active_host(self, host: str, scope: str = "cache") -> None:
        """
        Set active host.

        Args:
            host: Host name to set
            scope: 'cache' (global quick-switch) or 'project' (project-local)
        """
        with self._lock:
            if scope == "project":
                self._set_nested(self._project_data, "active_host", host)
                self._save_project()
            else:
                # Write to cache file for quick switching
                self._ensure_dirs()
                cache_file = self.cache_dir / "active_host.txt"
                cache_file.write_text(host, encoding="utf-8")

    def get_active_app(self) -> tuple[str | None, str]:
        """
        Get active app with source indicator.

        Resolution order:
        1. NAVIG_ACTIVE_APP environment variable
        2. Project-local config (.navig/config.yaml:app.name)
        3. Global cache (~/.navig/cache/active_app.txt)

        Returns:
            Tuple of (app_name, source)
        """
        try:
            from navig.config import get_config_manager

            cm = get_config_manager()
            result = cm.get_active_app(return_source=True)
            if isinstance(result, tuple):
                return result
            return (result, "config") if result else (None, "none")
        except Exception:  # noqa: BLE001
            env_app = os.environ.get("NAVIG_ACTIVE_APP")
            if env_app:
                return (env_app, "env")
            return (None, "none")

    def set_active_app(self, app_name: str, scope: str = "cache") -> None:
        """
        Set active app.

        Args:
            app_name: App name to set
            scope: 'cache' (global) or 'project' (project-local)
        """
        with self._lock:
            if scope == "project":
                self._set_nested(self._project_data, "app.name", app_name)
                self._save_project()
            else:
                self._ensure_dirs()
                cache_file = self.cache_dir / "active_app.txt"
                cache_file.write_text(app_name, encoding="utf-8")

    # =========================================================================
    # Plugin Configuration
    # =========================================================================

    def get_plugin_config(self, plugin_name: str, key: str = None, default: Any = None) -> Any:
        """
        Get plugin-specific configuration.

        Args:
            plugin_name: Plugin name (e.g., 'brain')
            key: Optional sub-key within plugin config
            default: Default value if not found

        Returns:
            Plugin configuration value
        """
        if key:
            return self.get(f"plugins.{plugin_name}.{key}", default)
        return self.get(f"plugins.{plugin_name}", default or {})

    def set_plugin_config(self, plugin_name: str, key: str, value: Any) -> None:
        """
        Set plugin-specific configuration.

        Args:
            plugin_name: Plugin name
            key: Configuration key
            value: Value to set
        """
        self.set(f"plugins.{plugin_name}.{key}", value)

    def is_plugin_disabled(self, plugin_name: str) -> bool:
        """Check if a plugin is explicitly disabled."""
        disabled = self.get("plugins.disabled_plugins", [])
        return plugin_name in disabled

    def disable_plugin(self, plugin_name: str) -> None:
        """Disable a plugin."""
        with self._lock:
            disabled = self._get_nested(self._global_data, "plugins.disabled_plugins") or []
            if plugin_name not in disabled:
                disabled.append(plugin_name)
                self._set_nested(self._global_data, "plugins.disabled_plugins", disabled)
                self._save_global()

    def enable_plugin(self, plugin_name: str) -> None:
        """Enable a previously disabled plugin."""
        with self._lock:
            disabled = self._get_nested(self._global_data, "plugins.disabled_plugins") or []
            if plugin_name in disabled:
                disabled.remove(plugin_name)
                self._set_nested(self._global_data, "plugins.disabled_plugins", disabled)
                self._save_global()

    # =========================================================================
    # Path Helpers
    # =========================================================================

    @property
    def hosts_dir(self) -> Path:
        """Get hosts configuration directory."""
        return self.global_config_dir / "hosts"

    @property
    def apps_dir(self) -> Path:
        """Get apps configuration directory."""
        return self.global_config_dir / "apps"

    @property
    def templates_dir(self) -> Path:
        """Get templates directory."""
        return self.global_config_dir / "templates"


# Singleton accessor
Config = ConfigSingleton
