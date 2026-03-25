"""navig.tui.widgets.brand_hero — Animated NAVIG logo widget."""

from __future__ import annotations

from typing import Any

from textual.widgets import Static


class BrandHero(Static):
    """Animated NAVIG logo widget."""

    DEFAULT_CSS = """
    BrandHero {
        color: #22d3ee;
        text-style: bold;
        padding: 0 2;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("", **kwargs)
        self._content = ""

    def render(self) -> str:  # type: ignore[override]
        return self._content

    def set_text(self, text: str) -> None:
        self._content = text
        self.refresh()
