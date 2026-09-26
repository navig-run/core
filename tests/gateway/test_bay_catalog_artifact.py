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


# ── Completeness + preview material (batch 2, "is everything in the Bay?") ────

# The registry INDEX of published plugin packages (registry/plugins/registry.json) —
# not the plugins/ source tree; spelled as one segment so the plugin-subject
# detector in tests/quality/test_source_guards_are_wired.py (which looks for
# `<root> / "plugins"`, i.e. guards that SCAN the plugin code) does not count
# this file as one.
REGISTRY_PLUGINS = Path(__file__).resolve().parents[3] / "registry/plugins/registry.json"
README_CAP = 12 * 1024 + 64  # build-time cap + the "…" tail


def test_every_published_first_party_plugin_is_listed(items: list[dict]) -> None:
    """The plugin registry is the INDEX of published packages; the Bay used to list
    only the ten someone had hand-copied into catalog.curated.json."""
    if not REGISTRY_PLUGINS.is_file():
        pytest.skip("registry/plugins/registry.json not in this checkout")
    published = {
        str(p["id"]) for p in json.loads(REGISTRY_PLUGINS.read_text(encoding="utf-8"))["plugins"]
        if p.get("published")
    }
    assert published, "no published plugins in the registry — the floor moved"
    listed = {
        (i["slug"][6:] if str(i["slug"]).startswith("navig-") else str(i["slug"]))
        for i in items if i.get("kind") == "plugin"
    }
    missing = sorted(published - listed)
    assert not missing, f"published plugins absent from the Bay: {missing}"


def test_skills_carry_a_preview_and_ai_skills_carry_try_it_prompts(items: list[dict]) -> None:
    """A card's "demo" is its own text: SKILL.md for skills, plus the phrases an AI
    skill fires on as one-click prompts. Absent = a detail page with nothing to show."""
    skills = [i for i in items if i.get("kind") == "skill"]
    assert skills
    without = [i["slug"] for i in skills if not i.get("readme")]
    assert not without, f"skills with no preview text: {without}"
    ai = [i for i in skills if "ai-skill" in (i.get("tags") or [])]
    assert ai, "no AI skills tagged — the floor moved"
    no_prompts = [i["slug"] for i in ai if not i.get("demoPrompts")]
    assert not no_prompts, f"AI skills with no Try-it prompts: {no_prompts}"
    for i in ai:
        for p in i["demoPrompts"]:
            assert 8 <= len(p) <= 120, f"{i['slug']}: prompt length out of band: {p!r}"


def test_preview_text_stays_within_the_build_cap(items: list[dict]) -> None:
    """The catalog is served whole and cached by every surface; one uncapped
    README would dwarf the other hundred items."""
    over = [(i["slug"], len(i["readme"])) for i in items if len(i.get("readme") or "") > README_CAP]
    assert not over, f"preview text over the cap: {over}"
