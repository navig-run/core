"""
NAVIG AI Providers - OAuth Authentication

OAuth PKCE flow implementation for providers like OpenAI Codex.
Based on standard authentication patterns.
"""

import asyncio
import base64
import hashlib
import http.server
import json
import secrets
import socket
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    httpx = None
    HTTPX_AVAILABLE = False


# ============================================================================
# PKCE Utilities
# ============================================================================


def generate_pkce_pair() -> tuple[str, str]:
    """
    Generate PKCE code_verifier and code_challenge.

    Returns:
        Tuple of (verifier, challenge)
    """
    # Generate 32 bytes of random data (64 hex chars)
    verifier = secrets.token_hex(32)

    # SHA256 hash the verifier, then base64url encode
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    return verifier, challenge


def generate_state() -> str:
    """Generate a random state parameter for OAuth."""
    return secrets.token_hex(16)


# ============================================================================
# OAuth Credentials
# ============================================================================


@dataclass
class OAuthCredentials:
    """OAuth credentials returned from authentication."""

    access: str
    refresh: str
    expires: int  # Unix timestamp in milliseconds
    account_id: str | None = None
    email: str | None = None
    client_id: str | None = None

    @property
    def is_expired(self) -> bool:
        """Check if the access token is expired."""
        # Add 5 minute buffer
        buffer_ms = 5 * 60 * 1000
        return time.time() * 1000 >= self.expires - buffer_ms

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "access": self.access,
            "refresh": self.refresh,
            "expires": self.expires,
            "account_id": self.account_id,
            "email": self.email,
            "client_id": self.client_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OAuthCredentials":
        """Create from dictionary."""
        return cls(
            access=data.get("access", ""),
            refresh=data.get("refresh", ""),
            expires=data.get("expires", 0),
            account_id=data.get("account_id"),
            email=data.get("email"),
            client_id=data.get("client_id"),
        )


# ============================================================================
# OAuth Provider Configuration
# ============================================================================


@dataclass
class OAuthProviderConfig:
    """Configuration for an OAuth provider."""

    name: str
    authorize_url: str
    token_url: str
    client_id: str
    client_secret: str | None = None
    redirect_uri: str = "http://127.0.0.1:1455/auth/callback"
    scopes: list[str] = field(default_factory=list)
    userinfo_url: str | None = None

    def build_authorize_url(
        self,
        state: str,
        code_challenge: str,
    ) -> str:
        """Build the authorization URL with PKCE."""
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if self.scopes:
            params["scope"] = " ".join(self.scopes)

        return f"{self.authorize_url}?{urlencode(params)}"


# Built-in OAuth provider configurations
# NOTE: OAuth providers require application registration and approved client IDs.
# OpenAI's OAuth is only available to enterprise partners, not for public use.
# This framework is ready for providers that support OAuth (e.g., Google, Microsoft, etc.)
OAUTH_PROVIDERS: dict[str, OAuthProviderConfig] = {
    # Example structure (requires valid client registration):
    # "provider-name": OAuthProviderConfig(
    #     name="Provider Name",
    #     authorize_url="https://provider.com/oauth/authorize",
    #     token_url="https://provider.com/oauth/token",
    #     client_id="YOUR_CLIENT_ID",
    #     redirect_uri="http://127.0.0.1:1455/auth/callback",
    #     scopes=["openid", "offline_access"],
    # ),
}


# ============================================================================
# OAuth Callback Server
# ============================================================================


class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler for OAuth callback."""

    def log_message(self, format: str, *args) -> None:
        """Suppress HTTP logs."""
        pass

    def do_GET(self):
        """Handle GET request (OAuth callback)."""
        parsed = urlparse(self.path)

        if parsed.path == "/auth/callback":
            query = parse_qs(parsed.query)
            code = query.get("code", [None])[0]
            state = query.get("state", [None])[0]
            error = query.get("error", [None])[0]

            if error:
                self.server.oauth_result = {"error": error}
                self._send_response("Authentication failed. You can close this window.")
            elif code and state:
                self.server.oauth_result = {"code": code, "state": state}
                self._send_response(
                    "Authentication successful! You can close this window."
                )
            else:
                self.server.oauth_result = {"error": "Missing code or state"}
                self._send_response("Invalid callback. Missing code or state.")
        else:
            self.send_error(404)

    def _send_response(self, message: str):
        """Send HTML response."""
        html = f"""<!DOCTYPE html>
