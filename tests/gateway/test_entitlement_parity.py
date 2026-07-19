"""The `@navig/bay` SDK mirrors the daemon's entitlement rules — guard the drift.

`packages/shared/bay-sdk/src/entitlement.ts` re-implements the daemon's
`_TIER_RANK` (and unlock logic) in TypeScript so surfaces can reason about
unlock state client-side. If the two diverge, the UI shows a lock state that
disagrees with what the daemon actually enforces. This pure-Python test parses
the TS `TIER_RANK` literal and asserts it equals the daemon's — no Node needed.
Skips cleanly when the SDK isn't checked out (e.g. a core-only sdist).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from navig.gateway.deck.routes.catalog import _TIER_RANK

_SDK_ENTITLEMENT = (
    Path(__file__).resolve().parents[3]
    / "packages" / "shared" / "bay-sdk" / "src" / "entitlement.ts"
)


def _parse_ts_tier_rank(src: str) -> dict[str, int]:
    """Extract the `TIER_RANK = { ... }` object literal into a Python dict."""
    block = re.search(r"TIER_RANK[^=]*=\s*\{(.*?)\}", src, re.DOTALL)
    assert block, "could not find TIER_RANK literal in entitlement.ts"
    return {k: int(v) for k, v in re.findall(r"(\w+)\s*:\s*(\d+)", block.group(1))}


def test_sdk_tier_rank_matches_daemon():
    if not _SDK_ENTITLEMENT.is_file():
        pytest.skip("bay-sdk not present (core-only checkout)")
    ts_rank = _parse_ts_tier_rank(_SDK_ENTITLEMENT.read_text(encoding="utf-8"))
    assert ts_rank == _TIER_RANK, (
        "SDK entitlement.ts TIER_RANK drifted from catalog.py _TIER_RANK — "
        f"SDK={ts_rank} daemon={_TIER_RANK}"
    )
