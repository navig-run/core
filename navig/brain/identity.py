"""Machine identity for Brain ownership.

``owner_id`` answers "which machine is claiming this Brain?". It is deliberately
**derived from the machine**, not from copied state:

* If it lived in ``<config_dir>/brain/owner.json``, then copying that config dir to a
  second machine during a migration would copy the id too — and both machines would
  present the same owner, so the lease could never tell them apart. That is exactly
  the incident this module exists to prevent (two machines ran daemon + telegram
  worker + gateway; the Windows gateway re-set the Telegram webhook on every start).
* So the fingerprint comes from the OS (Windows ``MachineGuid``, Linux
  ``/etc/machine-id``, macOS IOPlatformUUID), and a minted UUID is only the fallback
  for hosts that expose none. A copy of ``~/.navig`` therefore arrives on the new
  machine with a *different* ``owner_id``, and the first thing it does is refuse to
  start as a second Brain.

Everything here is read-only and cheap; the result is cached for the process.
"""

from __future__ import annotations

import hashlib
import os
import platform
import sys
import uuid
from functools import lru_cache
from pathlib import Path

__all__ = [
    "hostname",
    "installation_fingerprint",
    "machine_id",
    "owner_id",
    "owner_label",
]

_MACHINE_ID_FILE = "machine_id"


def hostname() -> str:
    """This machine's network name — display/audit only, never an identity."""
    try:
        return platform.node() or ""
    except Exception:  # noqa: BLE001 — a name is cosmetic; never fail a boot on it
        return ""


def _windows_machine_guid() -> str | None:
    try:
        import winreg  # noqa: PLC0415 — Windows-only, imported on demand

        flags = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0, flags
        ) as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
        return str(value).strip() or None
    except Exception:  # noqa: BLE001 — non-Windows, or a locked-down registry
        return None


def _linux_machine_id() -> str | None:
    for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            text = Path(candidate).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return None


def _darwin_platform_uuid() -> str | None:
    # `ioreg` is the only stable, non-privileged source on macOS. One call, cached.
    try:
        import subprocess  # noqa: PLC0415

        out = subprocess.run(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout
    except Exception:  # noqa: BLE001
        return None
    for line in out.splitlines():
        if "IOPlatformUUID" in line:
            _, _, value = line.partition("=")
            cleaned = value.strip().strip('"')
            if cleaned:
                return cleaned
    return None


def _hardware_node() -> str | None:
    """The MAC-derived node id, only when the OS actually reported one.

    ``uuid.getnode()`` sets bit 40 when it had to invent a random number (no usable
    NIC, or every MAC randomized). A random node would change on every process start —
    a new "machine" each boot — so it is rejected rather than used.
    """
    try:
        node = uuid.getnode()
    except Exception:  # noqa: BLE001
        return None
    if (node >> 40) & 1:
        return None
    return f"{node:012x}"


def _minted_machine_id() -> str:
    """Fallback identity for hosts that expose no fingerprint at all.

    Stored under the config dir, so it survives restarts. A copied config dir copies
    this too — the one case where two machines could share an id — which is why it is
    the LAST resort and why ``navig brain verify`` reports which source was used.
    """
    from navig.platform import paths  # noqa: PLC0415 — light, but keep import local

    path = paths.config_dir() / "brain" / _MACHINE_ID_FILE
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    minted = uuid.uuid4().hex
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(minted, encoding="utf-8")
    except OSError:
        pass  # read-only home: a process-scoped id still beats no id
    return minted


@lru_cache(maxsize=1)
def _fingerprint() -> tuple[str, str]:
    """``(value, source)`` for this machine. Cached for the process lifetime."""
    if sys.platform == "win32":
        guid = _windows_machine_guid()
        if guid:
            return guid, "windows:MachineGuid"
    elif sys.platform == "darwin":
        platform_uuid = _darwin_platform_uuid()
        if platform_uuid:
            return platform_uuid, "darwin:IOPlatformUUID"
    else:
        machine = _linux_machine_id()
        if machine:
            return machine, "linux:machine-id"
    node = _hardware_node()
    if node:
        return node, "hw:node"
    return _minted_machine_id(), "minted"


def machine_id() -> str:
    """This machine's raw fingerprint."""
    return _fingerprint()[0]


def machine_id_source() -> str:
    """Where :func:`machine_id` came from — shown by ``navig brain verify``."""
    return _fingerprint()[1]


def _digest(value: str) -> str:
    return hashlib.sha256(f"navig-brain-owner:{value}".encode()).hexdigest()


def owner_id() -> str:
    """Stable, machine-bound owner id, e.g. ``brn-3f9a1c…``.

    Never derived from (and never equal to) a secret: it is printed in status output,
    receipts and takeover audits.
    """
    return f"brn-{_digest(machine_id())[:16]}"


def owner_label(oid: str | None = None) -> str:
    """``hostname (brn-…)`` — the one human-readable owner string."""
    host = hostname() or "unknown-host"
    return f"{host} ({oid or owner_id()})"


def installation_fingerprint(value: str | None = None) -> str:
    """A short fingerprint of a secret we must never print (e.g. ``deck.api_key``).

    Migration receipts record this instead of the key: it proves the identity moved
    without becoming a second copy of the credential.
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def host_is_always_on() -> bool:
    """Best-effort: is this a host expected to stay powered?

    There is no portable "will this machine be switched off?" API, so this is an
    honest heuristic used only for a WARNING — never for enforcement:

    * a system service config dir (``/etc/navig``, ``/var/lib/navig``) is always-on;
    * a WINDOWS desktop normally is not;
    * a POSIX host with a graphical session is a workstation, one without is a server.

    The last rule is what the old expression meant to say and did not:
    ``return sys.platform != "darwin" or True`` is unconditionally True, so every Linux
    Brain was promised always-on and the warning never fired (measured 2026-10-30).
    """
    from navig.platform import paths  # noqa: PLC0415

    try:
        cfg = str(paths.config_dir())
    except Exception:  # noqa: BLE001
        cfg = ""
    if cfg.startswith("/etc/navig") or cfg.startswith("/var/lib/navig"):
        return True
    if sys.platform == "win32":
        return False
    return not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
