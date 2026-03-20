# NAVIG Platform Module
"""
Centralized platform detection and cross-platform path resolution.

This module is the single source of truth for:
- OS detection (Windows, Linux, macOS, WSL)
- All NAVIG directory paths
- Shell detection
- System service mode detection

Usage:
    from navig.platform import paths

    paths.current_os()     # 'windows', 'linux', 'macos', 'wsl'
    paths.config_dir()     # ~/.navig or /etc/navig
    paths.is_linux()       # True/False
    paths.platform_info()  # Full diagnostic dict
"""

from navig.platform.paths import (
    cache_dir,
    check_docker,
    config_dir,
    current_os,
    data_dir,
    debug_log_path,
    ensure_dirs,
    global_config_path,
    home_dir,
    is_linux,
    is_macos,
    is_unix,
    is_windows,
    is_wsl,
    log_dir,
    platform_info,
    shell_name,
    shell_rc_path,
    ssh_key_dir,
    stack_dir,
    temp_dir,
    workspace_dir,
)

__all__ = [
    "current_os",
    "is_windows",
    "is_linux",
    "is_macos",
    "is_wsl",
    "is_unix",
    "home_dir",
    "config_dir",
    "data_dir",
    "log_dir",
    "cache_dir",
    "workspace_dir",
    "debug_log_path",
    "global_config_path",
    "ssh_key_dir",
    "temp_dir",
    "stack_dir",
    "shell_name",
    "shell_rc_path",
    "platform_info",
    "ensure_dirs",
    "check_docker",
]
