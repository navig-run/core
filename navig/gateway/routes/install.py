"""
Install routes — the LAN "add a device" flow.

GET  /install               → the join page (NAVIG OS look, OS tabs, QR, copy buttons)
GET  /install/windows.ps1   → the Windows join script     (run: irm <url> | iex)
GET  /install/unix.sh       → the macOS/Linux join script (run: curl -fsSL <url> | bash)
GET  /install/config        → JSON mesh bootstrap config (token + gateway url)

Aliases kept for every command ever printed: ``/install/windows`` serves the
Windows script, ``/install/linux`` and ``/install/mac`` the unix one — the old
``& ([scriptblock]::Create((iwr …/install/windows).Content))`` and
``curl -fsSL …/install/linux | bash`` forms keep working.

Design intent:
  The join flow is a LAN service. Every route is gated to private/loopback
  requesters (`mesh.url_guard.ip_is_lan`) — a gateway that happens to be
  reachable from the internet hands NOTHING out here. Inside the LAN the
  mesh_token is a shared fleet secret (the same value `flux install --push`
  distributes), not a personal bearer token: it authenticates the joiner's
  `/mesh/ping` and later peer hops, never an admin route.

  The PAGE never shows the token: the visible commands only fetch a script,
  and the token travels inside that script. (The old page printed it in the
  one-liner, so it ended up in screenshots and chat logs.)
"""

from __future__ import annotations

import json
import socket
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from navig.gateway.server import NavigGateway

try:
    from aiohttp import web
except ImportError as _exc:
    raise RuntimeError("aiohttp is required for gateway routes (pip install aiohttp)") from _exc

from navig._daemon_defaults import _GATEWAY_PORT
from navig.debug_logger import get_debug_logger
from navig.gateway.routes import _install_assets as assets

logger = get_debug_logger()

_TEXT = "text/plain"


def register(app: web.Application, gateway: NavigGateway) -> None:
    app.router.add_get("/install", _html(gateway))
    app.router.add_get("/install/windows.ps1", _windows(gateway))
    app.router.add_get("/install/unix.sh", _unix(gateway))
    app.router.add_get("/install/config", _config(gateway))
    # Aliases — every command ever printed for this flow keeps working.
    app.router.add_get("/install/windows", _windows(gateway))
    app.router.add_get("/install/linux", _unix(gateway))
    app.router.add_get("/install/mac", _unix(gateway))


# ─────────────────────────── helpers ─────────────────────────────────────────


def _tunneled(request: web.Request) -> bool:
    """True when this request arrived through a tunnel/replay, not from the LAN.

    `request.remote` is the SOCKET peer, and a cloudflared quick tunnel
    terminates on this machine — so a request from the public internet presents
    as 127.0.0.1 and `ip_is_lan` waves it through to `/install/config` and the
    mesh_token inside it. The same two markers the deck's local-desktop bypass
    uses (`gateway/deck/auth.py::_request_is_local`) decide it: Cloudflare
    stamps `CF-Ray` on everything it proxies and the lighthouse replay stamps
    `X-Navig-Via`, neither of which a caller can strip on the way in.
    """
    headers = getattr(request, "headers", None) or {}
    return bool(headers.get("CF-Ray") or headers.get("X-Navig-Via"))


def _lan_only(request: web.Request) -> web.Response | None:
    """Refuse token-bearing bootstrap content to non-LAN requesters.

    Every route on this module embeds or returns the fleet-shared mesh_token,
    so every route inherits this gate. `ip_is_lan` accepts private + loopback
    (not link-local): a gateway accidentally exposed to the public internet
    answers 403 here instead of handing out its join secret — and a tunneled
    request (loopback peer, CF-Ray marker) is refused too, which is the shape
    the LAN check alone could not see.
    """
    from navig.mesh.url_guard import ip_is_lan

    if _tunneled(request):
        logger.warning("[install] refused tunnel-forwarded bootstrap request")
        return web.json_response(
            {
                "ok": False,
                "error": "The join page is served on the LAN only",
                "error_code": "not_lan",
            },
            status=403,
        )

    remote = str(getattr(request, "remote", "") or "")
    if ip_is_lan(remote):
        return None
    logger.warning(
        "[install] refused bootstrap from non-LAN remote %s", remote or "?"
    )
    return web.json_response(
        {
            "ok": False,
            "error": "The join page is served on the LAN only",
            "error_code": "not_lan",
        },
        status=403,
    )


