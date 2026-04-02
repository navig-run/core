"""
Local Host Discovery Module for NAVIG

Automatically detects and configures the local development environment.
Discovers OS, services (web server, database, PHP), and creates host configuration.
"""

import os
import platform
import shutil
import socket
import subprocess
from typing import Any

from navig import console_helper as ch
from navig.config import get_config_manager


def _decode_subprocess_output(data: bytes) -> str:
    """Decode subprocess bytes output robustly.

    Tries UTF-8 first; on failure falls back to the system's preferred encoding
    with ``errors='replace'`` so non-decodable bytes never raise an exception.
    This prevents UnicodeDecodeError crashes on Windows systems whose locale
    uses a code page other than UTF-8 (e.g. cp850/cp1252 with accented paths).
    """
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        import locale

        enc = locale.getpreferredencoding(False) or "cp1252"
        return data.decode(enc, errors="replace")


def run_local_command(command: str, timeout: int = 10) -> tuple[bool, str, str]:
    """
    Execute a local command and return (success, stdout, stderr).

    Args:
        command: Command string to execute
        timeout: Command timeout in seconds

    Returns:
        Tuple of (success: bool, stdout: str, stderr: str)
    """
    try:
        if platform.system() == "Windows":
            # Use PowerShell on Windows for better compatibility.
            # Do NOT pass text=True here — we decode manually so that non-UTF-8
            # bytes in output (e.g. accented chars from Windows locale paths)
            # don't crash the subprocess reader thread (UnicodeDecodeError).
            result = subprocess.run(
                ["powershell", "-Command", command],
                capture_output=True,
                timeout=timeout,
                shell=False,
            )
        else:
            result = subprocess.run(
                ["bash", "-c", command], capture_output=True, timeout=timeout
            )

        stdout = _decode_subprocess_output(result.stdout).strip()
        stderr = _decode_subprocess_output(result.stderr).strip()
        return (result.returncode == 0, stdout, stderr)
    except subprocess.TimeoutExpired:
        return (False, "", f"Command timed out after {timeout}s")
    except Exception as e:
        return (False, "", str(e))


def check_command_exists(command: str) -> bool:
    """Check if a command exists in PATH."""
    return shutil.which(command) is not None


