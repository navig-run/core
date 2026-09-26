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


DEFAULT_PROFILE = "connector"


def profile_for(account: str | None) -> str:
    """Vault profile id for a connector credential.

    The first account of a connector lives in the ``connector`` profile (the shape
    every existing install already has). A second account of the same connector —
    a personal Gmail next to the studio's — gets ``connector:<email>`` so linking it
    never overwrites the first. ``account`` is the email as Google reports it.
    """
    acct = (account or "").strip().lower()
    return f"{DEFAULT_PROFILE}:{acct}" if acct else DEFAULT_PROFILE


class ConnectorAuthManager:
    """
    Central auth manager for all connectors.

    Uses the NAVIG vault for encrypted token persistence and the OAuth
    module for PKCE flows and token refresh. Every token method takes an optional
    ``account`` (email): ``None`` is the connector's default account.
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

    def get_connected_account(self, connector_id: str, account: str | None = None) -> str | None:
        """Return the account email for a connected connector, or None."""
        creds = self._load_from_vault(connector_id, account)
        return creds.email if creds else None

    def list_accounts(self, connector_id: str) -> list[str]:
        """Every account email linked for *connector_id* — the default one first."""
        out: list[str] = []
        try:
            for cred in self._vault.list(provider=connector_id) or []:
                prof = str(getattr(cred, "profile_id", "") or "")
                if prof != DEFAULT_PROFILE and not prof.startswith(DEFAULT_PROFILE + ":"):
                    continue
                meta = getattr(cred, "metadata", None) or {}
                email = str(meta.get("email") or "").strip().lower()
                if prof == DEFAULT_PROFILE:
                    out.insert(0, email or "(default)")
                elif email or prof.partition(":")[2]:
                    out.append(email or prof.partition(":")[2])
        except Exception as exc:  # noqa: BLE001
            logger.debug("Vault list for %s accounts failed: %s", connector_id, exc)
        return out

    def resolve_account(self, connector_id: str, account: str | None) -> str | None:
        """Map an ``--account`` email onto the profile that holds it.

        ``None`` (no account asked) stays the default profile. An email that IS the
        default profile's account also resolves to the default profile, so callers
        may always pass the address they mean without knowing which slot it landed in.
        Raises ``ConnectorAuthError`` when nothing is linked under that address.
        """
        if not account:
            return None
        wanted = account.strip().lower()
        default_email = (self.get_connected_account(connector_id) or "").strip().lower()
        if wanted == default_email:
            return None
        if self._load_from_vault(connector_id, wanted) is not None:
            return wanted
        linked = ", ".join(self.list_accounts(connector_id)) or "none"
        raise ConnectorAuthError(
            connector_id, f"no linked account {wanted!r} (linked: {linked}) — run `navig connector connect {connector_id} --account {wanted}`"
        )

    def is_connected(self, connector_id: str, account: str | None = None) -> bool:
        """Return True if a *usable* token exists in the vault.

        "Usable" deliberately includes an **expired access token that carries a refresh
        token**. OAuth access tokens are short-lived — Google's last one hour — and
        ``get_access_token()`` refreshes them transparently on the very next call. Treating
        an aged-out access token as "not connected" reported a perfectly healthy account as
        disconnected roughly an hour after it was linked, and told the user to reconnect —
        which "fixed" it for exactly one more hour. Only a credential that is expired *and*
        has no refresh token genuinely needs the user to re-authenticate.

        This matches the semantics ``list_connected_accounts()`` already documents
        ("Includes expired tokens (still 'connected', just needs refresh)"); the two
        surfaces previously disagreed.
        """
        creds = self._load_from_vault(connector_id, account)
        if creds is None:
            return False
        return bool(creds.refresh) or not creds.is_expired

    async def inject_token(self, connector, account: str | None = None) -> bool:
        """Load *connector*'s stored token from the vault into the instance.

        Connectors hold their access token in-memory (``set_access_token``) but
        the persistent token lives in the vault. Before any search/fetch/act/
        health call, the dispatch layer must call this to hydrate the instance.
        Transparently refreshes an expired token. Returns True on success.
        """
        try:
            token = await self.get_access_token(connector.id, account)
            connector.set_access_token(token)
            return True
        except Exception as exc:
            # WARNING, not DEBUG: callers collapse this to a bare False and surface a
            # generic "token unavailable (reconnect)", so the reason the refresh failed
            # (revoked grant, network, provider config gone) would otherwise exist
            # nowhere the operator can see. This is the failure path for a credential
            # that IS stored — it means something broke, not that nothing was set up.
            logger.warning("Token injection for %s failed: %s", connector.id, exc)
            return False

    def list_connected_accounts(self) -> dict[str, str]:
        """Return {connector_id: account_email} for every stored connector token.

        Single vault read (one list call) instead of N per-connector lookups —
        used by the Deck connector-list endpoint to stay O(1) per connector.
        Includes expired tokens (still "connected", just needs refresh).
        """
        out: dict[str, str] = {}
        try:
            creds_list = self._vault.list(profile_id="connector")
        except Exception as exc:
            logger.debug("Vault list for connected accounts failed: %s", exc)
            return out

        for cred in creds_list or []:
            provider = getattr(cred, "provider", None)
            if not provider:
                continue
            meta = getattr(cred, "metadata", None) or {}
            out[provider] = meta.get("email") or ""
        return out

    # -- Token lifecycle ---------------------------------------------------

    async def authenticate(
        self,
        connector_id: str,
        *,
        interactive: bool = True,
        account: str | None = None,
    ) -> str:
        """
        Ensure a valid access token exists for *connector_id*.

        Resolution order:
        1. Vault lookup → if token present and valid, return it
        2. If expired, attempt refresh
        3. If refresh fails or no token, run PKCE flow
        4. Store new token in vault
        5. Return access_token string

        ``account`` (email) asks for a specific linked account, or — when nothing is
        linked under it yet — states which account the interactive flow must end on.

        Raises:
            ConnectorAuthError: If auth cannot be completed.
            ConnectorNotFoundError: If no provider config is registered.
        """
        config = self.get_provider_config(connector_id)
        if not config:
            raise ConnectorNotFoundError(connector_id)

        wanted = (account or "").strip().lower() or None
        slot: str | None = None
        if wanted:
            try:
                slot = self.resolve_account(connector_id, wanted)
            except ConnectorAuthError:
                slot = wanted  # not linked yet — the flow below will link it

        # 1. Check vault for existing credentials
        creds = self._load_from_vault(connector_id, slot)
        if creds and not creds.is_expired:
            logger.debug("Vault hit for %s — token valid", connector_id)
            return creds.access

        # 2. Attempt refresh
        if creds and creds.refresh:
            try:
                new_creds = await refresh_oauth_tokens(config, creds)
                self._save_to_vault(connector_id, new_creds, slot)
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

        got = (result.credentials.email or "").strip().lower()
        if wanted and got and got != wanted:
            raise ConnectorAuthError(
                connector_id,
                f"the browser signed in as {got}, not {wanted} — nothing was saved; retry and pick {wanted}",
            )
        self._save_to_vault(connector_id, result.credentials, self._slot_for_new(connector_id, result.credentials))
        logger.info("Authenticated %s via OAuth PKCE", connector_id)
        return result.credentials.access

    def _slot_for_new(self, connector_id: str, creds: OAuthCredentials) -> str | None:
        """Where a freshly linked account goes: the default slot unless it is already
        taken by a DIFFERENT account — then ``connector:<email>``, so the first account
        is never silently replaced by the second."""
        got = (creds.email or "").strip().lower()
        current = self._load_from_vault(connector_id)
        current_email = (current.email or "").strip().lower() if current else ""
        if current is None or not current_email or not got or current_email == got:
            return None
        return got

    async def get_access_token(self, connector_id: str, account: str | None = None) -> str:
        """
        Return a valid access token, refreshing transparently if needed.

        This is the method connectors call before every API request.
        """
        slot = self.resolve_account(connector_id, account)
        creds = self._load_from_vault(connector_id, slot)
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
            self._save_to_vault(connector_id, new_creds, slot)
            return new_creds.access
        except Exception as exc:
            logger.error("Token refresh failed for %s: %s", connector_id, exc)
            raise ConnectorAuthError(connector_id, f"Token refresh failed: {exc}") from exc

    async def revoke(self, connector_id: str, account: str | None = None) -> None:
        """Remove stored credentials for *connector_id* (one account, default when None)."""
        try:
            slot = self.resolve_account(connector_id, account) if account else None
            cred = self._vault.get(connector_id, profile_id=profile_for(slot))
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

    def _load_from_vault(self, connector_id: str, account: str | None = None) -> OAuthCredentials | None:
        """Load OAuth credentials from vault, or return None."""
        try:
            cred = self._vault.get(connector_id, profile_id=profile_for(account))
            if cred and cred.credential_type == CredentialType.OAUTH:
                return OAuthCredentials.from_dict(cred.data)
        except Exception as exc:
            logger.debug("Vault lookup for %s failed: %s", connector_id, exc)
        return None

    def _save_to_vault(self, connector_id: str, creds: OAuthCredentials, account: str | None = None) -> None:
        """Persist OAuth credentials to the vault.

        ``vault.add`` upserts by the unique ``(provider, profile)`` label — it updates the
        existing row **in place**, keeping the same credential id (see ``Vault.put``). So we must
        NOT remove-then-add: that opened a window where a failed ``add`` after a *committed*
        ``remove`` lost the refresh token entirely (a silent logout → full re-auth), and it churned
        a fresh id plus two audit entries on every hourly token refresh instead of one in-place
        update. A single ``add`` is atomic and preserves the id.
        """
        profile = profile_for(account)
        self._vault.add(
            provider=connector_id,
            credential_type=CredentialType.OAUTH.value,
            data=creds.to_dict(),
            profile_id=profile,
            label=f"{connector_id} connector OAuth" + (f" ({account})" if account else ""),
            metadata={
                "email": creds.email,
                "account_id": creds.account_id,
                "stored_at": time.time(),
            },
        )
        logger.debug("Saved credentials to vault for %s", connector_id)
