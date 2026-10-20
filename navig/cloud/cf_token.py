"""The user's Cloudflare credential — one resolver for everything that runs on THEIR account.

Lighthouse (the edge Worker), the deck deploy and the decision model (Workers AI) all act
on the account the user connected with ``navig lighthouse login`` / ``deploy``. They share
this resolver so a credential stored once — an API token, or an OAuth bundle that
refreshes itself — works for every one of them.

These functions used to live in :mod:`navig.commands.lighthouse`, which still re-exports
them under the same names; they moved here so code outside ``commands/`` (the agent's
decision layer) can read the credential without importing a CLI module.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def resolve_cf_token(explicit: str = "", *, caller: str = "lighthouse.deploy") -> str:
    """CF token resolution order: explicit → $CLOUDFLARE_API_TOKEN → vault.

    Pure read — persistence into the vault happens once in
    :func:`navig.commands.lighthouse.run_lighthouse_deploy` (the single deploy path), so a
    token sourced from a flag or the environment still ends up vaulted after the first
    deploy. *caller* labels the vault read in the access log.
    """
    if explicit and explicit.strip():
        return explicit.strip()
    env = (os.environ.get("CLOUDFLARE_API_TOKEN") or "").strip()
    if env:
        return env
    try:
        from navig.vault import get_vault

        cred = get_vault().get("cloudflare", caller=caller)
        if cred is not None:
            def _field(name: str) -> str:
                try:
                    return str(cred.get_secret(name) or "").strip()
                except Exception:  # noqa: BLE001
                    return ""

            tok = _field("token") or _field("api_key")
            # OAuth credentials auto-refresh when expired so redeploys stay unattended.
            if _field("auth") == "oauth":
                from navig.cloud import cf_oauth

                refresh_tok = _field("refresh_token")
                if refresh_tok and cf_oauth.is_expired(_field("expires_at")):
                    try:
                        bundle = cf_oauth.refresh(refresh_tok)
                        persist_cf_oauth(bundle)
                        return bundle.access_token
                    except cf_oauth.OAuthError as exc:  # noqa: BLE001
                        logger.debug("cloudflare oauth refresh failed: %s", exc)
            if tok:
                return tok
    except Exception:  # noqa: BLE001
        pass
    return ""


def resolve_cf_api_token() -> str:
    """Return the stored Cloudflare credential **only if it's a real API token**.

    wrangler validates ``CLOUDFLARE_API_TOKEN`` via an API-token-only endpoint
    and **rejects OAuth access tokens** (from ``navig lighthouse login``) — feeding
    it one breaks the deploy *and* shadows wrangler's own login. So for tooling that
    shells out to wrangler (the deck deploy), return "" when the vaulted credential
    is OAuth, letting wrangler use its own auth instead.
    """
    env = (os.environ.get("CLOUDFLARE_API_TOKEN") or "").strip()
    if env:
        return env
    try:
        from navig.vault import get_vault

        cred = get_vault().get("cloudflare", caller="miniapp.deploy")
        if cred is None:
            return ""

        def _field(name: str) -> str:
            try:
                return str(cred.get_secret(name) or "").strip()
            except Exception:  # noqa: BLE001
                return ""

        if _field("auth") == "oauth":
            return ""  # wrangler can't use OAuth tokens
        return _field("token") or _field("api_key")
    except Exception:  # noqa: BLE001
        return ""


def credential_kind() -> str:
    """``"env"`` · ``"oauth"`` · ``"token"`` · ``""`` — WHICH credential a resolve would use.

    Lets a status view say *how* to grant a missing permission: an OAuth login is fixed by
    re-running ``navig lighthouse login``, an API token by editing it in the dashboard.
    Never returns the secret itself.
    """
    if (os.environ.get("CLOUDFLARE_API_TOKEN") or "").strip():
        return "env"
    try:
        from navig.vault import get_vault

        cred = get_vault().get("cloudflare", caller="cf.kind")
        if cred is None:
            return ""
        try:
            auth = str(cred.get_secret("auth") or "").strip()
        except Exception:  # noqa: BLE001
            auth = ""
        return "oauth" if auth == "oauth" else "token"
    except Exception:  # noqa: BLE001
        return ""


def persist_cf_token(token: str) -> bool:
    """Store the Cloudflare API token in the vault (best-effort).

    Single source of truth for "the token must live in the vault" — called from
    token resolution and from the deploy path, so the CLI and the onboarding
    wizard both end up with the credential vaulted. Returns True if stored.
    """
    token = (token or "").strip()
    if not token:
        return False
    return _vault_put_cloudflare({"token": token})


def persist_cf_oauth(bundle) -> bool:
    """Store a Cloudflare OAuth :class:`TokenBundle` (access + refresh + expiry)."""
    return _vault_put_cloudflare(bundle.as_vault_data())


def _vault_put_cloudflare(data: dict) -> bool:
    """Upsert the ``cloudflare`` vault credential (update if present, else add)."""
    try:
        from navig.vault import get_vault

        vault = get_vault()
        if vault is None:
            return False
        existing = vault.get("cloudflare", caller="lighthouse.persist")
        if existing is not None:
            vault.update(existing.id, data=data)
        else:
            vault.add(
                provider="cloudflare",
                credential_type="token",
                data=data,
                profile_id="default",
                label="Cloudflare API Token",
            )
        return True
    except Exception:  # noqa: BLE001 — vault store is best-effort
        logger.debug("cloudflare token vault store failed", exc_info=True)
        return False
