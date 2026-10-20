"""Where the NAVIG OS server is listening, as it announces itself.

The desktop app starts its server on an EPHEMERAL loopback port with a per-launch token,
and `navig os serve` on a fixed port with a vault-stored one. Neither is in navig's
config, so the server writes a descriptor next to the gateway's own discovery file:

    <config_dir>/run/os-server.json   {"ws_url", "token", "pid", "startedAt", "kind", "protocol"}

(mode 0600, replaced atomically, removed on clean exit). The brain reads it each time a
remote device connects, so a restarted desktop — new port, new token — is picked up with
no registration round-trip and no dependence on start order.

The token in this file is a local secret with the same standing as ``config.yaml``: any
process that can read it already runs as the user.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

DESCRIPTOR_NAME = "os-server.json"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def descriptor_path() -> Path:
    from navig.platform.paths import config_dir

    return config_dir() / "run" / DESCRIPTOR_NAME


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil

        return bool(psutil.pid_exists(pid))
    except Exception:  # noqa: BLE001 — psutil missing: trust the file rather than refuse
        return True


def _is_loopback_ws(url: str) -> bool:
    """Only ever dial the PC itself. A descriptor pointing anywhere else is refused."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("ws", "wss") and (parts.hostname or "") in _LOOPBACK_HOSTS and bool(parts.port)


_READ_ATTEMPTS = 4
_READ_RETRY_S = 0.05


def read_descriptor(path: Path | None = None) -> dict[str, Any] | None:
    """The running NAVIG OS server, or None when none is running (or the file is bad).

    A descriptor whose process is gone is stale (the server crashed without cleaning
    up); it is ignored rather than dialled, so the caller reports "NAVIG OS is not
    running" instead of timing out against a dead port.
    """
    target = path or descriptor_path()
    # A read that races the writer (a Windows sharing violation while the desktop
    # replaces the file, or a half-written JSON body) is NOT "not running". Treating
    # it as such made the status flip true → false → true within seconds, so the
    # deck and the bot's /os said "NAVIG OS is not running" over a running app.
    # Retry briefly; a missing file is the honest "not running" and is not retried.
    data: Any = None
    for attempt in range(_READ_ATTEMPTS):
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            break
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            if attempt == _READ_ATTEMPTS - 1:
                logger.debug("os-server descriptor unreadable: %r", exc)
                return None
            time.sleep(_READ_RETRY_S * (attempt + 1))
    if not isinstance(data, dict):
        return None
    ws_url = str(data.get("ws_url") or "")
    token = str(data.get("token") or "")
    if not ws_url or len(token) < 16 or not _is_loopback_ws(ws_url):
        return None
    try:
        pid = int(data.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    if pid and not _pid_alive(pid):
        return None
    return {
        "ws_url": ws_url,
        "token": token,
        "pid": pid,
        "kind": str(data.get("kind") or ""),
        "protocol": str(data.get("protocol") or ""),
        "started_at": data.get("startedAt"),
    }


def write_descriptor(
    *, ws_url: str, token: str, pid: int, kind: str, protocol: str = "", started_at: float | None = None,
    path: Path | None = None,
) -> Path:
    """Atomically write the descriptor (used by `navig os serve`; the desktop writes its own)."""
    import time

    target = path or descriptor_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ws_url": ws_url,
        "token": token,
        "pid": pid,
        "kind": kind,
        "protocol": protocol,
        "startedAt": started_at if started_at is not None else time.time(),
    }
    fd, tmp = tempfile.mkstemp(prefix=".os-server-", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass  # Windows: the user profile's ACL already scopes it
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target


def remove_descriptor(*, pid: int | None = None, path: Path | None = None) -> None:
    """Remove the descriptor — only if it still names *pid*, so a newer server's survives."""
    target = path or descriptor_path()
    if pid is not None:
        current = None
        try:
            current = json.loads(target.read_text(encoding="utf-8")).get("pid")
        except (OSError, ValueError, AttributeError):
            return
        if current != pid:
            return
    try:
        target.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.debug("os-server descriptor not removed: %r", exc)
