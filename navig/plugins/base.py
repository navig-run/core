"""
NAVIG Plugin Base Class

Abstract base class that all plugins should inherit from to ensure consistent interface.

Usage:
    from navig.plugins.base import PluginBase
    
    class MyPlugin(PluginBase):
        @property
        def name(self) -> str:
            return "my-plugin"
        
        @property
        def app(self) -> typer.Typer:
            return my_app
        
        def check_dependencies(self) -> Tuple[bool, List[str]]:
            # Check if required packages are installed
            missing = []
            try:
                import some_package
            except ImportError:
                missing.append("some_package")
            return (len(missing) == 0, missing)
"""

from abc import ABC, abstractmethod
from typing import Tuple, List, Optional, Dict, Any
import typer


class PluginBase(ABC):
    """
    Base class for NAVIG plugins.
    
    Plugins extend NAVIG with custom commands and functionality.
    Each plugin is registered as a sub-command group (e.g., 'navig brain').
    
    Required implementations:
    - name: Plugin name (used as CLI command)
    - app: Typer app instance with plugin commands
    - check_dependencies(): Verify required packages are installed
    
    Optional overrides:
    - description: Plugin description for help text
    - version: Plugin version string
    - on_load(): Called after plugin is loaded
    - on_unload(): Called before plugin is unloaded
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """
        Plugin name (used as CLI command, e.g., 'brain' for 'navig brain').
        
        Must be a valid Python identifier (letters, numbers, underscores).
        """
        pass
    
    @property
    @abstractmethod
    def app(self) -> typer.Typer:
        """
        Typer app instance with plugin commands.
        
        Example:
            app = typer.Typer(help="My plugin description")
            
            @app.command()
            def my_command():
                '''My command help'''
                pass
            
            @property
            def app(self) -> typer.Typer:
                return app
        """
        pass
    
    @abstractmethod
    def check_dependencies(self) -> Tuple[bool, List[str]]:
        """
        Check if all required dependencies are available.
        
        This is called before the plugin is loaded. If dependencies
        are missing, the plugin will not be registered but NAVIG
        will continue to work without it.
        
        Returns:
            Tuple of (success, missing_packages)
            - (True, []) if all dependencies are satisfied
            - (False, ['pkg1', 'pkg2']) if dependencies are missing
        
        Example:
            def check_dependencies(self) -> Tuple[bool, List[str]]:
                missing = []
                try:
                    import openai
                except ImportError:
                    missing.append("openai")
                try:
                    import chromadb
                except ImportError:
                    missing.append("chromadb")
                return (len(missing) == 0, missing)
        """
        pass
    
    @property
    def description(self) -> str:
        """Plugin description (shown in help text and plugin list)."""
        return ""
    
    @property
    def version(self) -> str:
        """Plugin version string (semantic versioning recommended)."""
        return "1.0.0"
    
    @property
    def author(self) -> str:
        """Plugin author name or email."""
        return ""
    
    @property
    def homepage(self) -> str:
        """Plugin homepage URL."""
        return ""
    
    @property
    def permissions(self) -> List[str]:
        """
        Required permissions for this plugin.
        
        Valid permissions:
        - 'ssh': Execute commands on remote hosts
        - 'config_read': Read NAVIG configuration
        - 'config_write': Modify NAVIG configuration
        - 'file_system': Access local file system
        - 'network': Make network requests
        """
        return []
    
    def on_load(self) -> None:
        """
        Called after plugin is successfully loaded.
        
        Use this for initialization that depends on NAVIG being fully loaded.
        """
        pass
    
    def on_unload(self) -> None:
        """
        Called before plugin is unloaded.
        
        Use this for cleanup (close connections, save state, etc.).
        """
        pass
    
    def get_config(self, key: str = None, default: Any = None) -> Any:
        """
        Get plugin-specific configuration.
        
        Configuration is stored under plugins.<name>.* in NAVIG config.
        
        Args:
            key: Configuration key (relative to plugin namespace)
            default: Default value if not found
        
        Returns:
            Configuration value
        
        Example:
            # Get plugins.brain.db_path
            db_path = self.get_config('db_path', '~/.navig/brain.db')
        """
        from navig.core import Config
        config = Config()
        if key:
            return config.get_plugin_config(self.name, key, default)
        return config.get_plugin_config(self.name, default=default)
    
    def set_config(self, key: str, value: Any) -> None:
        """
        Set plugin-specific configuration.
        
        Args:
            key: Configuration key (relative to plugin namespace)
            value: Value to set
        """
        from navig.core import Config
        config = Config()
        config.set_plugin_config(self.name, key, value)
        config.save()


class PluginAPI:
    """
    NAVIG API for plugins to interact with the core system.
    
    Provides safe access to:
    - SSH/Remote execution
    - Configuration
    - Console output
    - Active host/app context
    
    Usage:
        from navig.plugins.base import PluginAPI
        
        api = PluginAPI()
        host = api.get_active_host()
        result = api.run_remote("ls -la")
        api.console.print("Hello!")
    """
    
    def __init__(self):
        from navig.core import Config
        from navig import console_helper as ch
        
        self.config = Config()
        self.console = ch
    
    def get_active_host(self) -> Optional[str]:
        """Get the currently active host name."""
        host, _ = self.config.get_active_host()
        return host
    
    def get_active_app(self) -> Optional[str]:
        """Get the currently active app name."""
        app, _ = self.config.get_active_app()
        return app
    
    def get_host_config(self, host_name: str = None) -> Optional[Dict[str, Any]]:
        """
        Get configuration for a host.
        
        Args:
            host_name: Host name (uses active host if not specified)
        
        Returns:
            Host configuration dict or None if not found
        """
        from navig.config import get_config_manager
        
        config_manager = get_config_manager()
        target_host = host_name or self.get_active_host()
        
        if not target_host:
            return None
        
        return config_manager.load_host_config(target_host)
    
    def run_remote(
        self,
        command: str,
        host_name: str = None,
        timeout: int = 30,
        capture_output: bool = True
    ) -> Tuple[bool, str, str]:
        """
        Execute a command on a remote host.
        
        Args:
            command: Command to execute
            host_name: Target host (uses active host if not specified)
            timeout: Command timeout in seconds
            capture_output: If True, return stdout/stderr
        
        Returns:
            Tuple of (success, stdout, stderr)
        """
        from navig.remote import RemoteOperations
        
        target_host = host_name or self.get_active_host()
        if not target_host:
            return (False, "", "No active host")
        
        host_config = self.get_host_config(target_host)
        if not host_config:
            return (False, "", f"Host '{target_host}' not found")
        
        try:
            remote = RemoteOperations(host_config)
            result = remote.execute(command, timeout=timeout)
            return (result.success, result.stdout, result.stderr)
        except Exception as e:
            return (False, "", str(e))
    
    def upload_file(
        self,
        local_path: str,
        remote_path: str,
        host_name: str = None
    ) -> Tuple[bool, str]:
        """
        Upload a file to a remote host.
        
        Args:
            local_path: Local file path
            remote_path: Remote destination path
            host_name: Target host (uses active host if not specified)
        
        Returns:
            Tuple of (success, error_message)
        """
        from navig.remote import RemoteOperations
        from pathlib import Path
        
        target_host = host_name or self.get_active_host()
        if not target_host:
            return (False, "No active host")
        
        host_config = self.get_host_config(target_host)
        if not host_config:
            return (False, f"Host '{target_host}' not found")
        
        try:
            remote = RemoteOperations(host_config)
            remote.upload(str(Path(local_path)), remote_path)
            return (True, "")
        except Exception as e:
            return (False, str(e))
    
    def download_file(
        self,
        remote_path: str,
        local_path: str,
        host_name: str = None
    ) -> Tuple[bool, str]:
        """
        Download a file from a remote host.
        
        Args:
            remote_path: Remote file path
            local_path: Local destination path
            host_name: Target host (uses active host if not specified)
        
        Returns:
            Tuple of (success, error_message)
        """
        from navig.remote import RemoteOperations
        from pathlib import Path
        
        target_host = host_name or self.get_active_host()
        if not target_host:
            return (False, "No active host")
        
        host_config = self.get_host_config(target_host)
        if not host_config:
            return (False, f"Host '{target_host}' not found")
        
        try:
            remote = RemoteOperations(host_config)
            remote.download(remote_path, str(Path(local_path)))
            return (True, "")
        except Exception as e:
            return (False, str(e))
