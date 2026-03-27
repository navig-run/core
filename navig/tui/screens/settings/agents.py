"""
navig.tui.screens.settings.agents — AgentSettingsScreen.

Edits agent configuration: active agent, model, agent JSON path,
and execution mode toggle.
Bindings: ctrl+s=save, escape=cancel.
On save: posts SettingsSaved("Agent").
"""

from __future__ import annotations

from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Select

from navig.tui.messages import SettingsSaved

_PROVIDERS = ["openai", "anthropic", "ollama", "groq", "mistral", "gemini", "custom"]
_MODES = ["standard", "plan-and-execute", "react", "simple"]


class AgentSettingsScreen(Screen):  # type: ignore[type-arg]
    """Agent / LLM runtime settings."""

    BINDINGS = [
        Binding("ctrl+s", "save", "Save", show=True),
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    DEFAULT_CSS = """
    AgentSettingsScreen {
        align: center middle;
        background: #0f172a;
    }
    #agent-panel {
        width: 60;
        border: round #22d3ee;
        background: #111827;
        padding: 1 3;
    }
    #agent-title {
        color: #22d3ee;
        text-style: bold;
        margin-bottom: 1;
    }
    .field-label {
        color: #94a3b8;
        margin-top: 1;
    }
    #agent-btns {
        margin-top: 2;
        align: right middle;
    }
    #agent-btns Button {
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
            ag = raw.get("agent", {})
            return {
                "provider": ag.get("provider", "openai"),
                "model": ag.get("model", ""),
                "agent_json": ag.get("agent_json_path", ""),
                "mode": ag.get("execution_mode", "standard"),
            }
        except Exception:  # noqa: BLE001
            return {
                "provider": "openai",
                "model": "",
                "agent_json": "",
                "mode": "standard",
            }

    def compose(self) -> ComposeResult:
        d = self._initial
        provider_opts = [(p, p) for p in _PROVIDERS]
        mode_opts = [(m, m) for m in _MODES]

        with Vertical(id="agent-panel"):
            yield Label("Agent Settings", id="agent-title", markup=False)

            yield Label("AI Provider", classes="field-label", markup=False)
            yield Select(
                options=provider_opts,
                value=d["provider"] if d["provider"] in _PROVIDERS else Select.BLANK,
                id="agent-provider",
            )

            yield Label("Model (leave blank for default)", classes="field-label", markup=False)
            yield Input(
                value=d["model"],
                placeholder="e.g. gpt-4o, claude-3-5-sonnet",
                id="agent-model",
            )

            yield Label(
                "Agent JSON path (optional override)",
                classes="field-label",
                markup=False,
            )
            yield Input(
                value=d["agent_json"],
                placeholder="~/.navig/agents/custom.json",
                id="agent-json-path",
            )

            yield Label("Execution Mode", classes="field-label", markup=False)
            yield Select(
                options=mode_opts,
                value=d["mode"] if d["mode"] in _MODES else Select.BLANK,
                id="agent-mode",
            )

            with Horizontal(id="agent-btns"):
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
            raw.setdefault("agent", {})

            provider_sel = self.query_one("#agent-provider", Select)
            mode_sel = self.query_one("#agent-mode", Select)

            raw["agent"]["provider"] = (
                str(provider_sel.value) if provider_sel.value is not Select.BLANK else "openai"
            )
            raw["agent"]["model"] = self.query_one("#agent-model", Input).value.strip()
            raw["agent"]["agent_json_path"] = self.query_one(
                "#agent-json-path", Input
            ).value.strip()
            raw["agent"]["execution_mode"] = (
                str(mode_sel.value) if mode_sel.value is not Select.BLANK else "standard"
            )

            save_config(raw, DEFAULT_CONFIG_FILE)
            self.post_message(SettingsSaved("Agent"))
            self.notify("Agent settings saved.", severity="information")
        except Exception as exc:  # noqa: BLE001
            self.notify(f"Save failed: {exc}", severity="error")
            return
        self.dismiss()
