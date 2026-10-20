"""The CyberAigen connector — contract, masking, and error-path honesty.

All network calls are mocked — no token, no live CyberAIGen, no credits spent.
The important promises: passwords are masked on the generic ``search()`` surface
while ``check()`` stays full-fidelity for the monitor/CLI, and every non-200
maps to a typed connector error instead of a parsed-looking dict.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.connectors.cyberaigen.connector import (
    CyberAigenConnector,
    mask_password,
)
from navig.connectors.errors import ConnectorAPIError, ConnectorAuthError
from navig.connectors.types import Action, ActionType, ConnectorDomain, ConnectorStatus

# ── manifest ─────────────────────────────────────────────────────────────────


def test_manifest_identity():
    assert CyberAigenConnector.manifest.id == "cyberaigen"
    assert CyberAigenConnector.manifest.requires_oauth is False
    assert CyberAigenConnector.manifest.auth_mode == "api_key"
    assert CyberAigenConnector.manifest.domain == ConnectorDomain.AUTH


def test_manifest_key_fields_cover_token_and_base_url():
    names = [f.name for f in CyberAigenConnector.manifest.key_fields]
    assert names == ["token", "base_url"]


def test_manifest_is_read_only():
    assert CyberAigenConnector.manifest.can_search is True
    assert CyberAigenConnector.manifest.can_fetch is False
    assert CyberAigenConnector.manifest.can_act is False


# ── masking ──────────────────────────────────────────────────────────────────


def test_mask_password_keeps_only_edges():
    assert mask_password("hunter2secret") == "h********t"
    assert mask_password("Hashed") == "Hashed"
    assert mask_password("abc") == "***"
    assert mask_password(None) == ""


# ── connect() ────────────────────────────────────────────────────────────────


def test_connect_without_a_token_fails_loudly(monkeypatch):
    monkeypatch.setattr(
        "navig.connectors.cyberaigen.connector.resolve", lambda field: ""
    )
    conn = CyberAigenConnector()
    with pytest.raises(ConnectorAuthError, match="cyberaigen/token"):
        import asyncio

        asyncio.run(conn.connect())
    assert conn.status == ConnectorStatus.ERROR


def test_connect_requires_the_operators_own_base_url(monkeypatch):
    """No shipped default endpoint: token alone must NOT connect — every user
    points the connector at their own deployment (vault/env)."""
    monkeypatch.setattr(
        "navig.connectors.cyberaigen.connector.resolve",
        lambda field: "secret-token" if field.name == "token" else "",
    )
    conn = CyberAigenConnector()
    import asyncio

    with pytest.raises(ValueError, match="base URL not found"):
        asyncio.run(conn.connect())
    assert conn.status == ConnectorStatus.ERROR


def test_connect_reads_token_and_base_url(monkeypatch):
    values = {"token": "secret-token", "base_url": "https://breach.example.test/app"}
    monkeypatch.setattr(
        "navig.connectors.cyberaigen.connector.resolve",
        lambda field: values.get(field.name, ""),
    )
    conn = CyberAigenConnector()
    import asyncio

    asyncio.run(conn.connect())
    assert conn.status == ConnectorStatus.CONNECTED
    assert conn._base_url == "https://breach.example.test/app"
    assert conn._token == "secret-token"


def test_scrub_endpoint_removes_every_url():
    from navig.connectors.cyberaigen.connector import scrub_endpoint

    dirty = "Failed for url: http://127.0.0.1:8800/api/v1/check and https://x.y/z?q=1"
    clean = scrub_endpoint(dirty)
    assert "http" not in clean
    assert clean.count("<endpoint>") == 2


# ── HTTP contract ────────────────────────────────────────────────────────────


class _FakeAsyncClient:
    """Stand-in for httpx.AsyncClient: a real class so ``async with`` works."""

    def __init__(self, resp):
        self.response = resp
        self.post = AsyncMock(return_value=resp)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _client_returning(status_code: int, payload=None, text: str = ""):
    """Build the fake client whose POST answers *status_code*."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text or json.dumps(payload if payload is not None else {})
    if payload is None:
        resp.json.side_effect = ValueError("no json")
    else:
        resp.json.return_value = payload
    return _FakeAsyncClient(resp)


def _connected(monkeypatch, token: str = "tok") -> CyberAigenConnector:
    monkeypatch.setattr(
        "navig.connectors.cyberaigen.connector.resolve",
        lambda field: token
        if field.name == "token"
        else "https://breach.example.test/app",
    )
    conn = CyberAigenConnector()
    import asyncio

    asyncio.run(conn.connect())
    return conn


