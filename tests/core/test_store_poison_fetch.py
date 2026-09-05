"""One corrupt JSON blob must cost its own row — never the whole fetch.

Stores parse JSON columns inside fetch LOOPS (``[from_row(r) for r in rows]``). A bare
``json.loads`` raising on ONE bad blob there loses EVERY row: the operator's entire room
list / credential list / history view disappears because of a single unreadable field.

The common guard ``json.loads(x) if x else {}`` looks like it covers this and does not — it
handles NULL and empty, while a *malformed* non-empty blob still raises.

Two different treatments are correct here, and the difference matters:

  * where the parsed value is never written back, degrade it (``safe_json_loads``);
  * where the object is round-tripped (fetch → mutate → save), degrading on read would let
    the next save PERSIST the emptied value over real data, so the row is isolated in the
    LIST instead and ``get()`` stays strict.

``tests/vault`` covers the deliberate exception: a credential's ``data`` (the secret) must
still raise, because "unreadable" is not "empty" on the secrets path.
"""

from __future__ import annotations

import sqlite3

import pytest

pytestmark = pytest.mark.unit


# ── matrix: no round-trip, so the blob degrades in place ─────────────────────


def _matrix_store(tmp_path):
    from navig.comms.matrix_store import MatrixStore

    return MatrixStore(db_path=tmp_path / "matrix.db")


def test_one_corrupt_room_does_not_hide_every_other_room(tmp_path):
    from navig.comms.matrix_store import MatrixRoom

    store = _matrix_store(tmp_path)
    store.upsert_room(MatrixRoom(room_id="!good:x", name="good"))
    store.upsert_room(MatrixRoom(room_id="!bad:x", name="bad"))

    conn = sqlite3.connect(store.db_path)
    conn.execute("UPDATE rooms SET metadata = ? WHERE room_id = ?", ("{not json", "!bad:x"))
    conn.commit()
    conn.close()

    rooms = store.list_rooms()
    assert {r.room_id for r in rooms} == {"!good:x", "!bad:x"}, (
        "a single unreadable metadata blob hid the whole room list"
    )
    bad = next(r for r in rooms if r.room_id == "!bad:x")
    assert bad.metadata == {}
    assert bad.name == "bad", "the row's real columns must survive its bad blob"


def test_a_null_metadata_column_still_reads(tmp_path):
    """The pre-existing `if row[...] else {}` guard covered this; keep it covered."""
    from navig.comms.matrix_store import MatrixRoom

    store = _matrix_store(tmp_path)
    store.upsert_room(MatrixRoom(room_id="!n:x", name="n"))
    conn = sqlite3.connect(store.db_path)
    conn.execute("UPDATE rooms SET metadata = NULL")
    conn.commit()
    conn.close()

    assert store.list_rooms()[0].metadata == {}


# ── identity: a documented fetch → mutate → save round-trip ──────────────────


def _identity_store(tmp_path):
    from navig.identity.store import IdentityStore

    return IdentityStore(tmp_path / "identity.db")


def test_one_corrupt_profile_does_not_hide_every_other_profile(tmp_path):
    store = _identity_store(tmp_path)
    store.get_or_create(telegram_id=111)
    store.get_or_create(telegram_id=222)

    conn = sqlite3.connect(store.db_path)
    conn.execute("UPDATE navig_identities SET socials = ? WHERE telegram_id = ?", ("[bad", 222))
    conn.commit()
    conn.close()

    ids = {p.telegram_id for p in store.list_all()}
    assert 111 in ids, "one unreadable profile hid the readable ones"


def test_get_still_raises_so_a_save_cannot_persist_a_degraded_blob(tmp_path):
    """The round-trip guard. `get_or_create -> mutate -> save` is this module's documented
    usage, and save() re-serialises socials/metadata — so if get() quietly returned an empty
    socials list for a malformed blob, the very next save would overwrite the user's real
    social links with it. Losing the row from a listing is recoverable; that is not."""
    store = _identity_store(tmp_path)
    store.get_or_create(telegram_id=333)

    conn = sqlite3.connect(store.db_path)
    conn.execute("UPDATE navig_identities SET socials = ? WHERE telegram_id = ?", ("[bad", 333))
    conn.commit()
    conn.close()

    with pytest.raises(Exception):
        store.get(333)


# ── bot cache: a poisoned entry must be a MISS, and must self-heal ───────────


