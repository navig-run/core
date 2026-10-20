"""creative_proposal: registered, deck-defaulted, seeded into the matrix on boot."""

from __future__ import annotations

import pytest

from navig.notify.types import NOTIFICATION_TYPES, emoji_for_type


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from navig.notify import store as notify_store

    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(notify_store, "_initialised", False)
    yield


def test_type_is_registered_with_deck_default():
    row = next((t for t in NOTIFICATION_TYPES if t["key"] == "creative_proposal"), None)
    assert row is not None, "creative_proposal must be a registered notification type"
    assert row["label"] == "NAVIG suggestions"
    # Deck only by default — suggestions are not phone buzz (plan §B10).
    assert row["default_channels"] == ["deck"]


def test_emoji_registered():
    assert emoji_for_type("creative_proposal") == "💡"


def test_existing_db_seeds_the_new_type_matrix_rows():
    """An install whose notify.db predates this type must gain its rows on the
    next init (INSERT OR IGNORE — user toggles of other types stay untouched)."""
    from navig.notify import store

    store.init_db()
    with store.conn() as conn:
        rows = dict(
            conn.execute(
                "SELECT channel, enabled FROM notify_matrix WHERE type = 'creative_proposal'"
            ).fetchall()
        )
    assert rows, "matrix rows must be seeded for the new type"
    assert rows.get("deck") == 1
    assert rows.get("telegram") == 0  # opt-in, not default