def test_check_returns_the_raw_contract(monkeypatch):
    payload = {
        "status": "found",
        "count": 1,
        "results": [{"email": "a@b.com", "password": "hunter2"}],
        "tokens_used": 1,
        "balance": 9,
    }
    client = _client_returning(200, payload)
    with patch("httpx.AsyncClient", return_value=client):
        conn = _connected(monkeypatch)
        import asyncio

        data = asyncio.run(conn.check("a@b.com"))
    assert data["status"] == "found"
    assert data["results"][0]["password"] == "hunter2"  # full fidelity on check()
    # the request carried the Bearer token
    headers = client.post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer tok"


def test_check_maps_401_to_auth_error(monkeypatch):
    with patch("httpx.AsyncClient", return_value=_client_returning(401, {"message": "no"})):
        conn = _connected(monkeypatch)
        import asyncio

        with pytest.raises(ConnectorAuthError):
            asyncio.run(conn.check("a@b.com"))


def test_check_maps_402_to_a_credits_error(monkeypatch):
    with patch("httpx.AsyncClient", return_value=_client_returning(402, {"error": "no credits"})):
        conn = _connected(monkeypatch)
        import asyncio

        with pytest.raises(ConnectorAPIError, match="credits"):
            asyncio.run(conn.check("a@b.com"))


def test_check_maps_500_to_an_api_error(monkeypatch):
    with patch("httpx.AsyncClient", return_value=_client_returning(500, {})):
        conn = _connected(monkeypatch)
        import asyncio

        with pytest.raises(ConnectorAPIError, match="server error"):
            asyncio.run(conn.check("a@b.com"))


def test_check_surfaces_the_api_error_message_on_400(monkeypatch):
    with patch(
        "httpx.AsyncClient",
        return_value=_client_returning(400, {"error": "Too many search terms"}),
    ):
        conn = _connected(monkeypatch)
        import asyncio

        with pytest.raises(ConnectorAPIError, match="Too many search terms"):
            asyncio.run(conn.check("a@b.com"))


def test_check_rejects_non_json_payloads(monkeypatch):
    with patch("httpx.AsyncClient", return_value=_client_returning(200, None, text="<html>")):
        conn = _connected(monkeypatch)
        import asyncio

        with pytest.raises(ConnectorAPIError, match="non-JSON"):
            asyncio.run(conn.check("a@b.com"))


# ── the generic search surface masks ─────────────────────────────────────────


def test_search_masks_passwords_but_check_does_not(monkeypatch):
    payload = {
        "status": "found",
        "count": 1,
        "results": [
            {
                "email": "a@b.com",
                "databaseName": "Dump",
                "createdAt": "2024-05-01",
                "password": "hunter2secret",
            }
        ],
    }
    with patch("httpx.AsyncClient", return_value=_client_returning(200, payload)):
        conn = _connected(monkeypatch)
        import asyncio

        resources = asyncio.run(conn.search("a@b.com"))

    assert len(resources) == 1
    serialised = json.dumps(resources[0].to_dict())
    assert "hunter2secret" not in serialised
    assert resources[0].metadata["password_masked"] == "h********t"


def test_act_and_fetch_are_honestly_unsupported():
    conn = CyberAigenConnector()
    import asyncio

    result = asyncio.run(conn.act(Action(action_type=ActionType.SEND, params={})))
    assert result.success is False
    assert asyncio.run(conn.fetch("anything")) is None


def test_health_check_accepts_the_expected_401():
    with patch("httpx.AsyncClient", return_value=_client_returning(401, {"message": "Unauthenticated."})):
        conn = CyberAigenConnector()
        conn._base_url = "https://breach.example.test/app"
        import asyncio

        status = asyncio.run(conn.health_check())
    assert status.ok is True


def test_check_transport_errors_never_carry_the_endpoint(monkeypatch):
    """httpx exceptions embed the full request URL; the raised message — which
    travels into state, logs and notifications — must not."""
    import httpx

    client = _FakeAsyncClient(MagicMock())
    client.post = AsyncMock(
        side_effect=httpx.ConnectError(
            "All connection attempts failed for url: http://127.0.0.1:8800/api/v1/check"
        )
    )
    with patch("httpx.AsyncClient", return_value=client):
        conn = _connected(monkeypatch)
        import asyncio

        with pytest.raises(ConnectorAPIError) as excinfo:
            asyncio.run(conn.check("a@b.com"))

    message = str(excinfo.value)
    assert "127.0.0.1" not in message and "http:" not in message and "https:" not in message
    assert "<endpoint>" in message


def test_health_check_reports_a_500_as_unhealthy():
    with patch("httpx.AsyncClient", return_value=_client_returning(500, {})):
        conn = CyberAigenConnector()
        import asyncio

        status = asyncio.run(conn.health_check())
    assert status.ok is False
    assert "500" in (status.message or "")


def test_health_check_reports_a_connection_failure_as_unhealthy():
    import httpx

    client = _FakeAsyncClient(MagicMock())
    client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

    with patch("httpx.AsyncClient", return_value=client):
        conn = CyberAigenConnector()
        import asyncio

        status = asyncio.run(conn.health_check())
    assert status.ok is False
