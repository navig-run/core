"""The dashboard's data panels must get rows at every terminal height it supports.

`create_layout` showed the 14-row Kraken mascot from 28 rows up. Header 3 + footer 3 +
kraken 14 + tip 5 are FIXED, so at 28–34 rows the two ratio panels beside it — Remote
Hosts and Recent Ops, the ones carrying the operator's data — were squeezed to their
borders and rendered empty. Seen in the first showcase recording at 32 rows: three
configured hosts, an empty "Remote Hosts" box. The mascot now waits for 38 rows.

Assert on RENDERED regions, not on the threshold constant: the number is only right
if every data panel actually gets rows.
"""

from __future__ import annotations

import pytest
from rich.console import Console

from navig.commands import dashboard

DATA_PANELS = ("services", "tunnels", "hosts", "history")
MIN_DATA_ROWS = 6  # border + header + at least three rows of content


def _region_heights(rows: int, cols: int = 120) -> dict[str, int]:
    layout = dashboard.create_layout(cols=cols, rows=rows)
    console = Console(width=cols, height=rows, force_terminal=True, file=None)
    regions = layout.render(console, console.options.update(height=rows))
    return {lay.name: r.region.height for lay, r in regions.items() if lay.name}


@pytest.mark.parametrize("rows", [24, 28, 30, 32, 34, 36, 38, 40, 50])
def test_every_data_panel_gets_rows_at_every_supported_height(rows: int) -> None:
    heights = _region_heights(rows)
    starved = {n: h for n, h in heights.items() if n in DATA_PANELS and h < MIN_DATA_ROWS}
    assert not starved, f"at {rows} rows these panels collapsed to their borders: {starved}"


def test_the_mascot_appears_only_when_it_leaves_room_for_the_data() -> None:
    assert "kraken" not in _region_heights(32)
    assert "kraken" in _region_heights(dashboard.KRAKEN_MIN_ROWS)
    assert dashboard.KRAKEN_MIN_ROWS >= 3 + 3 + 14 + 5 + 2 * MIN_DATA_ROWS
