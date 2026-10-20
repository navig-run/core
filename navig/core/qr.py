"""Dependency-free QR Code encoder → inline SVG.

Why this exists: the LAN join page used a third-party QR web service, which
sent the operator's LAN gateway URL to the internet and showed a broken image
offline. ``qrcode`` is only an optional extra, so core cannot rely on it.

Scope is deliberately small: byte mode, error-correction level M, versions
1-10 (up to 213 bytes — far more than any URL this needs). The algorithm is
the standard one (ISO/IEC 18004): data → Reed-Solomon blocks → interleave →
place in zig-zag → pick the lowest-penalty of the 8 masks. Matrices are
verified module-for-module against the reference ``qrcode`` library in
``tests/core/test_qr.py``.
"""

from __future__ import annotations

# Error-correction level M, indexed by version (index 0 unused).
_ECC_CODEWORDS_PER_BLOCK = (0, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26)
_NUM_ECC_BLOCKS = (0, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5)
_FORMAT_BITS_M = 0  # ECC level M's 2-bit format indicator
MAX_VERSION = 10

_MASKS = (
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
)


class QrTooLong(ValueError):
    """The payload does not fit in a version-10 QR code at level M."""


# ─────────────────────────── capacity ────────────────────────────────


def _num_raw_data_modules(ver: int) -> int:
    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        numalign = ver // 7 + 2
        result -= (25 * numalign - 10) * numalign - 55
        if ver >= 7:
            result -= 36
    return result


def _num_data_codewords(ver: int) -> int:
    return _num_raw_data_modules(ver) // 8 - _ECC_CODEWORDS_PER_BLOCK[ver] * _NUM_ECC_BLOCKS[ver]


# ─────────────────────────── Reed-Solomon ────────────────────────────


