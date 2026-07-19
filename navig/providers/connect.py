"""
connect.py — the ONE connection flow engine every surface calls.

Deck, CLI, and onboarding never talk to drivers or the vault directly; they call
this orchestrator so behavior never diverges. It ties:

    template catalog → driver (native/local/pi/external/fake) → vault → store

`connect_provider` runs the right auth method (inline api-key / keyless local /
async OAuth/device via the bridge later), validates through the driver, and
persists a :class:`Connection` with honest capabilities + health + auth state.

Secrets only ever flow into the vault (as a ``secret_ref``); drivers receive a
transient resolver and never persist or log them.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from navig.providers.connection_types import (
    AuthState,
    Capability,
    Connection,
    ConnectionNotFound,
    ConnectionValidationError,
    Driver,
    HealthState,
    new_connection_id,
)
from navig.providers.connections import ConnectionStore, get_connection_store
from navig.providers.drivers.base import ProviderDriver
from navig.providers.drivers.native import NativeDriver
from navig.providers.endpoint_guard import assert_safe_endpoint

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Built-in template catalog (ported from craft BUILT_IN_CONNECTION_TEMPLATES)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConnectionTemplate:
    template_id: str
    name: str
    driver: Driver
    default_endpoint: str | None = None
    requires_key: bool = True
    requires_endpoint: bool = False
    # "api_key" (x-api-key / Bearer) or "oauth" (subscription login flow).
    auth_kind: str = "api_key"
    # Which core provider config to use (BUILTIN_PROVIDERS / PROVIDER_BASE_URLS).
    provider_id: str | None = None
    # API-key input hint (from craft's PI_PROVIDER_DISPLAY).
    key_placeholder: str | None = None
    # UI grouping: "subscription" | "api" | "local" | "external".
    group: str = "api"


def _byok(template_id: str, name: str, provider_id: str, placeholder: str) -> ConnectionTemplate:
    return ConnectionTemplate(
        template_id, name, Driver.NATIVE, provider_id=provider_id,
        key_placeholder=placeholder, group="api",
    )


CONNECTION_TEMPLATES: dict[str, ConnectionTemplate] = {
    # ── Subscriptions (OAuth login) ──────────────────────────────────────────
    # Claude Pro/Max — NAVIG runs inference, presenting as the official Claude
    # Code CLI (anthropic_oauth rules). OAuth bearer.
    "claude-max": ConnectionTemplate(
        "claude-max", "Claude (Pro/Max subscription)", Driver.NATIVE,
        auth_kind="oauth", provider_id="anthropic", group="subscription",
    ),
    # ChatGPT Plus/Pro — OAuth login → RFC-8693 exchange to an OpenAI key → routes
    # via the standard OpenAI path (provider_id=openai, plain api-key at inference).
    "chatgpt": ConnectionTemplate(
        "chatgpt", "ChatGPT (Plus/Pro subscription)", Driver.NATIVE,
        auth_kind="oauth", provider_id="openai", group="subscription",
    ),

    # ── BYOK API-key providers (core-native) ─────────────────────────────────
    "openai-api": _byok("openai-api", "OpenAI", "openai", "sk-..."),
    "anthropic-api": _byok("anthropic-api", "Anthropic", "anthropic", "sk-ant-..."),
    "google": _byok("google", "Google AI Studio", "google", "AIza..."),
    "openrouter": _byok("openrouter", "OpenRouter", "openrouter", "sk-or-..."),
    "groq": _byok("groq", "Groq", "groq", "gsk_..."),
    "mistral": _byok("mistral", "Mistral", "mistral", "Paste your key…"),
    "deepseek": _byok("deepseek", "DeepSeek", "deepseek", "sk-..."),
    "xai": _byok("xai", "xAI (Grok)", "xai", "xai-..."),
    "cerebras": _byok("cerebras", "Cerebras", "cerebras", "csk-..."),
    "nvidia": _byok("nvidia", "NVIDIA NIM", "nvidia", "nvapi-..."),
    "together": _byok("together", "Together", "together", "Paste your key…"),
    "cohere": _byok("cohere", "Cohere", "cohere", "Paste your key…"),

    # ── Custom / local ───────────────────────────────────────────────────────
    "openai-compat": ConnectionTemplate(
        "openai-compat", "OpenAI-Compatible Endpoint", Driver.NATIVE,
        requires_key=True, requires_endpoint=True, group="api",
        key_placeholder="API key (or blank for local)",
    ),
    "ollama": ConnectionTemplate(
        "ollama", "Ollama (Local)", Driver.LOCAL,
        default_endpoint="http://127.0.0.1:11434/v1", requires_key=False, group="local",
    ),
    "lmstudio": ConnectionTemplate(
        "lmstudio", "LM Studio (Local)", Driver.LOCAL,
        default_endpoint="http://127.0.0.1:1234/v1", requires_key=False, group="local",
    ),

    # ── External coding-agent subscriptions — delegated to the official runtime.
    "claude-code": ConnectionTemplate("claude-code", "Claude Code", Driver.EXTERNAL,
                                      requires_key=False, group="external"),
    "codex": ConnectionTemplate("codex", "Codex (ChatGPT)", Driver.EXTERNAL,
                                requires_key=False, group="external"),
    "copilot": ConnectionTemplate("copilot", "GitHub Copilot", Driver.EXTERNAL,
                                  requires_key=False, group="external"),
}


def list_templates() -> list[ConnectionTemplate]:
    return list(CONNECTION_TEMPLATES.values())


# ─────────────────────────────────────────────────────────────────────────────
# Driver resolution
# ─────────────────────────────────────────────────────────────────────────────


def get_driver(template: ConnectionTemplate, store: ConnectionStore) -> ProviderDriver:
    """Resolve a driver instance for a template. native/local run through the
    NativeDriver (OAuth-aware via auth_kind); external delegates to the official
    runtime; pi arrives via the bridge."""
    if template.driver in (Driver.NATIVE, Driver.LOCAL):
        # OAuth-BEARER presentation is Anthropic-specific (Claude subscription).
        # ChatGPT/Codex also use auth_kind="oauth" but their stored credential is a
        # real OpenAI api key (from the RFC-8693 exchange) → plain api-key path.
        oauth_bearer = template.auth_kind == "oauth" and template.provider_id in (None, "anthropic")
        pid = template.provider_id

        def _resolver(ref: str | None) -> str | None:
            val = store.read_secret(ref)
            if val:
                return val
            # Live fallback to the SHARED key store (env/vault/auth-profiles) so a
            # virtual "configured-elsewhere" connection always uses the current key.
            if pid and not oauth_bearer:
                key, _ = _resolve_auth(pid)
                return key
            return None

        return NativeDriver(
            secret_resolver=_resolver,
            secret_writer=store.update_secret,
            oauth=oauth_bearer,
            provider_id=template.provider_id,
        )
    if template.driver == Driver.EXTERNAL:
        from navig.providers.drivers.external import ExternalDriver

        return ExternalDriver()
    raise ConnectionValidationError(
        f"Driver '{template.driver.value}' is not yet available (later phase)."
    )


# ─────────────────────────────────────────────────────────────────────────────
# The flow
# ─────────────────────────────────────────────────────────────────────────────


async def connect_provider(
    template_id: str,
    *,
    name: str | None = None,
    api_key: str | None = None,
    endpoint: str | None = None,
    model: str | None = None,
    store: ConnectionStore | None = None,
    driver: ProviderDriver | None = None,
    existing_secret_ref: str | None = None,
) -> Connection:
    """Connect a provider end-to-end and persist the resulting Connection.

    `driver` may be injected (tests); otherwise it is resolved from the template.
    `existing_secret_ref` is used when the secret was already stored in the vault
    (e.g. by the OAuth flow) — skips the api-key store step.
    """
    store = store or get_connection_store()
    template = CONNECTION_TEMPLATES.get(template_id)
    if template is None:
        raise ConnectionValidationError(f"Unknown template: {template_id}")

    drv = driver or get_driver(template, store)
    endpoint = endpoint or template.default_endpoint

    if template.requires_endpoint and not endpoint:
        raise ConnectionValidationError("This connection requires an endpoint URL.")
    if template.requires_key and not api_key and not existing_secret_ref:
        raise ConnectionValidationError("This connection requires an API key.")

    # SSRF guard: validate any custom endpoint before the driver ever fetches it.
    # Local-runtime templates (Ollama/LM Studio) bind LOOPBACK — allow only that,
    # not arbitrary LAN hosts (else the validation probe becomes an internal
    # port-scanner). Everything else must be a safe public destination.
    if endpoint:
        assert_safe_endpoint(endpoint, allow_loopback=(template.driver == Driver.LOCAL))

    # Shared-BYOK: write the key to the SHARED store (visible to `navig ai` too)
    # and resolve it live — no separate copy, no import, always in sync.
    shared = _is_shared_byok(template) and bool(api_key) and not existing_secret_ref
    _prev_shared_key: str | None = None
    _prev_shared_source: str | None = None
    if shared:
        # Snapshot the existing key BEFORE overwriting, so a failed validation can
        # roll back (a bad `connect add` must not clobber a working `navig ai` key).
        # Keep the SOURCE too: an env-provided key was never in the profile store,
        # and "restoring" it there would persist an env secret to disk (see
        # _restore_shared_key).
        _prev_shared_key, _prev_shared_source = _resolve_auth(template.provider_id)  # type: ignore[arg-type]
        _save_shared_key(template.provider_id, api_key)  # type: ignore[arg-type]
        secret_ref: str | None = None
    else:
        # Store the secret so validation/inference resolve it from the vault.
        secret_ref = existing_secret_ref
        if api_key and not existing_secret_ref:
            secret_ref = store.store_secret(template_id, api_key, provider=template_id)

    # Validate through the driver (real 1-token completion / probe).
    #
    # The shared key has ALREADY been overwritten by this point, so anything that
    # raises from here — a network blip, a driver crash, a garbage health value —
    # must put the previous key back. Without this, a transport error during
    # `navig connect add` silently destroyed the user's working `navig ai` key and
    # left nothing in its place.
    try:
        result = await drv.validate(secret_ref=secret_ref, endpoint=endpoint, model=model)
        health = HealthState(result.health)
    except Exception:
        if shared:
            _restore_shared_key(
                template.provider_id,  # type: ignore[arg-type]
                _prev_shared_key,
                _prev_shared_source,
            )
        raise

    caps = set(getattr(drv, "advertised_capabilities", set()))
    if result.ok:
        auth_state = AuthState.CONNECTED
    elif health == HealthState.INVALID:
        # Genuine auth failure (401/403) — the credential is actually bad.
        auth_state = AuthState.NEEDS_REAUTH
        caps.discard(Capability.INFERENCE)  # not routable until it re-validates
    else:
        # Transient (429 rate-limit / timeout / unreachable) does NOT prove the
        # credential is bad — don't force a re-auth. Stay CONNECTED + routable
        # (the client retries with backoff); the health downgrade is recorded and
        # surfaced as "unhealthy" in the UI.
        auth_state = AuthState.CONNECTED

    # External (delegated) runtimes: we only know it's installed — never claim
    # NAVIG-side "connected" or inference capability. Honest "detected" state.
    if template.driver == Driver.EXTERNAL:
        auth_state = AuthState.DETECTED_EXTERNAL if result.ok else AuthState.NEEDS_REAUTH
        caps.discard(Capability.INFERENCE)

    # Shared-BYOK rollback: a genuinely-bad key (INVALID) just overwrote a
    # possibly-good previous key with no revision history — restore it so a failed
    # `navig connect add` doesn't break the provider for `navig ai` too.
    #
    # A transient failure (429 / timeout) deliberately does NOT roll back: it does
    # not prove the key is bad, the user explicitly asked to use it, and the virtual
    # connection resolves LIVE from the shared store — so rolling back while still
    # reporting CONNECTED would make the returned connection contradict the store.
    if shared and health == HealthState.INVALID:
        _restore_shared_key(
            template.provider_id,  # type: ignore[arg-type]
            _prev_shared_key,
            _prev_shared_source,
        )

    models = [m.id for m in result.models]

    # Shared-BYOK keys are NOT stored as a row — they appear as a virtual
    # connection resolved live from the shared store (always in sync). Fall back
    # to the template's representative models if validation returned none.
    if shared:
        if not models:
            try:
                from navig.providers.registry import get_provider

                man = get_provider(template.provider_id)  # type: ignore[arg-type]
                models = list(man.models)[:8] if man and man.models else []
            except Exception:  # noqa: BLE001
                models = []
        return _virtual_connection(
            template.provider_id,  # type: ignore[arg-type]
            template_id,
            name or template.name,
            models,
            health=health,
            auth_state=auth_state,
        )

    conn = Connection(
        connection_id=new_connection_id(),
        template_id=template_id,
        name=name or template.name,
        driver=template.driver,
        secret_ref=secret_ref,
        capabilities=caps,
        auth_state=auth_state,
        health_state=health,
        models=models,
        default_model=(model or (models[0] if models else None)),
        metadata={"endpoint": endpoint} if endpoint else {},
    )
    created = store.create(conn)
    if not result.ok:
        logger.info("connection %s stored as needs_reauth: %s",
                    created.connection_id, result.error_message)
    return created


# ─────────────────────────────────────────────────────────────────────────────
# Management surface (mirrors craft channels) — shared by deck/CLI/onboard
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# OAuth login (Claude Pro/Max subscription — token minting)
# ─────────────────────────────────────────────────────────────────────────────

# In-flight PKCE flows live in the ConnectionStore, keyed by an opaque handle.
#
# They used to live in a module-level dict, on the assumption that "begin + complete
# hit the same gateway process". True — until that process restarts. The daemon gets
# restarted often enough mid-setup (a config change, or `navig gateway start` right
# after a Lighthouse deploy — which NAVIG itself tells you to do) that a login begun
# in the UI would die between opening the browser and pasting the code, surfacing as
# the useless "expired or already used". The dict also never pruned, so an abandoned
# login kept its PKCE verifier in memory forever, long past the 10-minute expiry the
# flow itself enforces.
#
# Rows are single-use (deleted the moment they're read) and pruned past expiry. The
# stored code_verifier is worthless without the matching single-use authorization
# code, which only ever reaches the user's own browser.


def _oauth_kind(template: ConnectionTemplate) -> str:
    """Which OAuth provider drives this template's login."""
    if template.template_id == "claude-max" or template.provider_id == "anthropic":
        return "claude"
    if template.template_id == "chatgpt" or template.provider_id == "openai":
        return "codex"
    raise ConnectionValidationError(f"{template.template_id} has no known OAuth provider.")


