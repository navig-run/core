"""
inference.py — run a completion THROUGH a NAVIG AI Connection.

The connection service (:mod:`navig.providers.connect`) is the control plane —
it authenticates and stores connections (claude-max OAuth, API-key providers,
local runtimes, external coding-agents). This module is the thin data-plane
bridge that lets any surface (``navig ask``, the gateway, agents) actually *run*
inference through the connection the operator selected, instead of a separate
OpenRouter/host code path that ignores the connections entirely.

Routing:
  * ``model_spec`` = ``"provider:model"`` → the routable connection whose provider
    matches (e.g. ``anthropic:claude-opus-4-8`` → the ``claude-max`` connection),
    with the model overridden.
  * ``model_spec`` = bare model / ``None`` → the default connection's model.
  * ``connection_id`` → that exact connection.

Credential handling mirrors the drivers exactly:
  * ``claude-max`` (Claude Pro/Max subscription) → OAuth bearer with the official
    Claude Code CLI presentation (auto-refreshed), never ``x-api-key``.
  * API-key providers / virtual "configured-elsewhere" connections → the shared
    key (vault / auth-profiles), resolved live.
  * ``codex`` / ``copilot`` / ``claude-code`` (delegated external runtimes) →
    :class:`ExternalNotRoutable`, an honest error rather than a silent fallback.
"""

from __future__ import annotations

import concurrent.futures as _cf
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# Delegated coding-agent runtimes: connected for reference, not routable by NAVIG.
_EXTERNAL_TEMPLATES = frozenset({"claude-code", "codex", "copilot"})


class NoRoutableConnection(Exception):
    """No connected/routable AI connection is available for this request."""


class ExternalNotRoutable(Exception):
    """The selected connection is a delegated external runtime (Claude Code CLI,
    Codex, Copilot) that NAVIG cannot run inference through yet."""

    def __init__(self, label: str):
        super().__init__(
            f"'{label}' is a delegated external runtime — NAVIG can't route inference "
            f"through it yet. Use a subscription login (`navig connect login claude-max` "
            f"or `chatgpt`) or an API-key connection instead."
        )
        self.label = label


# ─────────────────────────────────────────────────────────────────────────────
# Route resolution
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Route:
    connection: dict[str, Any]   # public connection dict (never carries a secret)
    model: str | None            # resolved model id


def _infer_provider(model: str) -> str | None:
    """Best-effort provider from a bare model id (mirrors generate._parse_model_spec)."""
    low = model.lower()
    if low.startswith("gpt-") or low.startswith("o1"):
        return "openai"
    if low.startswith("claude"):
        return "anthropic"
    if "/" in model:  # org/model → OpenRouter-style
        return "openrouter"
    if "grok" in low:
        return "xai"
    if "deepseek" in low:
        return "deepseek"
    return None  # unknown → no hint


def _parse_spec(model_spec: str | None) -> tuple[str | None, str | None]:
    """``"anthropic:claude-opus-4-8"`` → ``("anthropic", "claude-opus-4-8")``.

    OpenRouter-style ``vendor/model`` ids contain no colon, so splitting on the
    first ``:`` is unambiguous. For a bare model we INFER the provider (else a
    bare ``gpt-4o`` would be forced onto the default connection's provider — e.g.
    claude-max → 404). An unknown bare model returns ``(None, model)``.
    """
    if not model_spec:
        return None, None
    if ":" in model_spec:
        prov, _, model = model_spec.partition(":")
        return (prov.strip() or None), (model.strip() or None)
    m = model_spec.strip()
    if not m:
        return None, None
    return _infer_provider(m), m


def _provider_of(conn: dict[str, Any]) -> str | None:
    """The provider id backing a connection (anthropic/openai/xai/…)."""
    from navig.providers.connect import CONNECTION_TEMPLATES

    tmpl = CONNECTION_TEMPLATES.get(conn.get("template_id") or "")
    if tmpl and tmpl.provider_id:
        return tmpl.provider_id
    meta = conn.get("metadata") or {}
    return meta.get("provider_id")  # virtual "configured:<pid>" connections


def _is_external(conn: dict[str, Any]) -> bool:
    return (
        conn.get("template_id") in _EXTERNAL_TEMPLATES
        or conn.get("driver") == "external"
    )


