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


class TestEphemeralRequest:
    """A caller passing 0 wants ANY free port -- there is nothing to heal away from.

    Expanding it produced ``[0, 1, 2, 3, 4, 5, <last_bound>, 0]``: ports 1-5 are privileged
    and meaningless, and ``last_bound`` comes from the discovery file, which on a developer's
    machine is **their live gateway**. Measured before the fix:
    ``_bind_candidates(0, read_gateway_discovery()[0])`` returned
    ``[0, 1, 2, 3, 4, 5, 8789, 0]`` -- the operator's own daemon offered as a fallback.

    ``tests/e2e/test_gateway_api.py`` asks for exactly this ephemeral bind, and its docstring
    warns that a foreign gateway answering on the port it asserts against is "a silent false
    PASS that proves nothing about the gateway this test started".
    """

    def test_zero_asks_the_os_and_nothing_else(self):
        assert _bind_candidates(0, None) == [0]

    def test_zero_never_offers_the_sticky_port(self):
        candidates = _bind_candidates(0, 8789)
        assert candidates == [0], (
            f"an ephemeral request expanded to {candidates}; the 8789 there is whatever "
            "gateway bound here last -- potentially the operator's live daemon"
        )

    def test_a_real_preference_still_self_heals(self):
        """The fix must not disarm the behaviour this function exists for."""
        assert _bind_candidates(8789, 9000) == [
            8789, 8790, 8791, 8792, 8793, 8794, 9000, 0,
        ]

