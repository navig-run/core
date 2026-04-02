"""
Host management for NAVIG configuration.

Extracted from config.py (PR2/6 of config decomposition).
Handles host configuration CRUD operations with caching.
"""

from __future__ import annotations

import logging
import os
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

    def _get_config_directories(self) -> list[Path]: ...

    def _is_directory_accessible(self, directory: Path) -> bool: ...


class HostManager:
    """
    Manages host configurations with caching.

    Supports both new format (hosts/*.yaml) and legacy format (apps/*.yaml).
    Uses a config provider for directory resolution and accessibility checks.
    """

    def __init__(self, config: ConfigProvider):
        """
        Initialize HostManager.

        Args:
            config: Provider for configuration directories and utilities.
                    Typically the ConfigManager instance.
        """
        self._config = config
        self._host_config_cache: dict[str, dict[str, Any]] = {}
        self._hosts_list_cache: tuple[list[str], tuple[float, int]] | None = None

    def invalidate_cache(self, host_name: str | None = None) -> None:
        """
        Invalidate host caches.

        Args:
            host_name: Specific host to invalidate, or None for all caches.
        """
        if host_name:
            self._host_config_cache.pop(host_name, None)
        else:
            self._host_config_cache.clear()
        self._hosts_list_cache = None

    def exists(self, host_name: str) -> bool:
        """
        Check if host configuration exists.

        Checks both new format (hosts/) and legacy format (apps/) in
        both app-specific and global directories.

        Args:
            host_name: Host name to check

        Returns:
            True if host exists, False otherwise
        """
        config_dirs = self._config._get_config_directories()

        for config_dir in config_dirs:
            try:
                # Check new format
                host_file = config_dir / "hosts" / f"{host_name}.yaml"
                if host_file.exists():
                    return True

                # Check legacy format (backward compatibility)
                legacy_file = config_dir / "apps" / f"{host_name}.yaml"
                if legacy_file.exists():
                    return True
            except (PermissionError, OSError):
                continue

        return False

    def list_hosts(self) -> list[str]:
        """
        List all configured hosts.

        Merges hosts from app-specific and global directories.
        Uses caching with directory mtime invalidation for performance.

        Returns:
            Sorted list of host names
        """
        config_dirs = self._config._get_config_directories()

        # Build signature from mtimes + file count for cache invalidation
        max_mtime = 0.0
        file_count = 0
        for config_dir in config_dirs:
            hosts_dir = config_dir / "hosts"
            apps_dir = config_dir / "apps"
            for d in [hosts_dir, apps_dir]:
                if d.exists():
                    try:
                        max_mtime = max(max_mtime, d.stat().st_mtime)
                    except (OSError, PermissionError):
                        pass
                    try:
                        for yaml_file in d.glob("*.yaml"):
                            file_count += 1
                            try:
                                max_mtime = max(max_mtime, yaml_file.stat().st_mtime)
                            except (OSError, PermissionError):
                                pass
                    except (OSError, PermissionError):
                        pass

        signature = (max_mtime, file_count)

        # Return cached result if still valid
        if self._hosts_list_cache is not None:
            cached_hosts, cached_signature = self._hosts_list_cache
            if cached_signature == signature:
                return cached_hosts.copy()

        hosts: set[str] = set()

        for config_dir in config_dirs:
            try:
                # New format hosts
                hosts_dir = config_dir / "hosts"
                if hosts_dir.exists() and self._config._is_directory_accessible(hosts_dir):
                    try:
                        for yaml_file in hosts_dir.glob("*.yaml"):
                            hosts.add(yaml_file.stem)
                    except (PermissionError, OSError) as e:
                        if self._config.verbose:
                            from navig import console_helper as ch

                            ch.warning(f"Cannot read hosts from {hosts_dir}: {e}")

                # Legacy format hosts (backward compatibility)
                apps_dir = config_dir / "apps"
                if apps_dir.exists() and self._config._is_directory_accessible(apps_dir):
                    try:
                        for yaml_file in apps_dir.glob("*.yaml"):
                            if ".backup." not in yaml_file.name:
                                try:
                                    with open(yaml_file, encoding="utf-8") as f:
                                        config_data = yaml.safe_load(f) or {}
                                    host_value = config_data.get("host", "")
                                    # Legacy host: no 'host' field OR contains dots (IP/domain)
                                    if not host_value or "." in str(host_value):
                                        hosts.add(yaml_file.stem)
                                except Exception:
                                    pass
                    except (PermissionError, OSError) as e:
                        if self._config.verbose:
                            from navig import console_helper as ch

                            ch.warning(f"Cannot read hosts from {apps_dir}: {e}")
            except (PermissionError, OSError) as e:
                if self._config.verbose:
                    from navig import console_helper as ch

                    ch.warning(f"Cannot access config directory {config_dir}: {e}")

        result = sorted(list(hosts))
        self._hosts_list_cache = (result.copy(), signature)
        return result

    def load(self, host_name: str, use_cache: bool = True) -> dict[str, Any]:
        """
        Load host configuration with hierarchical support.

        Searches in priority order:
        1. App-specific hosts/ directory
        2. Global hosts/ directory
        3. Legacy apps/ directory

        Args:
            host_name: Host name
            use_cache: Whether to use cached config (default True)

        Returns:
            Host configuration dictionary

        Raises:
            FileNotFoundError: If host configuration not found
        """
        if use_cache and host_name in self._host_config_cache:
            return self._host_config_cache[host_name]

        config_dirs = self._config._get_config_directories()

        try:
            from navig.core.config_loader import load_config
        except ImportError:
            load_config = None

        for config_dir in config_dirs:
            # Try new format (hosts/)
            host_file = config_dir / "hosts" / f"{host_name}.yaml"
            if host_file.exists():
                if load_config:
                    config = load_config(host_file, schema_type="host", strict=False)
                else:
                    with open(host_file, encoding="utf-8") as f:
                        config = yaml.safe_load(f)

                # Expand user paths
                if "ssh_key" in config and config["ssh_key"]:
                    config["ssh_key"] = os.path.expanduser(config["ssh_key"])

                if self._config.verbose:
                    from navig import console_helper as ch

                    source = "app" if config_dir == self._config.app_config_dir else "global"
                    ch.dim(f"✓ Loaded host '{host_name}' from {source} config")

                self._host_config_cache[host_name] = config
                return config

            # Try legacy format (apps/)
            legacy_file = config_dir / "apps" / f"{host_name}.yaml"
            if legacy_file.exists():
                if load_config:
                    config = load_config(legacy_file, schema_type="host", strict=False)
                else:
                    with open(legacy_file, encoding="utf-8") as f:
                        config = yaml.safe_load(f)

                if "ssh_key" in config and config["ssh_key"]:
                    config["ssh_key"] = os.path.expanduser(config["ssh_key"])

                if self._config.verbose:
                    from navig import console_helper as ch

                    source = "app" if config_dir == self._config.app_config_dir else "global"
                    ch.dim(f"✓ Loaded host '{host_name}' from {source} config (legacy format)")

                self._host_config_cache[host_name] = config
                return config

        raise FileNotFoundError(f"Host configuration not found: {host_name}")

    def save(self, host_name: str, config: dict[str, Any]) -> None:
        """
        Save host configuration.

        Saves to app-specific config if in app context,
        otherwise saves to global config.

        Args:
            host_name: Host name
            config: Host configuration dictionary
        """
        self.invalidate_cache(host_name)

        # Determine save location
        if self._config.app_config_dir:
            host_file = self._config.app_config_dir / "hosts" / f"{host_name}.yaml"
        else:
            host_file = self._config.global_config_dir / "hosts" / f"{host_name}.yaml"

        # Add timestamp to metadata
        if "metadata" not in config:
            config["metadata"] = {}
        config["metadata"]["last_updated"] = datetime.now().isoformat()

        # Ensure hosts directory exists
        host_file.parent.mkdir(parents=True, exist_ok=True)

        atomic_write_yaml(config, host_file, allow_unicode=True)

        if self._config.verbose:
            from navig import console_helper as ch

            location = "app" if self._config.app_config_dir else "global"
            ch.dim(f"✓ Saved host '{host_name}' to {location} config")

    def delete(self, host_name: str) -> bool:
        """
        Delete host configuration.

        Deletes from app-specific config if it exists there,
        otherwise deletes from global config.

        Args:
            host_name: Host name to delete

        Returns:
            True if deleted, False if not found
        """
        self.invalidate_cache(host_name)

        config_dirs = self._config._get_config_directories()

        for config_dir in config_dirs:
            # Delete from new format
            host_file = config_dir / "hosts" / f"{host_name}.yaml"
            if host_file.exists():
                host_file.unlink()
                if self._config.verbose:
                    from navig import console_helper as ch

                    location = "app" if config_dir == self._config.app_config_dir else "global"
                    ch.dim(f"✓ Deleted host '{host_name}' from {location} config")
                return True

            # Delete from legacy format
            legacy_file = config_dir / "apps" / f"{host_name}.yaml"
            if legacy_file.exists():
                legacy_file.unlink()
                if self._config.verbose:
                    from navig import console_helper as ch

                    location = "app" if config_dir == self._config.app_config_dir else "global"
                    ch.dim(f"✓ Deleted host '{host_name}' from {location} config (legacy format)")
                return True

        return False


# Backward compatibility aliases
host_exists = HostManager.exists
list_hosts = HostManager.list_hosts
load_host_config = HostManager.load
save_host_config = HostManager.save
delete_host_config = HostManager.delete
