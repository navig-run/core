"""The hub/store state banner — the one-line wire-state summary rendered at the top
of `navig store list` / `status`. Locks order, zero-fill, and the status view's
exclusion of the AVAILABLE (catalog) count.
"""

from __future__ import annotations

from navig.commands.store import _state_banner


def test_banner_shows_every_state_with_counts():
    s = _state_banner({"wired": 5, "unwired": 2, "available": 8, "broken": 1}, include_available=True)
    assert "5 wired" in s
    assert "2 unwired" in s
    assert "8 available" in s
    assert "1 broken" in s
    # order: wired · unwired · available · broken
    assert s.index("wired") < s.index("unwired") < s.index("available") < s.index("broken")


def test_banner_omits_available_for_status_view():
    s = _state_banner({"wired": 5}, include_available=False)
    assert "available" not in s
    assert "5 wired" in s
    assert "0 unwired" in s  # missing states are zero-filled
    assert "0 broken" in s


def test_banner_zero_fills_all_missing():
    s = _state_banner({}, include_available=True)
    for word in ("0 wired", "0 unwired", "0 available", "0 broken"):
        assert word in s
