"""
navig.tui.screens.settings.scheduler — SchedulerSettingsScreen.

Edits scheduler configuration: enabled toggle, cron expression,
max concurrent tasks, and retry policy.
Bindings: ctrl+s=save, escape=cancel.
On save: posts SettingsSaved("Scheduler").
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


class SchedulerSettingsScreen(Screen):  # type: ignore[type-arg]
    """Scheduler / task runner settings."""

    BINDINGS = [
        Binding("ctrl+s",  "save",   "Save",   show=True),
        Binding("escape",  "cancel", "Cancel", show=True),
    ]

    DEFAULT_CSS = """
    SchedulerSettingsScreen {
        align: center middle;
        background: #0f172a;
    }
    #sched-panel {
        width: 60;
        border: round #22d3ee;
        background: #111827;
        padding: 1 3;
    }
    #sched-title {
        color: #22d3ee;
        text-style: bold;
        margin-bottom: 1;
    }
    .field-label {
        color: #94a3b8;
        margin-top: 1;
    }
    #sched-btns {
        margin-top: 2;
        align: right middle;
    }
    #sched-btns Button {
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
            sc = raw.get("scheduler", {})
            return {
                "enabled":     bool(sc.get("enabled", False)),
                "cron":        sc.get("cron", "*/5 * * * *"),
                "max_concurrent": str(sc.get("max_concurrent", "4")),
                "retry_limit": str(sc.get("retry_limit", "3")),
                "retry_delay": str(sc.get("retry_delay_seconds", "60")),
            }
        except Exception:  # noqa: BLE001
            return {
                "enabled": False,
                "cron": "*/5 * * * *",
                "max_concurrent": "4",
                "retry_limit": "3",
                "retry_delay": "60",
            }

    def compose(self) -> ComposeResult:
        d = self._initial
        with Vertical(id="sched-panel"):
            yield Label("Scheduler Settings", id="sched-title", markup=False)

            yield Label("Enabled", classes="field-label", markup=False)
            yield Switch(value=d["enabled"], id="sched-enabled")

            yield Label("Default Cron Expression", classes="field-label", markup=False)
            yield Input(value=d["cron"], placeholder="*/5 * * * *", id="sched-cron")

            yield Label("Max Concurrent Tasks", classes="field-label", markup=False)
            yield Input(value=d["max_concurrent"], placeholder="4", id="sched-max")

            yield Label("Retry Limit", classes="field-label", markup=False)
            yield Input(value=d["retry_limit"], placeholder="3", id="sched-retry-limit")

            yield Label("Retry Delay (seconds)", classes="field-label", markup=False)
            yield Input(value=d["retry_delay"], placeholder="60", id="sched-retry-delay")

            with Horizontal(id="sched-btns"):
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
            from navig.tui.config_model import DEFAULT_CONFIG_FILE, load_navig_json
            from navig.commands.onboard import save_config

            raw = load_navig_json() or {}
            raw.setdefault("scheduler", {})

            raw["scheduler"]["enabled"] = self.query_one("#sched-enabled", Switch).value

            cron = self.query_one("#sched-cron", Input).value.strip()
            if cron:
                raw["scheduler"]["cron"] = cron

            max_str  = self.query_one("#sched-max", Input).value.strip()
            rlim_str = self.query_one("#sched-retry-limit", Input).value.strip()
            rdel_str = self.query_one("#sched-retry-delay", Input).value.strip()

            if max_str.isdigit():
                raw["scheduler"]["max_concurrent"] = int(max_str)
            if rlim_str.isdigit():
                raw["scheduler"]["retry_limit"] = int(rlim_str)
            if rdel_str.isdigit():
                raw["scheduler"]["retry_delay_seconds"] = int(rdel_str)

            save_config(raw, DEFAULT_CONFIG_FILE)
            self.post_message(SettingsSaved("Scheduler"))
            self.notify("Scheduler settings saved.", severity="information")
        except Exception as exc:  # noqa: BLE001
            self.notify(f"Save failed: {exc}", severity="error")
            return
        self.dismiss()
