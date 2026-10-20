"""Every notification type has its own glyph, and the Deck shows the same one.

`apps/deck/lib/notify-meta.ts` says it mirrors `navig.notify.types.emoji_for_type`,
but nothing checked it: `appointment`, `sale`, `sales_digest` and
`sales_check_failed` all fell back to the generic 📢 in the Mini App.
"""

from __future__ import annotations

import re
from pathlib import Path

from navig.notify.types import TYPE_KEYS, emoji_for_type

DECK_META = Path(__file__).resolve().parents[3] / "apps" / "deck" / "lib" / "notify-meta.ts"


def _deck_map() -> dict[str, str]:
    src = DECK_META.read_text(encoding="utf-8")
    block = src[src.index("const TYPE_EMOJI") : src.index("}", src.index("const TYPE_EMOJI"))]
    return dict(re.findall(r"^\s*([a-z_]+):\s*'([^']+)'", block, re.MULTILINE))


def test_the_deck_map_was_actually_read():
    assert DECK_META.exists(), DECK_META
    assert len(_deck_map()) >= 20  # a vacuity floor: a broken parse must not pass


def test_every_type_has_its_own_glyph():
    generic = [t for t in TYPE_KEYS if t != "custom" and emoji_for_type(t) == "📢"]
    assert generic == [], f"types falling back to the generic glyph: {generic}"


def test_the_deck_shows_the_same_glyph_as_core():
    deck = _deck_map()
    mismatched = {t: (emoji_for_type(t), deck.get(t)) for t in TYPE_KEYS if deck.get(t) != emoji_for_type(t)}
    assert mismatched == {}, f"core vs deck glyph mismatch: {mismatched}"
