"""Runtime smoke tests for connectors that were DEAD ON ARRIVAL.

The static guard (``tests/quality/test_connector_type_contract.py``) proves the *shapes* match.
These prove the code actually *runs*: every one of these connectors previously raised
``AttributeError``/``TypeError`` on the first line of every method, so nothing below could
return at all.

Covered:
* not-connected → a clear ``ConnectorAuthError`` (was ``AttributeError`` from a
  ``_require_connected`` that did not exist),
* ``health_check()`` on an unhealthy connector RETURNS an unhealthy status instead of raising
  (``HealthStatus(healthy=…)`` → ``TypeError``; and the ``latency_ms``-less failure paths in five
  otherwise-working connectors, which crashed exactly when the connector was unhealthy),
* ``act()`` dispatch reads the op from ``params`` (``action.name`` never existed).
"""

from __future__ import annotations

import asyncio

import pytest

from navig.connectors.errors import ConnectorAuthError
from navig.connectors.gcp_translate.connector import GcpTranslateConnector
from navig.connectors.google_maps.connector import GoogleMapsConnector
from navig.connectors.types import Action, ActionType, HealthStatus
from navig.connectors.youtube.connector import YouTubeConnector

_DOA = [GoogleMapsConnector, YouTubeConnector, GcpTranslateConnector]


@pytest.mark.parametrize("cls", _DOA, ids=lambda c: c.manifest.id)
def test_act_on_unconnected_raises_clean_auth_error(cls):
    """Was AttributeError('_require_connected') — now a catchable ConnectorAuthError."""
    conn = cls()
    with pytest.raises(ConnectorAuthError):
        asyncio.run(conn.act(Action(action_type=ActionType.SEND, params={"op": "whatever"})))


@pytest.mark.parametrize("cls", _DOA, ids=lambda c: c.manifest.id)
def test_health_check_on_unconnected_returns_unhealthy(cls):
    """Was TypeError (HealthStatus has `ok`, not `healthy`) — a health check must never raise."""
    status = asyncio.run(cls().health_check())
    assert isinstance(status, HealthStatus)
    assert status.ok is False


@pytest.mark.parametrize(
    "module_name, cls_name",
    [
        ("navig.connectors.slack.connector", "SlackConnector"),
        ("navig.connectors.notion.connector", "NotionConnector"),
        ("navig.connectors.google_drive.connector", "GoogleDriveConnector"),
        ("navig.connectors.github.connector", "GitHubConnector"),
    ],
)
def test_health_check_without_httpx_returns_unhealthy(module_name, cls_name, monkeypatch):
    """The `HealthStatus(ok=False, message=…)` failure paths omitted the once-required
    `latency_ms` and raised TypeError — exactly when the connector was unhealthy."""
    import importlib

    mod = importlib.import_module(module_name)
    monkeypatch.setattr(mod, "HTTPX_AVAILABLE", False)
    status = asyncio.run(getattr(mod, cls_name)().health_check())
    assert isinstance(status, HealthStatus)
    assert status.ok is False
    assert status.latency_ms == 0.0  # no request was timed — reported, not faked


def test_youtube_act_dispatch_reads_op_from_params(monkeypatch):
    """`action.name` never existed; the op now comes from params — and returns a valid result."""
    conn = YouTubeConnector()
    conn._api_key = "k"
    monkeypatch.setattr(
        "navig.connectors.youtube.connector._yt_get",
        lambda *a, **k: {"items": [{"id": "v1"}]},
    )
    res = asyncio.run(conn.act(Action(action_type=ActionType.SEND, params={"op": "trending"})))
    assert res.success is True
    assert res.resource is not None
    assert res.resource.metadata["videos"] == [{"id": "v1"}]


def test_youtube_act_unknown_op_fails_cleanly():
    conn = YouTubeConnector()
    conn._api_key = "k"
    res = asyncio.run(conn.act(Action(action_type=ActionType.SEND, params={"op": "nope"})))
    assert res.success is False and "Unknown action" in (res.error or "")