def resolve_route(
    model_spec: str | None = None,
    connection_id: str | None = None,
    *,
    workspace_id: str | None = None,
) -> Route | None:
    """Pick the connection + model to run this request. Returns ``None`` when no
    connection is available (caller falls back to the legacy path)."""
    from navig.providers.connect import list_connections

    conns = list_connections()
    if not conns:
        return None
    prov, model = _parse_spec(model_spec)

    chosen: dict[str, Any] | None = None
    if connection_id:
        chosen = next((c for c in conns if c["connection_id"] == connection_id), None)
    elif prov:
        cands = [c for c in conns if _provider_of(c) == prov]
        routable = [c for c in cands if c.get("is_routable")]
        pool = routable or cands
        chosen = next((c for c in pool if c.get("is_default")), None) or (pool[0] if pool else None)
    else:
        # per-workspace default → global default → first
        chosen = next((c for c in conns if c.get("is_default")), None) or conns[0]

    if chosen is None:
        return None
    resolved_model = model or chosen.get("default_model") or (chosen.get("models") or [None])[0]
    return Route(connection=chosen, model=resolved_model)


def has_routable_connection(
    model_spec: str | None = None, connection_id: str | None = None
) -> bool:
    """True when :func:`complete_via_connection` can act on this request — either
    a genuinely routable connection, or a selected external one (so the caller
    surfaces :class:`ExternalNotRoutable` instead of silently using a legacy path)."""
    route = resolve_route(model_spec, connection_id)
    if route is None:
        return False
    return bool(route.connection.get("is_routable")) or _is_external(route.connection)


# ─────────────────────────────────────────────────────────────────────────────
# Credential + provider-config resolution
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_api_key(conn: dict[str, Any], provider_id: str | None) -> str | None:
    from navig.providers import connect as _connect
    from navig.providers.connections import get_connection_store

    ref = conn.get("secret_ref")
    if ref:
        key = get_connection_store().read_secret(ref)
        if key:
            return key
    if provider_id:
        key, _ = _connect._resolve_auth(provider_id)  # shared store (env/vault/profiles)
        return key
    return None


async def _oauth_bearer(secret_ref: str | None) -> str | None:
    """Resolve the Claude subscription OAuth bearer for a connection's secret_ref,
    auto-refreshing a token bundle within a 5-minute buffer (reuses the driver)."""
    from navig.providers.connections import get_connection_store
    from navig.providers.drivers.native import NativeDriver

    store = get_connection_store()
    drv = NativeDriver(
        secret_resolver=store.read_secret,
        secret_writer=store.update_secret,
        oauth=True,
        provider_id="anthropic",
    )
    return await drv._oauth_access_token(secret_ref)


def has_connection_for_provider(provider_id: str | None) -> bool:
    """True when a routable connection backs ``provider_id`` (e.g. claude-max for
    ``anthropic``). A LIGHT check — inspects the connection list only, never
    resolves/refreshes a token — so it is cheap enough for routing decisions."""
    if not provider_id:
        return False
    try:
        from navig.providers.connect import list_connections

        conns = list_connections()
    except Exception:  # noqa: BLE001
        return False
    return any(_provider_of(c) == provider_id and c.get("is_routable") for c in conns)


# ── Dispatch-credential single-flight (parallel fan-out hardening) ──────────
#
# Council/formation runs dispatch N agent calls at once from a ThreadPoolExecutor,
# and each used to re-run the FULL credential resolution (connection list → vault
# read → possible OAuth refresh) concurrently. The vault path was not
# concurrency-proof (see navig/vault/store.py), so 1–3 of the parallel calls
# intermittently resolved (None, None) → the misleading "No credential configured
# for provider 'anthropic'" while their siblings succeeded in the same second.
#
# Hardening: per-(provider, connection_id) lock so only ONE thread resolves at a
# time, plus a short-TTL cache of SUCCESSFUL resolutions so a parallel fan-out
# reuses one resolution instead of racing. Failures are never cached (a just-
# connected credential must surface immediately); the TTL is short so a revoked
# credential stops being served within seconds. The cache holds exactly the
# ``(api_key, oauth_token)`` tuple callers receive anyway — no extra copies,
# never logged, never persisted.

_CRED_TTL_SECONDS = 15.0
_cred_meta_lock = threading.Lock()
_cred_locks: dict[tuple[str, str | None], threading.Lock] = {}
_cred_cache: dict[tuple[str, str | None], tuple[float, tuple[str | None, str | None]]] = {}


