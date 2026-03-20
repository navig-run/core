"""
navig.ui — Centralized design system for NAVIG CLI output.

All command output must go through this package. Zero print() / console.print()
outside navig/ui/ after migration.

Public API:
    from navig.ui import renderer
    renderer.render_status_header(chips)
    renderer.render_primary_state(label, icon, detail, style)
    # ... see renderer.py for full surface
"""

from navig.ui import renderer  # noqa: F401

__all__ = ["renderer"]