def begin_oauth(
    template_id: str = "claude-max", *, store: ConnectionStore | None = None
) -> dict[str, Any]:
    """Start an OAuth login. Returns {handle, auth_url} — the surface opens the
    URL; the user authenticates and pastes back the code to :func:`complete_oauth`.

    The PKCE state is persisted (not held in memory) so the login survives a daemon
    restart between those two steps.
    """
    template = CONNECTION_TEMPLATES.get(template_id)
    if template is None or template.auth_kind != "oauth":
        raise ConnectionValidationError(f"{template_id} is not an OAuth template.")
    store = store or get_connection_store()
    kind = _oauth_kind(template)
    if kind == "claude":
        from navig.providers import claude_oauth as mod
    else:
        from navig.providers import codex_oauth as mod

    # Sweep abandoned logins on the way in — cheap, and keeps stale verifiers from
    # outliving the expiry the flow enforces.
    try:
        store.prune_pending_oauth(time.time() - mod.STATE_EXPIRY_S)
    except Exception as exc:  # noqa: BLE001 — never fail a login on housekeeping
        logger.debug("pending-oauth prune failed: %r", exc)

    url, flow = mod.build_authorize_url()
    handle = new_connection_id()
    store.put_pending_oauth(
        handle,
        kind=kind,
        template_id=template_id,
        state=flow.state,
        code_verifier=flow.code_verifier,
        created_at=flow.created_at,
    )
    # `state` is returned so callers can match the redirect (the codex loopback
    # capture needs it) without reaching into private state. It is NOT a secret —
    # it's a CSRF nonce that already travels in `auth_url`'s query string. The
    # code_verifier, which IS secret, never leaves the host.
    return {
        "handle": handle,
        "auth_url": url,
        "template_id": template_id,
        "kind": kind,
        "state": flow.state,
    }


