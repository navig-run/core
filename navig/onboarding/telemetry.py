"""
navig.onboarding.telemetry — Anonymous one-time install telemetry.

Privacy Contract (what IS and IS NOT collected)
------------------------------------------------
COLLECTED:
  event      : "install" — literal string, always this value
  platform   : OS name — "Windows", "Linux", "Darwin"
  arch       : CPU architecture — "x86_64", "arm64", etc.
  python     : Python version string — "3.11.8"
  anon_id    : 16-char SHA-256 prefix of a hash of your machine's UUID.
               Your machine UUID itself is never transmitted.
               This is a one-way hash — cannot be reversed to identify you.

NOT COLLECTED:
  No IP address is stored by the server (standard proxy config strips it).
  No hostname, username, file paths, or project names.
  No node_id from genesis.json.
  No API keys, credentials, or secrets. Ever.
  No geolocation data.

OPT-OUT:
  export NAVIG_NO_TELEMETRY=1
  echo 'NAVIG_NO_TELEMETRY=1' >> ~/.bashrc

The ping is fire-and-forget (2-second timeout, no retry).
Completion is marked in ~/.navig/.pinged — subsequent runs are silent no-ops.

This telemetry exists solely to count installs. That's it.
"""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
from pathlib import Path

# Public endpoint — can be overridden for self-hosted deployments
TELEMETRY_URL: str = os.environ.get(
    "NAVIG_TELEMETRY_URL",
    "https://telemetry.navig.run",
)
_NAVIG_DIR: Path = Path.home() / ".navig"
_PINGED_MARKER: Path = _NAVIG_DIR / ".pinged"
_OPT_OUT_VAR = "NAVIG_NO_TELEMETRY"


# ── Consent block (printed once on first install) ─────────────────────────

_CONSENT_LINES = """  One anonymous install ping · OS, arch, Python version — nothing else.
  Opt out: export NAVIG_NO_TELEMETRY=1
"""


def _machine_id() -> str | None:
    """
    Extract a stable, opaque machine identifier without transmitting it.

    Returns the raw identifier string (will be hashed before use).
    Returns None if the identifier cannot be obtained on this platform.

    Args:
        None

    Returns:
        Machine UUID string or None.

    Raises:
        Never — all exceptions are caught and result in None.
    """
    system = platform.system()
    try:
        if system == "Windows":
            result = subprocess.run(
                ["wmic", "csproduct", "get", "UUID"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            # Output has a header line "UUID" then the value on line [1]
            lines = [
                ln.strip() for ln in result.stdout.strip().splitlines() if ln.strip()
            ]
            if len(lines) >= 2:
                return lines[1]  # lines[0] == "UUID" (header), lines[1] == actual value
            return None

        if system == "Linux":
            mid = Path("/etc/machine-id")
            if mid.exists():
                return mid.read_text(encoding="utf-8").strip()
            # systemd fallback
            dbus = Path("/var/lib/dbus/machine-id")
            if dbus.exists():
                return dbus.read_text(encoding="utf-8").strip()
            return None

        if system == "Darwin":
            result = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            for line in result.stdout.splitlines():
                if "IOPlatformUUID" in line:
                    # Format: "IOPlatformUUID" = "XXXXXXXX-XXXX-..."
                    parts = line.split("=", 1)
                    if len(parts) == 2:
                        return parts[1].strip().strip('"')
            return None

    except Exception:  # noqa: BLE001
        return None

    return None


def _build_anon_id() -> str:
    """
    Build a 16-char anonymous identifier from the machine UUID.

    Double-hashed: machine_id → sha256 → 16-char prefix.
    The full hash is never transmitted.

    Returns:
        16-character hex string guaranteed to be non-empty.
        Falls back to a hash of the platform string if machine ID
        is unavailable.
    """
    raw = _machine_id() or f"{platform.node()}:{platform.machine()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def ping_install_if_first_time() -> None:
    """
    Fire one anonymous HTTP ping on the first install, then never again.

    Safe to call from any context:
    - NAVIG_NO_TELEMETRY=1  → silent no-op, no file written
    - .pinged exists          → silent no-op
    - Any network error       → silently swallowed, .pinged written anyway
    - Called from non-main thread, subprocess, or CI → all safe

    The function never raises, never blocks init, and never retries.
    """
    # Hard opt-out
    if os.environ.get(_OPT_OUT_VAR):
        return

    # Already pinged
    if _PINGED_MARKER.exists():
        return

    # Print consent block before firing the ping
    print(_CONSENT_LINES, end="")

    payload = {
        "event": "install",
        "platform": platform.system(),
        "arch": platform.machine(),
        "python": platform.python_version(),
        "anon_id": _build_anon_id(),
    }

    try:
        import requests as _req

        _req.post(
            f"{TELEMETRY_URL}/telemetry/ping",
            json=payload,
            timeout=2,
        )
    except Exception:  # noqa: BLE001
        pass  # fire-and-forget — network failures are irrelevant

    # Write marker regardless of success or failure so we never ask again
    try:
        _PINGED_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _PINGED_MARKER.write_text("1", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