def _cred_lock_for(key: tuple[str, str | None]) -> threading.Lock:
    with _cred_meta_lock:
        lock = _cred_locks.get(key)
        if lock is None:
            lock = _cred_locks[key] = threading.Lock()
        return lock


def invalidate_credential_cache(provider_id: str | None = None) -> None:
    """Drop cached dispatch credentials (all providers when ``provider_id`` is
    None). Call after connect/disconnect/default-switch so the next dispatch
    re-resolves immediately instead of waiting out the TTL."""
    with _cred_meta_lock:
        if provider_id is None:
            _cred_cache.clear()
        else:
            for key in [k for k in _cred_cache if k[0] == provider_id]:
                _cred_cache.pop(key, None)


def resolve_provider_credential(
    provider_id: str, connection_id: str | None = None
) -> tuple[str | None, str | None]:
    """THE canonical dispatch credential for a provider.

    Prefers a routable NAVIG connection (a claude-max OAuth subscription, or a
    connection-stored key), then falls back to the shared AuthProfileManager key
    store (env / vault / profiles). Returns ``(api_key, oauth_token)`` with
    exactly one element set on success, else ``(None, None)``.

    When *connection_id* is given, credentials come from **that exact
    connection** — the seam that lets the fallback layer rotate across several
    subscriptions of the same provider (e.g. three Claude Max accounts). A
    specific connection is authoritative: no AuthProfileManager fallback (that
    would silently use a *different* account's key).

    Every inference path — agents, LLM modes, the gateway, plugins — should route
    credentials through this so a subscription (which has NO API key) works
    everywhere, not just in one dispatcher.

    Thread-safe: resolution is serialized per (provider, connection) and a
    successful result is shared for a few seconds so a parallel fan-out (e.g. a
    council round) performs ONE resolution instead of racing the vault/OAuth
    path from every worker thread.
    """
    if not provider_id:
        return None, None
    key = (provider_id, connection_id)
    with _cred_lock_for(key):
        with _cred_meta_lock:
            hit = _cred_cache.get(key)
        if hit is not None and time.monotonic() < hit[0]:
            return hit[1]
        cred = _resolve_provider_credential_uncached(provider_id, connection_id)
        with _cred_meta_lock:
            if cred != (None, None):
                _cred_cache[key] = (time.monotonic() + _CRED_TTL_SECONDS, cred)
            else:
                _cred_cache.pop(key, None)  # never serve (or keep) a failure
        return cred


def _resolve_provider_credential_uncached(
    provider_id: str, connection_id: str | None = None
) -> tuple[str | None, str | None]:
    """The actual resolution body — see :func:`resolve_provider_credential`."""
    if connection_id:
        try:
            from navig.providers.connect import list_connections

            conn = next(
                (c for c in list_connections() if c.get("connection_id") == connection_id),
                None,
            )
            if conn is not None:
                cred = credential_for_connection(conn, provider_id)
                if cred is not None:
                    return cred
        except Exception:  # noqa: BLE001
            pass
        return None, None
    try:
        cred = credential_for_provider(provider_id)
        if cred is not None:
            return cred
    except Exception:  # noqa: BLE001
        pass
    try:
        from navig.providers.auth import AuthProfileManager

        key, _ = AuthProfileManager().resolve_auth(provider_id)
        if key is not None and hasattr(key, "get_secret_value"):
            key = key.get_secret_value()
        return (str(key) if key else None), None
    except Exception:  # noqa: BLE001
        return None, None


def default_connection_id(provider_id: str | None) -> str | None:
    """The connection_id of the DEFAULT routable connection for ``provider_id``,
    or ``None`` when no connection backs it (a raw-key / env provider).

    Lets the ``run_llm`` dispatcher key the default account's cooldown by
    ``@conn:<cid>`` so an account-wide cap it records is shared with the
    connection-first paths (``complete_via_connection`` / the agent loop), which
    already key every account that way. Best-effort — never raises."""
    try:
        conns = list_provider_connections(provider_id)  # default-first
    except Exception:  # noqa: BLE001
        return None
    return conns[0].get("connection_id") if conns else None


