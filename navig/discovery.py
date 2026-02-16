"""
Server Auto-Discovery Module

Automatically detect server configuration, services, and environment details.
The Schema sees all. Catalogues all. Knows all.
"""

import subprocess
import re
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
from navig import console_helper as ch

# Lazy paramiko import - deferred until SSH operations are actually needed
_paramiko = None

def _get_paramiko():
    """Lazy import paramiko only when SSH operations are needed."""
    global _paramiko
    if _paramiko is None:
        try:
            import paramiko
            _paramiko = paramiko
        except ImportError:
            _paramiko = False
    return _paramiko

# For backward compatibility
HAS_PARAMIKO = True  # Will be checked at runtime


class ServerDiscovery:
    """
    Auto-discover server configuration and services via SSH.

    Detects:
    - Operating system and version
    - Installed databases (MySQL, PostgreSQL, etc.)
    - Web servers (Nginx, Apache)
    - PHP version and configuration
    - Application paths (Laravel, WordPress, etc.)
    - Service status and ports
    """

    def __init__(self, ssh_config: Dict[str, Any], debug_logger: Optional[Any] = None):
        """
        Initialize discovery with SSH configuration.

        Args:
            ssh_config: Dict with keys: host, port, user, ssh_key or ssh_password
            debug_logger: Optional DebugLogger instance for logging SSH operations
        """
        self.host = ssh_config['host']
        self.port = ssh_config.get('port', 22)
        self.user = ssh_config['user']
        self.ssh_key = ssh_config.get('ssh_key')
        self.ssh_password = ssh_config.get('ssh_password')
        self.debug_logger = debug_logger

        self.discovered_data = {}
    
    def _build_ssh_command(self, remote_command: str) -> List[str]:
        """Build SSH command with proper authentication."""
        cmd = ['ssh', '-p', str(self.port)]
        
        # Disable strict host key checking for automation
        cmd.extend(['-o', 'StrictHostKeyChecking=no'])
        cmd.extend(['-o', 'UserKnownHostsFile=/dev/null'])
        cmd.extend(['-o', 'LogLevel=ERROR'])  # Suppress warnings
        
        if self.ssh_key:
            cmd.extend(['-i', str(Path(self.ssh_key).expanduser())])
        
        cmd.append(f"{self.user}@{self.host}")
        cmd.append(remote_command)
        
        return cmd
    
    def _execute_ssh(self, command: str) -> Tuple[bool, str, str]:
        """
        Execute SSH command and return (success, stdout, stderr).
        
        Returns:
            Tuple of (success: bool, stdout: str, stderr: str)
        """
        # Use paramiko if password authentication and available
        if self.ssh_password and HAS_PARAMIKO:
            return self._execute_ssh_paramiko(command)
        else:
            return self._execute_ssh_subprocess(command)
    
    def _execute_ssh_paramiko(self, command: str) -> Tuple[bool, str, str]:
        """Execute SSH command using paramiko with connection pooling."""
        import time
        start_time = time.time()

        # Log command start
        if self.debug_logger:
            self.debug_logger.log_ssh_command(
                host=self.host,
                port=self.port,
                user=self.user,
                command=command,
                method="paramiko-pooled"
            )

        try:
            # Use connection pool for better performance
            from navig.connection_pool import SSHConnectionPool
            
            ssh_config = {
                'host': self.host,
                'port': self.port,
                'user': self.user,
                'ssh_key': self.ssh_key,
                'ssh_password': self.ssh_password,
            }
            
            pool = SSHConnectionPool.get_instance()
            conn = pool.get_connection(ssh_config)
            success, stdout_text, stderr_text = conn.execute(command, timeout=30)

            duration_ms = (time.time() - start_time) * 1000

            # Log result
            if self.debug_logger:
                self.debug_logger.log_ssh_result(
                    success=success,
                    output=stdout_text,
                    error=stderr_text,
                    duration_ms=duration_ms
                )

            return (success, stdout_text, stderr_text)

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            error_msg = str(e)

            # Log error
            if self.debug_logger:
                self.debug_logger.log_ssh_result(
                    success=False,
                    output="",
                    error=error_msg,
                    duration_ms=duration_ms
                )
                self.debug_logger.log_error(
                    error=e,
                    context=f"SSH connection to {self.user}@{self.host}:{self.port}"
                )

            return (False, "", error_msg)
    
    def _execute_ssh_subprocess(self, command: str) -> Tuple[bool, str, str]:
        """Execute SSH command using subprocess (key auth only)."""
        import time
        start_time = time.time()

        # Log command start
        if self.debug_logger:
            self.debug_logger.log_ssh_command(
                host=self.host,
                port=self.port,
                user=self.user,
                command=command,
                method="subprocess"
            )

        try:
            ssh_cmd = self._build_ssh_command(command)
            result = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=30
            )

            success = result.returncode == 0
            stdout_text = result.stdout.strip()
            stderr_text = result.stderr.strip()
            duration_ms = (time.time() - start_time) * 1000

            # Log result
            if self.debug_logger:
                self.debug_logger.log_ssh_result(
                    success=success,
                    output=stdout_text,
                    error=stderr_text,
                    duration_ms=duration_ms
                )

            return (success, stdout_text, stderr_text)

        except subprocess.TimeoutExpired:
            duration_ms = (time.time() - start_time) * 1000
            error_msg = "Command timed out"

            if self.debug_logger:
                self.debug_logger.log_ssh_result(
                    success=False,
                    output="",
                    error=error_msg,
                    duration_ms=duration_ms
                )

            return (False, "", error_msg)

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            error_msg = str(e)

            if self.debug_logger:
                self.debug_logger.log_ssh_result(
                    success=False,
                    output="",
                    error=error_msg,
                    duration_ms=duration_ms
                )
                self.debug_logger.log_error(
                    error=e,
                    context=f"SSH subprocess to {self.user}@{self.host}:{self.port}"
                )

            return (False, "", error_msg)
    
    def test_connection(self) -> bool:
        """Test if SSH connection works."""
        success, stdout, stderr = self._execute_ssh("echo 'NAVIG_TEST'")
        return success and "NAVIG_TEST" in stdout
    
    def discover_os(self, progress: bool = True) -> Dict[str, str]:
        """Detect operating system and version."""
        if progress:
            ch.step("Detecting OS...")

        os_info = {
            'os': 'Unknown',
            'os_version': 'Unknown',
            'kernel': 'Unknown',
        }

        # Try /etc/os-release (most modern Linux)
        success, stdout, _ = self._execute_ssh("cat /etc/os-release 2>/dev/null || cat /etc/lsb-release 2>/dev/null")
        if success and stdout:
            for line in stdout.split('\n'):
                if line.startswith('PRETTY_NAME='):
                    os_info['os'] = line.split('=', 1)[1].strip('"')
                elif line.startswith('VERSION_ID='):
                    os_info['os_version'] = line.split('=', 1)[1].strip('"')

        # Get kernel version
        success, stdout, _ = self._execute_ssh("uname -r")
        if success:
            os_info['kernel'] = stdout

        if progress:
            ch.success(f"OS: {os_info['os']}")
        return os_info
    
    def discover_databases(self, progress: bool = True) -> Dict[str, Any]:
        """Detect installed databases and their configurations."""
        if progress:
            ch.step("Detecting databases...")

        databases = []

        # Check MySQL/MariaDB
        mysql_info = self._discover_mysql()
        if mysql_info:
            databases.append(mysql_info)
            if progress:
                ch.success(f"  MySQL: {mysql_info['version']} (port {mysql_info['port']})")

        # Check PostgreSQL
        postgres_info = self._discover_postgresql()
        if postgres_info:
            databases.append(postgres_info)
            if progress:
                ch.success(f"  PostgreSQL: {postgres_info['version']} (port {postgres_info['port']})")

        if not databases and progress:
            ch.dim("  No databases detected")

        return {'databases': databases}
    
    def _discover_mysql(self) -> Optional[Dict[str, Any]]:
        """Detect MySQL/MariaDB installation."""
        # First check if process is running
        success, stdout, _ = self._execute_ssh("ps aux | grep -E 'mysqld|mariadbd' | grep -v grep")
        process_running = success and stdout

        # Check service status
        success, stdout, _ = self._execute_ssh("systemctl is-active mysql 2>/dev/null || systemctl is-active mariadb 2>/dev/null")
        service_active = success and stdout == 'active'

        # If neither process nor service, no MySQL
        if not process_running and not service_active:
            return None

        mysql_info = {
            'type': 'mysql',
            'port': 3306,  # Default
            'version': 'Unknown',
            'service_name': 'mysql',
            'root_user': 'root',
            'root_password': None,
        }
        
        # Get MySQL/MariaDB version - try multiple methods
        version_commands = [
            'mysql --version',
            'mariadb --version',
            '/usr/bin/mysql --version',
            '/usr/bin/mariadb --version',
            '/usr/local/bin/mysql --version',
            'mysqld --version',
            'mariadbd --version'
        ]

        for cmd in version_commands:
            success, stdout, _ = self._execute_ssh(f"{cmd} 2>/dev/null")
            if success and stdout:
                # Detect MariaDB first
                if 'mariadb' in stdout.lower():
                    mysql_info['type'] = 'mariadb'
                    mysql_info['service_name'] = 'mariadb'

                # Try multiple version patterns
                # Pattern 1: "Ver 15.1 Distrib 10.11.6-MariaDB" or "mysql  Ver 8.0.35"
                match = re.search(r'Ver\s+[\d.]+\s+Distrib\s+(\d+\.\d+\.\d+)', stdout, re.IGNORECASE)
                if match:
                    mysql_info['version'] = match.group(1)
                    break

                # Pattern 2: "Ver 8.0.35" or "Ver 10.11.6"
                match = re.search(r'Ver\s+(\d+\.\d+\.\d+)', stdout, re.IGNORECASE)
                if match:
                    mysql_info['version'] = match.group(1)
                    break

                # Pattern 3: Just version number "10.11.6-MariaDB" or "8.0.35"
                match = re.search(r'(\d+\.\d+\.\d+)', stdout)
                if match:
                    mysql_info['version'] = match.group(1)
                    break
        
        # Try to detect port from listening sockets
        success, stdout, _ = self._execute_ssh("ss -tlnp 2>/dev/null | grep -E 'mysqld|mariadb' || netstat -tlnp 2>/dev/null | grep -E 'mysqld|mariadb'")
        if success and stdout:
            match = re.search(r':(\d+)', stdout)
            if match:
                mysql_info['port'] = int(match.group(1))
        
        # Try to auto-detect root credentials
        root_creds = self._discover_mysql_root_credentials()
        if root_creds:
            mysql_info['root_user'] = root_creds.get('user', 'root')
            mysql_info['root_password'] = root_creds.get('password')

        return mysql_info

    def _discover_mysql_root_credentials(self) -> Optional[Dict[str, str]]:
        """
        Auto-detect MySQL root credentials from common configuration files.

        Returns:
            Dict with 'user' and 'password' if found, None otherwise
        """
        # Method 1: Check /root/.my.cnf
        success, stdout, _ = self._execute_ssh("cat /root/.my.cnf 2>/dev/null")
        if success and stdout:
            # Parse [client] section
            user_match = re.search(r'user\s*=\s*(\S+)', stdout)
            pass_match = re.search(r'password\s*=\s*["\']?([^"\'\n]+)["\']?', stdout)
            if pass_match:
                return {
                    'user': user_match.group(1) if user_match else 'root',
                    'password': pass_match.group(1).strip()
                }

        # Method 2: Check /etc/mysql/debian.cnf (Debian/Ubuntu)
        success, stdout, _ = self._execute_ssh("cat /etc/mysql/debian.cnf 2>/dev/null")
        if success and stdout:
            user_match = re.search(r'user\s*=\s*(\S+)', stdout)
            pass_match = re.search(r'password\s*=\s*["\']?([^"\'\n]+)["\']?', stdout)
            if user_match and pass_match:
                return {
                    'user': user_match.group(1).strip(),
                    'password': pass_match.group(1).strip()
                }

        # Method 3: Check HestiaCP MySQL config
        success, stdout, _ = self._execute_ssh("cat /usr/local/hestia/conf/mysql.conf 2>/dev/null")
        if success and stdout:
            # HestiaCP format: HOST='localhost' USER='admin' PASSWORD='...' CHARGER='...'
            user_match = re.search(r"USER='([^']+)'", stdout)
            pass_match = re.search(r"PASSWORD='([^']+)'", stdout)
            if user_match and pass_match:
                return {
                    'user': user_match.group(1),
                    'password': pass_match.group(1)
                }

        # Method 4: Check for mysql_config_editor (MySQL 5.6+)
        # This stores encrypted credentials, but we can try to use it
        success, stdout, _ = self._execute_ssh("mysql_config_editor print --all 2>/dev/null")
        if success and stdout and 'password' in stdout.lower():
            # If credentials are stored, we can't extract them but we know they exist
            # Return a marker that credentials exist but are encrypted
            user_match = re.search(r'user\s*=\s*(\S+)', stdout)
            if user_match:
                return {
                    'user': user_match.group(1),
                    'password': '<encrypted>'  # Marker for encrypted credentials
                }

        return None

    def _discover_postgresql(self) -> Optional[Dict[str, Any]]:
        """Detect PostgreSQL installation."""
        success, stdout, _ = self._execute_ssh("systemctl is-active postgresql 2>/dev/null")
        if not success or stdout != 'active':
            return None
        
        postgres_info = {
            'type': 'postgresql',
            'port': 5432,  # Default
            'version': 'Unknown',
            'service_name': 'postgresql',
        }
        
        # Get PostgreSQL version
        success, stdout, _ = self._execute_ssh("psql --version 2>/dev/null")
        if success:
            match = re.search(r'(\d+\.\d+)', stdout)
            if match:
                postgres_info['version'] = match.group(1)
        
        return postgres_info
    
    def discover_web_servers(self, progress: bool = True) -> Dict[str, Any]:
        """Detect web servers (Nginx, Apache)."""
        if progress:
            ch.step("Detecting web servers...")

        web_servers = []

        # Check Nginx
        nginx_info = self._discover_nginx()
        if nginx_info:
            web_servers.append(nginx_info)
            if progress:
                ch.success(f"  Nginx: {nginx_info['version']}")

        # Check Apache
        apache_info = self._discover_apache()
        if apache_info:
            web_servers.append(apache_info)
            if progress:
                ch.success(f"  Apache: {apache_info['version']}")

        if not web_servers and progress:
            ch.dim("  No web servers detected")

        return {'web_servers': web_servers}
    
    def _discover_nginx(self) -> Optional[Dict[str, Any]]:
        """Detect Nginx installation."""
        # First check if process is running
        success, stdout, _ = self._execute_ssh("ps aux | grep nginx | grep -v grep")
        process_running = success and 'nginx' in stdout
        
        # Check service status
        success, stdout, _ = self._execute_ssh("systemctl is-active nginx 2>/dev/null")
        service_active = success and stdout == 'active'
        
        # If neither process nor service, no Nginx
        if not process_running and not service_active:
            return None
        
        nginx_info = {
            'type': 'nginx',
            'version': 'Unknown',
            'config_path': '/etc/nginx',
            'sites_path': '/etc/nginx/sites-available',
        }
        
        # Get Nginx version - try multiple methods
        for cmd in ['nginx -v', '/usr/sbin/nginx -v', '/usr/local/nginx/sbin/nginx -v']:
            success, stdout, stderr = self._execute_ssh(f"{cmd} 2>&1")
            output = stdout or stderr
            if output:
                match = re.search(r'nginx[/\s](\d+\.\d+\.\d+)', output)
                if match:
                    nginx_info['version'] = match.group(1)
                    break
        
        # Try to detect config path from process
        if process_running:
            success, stdout, _ = self._execute_ssh("ps aux | grep 'nginx: master process' | grep -v grep")
            if success and stdout:
                # Extract config path if it's in the command
                match = re.search(r'-c\s+(\S+)', stdout)
                if match:
                    nginx_info['config_path'] = match.group(1).replace('/nginx.conf', '')
        
        return nginx_info
    
    def _discover_apache(self) -> Optional[Dict[str, Any]]:
        """Detect Apache installation."""
        success, stdout, _ = self._execute_ssh("systemctl is-active apache2 2>/dev/null || systemctl is-active httpd 2>/dev/null")
        if not success or stdout != 'active':
            return None
        
        apache_info = {
            'type': 'apache',
            'version': 'Unknown',
            'config_path': '/etc/apache2',
        }
        
        # Get Apache version
        success, stdout, _ = self._execute_ssh("apache2 -v 2>/dev/null || httpd -v 2>/dev/null")
        if success:
            match = re.search(r'Apache/(\d+\.\d+\.\d+)', stdout)
            if match:
                apache_info['version'] = match.group(1)
        
        return apache_info
    
    def discover_php(self, progress: bool = True) -> Dict[str, Any]:
        """Detect PHP version and configuration."""
        if progress:
            ch.step("Detecting PHP...")

        php_info = {
            'installed': False,
            'version': None,
            'fpm_service': None,
            'config_path': None,
        }

        # Get PHP version
        success, stdout, _ = self._execute_ssh("php -v 2>/dev/null")
        if success:
            match = re.search(r'PHP (\d+\.\d+\.\d+)', stdout)
            if match:
                php_info['installed'] = True
                php_info['version'] = match.group(1)

                # Detect PHP-FPM service name
                major_minor = '.'.join(php_info['version'].split('.')[:2])
                php_info['fpm_service'] = f"php{major_minor}-fpm"

                # Detect config path
                php_info['config_path'] = f"/etc/php/{major_minor}/fpm/pool.d"

                if progress:
                    ch.success(f"  PHP: {php_info['version']}")

        if not php_info['installed'] and progress:
            ch.dim("  PHP not detected")

        return php_info
    
    def discover_application_paths(self, progress: bool = True, skip_web_root: bool = False) -> Dict[str, Any]:
        """
        Detect common application paths.

        Args:
            progress: Show progress indicators
            skip_web_root: Skip web root detection (for host-level discovery)
        """
        if progress:
            ch.step("Discovering application paths...")

        paths = {
            'web_root': None,
            'log_paths': [],
            'app_paths': [],
        }

        # Skip web root detection if requested (host-level discovery)
        if not skip_web_root:
            # Common web root locations
            web_roots = [
                '/var/www/html',
                '/var/www',
                '/usr/share/nginx/html',
                '/home/*/web/*/public_html',
                '/home/*/public_html',
            ]

            for root in web_roots:
                # Handle wildcard patterns
                if '*' in root:
                    success, stdout, _ = self._execute_ssh(f"find {root.split('*')[0]} -type d -name '{root.split('/')[-1]}' 2>/dev/null | head -5")
                    if success and stdout:
                        paths['app_paths'].extend(stdout.split('\n'))
                else:
                    success, _, _ = self._execute_ssh(f"test -d {root}")
                    if success:
                        if not paths['web_root']:
                            paths['web_root'] = root
                        paths['app_paths'].append(root)

            if paths['web_root'] and progress:
                ch.success(f"  Web root: {paths['web_root']}")

            # Detect Laravel installations
            laravel_apps = self._discover_laravel_apps()
            if laravel_apps:
                paths['laravel_apps'] = laravel_apps
                if progress:
                    ch.success(f"  Found {len(laravel_apps)} Laravel app(s)")

        # Always detect logs (host-level configuration)
        log_paths = [
            '/var/log/nginx',
            '/var/log/apache2',
            '/var/log/mysql',
        ]

        for log_path in log_paths:
            success, _, _ = self._execute_ssh(f"test -d {log_path}")
            if success:
                paths['log_paths'].append(log_path)

        return paths
    
    def _discover_laravel_apps(self) -> List[str]:
        """Find Laravel installations."""
        # Look for artisan files (indicator of Laravel)
        success, stdout, _ = self._execute_ssh("find /var/www /home -name 'artisan' -type f 2>/dev/null | head -10")
        if success and stdout:
            apps = []
            for artisan_path in stdout.split('\n'):
                app_path = str(Path(artisan_path).parent)
                apps.append(app_path)
            return apps
        return []
    
    def discover_all(self, progress: bool = True, skip_web_root: bool = False) -> Dict[str, Any]:
        """
        Run all discovery tasks and return complete server profile.

        Args:
            progress: Show progress indicators
            skip_web_root: Skip web root detection (for host-level discovery)

        Returns:
            Dict with all discovered information
        """
        if progress:
            ch.header("Server Auto-Discovery")

        # Test connection first
        if not self.test_connection():
            ch.error("SSH connection failed")
            return {}

        if progress:
            ch.success("SSH connection successful\n")

        discovered = {}

        # Run discovery tasks (pass progress parameter to suppress output if needed)
        discovered.update(self.discover_os(progress=progress))
        discovered.update(self.discover_databases(progress=progress))
        discovered.update(self.discover_web_servers(progress=progress))
        discovered.update(self.discover_php(progress=progress))
        discovered.update(self.discover_application_paths(progress=progress, skip_web_root=skip_web_root))

        if progress:
            ch.success_panel("Discovery Complete")

        return discovered
    
    def format_for_config(self, discovered: Dict[str, Any]) -> Dict[str, Any]:
        """
        Format discovered data for server configuration.
        
        Returns:
            Dict formatted for ConfigManager.create_server_config()
        """
        config = {
            'metadata': {
                'os': discovered.get('os', 'Unknown'),
                'kernel': discovered.get('kernel', 'Unknown'),
            },
            'paths': {
                'web_root': discovered.get('web_root', ''),
                'logs': '',
                'php_config': '',
                'nginx_config': '',
                'app_storage': '',
            },
            'services': {
                'web': 'nginx',
                'php': 'php-fpm',
                'database': 'mysql',
                'cache': 'redis-server',
            },
        }
        
        # Extract database info
        databases = discovered.get('databases', [])
        if databases:
            db = databases[0]  # Use first database found
            config['database'] = {
                'type': db['type'],
                'remote_port': db['port'],
                'local_tunnel_port': 3307,
                'name': '',  # Still require user input
                'user': '',  # Still require user input
                'password': '',  # Still require user input
            }
            config['services']['database'] = db['service_name']
            config['metadata']['mysql_version'] = db.get('version', 'Unknown')
        
        # Extract web server info
        web_servers = discovered.get('web_servers', [])
        if web_servers:
            web = web_servers[0]  # Use first web server found
            config['services']['web'] = web['type']
            if web['type'] == 'nginx':
                config['paths']['nginx_config'] = web.get('sites_path', '/etc/nginx/sites-available')
        
        # Extract PHP info
        php_info = discovered.get('php_info', {})
        if php_info and php_info.get('installed'):
            config['metadata']['php_version'] = php_info['version']
            config['services']['php'] = php_info.get('fpm_service', 'php-fpm')
            if php_info.get('config_path'):
                config['paths']['php_config'] = php_info['config_path']
        
        # Extract paths
        if discovered.get('web_root'):
            config['paths']['web_root'] = discovered['web_root']
        
        log_paths = discovered.get('log_paths', [])
        if log_paths:
            config['paths']['logs'] = log_paths[0]  # Use first log path
        
        return config
    
    def discover_templates(self, progress: bool = True) -> Dict[str, Dict[str, Any]]:
        """
        Auto-detect installed templates on the server.

        Args:
            progress: Show progress indicators

        Returns:
            Dict mapping template name to detection info:
            {
                'template_name': {
                    'detected': bool,
                    'version': str,
                    'paths': dict,
                    'services': list,
                    'ports': list
                }
            }
        """
        if progress:
            ch.header("Template Auto-Detection")

        detected_templates = {}

        # Detect n8n
        n8n_info = self._detect_n8n()
        if n8n_info['detected']:
            detected_templates['n8n'] = n8n_info
            if progress:
                ch.success(f"✓ n8n detected (v{n8n_info.get('version', 'unknown')})")

        # Detect HestiaCP
        hestia_info = self._detect_hestiacp()
        if hestia_info['detected']:
            detected_templates['hestiacp'] = hestia_info
            if progress:
                ch.success(f"✓ HestiaCP detected (v{hestia_info.get('version', 'unknown')})")

        # Detect Gitea
        gitea_info = self._detect_gitea()
        if gitea_info['detected']:
            detected_templates['gitea'] = gitea_info
            if progress:
                ch.success(f"✓ Gitea detected (v{gitea_info.get('version', 'unknown')})")

        if not detected_templates and progress:
            ch.dim("No templates detected")

        return detected_templates
    
    def _detect_n8n(self) -> Dict[str, Any]:
        """
        Detect n8n workflow automation platform.
        
        Checks for:
        - n8n systemd service
        - n8n process listening on port 5678
        - n8n installation directory (~/.n8n)
        - n8n binary in PATH
        """
        info = {
            'detected': False,
            'version': None,
            'paths': {},
            'services': [],
            'ports': []
        }
        
        # Check for systemd service
        success, stdout, _ = self._execute_ssh("systemctl is-active n8n 2>/dev/null")
        if success or "active" in stdout:
            info['detected'] = True
            info['services'].append('n8n.service')
        
        # Check for n8n process
        success, stdout, _ = self._execute_ssh("pgrep -f 'n8n' 2>/dev/null")
        if success and stdout:
            info['detected'] = True
        
        # Check for n8n binary and get version - try multiple methods
        version_commands = [
            "n8n --version",
            "n8n version",
            "/usr/local/bin/n8n --version",
            "/usr/bin/n8n --version",
            "npm list -g n8n 2>/dev/null | grep n8n@",
            "cat ~/.n8n/package.json 2>/dev/null | grep version",
        ]

        for cmd in version_commands:
            success, stdout, _ = self._execute_ssh(f"{cmd} 2>/dev/null")
            if success and stdout:
                info['detected'] = True
                # Extract version (e.g., "1.18.2")
                version_match = re.search(r'(\d+\.\d+\.\d+)', stdout)
                if version_match:
                    info['version'] = version_match.group(1)
                    break
        
        # Check for listening port
        success, stdout, _ = self._execute_ssh("ss -tlnp 2>/dev/null | grep -E ':5678\\s'")
        if success and stdout:
            info['ports'].append(5678)
        
        # Check for n8n home directory
        success, stdout, _ = self._execute_ssh("test -d ~/.n8n && echo 'exists' || echo 'missing'")
        if success and "exists" in stdout:
            # Get actual home directory path
            success2, home_path, _ = self._execute_ssh("echo $HOME")
            if success2 and home_path:
                info['paths']['n8n_home'] = f"{home_path.strip()}/.n8n"
                info['paths']['workflows_dir'] = f"{home_path.strip()}/.n8n/workflows"
                info['paths']['credentials_dir'] = f"{home_path.strip()}/.n8n/credentials"
        
        # Check common installation paths
        for path in ['/opt/n8n', '/usr/local/n8n', '/var/lib/n8n']:
            success, stdout, _ = self._execute_ssh(f"test -d {path} && echo 'exists'")
            if success and "exists" in stdout:
                info['paths']['install_dir'] = path
                break
        
        return info
    
    def _detect_hestiacp(self) -> Dict[str, Any]:
        """
        Detect HestiaCP control panel.
        
        Checks for:
        - Hestia service
        - /usr/local/hestia installation
        - Hestia CLI binary (v-list-users)
        - Port 8083 (web interface)
        """
        info = {
            'detected': False,
            'version': None,
            'paths': {},
            'services': [],
            'ports': []
        }
        
        # Check for Hestia installation directory
        success, stdout, _ = self._execute_ssh("test -d /usr/local/hestia && echo 'exists'")
        if success and "exists" in stdout:
            info['detected'] = True
            info['paths']['hestia_root'] = '/usr/local/hestia'
            info['paths']['hestia_bin'] = '/usr/local/hestia/bin'
            info['paths']['hestia_conf'] = '/usr/local/hestia/conf'
            info['paths']['hestia_data'] = '/usr/local/hestia/data'
        
        # Check for Hestia CLI
        success, stdout, _ = self._execute_ssh("which v-list-users 2>/dev/null")
        if success and stdout:
            info['detected'] = True
        
        # Get Hestia version - try multiple methods
        version_commands = [
            "/usr/local/hestia/bin/v-list-sys-info json 2>/dev/null",
            "cat /usr/local/hestia/conf/hestia.conf 2>/dev/null | grep VERSION",
            "dpkg -l | grep hestia",
            "/usr/local/hestia/bin/v-list-sys-hestia-version plain 2>/dev/null",
        ]

        for cmd in version_commands:
            success, stdout, _ = self._execute_ssh(cmd)
            if success and stdout:
                # Try JSON format first
                version_match = re.search(r'"version":"([^"]+)"', stdout)
                if version_match:
                    info['version'] = version_match.group(1)
                    break

                # Try VERSION= format
                version_match = re.search(r'VERSION[=\s]+["\']?([0-9.]+)', stdout, re.IGNORECASE)
                if version_match:
                    info['version'] = version_match.group(1)
                    break

                # Try plain version number
                version_match = re.search(r'(\d+\.\d+\.\d+)', stdout)
                if version_match:
                    info['version'] = version_match.group(1)
                    break
        
        # Check for Hestia service
        success, stdout, _ = self._execute_ssh("systemctl is-active hestia 2>/dev/null")
        if success or "active" in stdout:
            info['services'].append('hestia')
        
        # Check for web interface port
        success, stdout, _ = self._execute_ssh("ss -tlnp 2>/dev/null | grep -E ':8083\\s'")
        if success and stdout:
            info['ports'].append(8083)
        
        # Check for common Hestia paths
        common_paths = {
            'web_root': '/home',
            'backup_dir': '/backup',
            'log_dir': '/var/log/hestia'
        }
        
        for key, path in common_paths.items():
            success, stdout, _ = self._execute_ssh(f"test -d {path} && echo 'exists'")
            if success and "exists" in stdout:
                info['paths'][key] = path
        
        return info
    
    def _detect_gitea(self) -> Dict[str, Any]:
        """
        Detect Gitea self-hosted Git service.
        
        Checks for:
        - Gitea systemd service
        - Gitea process
        - Gitea binary
        - Port 3000 (default web interface)
        - /var/lib/gitea or /home/git/gitea
        """
        info = {
            'detected': False,
            'version': None,
            'paths': {},
            'services': [],
            'ports': []
        }
        
        # Check for systemd service
        success, stdout, _ = self._execute_ssh("systemctl is-active gitea 2>/dev/null")
        if success or "active" in stdout:
            info['detected'] = True
            info['services'].append('gitea.service')
        
        # Check for Gitea binary and get version
        success, stdout, _ = self._execute_ssh("gitea --version 2>/dev/null")
        if success and stdout:
            info['detected'] = True
            # Extract version (e.g., "Gitea version 1.21.3")
            version_match = re.search(r'version\s+(\d+\.\d+\.\d+)', stdout, re.IGNORECASE)
            if version_match:
                info['version'] = version_match.group(1)
        
        # Check common binary locations
        for bin_path in ['/usr/local/bin/gitea', '/usr/bin/gitea', '/opt/gitea/gitea']:
            success, stdout, _ = self._execute_ssh(f"test -f {bin_path} && echo 'exists'")
            if success and "exists" in stdout:
                info['detected'] = True
                info['paths']['gitea_binary'] = bin_path
                break
        
        # Check for listening port
        success, stdout, _ = self._execute_ssh("ss -tlnp 2>/dev/null | grep -E ':3000\\s'")
        if success and stdout:
            info['ports'].append(3000)
        
        # Check common installation paths
        for install_path in ['/var/lib/gitea', '/home/git/gitea', '/opt/gitea']:
            success, stdout, _ = self._execute_ssh(f"test -d {install_path} && echo 'exists'")
            if success and "exists" in stdout:
                info['paths']['gitea_root'] = install_path
                
                # Try to find common subdirectories
                for subdir in ['repositories', 'data', 'log', 'custom']:
                    success2, stdout2, _ = self._execute_ssh(f"test -d {install_path}/{subdir} && echo 'exists'")
                    if success2 and "exists" in stdout2:
                        info['paths'][f'gitea_{subdir}'] = f"{install_path}/{subdir}"
                
                break
        
        # Check for config file
        for config_path in ['/etc/gitea/app.ini', '/var/lib/gitea/custom/conf/app.ini']:
            success, stdout, _ = self._execute_ssh(f"test -f {config_path} && echo 'exists'")
            if success and "exists" in stdout:
                info['paths']['gitea_config'] = config_path
                break
        
        return info

