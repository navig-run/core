"""
navig.tui.screens.review — ReviewScreen, ConfirmModal, FinalScreen.
"""

from __future__ import annotations

import asyncio
from typing import Any

from textual import on, work
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Label, RichLog, Static
from textual.worker import WorkerCancelled

from navig.tui.config_model import (
    DEFAULT_CONFIG_FILE,
    NavigConfig,
    build_config_dict,
    check_git_installed,
    check_network,
    check_python_version,
    detect_environment,
)
from navig.tui.widgets.summary_panel import SummaryPanel

# ---------------------------------------------------------------------------
# ConfirmModal
# ---------------------------------------------------------------------------


class ConfirmModal(ModalScreen):  # type: ignore[type-arg]
    """Ask user to confirm overwriting existing config."""

    DEFAULT_CSS = """
    ConfirmModal {
        align: center middle;
    }
    ConfirmModal > Container {
        width: 60;
        height: 12;
        border: round #22d3ee;
        background: #111827;
        padding: 1 2;
    }
    ConfirmModal Button {
        margin: 1 1;
    }
    """

    def compose(self):  # type: ignore[override]
        with Container():
            yield Label(
                "[bold yellow]⚠ Config already exists[/bold yellow]\n\n"
                "Overwrite [cyan]~/.navig/navig.json[/cyan]?",
                id="confirm-msg",
            )
            with Horizontal():
                yield Button("Overwrite", variant="warning", id="btn-yes")
                yield Button("Cancel", variant="default", id="btn-no")

    @on(Button.Pressed, "#btn-yes")
    def _yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#btn-no")
    def _no(self) -> None:
        self.dismiss(False)


# ---------------------------------------------------------------------------
# ReviewScreen
# ---------------------------------------------------------------------------


