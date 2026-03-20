# NAVIG Automation Adapters
"""
Platform-specific automation adapters.

Currently supported:
- AutoHotkey v2 (Windows only)
"""

import sys

# Conditional imports based on platform
if sys.platform == 'win32':
    try:
        from .ahk import (
            AHKAdapter,
            AHKError,
            AHKExecutionError,
            AHKNotFoundError,
            AHKSafetyError,
        )
        __all__ = [
            'AHKAdapter',
            'AHKError',
            'AHKNotFoundError',
            'AHKExecutionError',
            'AHKSafetyError',
        ]

        # Also export AI module if available
        try:
            from .ahk_ai import AHKAIGenerator, AHKScriptArchive
            __all__.extend(['AHKAIGenerator', 'AHKScriptArchive'])
        except ImportError:
            pass

    except ImportError:
        # AHK dependencies not installed
        __all__ = []
else:
    __all__ = []
