"""navig.tui.widgets.step_indicator — Dot-based wizard step progress widget."""

from __future__ import annotations

from typing import List

from textual.reactive import reactive
from textual.widgets import Static


class StepIndicator(Static):
    """Renders  ● ● ○ ○ ○  step dots with a label."""

    current_step: reactive[int] = reactive(0)
    total_steps: reactive[int] = reactive(5)

    def render(self) -> str:  # type: ignore[override]
        dots: List[str] = []
        for i in range(self.total_steps):
            if i < self.current_step:
                dots.append("[bold #10b981]●[/bold #10b981]")
            elif i == self.current_step:
                dots.append("[bold #22d3ee]●[/bold #22d3ee]")
            else:
                dots.append("[#334155]○[/#334155]")
        step_num = self.current_step + 1
        labels = ["Identity", "Provider", "Runtime", "Packs", "Shell"]
        label = labels[min(self.current_step, len(labels) - 1)]
        return f"  {' '.join(dots)}   Step {step_num} / {self.total_steps} — {label}"