def accounts_to_try(
    connections: list[dict[str, Any]],
    provider_id: str,
    model: str,
    *,
    exclude_connection_id: str | None = None,
    reprobe_when_all_cooling: bool = True,
) -> list[dict[str, Any]]:
    """The accounts to attempt for ``(provider, model)``, in preference order.

    The shared account-selection core for the rotation loops
    (:func:`complete_via_connection` and the agent's ``_account_retry``):
    default-first, currently-cooling accounts dropped (via the shared cooldown
    map, which is account-wide aware). Optionally excludes the account that just
    failed. When EVERY account is cooling it returns just the first — a re-probe,
    since the window may have cleared — unless *reprobe_when_all_cooling* is False
    (the agent's mid-turn retry would rather drop to the fast model than re-hit a
    capped account). Pure: no I/O, so both callers share one definition and it's
    trivially unit-testable.

    The *execution* of each account differs between callers (sync one-shot string
    vs. async client-adopting stream), so only the selection is shared here.
    """
    from navig.llm.fallback_policy import account_cool_key, is_cooling

    conns = [
        c for c in connections
        if c.get("connection_id") and c.get("connection_id") != exclude_connection_id
    ]
    if not conns:
        return []
    live = [
        c for c in conns
        if not is_cooling(account_cool_key(provider_id, model, c.get("connection_id")))
    ]
    if live:
        return live
    return conns[:1] if reprobe_when_all_cooling else []


def resolve_rotating_credential(
    provider_id: str, model: str | None = None
) -> tuple[str | None, str | None, str | None]:
    """Like :func:`resolve_provider_credential` but **skips accounts on cooldown**
    and returns the connection_id used.

    Picks the first routable connection for *provider_id* (default-first) that is
    NOT currently cooling down (per :mod:`navig.llm.fallback_policy`) for
    *model*; falls back to the default account when they are all cooling. Returns
    ``(api_key, oauth_token, connection_id)``; ``connection_id`` is ``None`` when
    the credential comes from the shared key store (no connection backs it).

    This is what lets a long-running agent conversation move off a capped Claude
    Max account onto a sibling subscription on the next turn — reading the same
    cooldown map the CLI/agent fallback already writes to.
    """
    if not provider_id:
        return None, None, None
    conns = list_provider_connections(provider_id)
    if conns:
        chosen: dict[str, Any] | None = None
        if model:
            try:
                from navig.llm.fallback_policy import account_cool_key, is_cooling
            except Exception:  # noqa: BLE001
                is_cooling = None  # type: ignore[assignment]
            if is_cooling is not None:
                for c in conns:
                    cid = c.get("connection_id")
                    keys = [account_cool_key(provider_id, model, cid)]
                    if c.get("is_default"):
                        # run_llm cools the DEFAULT account under the bare key.
                        keys.append(account_cool_key(provider_id, model, None))
                    if any(is_cooling(k) for k in keys):
                        continue
                    chosen = c
                    break
        chosen = chosen or conns[0]  # all cooling (or no model) → default account
        cred = credential_for_connection(chosen, provider_id)
        if cred is not None:
            return cred[0], cred[1], chosen.get("connection_id")
    key, oauth = resolve_provider_credential(provider_id)
    return key, oauth, None


def list_provider_connections(provider_id: str | None) -> list[dict[str, Any]]:
    """Routable connections backing ``provider_id`` — the **default first**, then
    the rest. Used to rotate across sibling subscriptions (e.g. several Claude Max
    accounts) when one rate-limits. Public dicts only; never carries a secret."""
    if not provider_id:
        return []
    try:
        from navig.providers.connect import list_connections

        conns = list_connections()
    except Exception:  # noqa: BLE001
        return []
    cands = [c for c in conns if _provider_of(c) == provider_id and c.get("is_routable")]
    cands.sort(key=lambda c: 0 if c.get("is_default") else 1)
    return cands


def credential_for_connection(
    conn: dict[str, Any], provider_id: str | None = None
) -> tuple[str | None, str | None] | None:
    """Resolve ``(api_key, oauth_token)`` for a specific connection dict (exactly
    one element set), or ``None``. A claude-max connection yields its OAuth bearer
    (auto-refreshed); every other connection yields its stored/shared key."""
    if conn is None:
        return None
    pid = provider_id or _provider_of(conn)
    if conn.get("template_id") == "claude-max":
        try:
            token = _run_sync(lambda: _oauth_bearer(conn.get("secret_ref")))
        except Exception:  # noqa: BLE001
            return None
        return (None, token) if token else None
    key = _resolve_api_key(conn, pid)
    return (key, None) if key else None


