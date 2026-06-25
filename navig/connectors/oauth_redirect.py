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
_DEFAULT_BASE = f"http://localhost:{_GATEWAY_PORT}"


def connector_redirect_uri() -> str:
    """Return the absolute redirect URI for the Deck connector OAuth flow."""
    base = os.getenv("NAVIG_OAUTH_REDIRECT_BASE", _DEFAULT_BASE).rstrip("/")
    return f"{base}{_CALLBACK_PATH}"
