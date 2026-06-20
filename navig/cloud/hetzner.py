"""Hetzner Cloud provider for navig hosts.

A navig host can carry a cloud identity alongside its SSH transport. The host
YAML gets:

    cloud_provider:
      type: hetzner
      credential_id: a1bb12c2   # vault credential → one key per host (many keys ok)
      server_id: 113224359      # optional; cached after first discovery
      region: eu-central

This module resolves the per-host token from the vault (falling back to the
``hetzner`` provider key / ``HETZNER_API_TOKEN`` env) and talks to
api.hetzner.cloud. Pure stdlib + the vault, so importing it never pulls heavy
deps — it stays cheap to load inside the ``navig host`` command group.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

API = "https://api.hetzner.cloud/v1"
_TIMEOUT = 15


class HetznerError(RuntimeError):
    """Raised on any Hetzner API / auth failure (message is human-readable)."""


def token_for_host(
    host_cfg: dict[str, Any] | None = None,
    *,
    credential_id: str | None = None,
) -> str | None:
    """Resolve the Hetzner API token for a host.

    Order: explicit ``credential_id`` → host ``cloud_provider.credential_id``
    (vault ``get_by_id``) → the ``hetzner`` provider key / ``HETZNER_API_TOKEN``.
    Returns None when no key is configured (caller decides how to report it).
    """
    from navig.vault import get_vault

    vault = get_vault()
    cid = credential_id
    if cid is None and host_cfg:
        cid = (host_cfg.get("cloud_provider") or {}).get("credential_id")
    if cid:
        try:
            cred = vault.get_by_id(cid, caller="host.hetzner")
        except Exception:  # noqa: BLE001
            cred = None
        if cred is not None and getattr(cred, "data", None):
            tok = cred.data.get("token") or cred.data.get("api_key") or cred.data.get("value")
            if tok:
                return str(tok)
    # Provider-level fallback (active profile) + HETZNER_API_KEY env.
    try:
        return vault.get_api_key("hetzner", caller="host.hetzner")
    except Exception:  # noqa: BLE001
        return None


def has_cloud(host_cfg: dict[str, Any] | None) -> bool:
    """True if the host YAML declares a Hetzner cloud identity."""
    return bool(host_cfg) and (host_cfg.get("cloud_provider") or {}).get("type") == "hetzner"


def _req(path: str, token: str, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read() or b"{}")
            msg = (payload.get("error") or {}).get("message") or f"HTTP {e.code}"
        except Exception:  # noqa: BLE001
            msg = f"HTTP {e.code}"
        raise HetznerError(msg) from None
    except Exception as e:  # noqa: BLE001
        raise HetznerError(str(e)) from None


def list_servers(token: str) -> list[dict]:
    return _req("/servers", token).get("servers", [])


def get_server(token: str, server_id: int | str) -> dict | None:
    try:
        return _req(f"/servers/{server_id}", token).get("server")
    except HetznerError:
        return None


def find_server_by_ip(token: str, ip: str) -> dict | None:
    for s in list_servers(token):
        if (s.get("public_net", {}).get("ipv4") or {}).get("ip") == ip:
            return s
    return None


def list_firewalls(token: str) -> list[dict]:
    return _req("/firewalls", token).get("firewalls", [])


def server_action(token: str, server_id: int | str, action: str) -> dict:
    """Power op: action ∈ {poweron, poweroff, reboot, shutdown, reset}."""
    return _req(f"/servers/{server_id}/actions/{action}", token, method="POST")


def summarize_server(s: dict) -> dict:
    """Flatten a Hetzner server object to the fields navig host / the deck need."""
    return {
        "id": s.get("id"),
        "name": s.get("name"),
        "status": s.get("status"),  # running | off | starting | ...
        "ip": (s.get("public_net", {}).get("ipv4") or {}).get("ip"),
        "type": (s.get("server_type") or {}).get("name"),
        "region": (s.get("datacenter") or {}).get("location", {}).get("name"),
        "firewalls": [
            f.get("id") for f in (s.get("public_net", {}).get("firewalls") or [])
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Deck-facing cloud summary — static identity + best-effort LIVE state, cached.
# `/api/deck/hosts` is a hot endpoint, so we never block it on a slow/failed
# Hetzner call: live data is fetched once and memoized for `_CLOUD_TTL` seconds,
# and any failure degrades gracefully to the static YAML identity.
# ─────────────────────────────────────────────────────────────────────────────
_CLOUD_TTL = 60.0
_cloud_cache: dict[str, tuple[float, dict]] = {}


def host_cloud_summary(host_cfg: dict[str, Any] | None, *, ttl: float = _CLOUD_TTL) -> dict | None:
    """Return a deck-renderable cloud block for a host, or None if it has no
    cloud identity. Shape:

        {provider, server_id, region, power, server_type, firewalls:[{id,name}],
         name, has_key, live}

    `live` is True when the fields reflect a fresh Hetzner API read; False when
    they fall back to the static `cloud_provider` YAML. Never raises.
    """
    cp = (host_cfg or {}).get("cloud_provider") or {}
    if cp.get("type") != "hetzner":
        return None

    server_id = cp.get("server_id")
    base: dict[str, Any] = {
        "provider": "hetzner",
        "server_id": str(server_id) if server_id else None,
        "region": cp.get("region"),
        "power": None,
        "server_type": None,
        "firewalls": [],
        "name": None,
        "has_key": False,
        "live": False,
    }

    ckey = f"hetzner:{server_id or (host_cfg or {}).get('host') or 'unknown'}"
    now = time.time()
    cached = _cloud_cache.get(ckey)
    if cached and now - cached[0] < ttl:
        return {**base, **cached[1]}

    token = token_for_host(host_cfg)
    base["has_key"] = bool(token)
    if not token:
        _cloud_cache[ckey] = (now, {"has_key": False})
        return base

    try:
        s = get_server(token, server_id) if server_id else None
        if s is None:
            ip = (host_cfg or {}).get("host")
            if ip:
                s = find_server_by_ip(token, ip)
        if not s:
            _cloud_cache[ckey] = (now, {"has_key": True})
            return base

        summ = summarize_server(s)
        fw_ids = [str(f) for f in (summ.get("firewalls") or [])]
        fw: list[dict] = [{"id": i, "name": None} for i in fw_ids]
        if fw_ids:
            try:
                names = {str(f.get("id")): f.get("name") for f in list_firewalls(token)}
                fw = [{"id": i, "name": names.get(i)} for i in fw_ids]
            except HetznerError:
                pass

        live = {
            "server_id": str(summ["id"]) if summ.get("id") else base["server_id"],
            "region": summ.get("region") or cp.get("region"),
            "power": summ.get("status"),      # running | off | starting | ...
            "server_type": summ.get("type"),  # e.g. cpx32
            "firewalls": fw,
            "name": summ.get("name"),
            "has_key": True,
            "live": True,
        }
        _cloud_cache[ckey] = (now, live)
        return {**base, **live}
    except Exception:  # noqa: BLE001 — deck endpoint must never break on cloud
        _cloud_cache[ckey] = (now, {"has_key": True})
        return base
