"""
NAVIG Hello Plugin - Plugin Registration

This file is the entry point for the plugin. It must export:
- name: Plugin CLI command name (e.g., 'hello' for 'navig hello')
- app: Typer app instance with commands
- check_dependencies(): Function to verify required packages

Optional exports:
- description: Plugin description
- version: Plugin version
"""

from typing import Tuple, List
import typer

# =============================================================================
# PLUGIN METADATA
# =============================================================================

name = "hello"
description = "Example plugin - demonstrates NAVIG plugin development"
version = "1.0.0"
author = "NAVIG Team"

# =============================================================================
# PLUGIN APP
# =============================================================================

app = typer.Typer(
    name=name,
    help=description,
    no_args_is_help=True,
)

# =============================================================================
# DEPENDENCY CHECK
# =============================================================================

def check_dependencies() -> Tuple[bool, List[str]]:
    """
    Check if required packages are installed.
    
    This plugin has no external dependencies, so it always returns success.
    For plugins with dependencies, check each required package:
    
        missing = []
        try:
            import some_package
        except ImportError:
            missing.append("some_package")
        return (len(missing) == 0, missing)
    
    Returns:
        Tuple of (all_satisfied, missing_packages)
    """
    # This plugin has no external dependencies
    return (True, [])

# =============================================================================
# IMPORT COMMANDS
# =============================================================================

# Import commands after app is defined
from navig.plugins.hello import commands  # noqa: F401, E402
