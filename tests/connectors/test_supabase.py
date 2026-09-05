"""First tests for the Supabase connector.

Two classes of bug are pinned here:

* DOA: `search`/`fetch`/`act` all called `self._require_connected()`, which was defined NOWHERE —
  every call raised `AttributeError` before doing anything. The connector never worked (no tests
  hid it). It must now guard cleanly (raise `ConnectorAuthError` when not connected).
* Phantom-empty reads: the read paths only handled 401/403, so any other error (bad column, bad
  filter, 404, 429, 5xx) returned `[]` / a "0 row(s) fetched" success — a FAILED query looked
  identical to "no results". They must raise `ConnectorAPIError`. And `fetch` must flag a
  limit-capped read as truncated rather than presenting it as the whole table.
"""
from __future__ import annotations

import asyncio

import pytest

from navig.connectors.errors import ConnectorAPIError, ConnectorAuthError
from navig.connectors.supabase.connector import SupabaseConnector
from navig.connectors.types import Action, ActionType, ConnectorStatus


def _connected() -> SupabaseConnector:
    c = SupabaseConnector()
    c._url = "https://proj.supabase.co"
    c._anon_key = "anon-key"
    c._status = ConnectorStatus.CONNECTED
    return c


def _patch_request(monkeypatch, status: int, data):
    monkeypatch.setattr(
        "navig.connectors.supabase.connector._sb_request",
        lambda *a, **k: (status, data),
    )


# ── DOA guard: _require_connected exists and gates ───────────────────────────

def test_require_connected_defined_and_passes_when_connected():
    c = _connected()
    c._require_connected()  # must NOT raise (and must exist at all)


def test_operations_raise_clean_error_when_not_connected(monkeypatch):
    c = SupabaseConnector()  # never connect()ed → _url / _anon_key are None
    # No request should be attempted — a clear ConnectorAuthError, never AttributeError.
    with pytest.raises(ConnectorAuthError):
        asyncio.run(c.fetch("t"))
    with pytest.raises(ConnectorAuthError):
        asyncio.run(c.search("q", table="t"))
    with pytest.raises(ConnectorAuthError):
        asyncio.run(c.act(Action(action_type=ActionType.SEND, params={"op": "buckets"})))


# ── search: errors surface, success returns rows ─────────────────────────────

def test_search_raises_on_api_error(monkeypatch):
    _patch_request(monkeypatch, 400, {"message": 'column "nope" does not exist'})
    c = _connected()
    with pytest.raises(ConnectorAPIError) as ei:
        asyncio.run(c.search("x", table="t", column="nope"))
    assert ei.value.status_code == 400
    assert "does not exist" in ei.value.detail


def test_search_raises_auth_on_401(monkeypatch):
    _patch_request(monkeypatch, 401, {"message": "bad key"})
    c = _connected()
    with pytest.raises(ConnectorAuthError):
        asyncio.run(c.search("x", table="t"))


def test_search_returns_rows_on_success(monkeypatch):
    _patch_request(monkeypatch, 200, [{"id": 1, "title": "hello"}])
    c = _connected()
    rows = asyncio.run(c.search("hel", table="t"))
    assert len(rows) == 1 and rows[0].title == "hello"


# ── fetch: errors surface, 404 → None, truncation flagged ────────────────────

def test_fetch_raises_on_server_error(monkeypatch):
    _patch_request(monkeypatch, 500, {"message": "boom"})
    c = _connected()
    with pytest.raises(ConnectorAPIError) as ei:
        asyncio.run(c.fetch("t"))
    assert ei.value.status_code == 500


def test_fetch_returns_none_on_404(monkeypatch):
    _patch_request(monkeypatch, 404, {"message": "not found"})
    c = _connected()
    assert asyncio.run(c.fetch("missing_table")) is None


def test_fetch_flags_truncation_at_limit(monkeypatch):
    rows = [{"id": i} for i in range(5)]
    _patch_request(monkeypatch, 200, rows)
    c = _connected()
    res = asyncio.run(c.fetch("t", limit=5))  # got exactly `limit` → more may exist
    assert res is not None
    assert res.metadata["truncated"] is True
    assert res.metadata["row_count"] == 5
    assert "more rows may exist" in res.preview


def test_fetch_not_truncated_below_limit(monkeypatch):
    _patch_request(monkeypatch, 200, [{"id": 1}, {"id": 2}])
    c = _connected()
    res = asyncio.run(c.fetch("t", limit=1000))
    assert res is not None
    assert res.metadata["truncated"] is False
    assert "more rows may exist" not in res.preview


# ── act: op is read from params (was action.name, which never existed) ───────

def test_act_insert_reads_op_from_params(monkeypatch):
    seen = {}

    def fake_request(url, headers, method="GET", body=None, timeout=15):
        seen["method"] = method
        seen["url"] = url
        return 201, [{"id": 1}]

    monkeypatch.setattr("navig.connectors.supabase.connector._sb_request", fake_request)
    c = _connected()
    res = asyncio.run(c.act(Action(
        action_type=ActionType.CREATE,
        params={"op": "insert", "table": "notes", "rows": [{"title": "x"}]},
    )))
    assert res.success is True
    assert seen["method"] == "POST" and seen["url"].endswith("/rest/v1/notes")


def test_act_unknown_op_fails_cleanly():
    c = _connected()
    res = asyncio.run(c.act(Action(action_type=ActionType.SEND, params={"op": "nope"})))
    assert res.success is False and "Unknown action" in (res.error or "")


def test_act_insert_error_surfaces_not_phantom_success(monkeypatch):
    _patch_request(monkeypatch, 409, {"message": "duplicate key value"})
    c = _connected()
    res = asyncio.run(c.act(Action(
        action_type=ActionType.CREATE,
        params={"op": "insert", "table": "notes", "rows": [{"id": 1}]},
    )))
    assert res.success is False
    assert "409" in (res.error or "") and "duplicate" in (res.error or "")


# ── health_check returns a valid HealthStatus (field is `ok`, not `healthy`) ─

def test_health_check_not_connected_returns_valid_status():
    c = SupabaseConnector()  # never connected → the early-return branch
    hs = asyncio.run(c.health_check())
    assert hs.ok is False  # constructs a valid HealthStatus (was HealthStatus(healthy=…) → TypeError)
