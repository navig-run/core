"""
App management for NAVIG configuration.

Extracted from config.py (PR3/6 of config decomposition).
Handles app configuration CRUD operations.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import yaml

from navig.core.yaml_io import atomic_write_yaml

if TYPE_CHECKING:
    from navig.config import ConfigManager

logger = logging.getLogger(__name__)


class ConfigProvider(Protocol):
    """Protocol for config provider dependency injection."""

    @property
    def app_config_dir(self) -> Path | None: ...

    @property
    def global_config_dir(self) -> Path: ...

    @property
    def base_dir(self) -> Path: ...

    @property
    def verbose(self) -> bool: ...

    def get_config_directories(self) -> list[Path]: ...

    def load_host_config(self, host_name: str, use_cache: bool = True) -> dict[str, Any]: ...

    def save_host_config(self, host_name: str, config: dict[str, Any]) -> None: ...

    def list_hosts(self) -> list[str]: ...


class AppManager:
    """
    Manages app configurations.

    Supports both new format (individual files in apps/) and legacy format
    (embedded in host YAML). Uses a config provider for directory resolution
    and host operations.
    """

    def __init__(self, config: ConfigProvider):
        """
        Initialize AppManager.

        Args:
            config: Provider for configuration directories and host operations.
                    Typically the ConfigManager instance.
        """
        self._config = config

    def exists(self, host_name: str, app_name: str) -> bool:
        """
        Check if app exists on host.

        Checks both individual files (new format) and embedded format (legacy).

        Args:
            host_name: Host name
            app_name: App name

        Returns:
            True if app exists on host, False otherwise
        """
        # 1. Check individual files (new format)
        config_dirs = self._config.get_config_directories()
        for config_dir in config_dirs:
            app_config = self.load_from_file(app_name, config_dir)
            if app_config and app_config.get("host") == host_name:
                return True

        # 2. Check embedded format (legacy)
        try:
            host_config = self._config.load_host_config(host_name)
            return "apps" in host_config and app_name in host_config["apps"]
        except FileNotFoundError:
            return False

    def list_apps(self, host_name: str) -> list[str]:
        """
        List all apps on a host.

        Supports both individual files and embedded format.

        Args:
            host_name: Host name

        Returns:
            Sorted list of app names
        """
        apps: set[str] = set()

        # 1. Get apps from individual files (new format)
        config_dirs = self._config.get_config_directories()
        for config_dir in config_dirs:
            apps_dir = config_dir / "apps"
            if apps_dir.exists():
                for app_file in apps_dir.glob("*.yaml"):
                    try:
                        with open(app_file, encoding="utf-8") as f:
                            app_data = yaml.safe_load(f) or {}
                        if app_data.get("host") == host_name:
                            apps.add(app_file.stem)
                    except Exception:
                        continue

        # 2. Get apps from host YAML (legacy embedded format)
        try:
            host_config = self._config.load_host_config(host_name)
            if "apps" in host_config:
                apps.update(host_config["apps"].keys())
        except FileNotFoundError:
            pass

        return sorted(list(apps))

    def find_hosts_with_app(self, app_name: str) -> list[str]:
        """
        Find all hosts that contain a specific app.

        Args:
            app_name: App name to search for

        Returns:
            List of host names that contain the app
        """
        hosts_with_app = []

        for host_name in self._config.list_hosts():
            try:
                if self.exists(host_name, app_name):
                    hosts_with_app.append(host_name)
            except Exception:
                continue

        return hosts_with_app

    def load(self, host_name: str, app_name: str) -> dict[str, Any]:
        """
        Load app configuration.

        Priority:
        1. Individual app file (.navig/apps/<name>.yaml)
        2. Embedded in host YAML (legacy format)

        Args:
            host_name: Host name
            app_name: App name

        Returns:
            App configuration dictionary

        Raises:
            FileNotFoundError: If app not found
            ValueError: If webserver.type is missing
        """
        # 1. Try loading from individual file (new format)
        config_dirs = self._config.get_config_directories()
        for config_dir in config_dirs:
            app_config = self.load_from_file(app_name, config_dir)
            if app_config and app_config.get("host") == host_name:
                if "webserver" not in app_config or "type" not in app_config.get("webserver", {}):
                    raise ValueError(
                        f"App '{app_name}' is missing required field 'webserver.type'. "
                        f"Please edit the app configuration and add this field."
                    )
                return app_config

        # 2. Fall back to legacy format
        host_config = self._config.load_host_config(host_name)

        if "apps" in host_config:
            if app_name not in host_config["apps"]:
                raise FileNotFoundError(
                    f"App '{app_name}' not found on host '{host_name}'. "
                    f"Available apps: {', '.join(host_config['apps'].keys())}"
                )

            app_config = host_config["apps"][app_name]

            if "webserver" not in app_config or "type" not in app_config.get("webserver", {}):
                raise ValueError(
                    f"Missing 'webserver.type' in configuration for app '{app_name}' on host '{host_name}'. "
                    f"Please add 'webserver.type: nginx' or 'webserver.type: apache2' to your app config."
                )

            return app_config
        else:
            # Legacy format: host config IS the app config
            return host_config

    def save(
        self,
        host_name: str,
        app_name: str,
        app_config: dict[str, Any],
        use_individual_file: bool = True,
    ) -> None:
        """
        Save app configuration.

        Args:
            host_name: Host name
            app_name: App name
            app_config: App configuration dictionary
            use_individual_file: If True, save to individual file; if False, use legacy embedded format
        """
        if use_individual_file:
            app_config["host"] = host_name
            app_config["name"] = app_name

            navig_dir = self._get_default_navig_dir()
            self.save_to_file(app_name, app_config, navig_dir)
        else:
            host_config = self._config.load_host_config(host_name)

            if "apps" not in host_config:
                host_config["apps"] = {}

            host_config["apps"][app_name] = app_config
            self._config.save_host_config(host_name, host_config)

    def delete(self, host_name: str, app_name: str) -> bool:
        """
        Delete app configuration.

        Tries individual file first, then legacy embedded format.

        Args:
            host_name: Host name
            app_name: App name

        Returns:
            True if deleted, False if not found
        """
        config_dirs = self._config.get_config_directories()

        # Try individual file first
        for config_dir in config_dirs:
            app_file = config_dir / "apps" / f"{app_name}.yaml"
            if app_file.exists():
                try:
                    with open(app_file, encoding="utf-8") as f:
                        app_data = yaml.safe_load(f) or {}
                    if app_data.get("host") == host_name:
                        app_file.unlink()
                        if self._config.verbose:
                            from navig import console_helper as ch

                            location = "app" if config_dir == self._config.app_config_dir else "global"
                            ch.dim(f"✓ Deleted app '{app_name}' from {location} config (individual file)")
                        return True
                except Exception:
                    pass

        # Fall back to legacy format
        try:
            host_config = self._config.load_host_config(host_name)
            if "apps" in host_config and app_name in host_config["apps"]:
                del host_config["apps"][app_name]
                if not host_config["apps"]:
                    del host_config["apps"]
                self._config.save_host_config(host_name, host_config)
                if self._config.verbose:
                    from navig import console_helper as ch

                    ch.dim(f"✓ Deleted app '{app_name}' from host '{host_name}' (legacy format)")
                return True
        except FileNotFoundError:
            pass

        return False

    # ================================================================
    # Individual App File Support
    # ================================================================

    def _get_default_navig_dir(self) -> Path:
        """
        Return the default NAVIG directory to use for app configuration files.

        Prefers the explicit ``app_config_dir`` if set, otherwise falls back to ``base_dir``.
        """
        return self._config.app_config_dir if self._config.app_config_dir else self._config.base_dir

    def get_file_path(self, app_name: str, navig_dir: Path | None = None) -> Path:
        """
        Get path to individual app file.

        Args:
            app_name: App name
            navig_dir: Optional .navig directory path

        Returns:
            Path to app file
        """
        if navig_dir is None:
            navig_dir = self._get_default_navig_dir()

        return navig_dir / "apps" / f"{app_name}.yaml"

    def load_from_file(self, app_name: str, navig_dir: Path | None = None) -> dict[str, Any] | None:
        """
        Load app configuration from individual file.

        Args:
            app_name: App name
            navig_dir: Optional .navig directory path

        Returns:
            App configuration dictionary or None if file doesn't exist
        """
        app_file = self.get_file_path(app_name, navig_dir)

        if not app_file.exists():
            return None

        try:
            with open(app_file, encoding="utf-8") as f:
                app_config = yaml.safe_load(f) or {}

            if "name" not in app_config:
                raise ValueError(f"App file missing required field 'name': {app_file}")
            if "host" not in app_config:
                raise ValueError(f"App file missing required field 'host': {app_file}")

            if app_config["name"] != app_name:
                raise ValueError(
                    f"App name mismatch: filename is '{app_name}.yaml' but name field is '{app_config['name']}'"
                )

            return app_config
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in app file {app_file}: {e}") from e

    def save_to_file(
        self,
        app_name: str,
        app_config: dict[str, Any],
        navig_dir: Path | None = None,
    ) -> None:
        """
        Save app configuration to individual file.

        Args:
            app_name: App name
            app_config: App configuration dictionary
            navig_dir: Optional .navig directory path
        """
        if "name" not in app_config:
            app_config["name"] = app_name
        if "host" not in app_config:
            raise ValueError("App configuration must include 'host' field")

        if app_config["name"] != app_name:
            raise ValueError(
                f"App name mismatch: parameter is '{app_name}' but config['name'] is '{app_config['name']}'"
            )

        app_file = self.get_file_path(app_name, navig_dir)
        app_file.parent.mkdir(parents=True, exist_ok=True)

        if "metadata" not in app_config:
            app_config["metadata"] = {}
        if "created" not in app_config["metadata"]:
            app_config["metadata"]["created"] = datetime.now().isoformat()
        app_config["metadata"]["updated"] = datetime.now().isoformat()

        atomic_write_yaml(app_config, app_file)

        if self._config.verbose:
            from navig import console_helper as ch

            location = "app" if navig_dir == self._config.app_config_dir else "global"
            ch.dim(f"✓ Saved app '{app_name}' to {location} config (individual file)")

    def list_from_files(self, navig_dir: Path | None = None) -> list[str]:
        """
        List all apps from individual files in apps/ directory.

        Args:
            navig_dir: Optional .navig directory path

        Returns:
            List of app names
        """
        if navig_dir is None:
            navig_dir = self._get_default_navig_dir()

        apps_dir = navig_dir / "apps"

        if not apps_dir.exists():
            return []

        apps = []
        for app_file in apps_dir.glob("*.yaml"):
            app_name = app_file.stem
            try:
                with open(app_file, encoding="utf-8") as f:
                    app_data = yaml.safe_load(f) or {}
                if "host" in app_data:
                    apps.append(app_name)
            except Exception:
                continue

        return sorted(apps)

    def migrate_from_host(
        self,
        host_name: str,
        navig_dir: Path | None = None,
        remove_from_host: bool = True,
    ) -> dict[str, Any]:
        """
        Migrate apps from host YAML (legacy) to individual files (new format).

        Args:
            host_name: Host name
            navig_dir: Optional .navig directory path
            remove_from_host: If True, remove apps from host YAML after migration

        Returns:
            Migration results dictionary
        """
        if navig_dir is None:
            navig_dir = self._get_default_navig_dir()

        results: dict[str, Any] = {"migrated": [], "skipped": [], "errors": {}}

        try:
            host_config = self._config.load_host_config(host_name)

            if "apps" not in host_config or not host_config["apps"]:
                return results

            for app_name, app_config in host_config["apps"].items():
                try:
                    app_file = self.get_file_path(app_name, navig_dir)
                    if app_file.exists():
                        results["skipped"].append(app_name)
                        continue

                    app_config["name"] = app_name
                    app_config["host"] = host_name

                    self.save_to_file(app_name, app_config, navig_dir)
                    results["migrated"].append(app_name)

                except Exception as e:
                    results["errors"][app_name] = str(e)

            if remove_from_host and results["migrated"]:
                host_config["apps"] = {
                    name: config
                    for name, config in host_config["apps"].items()
                    if name not in results["migrated"]
                }

                if not host_config["apps"]:
                    del host_config["apps"]

                self._config.save_host_config(host_name, host_config)

        except Exception as e:
            results["errors"]["_migration"] = str(e)

        return results


# Backward compatibility aliases
app_exists = AppManager.exists
list_apps = AppManager.list_apps
find_hosts_with_app = AppManager.find_hosts_with_app
load_app_config = AppManager.load
save_app_config = AppManager.save
delete_app_config = AppManager.delete