def credential_for_provider(provider_id: str) -> tuple[str | None, str | None] | None:
    """Return ``(api_key, oauth_token)`` from the routable connection backing
    ``provider_id`` (exactly one element set), or ``None`` when no connection
    backs it. Lets the LLM dispatch layer use a claude-max OAuth subscription /
    connection-stored key instead of only the AuthProfileManager key store."""
    if not provider_id:
        return None
    cands = list_provider_connections(provider_id)
    if not cands:
        return None
    return credential_for_connection(cands[0], provider_id)  # default first


def _provider_config(provider_id: str | None, endpoint: str | None, *, oauth: bool):
    from navig.providers.clients import ProviderConfig, get_builtin_provider
    from navig.providers.types import ModelApi

    if oauth:
        return get_builtin_provider("anthropic")
    if endpoint:
        return ProviderConfig(
            name=provider_id or "custom", base_url=endpoint.strip(),
            api=ModelApi.OPENAI_COMPLETIONS,
        )
    if provider_id:
        cfg = get_builtin_provider(provider_id)
        if cfg is not None:
            return cfg
        try:
            from navig.llm.router import PROVIDER_BASE_URLS

            base = PROVIDER_BASE_URLS.get(provider_id)
        except Exception:  # noqa: BLE001
            base = None
        if base:
            return ProviderConfig(name=provider_id, base_url=base, api=ModelApi.OPENAI_COMPLETIONS)
    return get_builtin_provider("openai")


def _run_sync(coro_fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run an async fn to completion from sync code, safe whether or not an event
    loop is already running (offloads to a worker thread when it is)."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_fn())
    with _cf.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(coro_fn())).result()


# ─────────────────────────────────────────────────────────────────────────────
# The bridge
# ─────────────────────────────────────────────────────────────────────────────


def _complete_one_account(
    conn: dict[str, Any],
    provider_id: str | None,
    model: str,
    system_prompt: str,
    user_content: str,
    temperature: float,
    max_tokens: int,
    timeout: float,
) -> str:
    """Run one system+user completion through a SPECIFIC connection (account).

    Resolves that connection's own credential (claude-max OAuth bearer, else its
    stored/shared key) — so rotating across sibling subscriptions uses each
    account's real token, never a shared one."""
    template_id = conn.get("template_id")
    is_oauth = template_id == "claude-max"
    endpoint = (conn.get("metadata") or {}).get("endpoint")

    async def _run() -> str:
        from navig.providers.clients import CompletionRequest, Message, create_client

        token: str | None = None
        api_key: str | None = None

        if is_oauth:
            # Reuse the driver's OAuth resolver: reveals the bearer and
            # auto-refreshes a token bundle within a 5-minute buffer.
            from navig.providers.connections import get_connection_store
            from navig.providers.drivers.native import NativeDriver

            store = get_connection_store()
            drv = NativeDriver(
                secret_resolver=store.read_secret,
                secret_writer=store.update_secret,
                oauth=True,
                provider_id="anthropic",
            )
            token = await drv._oauth_access_token(conn.get("secret_ref"))
            if not token:
                raise NoRoutableConnection(
                    "Claude subscription token unavailable — re-run "
                    "`navig connect login claude-max`."
                )
        else:
            api_key = _resolve_api_key(conn, provider_id)

        config = _provider_config(provider_id, endpoint, oauth=is_oauth)
        if config is None:
            raise NoRoutableConnection(f"No provider config for '{provider_id or template_id}'.")

        client = create_client(config, api_key=api_key, oauth_token=token, timeout=timeout)
        try:
            resp = await client.complete(CompletionRequest(
                messages=[
                    Message(role="system", content=system_prompt),
                    Message(role="user", content=user_content),
                ],
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            ))
            return resp.content or ""
        finally:
            close = getattr(client, "close", None)
            if close:
                try:
                    await close()
                except Exception:  # noqa: BLE001
                    pass

    return _run_sync(_run)


def _conn_label(conn: dict[str, Any]) -> str:
    return conn.get("name") or conn.get("template_id") or "connection"


