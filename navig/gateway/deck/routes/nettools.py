"""NetTools — IP / DNS / SSL / WHOIS / Weather diagnostics for the Deck.

Each handler is a thin, self-contained read-only probe using stdlib + a couple
of well-known public services (wttr.in for weather). Defensive: any failure
returns ``{ok: false, error: ...}`` so the UI can render a clean error card.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import ssl
import subprocess
from datetime import datetime, timezone
from typing import Any

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)


def _ok(data: object, status: int = 200) -> "web.Response":
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 500) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


def _clean_domain(s: str) -> str:
    return (s or "").strip().lstrip("https://").lstrip("http://").rstrip("/").split("/")[0].lower()


async def handle_deck_net_server(request: "web.Request") -> "web.Response":
    """Server snapshot: hostname, public IP (best-effort), local interfaces, time."""
    try:
        hostname = socket.gethostname()
    except Exception:
        hostname = ""
    local_ip = ""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
    except Exception:
        pass
    public_ip = ""
    try:
        proc = await asyncio.create_subprocess_exec(
            "curl", "-sS", "--max-time", "4", "https://api.ipify.org",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        public_ip = (out or b"").decode().strip()
    except Exception:
        pass
    return _ok({
        "hostname": hostname,
        "local_ip": local_ip,
        "public_ip": public_ip,
        "time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })


async def handle_deck_net_dns(request: "web.Request") -> "web.Response":
    domain = _clean_domain(request.query.get("domain", ""))
    record_type = request.query.get("type", "A").upper()
    if not domain:
        return _err("missing ?domain=", status=400)
    try:
        # Use socket for A records (fast & dependency-free); shell out to nslookup for others.
        if record_type == "A":
            infos = await asyncio.get_event_loop().run_in_executor(
                None, lambda: socket.getaddrinfo(domain, None, socket.AF_INET),
            )
            addresses = sorted({i[4][0] for i in infos})
            return _ok({"domain": domain, "type": "A", "answers": addresses})
        else:
            proc = await asyncio.create_subprocess_exec(
                "nslookup", "-type=" + record_type, domain,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=8)
            text = (out or b"").decode(errors="replace")
            return _ok({"domain": domain, "type": record_type, "raw": text})
    except Exception as exc:
        return _err(str(exc))


async def handle_deck_net_ssl(request: "web.Request") -> "web.Response":
    domain = _clean_domain(request.query.get("domain", ""))
    if not domain:
        return _err("missing ?domain=", status=400)
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((domain, 443), timeout=6) as sock:
            with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert()
        not_after = cert.get("notAfter", "")
        not_before = cert.get("notBefore", "")
        subject = dict(x[0] for x in cert.get("subject", []))
        issuer = dict(x[0] for x in cert.get("issuer", []))
        san = [v for k, v in cert.get("subjectAltName", []) if k == "DNS"]
        # parse expiry
        days_left = None
        try:
            exp = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
            days_left = (exp - datetime.now(timezone.utc)).days
        except Exception:
            pass
        return _ok({
            "domain": domain,
            "common_name": subject.get("commonName", ""),
            "issuer": issuer.get("organizationName", "") or issuer.get("commonName", ""),
            "not_before": not_before,
            "not_after": not_after,
            "days_left": days_left,
            "san": san,
        })
    except Exception as exc:
        return _err(str(exc))


async def handle_deck_net_whois(request: "web.Request") -> "web.Response":
    domain = _clean_domain(request.query.get("domain", ""))
    if not domain:
        return _err("missing ?domain=", status=400)
    try:
        # Bare TCP whois — works for most TLDs without a system whois binary.
        loop = asyncio.get_event_loop()
        def _query() -> str:
            with socket.create_connection(("whois.iana.org", 43), timeout=5) as s:
                s.sendall((domain + "\r\n").encode())
                data = b""
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                    if len(data) > 65536:
                        break
            return data.decode(errors="replace")
        raw = await loop.run_in_executor(None, _query)
        # If IANA points to a TLD-specific server, follow once.
        refer = next((line.split(":", 1)[1].strip()
                      for line in raw.splitlines() if line.lower().startswith("refer:")), None)
        if refer:
            def _refer() -> str:
                with socket.create_connection((refer, 43), timeout=5) as s:
                    s.sendall((domain + "\r\n").encode())
                    data = b""
                    while True:
                        chunk = s.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                        if len(data) > 65536:
                            break
                return data.decode(errors="replace")
            try:
                raw = await loop.run_in_executor(None, _refer)
            except Exception:
                pass
        return _ok({"domain": domain, "raw": raw[:32768]})
    except Exception as exc:
        return _err(str(exc))


async def handle_deck_net_weather(request: "web.Request") -> "web.Response":
    city = (request.query.get("city", "") or "").strip()
    spec = city or ""
    try:
        url = f"https://wttr.in/{spec}?format=j1" if spec else "https://wttr.in/?format=j1"
        proc = await asyncio.create_subprocess_exec(
            "curl", "-sS", "--max-time", "8", url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        import json
        data = json.loads((out or b"{}").decode(errors="replace"))
        current = (data.get("current_condition") or [{}])[0]
        area = (data.get("nearest_area") or [{}])[0]
        return _ok({
            "city": (area.get("areaName") or [{}])[0].get("value", "") or city,
            "country": (area.get("country") or [{}])[0].get("value", ""),
            "temp_c": current.get("temp_C", ""),
            "feels_like_c": current.get("FeelsLikeC", ""),
            "humidity_pct": current.get("humidity", ""),
            "wind_kmh": current.get("windspeedKmph", ""),
            "description": (current.get("weatherDesc") or [{}])[0].get("value", ""),
            "observation_time": current.get("observation_time", ""),
        })
    except Exception as exc:
        return _err(str(exc))
