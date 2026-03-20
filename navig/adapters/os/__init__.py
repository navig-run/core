# NAVIG OS Adapters Package
"""
OS-specific adapters for handling platform differences in commands and paths.

Supports:
- Windows (PowerShell/CMD)
- Linux (various distributions)
- macOS (Darwin)
"""

from navig.adapters.os.base import OSAdapter
from navig.adapters.os.factory import detect_os, get_os_adapter

__all__ = ['get_os_adapter', 'detect_os', 'OSAdapter']
