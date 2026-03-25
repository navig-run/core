"""
navig.tui.app — NavigOnboardingApp.

State-aware Textual Application:
  mode="wizard"    → push BootScreen (first-run, no config)
  mode="dashboard" → push DashboardScreen (config exists)

deep_link is forwarded to DashboardScreen for startup routing
(e.g. deep_link="provider" → wizard steps open on AI Provider step).
"""
from __future__ import annotations

from typing import Optional

from textual.app import App
from textual.binding import Binding

from navig.tui.config_model import NavigConfig


class NavigOnboardingApp(App):  # type: ignore[type-arg]
    """NAVIG state-aware TUI entry point."""

    TITLE = "NAVIG Setup"
    SUB_TITLE = "Autonomous Operations Assistant"

    BINDINGS = [Binding("ctrl+c", "quit", "Quit", priority=True)]

    CSS = """
    Screen { background: #0f172a; }

    .muted { color: #64748b; }
    .step-dot-active  { color: #22d3ee; }
    .step-dot-done    { color: #10b981; }
    .step-dot-pending { color: #334155; }

    .brand-panel {
        border: round #22d3ee;
        background: #111827;
        padding: 1 2;
    }

    /* Wizard step fade-in */
    Step1IdentityWidget { opacity: 0%; }
    Step1IdentityWidget.visible { opacity: 100%; }
    Step2ProviderWidget { opacity: 0%; }
    Step2ProviderWidget.visible { opacity: 100%; }
    Step3RuntimeWidget { opacity: 0%; }
    Step3RuntimeWidget.visible { opacity: 100%; }
    Step4PacksWidget { opacity: 0%; }
    Step4PacksWidget.visible { opacity: 100%; }
    Step5ShellWidget { opacity: 0%; }
    Step5ShellWidget.visible { opacity: 100%; }

    /* Buttons */
    Button.-primary {
        background: #22d3ee;
        color: #0f172a;
    }
    Button.-primary:hover { background: #38bdf8; }

    /* Inputs */
    Input { border: tall #1e293b; background: #1e293b; }
    Input:focus { border: tall #22d3ee; }
    Input.-valid { border: tall #10b981; }
    Input.-invalid { border: tall #ef4444; }

    RadioSet { background: #111827; border: round #1e293b; padding: 0 1; }
    Checkbox { background: #111827; }
    Switch.-on .switch--slider { background: #22d3ee; }
    Select { background: #1e293b; }

    /* StatusRow */
    StatusRow { height: auto; padding: 0 1; }
    StatusRow.error  { color: #ef4444; }
    StatusRow.warn   { color: #f59e0b; }
    StatusRow.ok     { color: #10b981; }
    StatusRow.missing { color: #64748b; }

    /* Dashboard */
    #dash-header { padding: 0 2; }
    #dash-body { padding: 1 2; }

    /* Settings panels */
    .field-label { color: #94a3b8; margin-top: 1; }

    /* System checks */
    CheckRow { height: 1; }
    CheckRow .check-name  { width: 30; }
    CheckRow .check-state { width: 6; }
    CheckRow .check-hint  { color: #64748b; }

    /* StepIndicator */
    StepIndicator { height: 1; }

    /* SummaryPanel */
    SummaryPanel { height: auto; }

    /* ConfirmModal overlay */
    ConfirmModal > Container {
        width: 60;
        height: 12;
        border: round #22d3ee;
        background: #111827;
        padding: 1 2;
    }

    /* FinalScreen */
    #final-hint {
        color: #22d3ee;
        text-style: dim;
        text-align: center;
        margin-top: 1;
    }

    /* RichLog */
    RichLog { border: solid #1e293b; background: #0f172a; }
    """

    def __init__(
        self,
        mode: str = "wizard",
        deep_link: Optional[str] = None,
        config: Optional[NavigConfig] = None,
    ) -> None:
        super().__init__()
        self._mode = mode
        self._deep_link = deep_link
        # Store config on app so screens can access via self.app.config
        self.config: Optional[NavigConfig] = config

    def on_mount(self) -> None:
        if self._mode == "dashboard":
            from navig.tui.screens.dashboard import DashboardScreen
            self.push_screen(DashboardScreen(deep_link=self._deep_link))
        else:
            from navig.tui.screens.boot import BootScreen
            self.push_screen(BootScreen())
