"""Shared helpers for Deck API route modules."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def confine_under(base: Path, rel: str | None) -> Path | None:
    """Resolve *rel* under *base*, or ``None`` when it escapes (path traversal).

    The ONE containment guard for the deck routes — every handler that turns CLIENT-SUPPLIED
    input into a filesystem path routes through here (the deck API is reachable remotely via
    Lighthouse). Rejects empty / absolute / drive-qualified input, then confirms containment
    AFTER ``.resolve()`` so ``..`` segments and symlinks that escape *base* are caught. Callers
    keep their own thin wrapper (``_confined_doc_path`` / ``_confined_page_path`` /
    ``_confined_space_dir``) so a weaker copy can't drift back in.
    """
    r = (rel or "").strip().replace("\\", "/")
    if not r:
        return None
    p = Path(r)
    if p.is_absolute() or p.drive:
        return None
    try:
        target = (base / p).resolve()
        root = base.resolve()
    except OSError:
        return None
    return target if target.is_relative_to(root) else None


async def run_on_host(host: str, command: str, timeout: float = 30.0) -> tuple[bool, str, str]:
    """Run a shell command on a CONFIGURED `host` via the project SSH executor, off the event loop.

    The ONE shared SSH runner for the deck routes — both ``remote._ssh`` and ``database._run_remote``
    delegate here (like ``confine_under`` is the one path guard). Consolidated after #426: those two
    were byte-for-byte duplicates, and that duplication is exactly what let the DB console ship broken
    (its copy called ``ServerDiscovery(cfg, host_name=host)`` — a ConfigManager plus a kwarg the
    constructor rejects — so every db list/tables/query raised) while ``remote._ssh`` worked. One
    runner means a fix lands in both.

    Contract, in order:
      * gate on ``cfg.host_exists(host)`` — NEVER SSH an unconfigured / arbitrary host (the deck API is
        reachable remotely via Lighthouse);
      * ``load_host_config(host)`` → the SSH config DICT (host/user/port/key…) that ``ServerDiscovery``
        actually takes (it has no ``host_name`` param);
      * run the blocking ``_execute_ssh`` in a thread via ``run_in_executor`` under ``wait_for`` so a
        slow/hung SSH can't stall the whole gateway.

    Returns ``(ok, stdout, stderr)`` and never raises — every failure path (backend missing, host
    unconfigured, bad config, timeout, unexpected result shape) comes back as ``(False, "", reason)``.
    """
    try:
        from navig.config import get_config_manager  # type: ignore[import]
        from navig.discovery import ServerDiscovery  # type: ignore[import]
    except Exception as exc:  # noqa: BLE001
        return False, "", f"navig backend unavailable: {exc}"
    cfg = get_config_manager()
    # ServerDiscovery takes the host's SSH config *dict* (host/user/port/key…),
    # not the ConfigManager. Load the named host's config first.
    try:
        if not cfg.host_exists(host):
            return False, "", f"host '{host}' not configured"
        ssh_config = cfg.load_host_config(host)
    except Exception as exc:  # noqa: BLE001
        return False, "", f"could not load host '{host}': {exc}"
    try:
        disco = ServerDiscovery(ssh_config)
    except Exception as exc:  # noqa: BLE001
        return False, "", f"host '{host}' invalid config: {exc}"
    loop = asyncio.get_event_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: disco._execute_ssh(command)),
            timeout=timeout,
        )
        if isinstance(result, tuple) and len(result) == 3:
            return result[0], result[1] or "", result[2] or ""
        return False, "", "unexpected ssh result shape"
    except asyncio.TimeoutError:
        return False, "", f"timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001
        return False, "", str(exc)


def _get_vault():
    """Return the vault instance, or None if unavailable (best-effort)."""
    try:
        from navig.vault import get_vault

        return get_vault()
    except Exception as e:  # noqa: BLE001
        logger.debug("Vault not available: %s", e)
        return None
