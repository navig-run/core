"""The persistent announce-once claim: survives a 'restart' and is shared across
'processes' (both are just fresh callers reading the same notify.db)."""

from __future__ import annotations

import pytest


@pytest.fixture
def dd(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    from navig.notify import dedupe, store

    monkeypatch.setattr(store, "_initialised", False)
    store.init_db()
    assert str(store._db_path()).startswith(str(tmp_path))
    return dedupe


def test_claim_once_per_cooldown(dd):
    assert dd.claim("k", 3600, now=1000.0) is True
    assert dd.claim("k", 3600, now=1001.0) is False  # a second process / after a restart
    assert dd.claim("k", 3600, now=1000.0 + 3600) is True  # cooldown over
    assert dd.claim("other", 3600, now=1001.0) is True


def test_release_gives_the_claim_back(dd):
    assert dd.claim("k", 3600, now=1000.0)
    dd.release("k")
    assert dd.claim("k", 3600, now=1001.0) is True


def test_an_unreadable_store_announces_rather_than_loses(dd, monkeypatch):
    from navig.notify import store

    def _boom():
        raise RuntimeError("disk gone")

    monkeypatch.setattr(store, "conn", _boom)
    assert dd.claim("k", 3600) is True
