"""Tier-C — a cloud anti-detect browser over CDP (opt-in, BYO, off by default).

When local stealth + a proxy still gets blocked, the last resort is to rent a browser:
services like Scrapeless/Browserless expose a real anti-detect browser at a ``wss://``
CDP endpoint with residential egress and challenge-handling done server-side. Connecting
is the *same* ``connect_over_cdp`` call as :class:`CDPBridge` — only the endpoint changes —
so ``CloudBridge`` is a thin subclass (connection-pattern only; the endpoint is BYO).

Config (``~/.navig/config.yaml``)::

    browser:
      cloud:
        endpoint: "wss://browser.example.com/cdp"   # provider CDP WS
        token: "…"        # prefer NAVIG_CLOUD_BROWSER_TOKEN env / vault over plaintext

The token is treated as a secret: it is never logged (the display endpoint strips it).
"""

from __future__ import annotations

import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from navig.browser.cdp_bridge import CDPBridge
from navig.debug_logger import get_debug_logger

logger = get_debug_logger()

__all__ = ["CloudBridge", "cloud_config", "cloud_enabled"]

_TOKEN_ENV = "NAVIG_CLOUD_BROWSER_TOKEN"


class CloudBridge(CDPBridge):
    """Attach to a remote anti-detect browser at a ``wss://`` CDP endpoint.

    Inherits every driving method from CDPBridge/BrowserController; only the connection
    target (and its secret handling) differs. ``stop()`` disconnects the WS without killing
    the remote session, exactly like the local attach mode.
    """

    def __init__(self, endpoint: str, *, token: str | None = None,
                 token_param: str = "token"):
        super().__init__(debug_port=0)
        self._raw_endpoint = endpoint
        self._token = token
        self._token_param = token_param
        self._cdp_endpoint = self._build_endpoint(endpoint, token, token_param)

    @staticmethod
    def _build_endpoint(endpoint: str, token: str | None, token_param: str) -> str:
        """Attach the token as a query param without duplicating an existing one."""
        if not token:
            return endpoint
        parts = urlsplit(endpoint)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query.setdefault(token_param, token)
        return urlunsplit((parts.scheme, parts.netloc, parts.path,
                           urlencode(query), parts.fragment))

    def _display_endpoint(self) -> str:
        """Endpoint with the token redacted — safe to log."""
        parts = urlsplit(self._cdp_endpoint)
        if not parts.query:
            return self._cdp_endpoint
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        if self._token_param in query:
            query[self._token_param] = "***"
        return urlunsplit((parts.scheme, parts.netloc, parts.path,
                           urlencode(query), parts.fragment))

    def _connection_hint(self) -> str:
        return ("Check browser.cloud.endpoint and the cloud token "
                f"({_TOKEN_ENV} or browser.cloud.token). The provider session may be "
                "exhausted or the token invalid.")

    # ── construction from config ─────────────────────────────────────────────
    @classmethod
    def from_config(cls) -> "CloudBridge | None":
        """Build from ``browser.cloud`` config, or None if not configured/enabled."""
        cfg = cloud_config()
        endpoint = (cfg.get("endpoint") or "").strip()
        if not endpoint:
            return None
        token = os.environ.get(_TOKEN_ENV) or cfg.get("token") or None
        return cls(endpoint, token=token,
                   token_param=str(cfg.get("token_param", "token")))


def cloud_config() -> dict:
    """The ``browser.cloud`` config subtree (empty dict if unset)."""
    try:
        from navig.config import get_config_manager  # noqa: PLC0415

        return (get_config_manager().global_config.get("browser", {}) or {}).get("cloud", {}) or {}
    except Exception:  # noqa: BLE001
        return {}


def cloud_enabled() -> bool:
    """True only when a cloud endpoint is configured (Tier-C is opt-in)."""
    return bool((cloud_config().get("endpoint") or "").strip())
