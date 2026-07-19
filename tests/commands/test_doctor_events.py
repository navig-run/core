"""Tests for the ``navig doctor`` Event processor check (check_event_processor).

The check reads the additive ``events`` block on GET /api/deck/status:
- daemon unreachable → informational "not checked" warn (never fails doctor);
- running with a small backlog → green;
- running with pending ≥ threshold → warn (backlog growing);
- not running → hard failure (emitted events pile up undrained);
- older daemon without the field → informational warn.
"""

from __future__ import annotations

import pytest
import requests

from navig.commands.doctor import _EVENTS_BACKLOG_WARN, check_event_processor


class _FakeResponse:
    def __init__(self, payload: dict | None, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict | None:
        return self._payload


@pytest.fixture()
def reachable(monkeypatch):
    """Pretend the gateway port accepts connections."""
    monkeypatch.setattr("navig.commands.doctor._gateway_reachable", lambda *a, **kw: True)


def _mock_status(monkeypatch, payload: dict | None, status_code: int = 200) -> None:
    monkeypatch.setattr(
        requests, "get", lambda *a, **kw: _FakeResponse(payload, status_code=status_code)
    )


# NOTE: #198 ("a ✓ must mean verified healthy, never 'I could not look'") deliberately
# changed the "could not verify" cases below from a green ✓ (ok=True) to a ⚠ warning
# (ok=False + warn=True). These four tests were written for the older #177 behaviour and
# were never migrated; they now assert the current contract — a ⚠, not a ✓.


def test_daemon_unreachable_is_a_warning_not_a_pass(monkeypatch):
    monkeypatch.setattr("navig.commands.doctor._gateway_reachable", lambda *a, **kw: False)
    results = check_event_processor(port=1)
    assert len(results) == 1
    icon, ok, line = results[0]
    # "could not look" (daemon down) is a ⚠, not a green ✓ — but it stays a warn, not a
    # hard ✗, so it doesn't double-report the Gateway row's failure as an error.
    assert ok is False
    assert icon == "⚠"
    assert "not checked" in line


def test_running_with_small_backlog_is_green(monkeypatch, reachable):
    _mock_status(monkeypatch, {"events": {"running": True, "pending": 3, "history": 42}})
    _icon, ok, line = check_event_processor(port=1)[0]
    assert ok is True
    assert _icon == "✓"
    assert "running" in line
    assert "3 pending" in line


def test_running_with_growing_backlog_warns(monkeypatch, reachable):
    pending = _EVENTS_BACKLOG_WARN
    _mock_status(monkeypatch, {"events": {"running": True, "pending": pending, "history": 0}})
    _icon, ok, line = check_event_processor(port=1)[0]
    # House style: a ⚠ row is ok=False + warn=True — it flips doctor's exit
    # code (like the down-gateway row) but renders as a warning, not ✗.
    assert ok is False
    assert _icon == "⚠"
    assert f"{pending} pending" in line
    assert "backlog growing" in line


def test_not_running_is_a_hard_failure(monkeypatch, reachable):
    _mock_status(monkeypatch, {"events": {"running": False, "pending": 2737, "history": 0}})
    _icon, ok, line = check_event_processor(port=1)[0]
    assert ok is False
    assert "piling up undrained" in line
    assert "restart the gateway" in line.lower()


def test_older_daemon_without_events_field_warns_not_fails(monkeypatch, reachable):
    _mock_status(monkeypatch, {"avatar_state": "calm"})  # pre-upgrade daemon
    icon, ok, line = check_event_processor(port=1)[0]
    assert ok is False
    assert icon == "⚠"
    assert "does not expose event stats" in line


def test_non_200_status_is_a_warning(monkeypatch, reachable):
    _mock_status(monkeypatch, {"error": "unauthorized"}, status_code=401)
    icon, ok, line = check_event_processor(port=1)[0]
    assert ok is False
    assert icon == "⚠"
    assert "HTTP 401" in line


def test_request_exception_never_crashes_doctor(monkeypatch, reachable):
    def _boom(*a, **kw):
        raise requests.ConnectionError("connection reset")

    monkeypatch.setattr(requests, "get", _boom)
    icon, ok, line = check_event_processor(port=1)[0]
    # Never raises (a doctor check must not crash doctor); surfaces as a ⚠ "could not
    # verify" rather than a green ✓.
    assert ok is False
    assert icon == "⚠"
    assert "COULD NOT VERIFY" in line
