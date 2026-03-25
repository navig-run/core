"""
navig.tui.screens.settings.gateway — GatewaySettingsScreen.

Edits gateway configuration: webhook URL, Telegram bot token,
webhook secret, and enabled flag.
Bindings: ctrl+s=save, escape=cancel.
On save: posts SettingsSaved("Gateway").
"""

from __future__ import annotations

from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Switch

from navig.tui.messages import SettingsSaved


class GatewaySettingsScreen(Screen):  # type: ignore[type-arg]
    """Gateway / Telegram channel settings."""

    BINDINGS = [
        Binding("ctrl+s", "save", "Save", show=True),
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    DEFAULT_CSS = """
    GatewaySettingsScreen {
        align: center middle;
        background: #0f172a;
    }
    #gw-panel {
        width: 60;
        border: round #22d3ee;
        background: #111827;
        padding: 1 3;
    }
    #gw-title {
        color: #22d3ee;
        text-style: bold;
        margin-bottom: 1;
    }
    .field-label {
        color: #94a3b8;
        margin-top: 1;
    }
    #gw-btns {
        margin-top: 2;
        align: right middle;
    }
    #gw-btns Button {
        margin: 0 1;
    }
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._initial = self._load()

    @staticmethod
    def _load() -> dict:
        try:
            from navig.tui.config_model import load_navig_json

            raw = load_navig_json() or {}
            gw = raw.get("gateway", {})
            return {
                "webhook_url": gw.get("webhook_url", ""),
                "bot_token": gw.get("telegram", {}).get("bot_token", ""),
                "webhook_secret": gw.get("webhook_secret", ""),
                "enabled": bool(gw.get("enabled", False)),
            }
        except Exception:  # noqa: BLE001
            return {
                "webhook_url": "",
                "bot_token": "",
                "webhook_secret": "",
                "enabled": False,
            }

    def compose(self) -> ComposeResult:
        d = self._initial
        with Vertical(id="gw-panel"):
            yield Label("Gateway Settings", id="gw-title", markup=False)

            yield Label("Enabled", classes="field-label", markup=False)
            yield Switch(value=d["enabled"], id="gw-enabled")

            yield Label("Webhook URL", classes="field-label", markup=False)
            yield Input(
                value=d["webhook_url"], placeholder="https://…", id="gw-webhook-url"
            )

            yield Label("Telegram Bot Token", classes="field-label", markup=False)
            yield Input(
                value=d["bot_token"], placeholder="123456:ABC-…", id="gw-bot-token"
            )

            yield Label("Webhook Secret", classes="field-label", markup=False)
            yield Input(
                value=d["webhook_secret"],
                placeholder="optional shared secret",
                id="gw-webhook-secret",
            )

            with Horizontal(id="gw-btns"):
                yield Button("Save  [ctrl+s]", variant="primary", id="btn-save")
                yield Button("Cancel", variant="default", id="btn-cancel")

    def action_save(self) -> None:
        self._do_save()

    def action_cancel(self) -> None:
        self.dismiss()

    @on(Button.Pressed, "#btn-save")
    def _btn_save(self) -> None:
        self._do_save()

    @on(Button.Pressed, "#btn-cancel")
    def _btn_cancel(self) -> None:
        self.dismiss()

    def _do_save(self) -> None:
        try:
            from navig.commands.onboard import save_config
            from navig.tui.config_model import DEFAULT_CONFIG_FILE, load_navig_json

            raw = load_navig_json() or {}
            raw.setdefault("gateway", {})
            raw["gateway"]["webhook_url"] = self.query_one(
                "#gw-webhook-url", Input
            ).value.strip()
            raw["gateway"]["webhook_secret"] = self.query_one(
                "#gw-webhook-secret", Input
            ).value.strip()
            raw["gateway"]["enabled"] = self.query_one("#gw-enabled", Switch).value
            raw["gateway"].setdefault("telegram", {})
            raw["gateway"]["telegram"]["bot_token"] = self.query_one(
                "#gw-bot-token", Input
            ).value.strip()

            save_config(raw, DEFAULT_CONFIG_FILE)
            self.post_message(SettingsSaved("Gateway"))
            self.notify("Gateway settings saved.", severity="information")
        except Exception as exc:  # noqa: BLE001
            self.notify(f"Save failed: {exc}", severity="error")
            return
        self.dismiss()