def _get_my_url(gw: NavigGateway) -> str:
    """Best guess at this machine's reachable gateway URL from other LAN machines.

    A loopback bind (`127.0.0.1`, `::1`, `localhost`) is exactly as unreachable
    from another machine as `0.0.0.0` is reachable — so it advertises the LAN IP
    too. Measured 2026-10-27: a brain defaulting to `host: 127.0.0.1` handed a
    joining laptop `irm http://127.0.0.1:8789/…`, i.e. the laptop's own loopback;
    the command could never reach the gateway it came from.
    """
    try:
        host = str(getattr(gw.config, "host", "") or "")
        if host in ("", "0.0.0.0", "::", "[::]") or host.lower() == "localhost" or host.startswith(
            "127."
        ):
            host = _lan_ip()
        port = int(getattr(gw.config, "port", None) or _GATEWAY_PORT)
        return f"http://{host}:{port}"
    except Exception:
        return f"http://localhost:{_GATEWAY_PORT}"


def _lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def _mesh_token(gw: NavigGateway) -> str:
    """The fleet-shared peer secret — request-time read (first-boot mint safe)."""
    from navig.gateway.routes.common import gateway_mesh_token

    return gateway_mesh_token(gw) or ""


def _hub_name() -> str:
    return socket.gethostname() or "your NAVIG"


def _peer_count(gw: NavigGateway) -> int | None:
    """Devices already linked — a page decoration, so any failure is just 'unknown'."""
    try:
        from navig.mesh.registry import get_registry

        return len(get_registry(gw.storage_dir).get_peers())
    except Exception:  # noqa: BLE001 — never let the badge break the page
        return None


def windows_command(gateway_url: str) -> str:
    """What the page shows for Windows — a fetch, never the token itself."""
    return f"irm {gateway_url}/install/windows.ps1 | iex"


def unix_command(gateway_url: str) -> str:
    """What the page shows for macOS / Linux — a fetch, never the token itself."""
    return f"curl -fsSL {gateway_url}/install/unix.sh | bash"


def _qr_svg(page_url: str) -> str:
    try:
        from navig.core.qr import qr_svg

        return qr_svg(page_url, title="Scan to open the NAVIG join page")
    except Exception as exc:  # noqa: BLE001 — a missing QR must never break the page
        logger.warning("[install] QR render failed: %s", exc)
        return ""


# ─────────────────────────── route handlers ──────────────────────────────────


def _html(gw: NavigGateway):
    async def h(r: web.Request) -> web.Response:
        blocked = _lan_only(r)
        if blocked is not None:
            return blocked
        url = _get_my_url(gw)
        body = assets.page_html(
            hub_name=_hub_name(),
            gateway_url=url,
            win_cmd=windows_command(url),
            nix_cmd=unix_command(url),
            qr_svg=_qr_svg(f"{url}/install"),
            peer_count=_peer_count(gw),
        )
        return web.Response(
            text=body,
            content_type="text/html",
            charset="utf-8",
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    return h


def _windows(gw: NavigGateway):
    async def h(r: web.Request) -> web.Response:
        blocked = _lan_only(r)
        if blocked is not None:
            return blocked
        script = assets.windows_script(
            hub_name=_hub_name(), gateway_url=_get_my_url(gw), mesh_token=_mesh_token(gw)
        )
        # PowerShell 5.1 decodes `irm` bodies by this charset — without it the
        # script's non-ASCII bytes arrive as mojibake.
        return web.Response(
            text=script, content_type=_TEXT, charset="utf-8", headers={"Cache-Control": "no-store"}
        )

    return h


def _unix(gw: NavigGateway):
    async def h(r: web.Request) -> web.Response:
        blocked = _lan_only(r)
        if blocked is not None:
            return blocked
        script = assets.unix_script(
            hub_name=_hub_name(), gateway_url=_get_my_url(gw), mesh_token=_mesh_token(gw)
        )
        return web.Response(
            text=script, content_type=_TEXT, charset="utf-8", headers={"Cache-Control": "no-store"}
        )

    return h


def _config(gw: NavigGateway):
    """Return a minimal JSON bootstrap config so a new node can join without browsing."""

    async def h(r: web.Request) -> web.Response:
        blocked = _lan_only(r)
        if blocked is not None:
            return blocked
        url = _get_my_url(gw)
        data = {
            "gateway_url": url,
            "mesh_token": _mesh_token(gw),
            "install_hint": unix_command(url),
            "install_hint_windows": windows_command(url),
        }
        return web.Response(
            text=json.dumps(data, indent=2),
            content_type="application/json",
            headers={"Cache-Control": "no-store"},
        )

    return h