# Alias for clarity at call sites (CLI/deck both accept any oauth template).
begin_codex_oauth = begin_oauth


async def complete_oauth(
    handle: str,
    code: str,
    *,
    name: str | None = None,
    store: ConnectionStore | None = None,
) -> Connection:
    """Exchange the pasted code for credentials, store them in the vault, and
    create a routable subscription connection (Claude or ChatGPT)."""
    import json as _json

    store = store or get_connection_store()
    pending = store.take_pending_oauth(handle)  # single-use — consumed on read
    if pending is None:
        raise ConnectionValidationError("No pending login for that handle (expired or already used).")
    kind, template_id = pending["kind"], pending["template_id"]

    # Rebuild the flow the exchange expects. created_at is carried through, so the
    # 10-minute expiry (and the CSRF state check) are enforced exactly as before —
    # persisting the flow must not quietly extend its lifetime.
    if kind == "claude":
        from navig.providers.claude_oauth import ClaudeOAuthFlow

        flow: Any = ClaudeOAuthFlow(
            state=pending["state"],
            code_verifier=pending["code_verifier"],
            created_at=pending["created_at"],
        )
    else:
        from navig.providers.codex_oauth import CodexOAuthFlow

        flow = CodexOAuthFlow(
            state=pending["state"],
            code_verifier=pending["code_verifier"],
            created_at=pending["created_at"],
        )

    def _return_handle_for_retry() -> None:
        """Put the login back so a typo'd code — or a network blip — doesn't cost the
        user the whole browser round-trip.

        Single-use is unaffected: a SUCCESSFUL exchange still consumes the handle
        exactly once (we only restore when the exchange itself failed), and an
        EXPIRED flow is never restored. The handle alone is useless — redeeming it
        still requires a valid, single-use authorization code from the provider.
        """
        if flow.expired:
            return
        try:
            store.put_pending_oauth(
                handle,
                kind=kind,
                template_id=template_id,
                state=flow.state,
                code_verifier=flow.code_verifier,
                created_at=flow.created_at,
            )
        except Exception as exc:  # noqa: BLE001 — never mask the real failure
            logger.debug("could not restore the pending login for retry: %r", exc)

    if kind == "claude":
        from navig.providers import claude_oauth

        try:
            tokens = await claude_oauth.exchange_code(code, flow)
        except Exception:
            _return_handle_for_retry()
            raise
        if not tokens.get("access_token"):
            raise ConnectionValidationError("Token exchange returned no access token.")
        bundle = _json.dumps(tokens)
        account_uuid = tokens.get("account_uuid")
        account_email = tokens.get("account_email")
        meta_id = {k: v for k, v in {
            "account_uuid": account_uuid,
            "account_email": account_email,
            "organization": tokens.get("organization"),
        }.items() if v}
        # Label by email so multiple Claude subscriptions are distinguishable.
        display = name or (f"Claude — {account_email}" if account_email else None)

        existing = [c for c in store.list() if c.template_id == "claude-max"]
        # Re-login updates the SAME account (matched by uuid); DIFFERENT accounts
        # stay as separate connections (never blindly delete another account).
        match = None
        if account_uuid:
            match = next((c for c in existing
                          if (c.metadata or {}).get("account_uuid") == account_uuid), None)
        elif name is None and len(existing) == 1:
            # No account id and no explicit name → single-account convenience.
            match = existing[0]

        if match is not None:
            if match.secret_ref:
                store.update_secret(match.secret_ref, bundle)
            else:
                match.secret_ref = store.store_secret("claude-max", bundle, provider="claude-max")
            if display:
                match.name = display
            if meta_id:
                match.metadata = {**(match.metadata or {}), **meta_id}
            store.update(match, expected_revision=match.revision)
            # Re-login replaced the token bundle — drop any cached resolution so
            # dispatch never serves the pre-login bearer for the TTL window.
            from navig.providers.inference import invalidate_credential_cache

            invalidate_credential_cache("anthropic")
            return await revalidate(match.connection_id, store=store)

        # New account (or first login) → create; leave other accounts untouched.
        secret_ref = store.store_secret("claude-max", bundle, provider="claude-max")
        conn = await connect_provider("claude-max", name=display, store=store,
                                      existing_secret_ref=secret_ref)
        if meta_id:
            fresh = store.get(conn.connection_id)
            fresh.metadata = {**(fresh.metadata or {}), **meta_id}
            store.update(fresh, expected_revision=fresh.revision)
            return store.get(conn.connection_id)
        return conn

    # ChatGPT/Codex → exchange the id_token for a real OpenAI API key, then store
    # it as a standard openai connection (routable via the OpenAI native path).
    from navig.providers import codex_oauth

    try:
        tokens = await codex_oauth.exchange_code(code, flow)
    except Exception:
        _return_handle_for_retry()
        raise
    id_token = tokens.get("id_token")
    if not id_token:
        raise ConnectionValidationError("ChatGPT login returned no id_token.")
    api_key = await codex_oauth.exchange_id_token_for_api_key(id_token)
    secret_ref = store.store_secret("chatgpt", api_key, provider="chatgpt")
    return await connect_provider("chatgpt", name=name, store=store,
                                  existing_secret_ref=secret_ref)