<html>
<head><title>NAVIG OAuth</title></head>
<body style="font-family: sans-serif; text-align: center; padding: 50px;">
<h1>{message}</h1>
<p>You can close this window.</p>
</body>
</html>"""

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode())


class OAuthCallbackServer:
    """Local HTTP server for OAuth callbacks."""

    def __init__(self, port: int = 1455, timeout: float = 120.0):
        self.port = port
        self.timeout = timeout
        self.server = None
        self.result = None
        self._thread = None

    def is_port_available(self) -> bool:
        """Check if the port is available."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", self.port))
                return True
        except OSError:
            return False

    def start(self) -> bool:
        """Start the callback server."""
        if not self.is_port_available():
            return False

        try:
            self.server = http.server.HTTPServer(
                ("127.0.0.1", self.port),
                OAuthCallbackHandler,
            )
            self.server.oauth_result = None
            self.server.timeout = 1.0

            def serve():
                start = time.time()
                while time.time() - start < self.timeout:
                    self.server.handle_request()
                    if self.server.oauth_result:
                        break

            self._thread = threading.Thread(target=serve, daemon=True)
            self._thread.start()
            return True
        except Exception:
            return False

    def wait_for_callback(self) -> dict[str, str] | None:
        """Wait for the OAuth callback."""
        if self._thread:
            self._thread.join(timeout=self.timeout)

        if self.server and self.server.oauth_result:
            return self.server.oauth_result
        return None

    def stop(self):
        """Stop the callback server."""
        if self.server:
            self.server.shutdown()
            self.server = None


# ============================================================================
# OAuth Flow
# ============================================================================


@dataclass
class OAuthFlowResult:
    """Result of an OAuth flow."""

    success: bool
    credentials: OAuthCredentials | None = None
    error: str | None = None


