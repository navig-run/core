"""A read that races the NAVIG OS writer is not "NAVIG OS is not running".

Measured on the live daemon: `os_server.running` answered true, false, true within
30 s while the desktop app ran the whole time — `read_descriptor()` mapped every
read error (a Windows sharing violation mid-replace, a half-written JSON body) to
None. The deck's "Open in NAVIG OS" rows and Telegram's /os read that verdict.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.cloud import os_discovery as od

GOOD = {"ws_url": "ws://127.0.0.1:9100/x", "token": "t" * 32, "pid": 0, "kind": "desktop"}


@pytest.fixture
def descriptor(tmp_path: Path) -> Path:
    p = tmp_path / "os-server.json"
    p.write_text(json.dumps(GOOD), encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(od.time, "sleep", lambda _s: None)


def _flaky_read(monkeypatch, failures: list[Exception]):
    real = Path.read_text

    def read_text(self, *a, **k):
        if self.name == "os-server.json" and failures:
            raise failures.pop(0)
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", read_text)


def test_a_transient_lock_is_retried(monkeypatch, descriptor):
    _flaky_read(monkeypatch, [PermissionError(13, "sharing violation")])
    assert od.read_descriptor(descriptor)["kind"] == "desktop"


def test_a_half_written_body_is_retried(monkeypatch, descriptor):
    _flaky_read(monkeypatch, [ValueError("Expecting value"), PermissionError(13, "busy")])
    assert od.read_descriptor(descriptor) is not None


def test_a_missing_file_is_not_running_without_retrying(monkeypatch, tmp_path):
    calls: list[float] = []
    monkeypatch.setattr(od.time, "sleep", lambda s: calls.append(s))
    assert od.read_descriptor(tmp_path / "os-server.json") is None
    assert calls == [], "a missing descriptor is the honest 'not running' — no back-off"


def test_a_persistently_unreadable_file_still_gives_up(monkeypatch, descriptor):
    _flaky_read(monkeypatch, [PermissionError(13, "busy")] * 10)
    assert od.read_descriptor(descriptor) is None