async def complete_codex_oauth(handle: str, code: str, *, name: str | None = None,
                               store: ConnectionStore | None = None) -> Connection:
    """Explicit alias for the ChatGPT/Codex completion (same dispatch)."""
    return await complete_oauth(handle, code, name=name, store=store)


def detect_external() -> list[dict[str, Any]]:
    """Detect installed official coding-agent runtimes (Claude Code/Codex/Copilot)
    without reading credentials. Non-secret descriptors for "Detected — reuse" UI."""
    from navig.providers.drivers.external import detect_external_runtimes

    return detect_external_runtimes()


# ─────────────────────────────────────────────────────────────────────────────
# Import existing keys (bridge the older env/vault/config provider config)
# ─────────────────────────────────────────────────────────────────────────────


def _api_templates_by_provider() -> dict[str, str]:
    return {
        t.provider_id: t.template_id
        for t in CONNECTION_TEMPLATES.values()
        if t.provider_id and t.group == "api"
    }


VIRTUAL_PREFIX = "configured:"


def _is_shared_byok(template: ConnectionTemplate) -> bool:
    """A plain API-key provider whose key lives in the SHARED store (env/vault/
    auth-profiles) that `navig ai` also reads — so the two stay in sync."""
    return (
        template.group == "api"
        and bool(template.provider_id)
        and not template.requires_endpoint
        and template.auth_kind != "oauth"
    )


