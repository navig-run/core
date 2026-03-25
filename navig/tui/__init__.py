"""
navig.tui — Textual-based TUI screens and widgets for NAVIG.

Textual is an optional dependency (``pip install navig[tui]``).
All code here is guarded by ``_TEXTUAL_AVAILABLE``.  Import from this
package only when you need the full TUI classes; use
``navig.tui.resolvers`` for status-badge helpers which have no Textual
dependency.
"""
from __future__ import annotations

try:
    import textual  # noqa: F401  — version probe only
    _TEXTUAL_AVAILABLE = True
except ImportError:
    _TEXTUAL_AVAILABLE = False

if _TEXTUAL_AVAILABLE:
    from navig.tui.app import NavigOnboardingApp
    from navig.tui.screens.dashboard import DashboardScreen
    from navig.tui.screens.wizard import WizardScreen

    __all__ = [
        "_TEXTUAL_AVAILABLE",
        "NavigOnboardingApp",
        "DashboardScreen",
        "WizardScreen",
    ]
else:
    __all__ = ["_TEXTUAL_AVAILABLE"]
