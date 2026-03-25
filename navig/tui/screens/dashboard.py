"""
navig.tui.screens.dashboard — DashboardScreen.

Upgraded implementation:
- 11 sections from resolvers.SECTIONS (ported + 6 new)
- Errors sorted to top with '!' prefix
- StatusRow inline CTAs deep-link to scoped settings screens
- Subscribes to SettingsSaved → re-resolves changed section
- BINDINGS: i=wizard, s=settings, r=refresh, q=quit
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, Static

from navig.tui.messages import SettingsSaved
from navig.tui.resolvers import SECTIONS, StatusBadge
from navig.tui.widgets.status_row import StatusRow

# Deep-link key → import path for settings screens
_DEEPLINK_SCREENS: Dict[str, str] = {
    "/settings/gateway":   "navig.tui.screens.settings.gateway.GatewaySettingsScreen",
    "/settings/agents":    "navig.tui.screens.settings.agents.AgentSettingsScreen",
    "/settings/vault":     "navig.tui.screens.settings.vault.VaultSettingsScreen",
    "/settings/scheduler": "navig.tui.screens.settings.scheduler.SchedulerSettingsScreen",
}


def _import_screen_class(dotted: str):  # type: ignore[return]
    """Lazily import and return a Screen class from a dotted path string."""
    module_path, _, cls_name = dotted.rpartition(".")
    import importlib
    module = importlib.import_module(module_path)
    return getattr(module, cls_name)


def _build_sorted_badges(badges: Sequence[StatusBadge]) -> List[StatusBadge]:
    """Return badges with error rows first, then warn, then ok/missing."""
    errors = [b for b in badges if b.status == "error"]
    warns  = [b for b in badges if b.status == "warn"]
    others = [b for b in badges if b.status not in ("error", "warn")]
    return errors + warns + others


class DashboardScreen(Screen):  # type: ignore[type-arg]
    """
    State overview for a configured NAVIG operator.

    Mounted after setup wizard or on `navig init` when config already exists.
    """

    BINDINGS = [
        Binding("i", "start_wizard",   "Wizard",   show=True),
        Binding("s", "open_settings",  "Settings", show=True),
        Binding("r", "refresh_status", "Refresh",  show=True),
        Binding("q", "quit",           "Quit",     show=False),
        Binding("question_mark", "show_help", "Help", show=False),
    ]

    DEFAULT_CSS = """
    DashboardScreen {
        background: #0f172a;
    }

    #dash-header {
        height: 3;
        content-align: left middle;
        background: #0f172a;
        padding: 0 2;
        border-bottom: solid #1e3a5f;
    }

    #dash-title {
        color: #22d3ee;
        text-style: bold;
        width: 1fr;
    }

    #dash-operator {
        color: #94a3b8;
        text-style: dim;
    }

    #dash-body {
        padding: 1 2;
    }

    #dash-section-title {
        color: #22d3ee;
        text-style: bold;
        margin-bottom: 1;
    }

    #status-scroll {
        height: auto;
        max-height: 1fr;
    }

    #dash-actions {
        dock: bottom;
        height: 3;
        border-top: solid #1e3a5f;
        background: #0f172a;
        padding: 0 2;
        align: left middle;
    }

    #dash-actions Button {
        margin: 0 1;
    }
    """

    def __init__(
        self,
        *args: Any,
        deep_link: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._deep_link = deep_link
        # section_key -> StatusRow widget id (built on compose)
        self._row_ids: Dict[str, str] = {}
        # resolved badges cache before compose
        self._pending_badges: List[tuple[str, StatusBadge]] = []

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        operator_name = self._read_operator_name()

        with Vertical(id="dash-body"):
            with Horizontal(id="dash-header"):
                yield Label("NAVIG  ◆  Status", id="dash-title", markup=True)
                yield Label(f"  {operator_name}", id="dash-operator", markup=False)

            with ScrollableContainer(id="status-scroll"):
                yield Label("System Status", id="dash-section-title", markup=False)
                # Placeholder rows; real badges resolved on_mount via worker
                for (section_key, _resolver) in SECTIONS:
                    row_id = f"row-{section_key.lower().replace(' ', '-')}"
                    self._row_ids[section_key] = row_id
                    badge = StatusBadge(label=section_key, status="missing", detail="Loading…")
                    yield StatusRow(badge, id=row_id, key=section_key)

            with Horizontal(id="dash-actions"):
                yield Button("Wizard [i]",    variant="primary",   id="btn-wizard")
                yield Button("Settings [s]",  variant="default",   id="btn-settings")
                yield Button("Refresh [r]",   variant="default",   id="btn-refresh")
                yield Button("Quit [q]",      variant="default",   id="btn-quit")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_mount(self) -> None:
        self._resolve_all()
        if self._deep_link == "provider":
            # Deep-link from --provider flag: open wizard on provider step
            self.set_timer(0.1, self._open_wizard_provider_step)

    def _open_wizard_provider_step(self) -> None:
        from navig.tui.screens.wizard import WizardScreen
        cfg = getattr(self.app, "config", None)
        if cfg is not None:
            self.app.push_screen(WizardScreen(cfg, start_step=1))

    # ------------------------------------------------------------------
    # Resolve workers
    # ------------------------------------------------------------------

    @work(exclusive=False, thread=True)
    def _resolve_all(self) -> None:
        """Resolve each section badge in a background thread."""
        badges: list[tuple[str, StatusBadge]] = []
        for (section_key, resolver) in SECTIONS:
            try:
                badge = resolver(self._get_config())
            except Exception as exc:  # noqa: BLE001
                badge = StatusBadge(
                    label=section_key,
                    status="error",
                    detail=f"Resolver error: {exc}",
                )
            badges.append((section_key, badge))
        self.call_from_thread(self._apply_badges, badges)

    def _apply_badges(self, badges: list[tuple[str, StatusBadge]]) -> None:
        """Sort and update all StatusRow widgets on the main thread."""
        # Re-sort: errors first, then warn, then ok/missing
        section_map: Dict[str, StatusBadge] = dict(badges)
        all_keys_ordered = [k for k, _ in SECTIONS]
        sorted_badges = _build_sorted_badges([section_map[k] for k in all_keys_ordered if k in section_map])

        # Re-order DOM rows
        container: ScrollableContainer = self.query_one("#status-scroll", ScrollableContainer)
        # Update each row in sorted order by moving DOM nodes
        for badge in sorted_badges:
            row_id = self._row_ids.get(badge.label)
            if row_id:
                try:
                    row_widget: StatusRow = self.query_one(f"#{row_id}", StatusRow)
                    row_widget.update_badge(badge)
                    container.move_child(row_widget, after=-1)  # move to end (sorted pass)
                except Exception:  # noqa: BLE001
                    pass

        # Final pass: put errors truly first
        sorted_order = [self._row_ids.get(b.label) for b in sorted_badges if self._row_ids.get(b.label)]
        for idx, row_id in enumerate(sorted_order):
            try:
                row_widget: StatusRow = self.query_one(f"#{row_id}", StatusRow)
                container.move_child(row_widget, before=idx)
            except Exception:  # noqa: BLE001
                pass

    def _resolve_single(self, section_key: str) -> None:
        """Re-resolve a single section (called after SettingsSaved)."""
        resolver_map = {k: fn for k, fn in SECTIONS}
        resolver = resolver_map.get(section_key)
        if not resolver:
            return
        try:
            badge = resolver(self._get_config())
        except Exception as exc:  # noqa: BLE001
            badge = StatusBadge(label=section_key, status="error", detail=str(exc))

        row_id = self._row_ids.get(section_key)
        if row_id:
            try:
                row_widget: StatusRow = self.query_one(f"#{row_id}", StatusRow)
                row_widget.update_badge(badge)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # SettingsSaved subscription
    # ------------------------------------------------------------------

    def on_settings_saved(self, message: SettingsSaved) -> None:  # type: ignore[override]
        """Re-resolve the section that was just saved."""
        self._resolve_single(message.section_key)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_start_wizard(self) -> None:
        from navig.tui.screens.wizard import WizardScreen
        cfg = getattr(self.app, "config", None)
        if cfg is not None:
            self.app.push_screen(WizardScreen(cfg))

    def action_open_settings(self) -> None:
        from navig.tui.screens.settings.root import SettingsRootScreen
        self.app.push_screen(SettingsRootScreen())

    def action_refresh_status(self) -> None:
        self._resolve_all()

    def action_quit(self) -> None:
        self.app.exit()

    def action_show_help(self) -> None:
        self.notify(
            "[i] Wizard  [s] Settings  [r] Refresh  [q] Quit\n"
            "Highlighted row [e] → open scoped settings",
            title="Keyboard Shortcuts",
        )

    def action_edit_section(self, deep_link: str = "") -> None:
        """Push settings screen for deep_link slug."""
        if not deep_link:
            # Try to pick the focused row's deep_link
            try:
                focused = self.focused
                if isinstance(focused, StatusRow):
                    deep_link = focused.deep_link
            except Exception:  # noqa: BLE001
                pass
        if not deep_link:
            return
        cls_path = _DEEPLINK_SCREENS.get(deep_link)
        if cls_path:
            try:
                screen_cls = _import_screen_class(cls_path)
                self.app.push_screen(screen_cls())
            except Exception as exc:  # noqa: BLE001
                self.notify(f"Cannot open settings: {exc}", severity="error")
        elif deep_link == "/wizard/provider":
            from navig.tui.screens.wizard import WizardScreen
            cfg = getattr(self.app, "config", None)
            if cfg is not None:
                self.app.push_screen(WizardScreen(cfg, start_step=1))

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    @on(Button.Pressed, "#btn-wizard")
    def _btn_wizard(self) -> None:
        self.action_start_wizard()

    @on(Button.Pressed, "#btn-settings")
    def _btn_settings(self) -> None:
        self.action_open_settings()

    @on(Button.Pressed, "#btn-refresh")
    def _btn_refresh(self) -> None:
        self.action_refresh_status()

    @on(Button.Pressed, "#btn-quit")
    def _btn_quit(self) -> None:
        self.action_quit()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_config(self) -> None:
        """Return the loaded navig.json dict (may be None)."""
        try:
            from navig.tui.config_model import load_navig_json
            return load_navig_json()
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _read_operator_name() -> str:
        try:
            from navig.tui.config_model import load_navig_json
            cfg_dict = load_navig_json()
            if cfg_dict:
                return (
                    cfg_dict.get("profile", {}).get("name", "")
                    or cfg_dict.get("operator", {}).get("name", "")
                    or ""
                )
        except Exception:  # noqa: BLE001
            pass
        return ""
