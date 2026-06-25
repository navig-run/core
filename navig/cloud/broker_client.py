"""Async HTTP client for the NAVIG cloud broker (api.navig.run) / hosted relay (relay.navig.run).

Endpoints (see ``navig-deck/functions/api/cloud/*.ts``):

- ``POST /api/cloud/register``      -- upsert (api_key_hash -> tunnel_url)
- ``POST /api/cloud/heartbeat``     -- refresh tunnel_url + last_seen_at
- ``POST /api/cloud/bind-telegram`` -- HMAC-validated, writes binding
- ``GET  /api/cloud/resolve``       -- ?api_key= or ?telegram_id=
- ``POST /api/cloud/unregister``    -- delete user row + bindings
- ``GET  /api/cloud/health``        -- broker liveness (no D1 hit)

The broker only stores routing metadata. All real auth (initData HMAC +
bearer-key check) happens on the daemon -- the broker is a routing
convenience, not an auth gateway.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 15.0


class BrokerError(RuntimeError):
    """Raised on non-2xx broker responses."""

    def __init__(self, status: int, message: str):
        super().__init__(f"broker {status}: {message}")
        self.status = status


class BrokerClient:
    """Async HTTP client. Holds its own ``aiohttp.ClientSession``."""

    def __init__(self, broker_url: str, api_key: str, timeout_s: float = _DEFAULT_TIMEOUT_S):
        self.broker_url = broker_url.rstrip("/")
        self.api_key = api_key
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def _post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        sess = await self._get_session()
        url = f"{self.broker_url}{path}"
        async with sess.post(url, json=body or {}, headers=self._headers()) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise BrokerError(resp.status, text[:200])
            try:
                return await resp.json(content_type=None) if text else {}
            except Exception:
                return {}

    async def _get(self, path: str, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
        sess = await self._get_session()
        url = f"{self.broker_url}{path}"
        async with sess.get(url, headers=headers or {}) as resp:
            text = await resp.text()
            if resp.status == 404:
                raise BrokerError(404, "not_found")
            if resp.status >= 400:
                raise BrokerError(resp.status, text[:200])
            try:
                return await resp.json(content_type=None) if text else {}
            except Exception:
                return {}

    # ── Endpoints ────────────────────────────────────────────────────────────

    async def register(self, tunnel_url: str, label: str | None = None) -> str:
        """Upsert this daemon's row. Returns the broker's ``user_id``."""
        data = await self._post("/api/cloud/register", {"tunnel_url": tunnel_url, "label": label})
        return str(data.get("user_id", ""))

    async def heartbeat(self, tunnel_url: str) -> None:
        """Refresh ``tunnel_url`` + ``last_seen_at``. 404 -> caller should re-register."""
        await self._post("/api/cloud/heartbeat", {"tunnel_url": tunnel_url})

    async def bind_telegram(self, telegram_user_id: int) -> int:
        """Bind a Telegram user_id to this daemon. Returns the bound user_id.

        The DAEMON is the source of truth for "which tg_id belongs to this
        api_key": it sees the incoming Telegram message via long-poll/webhook
        and extracts the user_id directly. The broker stores the binding and
        trusts the api_key Bearer auth -- no bot token leaves the daemon.
        """
        data = await self._post(
            "/api/cloud/bind-telegram",
            {"telegram_user_id": int(telegram_user_id)},
        )
        return int(data.get("telegram_user_id", telegram_user_id))

    async def unregister(self) -> None:
        """Best-effort delete -- callers catch BrokerError and continue."""
        try:
            await self._post("/api/cloud/unregister")
        except BrokerError as e:
            if e.status != 404:
                raise

    async def resolve_by_key(self, api_key: str) -> str | None:
        """Pure routing lookup -- returns None on 404 instead of raising."""
        sess = await self._get_session()
        url = f"{self.broker_url}/api/cloud/resolve?api_key={api_key}"
        async with sess.get(url) as resp:
            if resp.status == 404:
                return None
            if resp.status >= 400:
                raise BrokerError(resp.status, await resp.text())
            data = await resp.json(content_type=None)
            return data.get("tunnel_url")

    async def health(self) -> bool:
        try:
            await self._get("/api/cloud/health")
            return True
        except Exception:
            return False


async def _close_silently(client: BrokerClient | None) -> None:
    """Shutdown helper -- never re-raises so callers can use it in ``finally``."""
    if client is None:
        return
    try:
        await client.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("BrokerClient.close raised: %r", exc)


async def gather_with_log(*coros: Any) -> list[Any]:
    return await asyncio.gather(*coros, return_exceptions=True)
