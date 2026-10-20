"""The LAN join flow: serves bootstrap content (and only to the LAN), and pings back.

Regression targets, all verified broken before the fix:
  * every /install route 500'd — `GatewayConfig` has no `.get()`, and the handlers
    called `gw.config.get("gateway", ...)` (`install.py:131/132/152`);
  * the joiner's `/mesh/ping` carried no Authorization and was 401'd by
    bearer auth, while the one-liner echoed "joined the mesh!" anyway;
  * the token-bearing routes answered ANY requester, including a public IP.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.integration


def _gw():
    from navig.gateway.server import GatewayConfig

    config = GatewayConfig({"gateway": {"host": "127.0.0.1", "port": 8789}})
    config.auth_token = "admin-tok"
    return SimpleNamespace(
        config=config,
        config_manager=SimpleNamespace(
            global_config={
                "gateway": {"auth_token": "admin-tok", "mesh_token": "fleet-tok"}
            }
        ),
    )


@pytest.fixture()
def app():
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.routes import install as install_routes

    application = web.Application()
    install_routes.register(application, _gw())
    return application


async def _get(app, path: str):
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(app)) as client:
        r = await client.get(path)
        return r.status, await r.text()


async def test_every_install_route_answers_200(app):
    """The old code 500'd on all of them (GatewayConfig.get AttributeError)."""
    for path in (
        "/install",
        "/install/windows.ps1",
        "/install/unix.sh",
        # aliases — every command ever printed for this flow keeps working
        "/install/windows",
        "/install/linux",
        "/install/mac",
    ):
        status, body = await _get(app, path)
        assert status == 200, f"{path} → {status}"
        assert body


async def test_the_script_the_old_one_liner_fetched_now_exists(app):
    """`/install/windows.ps1` 404'd, so every Windows join fell back to pip."""
    status, win = await _get(app, "/install/windows.ps1")
    assert status == 200
    assert "navig.run/install.ps1" in win


async def test_config_returns_url_token_and_hint(app):
    status, body = await _get(app, "/install/config")
    assert status == 200
    data = json.loads(body)
    assert data["gateway_url"].startswith("http://127.0.0.1:")
    assert data["mesh_token"] == "fleet-tok"
    assert data["install_hint"].endswith("/install/unix.sh | bash")
    assert data["install_hint_windows"].endswith("/install/windows.ps1 | iex")


async def test_scripts_ping_back_with_the_fleet_token(app):
    """The joiner authenticates /mesh/ping with the token its script carries."""
    status, win = await _get(app, "/install/windows.ps1")
    assert status == 200
    assert 'Authorization = "Bearer $Token"' in win
    assert "$Token   = 'fleet-tok'" in win

    status, nix = await _get(app, "/install/unix.sh")
    assert status == 200
    assert '"Authorization: Bearer $TOKEN"' in nix
    assert "TOKEN='fleet-tok'" in nix


async def test_page_shows_short_commands_and_never_the_token(app):
    """The old page printed the mesh token in the visible one-liner — screenshots, chat logs."""
    status, body = await _get(app, "/install")
    assert status == 200
    assert "fleet-tok" not in body
    assert "irm http://127.0.0.1:8789/install/windows.ps1 | iex" in body
    assert "curl -fsSL http://127.0.0.1:8789/install/unix.sh | bash" in body


async def test_page_makes_no_external_requests(app):
    """The QR came from api.qrserver.com — the LAN URL went to the internet, offline it broke."""
    _, body = await _get(app, "/install")
    assert "qrserver" not in body
    assert "<svg" in body and 'aria-label="Scan to open the NAVIG join page"' in body
    for marker in ('src="http', "src='http", "url(http", "@import", 'href="http'):
        assert marker not in body, f"external resource: {marker}"


async def test_page_copy_works_without_a_secure_context(app):
    """navigator.clipboard is undefined on plain-http LAN pages — Copy did nothing."""
    _, body = await _get(app, "/install")
    assert "isSecureContext" in body
    assert 'execCommand("copy")' in body
    assert "alert(" not in body  # no blocking dialogs


async def test_page_points_at_the_command_that_shows_peers(app):
    """`navig host list` never shows mesh peers; the old page told users to run it."""
    _, body = await _get(app, "/install")
    assert "navig mesh peers" in body
    assert "navig host list" not in body


async def test_page_and_scripts_declare_utf8():
    """PowerShell 5.1 decodes `irm` bodies by charset — without it, non-ASCII is mojibake."""
    pytest.importorskip("aiohttp")
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from navig.gateway.routes import install as install_routes

    application = web.Application()
    install_routes.register(application, _gw())
    async with TestClient(TestServer(application)) as client:
        for path in ("/install", "/install/windows.ps1", "/install/unix.sh"):
            r = await client.get(path)
            assert r.charset == "utf-8", path
            assert r.headers.get("Cache-Control") == "no-store", path


async def test_non_lan_requester_gets_403_not_a_secret():
    """A gateway reachable from the internet must hand nothing out."""
    from navig.gateway.routes import install as install_routes

    handler = install_routes._config(_gw())
    resp = await handler(SimpleNamespace(remote="8.8.8.8"))
    assert resp.status == 403
    body = json.loads(resp.body)
    assert body["error_code"] == "not_lan"
    assert "fleet-tok" not in resp.body.decode()


async def test_loopback_requester_is_lan():
    from navig.gateway.routes import install as install_routes

    handler = install_routes._config(_gw())
    resp = await handler(SimpleNamespace(remote="127.0.0.1"))
    assert resp.status == 200
    assert b"fleet-tok" in resp.body