def test_a_poisoned_cache_entry_is_a_miss_and_is_evicted(tmp_path, monkeypatch):
    from navig.bot import stats_store as ss

    store = ss.BotStatsStore(db_path=tmp_path / "stats.db")
    store.cache_set("k", {"v": 1}, ttl_seconds=600)

    conn = sqlite3.connect(store.db_path)
    conn.execute("UPDATE cache SET value = ? WHERE key = ?", ("{not json", "k"))
    conn.commit()
    conn.close()
    store._cache.clear()  # force the DB path, not the in-memory hit

    assert store.cache_get("k") is None, "a corrupt cache entry must read as a miss, not raise"

    # And it must be gone, or the key is poisoned forever: cache_set() is never reached
    # while cache_get() raises, so nothing could ever replace it.
    conn = sqlite3.connect(store.db_path)
    remaining = conn.execute("SELECT COUNT(*) FROM cache WHERE key = ?", ("k",)).fetchone()[0]
    conn.close()
    assert remaining == 0, "the unparseable entry was left in place to poison every future read"

    store.cache_set("k", {"v": 2}, ttl_seconds=600)
    store._cache.clear()
    assert store.cache_get("k") == {"v": 2}, "the key must be usable again after eviction"


# ── a server sync must not overwrite NAVIG's own classifications ─────────────


class TestMatrixSyncPreservesNavigFields:
    """`purpose` / `encrypted` / `metadata` are NAVIG's, not the homeserver's.

    The sync used to send a whole MatrixRoom built from a room listing, so the dataclass
    DEFAULTS it carried went into the upsert's ON CONFLICT clause: every sync reset purpose to
    "general", encrypted to False and metadata to {} for every known room. Nothing writes those
    to anything else yet — which is the only reason no data has been lost, and exactly why it
    needed fixing before something does. `navig matrix rooms --purpose alerts` filters on the
    first, and the room table renders the second as a padlock.
    """

    def test_a_sync_leaves_purpose_encrypted_and_metadata_intact(self, tmp_path):
        from navig.comms.matrix_store import MatrixRoom, MatrixStore

        store = MatrixStore(db_path=tmp_path / "matrix.db")
        store.upsert_room(
            MatrixRoom(
                room_id="!r:x",
                name="Ops",
                purpose="alerts",
                encrypted=True,
                metadata={"team": "sre"},
            )
        )

        store.sync_room_from_server("!r:x", name="Ops renamed", topic="now with a topic")

        room = store.get_room("!r:x")
        assert room.name == "Ops renamed", "server-owned fields must still update"
        assert room.topic == "now with a topic"
        assert room.purpose == "alerts", "sync reset a NAVIG-side classification"
        assert room.encrypted is True, "sync cleared the encrypted flag"
        assert room.metadata == {"team": "sre"}, "sync wiped stored metadata"

    def test_a_sync_creates_an_unknown_room_with_defaults(self, tmp_path):
        from navig.comms.matrix_store import MatrixStore

        store = MatrixStore(db_path=tmp_path / "matrix.db")
        store.sync_room_from_server("!new:x", name="Fresh")

        room = store.get_room("!new:x")
        assert room is not None and room.name == "Fresh"
        assert room.purpose == "general" and room.encrypted is False and room.metadata == {}

    def test_a_blank_field_in_the_listing_does_not_erase_what_we_know(self, tmp_path):
        """An unnamed room in a sync response means "not said", not "clear the name"."""
        from navig.comms.matrix_store import MatrixRoom, MatrixStore

        store = MatrixStore(db_path=tmp_path / "matrix.db")
        store.upsert_room(MatrixRoom(room_id="!k:x", name="Known", topic="Known topic"))

        store.sync_room_from_server("!k:x", name="", topic="")

        room = store.get_room("!k:x")
        assert room.name == "Known" and room.topic == "Known topic"

    def test_upsert_room_still_writes_everything(self, tmp_path):
        """The full-overwrite path is intentional and still available — the fix is that the
        SYNC no longer uses it, not that upsert changed."""
        from navig.comms.matrix_store import MatrixRoom, MatrixStore

        store = MatrixStore(db_path=tmp_path / "matrix.db")
        store.upsert_room(MatrixRoom(room_id="!u:x", name="A", purpose="alerts"))
        store.upsert_room(MatrixRoom(room_id="!u:x", name="B"))

        room = store.get_room("!u:x")
        assert room.name == "B"
        assert room.purpose == "general", "upsert_room is documented to write every column"
