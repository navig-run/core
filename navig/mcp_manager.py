"""MCP (Model Context Protocol) Server Manager

Manages installation, configuration, and execution of MCP servers.
MCP servers provide context and tools to AI assistants.
"""

import json
import subprocess
from pathlib import Path
from typing import Any

from navig import console_helper as ch


class MCPServer:
    """Represents an MCP server instance."""

    def __init__(self, name: str, config: dict[str, Any]):
        """Initialize MCP server.

        Args:
            name: Server name (unique identifier)
            config: Server configuration dict with:
                - type: 'npm', 'python', 'standalone'
                - package: Package name/URL
                - command: Command to run server
                - args: List of command arguments
                - env: Environment variables
                - enabled: Whether server is enabled
        """
        self.name = name
        self.config = config
        self.process = None

    def is_enabled(self) -> bool:
        """Check if server is enabled."""
        return self.config.get("enabled", False)

    def is_running(self) -> bool:
        """Check if server process is running."""
        if self.process and self.process.poll() is None:
            return True
        return False

    def start(self) -> bool:
        """Start the MCP server process."""
        if self.is_running():
            ch.warning(f"MCP server '{self.name}' is already running")
            return True

        try:
            command = self.config.get("command")
            args = self.config.get("args", [])
            env_overrides = self.config.get("env", {})

            full_command = [command] + args

            # SECURITY FIX: Merge custom env with os.environ to preserve PATH and system vars
            # Previously: env=env stripped all parent environment variables
            # Now: Start with full environment and apply custom overrides
            import os

            full_env = os.environ.copy()
            full_env.update(env_overrides)

            ch.info(f"Starting MCP server: {self.name}")
            ch.dim(f"Command: {' '.join(full_command)}")

            self.process = subprocess.Popen(
                full_command,
                env=full_env,  # Use merged environment instead of only custom env
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            ch.success(f"MCP server '{self.name}' started (PID: {self.process.pid})")
            return True

        except Exception as e:
            ch.error(f"Failed to start MCP server '{self.name}': {e}")
            return False

    def stop(self) -> bool:
        """Stop the MCP server process."""
        if not self.is_running():
            ch.warning(f"MCP server '{self.name}' is not running")
            return True

        try:
            ch.info(f"Stopping MCP server: {self.name}")
            self.process.terminate()
            self.process.wait(timeout=5)
            ch.success(f"✓ MCP server '{self.name}' stopped")
            return True

        except subprocess.TimeoutExpired:
            ch.warning("Server did not stop gracefully, forcing...")
            self.process.kill()
            self.process.wait()
            ch.success(f"✓ MCP server '{self.name}' forcefully stopped")
            return True

        except Exception as e:
            ch.error(f"Failed to stop MCP server '{self.name}': {e}")
            return False

    def restart(self) -> bool:
        """Restart the MCP server."""
        self.stop()
        return self.start()

    def get_status(self) -> dict[str, Any]:
        """Get server status information."""
        return {
            "name": self.name,
            "enabled": self.is_enabled(),
            "running": self.is_running(),
            "pid": self.process.pid if self.is_running() else None,
            "type": self.config.get("type"),
            "command": self.config.get("command"),
        }


class MCPManager:
    """Manages MCP servers and directory."""

    MCP_DIRECTORY_URL = "https://mcp.so/directory"

    def __init__(self, config_dir: Path | None = None):
        """Initialize MCP manager.

        Args:
            config_dir: Configuration directory (default: ~/.navig/mcp/)
        """
        if config_dir is None:
            config_dir = Path.home() / ".navig" / "mcp"

        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)

        self.servers_file = self.config_dir / "servers.json"
        self.servers: dict[str, MCPServer] = {}

        self._load_servers()

    def _load_servers(self):
        """Load MCP servers from configuration file."""
        if not self.servers_file.exists():
            self.servers = {}
            return

        try:
            with open(self.servers_file) as f:
                servers_config = json.load(f)

            for name, config in servers_config.items():
                self.servers[name] = MCPServer(name, config)

            ch.dim(f"Loaded {len(self.servers)} MCP server(s)")

        except Exception as e:
            ch.error(f"Failed to load MCP servers: {e}")
            self.servers = {}

    def _save_servers(self):
        """Save MCP servers to configuration file."""
        try:
            servers_config = {name: server.config for name, server in self.servers.items()}

            with open(self.servers_file, "w", encoding="utf-8") as f:
                json.dump(servers_config, f, indent=2)

            ch.dim(f"Saved {len(self.servers)} MCP server(s)")

        except Exception as e:
            ch.error(f"Failed to save MCP servers: {e}")

    def search_directory(self, query: str) -> list[dict[str, Any]]:
        """Search MCP directory for servers.

        Args:
            query: Search query string

        Returns:
            List of matching server metadata
        """
        ch.info(f"Searching MCP directory for: {query}")

        try:
            # Known MCP servers from the official Model Context Protocol ecosystem
            # AUDIT: MANUAL REVIEW REQUIRED — official registry integration needs versioning, trust, and signature verification policy.
            # Extend with official MCP registry lookups here when the ecosystem matures
            # Reference: https://github.com/modelcontextprotocol/servers
            common_servers = [
                {
                    "name": "filesystem",
                    "description": "Access local filesystem with configurable allowed directories",
                    "type": "npm",
                    "package": "@modelcontextprotocol/server-filesystem",
                },
                {
                    "name": "github",
                    "description": "GitHub API integration for repositories, issues, PRs",
                    "type": "npm",
                    "package": "@modelcontextprotocol/server-github",
                },
                {
                    "name": "sqlite",
                    "description": "SQLite database access and querying",
                    "type": "npm",
                    "package": "@modelcontextprotocol/server-sqlite",
                },
                {
                    "name": "brave-search",
                    "description": "Web search via Brave Search API",
                    "type": "npm",
                    "package": "@modelcontextprotocol/server-brave-search",
                },
                {
                    "name": "memory",
                    "description": "Persistent knowledge graph memory for context",
                    "type": "npm",
                    "package": "@modelcontextprotocol/server-memory",
                },
                {
                    "name": "puppeteer",
                    "description": "Browser automation for web scraping and interaction",
                    "type": "npm",
                    "package": "@modelcontextprotocol/server-puppeteer",
                },
                {
                    "name": "fetch",
                    "description": "HTTP fetch capabilities for web content retrieval",
                    "type": "npm",
                    "package": "@modelcontextprotocol/server-fetch",
                },
                {
                    "name": "slack",
                    "description": "Slack workspace integration for channels and messages",
                    "type": "npm",
                    "package": "@modelcontextprotocol/server-slack",
                },
                {
                    "name": "postgres",
                    "description": "PostgreSQL database access with schema inspection",
                    "type": "npm",
                    "package": "@modelcontextprotocol/server-postgres",
                },
                {
                    "name": "google-drive",
                    "description": "Google Drive file access and search",
                    "type": "npm",
                    "package": "@modelcontextprotocol/server-gdrive",
                },
                {
                    "name": "google-maps",
                    "description": "Google Maps API for location services",
                    "type": "npm",
                    "package": "@modelcontextprotocol/server-google-maps",
                },
            ]

            # Filter by query
            results = [
                s
                for s in common_servers
                if query.lower() in s["name"].lower() or query.lower() in s["description"].lower()
            ]

            ch.success(f"Found {len(results)} matching server(s)")
            return results

        except Exception as e:
            ch.error(f"Failed to search MCP directory: {e}")
            return []

    def install_server(self, name: str, package: str, server_type: str = "npm") -> bool:
        """Install an MCP server.

        Args:
            name: Server name
            package: Package name or URL
            server_type: Installation type ('npm', 'python', 'standalone')

        Returns:
            True if installation successful
        """
        ch.header(f"Installing MCP Server: {name}")
        ch.info(f"Type: {server_type}")
        ch.info(f"Package: {package}")

        try:
            if server_type == "npm":
                # Install via npm globally
                ch.step("Installing npm package...")
                result = subprocess.run(
                    ["npm", "install", "-g", package], capture_output=True, text=True
                )

                if result.returncode != 0:
                    ch.error(f"npm install failed: {result.stderr}")
                    return False

                # Configure server
                command = "npx"
                args = [package]

            elif server_type == "python":
                # Install via pip
                ch.step("Installing Python package...")
                result = subprocess.run(["pip", "install", package], capture_output=True, text=True)

                if result.returncode != 0:
                    ch.error(f"pip install failed: {result.stderr}")
                    return False

                command = "python"
                args = ["-m", package]

            elif server_type == "standalone":
                ch.warning("Standalone servers must be configured manually")
                command = package
                args = []

            else:
                ch.error(f"Unknown server type: {server_type}")
                return False

            # Add server to configuration
            config = {
                "type": server_type,
                "package": package,
                "command": command,
                "args": args,
                "env": {},
                "enabled": False,
            }

            self.servers[name] = MCPServer(name, config)
            self._save_servers()

            ch.success(f"✓ MCP server '{name}' installed")
            ch.info("Enable with: navig mcp enable {name}")
            return True

        except Exception as e:
            ch.error(f"Failed to install MCP server: {e}")
            return False

    def uninstall_server(self, name: str) -> bool:
        """Uninstall an MCP server.

        Args:
            name: Server name

        Returns:
            True if uninstallation successful
        """
        if name not in self.servers:
            ch.error(f"MCP server '{name}' not found")
            return False

        server = self.servers[name]

        # Stop if running
        if server.is_running():
            server.stop()

        # Remove from configuration
        del self.servers[name]
        self._save_servers()

        ch.success(f"✓ MCP server '{name}' uninstalled")
        ch.warning("Package may still be installed globally - remove manually if needed")
        return True

    def enable_server(self, name: str) -> bool:
        """Enable an MCP server."""
        if name not in self.servers:
            ch.error(f"MCP server '{name}' not found")
            return False

        self.servers[name].config["enabled"] = True
        self._save_servers()
        ch.success(f"✓ MCP server '{name}' enabled")
        return True

    def disable_server(self, name: str) -> bool:
        """Disable an MCP server."""
        if name not in self.servers:
            ch.error(f"MCP server '{name}' not found")
            return False

        server = self.servers[name]

        # Stop if running
        if server.is_running():
            server.stop()

        server.config["enabled"] = False
        self._save_servers()
        ch.success(f"✓ MCP server '{name}' disabled")
        return True

    def start_server(self, name: str) -> bool:
        """Start an MCP server."""
        if name not in self.servers:
            ch.error(f"MCP server '{name}' not found")
            return False

        return self.servers[name].start()

    def stop_server(self, name: str) -> bool:
        """Stop an MCP server."""
        if name not in self.servers:
            ch.error(f"MCP server '{name}' not found")
            return False

        return self.servers[name].stop()

    def restart_server(self, name: str) -> bool:
        """Restart an MCP server."""
        if name not in self.servers:
            ch.error(f"MCP server '{name}' not found")
            return False

        return self.servers[name].restart()

    def list_servers(
        self, enabled_only: bool = False, running_only: bool = False
    ) -> list[MCPServer]:
        """List MCP servers.

        Args:
            enabled_only: Only return enabled servers
            running_only: Only return running servers

        Returns:
            List of MCPServer instances
        """
        servers = list(self.servers.values())

        if enabled_only:
            servers = [s for s in servers if s.is_enabled()]

        if running_only:
            servers = [s for s in servers if s.is_running()]

        return servers

    def get_server(self, name: str) -> MCPServer | None:
        """Get MCP server by name."""
        return self.servers.get(name)

    def start_all_enabled(self) -> int:
        """Start all enabled MCP servers.

        Returns:
            Number of servers started
        """
        enabled_servers = self.list_servers(enabled_only=True)

        if not enabled_servers:
            ch.warning("No enabled MCP servers to start")
            return 0

        ch.info(f"Starting {len(enabled_servers)} enabled MCP server(s)...")

        started = 0
        for server in enabled_servers:
            if server.start():
                started += 1

        ch.success(f"✓ Started {started}/{len(enabled_servers)} MCP server(s)")
        return started

    def stop_all(self) -> int:
        """Stop all running MCP servers.

        Returns:
            Number of servers stopped
        """
        running_servers = self.list_servers(running_only=True)

        if not running_servers:
            ch.warning("No running MCP servers to stop")
            return 0

        ch.info(f"Stopping {len(running_servers)} running MCP server(s)...")

        stopped = 0
        for server in running_servers:
            if server.stop():
                stopped += 1

        ch.success(f"✓ Stopped {stopped}/{len(running_servers)} MCP server(s)")
        return stopped
