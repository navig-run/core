# NAVIG Platform Module
"""
Centralized platform detection and cross-platform path resolution.

This package is the single source of truth for:
- OS detection (Windows, Linux, macOS, WSL)
- All NAVIG directory paths
- Shell detection
- System-service mode detection

Usage::

    from navig.platform import paths

    paths.current_os()     # 'windows', 'linux', 'macos', 'wsl'
    paths.config_dir()     # ~/.navig or /etc/navig
    paths.is_linux()       # True / False
    paths.platform_info()  # Full diagnostic dict
"""

from __future__ import annotations

from navig.platform.paths import (
    audio_configs_dir,
    builtin_packages_dir,
    builtin_store_dir,
    cache_dir,
    check_docker,
    config_dir,
    current_os,
    data_dir,
    debug_log_path,
    ensure_dirs,
    find_app_root,
    genesis_json_path,
    global_config_path,
    home_dir,
    is_directory_accessible,
    is_linux,
    is_macos,
    is_unix,
    is_windows,
    is_wsl,
    log_dir,
    msg_trace_path,
    onboarding_json_path,
    packages_dir,
    platform_info,
    shell_name,
    shell_rc_path,
    ssh_key_dir,
    stack_dir,
    store_dir,
    temp_dir,
    vault_dir,
    workspace_dir,
)

__all__ = [
    # OS detection
    "current_os",
    "is_windows",
    "is_linux",
    "is_macos",
    "is_wsl",
    "is_unix",
    # Path helpers
    "home_dir",
    "config_dir",
    "data_dir",
    "log_dir",
    "cache_dir",
    "workspace_dir",
    "debug_log_path",
    "global_config_path",
    "msg_trace_path",
    "genesis_json_path",
    "entity_json_path",
    "onboarding_json_path",
    "builtin_store_dir",
    "builtin_packages_dir",
    "store_dir",
    "packages_dir",
    "audio_configs_dir",
    "vault_dir",
    "ssh_key_dir",
    "temp_dir",
    "stack_dir",
    "blackbox_dir",
    # Discovery
    "find_app_root",
    "is_directory_accessible",
    # Shell
    "shell_name",
    "shell_rc_path",
    # Diagnostics / bootstrap
    "platform_info",
    "ensure_dirs",
    "check_docker",
]
