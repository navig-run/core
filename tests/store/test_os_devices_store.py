"""The paired-device store behind remote NAVIG OS.

What must hold, because each one is a way remote access could be granted by accident:
a pairing code works once and never after it expires; a token is never stored, only its
hash; a revoked device is unknown from that moment; and usage is counted per day so the
Cloudflare budget can actually be measured.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time

import pytest

from navig.store.os_devices import MAX_OPEN_CODES, OsDeviceStore


@pytest.fixture()
def store(tmp_path):
    s = OsDeviceStore(tmp_path / "os_remote.db")
    yield s
    s.close()


class TestPairingCodes:
    def test_a_code_works_exactly_once(self, store) -> None:
        code, _ = store.mint_pair_code()
        assert store.take_pair_code(code) is True
        assert store.take_pair_code(code) is False

    def test_an_expired_code_never_works(self, store) -> None:
        code, _ = store.mint_pair_code(ttl_s=-1)
        assert store.take_pair_code(code) is False

    def test_a_cancelled_code_stops_working_at_once(self, store) -> None:
        code, _ = store.mint_pair_code()
        store.cancel_pair_code(code)
        assert store.take_pair_code(code) is False

    def test_codes_are_128_bit_and_only_their_hash_is_stored(self, store) -> None:
        code, expires = store.mint_pair_code()
        assert len(code) >= 22  # token_urlsafe(16)
        assert expires > time.time()
        stored = [r[0] for r in sqlite3.connect(store.db_path).execute("SELECT code_hash FROM pair_codes")]
        assert code not in stored
        assert hashlib.sha256(code.encode()).hexdigest() in stored

    def test_open_codes_are_bounded(self, store) -> None:
        codes = [store.mint_pair_code()[0] for _ in range(MAX_OPEN_CODES + 3)]
        n = sqlite3.connect(store.db_path).execute("SELECT COUNT(*) FROM pair_codes").fetchone()[0]
        assert n == MAX_OPEN_CODES
        assert store.take_pair_code(codes[-1]) is True, "the newest code must survive the cap"

    def test_junk_is_refused_without_touching_the_db(self, store) -> None:
        assert store.take_pair_code("") is False
        assert store.take_pair_code("x" * 500) is False


class TestDevices:
    def test_a_device_is_found_by_its_token_and_the_token_is_not_stored(self, store) -> None:
        device_id, token = store.add_device(name="Safari on iPhone", ua="Mozilla/5.0 (iPhone)")
        assert store.device_for_token(token)["id"] == device_id
        raw = sqlite3.connect(store.db_path).execute("SELECT * FROM devices").fetchall()
        assert token not in repr(raw)
        assert len(token) >= 43  # token_urlsafe(32) — 256 bits

    def test_a_wrong_token_finds_nothing(self, store) -> None:
        store.add_device(name="a")
        assert store.device_for_token("not-a-token") is None
        assert store.device_for_token("") is None

    def test_revoking_makes_the_token_unknown_immediately(self, store) -> None:
        device_id, token = store.add_device(name="lost phone")
        assert store.revoke(device_id) is True
        assert store.device_for_token(token) is None
        assert store.revoke(device_id) is False, "revoking twice reports nothing changed"
        assert [d["id"] for d in store.list_devices()] == []
        assert [d["id"] for d in store.list_devices(include_revoked=True)] == [device_id]

    def test_names_are_trimmed_and_bounded(self, store) -> None:
        device_id, _ = store.add_device(name="  " + "n" * 500)
        assert len(store.get(device_id)["name"]) == 80


class TestUsage:
    def test_usage_accumulates_per_device_per_day(self, store) -> None:
        a, _ = store.add_device(name="a")
        b, _ = store.add_device(name="b")
        store.add_usage(a, msgs_in=3, msgs_out=7, bytes_in=30, bytes_out=700)
        store.add_usage(a, msgs_in=1, msgs_out=1, bytes_in=1, bytes_out=1)
        store.add_usage(b, msgs_out=10)
        assert store.usage_today() == {"msgs_in": 4, "msgs_out": 18, "bytes_in": 31, "bytes_out": 701}
        by_id = {d["id"]: d for d in store.list_devices()}
        assert by_id[a]["msgs_today"] == 12
        assert by_id[b]["msgs_today"] == 10

    def test_a_zero_update_writes_nothing(self, store) -> None:
        a, _ = store.add_device(name="a")
        store.add_usage(a)
        assert sqlite3.connect(store.db_path).execute("SELECT COUNT(*) FROM device_usage").fetchone()[0] == 0
