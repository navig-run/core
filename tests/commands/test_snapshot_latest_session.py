"""Regression: `navig snapshot` resolves the latest session when --session is omitted.

`_latest_session` imported `get_active_session_id` from `navig.gateway_client`
(a name that never existed) and fell back to `cfg.get("session.last_id")` (a
config key nothing ever writes). Both branches were dead, so it *always*
returned None — every snapshot command silently required an explicit
``--session``. The fix queries the gateway's ``/sessions`` endpoint and returns
the key with the newest ``updated_at`` (file snapshots are keyed by the gateway
session that made the edit).
"""

from __future__ import annotations

import navig.gateway_client as gwc
from navig.commands.snapshot import _latest_session


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_picks_session_with_newest_updated_at(monkeypatch):
    payload = {
        "sessions": [
            {"key": "a", "updated_at": "2026-01-01T00:00:00"},
            {"key": "b", "updated_at": "2026-07-15T10:00:00"},  # newest
            {"key": "c", "updated_at": "2026-03-01T00:00:00"},
        ]
    }
    monkeypatch.setattr(gwc, "gateway_request", lambda *a, **k: _Resp(200, payload))

    assert _latest_session() == "b"


def test_falls_back_to_created_at_when_no_updated_at(monkeypatch):
    payload = {
        "sessions": [
            {"key": "old", "created_at": "2026-01-01T00:00:00"},
            {"key": "new", "created_at": "2026-07-01T00:00:00"},
        ]
    }
    monkeypatch.setattr(gwc, "gateway_request", lambda *a, **k: _Resp(200, payload))

    assert _latest_session() == "new"


def test_none_when_no_sessions(monkeypatch):
    monkeypatch.setattr(gwc, "gateway_request", lambda *a, **k: _Resp(200, {"sessions": []}))
    assert _latest_session() is None


def test_none_on_non_200(monkeypatch):
    monkeypatch.setattr(gwc, "gateway_request", lambda *a, **k: _Resp(503, {}))
    assert _latest_session() is None


def test_none_when_gateway_unreachable(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("gateway down")

    monkeypatch.setattr(gwc, "gateway_request", boom)
    assert _latest_session() is None


def test_the_real_seam_exists_not_the_phantom():
    assert callable(gwc.gateway_request)
    assert not hasattr(gwc, "get_active_session_id")
