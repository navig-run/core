"""navig.tui.screens — Re-exports all TUI screen classes."""

from __future__ import annotations

from navig.tui.screens.boot import BootScreen
from navig.tui.screens.dashboard import DashboardScreen
from navig.tui.screens.review import ConfirmModal, FinalScreen, ReviewScreen
from navig.tui.screens.system_checks import SystemChecksScreen
from navig.tui.screens.welcome import WelcomeScreen
from navig.tui.screens.wizard import WizardScreen

__all__ = [
    "BootScreen",
    "WelcomeScreen",
    "SystemChecksScreen",
    "WizardScreen",
    "ReviewScreen",
    "FinalScreen",
    "ConfirmModal",
    "DashboardScreen",
]
