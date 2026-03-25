"""navig.tui.widgets.summary_panel — Live configuration summary side-panel."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from textual.widgets import Static

if TYPE_CHECKING:
    from navig.tui.config_model import NavigConfig


class SummaryPanel(Static):
    """Live config summary panel shown alongside wizard steps."""

    DEFAULT_CSS = """
    SummaryPanel {
        border: round #22d3ee;
        background: #111827;
        padding: 1 2;
        width: 36;
        height: 100%;
        color: #94a3b8;
    }
    """

    def __init__(self, cfg: "NavigConfig", **kwargs: Any) -> None:
        super().__init__("", **kwargs)
        self._cfg = cfg
        self._status = "unbound"

    def refresh_from(self, cfg: "NavigConfig") -> None:
        self._cfg = cfg
        self.refresh()

    def set_status(self, status: str) -> None:
        self._status = status
        self.refresh()

    def render(self) -> str:  # type: ignore[override]
        cfg = self._cfg
        packs = ", ".join(cfg.capability_packs) if cfg.capability_packs else "—"
        status_color = "#10b981" if self._status == "active" else "#f59e0b"
        lines = [
            "[bold #22d3ee]── Config Preview ──[/bold #22d3ee]",
            "",
            f"[dim]Operator :[/dim]  {cfg.profile_name or '—'}",
            f"[dim]Provider :[/dim]  {cfg.ai_provider}",
            f"[dim]Runtime  :[/dim]  {'local' if cfg.local_runtime_enabled else 'cloud'}",
            f"[dim]Packs    :[/dim]  {packs}",
            f"[dim]Shell    :[/dim]  {'✔' if cfg.shell_integration else '—'}",
            f"[dim]Hooks    :[/dim]  {'✔' if cfg.git_hooks else '—'}",
            f"[dim]Telemetry:[/dim]  {'✔' if cfg.telemetry else '—'}",
            "",
            f"[dim]Status   :[/dim]  [{status_color}]{self._status}[/{status_color}]",
        ]
        return "\n".join(lines)
