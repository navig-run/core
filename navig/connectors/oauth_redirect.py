"""Shared OAuth redirect-URI resolution for Deck-driven connector auth.

The headless (Deck) OAuth flow redirects the browser to a gateway-hosted
callback that completes the PKCE exchange. All connector ``oauth_config``
modules import ``connector_redirect_uri()`` so the redirect target is
consistent and configurable.

Override the base with env ``NAVIG_OAUTH_REDIRECT_BASE`` (e.g. a public
tunnel URL) — must match what's registered in each provider's OAuth app.
"""

from __future__ import annotations

import os

from navig._daemon_defaults import _GATEWAY_PORT

_CALLBACK_PATH = "/api/deck/connectors/oauth/callback"


def connector_redirect_uri() -> str:
    """Return the absolute redirect URI for the Deck connector OAuth flow.

    Without an env override, the port is LIVE-resolved (the gateway's
    self-healing bind may have moved it off 8789 — a redirect to a dead port
    can never complete). The hostname stays ``localhost``: providers register
    loopback redirects against that name, and the lenient ones (Google-style
    loopback rules) accept any port on it. Providers that pin the exact URI
    need ``NAVIG_OAUTH_REDIRECT_BASE`` to match their registration.
    """
    base = os.getenv("NAVIG_OAUTH_REDIRECT_BASE", "").rstrip("/")
    if base:
        return f"{base}{_CALLBACK_PATH}"
    try:
        from navig.gateway_client import gateway_live_defaults

        port = gateway_live_defaults()[0]
    except Exception:  # noqa: BLE001
        port = _GATEWAY_PORT
    return f"http://localhost:{port}{_CALLBACK_PATH}"
