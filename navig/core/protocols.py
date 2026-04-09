"""Shared protocols for navig.core dependency injection."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class ConfigProvider(Protocol):
    """Base protocol for config provider dependency injection.

    Shared by :class:`navig.core.hosts.HostManager` and
    :class:`navig.core.apps.AppManager`.
    """

    @property
    def app_config_dir(self) -> Path | None: ...

    @property
    def global_config_dir(self) -> Path: ...

    @property
    def base_dir(self) -> Path: ...

    @property
    def verbose(self) -> bool: ...

    def get_config_directories(self) -> list[Path]: ...


class HostConfigProvider(ConfigProvider, Protocol):
    """Extended protocol adding host-level directory accessibility check."""

    def _is_directory_accessible(self, directory: Path) -> bool: ...


class AppConfigProvider(ConfigProvider, Protocol):
    """Extended protocol adding host CRUD operations needed by AppManager."""

    def load_host_config(self, host_name: str, use_cache: bool = True) -> dict[str, Any]: ...

    def save_host_config(self, host_name: str, config: dict[str, Any]) -> None: ...

    def list_hosts(self) -> list[str]: ...
