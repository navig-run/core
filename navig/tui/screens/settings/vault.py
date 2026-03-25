"""
navig.tui.screens.settings.vault — VaultSettingsScreen.

All credential fields use password=True (masked display).
Bindings: ctrl+s=save, escape=cancel.
On save: posts SettingsSaved("Vault").
"""
from __future__ import annotations

from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Label

from navig.tui.messages import SettingsSaved

_MASK = "••••••••"


class VaultSettingsScreen(Screen):  # type: ignore[type-arg]
    """Vault-stored credential management (write-only)."""

    BINDINGS = [
        Binding("ctrl+s",  "save",   "Save",   show=True),
        Binding("escape",  "cancel", "Cancel", show=True),
    ]

    DEFAULT_CSS = """
    VaultSettingsScreen {
        align: center middle;
        background: #0f172a;
    }
    #vault-panel {
        width: 60;
        border: round #22d3ee;
        background: #111827;
        padding: 1 3;
    }
    #vault-title {
        color: #22d3ee;
        text-style: bold;
        margin-bottom: 1;
    }
    #vault-note {
        color: #94a3b8;
        text-style: dim;
        margin-bottom: 1;
    }
    .field-label {
        color: #94a3b8;
        margin-top: 1;
    }
    #vault-btns {
        margin-top: 2;
        align: right middle;
    }
    #vault-btns Button {
        margin: 0 1;
    }
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._has_existing = self._detect_existing()

    @staticmethod
    def _detect_existing() -> dict[str, bool]:
        """Return which vault keys already have entries."""
        result = {"openai": False, "anthropic": False, "telegram": False, "custom": False}
        try:
            from navig.tui.config_model import load_navig_json
            raw = load_navig_json() or {}
            profiles = raw.get("auth", {}).get("profiles", {})
            for key in result:
                if profiles.get(key, {}).get("vault_id"):
                    result[key] = True
        except Exception:  # noqa: BLE001
            pass
        return result

    def compose(self) -> ComposeResult:
        ex = self._has_existing

        def _placeholder(key: str, name: str) -> str:
            return f"{name} (already set — enter to replace)" if ex.get(key) else f"Paste {name} here"

        with Vertical(id="vault-panel"):
            yield Label("Vault — Credentials", id="vault-title", markup=False)
            yield Label(
                "Fields are masked. Leave blank to keep existing value.",
                id="vault-note",
                markup=False,
            )

            yield Label("OpenAI API Key", classes="field-label", markup=False)
            yield Input(
                value="",
                placeholder=_placeholder("openai", "OpenAI API Key"),
                password=True,
                id="vault-openai",
            )

            yield Label("Anthropic API Key", classes="field-label", markup=False)
            yield Input(
                value="",
                placeholder=_placeholder("anthropic", "Anthropic API Key"),
                password=True,
                id="vault-anthropic",
            )

            yield Label("Telegram Bot Token", classes="field-label", markup=False)
            yield Input(
                value="",
                placeholder=_placeholder("telegram", "Telegram Bot Token"),
                password=True,
                id="vault-telegram",
            )

            yield Label("Custom / Other Key", classes="field-label", markup=False)
            yield Input(
                value="",
                placeholder=_placeholder("custom", "Custom Key"),
                password=True,
                id="vault-custom",
            )

            with Horizontal(id="vault-btns"):
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
        fields = {
            "openai":     self.query_one("#vault-openai",    Input).value.strip(),
            "anthropic":  self.query_one("#vault-anthropic", Input).value.strip(),
            "telegram":   self.query_one("#vault-telegram",  Input).value.strip(),
            "custom":     self.query_one("#vault-custom",    Input).value.strip(),
        }

        saved_any = False
        try:
            from navig.commands.onboard import _store_in_vault
            from navig.tui.config_model import DEFAULT_CONFIG_FILE, load_navig_json
            from navig.commands.onboard import save_config

            raw = load_navig_json() or {}
            raw.setdefault("auth", {}).setdefault("profiles", {})

            for provider, value in fields.items():
                if not value:
                    continue
                vault_id = _store_in_vault(provider, "api_key", value, "api_key")
                if vault_id:
                    raw["auth"]["profiles"].setdefault(provider, {})
                    raw["auth"]["profiles"][provider]["vault_id"] = vault_id
                    saved_any = True

            if saved_any:
                save_config(raw, DEFAULT_CONFIG_FILE)

        except Exception as exc:  # noqa: BLE001
            self.notify(f"Vault save failed: {exc}", severity="error")
            return

        if saved_any:
            self.post_message(SettingsSaved("Vault"))
            self.notify("Vault credentials saved.", severity="information")
        else:
            self.notify("No values entered — nothing saved.", severity="warning")
        self.dismiss()
