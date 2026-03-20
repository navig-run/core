# NAVIG OS Adapter Factory
"""
Factory functions for creating OS-specific adapters.
"""

import platform
from typing import Optional

from navig.adapters.os.base import OSAdapter


def detect_os() -> str:
    """
    Detect the current operating system.
    
    Returns:
        OS name: 'windows', 'linux', or 'macos'
    """
    system = platform.system().lower()

    if system == 'windows':
        return 'windows'
    elif system == 'darwin':
        return 'macos'
    elif system == 'linux':
        return 'linux'
    else:
        # Fallback for other Unix-like systems
        return 'linux'


def detect_linux_distro() -> Optional[str]:
    """
    Detect the Linux distribution.
    
    Returns:
        Distribution name or None if not Linux/unknown
    """
    try:
        with open('/etc/os-release', 'r') as f:
            for line in f:
                if line.startswith('ID='):
                    return line.strip().split('=')[1].strip('"')
    except FileNotFoundError:
        pass  # file already gone; expected

    # Try other methods
    try:
        import distro
        return distro.id()
    except ImportError:
        pass  # optional dependency not installed; feature disabled

    return None


def get_os_adapter(os_name: Optional[str] = None) -> OSAdapter:
    """
    Get the appropriate OS adapter for the current or specified OS.
    
    Args:
        os_name: Optional OS name override ('windows', 'linux', 'macos')
                 If not provided, auto-detects current OS.
    
    Returns:
        OSAdapter instance for the detected/specified OS
        
    Raises:
        ValueError: If OS is not supported
    """
    if os_name is None:
        os_name = detect_os()

    os_name = os_name.lower()

    if os_name == 'windows':
        from navig.adapters.os.windows import WindowsAdapter
        return WindowsAdapter()

    elif os_name == 'linux':
        from navig.adapters.os.linux import LinuxAdapter
        distro = detect_linux_distro()
        return LinuxAdapter(distro=distro)

    elif os_name in ('macos', 'darwin'):
        from navig.adapters.os.macos import MacOSAdapter
        return MacOSAdapter()

    else:
        raise ValueError(f"Unsupported operating system: {os_name}")


def get_os_adapter_for_remote(os_info: str) -> OSAdapter:
    """
    Get an OS adapter based on remote system information.
    
    This is used when we detect the OS of a remote system
    via SSH and need the appropriate adapter.
    
    Args:
        os_info: OS information string (e.g., from 'uname -s')
        
    Returns:
        OSAdapter instance for the remote OS
    """
    os_lower = os_info.lower()

    if 'windows' in os_lower or 'mingw' in os_lower or 'msys' in os_lower:
        return get_os_adapter('windows')
    elif 'darwin' in os_lower:
        return get_os_adapter('macos')
    else:
        # Assume Linux for everything else
        return get_os_adapter('linux')