class LocalDiscovery:
    """
    Auto-discover local machine configuration and services.

    Detects:
    - Operating system and version
    - Installed databases (MySQL, PostgreSQL, SQLite)
    - Web servers (Nginx, Apache, IIS)
    - PHP version and configuration
    - Node.js, Python versions
    - Docker availability
    """

    def __init__(self, progress: bool = True):
        """
        Initialize local discovery.

        Args:
            progress: Whether to show progress messages
        """
        self.progress = progress
        self.discovered_data: dict[str, Any] = {}
        self.is_windows = platform.system() == "Windows"

    def _log(self, message: str, style: str = "info"):
        """Log a message if progress is enabled."""
        if self.progress:
            if style == "success":
                ch.success(message)
            elif style == "warning":
                ch.warning(message)
            elif style == "error":
                ch.error(message)
            elif style == "dim":
                ch.dim(message)
            else:
                ch.info(message)

    def discover_os(self) -> dict[str, Any]:
        """Discover operating system information."""
        self._log("Detecting operating system...")

        os_info = {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "hostname": socket.gethostname(),
        }

        # Get detailed OS info
        if self.is_windows:
            os_info["display_name"] = f"Windows {platform.release()}"
            # Try to get Windows edition
            success, stdout, _ = run_local_command(
                "(Get-CimInstance Win32_OperatingSystem).Caption"
            )
            if success and stdout:
                os_info["display_name"] = stdout.strip()
        else:
            # Linux/macOS
            if platform.system() == "Darwin":
                os_info["display_name"] = f"macOS {platform.mac_ver()[0]}"
            else:
                # Try to get Linux distro info
                success, stdout, _ = run_local_command(
                    'cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d= -f2 | tr -d \\"'
                )
                if success and stdout:
                    os_info["display_name"] = stdout.strip()
                else:
                    os_info["display_name"] = f"Linux {platform.release()}"

        self.discovered_data["os"] = os_info
        self._log(f"  ✓ {os_info['display_name']}", "success")
        return os_info

    def discover_network(self) -> dict[str, Any]:
        """Discover network configuration."""
        self._log("Detecting network configuration...")

        network_info = {
            "hostname": socket.gethostname(),
            "ip_addresses": [],
        }

        try:
            # Get local IP addresses
            hostname = socket.gethostname()
            network_info["ip_addresses"] = list(set(socket.gethostbyname_ex(hostname)[2]))

            # Add loopback if not present
            if "127.0.0.1" not in network_info["ip_addresses"]:
                network_info["ip_addresses"].insert(0, "127.0.0.1")

        except Exception:
            network_info["ip_addresses"] = ["127.0.0.1"]

        self.discovered_data["network"] = network_info
        self._log(f"  ✓ Hostname: {network_info['hostname']}", "success")
        self._log(f"  ✓ IPs: {', '.join(network_info['ip_addresses'][:3])}", "dim")
        return network_info

    def discover_databases(self) -> list[dict[str, Any]]:
        """Discover installed database servers."""
        self._log("Detecting database servers...")

        databases = []

        # MySQL / MariaDB
        if check_command_exists("mysql"):
            success, stdout, _ = run_local_command("mysql --version")
            if success:
                version = stdout.split()[1] if len(stdout.split()) > 1 else "unknown"
                db_type = "mariadb" if "mariadb" in stdout.lower() else "mysql"
                databases.append(
                    {
                        "type": db_type,
                        "version": version,
                        "port": 3306,
                        "command": "mysql",
                    }
                )
                self._log(f"  ✓ {db_type.title()} {version}", "success")

        # PostgreSQL
        if check_command_exists("psql"):
            success, stdout, _ = run_local_command("psql --version")
            if success:
                parts = stdout.split()
                version = parts[-1] if parts else "unknown"
                databases.append(
                    {
                        "type": "postgresql",
                        "version": version,
                        "port": 5432,
                        "command": "psql",
                    }
                )
                self._log(f"  ✓ PostgreSQL {version}", "success")

        # SQLite
        if check_command_exists("sqlite3"):
            success, stdout, _ = run_local_command("sqlite3 --version")
            if success:
                version = stdout.split()[0] if stdout else "unknown"
                databases.append({"type": "sqlite", "version": version, "command": "sqlite3"})
                self._log(f"  ✓ SQLite {version}", "success")

        # Redis
        if check_command_exists("redis-cli"):
            success, stdout, _ = run_local_command("redis-cli --version")
            if success:
                version = stdout.split()[1] if len(stdout.split()) > 1 else "unknown"
                databases.append(
                    {
                        "type": "redis",
                        "version": version,
                        "port": 6379,
                        "command": "redis-cli",
                    }
                )
                self._log(f"  ✓ Redis {version}", "success")

        if not databases:
            self._log("  No databases detected", "dim")

        self.discovered_data["databases"] = databases
        return databases

    def discover_web_servers(self) -> list[dict[str, Any]]:
        """Discover installed web servers."""
        self._log("Detecting web servers...")

        web_servers = []

        # Nginx
        if check_command_exists("nginx"):
            success, stdout, _ = run_local_command("nginx -v 2>&1")
            if success or stdout:
                # nginx outputs version to stderr
                version_line = stdout or ""
                version = (
                    version_line.split("/")[1].split()[0] if "/" in version_line else "unknown"
                )
                web_servers.append(
                    {
                        "type": "nginx",
                        "version": version,
                        "port": 80,
                        "command": "nginx",
                    }
                )
                self._log(f"  ✓ Nginx {version}", "success")

        # Apache
        apache_cmd = "httpd" if not self.is_windows else "httpd"
        if check_command_exists(apache_cmd) or check_command_exists("apache2"):
            cmd = "apache2 -v 2>&1" if check_command_exists("apache2") else "httpd -v 2>&1"
            success, stdout, _ = run_local_command(cmd)
            if success or stdout:
                for line in stdout.split("\n"):
                    if "Apache" in line:
                        parts = line.split("/")
                        version = parts[1].split()[0] if len(parts) > 1 else "unknown"
                        web_servers.append(
                            {
                                "type": "apache",
                                "version": version,
                                "port": 80,
                                "command": apache_cmd,
                            }
                        )
                        self._log(f"  ✓ Apache {version}", "success")
                        break

        # IIS (Windows)
        if self.is_windows:
            success, stdout, _ = run_local_command(
                "Get-WindowsFeature Web-Server -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Installed"
            )
            if success and stdout.strip().lower() == "true":
                web_servers.append(
                    {
                        "type": "iis",
                        "version": "installed",
                        "port": 80,
                        "command": "iis",
                    }
                )
                self._log("  ✓ IIS installed", "success")

        # Caddy
        if check_command_exists("caddy"):
            success, stdout, _ = run_local_command("caddy version")
            if success:
                version = stdout.split()[0] if stdout else "unknown"
                web_servers.append(
                    {
                        "type": "caddy",
                        "version": version,
                        "port": 80,
                        "command": "caddy",
                    }
                )
                self._log(f"  ✓ Caddy {version}", "success")

        if not web_servers:
            self._log("  No web servers detected", "dim")

        self.discovered_data["web_servers"] = web_servers
        return web_servers

    def discover_php(self) -> dict[str, Any] | None:
        """Discover PHP installation."""
        self._log("Detecting PHP...")

        if not check_command_exists("php"):
            self._log("  PHP not found", "dim")
            return None

        success, stdout, _ = run_local_command("php -v")
        if not success:
            return None

        # Parse PHP version
        version_line = stdout.split("\n")[0] if stdout else ""
        parts = version_line.split()
        version = parts[1] if len(parts) > 1 else "unknown"

        php_info = {
            "version": version,
            "command": "php",
        }

        # Check for composer
        if check_command_exists("composer"):
            success, stdout, _ = run_local_command("composer --version")
            if success:
                php_info["composer"] = stdout.split()[2] if len(stdout.split()) > 2 else "installed"

        self.discovered_data["php"] = php_info
        self._log(f"  ✓ PHP {version}", "success")
        if php_info.get("composer"):
            self._log(f"  ✓ Composer {php_info['composer']}", "dim")

        return php_info

    def discover_node(self) -> dict[str, Any] | None:
        """Discover Node.js installation."""
        self._log("Detecting Node.js...")

        if not check_command_exists("node"):
            self._log("  Node.js not found", "dim")
            return None

        success, stdout, _ = run_local_command("node --version")
        if not success:
            return None

        node_info = {
            "version": stdout.strip().lstrip("v"),
            "command": "node",
        }

        # Check for npm
        if check_command_exists("npm"):
            success, stdout, _ = run_local_command("npm --version")
            if success:
                node_info["npm"] = stdout.strip()

        # Check for yarn
        if check_command_exists("yarn"):
            success, stdout, _ = run_local_command("yarn --version")
            if success:
                node_info["yarn"] = stdout.strip()

        # Check for pnpm
        if check_command_exists("pnpm"):
            success, stdout, _ = run_local_command("pnpm --version")
            if success:
                node_info["pnpm"] = stdout.strip()

        self.discovered_data["node"] = node_info
        self._log(f"  ✓ Node.js {node_info['version']}", "success")

        return node_info

    def discover_python(self) -> dict[str, Any] | None:
        """Discover Python installation."""
        self._log("Detecting Python...")

        python_cmd = "python3" if check_command_exists("python3") else "python"
        if not check_command_exists(python_cmd):
            self._log("  Python not found", "dim")
            return None

        success, stdout, _ = run_local_command(f"{python_cmd} --version")
        if not success:
            return None

        version = stdout.split()[1] if len(stdout.split()) > 1 else "unknown"

        python_info = {
            "version": version,
            "command": python_cmd,
        }

        # Check for pip
        pip_cmd = "pip3" if check_command_exists("pip3") else "pip"
        if check_command_exists(pip_cmd):
            success, stdout, _ = run_local_command(f"{pip_cmd} --version")
            if success:
                python_info["pip"] = stdout.split()[1] if len(stdout.split()) > 1 else "installed"

        self.discovered_data["python"] = python_info
        self._log(f"  ✓ Python {version}", "success")

        return python_info

    def discover_docker(self) -> dict[str, Any] | None:
        """Discover Docker installation."""
        self._log("Detecting Docker...")

        if not check_command_exists("docker"):
            self._log("  Docker not found", "dim")
            return None

        success, stdout, _ = run_local_command("docker --version")
        if not success:
            return None

        # Parse version
        parts = stdout.split()
        version = parts[2].rstrip(",") if len(parts) > 2 else "unknown"

        docker_info = {
            "version": version,
            "command": "docker",
        }

        # Check if Docker daemon is running
        success, stdout, _ = run_local_command('docker info --format "{{.ServerVersion}}"')
        docker_info["running"] = success

        # Check for docker-compose
        if check_command_exists("docker-compose"):
            success, stdout, _ = run_local_command("docker-compose --version")
            if success:
                docker_info["compose"] = stdout.split()[-1] if stdout else "installed"
        elif success:
            # Check for docker compose (plugin version)
            success, stdout, _ = run_local_command("docker compose version")
            if success:
                docker_info["compose"] = stdout.split()[-1] if stdout else "installed"

        self.discovered_data["docker"] = docker_info
        status = "running" if docker_info["running"] else "installed (not running)"
        self._log(f"  ✓ Docker {version} ({status})", "success")

        return docker_info

    def discover_git(self) -> dict[str, Any] | None:
        """Discover Git installation."""
        if not check_command_exists("git"):
            return None

        success, stdout, _ = run_local_command("git --version")
        if not success:
            return None

        version = stdout.split()[-1] if stdout else "unknown"

        git_info = {
            "version": version,
            "command": "git",
        }

        self.discovered_data["git"] = git_info
        return git_info

    def discover_all(self) -> dict[str, Any]:
        """Run full discovery and return all collected data."""
        self._log("\n🔍 Discovering local environment...\n", "info")

        self.discover_os()
        self.discover_network()
        self.discover_databases()
        self.discover_web_servers()
        self.discover_php()
        self.discover_node()
        self.discover_python()
        self.discover_docker()
        self.discover_git()

        self._log("\n✓ Discovery complete!\n", "success")

        return self.discovered_data

    def generate_host_config(self, name: str = "localhost") -> dict[str, Any]:
        """
        Generate a host configuration from discovered data.

        Args:
            name: Name for the host configuration

        Returns:
            Host configuration dictionary
        """
        os_info = self.discovered_data.get("os", {})
        network = self.discovered_data.get("network", {})

        config = {
            "host": "127.0.0.1",
            "port": 22,
            "user": os.environ.get("USER", os.environ.get("USERNAME", "user")),
            "is_local": True,  # Special flag for local host
            "metadata": {
                "os": os_info.get("display_name", platform.system()),
                "hostname": network.get("hostname", socket.gethostname()),
            },
            "services": {},
            "paths": {},
        }

        # Add database info
        databases = self.discovered_data.get("databases", [])
        if databases:
            db = databases[0]  # Use first detected database
            config["database"] = {
                "type": db["type"],
                "host": "127.0.0.1",
                "port": db.get("port", 3306),
                "user": "root",
            }

        # Add web server info
        web_servers = self.discovered_data.get("web_servers", [])
        if web_servers:
            ws = web_servers[0]
            config["services"]["web"] = ws["type"]

        # Add PHP info
        php = self.discovered_data.get("php")
        if php:
            config["metadata"]["php_version"] = php["version"]

        # Add Node info
        node = self.discovered_data.get("node")
        if node:
            config["metadata"]["node_version"] = node["version"]

        # Add Python info
        python = self.discovered_data.get("python")
        if python:
            config["metadata"]["python_version"] = python["version"]

        # Add Docker info
        docker = self.discovered_data.get("docker")
        if docker:
            config["metadata"]["docker_version"] = docker["version"]
            config["services"]["docker"] = docker["running"]

        return config


