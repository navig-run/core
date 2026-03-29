"""navig.tui.widgets.step_indicator — Dot-based wizard step progress widget."""

from __future__ import annotations

from textual.reactive import reactive
from textual.widgets import Static


class StepIndicator(Static):
    """Renders  ● ● ○ ○ ○  step dots with a label."""

    current_step: reactive[int] = reactive(0)
    total_steps: reactive[int] = reactive(5)
    step_labels: reactive[list[str]] = reactive(
        ["Identity", "Provider", "Runtime", "Packs", "Shell", "Integrations"]
    )

    def render(self) -> str:  # type: ignore[override]
        dots: list[str] = []
        for i in range(self.total_steps):
            if i < self.current_step:
                dots.append("[bold #10b981]✔[/bold #10b981]")
            elif i == self.current_step:
                dots.append("[bold #22d3ee]●[/bold #22d3ee]")
            else:
                dots.append("[#334155]○[/#334155]")
        step_num = self.current_step + 1
        labels = self.step_labels or ["Step"]
        label = labels[min(self.current_step, len(labels) - 1)]
        pct = int((step_num / max(self.total_steps, 1)) * 100)
        return (
            f"  {' '.join(dots)}   "
            f"Step {step_num}/{self.total_steps}  [{pct:>3}%] — {label}"
        )
