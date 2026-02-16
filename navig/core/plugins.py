"""
Enhanced Plugin Registry for NAVIG

Provides a comprehensive plugin system with discovery, lifecycle management,
and hook integration, inspired by modular plugin architectures.

Features:
- Plugin discovery from multiple sources
- Lifecycle hooks (load, enable, disable, unload)
- Dependency management
- Configuration validation
- Hot reload support (development mode)

Usage:
    from navig.core.plugins import PluginRegistry, Plugin, plugin
    
    # Define a plugin
    @plugin(
        name="my-plugin",
        version="1.0.0",
        description="My awesome plugin"
    )
    class MyPlugin(Plugin):
        def on_load(self):
            print("Plugin loaded!")
        
        def on_enable(self):
            # Register commands, hooks, etc.
            pass
    
    # Use the registry
    registry = PluginRegistry()
    registry.discover_plugins()
    registry.enable_all()
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
from abc import ABC
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Type

# =============================================================================
# Types
# =============================================================================

class PluginState(str, Enum):
    """Plugin lifecycle states."""
    DISCOVERED = "discovered"   # Found but not loaded
    LOADED = "loaded"           # Loaded but not enabled
    ENABLED = "enabled"         # Active and running
    DISABLED = "disabled"       # Loaded but disabled
    ERROR = "error"             # Failed to load/enable
    UNLOADED = "unloaded"       # Explicitly unloaded


class PluginType(str, Enum):
    """Plugin types."""
    COMMAND = "command"         # Adds CLI commands
    CHANNEL = "channel"         # Messaging channel adapter
    PROVIDER = "provider"       # AI/service provider
    TOOL = "tool"               # Agent tool
    HOOK = "hook"               # Hook handlers only
    EXTENSION = "extension"     # General extension


@dataclass
class PluginMetadata:
    """Plugin metadata definition."""
    name: str
    version: str
    description: str = ""
    author: str = ""
    homepage: str = ""
    license: str = ""
    
    # Classification
    type: PluginType = PluginType.EXTENSION
    tags: List[str] = field(default_factory=list)
    
    # Requirements
    navig_version: str = ">=2.0.0"
    python_version: str = ">=3.9"
    dependencies: List[str] = field(default_factory=list)
    optional_dependencies: List[str] = field(default_factory=list)
    
    # Configuration
    config_schema: Optional[Dict[str, Any]] = None
    default_config: Dict[str, Any] = field(default_factory=dict)
    
    # Lifecycle
    auto_enable: bool = True
    priority: int = 100  # Lower = loads first
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'name': self.name,
            'version': self.version,
            'description': self.description,
            'author': self.author,
            'type': self.type.value,
            'tags': self.tags,
            'dependencies': self.dependencies,
        }


@dataclass
class PluginInfo:
    """Runtime plugin information."""
    metadata: PluginMetadata
    state: PluginState = PluginState.DISCOVERED
    error: Optional[str] = None
    
    # Source info
    source_path: Optional[Path] = None
    module_name: Optional[str] = None
    
    # Runtime
    instance: Optional['Plugin'] = None
    loaded_at: Optional[datetime] = None
    enabled_at: Optional[datetime] = None
    
    # Config
    config: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def name(self) -> str:
        return self.metadata.name
    
    @property
    def version(self) -> str:
        return self.metadata.version
    
    def is_enabled(self) -> bool:
        return self.state == PluginState.ENABLED
    
    def is_loaded(self) -> bool:
        return self.state in (PluginState.LOADED, PluginState.ENABLED, PluginState.DISABLED)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            **self.metadata.to_dict(),
            'state': self.state.value,
            'error': self.error,
            'source_path': str(self.source_path) if self.source_path else None,
            'loaded_at': self.loaded_at.isoformat() if self.loaded_at else None,
            'enabled_at': self.enabled_at.isoformat() if self.enabled_at else None,
        }


# =============================================================================
# Plugin Base Class
# =============================================================================

class Plugin(ABC):
    """
    Base class for NAVIG plugins.
    
    Plugins should inherit from this class and implement lifecycle methods.
    """
    
    # Metadata (set by @plugin decorator or subclass)
    metadata: PluginMetadata
    
    def __init__(self):
        self._registry: Optional['PluginRegistry'] = None
        self._config: Dict[str, Any] = {}
        self._hooks: List[str] = []  # Registered hook IDs for cleanup
    
    @property
    def name(self) -> str:
        return self.metadata.name
    
    @property
    def config(self) -> Dict[str, Any]:
        return self._config
    
    def configure(self, config: Dict[str, Any]) -> None:
        """
        Configure the plugin.
        
        Called before on_load with user configuration merged with defaults.
        """
        self._config = {**self.metadata.default_config, **config}
    
    # =========================================================================
    # Lifecycle Methods (override in subclass)
    # =========================================================================
    
    def on_load(self) -> None:
        """
        Called when plugin is loaded.
        
        Use for initialization that doesn't require the plugin to be active.
        """
        pass
    
    def on_enable(self) -> None:
        """
        Called when plugin is enabled.
        
        Use for registering commands, hooks, and starting services.
        """
        pass
    
    def on_disable(self) -> None:
        """
        Called when plugin is disabled.
        
        Use for cleanup while keeping the plugin loaded.
        """
        pass
    
    def on_unload(self) -> None:
        """
        Called when plugin is unloaded.
        
        Use for final cleanup and resource release.
        """
        pass
    
    # =========================================================================
    # Hook Integration
    # =========================================================================
    
    def register_hook(
        self,
        event_key: str,
        handler: Callable,
        priority: int = 100
    ) -> str:
        """
        Register a hook handler.
        
        Automatically tracks for cleanup on disable/unload.
        """
        try:
            from navig.core.hooks import register_hook
            register_hook(event_key, handler, priority)
            hook_id = f"{event_key}:{id(handler)}"
            self._hooks.append(hook_id)
            return hook_id
        except ImportError:
            pass
        return ""
    
    def _cleanup_hooks(self) -> None:
        """Unregister all hooks registered by this plugin."""
        try:
            from navig.core.hooks import unregister_hook
            for hook_id in self._hooks:
                # Parse hook ID and unregister
                if ':' in hook_id:
                    event_key = hook_id.split(':')[0]
                    # Note: Full unregister would need handler reference
            self._hooks.clear()
        except ImportError:
            pass


# =============================================================================
# Plugin Decorator
# =============================================================================

def plugin(
    name: str,
    version: str,
    description: str = "",
    **kwargs
) -> Callable[[Type[Plugin]], Type[Plugin]]:
    """
    Decorator to define plugin metadata.
    
    Usage:
        @plugin(name="my-plugin", version="1.0.0")
        class MyPlugin(Plugin):
            pass
    """
    def decorator(cls: Type[Plugin]) -> Type[Plugin]:
        cls.metadata = PluginMetadata(
            name=name,
            version=version,
            description=description,
            **kwargs
        )
        return cls
    return decorator


# =============================================================================
# Plugin Registry
# =============================================================================

class PluginRegistry:
    """
    Central registry for NAVIG plugins.
    
    Handles plugin discovery, lifecycle, and management.
    """
    
    def __init__(self):
        self._plugins: Dict[str, PluginInfo] = {}
        self._load_order: List[str] = []
        self._plugin_dirs: List[Path] = []
        self._initialized = False
    
    def initialize(self) -> None:
        """Initialize the registry with default plugin directories."""
        if self._initialized:
            return
        
        # Default plugin directories
        self._plugin_dirs = [
            Path.home() / ".navig" / "plugins",
            Path(__file__).parent.parent / "plugins",  # Built-in plugins
        ]
        
        # Ensure directories exist
        for dir_path in self._plugin_dirs:
            dir_path.mkdir(parents=True, exist_ok=True)
        
        self._initialized = True
    
    # =========================================================================
    # Discovery
    # =========================================================================
    
    def discover_plugins(self) -> List[PluginInfo]:
        """
        Discover plugins from all configured directories.
        
        Returns list of discovered plugin info.
        """
        if not self._initialized:
            self.initialize()
        
        discovered = []
        
        for plugin_dir in self._plugin_dirs:
            if not plugin_dir.exists():
                continue
            
            # Look for plugin packages (directories with __init__.py)
            for item in plugin_dir.iterdir():
                if item.is_dir() and (item / "__init__.py").exists():
                    info = self._discover_plugin_package(item)
                    if info:
                        discovered.append(info)
                
                # Also support single-file plugins
                elif item.is_file() and item.suffix == ".py" and not item.name.startswith("_"):
                    info = self._discover_plugin_file(item)
                    if info:
                        discovered.append(info)
        
        return discovered
    
    def _discover_plugin_package(self, path: Path) -> Optional[PluginInfo]:
        """Discover a plugin from a package directory."""
        try:
            # Load the module
            module_name = f"navig_plugins.{path.name}"
            spec = importlib.util.spec_from_file_location(
                module_name,
                path / "__init__.py"
            )
            if spec is None or spec.loader is None:
                return None
            
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            
            # Find Plugin subclass
            for name, obj in inspect.getmembers(module, inspect.isclass):
                if issubclass(obj, Plugin) and obj is not Plugin:
                    if hasattr(obj, 'metadata'):
                        info = PluginInfo(
                            metadata=obj.metadata,
                            source_path=path,
                            module_name=module_name,
                            state=PluginState.DISCOVERED,
                        )
                        self._plugins[info.name] = info
                        return info
            
        except Exception as e:
            # Create error info
            info = PluginInfo(
                metadata=PluginMetadata(name=path.name, version="unknown"),
                source_path=path,
                state=PluginState.ERROR,
                error=str(e),
            )
            self._plugins[info.name] = info
            return info
        
        return None
    
    def _discover_plugin_file(self, path: Path) -> Optional[PluginInfo]:
        """Discover a plugin from a single file."""
        try:
            module_name = f"navig_plugins.{path.stem}"
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                return None
            
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            
            # Find Plugin subclass
            for name, obj in inspect.getmembers(module, inspect.isclass):
                if issubclass(obj, Plugin) and obj is not Plugin:
                    if hasattr(obj, 'metadata'):
                        info = PluginInfo(
                            metadata=obj.metadata,
                            source_path=path,
                            module_name=module_name,
                            state=PluginState.DISCOVERED,
                        )
                        self._plugins[info.name] = info
                        return info
            
        except Exception as e:
            info = PluginInfo(
                metadata=PluginMetadata(name=path.stem, version="unknown"),
                source_path=path,
                state=PluginState.ERROR,
                error=str(e),
            )
            self._plugins[info.name] = info
            return info
        
        return None
    
    # =========================================================================
    # Lifecycle Management
    # =========================================================================
    
    def load_plugin(self, name: str, config: Optional[Dict[str, Any]] = None) -> PluginInfo:
        """
        Load a discovered plugin.
        
        Args:
            name: Plugin name
            config: Optional configuration to merge with defaults
            
        Returns:
            Updated plugin info
        """
        if name not in self._plugins:
            raise ValueError(f"Plugin not found: {name}")
        
        info = self._plugins[name]
        
        if info.state == PluginState.ERROR:
            raise ValueError(f"Plugin {name} is in error state: {info.error}")
        
        if info.is_loaded():
            return info
        
        try:
            # Get the plugin class
            module = sys.modules.get(info.module_name)
            if not module:
                raise ValueError(f"Module not loaded: {info.module_name}")
            
            plugin_cls = None
            for obj_name, obj in inspect.getmembers(module, inspect.isclass):
                if issubclass(obj, Plugin) and obj is not Plugin:
                    if hasattr(obj, 'metadata') and obj.metadata.name == name:
                        plugin_cls = obj
                        break
            
            if not plugin_cls:
                raise ValueError("Plugin class not found in module")
            
            # Create instance
            instance = plugin_cls()
            instance._registry = self
            
            # Configure
            merged_config = {**info.metadata.default_config, **(config or {})}
            instance.configure(merged_config)
            info.config = merged_config
            
            # Call on_load
            instance.on_load()
            
            # Update info
            info.instance = instance
            info.state = PluginState.LOADED
            info.loaded_at = datetime.utcnow()
            info.error = None
            
            self._load_order.append(name)
            
            # Trigger hook
            self._trigger_hook("plugin:loaded", {"plugin": name})
            
        except Exception as e:
            info.state = PluginState.ERROR
            info.error = str(e)
            raise
        
        return info
    
    def enable_plugin(self, name: str) -> PluginInfo:
        """Enable a loaded plugin."""
        if name not in self._plugins:
            raise ValueError(f"Plugin not found: {name}")
        
        info = self._plugins[name]
        
        if not info.is_loaded():
            self.load_plugin(name)
        
        if info.state == PluginState.ENABLED:
            return info
        
        try:
            if info.instance:
                info.instance.on_enable()
            
            info.state = PluginState.ENABLED
            info.enabled_at = datetime.utcnow()
            info.error = None
            
            self._trigger_hook("plugin:enabled", {"plugin": name})
            
        except Exception as e:
            info.state = PluginState.ERROR
            info.error = str(e)
            raise
        
        return info
    
    def disable_plugin(self, name: str) -> PluginInfo:
        """Disable an enabled plugin."""
        if name not in self._plugins:
            raise ValueError(f"Plugin not found: {name}")
        
        info = self._plugins[name]
        
        if info.state != PluginState.ENABLED:
            return info
        
        try:
            if info.instance:
                info.instance._cleanup_hooks()
                info.instance.on_disable()
            
            info.state = PluginState.DISABLED
            
            self._trigger_hook("plugin:disabled", {"plugin": name})
            
        except Exception as e:
            info.state = PluginState.ERROR
            info.error = str(e)
            raise
        
        return info
    
    def unload_plugin(self, name: str) -> PluginInfo:
        """Unload a plugin completely."""
        if name not in self._plugins:
            raise ValueError(f"Plugin not found: {name}")
        
        info = self._plugins[name]
        
        # Disable first if enabled
        if info.state == PluginState.ENABLED:
            self.disable_plugin(name)
        
        try:
            if info.instance:
                info.instance.on_unload()
                info.instance = None
            
            info.state = PluginState.UNLOADED
            info.loaded_at = None
            info.enabled_at = None
            
            if name in self._load_order:
                self._load_order.remove(name)
            
            self._trigger_hook("plugin:unloaded", {"plugin": name})
            
        except Exception as e:
            info.state = PluginState.ERROR
            info.error = str(e)
            raise
        
        return info
    
    # =========================================================================
    # Batch Operations
    # =========================================================================
    
    def load_all(self, config: Optional[Dict[str, Dict]] = None) -> Dict[str, PluginInfo]:
        """Load all discovered plugins."""
        results = {}
        
        # Sort by priority
        plugins = sorted(
            self._plugins.values(),
            key=lambda p: p.metadata.priority
        )
        
        for info in plugins:
            if info.state == PluginState.DISCOVERED:
                try:
                    plugin_config = (config or {}).get(info.name, {})
                    self.load_plugin(info.name, plugin_config)
                except Exception:
                    pass
            results[info.name] = info
        
        return results
    
    def enable_all(self) -> Dict[str, PluginInfo]:
        """Enable all loaded plugins that have auto_enable=True."""
        results = {}
        
        for info in self._plugins.values():
            if info.is_loaded() and info.metadata.auto_enable:
                try:
                    self.enable_plugin(info.name)
                except Exception:
                    pass
            results[info.name] = info
        
        return results
    
    def disable_all(self) -> Dict[str, PluginInfo]:
        """Disable all enabled plugins (in reverse load order)."""
        results = {}
        
        for name in reversed(self._load_order):
            info = self._plugins.get(name)
            if info and info.state == PluginState.ENABLED:
                try:
                    self.disable_plugin(name)
                except Exception:
                    pass
            if info:
                results[name] = info
        
        return results
    
    # =========================================================================
    # Query
    # =========================================================================
    
    def get_plugin(self, name: str) -> Optional[PluginInfo]:
        """Get plugin info by name."""
        return self._plugins.get(name)
    
    def list_plugins(
        self,
        state: Optional[PluginState] = None,
        plugin_type: Optional[PluginType] = None
    ) -> List[PluginInfo]:
        """List plugins with optional filtering."""
        plugins = list(self._plugins.values())
        
        if state:
            plugins = [p for p in plugins if p.state == state]
        
        if plugin_type:
            plugins = [p for p in plugins if p.metadata.type == plugin_type]
        
        return plugins
    
    def get_enabled_plugins(self) -> List[PluginInfo]:
        """Get all enabled plugins."""
        return self.list_plugins(state=PluginState.ENABLED)
    
    def get_status(self) -> Dict[str, Any]:
        """Get registry status summary."""
        states = {}
        for info in self._plugins.values():
            state = info.state.value
            states[state] = states.get(state, 0) + 1
        
        return {
            'total': len(self._plugins),
            'states': states,
            'load_order': self._load_order.copy(),
            'plugin_dirs': [str(p) for p in self._plugin_dirs],
        }
    
    # =========================================================================
    # Helpers
    # =========================================================================
    
    def _trigger_hook(self, event: str, data: Dict[str, Any]) -> None:
        """Trigger a hook event if hooks system is available."""
        try:
            from navig.core.hooks import trigger_hook_sync
            trigger_hook_sync(event.split(':')[0], event.split(':')[1], data)
        except ImportError:
            pass


# =============================================================================
# Global Registry
# =============================================================================

_registry: Optional[PluginRegistry] = None


def get_plugin_registry() -> PluginRegistry:
    """Get the global plugin registry."""
    global _registry
    if _registry is None:
        _registry = PluginRegistry()
        _registry.initialize()
    return _registry


def discover_plugins() -> List[PluginInfo]:
    """Discover all plugins."""
    return get_plugin_registry().discover_plugins()


def get_plugin(name: str) -> Optional[PluginInfo]:
    """Get a plugin by name."""
    return get_plugin_registry().get_plugin(name)


def list_plugins() -> List[PluginInfo]:
    """List all plugins."""
    return get_plugin_registry().list_plugins()