def complete_via_connection(
    *,
    system_prompt: str,
    user_content: str,
    model_spec: str | None = None,
    connection_id: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    timeout: float = 60.0,
    on_fallback: Callable[[dict[str, Any]], None] | None = None,
) -> str:
    """Run a system+user completion through the resolved connection, rotating
    across sibling accounts of the same provider on a transient failure.

    When the resolved connection is the operator's default (no explicit
    *connection_id*) and it rate-limits / overloads / has a credential issue,
    NAVIG tries the **other** routable connections for that provider (e.g. a
    second Claude Max subscription) with the same model before giving up.
    Per-account cooldowns (shared with the agent path via
    :mod:`navig.llm.fallback_policy`) skip an account that just failed. When a
    rotation happens, *on_fallback* is invoked with ``{from,to,connection_id,
    provider,model,reason}`` so the CLI/deck can show which account answered.

    Raises :class:`NoRoutableConnection` if none is available and
    :class:`ExternalNotRoutable` if the selection is a delegated runtime.
    """
    route = resolve_route(model_spec, connection_id)
    if route is None:
        raise NoRoutableConnection("No AI connection configured. Run `navig connect`.")
    primary = route.connection
    if _is_external(primary):
        raise ExternalNotRoutable(_conn_label(primary))

    model = route.model
    if not model:
        raise NoRoutableConnection(f"Connection '{primary.get('name')}' has no model configured.")

    provider_id = _provider_of(primary) or (
        "anthropic" if primary.get("template_id") == "claude-max" else None
    )

    # Account chain: the resolved connection first, then its siblings (same
    # provider) — but only when the caller didn't pin a specific connection.
    accounts = [primary]
    if connection_id is None and provider_id:
        primary_cid = primary.get("connection_id")
        accounts.extend(
            c for c in list_provider_connections(provider_id)
            if c.get("connection_id") != primary_cid
        )

    from navig.llm.fallback_policy import (
        account_cool_key,
        categorize_error,
        mark_cooldown,
        should_rotate_account,
    )

    def _cool_key(acct: dict[str, Any]) -> str:
        return account_cool_key(provider_id, model, acct.get("connection_id"))

    # Pre-emptively drop accounts that are still cooling (default-first among the
    # survivors) so a capped default doesn't cost a wasted round-trip every call;
    # if EVERY account is cooling, re-probe the default alone (it may have
    # recovered). Shared with the agent's rotation via `accounts_to_try`.
    primary_cid = primary.get("connection_id")
    ordered = accounts_to_try(accounts, provider_id, model)
    cooling: list[str] = [_conn_label(a) for a in accounts if a not in ordered]

    last_exc: Exception | None = None
    tried: list[str] = []       # accounts actually attempted (and failed)
    for acct in ordered:
        try:
            content = _complete_one_account(
                acct, provider_id, model, system_prompt, user_content,
                temperature, max_tokens, timeout,
            )
        except ExternalNotRoutable:
            raise
        except Exception as exc:  # noqa: BLE001 — classify, cool, maybe rotate
            category = categorize_error(exc)
            mark_cooldown(_cool_key(acct), category)
            last_exc = exc
            tried.append(_conn_label(acct))
            # Pinned account or a non-rotatable error → stop immediately.
            if connection_id is not None or not should_rotate_account(category):
                raise
            # Honest per-account line (don't claim a "next account" exists — the
            # loop may have nothing left to try). The outcome is logged below.
            logger.info(
                "account '%s' %s for %s:%s", _conn_label(acct), category, provider_id, model,
            )
            continue
        # An answer from any account other than the resolved default is a
        # rotation the caller should surface — including the case where the
        # default was pre-skipped for cooling (reason "cooldown", no live error).
        if acct.get("connection_id") != primary_cid and on_fallback is not None:
            try:
                on_fallback({
                    "from": _conn_label(primary),
                    "to": _conn_label(acct),
                    "connection_id": acct.get("connection_id"),
                    "provider": provider_id,
                    "model": model,
                    "reason": categorize_error(last_exc) if last_exc else "cooldown",
                })
            except Exception:  # noqa: BLE001 — reporting must never break the answer
                pass
        return content

    # Every account for this provider is unavailable. Make the failure legible in
    # the logs: how many accounts exist, which were tried, which are cooling.
    if len(accounts) > 1:
        logger.warning(
            "all %d %s account(s) unavailable for %s — tried [%s], cooling [%s]",
            len(accounts), provider_id, model,
            ", ".join(tried) or "—", ", ".join(cooling) or "—",
        )
    else:
        logger.warning(
            "the only %s account is unavailable for %s — connect another with "
            "`navig connect login claude-max` so NAVIG can rotate",
            provider_id, model,
        )
    if last_exc is not None:
        raise last_exc
    raise NoRoutableConnection("No routable AI connection for this request.")
