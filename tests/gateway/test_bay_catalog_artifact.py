"""The SHIPPED ``bay-catalog.json`` — data-integrity invariants.

``test_bay_catalog.py`` stubs the catalog to exercise the *handler*. These guard
the **real artifact that ships in the wheel**, because two surfaces silently
depend on its shape:

* ``_bay_item(slug)`` (``gateway/deck/routes/catalog.py``) resolves an item — and
  therefore its **pricing and capability gate** — by *slug alone, first match
  wins*. The desktop Bay likewise keys its per-item busy/note state by bare slug.
  A duplicate slug across kinds would gate one item against **another item's
  price**. This is reachable by design, not hypothetical: every installed Block
  also emits a ``SKILL.md`` shim, so a paid block and a free skill can converge
  on one slug — and the free one could win the lookup.
* The desktop Bay maps ``kind`` → glyph/label from a **closed** TS union
  (``BayKind`` in ``apps/os/.../lib/deck-types.ts``). A kind the client doesn't
  know renders without a proper label.

Skips (never fails) when the artifact isn't present in the checkout.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

ARTIFACT = Path(__file__).resolve().parents[2] / "navig" / "data" / "bay-catalog.json"

# Mirrors the `BayKind` union in apps/os/apps/webui/src/renderer/lib/deck-types.ts
# (+ KIND_GLYPHS / KIND_LABELS in BayPage.tsx). Keep the three in lockstep.
KNOWN_KINDS = {
    "space",
    "skill",
    "persona",
    "block",
    "plugin",
    "webapp",
    "formation",
    "prompt",
    "lens",
}


@pytest.fixture(scope="module")
def items() -> list[dict]:
    if not ARTIFACT.is_file():
        pytest.skip(f"bay catalog artifact not built: {ARTIFACT}")
    data = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    return [i for i in data.get("items", []) if isinstance(i, dict)]


def test_slugs_are_globally_unique(items: list[dict]) -> None:
    """A duplicate slug lets the paywall resolve the WRONG item's pricing."""
    dupes = {s: n for s, n in Counter(i.get("slug") for i in items).items() if n > 1}
    assert not dupes, (
        f"duplicate bay slugs {sorted(dupes)} — _bay_item() resolves an item (and its "
        "price/capability) by slug, first match wins, so a paid item could be gated "
        "against a free one's pricing. Slugs must be unique across ALL kinds."
    )


def test_every_item_has_slug_name_kind(items: list[dict]) -> None:
    """slug/name/kind are non-optional on the client (`BayItem`)."""
    broken = [
        i.get("slug") or i.get("name") or "<unnamed>"
        for i in items
        if not (i.get("slug") and i.get("name") and i.get("kind"))
    ]
    assert not broken, f"bay items missing slug/name/kind: {broken}"


def test_kinds_match_the_client_union(items: list[dict]) -> None:
    """An unknown kind renders in the desktop Bay without a glyph/label."""
    unknown = sorted({i["kind"] for i in items if i.get("kind") not in KNOWN_KINDS})
    assert not unknown, (
        f"bay kinds outside the client union: {unknown} — add them to `BayKind` in "
        "deck-types.ts AND to KIND_GLYPHS/KIND_LABELS in BayPage.tsx, then update "
        "KNOWN_KINDS here."
    )
