"""The dashboard's data panels must get rows at every terminal size it supports.

The first dashboard showed a 14-row mascot that squeezed the data panels beside it
to their borders at 28–34 rows — three configured hosts, an empty "Remote Hosts"
box (seen in the first showcase recording). The rebuilt dashboard has no fixed-size
decoration in the data column; this pins that every data panel keeps ≥ 6 rows at
every size the layout claims to support, and that the identity column appears only
when there is width for it.

Assert on RENDERED regions, not on constants.
"""

from __future__ import annotations

import pytest
from rich.console import Console

from navig.commands import dashboard

DATA_PANELS = ("services", "safety", "hosts", "activity")
MIN_DATA_ROWS = 6  # border + at least four rows of content


def _region_heights(rows: int, cols: int) -> dict[str, int]:
    layout = dashboard.create_layout(cols=cols, rows=rows)
    console = Console(width=cols, height=rows, force_terminal=True, file=None)
    regions = layout.render(console, console.options.update(height=rows))
    return {lay.name: r.region.height for lay, r in regions.items() if lay.name}


@pytest.mark.parametrize("cols", [72, 80, 100, 120, 160])
@pytest.mark.parametrize("rows", [24, 28, 30, 34, 40, 50])
def test_every_data_panel_gets_rows_in_the_grid_layouts(rows: int, cols: int) -> None:
    heights = _region_heights(rows, cols)
    starved = {n: heights.get(n, 0) for n in DATA_PANELS if heights.get(n, 0) < MIN_DATA_ROWS}
    assert not starved, f"at {cols}x{rows} these panels collapsed: {starved}"


@pytest.mark.parametrize("rows", [30, 40])
def test_the_stacked_layout_keeps_every_panel_when_tall_enough(rows: int) -> None:
    heights = _region_heights(rows, 60)
    assert all(heights.get(n, 0) >= MIN_DATA_ROWS for n in DATA_PANELS), heights


def test_the_identity_column_appears_only_when_there_is_width_for_it() -> None:
    assert "identity" in _region_heights(30, dashboard.WIDE_COLS)
    assert "identity" not in _region_heights(30, dashboard.WIDE_COLS - 1)
