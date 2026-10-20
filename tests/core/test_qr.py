"""navig.core.qr — module-exact against the reference ``qrcode`` library.

The golden hashes below were produced by ``qrcode`` (byte mode, level M, a
forced mask) and cover the structurally distinct versions: 1 (no alignment),
3 (one alignment), 7 (version-info bits appear) and 10 (16-bit length field).
A drift in ANY module — data, ECC, interleave, format or version bits — moves
the hash. Payloads use RFC 5737 documentation addresses only.
"""

from __future__ import annotations

import hashlib

import pytest

from navig.core.qr import QrTooLong, _pick_version, qr_matrix, qr_svg

_ALPHA = lambda n: bytes(65 + i % 26 for i in range(n))  # noqa: E731

GOLDEN = {
    "url": (b"http://192.0.2.10:8790/install", 3, [
        "1fe3434e0ae55b20", "aafe60b479141d57", "e6ec3c92ee566944", "1925a056eca14fcd",
        "809cbec7abad6376", "4062671432ae33c0", "ae70eacdcc9ec0f5", "dea53c4239df9b39",
    ]),
    "one": (b"A", 1, [
        "2bae48594b732587", "43f688a881761c91", "1df28de52a4a3bd0", "bc9009ae87ca68f1",
        "177b3a1897a152ae", "a6fca46e028f9962", "90e985ca4eb58895", "23eee31305a65603",
    ]),
    "v7": (_ALPHA(115), 7, [
        "55963f6c71f476d1", "70a56eed90da3395", "5eb4cadc4b3dc72e", "61b4134b8b2d9c5d",
        "bc916929b15b3e8b", "fd961716be508a0d", "58b82c245ae9151d", "ce2bdcb80558db16",
    ]),
    "v10": (_ALPHA(210), 10, [
        "c2f884495259d483", "5d4de3c7f4d06232", "43e8f9009bc76cd1", "827dc6fa55b3b68d",
        "cf5e6b901c72b2bf", "3d9c8ce851fc4f1e", "303cc5d367fe0e8a", "eb4ada328ee75eff",
    ]),
}


def _digest(matrix: list[list[bool]]) -> str:
    bits = "\n".join("".join("1" if c else "0" for c in row) for row in matrix)
    return hashlib.sha256(bits.encode()).hexdigest()[:16]


@pytest.mark.parametrize("case", sorted(GOLDEN))
def test_every_mask_matches_the_reference_library(case: str) -> None:
    payload, version, hashes = GOLDEN[case]
    assert _pick_version(payload) == version
    for mask, expected in enumerate(hashes):
        matrix = qr_matrix(payload, mask=mask)
        assert len(matrix) == version * 4 + 17
        assert _digest(matrix) == expected, f"{case}: mask {mask} drifted from qrcode"


def test_auto_mask_is_one_of_the_valid_encodings() -> None:
    payload, _, hashes = GOLDEN["url"]
    assert _digest(qr_matrix(payload)) in hashes


def test_finder_patterns_sit_in_three_corners() -> None:
    m = qr_matrix("http://192.0.2.10:8790/install")
    n = len(m)
    for ox, oy in ((0, 0), (n - 7, 0), (0, n - 7)):
        ring = [m[oy][ox + i] for i in range(7)] + [m[oy + 6][ox + i] for i in range(7)]
        assert all(ring), "finder outer ring must be dark"
        assert m[oy + 3][ox + 3], "finder centre must be dark"
        assert not m[oy + 1][ox + 1], "finder inner ring must be light"


def test_too_long_payload_is_refused_cleanly() -> None:
    with pytest.raises(QrTooLong):
        qr_matrix("x" * 400)


def test_svg_is_self_contained_and_scales() -> None:
    svg = qr_svg("http://192.0.2.10:8790/install", title="Join <NAVIG>")
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert 'viewBox="0 0 37 37"' in svg  # version 3 (29) + 2 × 4 quiet zone
    assert "http" not in svg.split("<path", 1)[1]  # no payload leaks into markup
    assert "&lt;NAVIG&gt;" in svg  # title is escaped
    assert "<script" not in svg and "href" not in svg
