"""navig.tui.screens.boot — BootScreen: animated startup sequence."""

from __future__ import annotations

import asyncio

from textual import work
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Label, RichLog
from textual.worker import WorkerCancelled

from navig.tui.config_model import detect_environment


class BootScreen(Screen):  # type: ignore[type-arg]
    """Staged reveal: character-by-character logo → capability bullets → identity block."""

    BINDINGS = [Binding("enter", "skip", "Skip")]

    DEFAULT_CSS = """
    BootScreen {
        background: #0f172a;
        align: center middle;
    }
    #boot-log {
        width: 72;
        height: 28;
        border: round #22d3ee;
        background: #111827;
        padding: 0 1;
    }
    #boot-skip {
        color: #334155;
        text-align: center;
    }
    """

    def compose(self):  # type: ignore[override]
        with Vertical():
            yield RichLog(id="boot-log", markup=True, highlight=False, wrap=False)
            yield Label("  press [bold]Enter[/bold] to skip", id="boot-skip")

    def on_mount(self) -> None:
        self._run_boot_sequence()

    @work(exclusive=True)
    async def _run_boot_sequence(self) -> None:
        from navig.tui.screens.welcome import WelcomeScreen

        log: RichLog = self.query_one("#boot-log", RichLog)
        try:
            # Phase 1: character-by-character logo
            logo_full = "NAVIG"
            for i in range(1, len(logo_full) + 1):
                log.write(f"[bold #22d3ee]{logo_full[:i]}[/bold #22d3ee]")
                await asyncio.sleep(0.18)

            await asyncio.sleep(0.25)
            log.write("")
            log.write("[dim]Autonomous Operations Assistant[/dim]")
            log.write("[#334155]────────────────────────────────[/#334155]")
            await asyncio.sleep(0.2)

            # Phase 2: capability bullets
            bullets = [
                "Initializing operator shell",
                "Loading runtime modules",
                "Checking local environment",
                "Preparing onboarding flow",
            ]
            for bullet in bullets:
                log.write(f"[#64748b]•[/#64748b] {bullet}")
                await asyncio.sleep(0.35)

            await asyncio.sleep(0.3)
            log.write("")

            # Phase 3: identity block
            env = detect_environment()
            log.write("[dim #64748b]NAVIG mesh detected:[/dim #64748b]  [#22d3ee]0 nodes[/#22d3ee]")
            log.write(
                "[dim #64748b]Operator identity:[/dim #64748b]   [yellow]not registered[/yellow]"
            )
            log.write("")
            log.write(f"[dim]Machine :[/dim]  [#22d3ee]{env['hostname']}[/#22d3ee]")
            log.write(f"[dim]Shell   :[/dim]  {env['shell']}")
            log.write(f"[dim]OS      :[/dim]  {env['os']} / Python {env['python']}")
            log.write("[dim]Mode    :[/dim]  local")
            log.write("[dim]Status  :[/dim]  [yellow]unbound[/yellow]")

            await asyncio.sleep(0.8)
            self.app.push_screen(WelcomeScreen())

        except WorkerCancelled:
            pass
        except Exception as exc:  # noqa: BLE001
            self.notify(f"Boot sequence error: {exc}", severity="warning")
            from navig.tui.screens.welcome import WelcomeScreen

            self.app.push_screen(WelcomeScreen())

    def action_skip(self) -> None:
        from navig.tui.screens.welcome import WelcomeScreen

        self.app.push_screen(WelcomeScreen())
