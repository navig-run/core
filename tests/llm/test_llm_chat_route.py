"""Tests for the /llm/chat gateway route.

Validates the HTTP contract that navig-bridge consumes:
  POST /llm/chat  →  ChatRequest  →  ChatResponse

Uses aiohttp test utilities to exercise the route without
starting a full gateway process.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.integration

# The gateway fails closed with 503 when no token is configured (require_bearer_auth),
# so the fixture configures a token and every request authenticates with it.
TOKEN = "test-token"
_AUTH = {"Authorization": f"Bearer {TOKEN}"}

# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def mock_gateway():
    """Minimal NavigGateway mock with a stubbed channel router."""
    gw = MagicMock()
    gw.config = SimpleNamespace(auth_token=TOKEN)
    gw.router = MagicMock()
    gw.router.route_message = AsyncMock(return_value="Hello from NAVIG")
    return gw


@pytest.fixture
def app(mock_gateway):
    """Build an aiohttp Application with just the LLM route registered."""
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.routes.llm import register

    application = web.Application()
    register(application, mock_gateway)
    return application


# ── Happy path ──────────────────────────────────────────────────────


async def test_llm_chat_basic(app, mock_gateway):
    """Minimal ChatRequest returns a ChatResponse with text + metadata."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(app)) as client:
        payload = {
            "text": "Hello",
            "conversation": [],
            "scope": "personal",
        }
        resp = await client.post("/llm/chat", json=payload, headers=_AUTH)
        assert resp.status == 200

        body = await resp.json()
        assert body["ok"] is True
        assert body["data"]["text"] == "Hello from NAVIG"
        assert "metadata" in body["data"]
        assert body["data"]["metadata"]["provider"] == "gateway"
        assert isinstance(body["data"]["metadata"]["latencyMs"], int)

    # Verify the router was called with correct args
    mock_gateway.router.route_message.assert_called_once()
    call_kwargs = mock_gateway.router.route_message.call_args
    assert call_kwargs.kwargs["channel"] == "http"
    assert call_kwargs.kwargs["message"] == "Hello"


async def test_llm_chat_full_request(app, mock_gateway):
    """All optional fields are forwarded via metadata."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(app)) as client:
        payload = {
            "text": "Explain formations",
            "conversation": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "Hello!"},
            ],
            "scope": "project",
            "projectRoot": "/home/user/myproject",
            "formation": "devops",
            "flags": {"autoEvolve": True, "includeWorkspaceContext": True},
            "workspaceContext": "src/main.py ...",
        }
        resp = await client.post("/llm/chat", json=payload, headers=_AUTH)
        assert resp.status == 200

    # Verify metadata propagation
    call_kwargs = mock_gateway.router.route_message.call_args.kwargs
    meta = call_kwargs["metadata"]
    assert meta["scope"] == "project"
    assert meta["formation"] == "devops"
    assert meta["flags"]["autoEvolve"] is True
    assert meta["workspace_context"] == "src/main.py ..."
    # autoEvolve → tier_override = "big"
    assert meta["tier_override"] == "big"


# ── Error paths ─────────────────────────────────────────────────────


async def test_llm_chat_missing_text(app):
    """Missing 'text' field returns 400."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/llm/chat", json={"conversation": [], "scope": "personal"}, headers=_AUTH
        )
        assert resp.status == 400
        body = await resp.json()
        assert "text" in body["error"].lower()


async def test_llm_chat_empty_text(app):
    """Empty string for 'text' returns 400."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/llm/chat",
            json={"text": "  ", "conversation": [], "scope": "personal"},
            headers=_AUTH,
        )
        assert resp.status == 400


async def test_llm_chat_invalid_json(app):
    """Non-JSON body returns 400."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/llm/chat",
            data=b"not json",
            headers={**_AUTH, "Content-Type": "application/json"},
        )
        assert resp.status == 400
        body = await resp.json()
        assert "json" in body["error"].lower()