def _save_shared_key(provider_id: str, api_key: str) -> None:
    from navig.providers import AuthProfileManager

    auth = AuthProfileManager()
    auth.add_api_key(provider=provider_id, api_key=api_key)
    auth.save()


def _remove_shared_key(provider_id: str) -> None:
    try:
        from navig.providers import AuthProfileManager

        auth = AuthProfileManager()
        auth.remove_profile(provider_id)
        auth.save()
    except Exception:  # noqa: BLE001
        pass


def _restore_shared_key(
    provider_id: str, prev_key: str | None, prev_source: str | None
) -> None:
    """Undo a shared-BYOK write, putting the provider back exactly as it was.

    Env-awareness matters here, and mirrors :func:`disconnect`: ``resolve_auth``
    checks auth *profiles* first and only then the *environment*. So a key that
    resolved from ``$OPENAI_API_KEY`` was never in the profile store — writing it
    back there would **persist an environment secret to disk** and permanently
    shadow the env var (profiles win). In that case, and when there was no previous
    key at all, the right undo is to drop the profile entry we just created: env
    resolution then works exactly as it did before we touched anything.

    Never raises — a failed rollback must not mask the outcome that triggered it.
    """
    try:
        from_env = bool(prev_key) and str(prev_source or "").lower().startswith("env")
        if prev_key and not from_env:
            _save_shared_key(provider_id, prev_key)
        else:
            _remove_shared_key(provider_id)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "could not roll back the shared key for %r (%s) — `navig ai` may now be "
            "using an unverified key; re-run `navig connect add` or set the key again",
            provider_id, exc,
        )


