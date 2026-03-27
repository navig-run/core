"""
NAVIG Migration Scripts

This module contains migration utilities for upgrading NAVIG configurations
between versions.
"""

from .migrate_addons_to_templates import migrate_addons_to_templates

__all__ = ["migrate_addons_to_templates"]
