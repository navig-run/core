"""The embedded gateway's CORS must be the daemon's allowlist — never a wildcard.

The telegram worker mounts its OWN copy of the gateway (``daemon/telegram_worker.py``
builds ``gateway._app`` with only ``_cors_middleware``), and on an install where the
worker owns the port that copy is the listener the Deck talks to. Its CORS method
answered **every** route with ``Access-Control-Allow-Origin: *``.

That was survivable only as long as every route carried a Bearer. These carry none:

    GET /api/memory/facts/pending   agent key-facts
    GET /install/config             mesh_token — the fleet shared secret
    GET /llm/providers              provider inventory (POST /llm/providers/register
                                    is the same handler family)
    GET /router/status              provider/trace disclosure

Measured on the live gateway 2026-10-19 (Origin: ``https://evil.example``): each answered
``200`` with ``Access-Control-Allow-Origin: *``, from loopback AND from the LAN address —
so any page open in any browser on the machine could ``fetch`` and read them, because
loopback plus a wildcard grant is exactly the combination a browser will hand across
origins.

The fix is delegation, not a second allowlist: ``cors_headers_for`` is the one the
daemon's middleware already uses, so the two listeners cannot drift apart again. These
tests pin that delegation — reverting to a literal ``*`` fails every one of them.
"""

from __future__ import annotations

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from navig.gateway.middleware import cors_headers_for
from navig.gateway.server import NavigGateway

_ACAO = "Access-Control-Allow-Origin"


async def _run(request: web.Request, handler=None) -> web.Response:
    """Drive the embedded middleware with a stub self (it reads no instance state)."""
    handler = handler or (lambda _req: web.json_response({"ok": True}))
    result = await NavigGateway._cors_middleware(object(), request, handler)
    assert isinstance(result, web.Response)
    return result


def _delegate_header(request: web.Request) -> str | None:
    """What the shared allowlist says this request should get — the contract."""
    return cors_headers_for(request).get(_ACAO)


async def test_a_random_web_page_gets_no_cors_grant() -> None:
    req = make_mocked_request(
        "GET", "/api/memory/facts/pending", headers={"Origin": "https://evil.example"}
    )
    resp = await _run(req)
    assert resp.status == 200
    assert _ACAO not in resp.headers, (
        "the embedded gateway granted CORS to a non-allowlisted origin — the routes it "
        "serves without a Bearer become readable from any open web page"
    )
    assert resp.headers.get(_ACAO) == _delegate_header(req)


async def test_the_telegram_webview_still_gets_the_grant_it_needs() -> None:
    req = make_mocked_request(
        "GET", "/api/deck/memory/stats", headers={"Origin": "https://web.telegram.org"}
    )
    resp = await _run(req)
    assert resp.headers.get(_ACAO) == "https://web.telegram.org"
    assert resp.headers.get(_ACAO) == _delegate_header(req)


async def test_localhost_dev_servers_still_work_on_any_port() -> None:
    req = make_mocked_request(
        "GET", "/api/deck/context", headers={"Origin": "http://localhost:7432"}
    )
    resp = await _run(req)
    assert resp.headers.get(_ACAO) == "http://localhost:7432"
    assert resp.headers.get(_ACAO) == _delegate_header(req)


async def test_preflight_from_an_arbitrary_origin_carries_no_grant() -> None:
    req = make_mocked_request(
        "OPTIONS",
        "/api/deck/memory/stats",
        headers={"Origin": "https://evil.example"},
    )
    resp = await _run(req)
    assert resp.status == 200
    assert _ACAO not in resp.headers


async def test_preflight_for_an_allowed_origin_still_answers() -> None:
    req = make_mocked_request(
        "OPTIONS",
        "/api/deck/memory/stats",
        headers={"Origin": "https://web.telegram.org"},
    )
    resp = await _run(req)
    assert resp.status == 200
    assert resp.headers.get(_ACAO) == "https://web.telegram.org"
    assert resp.headers.get("Access-Control-Allow-Headers") == (
        "Content-Type, Authorization, X-Telegram-Init-Data, X-Telegram-User"
    )


async def test_no_origin_means_no_cors_headers_at_all() -> None:
    """Native clients (curl, the OS shell, a fleet peer) never need — or get — CORS."""
    req = make_mocked_request("GET", "/api/deck/memory/stats")
    resp = await _run(req)
    assert _ACAO not in resp.headers
    assert resp.headers.get(_ACAO) == _delegate_header(req)
