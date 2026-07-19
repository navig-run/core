# NAVIG Automation Adapters
"""
Platform-specific automation adapters.

Currently supported:
- AutoHotkey v2 (Windows only)
"""

import sys

# Conditional imports based on platform
if sys.platform == "win32":
    try:
        # `ahk` exports AHKAdapter + AHKStatus; the old AHKError/AHKExecutionError/
        # AHKNotFoundError/AHKSafetyError hierarchy was removed (nothing imports it). Those
        # dead names made this whole import raise ImportError, so even on Windows the package
        # exported NOTHING (`__all__ = []`). Import only what exists.
        from .ahk import AHKAdapter, AHKStatus  # noqa: F401

        __all__ = ["AHKAdapter", "AHKStatus"]

        # Also export the AI generator if its optional deps are installed. `AHKScriptArchive`
        # no longer exists in ahk_ai, so importing it alongside AHKAIGenerator used to fail
        # the whole block and drop AHKAIGenerator too.
        try:
            from .ahk_ai import AHKAIGenerator  # noqa: F401

            __all__.append("AHKAIGenerator")
        except ImportError:
            pass  # optional dependency not installed; feature disabled

    except ImportError:
        # AHK dependencies not installed
        __all__ = []
else:
    __all__ = []
