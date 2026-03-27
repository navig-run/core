"""
NAVIG Plugin System

Plugin discovery, loading, and management for extending NAVIG functionality.

Plugin Types:
- Built-in plugins: navig/plugins/<plugin_name>/
- User plugins: ~/.navig/plugins/<plugin_name>/
- Project plugins: .navig/plugins/<plugin_name>/

Each plugin must contain:
- plugin.py with 'name', 'app', and 'check_dependencies()' exports
- Optional: requirements.txt with dependencies
- Optional: plugin.yaml with metadata
"""

import ast
import sys
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple  # noqa: F401

from rich.console import Console

console = Console()


@dataclass
class PluginInfo:
    """Information about a discovered plugin."""

    name: str
    path: Path
    source: str  # 'builtin', 'user', 'project'
    loaded: bool = False
    enabled: bool = True
    error: str | None = None
    version: str = "1.0.0"
    description: str = ""
    dependencies: list[str] = field(default_factory=list)
    missing_deps: list[str] = field(default_factory=list)


class PluginManager:
    """
    Discovers and loads NAVIG plugins.

    Plugin Discovery Order:
    1. Built-in plugins (navig/plugins/)
    2. User plugins (~/.navig/plugins/)
    3. Project plugins (.navig/plugins/)

    Later plugins can override earlier ones (same name).
    """

    def __init__(self):
        from navig.core import Config

        self.config = Config()

        # Plugin directories in order of priority (later overrides earlier)
        self.plugin_dirs = [
            Path(__file__).parent,  # Built-in: navig/plugins/
            self.config.plugins_dir,  # User: ~/.navig/plugins/
            Path.cwd() / ".navig" / "plugins",  # Project: .navig/plugins/
        ]

        self._plugins: dict[str, PluginInfo] = {}
        self._loaded_apps: dict[str, Any] = {}  # name -> typer.Typer

    def discover_plugins(self) -> dict[str, PluginInfo]:
        """
        Scan all plugin directories and discover available plugins.

        Returns:
            Dict mapping plugin names to PluginInfo objects
        """
        self._plugins = {}

        cache_file = self.config.cache_dir / "plugins_cache.json"
        current_mtime = 0
        try:
            current_mtime = max(p.stat().st_mtime for p in self.plugin_dirs if p.exists())
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        if cache_file.exists():
            try:
                import json

                with open(cache_file, encoding="utf-8") as f:
                    cached_data = json.load(f)
                if cached_data.get("mtime") == current_mtime:
                    for name, data in cached_data.get("plugins", {}).items():
                        info = PluginInfo(
                            name=data["name"],
                            path=Path(data["path"]),
                            source=data["source"],
                        )
                        info.version = data.get("version", "1.0.0")
                        info.description = data.get("description", "")
                        info.dependencies = data.get("dependencies", [])
                        info.enabled = data.get("enabled", True)
                        if self.config.is_plugin_disabled(
                            info.name
                        ) or self.config.is_plugin_disabled(info.path.name):
                            info.enabled = False
                        self._plugins[name] = info
                    return self._plugins
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        sources = ["builtin", "user", "project"]

        for plugin_dir, source in zip(self.plugin_dirs, sources):
            if not plugin_dir.exists():
                continue

            for plugin_path in plugin_dir.iterdir():
                if not plugin_path.is_dir():
                    continue
                if plugin_path.name.startswith("_"):
                    continue
                if plugin_path.name == "__pycache__":
                    continue

                plugin_file = plugin_path / "plugin.py"
                if not plugin_file.exists():
                    continue

                # Get plugin metadata
                info = self._get_plugin_info(plugin_path, source)

                # Check if disabled
                if self.config.is_plugin_disabled(info.name) or self.config.is_plugin_disabled(
                    plugin_path.name
                ):
                    info.enabled = False

                # Later sources override earlier (same name)
                self._plugins[info.name] = info

        # Save cache
        try:
            import json

            cache_data = {"mtime": current_mtime, "plugins": {}}
            for name, info in self._plugins.items():
                cache_data["plugins"][name] = {
                    "name": info.name,
                    "path": str(info.path),
                    "source": info.source,
                    "version": info.version,
                    "description": info.description,
                    "dependencies": info.dependencies,
                    "enabled": info.enabled,
                }

            cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(cache_data, f)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        return self._plugins

    def _get_plugin_info(self, plugin_path: Path, source: str) -> PluginInfo:
        """Extract plugin information from plugin directory."""
        info = PluginInfo(
            name=plugin_path.name,
            path=plugin_path,
            source=source,
        )

        # Try to read plugin.yaml for metadata
        metadata_file = plugin_path / "plugin.yaml"
        if metadata_file.exists():
            try:
                import yaml

                with open(metadata_file, encoding="utf-8") as f:
                    metadata = yaml.safe_load(f) or {}
                info.version = metadata.get("version", "1.0.0")
                info.description = metadata.get("description", "")
                info.dependencies = metadata.get("dependencies", [])
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        source_metadata = self._extract_plugin_source_metadata(plugin_path / "plugin.py")
        if source_metadata.get("name"):
            info.name = source_metadata["name"]
        if source_metadata.get("version"):
            info.version = source_metadata["version"]
        if source_metadata.get("description"):
            info.description = source_metadata["description"]

        # Try to read requirements.txt
        requirements_file = plugin_path / "requirements.txt"
        if requirements_file.exists():
            try:
                deps = requirements_file.read_text().strip().split("\n")
                info.dependencies = [d.strip() for d in deps if d.strip() and not d.startswith("#")]
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        return info

    def _extract_plugin_source_metadata(self, plugin_file: Path) -> dict[str, str]:
        """Read simple string metadata from ``plugin.py`` without importing it."""

        metadata: dict[str, str] = {}

        try:
            tree = ast.parse(plugin_file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return metadata

        for node in tree.body:
            target_name: str | None = None
            value_node = None

            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                target_name = node.targets[0].id
                value_node = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                target_name = node.target.id
                value_node = node.value

            if target_name not in {"name", "version", "description"} or value_node is None:
                continue

            try:
                value = ast.literal_eval(value_node)
            except Exception:  # noqa: BLE001
                continue

            if isinstance(value, str) and value.strip():
                metadata[target_name] = value.strip()

        return metadata

    def load_plugin(self, name: str) -> tuple[bool, str | None]:
        """
        Load a single plugin.

        Args:
            name: Plugin name

        Returns:
            Tuple of (success, error_message)
        """
        if name not in self._plugins:
            return (False, f"Plugin '{name}' not found")

        info = self._plugins[name]

        if not info.enabled:
            return (False, f"Plugin '{name}' is disabled")

        if info.loaded:
            return (True, None)

        try:
            # Determine module path based on source
            package_name = info.path.name
            if info.source == "builtin":
                module_name = f"navig.plugins.{package_name}.plugin"
            else:
                # For user/project plugins, we need to add to sys.path
                plugin_parent = info.path.parent
                if str(plugin_parent) not in sys.path:
                    sys.path.insert(0, str(plugin_parent))
                module_name = f"{package_name}.plugin"

            # Import plugin module
            plugin_module = import_module(module_name)

            # Check dependencies
            if hasattr(plugin_module, "check_dependencies"):
                deps_ok, missing_deps = plugin_module.check_dependencies()
                if not deps_ok:
                    info.missing_deps = missing_deps
                    info.error = f"Missing dependencies: {', '.join(missing_deps)}"
                    return (False, info.error)

            # Get plugin app and name
            if not hasattr(plugin_module, "app"):
                info.error = "Missing 'app' attribute in plugin.py"
                return (False, info.error)

            if not hasattr(plugin_module, "name"):
                info.error = "Missing 'name' attribute in plugin.py"
                return (False, info.error)

            # Store loaded app
            info.name = str(plugin_module.name)
            self._loaded_apps[info.name] = plugin_module.app
            info.loaded = True

            # Update info from module if available
            if hasattr(plugin_module, "description"):
                info.description = plugin_module.description
            if hasattr(plugin_module, "version"):
                info.version = plugin_module.version

            return (True, None)

        except ImportError as e:
            info.error = f"Import error: {e}"
            return (False, info.error)
        except Exception as e:
            info.error = f"Load error: {e}"
            return (False, info.error)

    def load_all_plugins(self, silent: bool = False) -> tuple[list[str], list[dict[str, str]]]:
        """
        Load all discovered plugins.

        Args:
            silent: If True, don't print warnings for failed plugins

        Returns:
            Tuple of (loaded_names, failed_info)
            where failed_info is list of dicts with 'name' and 'reason'
        """
        loaded = []
        failed = []

        for name, info in self._plugins.items():
            if not info.enabled:
                continue

            success, error = self.load_plugin(name)

            if success:
                loaded.append(info.name)
            else:
                failed.append({"name": info.name, "reason": error or "Unknown error"})

        # Log failures
        if failed and not silent:
            console.print("[yellow]⚠ Some plugins failed to load:[/yellow]", file=sys.stderr)
            for plugin in failed:
                console.print(f"  • {plugin['name']}: {plugin['reason']}", file=sys.stderr)

        return (loaded, failed)

    def get_loaded_apps(self) -> dict[str, Any]:
        """Get all loaded plugin Typer apps."""
        return self._loaded_apps

    def get_plugin_info(self, name: str) -> PluginInfo | None:
        """Get info about a specific plugin."""
        info = self._plugins.get(name)
        if info is not None:
            return info

        for plugin in self._plugins.values():
            if plugin.name == name or plugin.path.name == name:
                return plugin

        return None

    def list_plugins(self) -> dict[str, PluginInfo]:
        """Get all discovered plugins."""
        return self._plugins


# Singleton instance
_plugin_manager: PluginManager | None = None


def get_plugin_manager() -> PluginManager:
    """Get the singleton PluginManager instance."""
    global _plugin_manager
    if _plugin_manager is None:
        _plugin_manager = PluginManager()
    return _plugin_manager
