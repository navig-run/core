"""
NAVIG Migration Scripts

This module contains migration utilities for upgrading NAVIG configurations
between versions.
"""

from .migrate_addons_to_templates import migrate_addons_to_templates
from .workspace_to_spaces import ensure_no_stale_spaces_registration, migrate_workspace_to_spaces

__all__ = [
    "migrate_addons_to_templates",
    "migrate_workspace_to_spaces",
    "ensure_no_stale_spaces_registration",
]
