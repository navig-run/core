"""navig.tui.screens.tiered_init — tier selection for premium init UX."""

from __future__ import annotations

from typing import Any

from textual import on
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Label, Static

from navig.tui.config_model import NavigConfig


class TieredInitScreen(Screen):  # type: ignore[type-arg]
    """Choose onboarding intensity before system checks and configuration."""

    BINDINGS = [
        Binding("1", "choose_essential", "Essential", show=True),
        Binding("2", "choose_recommended", "Recommended", show=True),
        Binding("3", "choose_full", "Full", show=True),
        Binding("escape", "go_back", "Back", show=True),
    ]

    DEFAULT_CSS = """
    TieredInitScreen {
        background: #0f172a;
        align: center middle;
    }
    #tier-panel {
        width: 92;
        border: round #22d3ee;
        background: #111827;
        padding: 1 2;
    }
    #tier-title {
        color: #22d3ee;
        text-style: bold;
        margin-bottom: 1;
    }
    #tier-subtitle {
        color: #94a3b8;
        margin-bottom: 1;
    }
    .tier-card {
        width: 1fr;
        border: round #1e293b;
        background: #0b1220;
        padding: 1 1;
        margin: 0 1;
        height: auto;
    }
    .tier-title {
        color: #22d3ee;
        text-style: bold;
    }
    .tier-time {
        color: #94a3b8;
        text-style: dim;
        margin-bottom: 1;
    }
    .tier-bullet {
        color: #cbd5e1;
    }
    #tier-footer {
        margin-top: 1;
        align: center middle;
    }
    #tier-footer Button {
        margin: 0 1;
    }
    """

    def __init__(self, cfg: NavigConfig | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._cfg = cfg or NavigConfig()

    def compose(self):  # type: ignore[override]
        with Vertical(id="tier-panel"):
            yield Label("Choose Setup Tier", id="tier-title")
            yield Label(
                "Start fast now and unlock optional integrations later with dedicated commands.",
                id="tier-subtitle",
            )
            with Horizontal():
                with Vertical(classes="tier-card"):
                    yield Static("Essential", classes="tier-title")
                    yield Static("~2 min", classes="tier-time")
                    yield Static("• Core workspace + safe defaults", classes="tier-bullet")
                    yield Static("• Skip integrations for now", classes="tier-bullet")
                    yield Static("• Best for first-time quick start", classes="tier-bullet")
                    yield Button("1) Essential", id="btn-essential", variant="default")
                with Vertical(classes="tier-card"):
                    yield Static("Recommended", classes="tier-title")
                    yield Static("~5 min", classes="tier-time")
                    yield Static("• AI provider + vault setup", classes="tier-bullet")
                    yield Static("• Host bootstrap guidance", classes="tier-bullet")
                    yield Static("• Balanced default path", classes="tier-bullet")
                    yield Button("2) Recommended", id="btn-recommended", variant="primary")
                with Vertical(classes="tier-card"):
                    yield Static("Full", classes="tier-title")
                    yield Static("~8 min", classes="tier-time")
                    yield Static("• Includes optional integrations", classes="tier-bullet")
                    yield Static("• Matrix / SMTP / social prompts", classes="tier-bullet")
                    yield Static("• Best for team operations", classes="tier-bullet")
                    yield Button("3) Full", id="btn-full", variant="success")
            with Horizontal(id="tier-footer"):
                yield Button("← Back", id="btn-back", variant="default")

    @on(Button.Pressed, "#btn-essential")
    def _choose_essential(self) -> None:
        self._apply_tier("essential")

    @on(Button.Pressed, "#btn-recommended")
    def _choose_recommended(self) -> None:
        self._apply_tier("recommended")

    @on(Button.Pressed, "#btn-full")
    def _choose_full(self) -> None:
        self._apply_tier("full")

    @on(Button.Pressed, "#btn-back")
    def _back(self) -> None:
        self.app.pop_screen()

    def action_choose_essential(self) -> None:
        self._apply_tier("essential")

    def action_choose_recommended(self) -> None:
        self._apply_tier("recommended")

    def action_choose_full(self) -> None:
        self._apply_tier("full")

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def _apply_tier(self, tier: str) -> None:
        self._cfg.onboarding_tier = tier

        if tier == "essential":
            self._cfg.ai_provider = "none"
            self._cfg.api_key = ""
            self._cfg.local_runtime_enabled = False
            self._cfg.capability_packs = []
        elif tier == "recommended":
            if self._cfg.ai_provider == "none":
                self._cfg.ai_provider = "openrouter"
        else:  # full
            if not self._cfg.capability_packs:
                self._cfg.capability_packs = ["devops", "sysops", "lifeops"]

        from navig.tui.screens.system_checks import SystemChecksScreen

        self.app.push_screen(SystemChecksScreen(self._cfg))
