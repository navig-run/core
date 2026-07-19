"""
ChatGPT (Codex) subscription OAuth — PKCE + RFC 8693 token-exchange.

Lets a user connect a ChatGPT Plus/Pro subscription. Unlike Claude (which keeps an OAuth bearer and
presents as the CLI on every request), the ChatGPT flow converts the OAuth
`id_token` into a first-class **OpenAI API key** via RFC 8693 token-exchange —
so inference then runs through the standard OpenAI path with no request spoofing.

  1. build_authorize_url() → (url, flow): PKCE + the Codex authorize params.
  2. user opens the URL, authenticates; the official client redirects to
     http://localhost:1455/auth/callback?code=…&state=… (loopback). Capture the
     code with a transient listener, or paste the redirected URL / code.
  3. exchange_code(code, flow) → {id_token, access_token, refresh_token, expires_at}.
  4. exchange_id_token_for_api_key(id_token) → an OpenAI API key.

Token endpoint is **form-urlencoded** (unlike Claude's JSON body).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlencode, urlparse

# Official Codex CLI public client.
CHATGPT_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CHATGPT_AUTH_URL = "https://auth.openai.com/oauth/authorize"
CHATGPT_TOKEN_URL = "https://auth.openai.com/oauth/token"
CHATGPT_REDIRECT_URI = "http://localhost:1455/auth/callback"
CHATGPT_CALLBACK_PORT = 1455
CHATGPT_SCOPES = "openid profile email offline_access"
STATE_EXPIRY_S = 600


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def generate_pkce() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def generate_state() -> str:
    return secrets.token_bytes(32).hex()


@dataclass
class CodexOAuthFlow:
    state: str
    code_verifier: str
    created_at: float = field(default_factory=time.time)

    @property
    def expired(self) -> bool:
        return time.time() > self.created_at + STATE_EXPIRY_S


def build_authorize_url() -> tuple[str, CodexOAuthFlow]:
    state = generate_state()
    verifier, challenge = generate_pkce()
    params = urlencode({
        "client_id": CHATGPT_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": CHATGPT_REDIRECT_URI,
        "scope": CHATGPT_SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "codex_cli_simplified_flow": "true",
        "id_token_add_organizations": "true",
    })
    return f"{CHATGPT_AUTH_URL}?{params}", CodexOAuthFlow(state=state, code_verifier=verifier)


def parse_code_from_redirect(value: str) -> str:
    """Accept a raw code OR a full redirected localhost URL → return the code."""
    v = value.strip()
    if v.startswith("http://") or v.startswith("https://"):
        q = parse_qs(urlparse(v).query)
        return (q.get("code") or [""])[0]
    return v.split("#")[0].split("&")[0]


def _extract_state(value: str) -> str | None:
    """Return the STATE from a redirected URL (``?state=``) or ``code#state`` paste."""
    v = value.strip()
    if v.startswith("http://") or v.startswith("https://"):
        return (parse_qs(urlparse(v).query).get("state") or [None])[0]
    if "#" in v:
        return v.split("#", 1)[1].split("&")[0].strip() or None
    return None


async def _post_form(body: dict[str, str]) -> dict:
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            CHATGPT_TOKEN_URL,
            data=body,  # form-urlencoded
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        )
    if resp.status_code != 200:
        text = resp.text
        try:
            data = resp.json()
            text = data.get("error_description") or data.get("error") or text
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"ChatGPT token endpoint {resp.status_code}: {text[:300]}")
    return resp.json()


def _tokens(data: dict) -> dict:
    expires_in = data.get("expires_in")
    return {
        "id_token": data.get("id_token"),
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token"),
        "expires_at": (int(time.time()) + int(expires_in)) if expires_in else None,
    }


async def exchange_code(code: str, flow: CodexOAuthFlow) -> dict:
    if flow.expired:
        raise RuntimeError("OAuth state expired (older than 10 minutes). Start again.")
    # CSRF: verify the returned state when the paste carries it (PKCE also binds).
    returned_state = _extract_state(code)
    if returned_state and returned_state != flow.state:
        raise RuntimeError("OAuth state mismatch — aborting login (possible CSRF / wrong tab).")
    data = await _post_form({
        "grant_type": "authorization_code",
        "client_id": CHATGPT_CLIENT_ID,
        "code": parse_code_from_redirect(code),
        "redirect_uri": CHATGPT_REDIRECT_URI,
        "code_verifier": flow.code_verifier,
    })
    return _tokens(data)


async def refresh_tokens(refresh_token: str) -> dict:
    data = await _post_form({
        "grant_type": "refresh_token",
        "client_id": CHATGPT_CLIENT_ID,
        "refresh_token": refresh_token,
    })
    out = _tokens(data)
    if not out["refresh_token"]:
        out["refresh_token"] = refresh_token  # keep old if none returned
    return out


async def exchange_id_token_for_api_key(id_token: str) -> str:
    """RFC 8693: convert the ChatGPT id_token into a first-class OpenAI API key."""
    data = await _post_form({
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "client_id": CHATGPT_CLIENT_ID,
        "subject_token": id_token,
        "subject_token_type": "urn:ietf:params:oauth:token-type:id_token",
        "requested_token": "openai-api-key",
    })
    key = data.get("access_token") or data.get("api_key") or data.get("openai_api_key")
    if not key:
        raise RuntimeError("Token exchange returned no OpenAI API key.")
    return key


def capture_loopback_code(state: str, *, timeout: float = 180.0) -> str | None:
    """Run a transient localhost:1455 listener and return the captured code.

    Used by the CLI on the same machine; returns None on timeout. The deck flow
    uses paste instead (the listener can't span a remote browser session).
    """
    import http.server
    import threading

    captured: dict[str, str] = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_a):  # silence
            return

        def do_GET(self):  # noqa: N802
            q = parse_qs(urlparse(self.path).query)
            if q.get("state", [""])[0] == state and q.get("code"):
                captured["code"] = q["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>You can close this tab and return to NAVIG.</body></html>")
            done.set()

    try:
        server = http.server.HTTPServer(("127.0.0.1", CHATGPT_CALLBACK_PORT), Handler)
    except OSError:
        return None  # port busy → caller falls back to paste
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    got = done.wait(timeout)
    server.shutdown()
    return captured.get("code") if got else None
