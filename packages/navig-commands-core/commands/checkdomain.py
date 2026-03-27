"""
commands/checkdomain.py - Domain availability checker via RDAP.

Transport-agnostic: zero Telegram/Discord/CLI imports.
Works identically when called from CLI agent, Telegram adapter, or any channel.

Returns
-------
dict with keys:
    status  : "available" | "taken" | "error"
    domain  : str  - the queried domain
    details : str  - human-readable explanation
"""

from __future__ import annotations

import asyncio
import re
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from handler import PluginContext

# Strict domain validation pattern
_VALID_DOMAIN = re.compile(
    r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?" r"(?:\.[a-zA-Z]{2,})+$"
)

_RDAP_BASE = "https://rdap.org/domain/"


def _rdap_lookup(domain: str) -> dict:
    """Synchronous RDAP lookup. Call via asyncio.to_thread in async context."""
    url = f"{_RDAP_BASE}{domain}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                return {
                    "status": "taken",
                    "domain": domain,
                    "details": f"{domain} is already registered (RDAP 200).",
                }
        return {
            "status": "error",
            "domain": domain,
            "details": "Unexpected RDAP response.",
        }
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {
                "status": "available",
                "domain": domain,
                "details": f"{domain} appears to be available (RDAP 404 - not found).",
            }
        return {
            "status": "error",
            "domain": domain,
            "details": f"RDAP HTTP error {exc.code} for {domain}.",
        }
    except urllib.error.URLError as exc:
        return {
            "status": "error",
            "domain": domain,
            "details": f"Network error checking {domain}: {exc.reason}",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "domain": domain,
            "details": f"Unexpected error checking {domain}: {exc}",
        }


async def handle(args: dict, ctx: "PluginContext | None" = None) -> dict:
    """
    Check whether a domain is available.

    Parameters
    ----------
    args : dict
        Expected key: "domain" (str) - the domain to check, e.g. "example.com"
    ctx  : PluginContext | None
        Runtime context (unused for this handler; accepted for interface compat).

    Returns
    -------
    dict
        {"status": "available"|"taken"|"error", "domain": str, "details": str}
    """
    domain = (args.get("domain") or "").strip().lower().strip("./")

    if not domain:
        return {
            "status": "error",
            "domain": "",
            "details": "No domain provided. Usage: checkdomain example.com",
        }

    if not _VALID_DOMAIN.match(domain):
        return {
            "status": "error",
            "domain": domain,
            "details": f'"{domain}" is not a valid domain name.',
        }

    return await asyncio.to_thread(_rdap_lookup, domain)
