"""
Matrix Stats Webhook — push local store aggregates to Cloudflare D1.

The webhook fires on demand (CLI) or periodically (gateway cron).
Payload is HMAC-SHA256 signed so the CF Worker can verify authenticity.

Usage::

    from navig.comms.matrix_webhook import push_matrix_stats
    await push_matrix_stats()               # uses global config
    await push_matrix_stats(endpoint, key)   # explicit
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


def _sign(payload: str, secret: str) -> str:
    """HMAC-SHA256 hex digest."""
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


async def push_matrix_stats(
    endpoint: str | None = None,
    secret: str | None = None,
    stats: dict[str, Any] | None = None,
) -> bool:
    """
    Push Matrix store stats to the Cloudflare D1 stats endpoint.

    Parameters
    ----------
    endpoint : str, optional
        Full URL of the CF Worker endpoint (e.g.
        ``https://navig.run/api/admin/matrix-stats``).
        Falls back to ``comms.matrix.stats_endpoint`` in config.
    secret : str, optional
        HMAC signing key. Falls back to ``comms.matrix.stats_secret``.
    stats : dict, optional
        Pre-computed stats dict. If omitted, reads from local MatrixStore.

    Returns True on success, False otherwise.
    """
    # Resolve endpoint / secret from config if not provided
    if not endpoint or not secret:
        try:
            from navig.core.config import get_config

            cfg = get_config()
            matrix_cfg = cfg.get("comms", {}).get("matrix", {})
            endpoint = endpoint or matrix_cfg.get("stats_endpoint", "")
            secret = secret or matrix_cfg.get("stats_secret", "")
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

    if not endpoint or not secret:
        logger.debug("Matrix stats webhook: no endpoint/secret configured, skip")
        return False

    # Gather stats from local store
    if stats is None:
        try:
            import os

            from navig.comms.matrix_store import MatrixStore

            db_path = os.path.expanduser("~/.navig/matrix.db")
            if not os.path.exists(db_path):
                logger.debug("Matrix stats webhook: no store DB, skip")
                return False
            store = MatrixStore(db_path)
            try:
                stats = store.stats()
            finally:
                store.close()
        except Exception:
            logger.exception("Matrix stats webhook: failed to read store")
            return False

    payload = json.dumps(
        {
            "rooms": stats.get("rooms", 0),
            "events": stats.get("events", 0),
            "ts": int(time.time()),
        }
    )

    sig = _sign(payload, secret)

    # Send with httpx (async) or urllib (sync fallback)
    try:
        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                endpoint,
                content=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Signature": sig,
                },
            )
            ok = resp.status_code == 200
            if ok:
                logger.info(
                    "Matrix stats pushed to %s (rooms=%s, events=%s)",
                    endpoint,
                    stats.get("rooms"),
                    stats.get("events"),
                )
            else:
                logger.warning(
                    "Matrix stats push failed: %s %s", resp.status_code, resp.text[:200]
                )
            return ok
    except ImportError:
        # Fallback to urllib (sync, wrapped in thread)
        import asyncio

        return await asyncio.to_thread(_push_sync, endpoint, payload, sig)
    except Exception:
        logger.exception("Matrix stats push error")
        return False


def _push_sync(endpoint: str, payload: str, sig: str) -> bool:
    """Sync fallback using urllib."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        endpoint,
        data=payload.encode(),
        headers={
            "Content-Type": "application/json",
            "X-Signature": sig,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except urllib.error.URLError as exc:
        logger.warning("Matrix stats push (sync) failed: %s", exc)
        return False