async def exchange_code_for_tokens(
    provider: OAuthProviderConfig,
    code: str,
    code_verifier: str,
) -> OAuthCredentials:
    """
    Exchange authorization code for tokens.

    Args:
        provider: OAuth provider configuration
        code: Authorization code from callback
        code_verifier: PKCE code verifier

    Returns:
        OAuth credentials
    """
    if not HTTPX_AVAILABLE:
        raise ImportError("httpx is required for OAuth. Install: pip install httpx")

    async with httpx.AsyncClient() as client:
        # Build token request
        data = {
            "grant_type": "authorization_code",
            "client_id": provider.client_id,
            "code": code,
            "redirect_uri": provider.redirect_uri,
            "code_verifier": code_verifier,
        }
        if provider.client_secret:
            data["client_secret"] = provider.client_secret

        response = await client.post(
            provider.token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if response.status_code != 200:
            raise Exception(f"Token exchange failed: {response.text}")

        token_data = response.json()

        access_token = token_data.get("access_token", "")
        refresh_token = token_data.get("refresh_token", "")
        expires_in = token_data.get("expires_in", 3600)

        # Calculate expiry timestamp
        expires = int((time.time() + expires_in) * 1000)

        # Try to extract account ID from token claims (JWT)
        account_id = None
        try:
            # JWT structure: header.payload.signature
            parts = access_token.split(".")
            if len(parts) >= 2:
                # Add padding if needed
                payload = parts[1]
                padding = 4 - len(payload) % 4
                if padding != 4:
                    payload += "=" * padding
                decoded = base64.urlsafe_b64decode(payload)
                claims = json.loads(decoded)
                account_id = claims.get("sub") or claims.get("account_id")
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        return OAuthCredentials(
            access=access_token,
            refresh=refresh_token,
            expires=expires,
            account_id=account_id,
            client_id=provider.client_id,
        )


async def refresh_oauth_tokens(
    provider: OAuthProviderConfig,
    credentials: OAuthCredentials,
) -> OAuthCredentials:
    """
    Refresh OAuth tokens using the refresh token.

    Args:
        provider: OAuth provider configuration
        credentials: Existing credentials with refresh token

    Returns:
        New OAuth credentials
    """
    if not HTTPX_AVAILABLE:
        raise ImportError("httpx is required for OAuth. Install: pip install httpx")

    async with httpx.AsyncClient() as client:
        data = {
            "grant_type": "refresh_token",
            "client_id": provider.client_id,
            "refresh_token": credentials.refresh,
        }
        if provider.client_secret:
            data["client_secret"] = provider.client_secret

        response = await client.post(
            provider.token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if response.status_code != 200:
            raise Exception(f"Token refresh failed: {response.text}")

        token_data = response.json()

        access_token = token_data.get("access_token", credentials.access)
        refresh_token = token_data.get("refresh_token", credentials.refresh)
        expires_in = token_data.get("expires_in", 3600)

        expires = int((time.time() + expires_in) * 1000)

        return OAuthCredentials(
            access=access_token,
            refresh=refresh_token,
            expires=expires,
            account_id=credentials.account_id,
            email=credentials.email,
            client_id=credentials.client_id,
        )


def run_oauth_flow_interactive(
    provider_name: str,
    on_progress: Callable[[str], None] | None = None,
) -> OAuthFlowResult:
    """
    Run the OAuth flow interactively.

    This handles:
    1. Starting a local callback server (if possible)
    2. Opening the browser to the authorization URL
    3. Capturing the callback or prompting for manual input
    4. Exchanging the code for tokens

    Args:
        provider_name: Name of the OAuth provider (e.g., "openai-codex")
        on_progress: Optional callback for progress updates

    Returns:
        OAuthFlowResult with credentials or error
    """
    provider = OAUTH_PROVIDERS.get(provider_name)
    if not provider:
        return OAuthFlowResult(
            success=False,
            error=f"Unknown OAuth provider: {provider_name}",
        )

    def log(msg: str):
        if on_progress:
            on_progress(msg)
        else:
            print(msg)

    # Generate PKCE and state
    code_verifier, code_challenge = generate_pkce_pair()
    state = generate_state()

    # Build authorization URL
    auth_url = provider.build_authorize_url(state, code_challenge)

    # Try to start callback server
    callback_server = OAuthCallbackServer()
    use_callback_server = callback_server.start()

    if use_callback_server:
        log(f"Starting OAuth flow for {provider.name}...")
        log("A browser window will open. Complete the sign-in there.")
    else:
        log(f"Starting OAuth flow for {provider.name}...")
        log("Note: Could not start local callback server.")
        log("You will need to paste the redirect URL manually.")

    # Open browser
    log(f"\nOpening: {auth_url}\n")
    try:
        webbrowser.open(auth_url)
    except Exception:
        log("Could not open browser automatically.")
        log(f"Please open this URL manually:\n{auth_url}\n")

    # Wait for callback or manual input
    result = None

    if use_callback_server:
        log("Waiting for authentication callback...")
        result = callback_server.wait_for_callback()
        callback_server.stop()

    if not result:
        # Manual input fallback
        log("\nPaste the redirect URL (or just the authorization code):")
        try:
            user_input = input("> ").strip()
        except EOFError:
            return OAuthFlowResult(success=False, error="No input received")

        # Parse input
        if user_input.startswith("http"):
            parsed = urlparse(user_input)
            query = parse_qs(parsed.query)
            code = query.get("code", [None])[0]
            recv_state = query.get("state", [None])[0]

            if recv_state and recv_state != state:
                return OAuthFlowResult(success=False, error="State mismatch")

            result = {"code": code, "state": recv_state or state}
        else:
            # Assume it's just the code
            result = {"code": user_input, "state": state}

    if not result or "error" in result:
        error = result.get("error") if result else "No callback received"
        return OAuthFlowResult(success=False, error=str(error))

    code = result.get("code")
    if not code:
        return OAuthFlowResult(success=False, error="No authorization code received")

    # Exchange code for tokens
    log("\nExchanging code for tokens...")

    try:
        credentials = asyncio.run(
            exchange_code_for_tokens(provider, code, code_verifier)
        )
        log("Authentication successful!")
        return OAuthFlowResult(success=True, credentials=credentials)
    except Exception as e:
        return OAuthFlowResult(success=False, error=str(e))


def run_oauth_flow_headless(
    provider_name: str,
    on_auth_url: Callable[[str], None],
    get_callback_input: Callable[[], str],
) -> OAuthFlowResult:
    """
    Run the OAuth flow in headless/VPS mode.

    This is for environments where a browser can't be opened automatically.
    The caller must display the URL and get the callback input.

    Args:
        provider_name: Name of the OAuth provider
        on_auth_url: Callback to display the auth URL
        get_callback_input: Callback to get the redirect URL/code

    Returns:
        OAuthFlowResult with credentials or error
    """
    provider = OAUTH_PROVIDERS.get(provider_name)
    if not provider:
        return OAuthFlowResult(
            success=False,
            error=f"Unknown OAuth provider: {provider_name}",
        )

    # Generate PKCE and state
    code_verifier, code_challenge = generate_pkce_pair()
    state = generate_state()

    # Build authorization URL
    auth_url = provider.build_authorize_url(state, code_challenge)

    # Display URL
    on_auth_url(auth_url)

    # Get callback input
    user_input = get_callback_input().strip()

    # Parse input
    if user_input.startswith("http"):
        parsed = urlparse(user_input)
        query = parse_qs(parsed.query)
        code = query.get("code", [None])[0]
        recv_state = query.get("state", [None])[0]

        if recv_state and recv_state != state:
            return OAuthFlowResult(success=False, error="State mismatch")
    else:
        code = user_input

    if not code:
        return OAuthFlowResult(success=False, error="No authorization code")

    # Exchange code for tokens
    try:
        credentials = asyncio.run(
            exchange_code_for_tokens(provider, code, code_verifier)
        )
        return OAuthFlowResult(success=True, credentials=credentials)
    except Exception as e:
        return OAuthFlowResult(success=False, error=str(e))
