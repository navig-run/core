"""Unit tests for the gateway's self-healing bind candidate ordering."""
from __future__ import annotations

from navig.gateway.server import _bind_candidates


class TestBindCandidates:
    def test_default_order_without_sticky(self):
        assert _bind_candidates(8789, None) == [8789, 8790, 8791, 8792, 8793, 8794, 0]

    def test_sticky_port_before_os_pick(self):
        # A previously self-healed port slots in after the neighbours but
        # before port 0, so restarts keep a stable URL when the whole
        # preferred range is OS-reserved.
        assert _bind_candidates(8789, 56564) == [
            8789, 8790, 8791, 8792, 8793, 8794, 56564, 0,
        ]

    def test_sticky_equal_to_preferred_not_duplicated(self):
        assert _bind_candidates(8789, 8789) == [8789, 8790, 8791, 8792, 8793, 8794, 0]

    def test_sticky_inside_neighbour_range_not_duplicated(self):
        assert _bind_candidates(8789, 8791) == [8789, 8790, 8791, 8792, 8793, 8794, 0]

    def test_invalid_sticky_ignored(self):
        for bad in (0, -1, 65536, 700000):
            assert _bind_candidates(8789, bad) == [
                8789, 8790, 8791, 8792, 8793, 8794, 0,
            ]
