"""
NAVIG CLI Deprecation Utilities

Helper functions for managing deprecated commands with migration hints.
"""

import functools
from typing import Callable, Optional
from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")


def deprecated_command(
    old_command: str,
    new_command: str,
    version_removed: str = "3.0.0",
    show_warning: bool = True,
) -> Callable:
    """
    Decorator for deprecated commands that shows migration hints.
    
    Args:
        old_command: The deprecated command name (e.g., "navig host info")
        new_command: The new canonical command (e.g., "navig host show")
        version_removed: Version when the command will be removed
        show_warning: Whether to show the deprecation warning
    
    Example:
        @deprecated_command("navig host info", "navig host show")
        def host_info(ctx: typer.Context, ...):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if show_warning:
                ch.warning(
                    f"'{old_command}' is deprecated and will be removed in v{version_removed}.",
                    f"Use '{new_command}' instead."
                )
            return func(*args, **kwargs)
        return wrapper
    return decorator


def deprecation_warning(old_cmd: str, new_cmd: str, version: str = "3.0.0") -> None:
    """
    Print a deprecation warning for a command.
    
    Args:
        old_cmd: The deprecated command
        new_cmd: The replacement command
        version: Version when the old command will be removed
    """
    ch.warning(
        f"'{old_cmd}' is deprecated and will be removed in v{version}.",
        f"Use '{new_cmd}' instead."
    )


# Mapping of deprecated commands to their canonical replacements
DEPRECATION_MAP = {
    # Host commands
    "navig host info": "navig host show",
    "navig host inspect": "navig host show --inspect",
    "navig host current": "navig host show --current",
    "navig host default": "navig host use --default",
    "navig host clone": "navig host add --from <source>",
    
    # App commands
    "navig app info": "navig app show",
    "navig app current": "navig app show --current",
    "navig app clone": "navig app add --from <source>",
    "navig app search": "navig app list --search <query>",
    "navig app migrate": "navig app update",
    
    # Tunnel commands
    "navig tunnel start": "navig tunnel run",
    "navig tunnel stop": "navig tunnel remove",
    "navig tunnel restart": "navig tunnel run --restart",
    "navig tunnel status": "navig tunnel show",
    
    # DB commands
    "navig db query": "navig db run <sql>",
    "navig db file": "navig db run --file <path>",
    "navig db tables": "navig db list --tables <database>",
    "navig db dump": "navig backup add --type db",
    "navig db restore": "navig backup run --restore",
    "navig db shell": "navig db run --shell",
    "navig db containers": "navig db list --containers",
    "navig db users": "navig db list --users",
    "navig db optimize": "navig db run --optimize <table>",
    "navig db repair": "navig db run --repair <table>",
    
    # Legacy top-level DB commands
    "navig sql": "navig db run",
    "navig sqlfile": "navig db run --file",
    "navig db-list": "navig db list",
    "navig db-tables": "navig db list --tables",
    "navig db-databases": "navig db list",
    "navig db-show-tables": "navig db list --tables",
    "navig db-query": "navig db run",
    "navig db-dump": "navig backup add --type db",
    "navig db-shell": "navig db run --shell",
    "navig db-optimize": "navig db run --optimize",
    "navig db-repair": "navig db run --repair",
    "navig db-users": "navig db list --users",
    "navig db-containers": "navig db list --containers",
    
    # File commands (top-level to navig file)
    "navig upload": "navig file add",
    "navig download": "navig file show --download",
    "navig list": "navig file list",
    "navig ls": "navig file list",
    "navig delete": "navig file remove",
    "navig mkdir": "navig file add --dir",
    "navig chmod": "navig file edit --mode",
    "navig chown": "navig file edit --owner",
    "navig cat": "navig file show",
    "navig write-file": "navig file edit --content",
    "navig tree": "navig file list --tree",
    
    # Monitor commands
    "navig monitor resources": "navig monitor show --resources",
    "navig monitor disk": "navig monitor show --disk",
    "navig monitor services": "navig monitor show --services",
    "navig monitor network": "navig monitor show --network",
    "navig monitor health": "navig monitor test",
    "navig monitor report": "navig monitor show --full",
    "navig monitor-resources": "navig monitor show --resources",
    "navig monitor-disk": "navig monitor show --disk",
    "navig monitor-services": "navig monitor show --services",
    "navig monitor-network": "navig monitor show --network",
    "navig health-check": "navig monitor test",
    "navig monitoring-report": "navig monitor show --full",
    
    # Security commands
    "navig security firewall": "navig security show --firewall",
    "navig security firewall-add": "navig security add --rule",
    "navig security firewall-remove": "navig security remove --rule",
    "navig security firewall-enable": "navig security run --enable-firewall",
    "navig security firewall-disable": "navig security run --disable-firewall",
    "navig security fail2ban": "navig security show --fail2ban",
    "navig security unban": "navig security run --unban",
    "navig security ssh": "navig security show --ssh",
    "navig security updates": "navig security show --updates",
    "navig security connections": "navig security show --connections",
    "navig security scan": "navig security test",
    "navig firewall-status": "navig security show --firewall",
    "navig firewall-add": "navig security add --rule",
    "navig firewall-remove": "navig security remove --rule",
    "navig fail2ban-status": "navig security show --fail2ban",
    "navig security-scan": "navig security test",
    
    # Web/Server commands
    "navig web vhosts": "navig server list --vhosts",
    "navig web test": "navig server test",
    "navig web enable": "navig server run --enable",
    "navig web disable": "navig server run --disable",
    "navig web reload": "navig server run --reload",
    "navig webserver-list-vhosts": "navig server list --vhosts",
    "navig webserver-test-config": "navig server test",
    "navig webserver-reload": "navig server run --reload",
    
    # Docker commands (moving to server)
    "navig docker ps": "navig server list --containers",
    "navig docker logs": "navig log show --container",
    "navig docker exec": "navig server run --container",
    "navig docker restart": "navig server run --restart",
    "navig docker stop": "navig server run --stop",
    "navig docker start": "navig server run --start",
    
    # Workflow -> Task
    "navig workflow list": "navig task list",
    "navig workflow show": "navig task show",
    "navig workflow run": "navig task run",
    "navig workflow validate": "navig task test",
    "navig workflow create": "navig task add",
    "navig workflow delete": "navig task remove",
    "navig workflow edit": "navig task edit",
    
    # Backup commands
    "navig backup export": "navig backup add",
    "navig backup import": "navig backup run --restore",
    "navig backup inspect": "navig backup show",
    "navig backup delete": "navig backup remove",
    
    # Config commands
    "navig config migrate": "navig config update",
    "navig config validate": "navig config test",
    "navig config settings": "navig config list",
    "navig config set-mode": "navig config edit --mode",
    "navig config set-confirmation-level": "navig config edit --confirmation",
    "navig config set": "navig config edit",
    "navig config get": "navig config show",
    
    # Template commands
    "navig template info": "navig template show",
    
    # Plugin commands
    "navig plugin info": "navig plugin show",
    "navig plugin install": "navig plugin add",
    "navig plugin uninstall": "navig plugin remove",
    
    # Addon (deprecated for template)
    "navig addon list": "navig template list",
    "navig addon enable": "navig template run --enable",
    "navig addon disable": "navig template run --disable",
    "navig addon info": "navig template show",
    
    # Top-level commands
    "navig logs": "navig log show",
    "navig health": "navig monitor test",
    "navig restart": "navig server run --restart",
    "navig install": "navig deploy add",
    "navig init": "navig config add --init",
    
    # Legacy backup commands
    "navig backup-config": "navig backup add --type config",
    "navig backup-db-all": "navig backup add --type db-all",
    "navig backup-hestia": "navig backup add --type hestia",
    "navig backup-web": "navig backup add --type web",
    "navig backup-all": "navig backup add --type all",
    "navig list-backups": "navig backup list",
    "navig restore-backup": "navig backup run --restore",
    
    # Maintenance commands
    "navig update-packages": "navig server run --update-packages",
    "navig clean-packages": "navig server run --clean-packages",
    "navig rotate-logs": "navig log run --rotate",
    "navig cleanup-temp": "navig server run --cleanup-temp",
    "navig check-filesystem": "navig server test --filesystem",
    "navig system-maintenance": "navig server run --maintenance",
}


def get_canonical_command(deprecated_cmd: str) -> Optional[str]:
    """
    Get the canonical replacement for a deprecated command.
    
    Args:
        deprecated_cmd: The deprecated command string
    
    Returns:
        The canonical command string, or None if not found
    """
    return DEPRECATION_MAP.get(deprecated_cmd)
