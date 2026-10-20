"""NAVIG Dock pairing — which browser extension may act for the operator.

The daemon trusts genuinely-local requests (``gateway/deck/auth.py``). Before pairing
existed, *any* installed extension's ``chrome-extension://`` origin got that trust. Now:

* The first Dock that asks (``POST /api/deck/dock/pair``) is paired — trust on first use
  — and receives a random token. Only ``sha256(token)`` is stored here.
* From then on, an extension origin is trusted only when its id is paired, and the
  browser-control socket also needs the token (an Origin header alone is not proof).
* A second extension can never replace the pairing by asking; the operator runs
  ``navig dock unpair`` first.

The file is tiny and read on hot paths (every deck request from an extension), so reads
are cached on the file's (mtime, size). A file that exists but cannot be read FAILS
CLOSED: extension origins are refused rather than silently trusting all of them.

This is a leaf module: no ``navig.gateway`` imports, so the CLI stays fast.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PAIRING_FILE = "dock_pairing.json"
PAIR_PATH = "/api/deck/dock/pair"
_ORIGIN_RE = re.compile(r"^(chrome-extension|moz-extension)://([A-Za-z0-9-]{8,64})/?$")
_VERSION = 1


class PairingConflict(RuntimeError):
    """Another extension is already paired; the operator must unpair it first."""


class PairingUnreadable(RuntimeError):
    """The pairing file exists but could not be read — never overwrite it blindly."""


@dataclass(frozen=True)
class Pairing:
    extension_id: str
    token_sha256: str
    name: str = ""
    paired_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"extension_id": self.extension_id, "token_sha256": self.token_sha256,
                "name": self.name, "paired_at": self.paired_at}


def pairing_path() -> Path:
    from navig.platform.paths import config_dir  # noqa: PLC0415

    return config_dir() / PAIRING_FILE


def extension_id_from_origin(origin: str | None) -> str | None:
    """``chrome-extension://<id>`` → ``<id>``; anything else → ``None``."""
    m = _ORIGIN_RE.match((origin or "").strip())
    return m.group(2) if m else None


def is_extension_origin(origin: str | None) -> bool:
    o = (origin or "").strip()
    return o.startswith("chrome-extension://") or o.startswith("moz-extension://")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ── cached read ──────────────────────────────────────────────────────────────

_lock = threading.Lock()
_cache: tuple[str, int, int, tuple[Pairing, ...] | None] | None = None  # (path, mtime_ns, size, value)


def _parse(data: Any) -> tuple[Pairing, ...]:
    if not isinstance(data, dict):
        raise ValueError("not an object")
    out = []
    for row in data.get("pairings") or []:
        if not isinstance(row, dict):
            continue
        ext, digest = str(row.get("extension_id") or ""), str(row.get("token_sha256") or "")
        if ext and len(digest) == 64:
            out.append(Pairing(ext, digest, str(row.get("name") or ""), str(row.get("paired_at") or "")))
    return tuple(out)


def load() -> tuple[Pairing, ...] | None:
    """Current pairings; ``()`` when none; ``None`` when the file exists but is unreadable."""
    global _cache
    path = pairing_path()
    try:
        st = path.stat()
    except FileNotFoundError:
        return ()
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    with _lock:
        if _cache is not None and _cache[:3] == key:
            return _cache[3]
    value: tuple[Pairing, ...] | None
    try:
        from navig.core.yaml_io import read_text_retrying  # noqa: PLC0415

        text = read_text_retrying(path)
        import json  # noqa: PLC0415

        value = _parse(json.loads(text)) if text.strip() else ()
    except (OSError, ValueError) as exc:
        logger.warning("dock pairing file unreadable (%s) — extension origins refused", type(exc).__name__)
        value = None
    with _lock:
        _cache = (*key, value)
    return value


def _invalidate() -> None:
    global _cache
    with _lock:
        _cache = None


# ── policy ───────────────────────────────────────────────────────────────────


def extension_origin_trusted(origin: str | None, path: str = "") -> bool:
    """May a local request with this extension *origin* use the desktop bypass?

    No pairing yet → trusted (the legacy behaviour, so the Dock keeps working until it
    pairs itself). Paired → only the paired id. The pair route itself is always reachable
    so a Dock can pair (and is told clearly when another extension holds the pairing).
    """
    ext = extension_id_from_origin(origin)
    if ext is None:
        return False
    if path == PAIR_PATH:
        return True
    pairs = load()
    if pairs is None:
        return False
    if not pairs:
        return True
    return any(hmac.compare_digest(p.extension_id, ext) for p in pairs)


def verify(extension_id: str, token: str) -> bool:
    """Constant-time check of a Dock's token against the stored hash for its id."""
    if not extension_id or not token:
        return False
    pairs = load() or ()
    digest = _hash(token)
    ok = False
    for p in pairs:
        same_ext = hmac.compare_digest(p.extension_id, extension_id)
        same_tok = hmac.compare_digest(p.token_sha256, digest)
        ok = ok or (same_ext and same_tok)
    return ok


def pair(extension_id: str, name: str = "") -> str:
    """Pair *extension_id* and return its new token (shown once, never stored in clear).

    Re-pairing the SAME extension rotates its token (a reinstalled Dock lost its copy).
    Raises :class:`PairingConflict` when a different extension is paired.
    """
    from navig.core.json_io import (  # noqa: PLC0415
        JsonReadError,
        atomic_write_json,
        load_json_for_update,
    )

    if not extension_id or not re.fullmatch(r"[A-Za-z0-9-]{8,64}", extension_id):
        raise ValueError("not an extension id")
    path = pairing_path()
    try:
        data = load_json_for_update(path, default={})
    except JsonReadError as exc:
        raise PairingUnreadable(str(exc)) from exc
    current = _parse(data) if data else ()
    others = [p for p in current if p.extension_id != extension_id]
    if others:
        raise PairingConflict(others[0].name or others[0].extension_id)
    token = secrets.token_urlsafe(32)
    row = Pairing(extension_id, _hash(token), name[:80],
                  datetime.now(timezone.utc).replace(microsecond=0).isoformat())
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json({"version": _VERSION, "pairings": [row.to_dict()]}, path)
    _invalidate()
    logger.info("dock paired: extension %s…", extension_id[:6])
    return token


def unpair(extension_id: str | None = None) -> int:
    """Remove one pairing (or all). Returns how many were removed."""
    from navig.core.json_io import (  # noqa: PLC0415
        JsonReadError,
        atomic_write_json,
        load_json_for_update,
    )

    path = pairing_path()
    if not path.exists():
        return 0
    try:
        data = load_json_for_update(path, default={})
        current = _parse(data) if data else ()
    except (JsonReadError, ValueError):
        if extension_id is not None:
            raise
        current = ()  # unreadable + "remove all" → resetting it is exactly what was asked
    keep = [p for p in current if extension_id is not None and p.extension_id != extension_id]
    removed = len(current) - len(keep)
    atomic_write_json({"version": _VERSION, "pairings": [p.to_dict() for p in keep]}, path)
    _invalidate()
    return removed
