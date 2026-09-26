"""
NativeDriver — NAVIG runs inference itself for API-key and local providers.

Covers the Phase-2 vertical slice:
  * Anthropic / OpenAI API-key connections,
  * any OpenAI-compatible custom endpoint,
  * local loopback runtimes (Ollama / LM Studio / vLLM) — keyless.

Auth is "store the API key in the vault" (or keyless for loopback). Validation
runs a real 1-token completion through core's existing async provider clients
(:mod:`navig.providers.clients`) and maps failures to a health state via the
ported ``parse_test_connection_error`` rules. The driver never persists or logs
secrets — it resolves a ``secret_ref`` transiently via an injected resolver.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable
from urllib.parse import urlparse

from navig.providers.connection_types import Capability, HealthState
from navig.providers.drivers.base import (
    AuthStart,
    ModelInfo,
    ProviderDriver,
    ValidationResult,
)

logger = logging.getLogger(__name__)

SecretResolver = Callable[[str | None], "str | None | Awaitable[str | None]"]


def parse_test_connection_error(msg: str) -> tuple[str, str]:
    """Map a raw client error to (health_state, friendly message). Ported from
    craft's ``connection-setup-logic.ts:parseTestConnectionError``."""
    low = msg.lower()
    if any(s in low for s in ("econnrefused", "enotfound", "fetch failed",
                              "cannot connect", "connection refused", "timed out", "timeout")):
        return HealthState.UNREACHABLE.value, "Cannot reach the API endpoint. Check the URL / that the server is running."
    if any(s in low for s in ("401", "unauthorized", "authentication", "invalid api key", "invalid_api_key")):
        return HealthState.INVALID.value, "Invalid API key."
    if "403" in low:
        return HealthState.INVALID.value, "API key lacks permission for this resource."
    if "404" in low and "model" in low:
        return HealthState.DEGRADED.value, "Model not found. Check the model name."
    if "404" in low:
        return HealthState.UNREACHABLE.value, "API endpoint not found. Check the URL."
    if "429" in low or "rate limit" in low:
        return HealthState.DEGRADED.value, "Rate limit exceeded. Try again shortly."
    # 5xx is the PROVIDER failing, never the operator's credential. Without this
    # branch a 500/502/503 fell through to INVALID, and `_validate_and_store`
    # reads INVALID as "real auth failure only" — so one hiccup persisted
    # `needs_reauth` AND dropped Capability.INFERENCE, taking a working provider
    # out of routing until the operator re-authenticated a key that was fine.
    # ⚠ "504" is listed EXPLICITLY. An earlier version of this comment claimed it
    # "already lands in unreachable via 'timeout'" — true only when the message
    # carries that word. A bare `HTTP 504` fell straight through to INVALID, and
    # the parity guard in tests/llm/test_liveness_transient.py caught it on its
    # first run. A claim about what another branch catches is not a substitute
    # for listing the code.
    if any(s in low for s in ("500", "502", "503", "504", "internal server error",
                              "bad gateway", "service unavailable", "gateway timeout",
                              "overloaded")):
        return HealthState.DEGRADED.value, "Provider is temporarily unavailable. Try again shortly."
    return HealthState.INVALID.value, msg[:300]


def _extract_access_token(secret: str | None) -> str | None:
    """A Claude OAuth secret may be a JSON blob {access_token, refresh_token,
    expires_at} (minted flow) or a raw pasted token. Return the bearer token."""
    if not secret:
        return None
    s = secret.strip()
    if s.startswith("{"):
        try:
            import json

            data = json.loads(s)
            return data.get("access_token") or None
        except Exception:  # noqa: BLE001
            return s
    return s


def is_loopback(url: str | None) -> bool:
    if not url or not url.strip():
        return False
    try:
        host = urlparse(url.strip()).hostname or ""
    except Exception:  # noqa: BLE001
        return False
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host in ("localhost", "127.0.0.1", "::1")