def _rs_multiply(x: int, y: int) -> int:
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _rs_multiply(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _rs_multiply(root, 0x02)
    return result


def _rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _rs_multiply(coef, factor)
    return result


def _add_ecc_and_interleave(data: list[int], ver: int) -> list[int]:
    numblocks = _NUM_ECC_BLOCKS[ver]
    blockecclen = _ECC_CODEWORDS_PER_BLOCK[ver]
    rawcodewords = _num_raw_data_modules(ver) // 8
    numshortblocks = numblocks - rawcodewords % numblocks
    shortblocklen = rawcodewords // numblocks
    divisor = _rs_divisor(blockecclen)
    blocks: list[list[int]] = []
    k = 0
    for i in range(numblocks):
        take = shortblocklen - blockecclen + (0 if i < numshortblocks else 1)
        dat = data[k : k + take]
        k += take
        ecc = _rs_remainder(dat, divisor)
        if i < numshortblocks:
            dat.append(0)  # placeholder so all blocks share a length while interleaving
        blocks.append(dat + ecc)
    result: list[int] = []
    for i in range(len(blocks[0])):
        for j, blk in enumerate(blocks):
            if i != shortblocklen - blockecclen or j >= numshortblocks:
                result.append(blk[i])
    return result


# ─────────────────────────── data encoding ───────────────────────────


def _encode_codewords(payload: bytes, ver: int) -> list[int]:
    bits: list[int] = []

    def put(value: int, length: int) -> None:
        bits.extend((value >> i) & 1 for i in reversed(range(length)))

    put(0b0100, 4)  # byte mode
    put(len(payload), 8 if ver <= 9 else 16)
    for b in payload:
        put(b, 8)
    capacity = _num_data_codewords(ver) * 8
    put(0, min(4, capacity - len(bits)))  # terminator
    put(0, (8 - len(bits) % 8) % 8)
    codewords = [int("".join(map(str, bits[i : i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(codewords) < capacity // 8:
        codewords.append(pad)
        pad ^= 0xEC ^ 0x11
    return codewords


def _pick_version(payload: bytes) -> int:
    for ver in range(1, MAX_VERSION + 1):
        header_bits = 4 + (8 if ver <= 9 else 16)
        if header_bits + 8 * len(payload) <= _num_data_codewords(ver) * 8:
            return ver
    raise QrTooLong(f"{len(payload)} bytes do not fit a version-{MAX_VERSION} QR code")


# ─────────────────────────── matrix ──────────────────────────────────


class _Matrix:
    def __init__(self, ver: int) -> None:
        self.ver = ver
        self.size = ver * 4 + 17
        self.modules = [[False] * self.size for _ in range(self.size)]
        self.is_function = [[False] * self.size for _ in range(self.size)]

    def set_function(self, x: int, y: int, dark: bool) -> None:
        self.modules[y][x] = dark
        self.is_function[y][x] = True

    def alignment_positions(self) -> list[int]:
        if self.ver == 1:
            return []
        numalign = self.ver // 7 + 2
        step = (self.ver * 8 + numalign * 3 + 5) // (numalign * 4 - 4) * 2
        result = [self.size - 7 - i * step for i in range(numalign - 1)] + [6]
        return list(reversed(result))

    def draw_function_patterns(self) -> None:
        for i in range(self.size):
            self.set_function(6, i, i % 2 == 0)
            self.set_function(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (self.size - 4, 3), (3, self.size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    xx, yy = cx + dx, cy + dy
                    if 0 <= xx < self.size and 0 <= yy < self.size:
                        self.set_function(xx, yy, max(abs(dx), abs(dy)) not in (2, 4))
        positions = self.alignment_positions()
        last = len(positions) - 1
        for i, ax in enumerate(positions):
            for j, ay in enumerate(positions):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set_function(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format_bits(0)  # reserve; redrawn with the chosen mask
        self.draw_version()

    def draw_format_bits(self, mask: int) -> None:
        data = _FORMAT_BITS_M << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412

        def bit(i: int) -> bool:
            return (bits >> i) & 1 != 0

        for i in range(6):
            self.set_function(8, i, bit(i))
        self.set_function(8, 7, bit(6))
        self.set_function(8, 8, bit(7))
        self.set_function(7, 8, bit(8))
        for i in range(9, 15):
            self.set_function(14 - i, 8, bit(i))
        for i in range(8):
            self.set_function(self.size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set_function(8, self.size - 15 + i, bit(i))
        self.set_function(8, self.size - 8, True)  # the always-dark module

    def draw_version(self) -> None:
        if self.ver < 7:
            return
        rem = self.ver
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = self.ver << 12 | rem
        for i in range(18):
            dark = (bits >> i) & 1 != 0
            a = self.size - 11 + i % 3
            b = i // 3
            self.set_function(a, b, dark)
            self.set_function(b, a, dark)

    def draw_codewords(self, data: list[int]) -> None:
        i = 0
        right = self.size - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(self.size):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = self.size - 1 - vert if upward else vert
                    if not self.is_function[y][x] and i < len(data) * 8:
                        self.modules[y][x] = (data[i >> 3] >> (7 - (i & 7))) & 1 != 0
                        i += 1
            right -= 2

    def apply_mask(self, mask: int) -> None:
        fn = _MASKS[mask]
        for y in range(self.size):
            for x in range(self.size):
                if not self.is_function[y][x] and fn(x, y):
                    self.modules[y][x] = not self.modules[y][x]

    def penalty(self) -> int:
        size = self.size
        m = self.modules
        score = 0
        # N1 — runs of ≥5 same-colour modules (rows and columns)
        for lines in (m, [list(col) for col in zip(*m)]):
            for line in lines:
                run_color, run_len = line[0], 1
                for cell in line[1:]:
                    if cell == run_color:
                        run_len += 1
                    else:
                        if run_len >= 5:
                            score += 3 + (run_len - 5)
                        run_color, run_len = cell, 1
                if run_len >= 5:
                    score += 3 + (run_len - 5)
        # N2 — 2×2 blocks of one colour
        for y in range(size - 1):
            for x in range(size - 1):
                c = m[y][x]
                if c == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    score += 3
        # N3 — finder-like 1:1:3:1:1 with four light modules on either side
        pat_a = [True, False, True, True, True, False, True, False, False, False, False]
        pat_b = pat_a[::-1]
        for lines in (m, [list(col) for col in zip(*m)]):
            for line in lines:
                for i in range(size - 10):
                    window = line[i : i + 11]
                    if window == pat_a or window == pat_b:
                        score += 40
        # N4 — dark-module proportion away from 50 %
        dark = sum(sum(row) for row in m)
        total = size * size
        k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
        score += max(k, 0) * 10
        return score


def qr_matrix(text: str | bytes, *, mask: int | None = None) -> list[list[bool]]:
    """Encode *text* and return the module matrix (``True`` = dark), no quiet zone.

    ``mask`` forces one of the 8 masks (tests); by default the lowest-penalty
    mask is chosen, as the standard specifies.
    """
    payload = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    ver = _pick_version(payload)
    codewords = _add_ecc_and_interleave(_encode_codewords(payload, ver), ver)

    base = _Matrix(ver)
    base.draw_function_patterns()
    base.draw_codewords(codewords)

    candidates = range(8) if mask is None else (mask,)
    best: _Matrix | None = None
    best_score = -1
    for msk in candidates:
        trial = _Matrix(ver)
        trial.modules = [row[:] for row in base.modules]
        trial.is_function = base.is_function
        trial.apply_mask(msk)
        trial.draw_format_bits(msk)
        score = trial.penalty()
        if best is None or score < best_score:
            best, best_score = trial, score
    if best is None:  # unreachable: ``candidates`` is never empty
        raise RuntimeError("no QR mask was evaluated")
    return best.modules


def qr_svg(
    text: str | bytes,
    *,
    dark: str = "#0e0f12",
    light: str = "#ffffff",
    quiet_zone: int = 4,
    title: str = "QR code",
) -> str:
    """Render *text* as a crisp, scalable inline ``<svg>`` (one ``<path>``)."""
    matrix = qr_matrix(text)
    size = len(matrix) + quiet_zone * 2
    parts = []
    for y, row in enumerate(matrix):
        x = 0
        while x < len(row):
            if row[x]:
                start = x
                while x < len(row) and row[x]:
                    x += 1
                parts.append(f"M{start + quiet_zone} {y + quiet_zone}h{x - start}v1h-{x - start}z")
            else:
                x += 1
    from html import escape

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'shape-rendering="crispEdges" role="img" aria-label="{escape(title)}">'
        f"<title>{escape(title)}</title>"
        f'<rect width="{size}" height="{size}" fill="{light}"/>'
        f'<path fill="{dark}" d="{"".join(parts)}"/>'
        "</svg>"
    )
