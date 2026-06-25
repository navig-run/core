"""Cloudflare browser OAuth (PKCE) for one-click Lighthouse login.

Mirrors the flow `wrangler login` uses: open the Cloudflare dashboard's OAuth
consent screen, receive the auth code on a fixed localhost callback, and
exchange it (with PKCE) for an access + refresh token. The access token is a
Bearer that works against ``api.cloudflare.com/client/v4`` exactly like a
scoped API token, so the rest of the deploy path is unchanged.

Caveat: Cloudflare has no first-party OAuth-app registration for third parties,
so this reuses Wrangler's **public** client id + its registered localhost
redirect (``http://localhost:8976/oauth/callback``). It works today; if
Cloudflare ever locks that down, fall back to a scoped API token
(`navig lighthouse deploy --token …`).

Pure-Python: no Node, no wrangler binary.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

logger = logging.getLogger(__name__)

# Wrangler's public OAuth client + its registered redirect (fixed — do not change).
CLIENT_ID = "54d11594-84e4-41aa-b438-e81b8fa78ee7"
AUTH_URL = "https://dash.cloudflare.com/oauth2/auth"
TOKEN_URL = "https://dash.cloudflare.com/oauth2/token"
REVOKE_URL = "https://dash.cloudflare.com/oauth2/revoke"
CALLBACK_PORT = 8976
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/oauth/callback"

# Minimal scope subset (all standard Wrangler scopes). offline_access → refresh token.
SCOPES = [
    "account:read",
    "user:read",
    "workers_scripts:write",
    "workers_routes:write",
    "offline_access",
]


class OAuthError(Exception):
    pass


@dataclass
class TokenBundle:
    access_token: str
    refresh_token: str | None
    expires_at: str  # ISO-8601 UTC
    scope: str = ""

    def as_vault_data(self) -> dict[str, str]:
        return {
            "token": self.access_token,
            "refresh_token": self.refresh_token or "",
            "expires_at": self.expires_at,
            "auth": "oauth",
        }


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _expires_at(expires_in: int | None) -> str:
    secs = int(expires_in or 0)
    # 60s safety margin so we refresh slightly early.
    when = datetime.now(timezone.utc) + timedelta(seconds=max(0, secs - 60))
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


_DONE_HTML = (
    b"<!doctype html><meta charset=utf-8><title>NAVIG</title>"
    b"<body style='font-family:system-ui;background:#050505;color:#eee;"
    b"display:flex;align-items:center;justify-content:center;height:100vh'>"
    b"<div style='text-align:center'><div style='font-size:42px'>\xf0\x9f\x97\xbc</div>"
    b"<h2>Cloudflare connected</h2><p>You can close this tab and return to NAVIG.</p></div>"
)


class _CallbackHandler(BaseHTTPRequestHandler):
    result: dict[str, str] = {}

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/oauth/callback":
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        _CallbackHandler.result = {k: v[0] for k, v in params.items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_DONE_HTML)

    def log_message(self, *args):  # silence the default stderr logging
        return


def login(*, timeout: float = 180.0, open_browser: bool = True) -> TokenBundle:
    """Run the browser OAuth flow and return a :class:`TokenBundle`.

    Raises :class:`OAuthError` on denial, timeout, state mismatch, or if the
    callback port is unavailable.
    """
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(24)
    auth_url = AUTH_URL + "?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })

    _CallbackHandler.result = {}
    try:
        server = HTTPServer(("127.0.0.1", CALLBACK_PORT), _CallbackHandler)
    except OSError as exc:
        raise OAuthError(
            f"Can't bind localhost:{CALLBACK_PORT} for the OAuth callback "
            f"({exc}). Close whatever is using it (e.g. wrangler) and retry, "
            f"or use `navig lighthouse deploy --token …` instead."
        ) from exc
    server.timeout = 1.0

    if open_browser:
        import webbrowser

        try:
            webbrowser.open(auth_url)
        except Exception:  # noqa: BLE001
            pass

    deadline = time.monotonic() + timeout
    try:
        while not _CallbackHandler.result and time.monotonic() < deadline:
            server.handle_request()  # serves one request per loop (1s timeout)
    finally:
        server.server_close()

    result = _CallbackHandler.result
    if not result:
        raise OAuthError("Timed out waiting for Cloudflare authorization.")
    if result.get("error"):
        raise OAuthError(f"Authorization denied: {result.get('error_description') or result['error']}")
    if result.get("state") != state:
        raise OAuthError("OAuth state mismatch — aborting for safety.")
    code = result.get("code")
    if not code:
        raise OAuthError("No authorization code returned.")

    return _exchange({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "code_verifier": verifier,
    })


def refresh(refresh_token: str) -> TokenBundle:
    """Exchange a refresh token for a fresh access token."""
    return _exchange({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": CLIENT_ID,
    })


def _exchange(form: dict[str, str]) -> TokenBundle:
    try:
        resp = requests.post(
            TOKEN_URL,
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise OAuthError(f"Token exchange request failed: {exc}") from exc
    try:
        data = resp.json()
    except ValueError as exc:
        raise OAuthError(f"Token endpoint returned non-JSON (HTTP {resp.status_code}).") from exc
    if resp.status_code != 200 or "access_token" not in data:
        raise OAuthError(data.get("error_description") or data.get("error") or f"HTTP {resp.status_code}")
    return TokenBundle(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token") or form.get("refresh_token"),
        expires_at=_expires_at(data.get("expires_in")),
        scope=data.get("scope", ""),
    )


def is_expired(expires_at: str | None) -> bool:
    if not expires_at:
        return True
    try:
        exp = datetime.strptime(expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True
    return datetime.now(timezone.utc) >= exp
