"""`navig doctor` → Reachability → "Remote NAVIG OS".

The state worth a row is the silent one: an edge older than 1.3.0 refuses every phone
while the desktop, the bot and every other light stay green. And an edge that did not
answer is "could not verify" — a warning — never a green tick.
"""

from __future__ import annotations

import pytest

from navig.commands import doctor


class _CM:
    def __init__(self, **values):
        self._v = values

    def get(self, key, default=None):
        return self._v.get(key, default)


class _Store:
    def __init__(self, n: int):
        self._n = n

    def list_devices(self):
        return [{"id": f"d{i}"} for i in range(self._n)]


@pytest.fixture()
def wire(monkeypatch):
    def _wire(*, devices=1, version="1.3.0", **cfg):
        values = {"cloud.mode": "lighthouse", "cloud.lighthouse_url": "https://edge.example.workers.dev"}
        values.update(cfg)
        monkeypatch.setattr("navig.config.get_config_manager", lambda: _CM(**values))
        monkeypatch.setattr("navig.store.os_devices.get_os_device_store", lambda: _Store(devices))
        monkeypatch.setattr("navig.commands.lighthouse._deployed_worker_version", lambda url: version)
        rows = doctor._os_remote_row()
        return rows[0] if rows else None

    return _wire


def _ok(row) -> bool:
    return row[1]


def _text(row) -> str:
    return row[2]


def test_no_relay_and_nothing_paired_is_not_a_question(wire) -> None:
    assert wire(devices=0, **{"cloud.mode": ""}) is None


def test_a_ready_relay_is_green(wire) -> None:
    row = wire(devices=2)
    assert _ok(row) and "v1.3.0" in _text(row) and "2 paired devices" in _text(row)


def test_an_old_relay_is_a_warning_that_names_the_fix(wire) -> None:
    row = wire(version="1.2.1")
    assert not _ok(row)
    assert "navig lighthouse redeploy" in _text(row)


def test_an_unanswering_relay_is_never_a_green_tick(wire) -> None:
    row = wire(version="")
    assert not _ok(row)
    assert "COULD NOT VERIFY" in _text(row)


def test_turned_off_is_a_verified_state(wire) -> None:
    row = wire(**{"os.remote.enabled": "false"})
    assert _ok(row) and "turned off" in _text(row)


def test_devices_without_a_relay_say_how_to_get_one(wire) -> None:
    row = wire(devices=1, **{"cloud.mode": ""})
    assert not _ok(row) and "navig lighthouse login" in _text(row)