async def test_llm_chat_router_exception(app, mock_gateway):
    """Router exception returns 500 with error text."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    mock_gateway.router.route_message = AsyncMock(
        side_effect=RuntimeError("LLM provider unavailable")
    )

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/llm/chat",
            json={"text": "test", "conversation": [], "scope": "personal"},
            headers=_AUTH,
        )
        assert resp.status == 500
        body = await resp.json()
        assert body["ok"] is False
        assert body["error_code"] == "llm_error"
        assert "LLM provider unavailable" in body["details"]["message"]
        assert body["details"]["provider"] == "gateway"


async def test_llm_chat_default_scope(app, mock_gateway):
    """Scope defaults to 'personal' when omitted."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/llm/chat", json={"text": "hello"}, headers=_AUTH)
        assert resp.status == 200

    meta = mock_gateway.router.route_message.call_args.kwargs["metadata"]
    assert meta["scope"] == "personal"


async def test_llm_chat_auth_required_when_gateway_token_set(app, mock_gateway):
    """Route enforces bearer auth when gateway auth token is configured."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    mock_gateway.config.auth_token = "secret-token"

    async with TestClient(TestServer(app)) as client:
        unauthorized = await client.post("/llm/chat", json={"text": "hello"})
        assert unauthorized.status == 401
        unauthorized_body = await unauthorized.json()
        assert unauthorized_body["error_code"] == "unauthorized"

        authorized = await client.post(
            "/llm/chat",
            json={"text": "hello"},
            headers={"Authorization": "Bearer secret-token"},
        )
        assert authorized.status == 200


# ── The CLI passthrough must be off on this route (#1667 follow-up) ──────────
#
# /llm/chat is the ONE guarded route that also accepts the fleet-shared
# `gateway.mesh_token` — a PEER credential. A message starting "navig " would
# otherwise run as a subprocess with no approval gate, which
# require_bearer_auth's own contract says the mesh secret must never unlock.


async def test_mesh_peer_message_cannot_reach_the_cli_passthrough(app, mock_gateway):
    """A peer authenticating with gateway.mesh_token gets no CLI subprocess."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    mock_gateway.config_manager = SimpleNamespace(
        global_config={"gateway": {"mesh_token": "fleet-secret"}}
    )

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/llm/chat",
            json={"text": "navig vault list", "conversation": [], "scope": "personal"},
            headers={"Authorization": "Bearer fleet-secret"},
        )
        assert resp.status == 200  # the chat still works; only the passthrough is off

    kwargs = mock_gateway.router.route_message.call_args.kwargs
    assert kwargs["message"] == "navig vault list"  # text reaches the agent as TEXT
    assert kwargs["allow_cli"] is False


async def test_admin_token_keeps_the_cli_passthrough(app, mock_gateway):
    """The operator's own path is unchanged: `navig …` still runs here.

    NAVIG OS / anchor / echo authenticate with the ADMIN token and deliberately
    let the operator type a `navig …` line expecting the command to run. The fix
    is therefore per-credential, not unconditional — this test is what stops the
    next reader from "tightening" the route and breaking those desktop apps.
    """
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/llm/chat",
            json={"text": "/navig host list", "conversation": [], "scope": "personal"},
            headers=_AUTH,
        )
        assert resp.status == 200

    kwargs = mock_gateway.router.route_message.call_args.kwargs
    assert kwargs["allow_cli"] is True
    assert kwargs["message"] == "/navig host list"


async def test_an_absent_mesh_token_leaves_the_admin_path_alone(app, mock_gateway):
    """No peer secret configured → nothing to mistake for a peer, so admin behaves as always."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    mock_gateway.config_manager = SimpleNamespace(global_config={"gateway": {}})

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/llm/chat",
            json={"text": "navig status", "conversation": [], "scope": "personal"},
            headers=_AUTH,
        )
        assert resp.status == 200

    assert mock_gateway.router.route_message.call_args.kwargs["allow_cli"] is True