class NativeDriver(ProviderDriver):
    driver = "native"
    advertised_capabilities = {
        Capability.AUTH,
        Capability.INFERENCE,
        Capability.MODEL_DISCOVERY,
    }

    def __init__(
        self,
        *,
        secret_resolver: SecretResolver | None = None,
        secret_writer=None,
        oauth: bool = False,
        provider_id: str | None = None,
    ):
        self._resolve = secret_resolver
        self._write = secret_writer
        # When True the stored secret is a Claude.ai subscription OAuth token and
        # inference goes through Anthropic with the official-CLI presentation.
        self._oauth = oauth
        # Which provider's config to use (BUILTIN_PROVIDERS / PROVIDER_BASE_URLS).
        self._provider_id = provider_id

    async def _secret(self, secret_ref: str | None) -> str | None:
        if self._resolve is None:
            return None
        out = self._resolve(secret_ref)
        if hasattr(out, "__await__"):
            return await out  # type: ignore[misc]
        return out  # type: ignore[return-value]

    async def _store(self, secret_ref: str | None, payload: str) -> None:
        if self._write is None or not secret_ref:
            return
        out = self._write(secret_ref, payload)
        if hasattr(out, "__await__"):
            await out  # type: ignore[misc]

    def _resolve_config(self, *, endpoint: str | None, model: str | None):
        """Resolve a ProviderConfig: explicit endpoint → builtin by provider_id →
        PROVIDER_BASE_URLS (OpenAI-compatible) → legacy by-model fallback."""
        from navig.providers.clients import ProviderConfig, get_builtin_provider
        from navig.providers.types import ModelApi

        if endpoint:
            return ProviderConfig(name="custom", base_url=endpoint.strip(),
                                  api=ModelApi.OPENAI_COMPLETIONS)
        pid = self._provider_id
        if pid:
            cfg = get_builtin_provider(pid)
            if cfg is not None:
                return cfg
            try:
                from navig.llm.router import PROVIDER_BASE_URLS

                base = PROVIDER_BASE_URLS.get(pid)
            except Exception:  # noqa: BLE001
                base = None
            if base:
                return ProviderConfig(name=pid, base_url=base, api=ModelApi.OPENAI_COMPLETIONS)
        # Legacy fallback: infer openai/anthropic from the model id, else openai.
        m = (model or "").lower()
        if m.startswith("claude"):
            return get_builtin_provider("anthropic")
        return get_builtin_provider("openai")

    async def _oauth_access_token(self, secret_ref: str | None, *, force_refresh: bool = False) -> str | None:
        """Resolve the Anthropic OAuth bearer, refreshing within a 5-min buffer
        (or on demand). Re-stores the refreshed token bundle in the vault."""
        import json
        import time as _time

        secret = await self._secret(secret_ref)
        if not secret:
            return None
        s = secret.strip()
        if not s.startswith("{"):
            return s  # raw pasted token — nothing to refresh
        try:
            bundle = json.loads(s)
        except Exception:  # noqa: BLE001
            return s
        access = bundle.get("access_token")
        refresh = bundle.get("refresh_token")
        expires_at = bundle.get("expires_at")  # seconds
        needs = force_refresh or (expires_at and _time.time() > float(expires_at) - 300)
        if needs and refresh:
            try:
                from navig.providers import claude_oauth

                fresh = await claude_oauth.refresh_tokens(refresh)
                await self._store(secret_ref, json.dumps(fresh))
                return fresh.get("access_token") or access
            except Exception:  # noqa: BLE001 — fall back to the current token
                return access
        return access

    async def _probe_walk(self, client, config, candidates: list[str]) -> ValidationResult:
        """One-token completions over *candidates* until a model answers.

        The verdict is about the credential, so an id the provider reports as
        unknown/retired is skipped rather than reported. The answering model is
        listed FIRST in ``models`` — ``connect`` records ``models[0]`` as the
        connection's ``default_model``, and a retired default is what this
        replaces. On 2026-09-19 the previous single ``config.models[0]`` probe
        was a retired id for every provider the operator held a key for.
        """
        from navig.providers.clients import CompletionRequest, Message
        from navig.providers.probe_models import probe_first_answering

        async def _attempt(model: str) -> None:
            await client.complete(CompletionRequest(
                messages=[Message(role="user", content="ping")],
                model=model, max_tokens=1, temperature=0.0,
            ))

        answered, error, gone = await probe_first_answering(candidates, _attempt)
        if answered:
            known = [answered] + [m for m in candidates if m != answered and m not in gone]
            known += [m.id for m in config.models if m.id not in known and m.id not in gone]
            return ValidationResult(ok=True, health=HealthState.HEALTHY.value,
                                    models=[ModelInfo(id=m) for m in known])
        if error is None:
            return ValidationResult(ok=False, health=HealthState.INVALID.value,
                                    error_code="validation_error",
                                    error_message="No model id to probe with.")
        if gone and len(gone) == len(candidates):
            # Every id we know is retired: the credential was never judged.
            return ValidationResult(
                ok=False, health=HealthState.DEGRADED.value, error_code="validation_error",
                error_message=(
                    "Every known model id is retired (" + ", ".join(gone) + "). "
                    "Update navig/providers/registry.py; the credential was not judged."
                ),
            )
        # map, never leak internals/secrets
        health, friendly = parse_test_connection_error(str(error))
        return ValidationResult(ok=False, health=health,
                                error_code="validation_error", error_message=friendly)

    async def _probe_oauth(self, config, token: str, candidates: list[str]) -> ValidationResult:
        """Run a 1-token Anthropic completion using an OAuth bearer (CLI presentation)."""
        from navig.providers.clients import create_client

        client = create_client(config, oauth_token=token, timeout=20.0)
        try:
            return await self._probe_walk(client, config, candidates)
        finally:
            close = getattr(client, "close", None)
            if close:
                try:
                    await close()
                except Exception:  # noqa: BLE001
                    pass

    # ── auth (api-key / keyless local completes inline) ─────────────────────
    async def start_auth(self, template_id, *, api_key=None, endpoint=None, **kwargs) -> AuthStart:
        # The orchestrator stores the key in the vault and then calls validate();
        # there is no async handshake for api-key/local connections.
        return AuthStart(flow="api_key" if api_key else "none")

    # ── validation via a real 1-token completion ────────────────────────────
    async def validate(self, *, secret_ref, endpoint=None, model=None) -> ValidationResult:
        from navig.providers.clients import create_client, get_builtin_provider
        from navig.providers.probe_models import probe_candidates

        # OAuth (Anthropic Pro/Max subscription): resolve the bearer (auto-refresh
        # within a 5-min buffer), present as the official CLI, retry once on auth fail.
        if self._oauth:
            config = get_builtin_provider("anthropic")
            if config is None:
                return ValidationResult(ok=False, health=HealthState.INVALID.value,
                                        error_code="validation_error",
                                        error_message="Anthropic provider config unavailable.")
            candidates = probe_candidates("anthropic", first=model) or ["claude-haiku-4-5"]
            access = await self._oauth_access_token(secret_ref)
            if not access:
                return ValidationResult(ok=False, health=HealthState.INVALID.value,
                                        error_code="validation_error",
                                        error_message="Claude subscription token required.")
            result = await self._probe_oauth(config, access, candidates)
            if not result.ok and result.health == HealthState.INVALID.value:
                # token may be stale/revoked — force a refresh and retry once
                access2 = await self._oauth_access_token(secret_ref, force_refresh=True)
                if access2 and access2 != access:
                    result = await self._probe_oauth(config, access2, candidates)
            return result

        api_key = await self._secret(secret_ref)
        keyless = is_loopback(endpoint) and not api_key

        # Resolve the provider config (builtin by provider_id → base-url → custom endpoint).
        config = self._resolve_config(endpoint=endpoint, model=model)

        if config is None:
            return ValidationResult(ok=False, health=HealthState.INVALID.value,
                                    error_code="validation_error",
                                    error_message="No provider configuration resolved.")
        if not keyless and not api_key:
            return ValidationResult(ok=False, health=HealthState.INVALID.value,
                                    error_code="validation_error",
                                    error_message="API key required for a non-local endpoint.")

        candidates = probe_candidates(self._provider_id, first=model) or (
            [m.id for m in config.models[:1]] or ["gpt-4o-mini"]
        )
        client = create_client(config, api_key=api_key, timeout=20.0)
        try:
            return await self._probe_walk(client, config, candidates)
        finally:
            close = getattr(client, "close", None)
            if close:
                try:
                    await close()
                except Exception:  # noqa: BLE001
                    pass