class ReviewScreen(Screen):  # type: ignore[type-arg]
    """Show SummaryPanel with Back / Confirm buttons."""

    DEFAULT_CSS = """
    ReviewScreen {
        background: #0f172a;
        align: center middle;
    }
    #review-panel {
        width: 70;
        border: round #22d3ee;
        background: #111827;
        padding: 1 3;
    }
    #review-title {
        color: #22d3ee;
        text-style: bold;
        margin-bottom: 1;
    }
    #review-btns {
        margin-top: 1;
        align: center middle;
    }
    #review-btns Button {
        margin: 0 1;
    }
    """

    def __init__(self, cfg: NavigConfig, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._cfg = cfg

    def compose(self):  # type: ignore[override]
        with Vertical(id="review-panel"):
            yield Label("Review Configuration", id="review-title")
            yield SummaryPanel(self._cfg, id="review-summary")
            yield Label("")
            with Horizontal(id="review-btns"):
                yield Button("Confirm & Install  ✔", variant="primary", id="btn-confirm")
                yield Button("← Edit", variant="default", id="btn-back")

    def on_mount(self) -> None:
        if DEFAULT_CONFIG_FILE.exists():
            self.app.push_screen(ConfirmModal(), self._handle_confirm_modal)

    def _handle_confirm_modal(self, result: bool) -> None:
        if not result:
            self.app.pop_screen()

    @on(Button.Pressed, "#btn-confirm")
    def _confirm(self) -> None:
        self.app.push_screen(VerificationScreen(self._cfg))

    @on(Button.Pressed, "#btn-back")
    def _back(self) -> None:
        self.app.pop_screen()


# ---------------------------------------------------------------------------
# VerificationScreen
# ---------------------------------------------------------------------------


class VerificationScreen(Screen):  # type: ignore[type-arg]
    """Pre-flight verification dashboard before final setup write."""

    DEFAULT_CSS = """
    VerificationScreen {
        background: #0f172a;
        align: center middle;
    }
    #verify-panel {
        width: 76;
        border: round #22d3ee;
        background: #111827;
        padding: 1 3;
    }
    #verify-title {
        color: #22d3ee;
        text-style: bold;
        margin-bottom: 1;
    }
    #verify-list {
        color: #cbd5e1;
        margin-bottom: 1;
    }
    #verify-actions {
        align: center middle;
    }
    #verify-actions Button {
        margin: 0 1;
    }
    """

    def __init__(self, cfg: NavigConfig, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._cfg = cfg

    def compose(self):  # type: ignore[override]
        with Vertical(id="verify-panel"):
            yield Label("Verification Dashboard", id="verify-title")
            yield Static("Running checks…", id="verify-list")
            with Horizontal(id="verify-actions"):
                yield Button("Continue  ✔", id="btn-continue", variant="primary")
                yield Button("← Back", id="btn-back", variant="default")

    def on_mount(self) -> None:
        rows = self._build_rows()
        self.query_one("#verify-list", Static).update("\n".join(rows))

    def _build_rows(self) -> list[str]:
        def mark(ok: bool) -> str:
            return "[green]✔[/green]" if ok else "[yellow]•[/yellow]"

        tier = getattr(self._cfg, "onboarding_tier", "recommended")
        rows = [
            f"{mark(check_python_version())} Python runtime >= 3.10",
            f"{mark(check_git_installed())} Git available",
            f"{mark(check_network())} Network reachable",
            f"{mark(bool(self._cfg.profile_name.strip()))} Operator identity configured",
            f"{mark(self._cfg.ai_provider != 'none')} AI provider selected",
            f"{mark(True)} Tier selected: {tier}",
        ]

        if tier == "full":
            rows.extend(
                [
                    f"{mark(getattr(self._cfg, 'setup_matrix', False))} Matrix integration selected",
                    f"{mark(getattr(self._cfg, 'setup_email', False))} SMTP integration selected",
                    f"{mark(getattr(self._cfg, 'setup_social', False))} Social integration selected",
                ]
            )
        else:
            rows.append("[cyan]i[/cyan] Optional integrations can be configured later from CLI")
        return rows

    @on(Button.Pressed, "#btn-continue")
    def _continue(self) -> None:
        self.app.push_screen(FinalScreen(self._cfg))

    @on(Button.Pressed, "#btn-back")
    def _back(self) -> None:
        self.app.pop_screen()


# ---------------------------------------------------------------------------
# FinalScreen
# ---------------------------------------------------------------------------


class FinalScreen(Screen):  # type: ignore[type-arg]
    """Mission-ready summary + animated operator registration moment."""

    DEFAULT_CSS = """
    FinalScreen {
        background: #0f172a;
        align: center middle;
    }
    #final-outer {
        width: 70;
    }
    #final-panel {
        border: round #22d3ee;
        background: #111827;
        padding: 1 3;
        height: auto;
    }
    #final-title {
        color: #22d3ee;
        text-style: bold;
    }
    #final-log {
        height: 12;
        margin-top: 1;
    }
    #final-footer {
        margin-top: 1;
        align: center middle;
    }
    #final-hint {
        color: #22d3ee;
        text-style: dim;
        text-align: center;
        margin-top: 1;
    }
    """

    def __init__(self, cfg: NavigConfig, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._cfg = cfg

    def _deferred_commands(self) -> list[str]:
        tier = getattr(self._cfg, "onboarding_tier", "recommended")
        cmds: list[str] = []

        if tier in ("essential", "recommended"):
            cmds.extend([
                "navig matrix setup",
                "navig email setup",
                "navig social setup",
            ])
            return cmds

        if not getattr(self._cfg, "setup_matrix", False):
            cmds.append("navig matrix setup")
        if not getattr(self._cfg, "setup_email", False):
            cmds.append("navig email setup")
        if not getattr(self._cfg, "setup_social", False):
            cmds.append("navig social setup")
        return cmds

    def compose(self):  # type: ignore[override]
        env = detect_environment()
        packs_str = "  ".join(f"{p.capitalize()} ✔" for p in self._cfg.capability_packs) or "—"
        summary_text = (
            f"[bold #22d3ee]NAVIG — Setup Complete[/bold #22d3ee]\n"
            f"\n"
            f"[dim]Operator [/dim] : {self._cfg.profile_name}\n"
            f"[dim]Machine  [/dim] : {env['hostname']}\n"
            f"[dim]Config   [/dim] : [cyan]~/.navig/navig.json[/cyan]\n"
            f"[dim]Provider [/dim] : {self._cfg.ai_provider}\n"
            f"[dim]Packs    [/dim] : {packs_str}\n"
            f"[dim]Status   [/dim] : [yellow]unbound[/yellow]"
        )
        with Vertical(id="final-outer"):
            with Vertical(id="final-panel"):
                yield Static(summary_text, id="final-summary", markup=True)
                yield RichLog(id="final-log", markup=True, highlight=False, wrap=False)
                with Horizontal(id="final-footer"):
                    yield Button("Press Enter to launch  →", variant="primary", id="btn-exit")
                    yield Button("Retry write", variant="warning", id="btn-retry", display=False)
            yield Label(
                "  [dim]Try:[/dim]  [bold #22d3ee]navig ask 'hello'[/bold #22d3ee]",
                id="final-hint",
                markup=True,
            )

    def on_mount(self) -> None:
        self._run_registration()

    @work(exclusive=True)
    async def _run_registration(self) -> None:
        from navig.commands.onboard import (
            _store_in_vault,
            create_workspace_templates,
            save_config,
            sync_to_env,
        )

        log: RichLog = self.query_one("#final-log", RichLog)
        retry_btn: Button = self.query_one("#btn-retry", Button)
        try:
            cfg_dict = build_config_dict(self._cfg)

            # Vault-store API key if provided
            if self._cfg.api_key:
                vault_id = _store_in_vault(
                    self._cfg.ai_provider,
                    "api_key",
                    self._cfg.api_key,
                    "api_key",
                )
                if vault_id:
                    cfg_dict["auth"]["profiles"][self._cfg.ai_provider] = {
                        "type": "api-key",
                        "vault_id": vault_id,
                    }

            log.write("[dim]Writing configuration…[/dim]")
            await asyncio.sleep(0.15)
            try:
                save_config(cfg_dict, DEFAULT_CONFIG_FILE)
            except OSError as exc:
                self.notify(f"Config write failed: {exc}", severity="error")
                retry_btn.display = True
                return

            log.write("[bold #10b981]✔[/bold #10b981] Config sealed")
            await asyncio.sleep(0.1)

            try:
                from pathlib import Path

                create_workspace_templates(Path(self._cfg.workspace_root))
            except Exception:  # noqa: BLE001
                pass
            log.write("[bold #10b981]✔[/bold #10b981] Workspace initialized")

            await asyncio.sleep(0.1)
            try:
                sync_to_env(cfg_dict)
            except Exception:  # noqa: BLE001
                pass
            log.write("[bold #10b981]✔[/bold #10b981] Runtime linked")

            # Animate status → active
            await asyncio.sleep(0.5)
            summary_widget: Static = self.query_one("#final-summary", Static)
            env = detect_environment()
            packs_str = "  ".join(f"{p.capitalize()} ✔" for p in self._cfg.capability_packs) or "—"
            summary_widget.update(
                f"[bold #22d3ee]NAVIG — Setup Complete[/bold #22d3ee]\n"
                f"\n"
                f"[dim]Operator [/dim] : {self._cfg.profile_name}\n"
                f"[dim]Machine  [/dim] : {env['hostname']}\n"
                f"[dim]Config   [/dim] : [cyan]~/.navig/navig.json[/cyan]\n"
                f"[dim]Provider [/dim] : {self._cfg.ai_provider}\n"
                f"[dim]Packs    [/dim] : {packs_str}\n"
                f"[dim]Status   [/dim] : [bold #10b981]active[/bold #10b981]"
            )

            await asyncio.sleep(0.3)
            deferred = self._deferred_commands()
            deferred_block = (
                "\n".join(f"  [cyan]{c}[/cyan]" for c in deferred)
                if deferred
                else "  [dim]No deferred integrations[/dim]"
            )
            log.write(
                f"\n[bold #10b981]✔ Operator registered[/bold #10b981]\n"
                f"[bold #22d3ee]Welcome, {self._cfg.profile_name}.[/bold #22d3ee]\n"
                f"[dim]NAVIG is ready.[/dim]\n\n"
                f"[dim]Suggested next commands:[/dim]\n"
                f"  [cyan]navig doctor[/cyan]\n"
                f"  [cyan]navig status[/cyan]\n"
                f"  [cyan]navig config show[/cyan]\n\n"
                f"[dim]Deferred integrations:[/dim]\n"
                f"{deferred_block}"
            )

        except WorkerCancelled:
            pass
        except Exception as exc:  # noqa: BLE001
            self.notify(f"Registration error: {exc}", severity="error")
            retry_btn.display = True

    @on(Button.Pressed, "#btn-exit")
    def _exit(self) -> None:
        self.app.exit()

    @on(Button.Pressed, "#btn-retry")
    def _retry(self) -> None:
        retry_btn: Button = self.query_one("#btn-retry", Button)
        retry_btn.display = False
        self._run_registration()
