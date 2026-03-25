"""
navig.tui.screens.settings.root — SettingsRootScreen.

Navigation hub linking to all scoped settings panels.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Button, Label


class SettingsRootScreen(Screen):  # type: ignore[type-arg]
    """Top-level settings menu."""

    BINDINGS = [
        Binding("escape", "dismiss", "Back", show=True),
        Binding("q", "dismiss", "Back", show=False),
    ]

    DEFAULT_CSS = """
    SettingsRootScreen {
        align: center middle;
        background: #0f172a;
    }
    #settings-panel {
        width: 50;
        border: round #22d3ee;
        background: #111827;
        padding: 1 3;
    }
    #settings-title {
        color: #22d3ee;
        text-style: bold;
        margin-bottom: 1;
    }
    SettingsRootScreen Button {
        width: 100%;
        margin: 0 0 1 0;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-panel"):
            yield Label("Settings", id="settings-title", markup=False)
            yield Button("Gateway", variant="default", id="btn-gateway")
            yield Button("Agents", variant="default", id="btn-agents")
            yield Button("Vault", variant="default", id="btn-vault")
            yield Button("Scheduler", variant="default", id="btn-scheduler")
            yield Button("← Back", variant="default", id="btn-back")

    @on(Button.Pressed, "#btn-gateway")
    def _gateway(self) -> None:
        from navig.tui.screens.settings.gateway import GatewaySettingsScreen

        self.app.push_screen(GatewaySettingsScreen())

    @on(Button.Pressed, "#btn-agents")
    def _agents(self) -> None:
        from navig.tui.screens.settings.agents import AgentSettingsScreen

        self.app.push_screen(AgentSettingsScreen())

    @on(Button.Pressed, "#btn-vault")
    def _vault(self) -> None:
        from navig.tui.screens.settings.vault import VaultSettingsScreen

        self.app.push_screen(VaultSettingsScreen())

    @on(Button.Pressed, "#btn-scheduler")
    def _scheduler(self) -> None:
        from navig.tui.screens.settings.scheduler import SchedulerSettingsScreen

        self.app.push_screen(SchedulerSettingsScreen())

    @on(Button.Pressed, "#btn-back")
    def _back(self) -> None:
        self.dismiss()