@pytest.mark.parametrize(
    "marker",
    ["CF-Ray", "X-Navig-Via"],
    ids=["cloudflare-tunnel", "lighthouse-replay"],
)
async def test_tunneled_request_from_loopback_gets_no_bootstrap(marker):
    """A tunnel terminates on this machine, so `remote` is 127.0.0.1 — and the
    LAN check alone would hand it the mesh_token.

    Cloudflare stamps CF-Ray on everything it proxies and the lighthouse
    replay stamps X-Navig-Via; neither can be stripped by the caller, so either
    one means "not a LAN peer" no matter what the socket address says.
    """
    from navig.gateway.routes import install as install_routes

    handler = install_routes._config(_gw())
    req = SimpleNamespace(remote="127.0.0.1", headers={marker: "present"})
    resp = await handler(req)
    assert resp.status == 403
    body = json.loads(resp.body)
    assert body["error_code"] == "not_lan"
    assert "fleet-tok" not in resp.body.decode()


async def test_plain_loopback_with_headers_is_still_lan():
    """The marker is what refuses — not having headers at all (native, curl)."""
    from navig.gateway.routes import install as install_routes

    handler = install_routes._config(_gw())
    req = SimpleNamespace(remote="127.0.0.1", headers={})
    resp = await handler(req)
    assert resp.status == 200


# ── /mesh/ping: the joiner's credential ──────────────────────────────────────


def _ping_handler(gw=None):
    from navig.gateway.routes.mesh import _ping

    return _ping(gw or _gw())


def _req(headers: dict | None = None, payload: dict | None = None):
    async def json_body():
        return payload if payload is not None else {}

    return SimpleNamespace(
        headers=headers or {},
        remote="10.0.0.5",
        json=json_body,
    )


async def test_ping_accepts_the_fleet_mesh_token():
    """The one-liner's ping-back uses the token that flow distributed."""
    handler = _ping_handler()
    resp = await handler(
        _req(
            headers={"Authorization": "Bearer fleet-tok"},
            payload={"gateway_url": ""},
        )
    )
    # Auth PASSED — it got past the guard and failed on the empty URL (400),
    # not on credentials (401). No peer bootstrap is attempted.
    assert resp.status == 400


async def test_ping_still_accepts_the_admin_token():
    handler = _ping_handler()
    resp = await handler(
        _req(
            headers={"Authorization": "Bearer admin-tok"},
            payload={"gateway_url": ""},
        )
    )
    assert resp.status == 400


async def test_ping_without_credentials_is_401():
    handler = _ping_handler()
    resp = await handler(_req(headers={}, payload={"gateway_url": ""}))
    assert resp.status == 401


async def test_ping_with_a_foreign_token_is_401():
    handler = _ping_handler()
    resp = await handler(
        _req(headers={"Authorization": "Bearer wrong"}, payload={"gateway_url": ""})
    )
    assert resp.status == 401


async def test_ping_fails_closed_when_no_credential_is_configured():
    """Gate 0 behavior must survive the extra_tokens addition."""
    gw = _gw()
    gw.config.auth_token = None
    gw.config_manager.global_config = {"gateway": {}}
    handler = _ping_handler(gw)
    resp = await handler(_req(headers={"Authorization": "Bearer x"}))
    assert resp.status == 503


# ── /mesh/ping: a joiner that names itself "localhost" ───────────────────────


def test_loopback_gateway_url_is_rewritten_to_the_requester() -> None:
    """The join one-liner sent http://localhost:8789 — the hub then bootstrapped from ITSELF."""
    from navig.gateway.routes.mesh import _resolve_peer_url

    assert _resolve_peer_url("http://localhost:8789", "192.0.2.13") == "http://192.0.2.13:8789"
    assert _resolve_peer_url("http://127.0.0.1:8790", "10.0.0.5") == "http://10.0.0.5:8790"
    assert _resolve_peer_url("http://0.0.0.0:8789", "fd00::5") == "http://[fd00::5]:8789"


def test_a_real_peer_address_is_left_alone() -> None:
    from navig.gateway.routes.mesh import _resolve_peer_url

    assert _resolve_peer_url("http://192.0.2.13:8789", "192.0.2.99") == "http://192.0.2.13:8789"


def test_a_local_ping_keeps_its_loopback_url() -> None:
    """A ping from this same machine has no better address to offer."""
    from navig.gateway.routes.mesh import _resolve_peer_url

    assert _resolve_peer_url("http://localhost:8789", "127.0.0.1") == "http://localhost:8789"
    assert _resolve_peer_url("http://localhost:8789", "") == "http://localhost:8789"


def test_registry_never_files_its_own_record_as_a_peer(tmp_path, monkeypatch) -> None:
    """The self-bootstrap upserted this node's OWN record with is_self=False — it then
    listed itself in `navig mesh peers` and persisted that to mesh_peers.json."""
    from dataclasses import replace

    from navig.mesh.registry import NodeRegistry

    reg = NodeRegistry(tmp_path)
    own_id = reg._self.node_id
    reg.upsert_peer(replace(reg._self))  # what a self-bootstrap hands back
    assert reg.get_peers() == []
    assert reg._peers[own_id].is_self is True  # our own record is untouched

    other = replace(reg._self, node_id="navig-windows-laptop-0001")
    reg.upsert_peer(other)  # a real peer still registers
    assert [p.node_id for p in reg.get_peers()] == ["navig-windows-laptop-0001"]
