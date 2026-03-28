"""navig.tui.screens.welcome — WelcomeScreen: mode selection."""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual import on
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Label


class WelcomeScreen(Screen):  # type: ignore[type-arg]
    """Full-screen splash: product name, value proposition, mode selection."""

    DEFAULT_CSS = """
    WelcomeScreen {
        background: #0f172a;
        align: center middle;
    }
    #welcome-panel {
        width: 70;
        border: round #22d3ee;
        background: #111827;
        padding: 1 3;
    }
    #welcome-title {
        color: #22d3ee;
        text-style: bold;
    }
    .welcome-bullet {
        color: #94a3b8;
    }
    #welcome-btns {
        margin-top: 1;
        align: center middle;
    }
    #welcome-btns Button {
        margin: 0 1;
    }
    """

    def compose(self):  # type: ignore[override]
        with Vertical(id="welcome-panel"):
            yield Label("NAVIG — Setup Wizard", id="welcome-title")
            yield Label("")
            yield Label(
                "  ▸ Connect to any server via SSH with a single command",
                classes="welcome-bullet",
            )
            yield Label(
                "  ▸ Run remote commands, transfer files, and manage databases",
                classes="welcome-bullet",
            )
            yield Label(
                "  ▸ Automate workflows with flows and skills",
                classes="welcome-bullet",
            )
            yield Label(
                "  ▸ Converse with an AI assistant that knows your infrastructure",
                classes="welcome-bullet",
            )
            yield Label("")
            yield Label(
                "  [dim]Press [bold]Ctrl+C[/bold] at any time to cancel[/dim]",
                markup=True,
            )
            yield Label("")
            with Horizontal(id="welcome-btns"):
                yield Button("Advanced Setup  →", variant="primary", id="btn-advanced")
                yield Button("Quickstart", variant="default", id="btn-quickstart")

    @on(Button.Pressed, "#btn-advanced")
    def _go_advanced(self) -> None:
        from navig.tui.screens.system_checks import SystemChecksScreen

        self.app.push_screen(SystemChecksScreen())

    @on(Button.Pressed, "#btn-quickstart")
    def _go_quickstart(self) -> None:
        self.app.run_worker(self._do_quickstart(), exclusive=True)

    async def _do_quickstart(self) -> None:
        import functools

        from navig.commands.onboard import (
            create_workspace_templates,
            get_console,
            run_quickstart,
            save_config,
            sync_to_env,
        )
        from navig.tui.config_model import DEFAULT_CONFIG_FILE, DEFAULT_WORKSPACE_DIR

        console = get_console()
        cfg_dict = await asyncio.get_running_loop().run_in_executor(
            None, functools.partial(run_quickstart, console)
        )
        try:
            save_config(cfg_dict, DEFAULT_CONFIG_FILE)
            create_workspace_templates(Path(str(DEFAULT_WORKSPACE_DIR)))
            sync_to_env(cfg_dict)
        except OSError as exc:
            self.notify(f"Config save failed: {exc}", severity="error")
            return
        self.app.exit()
