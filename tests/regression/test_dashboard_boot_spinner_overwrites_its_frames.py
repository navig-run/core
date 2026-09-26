"""The Kraken boot spinner must redraw in place, not append a staircase.

`run_boot_sequence` animated each step by printing `"\\r  <spinner> <label> <bar>"` with
`end=""`. Rich strips control characters from `Text` before rendering, so the `\\r`
never reached the terminal: every frame was appended to the same line, wrapped at the
terminal width, and the boot screen was a staircase of the same label repeated ~23
times per step. It looked like that on every platform — the first showcase recording
of `navig dashboard` is how it was noticed.

The fix emits the carriage return through `console.control`, which Rich does write.
"""

from __future__ import annotations

import io

from rich.console import Console

from navig.commands import dashboard


def _boot_output(monkeypatch) -> str:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, color_system="standard", width=100)
    monkeypatch.setattr(dashboard, "console", console)
    monkeypatch.setattr(dashboard.time, "sleep", lambda *_: None)
    monkeypatch.setattr(dashboard, "_get_terminal_size", lambda: (100, 30))
    dashboard.run_boot_sequence(fast=False)
    return buf.getvalue()


def test_each_spinner_frame_returns_to_column_zero(monkeypatch) -> None:
    out = _boot_output(monkeypatch)
    frames_per_step = min(20, 100 - 30) + 3
    expected_moves = len(dashboard.BOOT_STEPS) * (frames_per_step + 1)
    # CSI 1 G = "cursor to column 1" — what Control.move_to_column(0) emits.
    assert out.count("\x1b[1G") == expected_moves, out.count("\x1b[1G")


def test_no_step_label_is_repeated_on_one_visual_line(monkeypatch) -> None:
    """Between two carriage returns there is exactly one label — no staircase."""
    out = _boot_output(monkeypatch)
    _icon, label = dashboard.BOOT_STEPS[1]
    segments = out.split("\x1b[1G")
    assert all(seg.count(label) <= 1 for seg in segments), "a frame carried more than one label"
