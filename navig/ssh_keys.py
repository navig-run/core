"""Local SSH key discovery helpers.

Used for onboarding/wizards and to speed up repeated runs via ~/.navig/cache/ssh_keys.json.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from navig.cache_store import read_json_cache, write_json_cache

_DEFAULT_KEY_NAMES = [
    "id_ed25519",
    "id_rsa",
    "id_ecdsa",
    "id_dsa",
]


def _looks_like_private_key(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.name.endswith(".pub"):
        return False
    # Skip known non-private-key files.
    if path.name in {"known_hosts", "config", "authorized_keys"}:
        return False
    return True


def discover_local_ssh_keys(*, no_cache: bool = False, ttl_seconds: int = 300) -> Dict[str, Any]:
    """Discover local SSH private keys.

    Returns:
        {"keys": [{"path": "...", "name": "..."}], "count": N}
    """

    cache = read_json_cache("ssh_keys.json", ttl_seconds=ttl_seconds, no_cache=no_cache)
    if cache.hit and not cache.expired and isinstance(cache.data, dict):
        return cache.data

    ssh_dir = Path.home() / ".ssh"
    keys: List[Dict[str, str]] = []

    # Prefer well-known key names first.
    for name in _DEFAULT_KEY_NAMES:
        candidate = ssh_dir / name
        if _looks_like_private_key(candidate):
            keys.append({"name": name, "path": str(candidate)})

    # Then scan any remaining files in ~/.ssh.
    if ssh_dir.exists() and ssh_dir.is_dir():
        for p in sorted(ssh_dir.iterdir()):
            if not _looks_like_private_key(p):
                continue
            if any(k["path"] == str(p) for k in keys):
                continue
            keys.append({"name": p.name, "path": str(p)})

    payload = {"keys": keys, "count": len(keys)}
    try:
        write_json_cache("ssh_keys.json", payload)
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    return payload
