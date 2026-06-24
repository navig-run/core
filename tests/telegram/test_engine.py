"""Pure-engine tests for the Telegram Manager (no Telegram connection, no config)."""

from __future__ import annotations

from navig.telegram import dedupe, util


def test_norm_strips_track_number_ext_and_junk():
    assert util.norm("01. Daft Punk - Get Lucky (Official Video).mp3") == "daft punk get lucky"


def test_extract_links_classifies_providers():
    links = util.extract_links(
        "see https://www.tiktok.com/@x/video/1 and https://youtu.be/abc and http://e.com"
    )
    provs = {link["provider"] for link in links}
    assert "tiktok" in provs
    assert "youtube" in provs
    assert "url" in provs


def test_dedupe_incoming_drops_inbox_copy_keeps_sorted():
    recs = [
        {"file_unique_id": "A", "performer": "X", "title": "Song", "duration": 200,
         "file_size": 1000, "topic": "Incoming", "message_id": 1, "chat_id": 9},
        {"file_unique_id": "A", "performer": "X", "title": "Song", "duration": 200,
         "file_size": 1000, "topic": "Rock", "message_id": 2, "chat_id": 9},
    ]
    d = dedupe.find_duplicates(recs)
    assert d["summary"]["safe_delete"] == 1
    # the Incoming copy (msg 1) is the one dropped; the sorted Rock copy is kept
    assert d["safe_delete"][0]["message_id"] == 1


def test_dedupe_conflict_is_review_never_auto_deleted():
    # same song in TWO different real topics → wrong-category candidate → REVIEW only
    recs = [
        {"file_unique_id": "A", "performer": "X", "title": "Song", "duration": 200,
         "file_size": 1000, "topic": "Rock", "message_id": 1, "chat_id": 9},
        {"file_unique_id": "B", "performer": "X", "title": "Song", "duration": 200,
         "file_size": 1200, "topic": "Jazz", "message_id": 2, "chat_id": 9},
    ]
    d = dedupe.find_duplicates(recs)
    assert d["summary"]["safe_delete"] == 0
    assert d["summary"]["review"] == 1
    assert d["review"][0]["tier"] == "CONFLICT"
