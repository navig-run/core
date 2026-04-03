"""
Context management for active host/app state.

This module extracts the context-related methods from ConfigManager
for better separation of concerns.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import yaml

if TYPE_CHECKING:
    from navig.config import ConfigManager


class ContextConfigProvider(Protocol):
    """Protocol for context-related config dependencies."""

    @property
    def base_dir(self) -> Path: ...
    @property
    def active_host_file(self) -> Path: ...
    @property
    def active_app_file(self) -> Path: ...
    @property
    def global_config(self) -> dict[str, Any]: ...
    @property
    def verbose(self) -> bool: ...

    def host_exists(self, host_name: str) -> bool: ...
    def app_exists(self, host_name: str, app_name: str) -> bool: ...
    def list_apps(self, host_name: str) -> list: ...
    def load_host_config(self, host_name: str) -> dict[str, Any]: ...
    def get_local_config(self, directory: Path | None = None) -> dict[str, Any]: ...
    def set_local_config(self, config: dict[str, Any], directory: Path | None = None) -> None: ...


class ContextManager:
    """
    Manages active host/app context state.

    Handles hierarchical resolution of active host and app,
    supporting environment variables, local config, legacy formats,
    global cache, and defaults.
    """

    def __init__(self, config_provider: ContextConfigProvider):
        """
        Initialize ContextManager with a config provider.

        Args:
            config_provider: Object implementing ContextConfigProvider protocol
                           (typically ConfigManager instance)
        """
        self._config = config_provider

    # =========================================================================
    # Active Host Resolution
    # =========================================================================

    def get_active_host(self, return_source: bool = False) -> str | None | tuple[str | None, str]:
        """
        Get currently active host name with hierarchical resolution.

        Priority:
        1. NAVIG_ACTIVE_HOST environment variable (for CI/CD and scripting)
        2. .navig/config.yaml:active_host (project-local preference)
        3. .navig file (legacy format: host or host:app) - deprecated
        4. ~/.navig/cache/active_host.txt (global cache for quick switching)
        5. default_host from global config (fallback)

        Args:
            return_source: If True, returns tuple (host_name, source) where source is
                          'env', 'local', 'legacy', 'global', 'default', or 'none'

        Returns:
            Active host name or None (or tuple if return_source=True)
        """
        # Priority 1: Check NAVIG_ACTIVE_HOST environment variable
        env_host = os.environ.get("NAVIG_ACTIVE_HOST", "").strip()
        if env_host and self._config.host_exists(env_host):
            return (env_host, "env") if return_source else env_host

        # Priority 2: Check .navig/config.yaml for project-local active_host
        local_navig_dir = Path.cwd() / ".navig"
        if local_navig_dir.exists() and local_navig_dir.is_dir():
            local_config = self._config.get_local_config()
            local_host = local_config.get("active_host")
            if local_host and self._config.host_exists(local_host):
                return (local_host, "project") if return_source else local_host

        # Priority 3: Check for .navig file (legacy format) - deprecated
        local_navig = Path.cwd() / ".navig"
        if local_navig.exists() and local_navig.is_file():
            try:
                content = local_navig.read_text(encoding="utf-8").strip()
                if ":" in content:
                    host_name, _ = content.split(":", 1)
                else:
                    host_name = content

                if host_name and self._config.host_exists(host_name):
                    return (host_name, "legacy") if return_source else host_name
            except (PermissionError, OSError):
                pass  # best-effort cleanup; ignore access/IO errors

        # Priority 4: Check global cache (set by `navig host use`)
        if self._config.active_host_file.exists():
            try:
                host_name = self._config.active_host_file.read_text(encoding="utf-8").strip()
                if host_name and self._config.host_exists(host_name):
                    return (host_name, "user") if return_source else host_name
            except (PermissionError, OSError):
                pass  # best-effort cleanup; ignore access/IO errors

        # Priority 5: Fall back to default host from global config
        default_host = self._config.global_config.get("default_host")
        if default_host and self._config.host_exists(default_host):
            return (default_host, "default") if return_source else default_host

        return (None, "none") if return_source else None

    # =========================================================================
    # Active App Resolution
    # =========================================================================

    def get_active_app(self, return_source: bool = False) -> str | None | tuple[str | None, str]:
        """
        Get currently active app name with hierarchical resolution.

        Priority:
        1. NAVIG_ACTIVE_APP environment variable (per-terminal session)
        2. Local active app (.navig/config.yaml in current directory)
        3. .navig file in current directory (legacy format: host:app)
        4. Cached active app (~/.navig/cache/active_app.txt)
        5. Default app from active host config

        Args:
            return_source: If True, returns tuple (app_name, source) where source is
                          'session', 'local', 'legacy', 'global', or 'default'

        Returns:
            Active app name or None (or tuple if return_source=True)
        """
        # Priority 0: Check NAVIG_ACTIVE_APP environment variable (per-terminal session)
        env_app = os.environ.get("NAVIG_ACTIVE_APP", "").strip()
        if env_app:
            # Validate that env app exists on current host
            active_host = self.get_active_host()
            if active_host and self._config.app_exists(active_host, env_app):
                return (env_app, "session") if return_source else env_app

        # Priority 1: Check for local active app in .navig/config.yaml
        local_navig_dir = Path.cwd() / ".navig"
        if local_navig_dir.exists() and local_navig_dir.is_dir():
            local_config = self._config.get_local_config()
            local_app = local_config.get("active_app")
            if local_app:
                # Validate that local app exists on current host
                active_host = self.get_active_host()
                if active_host and self._config.app_exists(active_host, local_app):
                    return (local_app, "project") if return_source else local_app
                else:
                    # Local app invalid - show warning and fall through to user config
                    if self._config.verbose:
                        from navig import console_helper as ch

                        ch.warning(
                            f"Project active app '{local_app}' not found on host '{active_host}'",
                            "Falling back to user active app",
                        )

        # Priority 2: Check for .navig file in current directory (legacy format)
        # NOTE: .navig can be either a FILE (legacy) or DIRECTORY (new hierarchical config)
        local_navig = Path.cwd() / ".navig"
        if local_navig.exists() and local_navig.is_file():
            try:
                content = local_navig.read_text(encoding="utf-8").strip()
                if ":" in content:
                    _, app_name = content.split(":", 1)
                    return (app_name, "legacy") if return_source else app_name
            except (PermissionError, OSError):
                # Cannot read .navig file - skip it
                pass

        # Priority 3: Check cached active app (project cache or user cache)
        if self._config.active_app_file.exists():
            try:
                app_name = self._config.active_app_file.read_text(encoding="utf-8").strip()
                if app_name:
                    # Determine if this is project or user cache
                    local_navig_dir = Path.cwd() / ".navig"
                    if local_navig_dir.exists():
                        try:
                            # Use Path.relative_to() instead of is_relative_to() (Python 3.9+)
                            # to determine whether the active_app_file is under the local
                            # project .navig directory.
                            self._config.active_app_file.relative_to(local_navig_dir)
                            source = "project"
                        except ValueError:
                            source = "user"
                    else:
                        source = "user"
                    return (app_name, source) if return_source else app_name
            except (PermissionError, OSError):
                pass  # best-effort cleanup; ignore access/IO errors

        # Priority 4: Auto-detect from project's .navig/apps/ (if only one app exists)
        local_navig_dir = Path.cwd() / ".navig"
        local_apps_dir = local_navig_dir / "apps"
        if local_apps_dir.exists() and local_apps_dir.is_dir():
            host_name = self.get_active_host()
            if host_name:
                local_apps = []
                for app_file in local_apps_dir.glob("*.yaml"):
                    try:
                        with open(app_file) as f:
                            app_data = yaml.safe_load(f) or {}
                        if app_data.get("host") == host_name:
                            local_apps.append(app_file.stem)
                    except Exception:
                        continue
                if len(local_apps) == 1:
                    # Single app in project - use it as the active app
                    return (local_apps[0], "project") if return_source else local_apps[0]

        # Priority 5: Fall back to default app from active host
        host_name = self.get_active_host()
        if host_name:
            try:
                host_config = self._config.load_host_config(host_name)
                default_app = host_config.get("default_app")
                if default_app:
                    return (default_app, "default") if return_source else default_app
            except FileNotFoundError:
                pass  # file already gone; expected

        return (None, "none") if return_source else None

    # =========================================================================
    # Set Active Host
    # =========================================================================

    def set_active_host(self, host_name: str, local: bool | None = None):
        """
        Set active host.

        Args:
            host_name: Host name to set as active
            local: If True, set in local .navig/config.yaml only
                   If False, set in global cache only
                   If None (default), set in both local (if exists) and global

        Raises:
            ValueError: If host doesn't exist
        """
        if not self._config.host_exists(host_name):
            raise ValueError(f"Host '{host_name}' not found")

        # Determine if we should update local config
        local_navig_dir = Path.cwd() / ".navig"
        has_local_config = local_navig_dir.exists() and local_navig_dir.is_dir()

        # Update local .navig/config.yaml if applicable
        if has_local_config and local is not False:
            self._set_active_host_local(host_name, local_navig_dir)

        # Update global cache if applicable
        if local is not True:
            self._config.active_host_file.write_text(host_name, encoding="utf-8")

    def _set_active_host_local(self, host_name: str, local_navig_dir: Path):
        """
        Set active host in local .navig/config.yaml.

        Args:
            host_name: Host name to set as active
            local_navig_dir: Path to the local .navig/ directory
        """
        # Load existing config or create new one
        local_config = self._config.get_local_config(
            local_navig_dir.parent
        )  # Pass parent dir to get_local_config

        # Set active_host
        local_config["active_host"] = host_name

        # Save local config
        self._config.set_local_config(local_config, local_navig_dir.parent)

    # =========================================================================
    # Set Active App
    # =========================================================================

    def set_active_app(self, app_name: str, local: bool | None = None):
        """
        Set active app (global or local scope).

        Args:
            app_name: App name to set as active
            local: If True, set as local active app (current directory only)
                   If False, set as global active app only
                   If None (default), set as both local (if available) and global

        Raises:
            FileNotFoundError: If local=True and .navig/ directory doesn't exist in current directory
            ValueError: If local=True and app doesn't exist on current host

        Note: Global mode does not validate if app exists on active host
        """
        # Update local .navig config if applicable
        if local is not False:
            self.set_active_app_local(app_name)

        # Update global cache if applicable
        if local is not True:
            self._config.active_app_file.write_text(app_name, encoding="utf-8")

    def set_active_app_local(self, app_name: str, directory: Path | None = None):
        """
        Set active app for a specific directory (local scope).

        Args:
            app_name: Name of the app to set as active
            directory: Directory path (defaults to current working directory)

        Raises:
            FileNotFoundError: If .navig/ directory doesn't exist in target directory
            ValueError: If app_name doesn't exist on current host
        """
        target_dir = directory or Path.cwd()
        local_navig_dir = target_dir / ".navig"

        # Validate .navig/ directory exists
        if not local_navig_dir.exists() or not local_navig_dir.is_dir():
            raise FileNotFoundError(
                f"Cannot set local active app: No .navig/ directory found in {target_dir}\n"
                f"Run 'navig init' first or use 'navig app use {app_name}' without --local flag."
            )

        # Validate app exists on current host
        active_host = self.get_active_host()
        if not active_host:
            raise ValueError(
                "No active host set. Please select a host first with 'navig host use <name>'"
            )

        if not self._config.app_exists(active_host, app_name):
            raise ValueError(
                f"App '{app_name}' not found on host '{active_host}'\n"
                f"Available apps: {', '.join(self._config.list_apps(active_host))}"
            )

        # Load or create local config
        local_config = self._config.get_local_config(target_dir)

        if not local_config:
            # If get_local_config returned empty, create a new config
            local_config = {
                "app": {
                    "name": target_dir.name,
                    "initialized": datetime.now().isoformat(),
                    "version": "1.0",
                }
            }

        # Set active app in local config
        local_config["active_app"] = app_name
        self._config.set_local_config(local_config, target_dir)

    def clear_active_app_local(self, directory: Path | None = None):
        """
        Clear local active app setting.

        Args:
            directory: Directory path (defaults to current working directory)

        Raises:
            FileNotFoundError: If .navig/ directory doesn't exist in target directory
        """
        target_dir = directory or Path.cwd()
        local_navig_dir = target_dir / ".navig"

        if not local_navig_dir.exists() or not local_navig_dir.is_dir():
            raise FileNotFoundError(
                f"Cannot clear local active app: No .navig/ directory found in {target_dir}"
            )

        local_config_file = local_navig_dir / "config.yaml"
        if not local_config_file.exists():
            return  # Nothing to clear

        local_config = self._config.get_local_config(target_dir)
        if "active_app" in local_config:
            del local_config["active_app"]
            self._config.set_local_config(local_config, target_dir)

    # =========================================================================
    # Set Active Context (Host + App)
    # =========================================================================

    def set_active_context(self, host_name: str, app_name: str):
        """
        Set both active host and app.

        Args:
            host_name: Host name to set as active
            app_name: App name to set as active

        Raises:
            ValueError: If host doesn't exist or app doesn't exist on host
        """
        if not self._config.host_exists(host_name):
            raise ValueError(f"Host '{host_name}' not found")

        if not self._config.app_exists(host_name, app_name):
            raise ValueError(f"App '{app_name}' not found on host '{host_name}'")

        self.set_active_host(host_name)
        self.set_active_app(app_name)
