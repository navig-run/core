"""A bridge PID that cannot be verified is stale, not alive.

`_read_and_validate` decides whether the registry routes real requests at a
bridge. It used to treat "the liveness check raised" as "assume alive":
`check_pid_exists` catches NoSuchProcess only, so what reaches that branch is a
garbage `pid` in the file (ValueError) or a PID that exists but cannot be
inspected — on Windows, `AccessDenied` for a system-owned process, i.e. the
bridge died and its PID was RECYCLED to something protected. Neither is
evidence of a live bridge.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from navig.providers import bridge_grid_reader as reader


@pytest.fixture
def grid(tmp_path, monkeypatch):
    """Write a fresh grid file and point the reader at it."""
    path = tmp_path / "bridge-grid.json"
    monkeypatch.setattr(reader, "_grid_path", lambda: path)

    def write(**fields):
        data = {"ts": datetime.now(timezone.utc).isoformat(), "bridge_port": 7777, **fields}
        path.write_text(json.dumps(data), encoding="utf-8")
        return data

    return write


def test_a_garbage_pid_is_not_evidence_of_life(grid):
    grid(pid="not-a-number")

    assert reader._read_and_validate() is None


def test_a_pid_that_cannot_be_inspected_is_stale(grid, monkeypatch):
    """The recycled-PID case: exists, but psutil cannot look at it."""
    grid(pid=4242)

    def denied(pid):
        raise PermissionError("access denied")

    monkeypatch.setattr(reader, "_is_pid_alive", denied)

    assert reader._read_and_validate() is None


def test_a_verified_live_pid_is_still_accepted(grid, monkeypatch):
    """Anti-over-suppression floor: failing closed must not reject the real thing."""
    data = grid(pid=4242)
    monkeypatch.setattr(reader, "_is_pid_alive", lambda pid: True)

    assert reader._read_and_validate() == data


def test_a_verified_dead_pid_is_rejected(grid, monkeypatch):
    grid(pid=4242)
    monkeypatch.setattr(reader, "_is_pid_alive", lambda pid: False)

    assert reader._read_and_validate() is None
