"""
Claude Pro/Max subscription OAuth — PKCE token acquisition.

Mints a Claude.ai subscription OAuth token so `claude-max` is self-service
(no pasting a raw token):

  1. build_authorize_url() → (url, flow): PKCE + state + the claude.ai authorize URL.
  2. user opens the URL, authenticates; the callback page shows an authorization
     code (the redirect URI is Anthropic's console, so the user copies the code).
  3. exchange_code(code, flow) → tokens {access_token, refresh_token, expires_at}.
  4. refresh_tokens(refresh_token) → fresh tokens when the access token expires.

Works for **both Pro and Max** — the tier only affects rate limits, not auth.
The minted token is then used by the OAuth request path in
:mod:`navig.providers.anthropic_oauth` (presents as the official Claude Code CLI).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

# Public OAuth client config — the same public PKCE client the official CLI uses.
CLAUDE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
CLAUDE_AUTH_URL = "https://claude.ai/oauth/authorize"
CLAUDE_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLAUDE_REDIRECT_URI = "https://console.anthropic.com/oauth/code/callback"
CLAUDE_OAUTH_SCOPES = "org:create_api_key user:profile user:inference"
STATE_EXPIRY_S = 600  # 10 minutes


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def generate_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for S256 PKCE."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def generate_state() -> str:
    return secrets.token_bytes(32).hex()


@dataclass
class ClaudeOAuthFlow:
    """Server-held PKCE state for one in-flight login. Never leaves the host."""

    state: str
    code_verifier: str
    created_at: float = field(default_factory=time.time)

    @property
    def expired(self) -> bool:
        return time.time() > self.created_at + STATE_EXPIRY_S


def build_authorize_url() -> tuple[str, ClaudeOAuthFlow]:
    """Build the claude.ai authorize URL + the flow state to complete it."""
    state = generate_state()
    verifier, challenge = generate_pkce()
    params = urlencode({
        "code": "true",
        "client_id": CLAUDE_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": CLAUDE_REDIRECT_URI,
        "scope": CLAUDE_OAUTH_SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    })
    return f"{CLAUDE_AUTH_URL}?{params}", ClaudeOAuthFlow(state=state, code_verifier=verifier)


def _clean_code(code: str) -> str:
    """The callback page often yields ``CODE#STATE`` (or with &fragments)."""
    return code.split("#")[0].split("&")[0].strip()


def _extract_state(code: str) -> str | None:
    """Return the STATE from a ``CODE#STATE`` paste, or None if not present."""
    if "#" in code:
        return code.split("#", 1)[1].split("&")[0].strip() or None
    return None


async def _post_token(body: dict[str, str]) -> dict:
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            CLAUDE_TOKEN_URL,
            json=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "navig-cli (external, cli)",
            },
        )
    if resp.status_code != 200:
        text = resp.text
        try:
            data = resp.json()
            text = data.get("error_description") or data.get("error") or text
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"Token endpoint {resp.status_code}: {text[:300]}")
    return resp.json()


def _tokens_from_response(data: dict) -> dict:
    expires_in = data.get("expires_in")
    out = {
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token"),
        "expires_at": (int(time.time()) + int(expires_in)) if expires_in else None,
        "scopes": (data.get("scope") or "").split() if data.get("scope") else [],
    }
    # Account identity (present on the INITIAL token exchange, not on refresh) —
    # lets NAVIG label each connection by email and de-dupe by account UUID so
    # multiple Claude subscriptions (different accounts) stay separate connections.
    acct = data.get("account") or {}
    if isinstance(acct, dict):
        if acct.get("uuid"):
            out["account_uuid"] = acct.get("uuid")
        if acct.get("email_address") or acct.get("email"):
            out["account_email"] = acct.get("email_address") or acct.get("email")
    org = data.get("organization") or {}
    if isinstance(org, dict) and org.get("name"):
        out["organization"] = org.get("name")
    return out


async def exchange_code(code: str, flow: ClaudeOAuthFlow) -> dict:
    """Exchange an authorization code for tokens. Raises on expiry/HTTP error."""
    if flow.expired:
        raise RuntimeError("OAuth state expired (older than 10 minutes). Start again.")
    # CSRF: verify the returned state when the paste carries it (CODE#STATE).
    # (If absent, PKCE's code_verifier still binds the code to this client.)
    returned_state = _extract_state(code)
    if returned_state and returned_state != flow.state:
        raise RuntimeError("OAuth state mismatch — aborting login (possible CSRF / wrong tab).")
    data = await _post_token({
        "grant_type": "authorization_code",
        "client_id": CLAUDE_CLIENT_ID,
        "code": _clean_code(code),
        "redirect_uri": CLAUDE_REDIRECT_URI,
        "code_verifier": flow.code_verifier,
        "state": flow.state,
    })
    return _tokens_from_response(data)


async def refresh_tokens(refresh_token: str) -> dict:
    """Mint a fresh access token from a refresh token."""
    data = await _post_token({
        "grant_type": "refresh_token",
        "client_id": CLAUDE_CLIENT_ID,
        "refresh_token": refresh_token,
    })
    out = _tokens_from_response(data)
    # Some refresh responses omit a new refresh_token — keep the old one.
    if not out["refresh_token"]:
        out["refresh_token"] = refresh_token
    return out
