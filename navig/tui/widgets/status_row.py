"""navig.tui.widgets.status_row — Dashboard status row widget with inline CTAs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from textual.widgets import Static

if TYPE_CHECKING:
    from navig.tui.resolvers import StatusBadge


class StatusRow(Static):
    """Single status row widget for DashboardScreen.

    For error/warn rows that carry a ``deep_link``, a CTA hint is rendered
    on a second line.  Pressing the ``[→]`` key or selecting this row and
    pressing ``e`` opens the scoped settings panel.
    """

    DEFAULT_CSS = """
    StatusRow { height: auto; padding: 0 1; }
    """

    def __init__(self, badge: StatusBadge, **kwargs: Any) -> None:
        super().__init__("", **kwargs)
        self._badge = badge
        self.update_badge(badge)

    def update_badge(self, badge: StatusBadge) -> None:
        """Re-render this row with an updated badge."""
        self._badge = badge
        color = badge.color
        sym = badge.symbol

        # Prefix errors with "!" for visual prominence
        prefix = "[bold red]! [/bold red]" if badge.status == "error" else "  "
        detail = f"  [dim]{badge.detail}[/dim]" if badge.detail else ""

        # CTA hint for actionable rows
        cta = ""
        if badge.status in ("error", "warn") and badge.deep_link:
            section = badge.deep_link.replace("/settings/", "")
            cta = f"\n     [dim #22d3ee]→ [e] Edit {section} settings[/dim #22d3ee]"
        elif badge.status == "missing" and badge.deep_link:
            section = badge.deep_link.replace("/settings/", "")
            cta = f"\n     [dim #64748b]→ [e] Configure {section}[/dim #64748b]"

        self.update(
            f"  {prefix}[{color}]{sym}[/{color}]  "
            f"[white]{badge.label:<22}[/white]{detail}{cta}"
        )

    @property
    def badge(self) -> StatusBadge:
        return self._badge

    @property
    def deep_link(self) -> str:
        return self._badge.deep_link
