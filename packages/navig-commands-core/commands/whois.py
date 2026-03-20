"""
navig-commands-core/commands/whois.py

RDAP-based domain / IP lookup — stdlib only (urllib).
"""
from __future__ import annotations
import json
from urllib.request import urlopen, Request
from urllib.error import URLError
from typing import Any

_RDAP_DOMAIN = "https://rdap.org/domain/{}"
_RDAP_IP     = "https://rdap.org/ip/{}"

_HEADERS = {"Accept": "application/json", "User-Agent": "navig-commands-core/1.1"}


def _rdap_fetch(url: str, timeout: float) -> dict:
    req = Request(url, headers=_HEADERS)
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _is_ip(value: str) -> bool:
    import socket
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            socket.inet_pton(family, value)
            return True
        except OSError:
            pass  # best-effort cleanup
    return False


def handle(args: dict, ctx: Any = None) -> dict:
    """
    Look up RDAP info for a domain or IP address.

    args:
      target (str): Domain name or IP address.
      timeout (float, optional): Request timeout seconds (default 10).
    """
    target = args.get("target", "").strip()
    if not target:
        return {"status": "error", "message": "Missing 'target' argument"}
    timeout = float(args.get("timeout", 10.0))

    url = _RDAP_IP.format(target) if _is_ip(target) else _RDAP_DOMAIN.format(target)
    try:
        raw = _rdap_fetch(url, timeout)
    except URLError as exc:
        return {"status": "error", "message": f"RDAP lookup failed: {exc}", "target": target}
    except Exception as exc:
        return {"status": "error", "message": str(exc), "target": target}

    # Extract the most useful fields
    result: dict[str, Any] = {"target": target, "rdap_url": url}

    if "ldhName" in raw:              # domain
        result["name"] = raw.get("ldhName")
        result["status"] = raw.get("status", [])
        nameservers = [ns.get("ldhName") for ns in raw.get("nameservers", [])]
        result["nameservers"] = nameservers
        events = {e["eventAction"]: e["eventDate"] for e in raw.get("events", [])}
        result["registered"] = events.get("registration")
        result["expires"] = events.get("expiration")
        entities = []
        for e in raw.get("entities", []):
            roles = e.get("roles", [])
            vcards = e.get("vcardArray", [None, []])
            name_entry = next((v[3] for v in vcards[1] if v[0] == "fn"), None) if len(vcards) > 1 else None
            entities.append({"roles": roles, "name": name_entry})
        result["entities"] = entities

    elif "ipVersion" in raw:          # IP
        result["ip_version"] = raw.get("ipVersion")
        result["handle"] = raw.get("handle")
        result["country"] = raw.get("country")
        result["name"] = raw.get("name")
        result["type"] = raw.get("type")

    return {"status": "ok", "data": result}