def _virtual_connection(
    provider_id: str,
    template_id: str,
    name: str,
    models: list[str],
    *,
    health: HealthState = HealthState.HEALTHY,
    auth_state: AuthState = AuthState.CONNECTED,
    source: str = "configured",
) -> Connection:
    """A connection backed by the SHARED key store (not a stored row). Its key is
    resolved live, so it's always in sync with whatever's configured."""
    caps = {Capability.AUTH, Capability.MODEL_DISCOVERY}
    if auth_state == AuthState.CONNECTED:
        caps.add(Capability.INFERENCE)
    return Connection(
        connection_id=VIRTUAL_PREFIX + provider_id,
        template_id=template_id,
        name=name,
        driver=Driver.NATIVE,
        secret_ref=None,
        capabilities=caps,
        auth_state=auth_state,
        health_state=health,
        models=models,
        default_model=(models[0] if models else None),
        metadata={"source": source, "provider_id": provider_id, "virtual": True},
    )


def _resolve_auth(provider_id: str) -> tuple[str | None, str | None]:
    """Resolve a provider's configured key + source via the same path `navig ai
    providers` uses (env / vault / auth profiles). Returns (key, source)."""
    try:
        from navig.providers import AuthProfileManager

        key, source = AuthProfileManager().resolve_auth(provider_id)
    except Exception:  # noqa: BLE001
        return None, None
    if key and hasattr(key, "get_secret_value"):
        key = key.get_secret_value()
    return (str(key) if key else None), source


def detect_configured(store: ConnectionStore | None = None) -> list[dict[str, Any]]:
    """Existing API-key providers configured elsewhere (env / vault / auth
    profiles — the same source as ``navig ai providers``) that map to a BYOK
    template and aren't already a Connection. Non-secret descriptors — importable."""
    store = store or get_connection_store()
    existing = {c.template_id for c in store.list()}
    out: list[dict[str, Any]] = []
    for provider_id, tid in _api_templates_by_provider().items():
        if tid in existing:
            continue
        key, source = _resolve_auth(provider_id)
        if not key:
            continue
        try:
            from navig.providers.registry import get_provider

            manifest = get_provider(provider_id)
        except Exception:  # noqa: BLE001
            manifest = None
        out.append({
            "provider_id": provider_id,
            "template_id": tid,
            "display_name": (manifest.display_name if manifest else provider_id),
            "models": (list(manifest.models)[:8] if manifest and manifest.models else []),
            "source": source or "configured",
        })
    return out


def _virtual_connections(store: ConnectionStore) -> list[Connection]:
    """Configured-elsewhere providers (env/vault/auth-profiles) as live virtual
    connections — auto-synced, never imported."""
    return [
        _virtual_connection(d["provider_id"], d["template_id"], d["display_name"],
                            d["models"], source=d.get("source", "configured"))
        for d in detect_configured(store)
    ]


def _all_connections(store: ConnectionStore) -> list[Connection]:
    """Stored connections (OAuth / local / custom endpoint) + virtual ones."""
    return list(store.list()) + _virtual_connections(store)


def _find(store: ConnectionStore, connection_id: str) -> Connection | None:
    for c in _all_connections(store):
        if c.connection_id == connection_id:
            return c
    return None


def _default_id(store: ConnectionStore, conns: list[Connection]) -> str | None:
    explicit = store.get_setting("default_connection_id")
    ids = [c.connection_id for c in conns]
    if explicit and explicit in ids:
        return explicit
    return ids[0] if ids else None


def list_connections(store: ConnectionStore | None = None) -> list[dict[str, Any]]:
    store = store or get_connection_store()
    conns = _all_connections(store)
    default_id = _default_id(store, conns)
    out: list[dict[str, Any]] = []
    for c in conns:
        d = c.to_public_dict()
        d["is_default"] = (c.connection_id == default_id)
        out.append(d)
    return out


