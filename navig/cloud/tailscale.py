"""
Tailscale Funnel manager — the canonical self-host path for Telegram
Mini App entry-points.

Why Tailscale Funnel
--------------------
Telegram requires the Mini App URL to be a public HTTPS URL set in
BotFather. The hosted ``relay.navig.run`` covers users who don't want to
self-host. For everyone else, Tailscale Funnel gives:

  * Free for personal use (no payment, no card)
  * Stable URL: ``https://<machine>.<tailnet>.ts.net`` -- never rotates
  * Automatic TLS via Let's Encrypt -- no manual cert wrangling
  * One command to enable

What this module does
---------------------
- Detect whether the ``tailscale`` CLI is installed
- Enable Funnel for the gateway's HTTP port (gateway.port, default 8789)
- Capture the public ``*.ts.net`` URL and write it to
  ``cloud.public_url`` so CloudManager picks up direct mode automatically
- Disable / status helpers

It does NOT:
- Install Tailscale (we print OS-specific install instructions instead;
  we won't pipe-to-sudo on the user's behalf)
- Touch Tailscale ACLs / authkeys -- the user is already on their tailnet
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sys
from dataclasses import dataclass
from typing import Any

from navig._daemon_defaults import _GATEWAY_PORT

logger = logging.getLogger(__name__)


@dataclass
class FunnelStatus:
    """Snapshot of the current Tailscale Funnel state for our port."""

    installed: bool
    logged_in: bool
    funnel_enabled: bool
    public_url: str | None
    forwarded_port: int | None
    error: str | None = None


# ── Detection / install hints ───────────────────────────────────────────────


def tailscale_binary() -> str | None:
    """Return the path to the ``tailscale`` CLI or None if missing."""
    return shutil.which("tailscale")


def install_hint() -> str:
    """OS-appropriate install instructions for Tailscale + Funnel access."""
    base = (
        "Tailscale not detected. Install:\n"
        "  Linux:   curl -fsSL https://tailscale.com/install.sh | sh\n"
        "  macOS:   brew install --cask tailscale   (or download from tailscale.com/download)\n"
        "  Windows: winget install --id tailscale.tailscale   (or download from tailscale.com/download)\n"
        "\n"
        "Then sign in:  tailscale up\n"
        "Funnel requires HTTPS + Funnel feature -- enable in the admin console:\n"
        "  https://login.tailscale.com/admin/dns  (turn on HTTPS Certificates)\n"
        "  https://login.tailscale.com/admin/acls (add 'funnel' node attribute)\n"
    )
    if sys.platform == "win32":
        return base
    return base


# ── Subprocess helpers ──────────────────────────────────────────────────────


async def _run(*args: str, timeout: float = 10.0) -> tuple[int, str, str]:
    """Run ``tailscale ...`` and capture (returncode, stdout, stderr)."""
    bin_ = tailscale_binary()
    if not bin_:
        return 127, "", "tailscale binary not found"
    try:
        proc = await asyncio.create_subprocess_exec(
            bin_, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        rc = proc.returncode if proc.returncode is not None else -1
        return rc, stdout_b.decode("utf-8", errors="replace"), stderr_b.decode("utf-8", errors="replace")
    except asyncio.TimeoutError:
        return 124, "", f"tailscale {' '.join(args)}: timeout after {timeout}s"
    except Exception as exc:  # noqa: BLE001
        return -1, "", f"tailscale {' '.join(args)}: {exc!r}"


# ── Public API ──────────────────────────────────────────────────────────────


async def status(port: int = _GATEWAY_PORT) -> FunnelStatus:
    """Return the current Tailscale + Funnel state for ``port``.

    Reads ``tailscale serve status --json`` and surfaces just the funnel
    binding for our target HTTP port. Safe to call repeatedly; never
    mutates state.
    """
    if tailscale_binary() is None:
        return FunnelStatus(
            installed=False, logged_in=False, funnel_enabled=False,
            public_url=None, forwarded_port=None,
            error="tailscale CLI not found",
        )

    # Check login + machine state via `tailscale status --json`.
    rc, out, err = await _run("status", "--json")
    if rc != 0:
        return FunnelStatus(
            installed=True, logged_in=False, funnel_enabled=False,
            public_url=None, forwarded_port=None,
            error=err.strip() or f"tailscale status exit {rc}",
        )
    try:
        st = json.loads(out)
    except (json.JSONDecodeError, ValueError) as exc:
        return FunnelStatus(
            installed=True, logged_in=False, funnel_enabled=False,
            public_url=None, forwarded_port=None,
            error=f"tailscale status: {exc}",
        )

    backend_state = str(st.get("BackendState") or "").lower()
    logged_in = backend_state == "running"
    if not logged_in:
        return FunnelStatus(
            installed=True, logged_in=False, funnel_enabled=False,
            public_url=None, forwarded_port=None,
            error=f"tailscale not logged in (state={backend_state}). Run: tailscale up",
        )

    # Pull the machine's *.ts.net DNS name (Self.DNSName ends with a dot).
    self_node = st.get("Self") or {}
    dns_name = str(self_node.get("DNSName") or "").rstrip(".")
    if not dns_name:
        return FunnelStatus(
            installed=True, logged_in=True, funnel_enabled=False,
            public_url=None, forwarded_port=None,
            error="tailscale: no DNS name (MagicDNS may be disabled)",
        )

    # Check the funnel serve config for our port.
    rc2, out2, err2 = await _run("serve", "status", "--json")
    if rc2 != 0:
        # No serve config is a normal state (returns non-zero on some versions);
        # treat as funnel disabled.
        return FunnelStatus(
            installed=True, logged_in=True, funnel_enabled=False,
            public_url=None, forwarded_port=None,
            error=None,
        )
    funnel_enabled, forwarded = _parse_serve_status(out2, port)
    public_url = f"https://{dns_name}" if funnel_enabled else None

    return FunnelStatus(
        installed=True, logged_in=True,
        funnel_enabled=funnel_enabled,
        public_url=public_url,
        forwarded_port=forwarded,
    )


def _parse_serve_status(json_text: str, port: int) -> tuple[bool, int | None]:
    """Inspect ``tailscale serve status --json`` for our forwarded port.

    The schema varies slightly across Tailscale versions. We look for any
    funnel binding whose handler proxies ``http://127.0.0.1:<port>``.
    Returns (funnel_enabled, forwarded_port).
    """
    try:
        sv = json.loads(json_text or "{}")
    except Exception:  # noqa: BLE001
        return False, None
    web = (sv or {}).get("Web") or {}
    af = (sv or {}).get("AllowFunnel") or {}

    # Quick check: is funnel enabled on ANY of the host's HTTPS ports?
    funnel_on = any(bool(v) for v in af.values())
    if not funnel_on:
        return False, None

    # Look for our target port in any web handler.
    for _host, host_cfg in web.items():
        handlers = (host_cfg or {}).get("Handlers") or {}
        for _path, h in handlers.items():
            proxy = str((h or {}).get("Proxy") or "")
            if proxy.endswith(f":{port}") or proxy.endswith(f":{port}/"):
                return True, port
    # Funnel is on but doesn't point at our port -- caller decides what to do.
    return True, None


async def enable(port: int = _GATEWAY_PORT) -> FunnelStatus:
    """Enable Tailscale Funnel for ``port`` and return the resulting status.

    Idempotent -- safe to call when already enabled.
    """
    if tailscale_binary() is None:
        return FunnelStatus(
            installed=False, logged_in=False, funnel_enabled=False,
            public_url=None, forwarded_port=None,
            error="tailscale not installed",
        )

    # Modern syntax: `tailscale funnel <port>` enables funnel + serve on
    # the default 443 mapped to that local port. Background mode keeps it
    # alive across daemon restarts.
    rc, _out, err = await _run("funnel", "--bg", str(port), timeout=20.0)
    if rc != 0:
        # Older Tailscale CLIs (pre-1.50) used a different syntax. Try a
        # compatibility path: explicit `serve` + `funnel on`.
        rc2, _, err2 = await _run("serve", "https:443", f"http://127.0.0.1:{port}", timeout=15.0)
        rc3, _, err3 = await _run("funnel", "443", "on", timeout=15.0)
        if rc2 != 0 and rc3 != 0:
            return FunnelStatus(
                installed=True, logged_in=False, funnel_enabled=False,
                public_url=None, forwarded_port=None,
                error=err.strip() or err2.strip() or err3.strip() or "tailscale funnel: failed",
            )

    # Re-read status so caller gets the fresh public_url.
    return await status(port=port)


async def disable(port: int = _GATEWAY_PORT) -> FunnelStatus:
    """Tear down the funnel binding. Best-effort + idempotent."""
    if tailscale_binary() is None:
        return FunnelStatus(
            installed=False, logged_in=False, funnel_enabled=False,
            public_url=None, forwarded_port=None,
            error="tailscale not installed",
        )
    # Modern syntax
    await _run("funnel", "--bg", str(port), "off", timeout=10.0)
    # Compat path
    await _run("funnel", "443", "off", timeout=10.0)
    await _run("serve", "reset", timeout=10.0)
    return await status(port=port)


# ── Config persistence ─────────────────────────────────────────────────────


def persist_public_url(url: str) -> None:
    """Write ``cloud.public_url`` to ``~/.navig/config.yaml`` so CloudManager
    picks up direct mode on next start (or via /api/deck/cloud/restart)."""
    from navig.core import Config
    cfg = Config()
    cfg.set("cloud.public_url", url, scope="global")
    cfg.set("cloud.enabled", True, scope="global")
    cfg.save(scope="global")


def clear_public_url() -> None:
    """Clear ``cloud.public_url`` -- CloudManager falls back to tunnel mode."""
    from navig.core import Config
    cfg = Config()
    cfg.set("cloud.public_url", "", scope="global")
    cfg.save(scope="global")