def discover_local_host(
    name: str = "localhost",
    auto_confirm: bool = False,
    set_active: bool = True,
    progress: bool = True,
    no_cache: bool = False,
) -> dict[str, Any] | None:
    """
    Discover and configure local host.

    Args:
        name: Name for the host configuration
        auto_confirm: Skip confirmation prompts
        set_active: Set as active host after creation
        progress: Show progress output

    Returns:
        Created host configuration or None if cancelled/failed
    """
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm
    from rich.table import Table

    console = Console()
    config_manager = get_config_manager()

    # Best-effort cache for the expensive discovery phase.
    cached_payload = None
    try:
        from navig.cache_store import read_json_cache, write_json_cache

        ttl_cfg = config_manager.global_config.get("cache_ttl", {})
        ttl_seconds = int(ttl_cfg.get("host_discovery_seconds", ttl_cfg.get("host_discovery", 300)))
        cache = read_json_cache("host_discovery.json", ttl_seconds=ttl_seconds, no_cache=no_cache)
        if cache.hit and not cache.expired and isinstance(cache.data, dict):
            cached_payload = cache.data
            if progress:
                ch.dim("→ Using cached local discovery result")
    except Exception:
        cached_payload = None

    # Check if host already exists
    existing_hosts = config_manager.list_hosts()
    if name in existing_hosts:
        if not auto_confirm:
            if not Confirm.ask(
                f"\n[yellow]Host '{name}' already exists. Overwrite?[/yellow]",
                default=False,
            ):
                ch.warning("Cancelled.")
                return None

    if cached_payload and cached_payload.get("name") == name:
        discovered = cached_payload.get("discovered") or {}
        config = cached_payload.get("config") or {}
    else:
        # Run discovery
        discovery = LocalDiscovery(progress=progress)
        discovered = discovery.discover_all()

        # Generate config
        config = discovery.generate_host_config(name)

        # Cache results for subsequent runs
        try:
            from navig.cache_store import write_json_cache

            write_json_cache(
                "host_discovery.json",
                {"name": name, "discovered": discovered, "config": config},
            )
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

    # Show summary
    if progress:
        console.print()
        console.print(Panel("[bold cyan]Local Environment Summary[/bold cyan]", expand=False))

        # Create summary table
        table = Table(box=None, show_header=False, padding=(0, 2))
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="white")

        os_info = discovered.get("os", {})
        table.add_row("OS", os_info.get("display_name", "Unknown"))
        table.add_row("Hostname", discovered.get("network", {}).get("hostname", "localhost"))

        # Databases
        dbs = discovered.get("databases", [])
        if dbs:
            db_str = ", ".join([f"{d['type']} {d['version']}" for d in dbs])
            table.add_row("Databases", db_str)

        # Web servers
        ws = discovered.get("web_servers", [])
        if ws:
            ws_str = ", ".join([f"{w['type']} {w['version']}" for w in ws])
            table.add_row("Web Servers", ws_str)

        # PHP
        php = discovered.get("php")
        if php:
            table.add_row("PHP", php["version"])

        # Node
        node = discovered.get("node")
        if node:
            table.add_row("Node.js", node["version"])

        # Docker
        docker = discovered.get("docker")
        if docker:
            status = "running" if docker.get("running") else "not running"
            table.add_row("Docker", f"{docker['version']} ({status})")

        console.print(table)
        console.print()

    # Confirm creation
    if not auto_confirm:
        if not Confirm.ask(f"Create host configuration '[cyan]{name}[/cyan]'?", default=True):
            ch.warning("Cancelled.")
            return None

    # Save configuration
    try:
        # Prepare config for saving
        host_config = {
            "host": config["host"],
            "port": config["port"],
            "user": config["user"],
            "is_local": True,
        }

        # Add optional fields
        if "database" in config:
            host_config["database"] = config["database"]
        if "services" in config:
            host_config["services"] = config["services"]
        if "paths" in config:
            host_config["paths"] = config["paths"]

        # Save host
        config_manager.save_host_config(name, host_config)

        # Save metadata separately
        if "metadata" in config:
            config_manager.update_host_metadata(name, config["metadata"])

        ch.success(f"\n✓ Host '{name}' created successfully!")

        # Set as active if requested
        if set_active:
            config_manager.set_active_host(name)
            ch.info(f"✓ Set '{name}' as active host")

        return config

    except Exception as e:
        ch.error(f"Failed to save host configuration: {e}")
        return None


def should_prompt_local_discovery() -> bool:
    """
    Check if we should prompt for local discovery.

    Returns True if:
    - No hosts are configured
    - Running for the first time
    """
    config_manager = get_config_manager()
    hosts = config_manager.list_hosts()
    return len(hosts) == 0
