"""One corrupt JSON column must not poison a whole store LIST fetch.

Every store maps rows through a ``_row_to_*`` helper that is called inside a list
comprehension over ``fetchall()`` — ``[self._row_to_dict(r) for r in rows]``. A bare
``json.loads`` raising on ONE corrupt blob there loses the ENTIRE list (every row), not
the single bad record: a scheduled-post queue, a contact book, or a Telegram catalog
goes completely blank. The usual guard ``json.loads(row["x"] or "{}")`` only covers
NULL/empty — a non-empty *malformed* blob still raises.

These pin the degrade-instead-of-raise contract on the list paths, and (crucially) that
the read-MODIFY-write path still refuses to degrade, because writing the default back
there would destroy the original blob.
"""

from __future__ import annotations

import json

import pytest

from navig.core.json_io import safe_json_loads

# ── the primitive ────────────────────────────────────────────────────────────


def test_safe_json_loads_degrades_instead_of_raising():
    # valid JSON is returned unchanged (no type coercion)
    assert safe_json_loads('{"a": 1}', {}) == {"a": 1}
    assert safe_json_loads("[1, 2]", []) == [1, 2]
    # NULL / empty / non-str → the caller's default
    assert safe_json_loads(None, {}) == {}
    assert safe_json_loads("", []) == []
    assert safe_json_loads(123, {"d": 1}) == {"d": 1}
    # malformed (the case `or "{}"` does NOT cover) → default, never a raise
    assert safe_json_loads("{not json", {}) == {}
    assert safe_json_loads('{"a": ', []) == []


# ── the list paths ───────────────────────────────────────────────────────────


def test_scheduled_posts_list_survives_a_corrupt_row(tmp_path):
    """A corrupt content blob on ONE post must not blank the whole publish queue."""
    from navig.store.scheduled_posts import ScheduledPostStore

    store = ScheduledPostStore(db_path=tmp_path / "posts.db")
    try:
        bad = store.create(body="broken", content={"text": "x"}, targets=["telegram"])
        store.create(body="fine", content={"text": "ok"}, targets=["telegram"])
        store._write(
            "UPDATE scheduled_posts SET content_json = ? WHERE id = ?", ("{not json", bad)
        )

        posts = store.list()  # pre-fix: raises JSONDecodeError → the whole queue is lost

        assert len(posts) == 2, "both posts must survive; only the bad blob degrades"
        broken = next(p for p in posts if p["id"] == bad)
        healthy = next(p for p in posts if p["id"] != bad)
        assert broken["content"] == {}  # degraded
        assert broken["body"] == "broken"  # the real content lives in other columns
        assert healthy["content"] == {"text": "ok"}  # untouched
    finally:
        store.close()


def test_thread_list_survives_a_corrupt_meta_row(tmp_path):
    from navig.store.threads import ThreadStore

    store = ThreadStore(db_path=tmp_path / "threads.db")
    try:
        bad = store.get_or_create("telegram", "chat-bad")
        store.get_or_create("telegram", "chat-good")
        store._write("UPDATE threads SET meta_json = ? WHERE id = ?", ("{not json", bad.id))

        threads = store.list_threads()

        assert len(threads) == 2
        assert next(t for t in threads if t.id == bad.id).meta == {}
    finally:
        store.close()


def test_contact_list_survives_a_corrupt_fallbacks_row(tmp_path):
    from navig.store.contacts import ContactStore

    store = ContactStore(db_path=tmp_path / "contacts.db")
    try:
        store.add_contact("bad", "Bad", routes=["telegram:1"])
        store.add_contact("good", "Good", routes=["telegram:2"])
        store._write("UPDATE contacts SET fallbacks_json = ? WHERE alias = ?", ("{x", "bad"))

        contacts = store.list_contacts()

        assert len(contacts) == 2
        bad = next(c for c in contacts if c.alias == "bad")
        assert bad.fallbacks == []
        assert bad.display_name == "Bad"  # the rest of the row is intact
    finally:
        store.close()


# ── the write path must NOT degrade ──────────────────────────────────────────


def test_thread_update_meta_still_refuses_a_corrupt_blob(tmp_path):
    """A failed READ must never become a destructive WRITE.

    ``update_meta`` is read-modify-write: degrading a corrupt blob to ``{}`` there would
    persist the emptiness over the original. It must keep raising so the write ABORTS —
    the opposite of the list paths above, and deliberately so.
    """
    from navig.store.threads import ThreadStore

    store = ThreadStore(db_path=tmp_path / "threads.db")
    try:
        t = store.get_or_create("telegram", "chat-1")
        store._write("UPDATE threads SET meta_json = ? WHERE id = ?", ("{not json", t.id))

        with pytest.raises(ValueError):  # JSONDecodeError subclasses ValueError
            store.update_meta(t.id, {"k": "v"})

        row = store._read_one("SELECT meta_json FROM threads WHERE id = ?", (t.id,))
        assert row["meta_json"] == "{not json", "the original blob must survive the refusal"
    finally:
        store.close()


def test_valid_json_round_trips_unchanged(tmp_path):
    """Guard against the sweep changing happy-path behaviour."""
    from navig.store.scheduled_posts import ScheduledPostStore

    store = ScheduledPostStore(db_path=tmp_path / "posts.db")
    try:
        content = {"text": "hello", "nested": {"a": [1, 2]}}
        targets = ["telegram", "x"]
        pid = store.create(body="b", content=content, targets=targets)
        got = next(p for p in store.list() if p["id"] == pid)
        assert got["content"] == content
        assert got["targets"] == targets
        assert got["receipts"] == []
        assert json.loads(json.dumps(got["content"])) == content
    finally:
        store.close()