def get_connection(connection_id: str, store: ConnectionStore | None = None) -> dict[str, Any]:
    store = store or get_connection_store()
    c = _find(store, connection_id)
    if c is None:
        raise ConnectionNotFound(f"No connection: {connection_id}")
    return c.to_public_dict()


def disconnect(connection_id: str, store: ConnectionStore | None = None) -> bool:
    """Remove a connection. For a virtual (configured-elsewhere) connection this
    removes the shared key — so it disappears from `navig ai` too (true sync).

    Raises :class:`ConnectionValidationError` if the virtual key can't actually be
    removed (it's provided by an environment variable, or a vault entry we won't
    silently delete) — rather than reporting a false success and having the
    connection reappear on the next list.
    """
    from navig.providers.inference import invalidate_credential_cache

    store = store or get_connection_store()
    if connection_id.startswith(VIRTUAL_PREFIX):
        provider_id = connection_id[len(VIRTUAL_PREFIX):]
        # An environment variable can't be removed by NAVIG — refuse honestly
        # instead of reporting success and having the connection reappear on the
        # next list (removing the auth-profile entry is a no-op for an env key).
        _key, source = _resolve_auth(provider_id)
        if _key and str(source or "").lower().startswith("env"):
            raise ConnectionValidationError(
                f"'{provider_id}' is provided by an environment variable ({source}) — "
                f"unset it in your shell/service to disconnect it; NAVIG can't remove "
                f"an env var.",
                code="env_backed",
            )
        _remove_shared_key(provider_id)  # removes an auth-profile / vault entry
        if store.get_setting("default_connection_id") == connection_id:
            store.set_setting("default_connection_id", None)
        invalidate_credential_cache(provider_id)  # dispatch must not reuse it
        return True
    removed = store.delete(connection_id)
    if removed:
        invalidate_credential_cache()  # dispatch must re-resolve immediately
    return removed


async def revalidate(connection_id: str, store: ConnectionStore | None = None) -> Connection:
    """Re-run a connection's driver validation and persist the fresh
    auth/health/capability state. Used by `navig connect test` and to heal a
    connection wrongly marked ``needs_reauth`` (e.g. after a secret-read fix).

    Virtual "configured-elsewhere" connections are validated too, but nothing is
    persisted: they have no stored row (their key is resolved live from the shared
    store), so the fresh state is simply returned.
    """
    store = store or get_connection_store()
    if connection_id.startswith(VIRTUAL_PREFIX):
        c = _find(store, connection_id)
        if c is None:
            raise ConnectionNotFound(f"No connection: {connection_id}")

        # A virtual connection is SYNTHESISED as connected/healthy purely because a
        # key string exists somewhere (env/vault/auth-profile) — it is never proven.
        # Returning that record unchanged made `navig connect test <id>` a
        # guaranteed green light even for a garbage key: the one command whose whole
        # job is to tell you the truth. "Needs no persistence" (true — there's no
        # row) had been conflated with "needs no validation" (false).
        template = CONNECTION_TEMPLATES.get(c.template_id)
        provider_id = (c.metadata or {}).get("provider_id")
        if template is None or not provider_id:
            return c

        drv = get_driver(template, store)
        # secret_ref=None → get_driver's resolver falls back to the SHARED key store,
        # so this probes whatever key is configured right now.
        result = await drv.validate(
            secret_ref=None,
            endpoint=(c.metadata or {}).get("endpoint"),
            model=c.default_model,
        )
        health = HealthState(result.health)
        if result.ok:
            auth_state = AuthState.CONNECTED
        elif health == HealthState.INVALID:
            auth_state = AuthState.NEEDS_REAUTH  # drops INFERENCE → not routable
        else:
            # Transient (429/timeout) doesn't prove the key is bad — stay routable,
            # surface the health downgrade. Same rule as connect_provider.
            auth_state = AuthState.CONNECTED

        return _virtual_connection(
            str(provider_id),
            c.template_id,
            c.name,
            [m.id for m in result.models] or list(c.models),
            health=health,
            auth_state=auth_state,
            source=(c.metadata or {}).get("source", "configured"),
        )

    conn = store.get(connection_id)  # raises ConnectionNotFound
    template = CONNECTION_TEMPLATES.get(conn.template_id)
    if template is None:
        raise ConnectionValidationError(f"Unknown template: {conn.template_id}")

    drv = get_driver(template, store)
    endpoint = (conn.metadata or {}).get("endpoint")
    result = await drv.validate(secret_ref=conn.secret_ref, endpoint=endpoint, model=conn.default_model)

    caps = set(getattr(drv, "advertised_capabilities", set()))
    health = HealthState(result.health)
    if template.driver == Driver.EXTERNAL:
        conn.auth_state = AuthState.DETECTED_EXTERNAL if result.ok else AuthState.NEEDS_REAUTH
        caps.discard(Capability.INFERENCE)
    elif result.ok:
        conn.auth_state = AuthState.CONNECTED
    elif health == HealthState.INVALID:
        # Real auth failure only — re-auth needed.
        conn.auth_state = AuthState.NEEDS_REAUTH
        caps.discard(Capability.INFERENCE)
    else:
        # Transient (429/timeout/unreachable): keep the previously-valid connection
        # CONNECTED + routable rather than falsely demoting it to needs_reauth.
        conn.auth_state = AuthState.CONNECTED

    conn.health_state = health
    conn.capabilities = caps
    if result.models:
        conn.models = [m.id for m in result.models]
        if not conn.default_model and conn.models:
            conn.default_model = conn.models[0]
    return store.update(conn, expected_revision=conn.revision)


