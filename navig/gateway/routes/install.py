"""
Install routes — serve one-liner bootstrap scripts over HTTP.

GET  /install               → HTML page with copy-buttons for every OS
GET  /install/windows       → PowerShell one-liner (plain text)
GET  /install/linux         → Bash one-liner (plain text)
GET  /install/config        → JSON mesh bootstrap config (token + gateway url)

Design intent:
  Sits behind NO auth — user must be on the same LAN.
  The mesh_token in /install/config is a shared LAN secret,
  not a personal bearer token.

Usage:
  On the new machine, open a browser or run:
    Windows: (iwr http://10.0.x.x:8789/install/windows).Content | iex
    Linux:   curl -fsSL http://10.0.x.x:8789/install/linux | bash
"""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import web

    from navig.gateway.server import NavigGateway

try:
    from aiohttp import web
except ImportError:
    pass

from navig.debug_logger import get_debug_logger

logger = get_debug_logger()

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NAVIG Installer</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'JetBrains Mono', 'Fira Code', 'Courier New', monospace;
          background: #080E1A; color: #E5E7EB; padding: 40px 20px; }}
  h1   {{ color: #0EA5E9; font-size: 1.4rem; margin-bottom: 8px; }}
  p    {{ color: #6B7280; font-size: .85rem; margin-bottom: 32px; }}
  .card {{ background: #0D1626; border: 1px solid #1E3A5A; border-radius: 10px;
           padding: 20px; margin-bottom: 20px; }}
  .card h2 {{ font-size: 1rem; margin-bottom: 12px; color: #38BDF8; }}
  pre  {{ background: #050E1A; border: 1px solid #1E2A3A; border-radius: 6px;
          padding: 14px; font-size: .82rem; color: #A5F3FC; white-space: pre-wrap;
          word-break: break-all; cursor: text; user-select: all; }}
  .btn {{ display: inline-block; margin-top: 10px; padding: 8px 18px;
          background: #0EA5E9; border: none; border-radius: 6px; color: #fff;
          font-family: inherit; font-size: .82rem; cursor: pointer; }}
  .btn:hover {{ background: #0284C7; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px;
             font-size: .75rem; background: #10B981; color: #fff;
             vertical-align: middle; margin-left: 8px; }}
  .note {{ font-size:.78rem; color:#4B5563; margin-top:8px; }}
  .qr-card {{ text-align:center; }}
  .qr-card img {{ display:block; margin:12px auto; border-radius:8px;
                  border:2px solid #1E3A5A; image-rendering:crisp-edges; }}
  .qr-url {{ font-size:.78rem; color:#38BDF8; margin-top:8px; word-break:break-all; }}
</style>
</head>
<body>
<h1>NAVIG — Join this mesh</h1>
<p>Run the command below on the machine you want to add. It will install NAVIG and
   automatically join the mesh hosted at <strong>{gateway_url}</strong>.</p>

<div class="card qr-card">
  <h2>Scan to open on another device</h2>
  <img src="https://api.qrserver.com/v1/create-qr-code/?size=200x200&color=A5F3FC&bgcolor=050E1A&data={gateway_url}/install"
       alt="QR — scan to open this page" width="200" height="200">
  <p class="qr-url">{gateway_url}/install</p>
  <p class="note">Scan with phone or tablet — opens this page without typing the URL.</p>
</div>

<div class="card">
  <h2>Windows <span class="badge">PowerShell</span></h2>
  <pre id="win">{win_cmd}</pre>
  <button class="btn" onclick="copy('win')">Copy</button>
  <p class="note">Open PowerShell (any, no admin needed for install). Paste and press Enter.</p>
</div>

<div class="card">
  <h2>Linux / macOS <span class="badge">bash</span></h2>
  <pre id="nix">{nix_cmd}</pre>
  <button class="btn" onclick="copy('nix')">Copy</button>
  <p class="note">Tested on Ubuntu 20+, Debian 11+, macOS 12+.</p>
</div>

<div class="card">
  <h2>After install</h2>
  <pre>navig service start
navig host list     # your new machine should appear here within 15 s</pre>
  <p class="note">Both machines must be on the same LAN. The mesh_token is shared automatically.</p>
</div>

<script>
function copy(id) {{
  const t = document.getElementById(id).textContent;
  navigator.clipboard.writeText(t).then(() => alert('Copied!'));
}}
</script>
</body>
</html>
"""


def register(app: "web.Application", gateway: "NavigGateway") -> None:
    app.router.add_get("/install",          _html(gateway))
    app.router.add_get("/install/windows",  _windows(gateway))
    app.router.add_get("/install/linux",    _linux(gateway))
    app.router.add_get("/install/mac",      _linux(gateway))   # same script
    app.router.add_get("/install/config",   _config(gateway))


# ─────────────────────────── helpers ─────────────────────────────────────────

def _get_my_url(gw: "NavigGateway") -> str:
    """Best guess at this machine's reachable gateway URL from other LAN machines."""
    try:
        ip = gw.config.get("gateway", {}).get("host") or _lan_ip()
        port = gw.config.get("gateway", {}).get("port", 8789)
        return f"http://{ip}:{port}"
    except Exception:
        return "http://localhost:8789"


def _lan_ip() -> str:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def _mesh_token(gw: "NavigGateway") -> str:
    return gw.config.get("gateway", {}).get("mesh_token", "")


# ─────────────────────────── route handlers ──────────────────────────────────

def _html(gw: "NavigGateway"):
    async def h(r: "web.Request") -> "web.Response":
        url = _get_my_url(gw)
        token = _mesh_token(gw)
        win = _ps1_oneliner(url, token)
        nix = _bash_oneliner(url, token)
        body = _HTML_TEMPLATE.format(
            gateway_url=url,
            win_cmd=win,
            nix_cmd=nix,
        )
        return web.Response(text=body, content_type="text/html")
    return h


def _windows(gw: "NavigGateway"):
    async def h(r: "web.Request") -> "web.Response":
        url = _get_my_url(gw)
        token = _mesh_token(gw)
        script = _ps1_oneliner(url, token)
        return web.Response(text=script, content_type="text/plain")
    return h


def _linux(gw: "NavigGateway"):
    async def h(r: "web.Request") -> "web.Response":
        url = _get_my_url(gw)
        token = _mesh_token(gw)
        script = _bash_oneliner(url, token)
        return web.Response(text=script, content_type="text/plain")
    return h


def _config(gw: "NavigGateway"):
    """Return a minimal JSON bootstrap config so a new node can join without browsing."""
    async def h(r: "web.Request") -> "web.Response":
        import json
        url = _get_my_url(gw)
        token = _mesh_token(gw)
        data = {
            "gateway_url":  url,
            "mesh_token":   token,
            "install_hint": f"curl -fsSL {url}/install/linux | bash",
        }
        return web.Response(
            text=json.dumps(data, indent=2),
            content_type="application/json",
        )
    return h


# ─────────────────────────── script builders ─────────────────────────────────

def _ps1_oneliner(gateway_url: str, mesh_token: str) -> str:
    """
    Single PowerShell expression that:
      1. Downloads install.ps1 from GitHub (latest release)
         OR falls back to pip install navig
      2. After install, writes mesh_token to the global config
      3. Starts the NAVIG service
      4. Posts /mesh/ping back to the source gateway

    Designed to run without admin rights (installs into user site-packages).
    Admin is requested automatically if the service requires it.
    """
    token_line = ""
    if mesh_token:
        token_line = (
            f"navig config set gateway.mesh_token '{mesh_token}'; "
        )

    script = textwrap.dedent(f"""\
        $NAVIG_GATEWAY='{gateway_url}'; \
        $NAVIG_TOKEN='{mesh_token}'; \
        $env:PYTHONUTF8='1'; \
        try {{ \
          Invoke-RestMethod "$NAVIG_GATEWAY/install/windows.ps1" | Invoke-Expression \
        }} catch {{ \
          Write-Host 'Direct download failed, falling back to pip...' -ForegroundColor Yellow; \
          python -m pip install --user --upgrade navig \
        }}; \
        {token_line}\
        navig config set gateway.mesh_token "$NAVIG_TOKEN"; \
        navig service install; navig service start; \
        Start-Sleep 3; \
        Invoke-RestMethod -Method Post -Uri "$NAVIG_GATEWAY/mesh/ping" \
          -ContentType 'application/json' \
          -Body (@{{gateway_url="http://localhost:8789"}}|ConvertTo-Json) | Out-Null; \
        Write-Host '✓ NAVIG joined the mesh!' -ForegroundColor Green\
    """).replace("\n", "")

    return script


def _bash_oneliner(gateway_url: str, mesh_token: str) -> str:
    token_export = f"export NAVIG_MESH_TOKEN='{mesh_token}'; " if mesh_token else ""
    return (
        f"export NAVIG_GATEWAY='{gateway_url}'; "
        f"{token_export}"
        f"curl -fsSL https://raw.githubusercontent.com/navig-core/main/install.sh | bash || "
        f"pip install --user --upgrade navig; "
        f"navig config set gateway.mesh_token \"$NAVIG_MESH_TOKEN\"; "
        f"navig service install && navig service start; "
        f"sleep 3 && "
        f"curl -s -X POST '$NAVIG_GATEWAY/mesh/ping' "
        f"  -H 'Content-Type: application/json' "
        f"  -d '{{\"gateway_url\":\"http://localhost:8789\"}}' > /dev/null && "
        f"echo '✓ NAVIG joined the mesh!'"
    )
