"""
Connector Auth Manager

Wraps the existing NAVIG vault (``navig.vault``) and OAuth PKCE infra
(``navig.providers.oauth``) to provide a unified auth façade for connectors.

Responsibilities:
1. ``authenticate(connector_id)`` — vault lookup → refresh if expired →
   full PKCE flow if no token → inject into connector
2. ``get_access_token(connector_id)`` — transparent refresh + return
3. ``revoke(connector_id)`` — delete vault entry, mark disconnected
4. ``register_provider(connector_id, config)`` — add OAuth config

Error contract (per exception-policy.instructions.md):
- Refresh failure → log + raise ``ConnectorAuthError`` (never silent)
- Missing provider config → raise ``ConnectorNotFoundError``
"""

from __future__ import annotations

import logging
import time

from navig.connectors.errors import ConnectorAuthError, ConnectorNotFoundError
from navig.providers.oauth import (
    OAUTH_PROVIDERS,
    OAuthCredentials,
    OAuthProviderConfig,
    generate_pkce_pair,
    generate_state,
    refresh_oauth_tokens,
    run_oauth_flow_interactive,
)
from navig.vault import CredentialType, get_vault

logger = logging.getLogger("navig.connectors.auth")


class ConnectorAuthManager:
    """
    Central auth manager for all connectors.

    Uses the NAVIG vault for encrypted token persistence and the OAuth
    module for PKCE flows and token refresh.
    """

    # Class-level provider config registry (supplements OAUTH_PROVIDERS)
    _provider_configs: dict[str, OAuthProviderConfig] = {}

    # In-memory PKCE pending state: state_token → (connector_id, verifier)
    # Entries expire after 10 minutes (checked on access).
    _pending_auth: dict[str, tuple[str, str, float]] = {}  # state → (id, verifier, created_at)

    def __init__(self) -> None:
        self._vault = get_vault()

    # -- Provider registration ---------------------------------------------

    @classmethod
    def register_provider(cls, connector_id: str, config: OAuthProviderConfig) -> None:
        """
        Register an OAuth provider config for a connector.

        Also populates the global ``OAUTH_PROVIDERS`` dict so the
        existing ``run_oauth_flow_interactive`` / ``_headless`` helpers
        can resolve the provider by name.
        """
        cls._provider_configs[connector_id] = config
        OAUTH_PROVIDERS[connector_id] = config
        logger.debug("Registered OAuth provider config: %s", connector_id)

    @classmethod
    def get_provider_config(cls, connector_id: str) -> OAuthProviderConfig | None:
        """Return the OAuth config for *connector_id*, or ``None``."""
        return cls._provider_configs.get(connector_id) or OAUTH_PROVIDERS.get(connector_id)

    @classmethod
    def reset_providers(cls) -> None:
        """Clear all registered provider configs.

        Useful in tests to prevent state leakage between test cases that call
        ``register_provider()`` — call this in your fixture teardown.
        """
        cls._provider_configs.clear()

    # -- Headless OAuth (Deck/UI flow) -------------------------------------

    def get_auth_url(self, connector_id: str) -> tuple[str, str, str]:
        """
        Generate an OAuth authorization URL for headless (browser-opened) flow.

        Returns (auth_url, state, verifier).  The caller should open auth_url
        in a browser; after the user authorizes, call
        ``exchange_auth_code(state, code)`` to complete the flow.

        Raises:
            ConnectorNotFoundError: If no provider config is registered.
        """
        config = self.get_provider_config(connector_id)
        if not config:
            raise ConnectorNotFoundError(connector_id)

        verifier, challenge = generate_pkce_pair()
        state = generate_state()
        auth_url = config.build_authorize_url(state, challenge)

        ConnectorAuthManager._pending_auth[state] = (connector_id, verifier, time.time())
        logger.debug("Generated auth URL for %s (state=%s…)", connector_id, state[:8])
        return auth_url, state, verifier

    async def exchange_auth_code(
        self, state: str, code: str
    ) -> OAuthCredentials:
        """
        Exchange an OAuth authorization code for tokens.

        Looks up the pending PKCE verifier by *state*, calls the token
        endpoint, stores the result in the vault, and returns the credentials.
        """
        from navig.providers.oauth import exchange_code_for_tokens

        _PENDING_TTL = 600  # 10 minutes

        entry = ConnectorAuthManager._pending_auth.get(state)
        if not entry:
            raise ConnectorAuthError("unknown", f"No pending auth for state {state!r}")

        connector_id, verifier, created_at = entry
        if time.time() - created_at > _PENDING_TTL:
            del ConnectorAuthManager._pending_auth[state]
            raise ConnectorAuthError(connector_id, "Auth session expired — please retry")

        config = self.get_provider_config(connector_id)
        if not config:
            raise ConnectorNotFoundError(connector_id)

        try:
            creds = await exchange_code_for_tokens(config, code, verifier)
        except Exception as exc:
            raise ConnectorAuthError(connector_id, f"Token exchange failed: {exc}") from exc
        finally:
            ConnectorAuthManager._pending_auth.pop(state, None)

        self._save_to_vault(connector_id, creds)
        logger.info("Completed OAuth exchange for %s (account=%s)", connector_id, creds.email)
        return creds

    def get_connected_account(self, connector_id: str) -> str | None:
        """Return the account email for a connected connector, or None."""
        creds = self._load_from_vault(connector_id)
        return creds.email if creds else None

    def is_connected(self, connector_id: str) -> bool:
        """Return True if a non-expired token exists in the vault."""
        creds = self._load_from_vault(connector_id)
        return creds is not None and not creds.is_expired

    # -- Token lifecycle ---------------------------------------------------

    async def authenticate(
        self,
        connector_id: str,
        *,
        interactive: bool = True,
    ) -> str:
        """
        Ensure a valid access token exists for *connector_id*.

        Resolution order:
        1. Vault lookup → if token present and valid, return it
        2. If expired, attempt refresh
        3. If refresh fails or no token, run PKCE flow
        4. Store new token in vault
        5. Return access_token string

        Raises:
            ConnectorAuthError: If auth cannot be completed.
            ConnectorNotFoundError: If no provider config is registered.
        """
        config = self.get_provider_config(connector_id)
        if not config:
            raise ConnectorNotFoundError(connector_id)

        # 1. Check vault for existing credentials
        creds = self._load_from_vault(connector_id)
        if creds and not creds.is_expired:
            logger.debug("Vault hit for %s — token valid", connector_id)
            return creds.access

        # 2. Attempt refresh
        if creds and creds.refresh:
            try:
                new_creds = await refresh_oauth_tokens(config, creds)
                self._save_to_vault(connector_id, new_creds)
                logger.info("Refreshed token for %s", connector_id)
                return new_creds.access
            except Exception as exc:
                logger.warning(
                    "Token refresh failed for %s: %s — will re-authenticate",
                    connector_id,
                    exc,
                )
                # Fall through to full flow

        # 3. Full PKCE flow
        if not interactive:
            raise ConnectorAuthError(
                connector_id,
                "No valid token and non-interactive mode — cannot authenticate",
            )

        result = run_oauth_flow_interactive(connector_id)
        if not result.success or not result.credentials:
            raise ConnectorAuthError(
                connector_id,
                result.error or "OAuth flow failed",
            )

        self._save_to_vault(connector_id, result.credentials)
        logger.info("Authenticated %s via OAuth PKCE", connector_id)
        return result.credentials.access

    async def get_access_token(self, connector_id: str) -> str:
        """
        Return a valid access token, refreshing transparently if needed.

        This is the method connectors call before every API request.
        """
        creds = self._load_from_vault(connector_id)
        if not creds:
            raise ConnectorAuthError(
                connector_id, "No stored credentials — run authenticate() first"
            )

        if not creds.is_expired:
            return creds.access

        # Attempt refresh
        config = self.get_provider_config(connector_id)
        if not config:
            raise ConnectorNotFoundError(connector_id)

        if not creds.refresh:
            raise ConnectorAuthError(connector_id, "Token expired and no refresh token available")

        try:
            new_creds = await refresh_oauth_tokens(config, creds)
            self._save_to_vault(connector_id, new_creds)
            return new_creds.access
        except Exception as exc:
            logger.error("Token refresh failed for %s: %s", connector_id, exc)
            raise ConnectorAuthError(connector_id, f"Token refresh failed: {exc}") from exc

    async def revoke(self, connector_id: str) -> None:
        """Remove stored credentials for *connector_id*."""
        try:
            cred = self._vault.get(connector_id, profile_id="connector")
            if cred:
                self._vault.remove(cred.id)
                logger.info("Revoked credentials for %s", connector_id)
        except Exception as exc:
            logger.debug(
                "Failed to remove vault entry for %s: %s (non-critical)",
                connector_id,
                exc,
            )

    # -- Vault helpers (private) -------------------------------------------

    def _load_from_vault(self, connector_id: str) -> OAuthCredentials | None:
        """Load OAuth credentials from vault, or return None."""
        try:
            cred = self._vault.get(connector_id, profile_id="connector")
            if cred and cred.credential_type == CredentialType.OAUTH:
                return OAuthCredentials.from_dict(cred.data)
        except Exception as exc:
            logger.debug("Vault lookup for %s failed: %s", connector_id, exc)
        return None

    def _save_to_vault(self, connector_id: str, creds: OAuthCredentials) -> None:
        """Persist OAuth credentials to vault."""
        # Remove old entry if exists
        try:
            existing = self._vault.get(connector_id, profile_id="connector")
            if existing:
                self._vault.remove(existing.id)
        except Exception:  # noqa: BLE001
            pass  # best-effort cleanup of stale entry

        self._vault.add(
            provider=connector_id,
            credential_type=CredentialType.OAUTH.value,
            data=creds.to_dict(),
            profile_id="connector",
            label=f"{connector_id} connector OAuth",
            metadata={
                "email": creds.email,
                "account_id": creds.account_id,
                "stored_at": time.time(),
            },
        )
        logger.debug("Saved credentials to vault for %s", connector_id)
