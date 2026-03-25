"""navig.tui.widgets.check_row — System-check row widget."""
from __future__ import annotations

from textual.widgets import Static


class CheckRow(Static):
    """One system-check row: icon + label + optional fix hint."""

    DEFAULT_CSS = """
    CheckRow {
        height: auto;
        padding: 0 1;
    }
    """

    def __init__(self, label: str, **kwargs: None) -> None:  # type: ignore[override]
        super().__init__("", **kwargs)  # type: ignore[arg-type]
        self._label = label
        self._state = "pending"
        self._hint = ""

    def set_pending(self) -> None:
        self._state = "pending"
        self._hint = ""
        self._refresh_render()

    def set_pass(self) -> None:
        self._state = "pass"
        self._hint = ""
        self._refresh_render()

    def set_fail(self, hint: str = "") -> None:
        self._state = "fail"
        self._hint = hint
        self._refresh_render()

    def _refresh_render(self) -> None:
        icon_map = {
            "pending": "[yellow]…[/yellow]",
            "pass": "[bold #10b981]✔[/bold #10b981]",
            "fail": "[bold red]✖[/bold red]",
        }
        icon = icon_map.get(self._state, "?")
        text = f"  {icon}  {self._label}"
        if self._hint and self._state == "fail":
            text += f"\n     [dim #64748b]↳ {self._hint}[/dim #64748b]"
        self.update(text)