def set_default(connection_id: str, store: ConnectionStore | None = None) -> None:
    from navig.providers.inference import invalidate_credential_cache

    store = store or get_connection_store()
    if _find(store, connection_id) is None:
        raise ConnectionNotFound(f"No connection: {connection_id}")
    store.set_setting("default_connection_id", connection_id)
    if not connection_id.startswith(VIRTUAL_PREFIX):
        try:
            store.set_default(connection_id)  # keep the stored flag in sync
        except Exception:  # noqa: BLE001
            pass
    invalidate_credential_cache()  # the default account changed — re-resolve


def set_workspace_default(workspace_id: str, connection_id: str,
                          store: ConnectionStore | None = None) -> None:
    store = store or get_connection_store()
    if _find(store, connection_id) is None:
        raise ConnectionNotFound(f"No connection: {connection_id}")
    store.set_setting(f"ws_default:{workspace_id}", connection_id)


def resolve_default(workspace_id: str | None = None,
                    store: ConnectionStore | None = None) -> dict[str, Any] | None:
    store = store or get_connection_store()
    conns = _all_connections(store)
    by_id = {c.connection_id: c for c in conns}
    if workspace_id:
        wid = store.get_setting(f"ws_default:{workspace_id}")
        if wid and wid in by_id:
            return by_id[wid].to_public_dict()
    did = _default_id(store, conns)
    return by_id[did].to_public_dict() if did and did in by_id else None


def resolve_for(
    *,
    workspace_id: str | None = None,
    connection_id: str | None = None,
    store: ConnectionStore | None = None,
) -> dict[str, Any] | None:
    """Routing resolver with an explicit fallback chain (Phase 5):
    explicit ``connection_id`` → per-workspace default → global default. Used by
    per-agent / per-task routing; only ever returns a routable connection's data
    when one exists, else the best available."""
    store = store or get_connection_store()
    if connection_id:
        c = _find(store, connection_id)
        if c is not None:
            return c.to_public_dict()
        # fall through to defaults
    return resolve_default(workspace_id, store)


def diagnostics_report(
    workspace_id: str | None = None,
    store: ConnectionStore | None = None,
) -> dict[str, Any]:
    """An exportable support report with **all secret material redacted**.

    Connections carry only a boolean 'has secret' marker — never the secret_ref
    label or value. Safe to paste into a bug report. (Verified by tests.)"""
    store = store or get_connection_store()
    all_conns = _all_connections(store)
    default_id = _default_id(store, all_conns)
    conns: list[dict[str, Any]] = []
    for c in all_conns:
        d = c.to_public_dict()
        d["secret_ref"] = None
        d["has_secret"] = bool(c.secret_ref) or bool(c.metadata.get("virtual"))
        d["is_default"] = (c.connection_id == default_id)
        # endpoints can carry private hostnames — keep only scheme+host shape
        meta = d.get("metadata") or {}
        if isinstance(meta, dict) and meta.get("endpoint"):
            try:
                from urllib.parse import urlparse

                u = urlparse(str(meta["endpoint"]))
                meta = {**meta, "endpoint": f"{u.scheme}://{u.hostname}"}
                d["metadata"] = meta
            except Exception:  # noqa: BLE001
                pass
        conns.append(d)
    ws_default = resolve_default(workspace_id, store) if workspace_id else None

    # How many OAuth logins are mid-flight. A COUNT only — never the handle, the
    # state, or the verifier: this report is meant to be pasteable into a bug report.
    try:
        pending_logins = store.count_pending_oauth()
    except Exception:  # noqa: BLE001 — diagnostics must never be the thing that breaks
        pending_logins = 0

    return {
        "connections": conns,
        "default_connection_id": default_id,
        "resolved_for_workspace": (ws_default["connection_id"] if ws_default else None),
        "detected_external": detect_external(),
        "pending_logins": pending_logins,
        "bridge": {"protocol": "1.0", "pi_pinned": "0.79.9"},
    }
