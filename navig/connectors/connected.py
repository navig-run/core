"""navig.connectors.connected — "which connectors are really connected", and search them all.

Lifted from ``commands/connector_cmd.py`` so the CLI and the agent's unified search
(``search_all``) share one answer:

* the **vault** is the durable source of truth for "connected" — ``registry.list_connected()``
  only sees instances this process already created, so a fresh process reports nothing;
* a fresh instance holds no access token until :func:`hydrate` loads it from the vault.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


def vault_connected_ids() -> dict[str, str]:
    """``{connector_id: account_email}`` for everything linked, from the vault."""
    try:
        from navig.connectors.auth_manager import ConnectorAuthManager

        return ConnectorAuthManager().list_connected_accounts()
    except Exception as exc:  # noqa: BLE001 — a vault problem must not kill a listing
        logger.warning("Could not read connected connectors from the vault: %s", exc)
        return {}


def resolve_connected(registry: Any = None) -> list:
    """Connector instances that are actually usable, vault first.

    Union of the vault-backed ids and any instance this process already marked
    connected, so non-OAuth connectors (which keep no vault credential) are kept.
    """
    if registry is None:
        from navig.connectors.registry import get_connector_registry

        registry = get_connector_registry()
    out: dict[str, object] = {}
    for cid in vault_connected_ids():
        if registry.has(cid):
            out[cid] = registry.get(cid)
    for inst in registry.list_connected():
        out.setdefault(inst.manifest.id, inst)
    return list(out.values())


async def hydrate(connector: Any) -> bool:
    """Load *connector*'s stored vault token into the instance; True on success."""
    from navig.connectors.auth_manager import ConnectorAuthManager
    from navig.connectors.types import ConnectorStatus

    ok = await ConnectorAuthManager().inject_token(connector)
    if ok:
        connector.status = ConnectorStatus.CONNECTED
    return ok


async def search_connected(
    query: str, *, limit: int = 5, timeout: float = 5.0
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Search every connected, searchable connector at once.

    Returns ``(results, status)``: results are ``Resource.to_dict()`` rows; ``status``
    maps each connector id to ``ok`` · ``timeout`` · ``not linked`` · ``error: …`` so
    a caller can report a source that failed instead of silently dropping it.
    """
    try:
        from navig.connectors.bootstrap import ensure_connectors_loaded

        ensure_connectors_loaded()
    except Exception as exc:  # noqa: BLE001
        logger.debug("connector bootstrap skipped: %s", exc)
    connectors = [c for c in resolve_connected() if getattr(c.manifest, "can_search", True)]

    async def _one(c: Any) -> tuple[str, list[dict[str, Any]], str]:
        cid = c.manifest.id
        try:
            if not await hydrate(c) and getattr(c.manifest, "requires_oauth", True):
                return cid, [], "not linked"
            found = await asyncio.wait_for(c.search(query, limit=limit), timeout=timeout)
            return cid, [r.to_dict() for r in found], "ok"
        except asyncio.TimeoutError:
            return cid, [], "timeout"
        except Exception as exc:  # noqa: BLE001 — one connector never fails the rest
            return cid, [], f"error: {type(exc).__name__}"

    results: list[dict[str, Any]] = []
    status: dict[str, str] = {}
    for cid, rows, state in await asyncio.gather(*(_one(c) for c in connectors)):
        results.extend(rows)
        status[cid] = state
    return results, status
