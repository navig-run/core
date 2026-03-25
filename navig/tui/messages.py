"""
navig.tui.messages — Textual message classes for inter-screen communication.
"""
from __future__ import annotations

try:
    from textual.message import Message

    class SettingsSaved(Message):
        """Posted by a settings screen after a successful save.

        DashboardScreen subscribes and re-resolves only the affected section
        row — no full restart required.
        """

        def __init__(self, section_key: str) -> None:
            super().__init__()
            self.section_key = section_key  # e.g. "gateway", "vault", "agents"

except ImportError:
    pass  # Textual not installed; messages are simply unavailable
