"""
LLM Generate — Unified LLM call interface using the mode router.

This is the primary integration point. All NAVIG subsystems should call:

    from navig.llm.generate import llm_generate
    response = llm_generate(mode="coding", messages=[...])

Or use the new typed orchestrator:

    from navig.llm.generate import run_llm
    result = run_llm(messages=[...], mode="coding")
    # result is an LLMResult with .content, .model, .provider, .latency_ms, etc.

If llm_modes is not configured, falls back to the existing NAVIG_AI_MODEL /
AIAssistant / FallbackManager path — zero breakage.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from navig.agent.usage_tracker import CostTracker
    from navig.llm.types import LLMResult, ModelSelection

logger = logging.getLogger("navig.llm.generate")

# ─────────────────────────────────────────────────────────────
# Module constants — single source of truth for llm_generate defaults
# ─────────────────────────────────────────────────────────────
from navig._llm_defaults import _DEFAULT_MAX_TOKENS as _LLM_DEFAULT_MAX_TOKENS  # noqa: F401
from navig._llm_defaults import _DEFAULT_TEMPERATURE as _LLM_DEFAULT_TEMPERATURE  # noqa: F401

_LLM_DEFAULT_TIMEOUT: float = 120.0     # Default HTTP timeout (seconds)


def llm_generate(
    messages: list[dict[str, str]],
    mode: str | None = None,
    user_input: str | None = None,
    prefer_uncensored: bool | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    model_override: str | None = None,
    provider_override: str | None = None,
    stream: bool = False,
    timeout: float = _LLM_DEFAULT_TIMEOUT,
    effort: str | None = None,
) -> str:
    """
    Unified LLM generation — routes through llm_router then dispatches.

    Priority:
      1. If model_override is set → use it directly (bypass router)
      2. If mode is set → resolve via LLMModeRouter
      3. If user_input is set and mode is None → auto-detect mode
      4. If nothing is configured (no llm_modes) → legacy NAVIG_AI_MODEL path

    Args:
        messages: List of {role, content} dicts.
        mode: LLM mode hint (e.g. "coding", "chat", "research").
        user_input: Raw user text for auto-detection.
        prefer_uncensored: Override mode's uncensored preference.
        temperature: Override temperature.
        max_tokens: Override max_tokens.
        model_override: Force a specific model (e.g. "openai:gpt-4o").
        provider_override: Force a specific provider.
        stream: Whether to stream (not yet implemented).
        timeout: HTTP timeout in seconds.

    Returns:
        Generated text content.
    """
    # When effort is specified, delegate to run_llm for full thinking-param
    # handling.  run_llm already resolves effort → thinking_params per provider;
    # we return .content to preserve the str return-type contract.
    if effort is not None:
        result = run_llm(
            messages=messages,
            mode=mode,
            user_input=user_input,
            prefer_uncensored=prefer_uncensored,
            temperature=temperature,
            max_tokens=max_tokens,
            model_override=model_override,
            provider_override=provider_override,
            timeout=timeout,
            effort=effort,
        )
        # Consistency: the non-effort paths below RAISE on a failed call. Don't
        # let the effort path silently return "" (a blank reply the caller can't
        # distinguish from a real empty completion). Callers already handle
        # llm_generate raising, so surface the error here too.
        _fr = str(getattr(result, "finish_reason", "") or "")
        if not (result.content or "").strip() and _fr.startswith("error"):
            raise RuntimeError(f"LLM generation failed ({_fr})")
        return result.content or ""

    if model_override:
        # Direct model specification — skip router
        provider, model = _parse_model_spec(model_override, provider_override)
        return _call_provider(
            provider=provider,
            model=model,
            messages=messages,
            temperature=temperature or _LLM_DEFAULT_TEMPERATURE,
            max_tokens=max_tokens or _LLM_DEFAULT_MAX_TOKENS,
            timeout=timeout,
        )

    from navig.llm.router import resolve_llm

    resolved = resolve_llm(
        mode=mode,
        user_input=user_input,
        prefer_uncensored=prefer_uncensored,
    )
    logger.debug(
        "Router resolved: %s → %s:%s (reason: %s)",
        resolved.mode,
        resolved.provider,
        resolved.model,
        resolved.resolution_reason,
    )

    # Allow temperature/max_tokens overrides
    temp = temperature if temperature is not None else resolved.temperature
    mt = max_tokens if max_tokens is not None else resolved.max_tokens

    return _call_provider(
        provider=resolved.provider,
        model=resolved.model,
        messages=messages,
        temperature=temp,
        max_tokens=mt,
        timeout=timeout,
        base_url=resolved.base_url,
    )


# ─────────────────────────────────────────────────────────────
# run_llm() — Typed orchestrator (new canonical entrypoint)
# ─────────────────────────────────────────────────────────────


def run_llm(
    messages: list[dict[str, str]],
    mode: str | None = None,
    user_input: str | None = None,
    prefer_uncensored: bool | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    model_override: str | None = None,
    provider_override: str | None = None,
    stream: bool = False,
    timeout: float = _LLM_DEFAULT_TIMEOUT,
    fallback_models: list[str] | None = None,
    caller_info: dict[str, Any] | None = None,
    session_id: str | None = None,
    enable_tools: bool = False,
    cost_tracker: CostTracker | None = None,
    turn: int = 1,
    effort: str | None = None,
) -> LLMResult:
    """
    Typed LLM orchestrator — routes through all 4 stages and returns LLMResult.

    Pipeline:
      0. Assemble context via ContextBuilder (conversation history, KB, etc.)
      1. Resolve mode via Layer 1 (ModeRouter) — context passed for inspection
      2. Select model via Layer 2 (config-based routing)
      3. Build prompt incorporating context, dispatch to provider via Layer 3
      4. (Optional) Parse LLM output for tool calls and execute via ToolRouter
      5. Wraps errors with FallbackManager when fallback_models is set

    Args:
        messages: List of {role, content} dicts.
        mode: LLM mode hint (e.g. "coding", "chat", "research").
        user_input: Raw user text for auto-detection.
        prefer_uncensored: Override mode's uncensored preference.
        temperature: Override temperature.
        max_tokens: Override max_tokens.
        model_override: Force a specific model (e.g. "openai:gpt-4o").
        provider_override: Force a specific provider.
        stream: Whether to stream (not yet implemented).
        timeout: HTTP timeout in seconds.
        fallback_models: List of fallback model specs for automatic retry.
        caller_info: Caller-provided flags for ContextBuilder
                     (e.g. {"enable_kb": True, "include_workspace": False}).
        session_id: Conversation session identifier for history retrieval.
        enable_tools: When True, parse LLM response for tool calls and
                      execute them via the ToolRouter before returning.

    Returns:
        LLMResult with content, model, provider, latency, and token usage.
        When enable_tools is True and the LLM requests a tool call, the result
        metadata will contain 'tool_results' with execution output.
    """
    from navig.llm.types import ModelSelection

    t0 = time.monotonic()

    # --- Step 0: Assemble context ---
    context = _build_pipeline_context(
        user_input=user_input or _extract_user_text(messages),
        caller_info=caller_info,
        session_id=session_id,
    )

    # --- Step 1: Resolve routing ---
    if model_override:
        provider, model = _parse_model_spec(model_override, provider_override)
        selection = ModelSelection(
            provider_name=provider,
            model_name=model,
            temperature=temperature or _LLM_DEFAULT_TEMPERATURE,
            max_tokens=max_tokens or _LLM_DEFAULT_MAX_TOKENS,
            strategy_name="model_override",
        )
    else:
        from navig.llm.router import resolve_llm

        resolved = resolve_llm(
            mode=mode,
            user_input=user_input,
            prefer_uncensored=prefer_uncensored,
        )
        selection = ModelSelection(
            provider_name=resolved.provider,
            model_name=resolved.model,
            temperature=(temperature if temperature is not None else resolved.temperature),
            max_tokens=max_tokens if max_tokens is not None else resolved.max_tokens,
            base_url=resolved.base_url,
            api_key_env=resolved.api_key_env,
            is_uncensored=resolved.is_uncensored,
            strategy_name="llm_router",
            metadata={"mode": resolved.mode, "reason": resolved.resolution_reason},
        )

    # --- Effort resolution ---
    thinking_params: dict[str, Any] = {}
    try:
        from navig.agent.effort import auto_detect_effort, get_thinking_params, resolve_effort

        if effort is not None:
            eff_level = resolve_effort(effort)
        else:
            eff_level = auto_detect_effort(user_input or _extract_user_text(messages))
        thinking_params = get_thinking_params(eff_level, provider=selection.provider_name)
        selection.metadata["effort"] = eff_level.value
    except Exception as eff_err:
        logger.debug("Effort resolution failed (non-fatal): %s", eff_err)

    logger.debug(
        "run_llm routing: %s -> %s:%s (strategy: %s, effort: %s)",
        selection.metadata.get("mode", mode),
        selection.provider_name,
        selection.model_name,
        selection.strategy_name,
        selection.metadata.get("effort", "n/a"),
    )

    # --- Step 2: Build prompt with context ---
    enriched_messages = _enrich_messages_with_context(messages, context)

    # --- Step 3: Dispatch with runtime error-driven fallback ---
    from navig.llm.fallback_policy import (
        account_cool_key,
        categorize_error,
        cooldown_remaining,
        is_cooling,
        mark_cooldown,
        should_fallback,
    )

    # Key the primary by the DEFAULT account's connection (@conn:<cid>) — the same
    # account-aware key the connection-first paths use — so run_llm's pre-emptive
    # skip and cooldown marking are account-wide aware (a default account capped
    # on one model is skipped for every model) with no bare-key special case.
    # Falls back to the bare provider:model for a raw-key provider (no connection).
    _primary_cid: str | None = None
    try:
        from navig.providers.inference import default_connection_id

        _primary_cid = default_connection_id(selection.provider_name)
    except Exception as _pc_exc:  # noqa: BLE001
        logger.debug("primary connection-id resolution skipped: %s", _pc_exc)
    primary_spec = account_cool_key(
        selection.provider_name, selection.model_name, _primary_cid
    )
    _global_chain = fallback_models if fallback_models is not None else _load_fallback_chain()

    # The chain is built LAZILY — assembling it may probe a local runtime
    # (Ollama) for reachability, so we only pay that when a fallback is actually
    # needed (primary cooling or failed), never on the happy path.
    _chain_cache: list[str] | None = None

    def _chain() -> list[str]:
        nonlocal _chain_cache
        if _chain_cache is None:
            _chain_cache = _assemble_fallback_chain(selection, _global_chain, model_override)
        return _chain_cache

    # Pre-emptive skip: if the primary is *already* cooling down from a recent
    # failure (e.g. Claude Max capped for the hour), don't waste a round-trip on
    # it — route straight to the fallback chain. Only when routing by MODE; an
    # explicit --model override is always honoured as-is.
    if not model_override and is_cooling(primary_spec) and _chain():
        logger.warning(
            "primary %s is cooling down (%.0fs left) — routing to fallback first",
            primary_spec, cooldown_remaining(primary_spec),
        )
        result = _call_with_fallback(
            messages=enriched_messages,
            selection=selection,
            fallback_models=_chain(),
            timeout=timeout,
            cost_tracker=cost_tracker,
            turn=turn,
            thinking_params=thinking_params,
            primary_category="cooldown",
        )
    else:
        # Always run the PRIMARY through _call_and_wrap so effort/thinking params
        # AND cost tracking are applied (the fallback path preserves them per
        # candidate too). Only drop to the chain if the primary actually failed.
        result = _call_and_wrap(
            messages=enriched_messages,
            selection=selection,
            timeout=timeout,
            cost_tracker=cost_tracker,
            turn=turn,
            thinking_params=thinking_params,
        )
        if str(getattr(result, "finish_reason", "") or "").startswith("error"):
            category = categorize_error(result.finish_reason)
            # primary_spec is now the account-aware @conn:<cid> key, so this one
            # mark cools the default account per-model AND account-wide (shared
            # with the connection-first paths); no separate bare-key mark needed.
            mark_cooldown(primary_spec, category)
            if should_fallback(category) and _chain():
                logger.warning(
                    "primary %s failed (%s: %s) — auto-falling back over %d model(s)",
                    primary_spec, category, _short_err(result.finish_reason), len(_chain()),
                )
                result = _call_with_fallback(
                    messages=enriched_messages,
                    selection=selection,
                    fallback_models=_chain(),
                    timeout=timeout,
                    cost_tracker=cost_tracker,
                    turn=turn,
                    thinking_params=thinking_params,
                    primary_category=category,
                )
            else:
                logger.warning(
                    "primary %s failed (%s: %s) — no fallback available",
                    primary_spec, category, _short_err(result.finish_reason),
                )

    # Stamp timing and selection
    if result.latency_ms == 0:
        result.latency_ms = int((time.monotonic() - t0) * 1000)
    result.selection = selection

    # --- Step 4: Optional tool execution ---
    if enable_tools and result.content:
        result = _maybe_execute_tools(result)

    return result


def _call_and_wrap(
    messages: list[dict[str, str]],
    selection: ModelSelection,
    timeout: float,
    cost_tracker: CostTracker | None = None,
    turn: int = 1,
    thinking_params: dict[str, Any] | None = None,
) -> LLMResult:
    """Dispatch a single LLM call and wrap the response in LLMResult.

    Optionally records a :class:`~navig.agent.usage_tracker.UsageEvent`
    into *cost_tracker* after a successful call.
    """
    from navig.llm.types import LLMResult

    try:
        content, prompt_tokens, completion_tokens, extra = _call_provider_rich(
            provider=selection.provider_name,
            model=selection.model_name,
            messages=messages,
            temperature=selection.temperature,
            max_tokens=selection.max_tokens,
            timeout=timeout,
            base_url=selection.base_url or None,
            thinking_params=thinking_params,
            connection_id=(selection.metadata or {}).get("connection_id"),
        )
        # Wire cost tracking
        if cost_tracker is not None:
            try:
                from navig.agent.usage_tracker import UsageEvent

                cost_tracker.record(
                    UsageEvent(
                        turn=turn,
                        model=selection.model_name,
                        provider=selection.provider_name,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        cache_read_tokens=extra.get("cache_read_tokens", 0),
                        cache_write_tokens=extra.get("cache_write_tokens", 0),
                    )
                )
            except Exception as tracker_err:
                logger.debug("CostTracker.record failed (non-fatal): %s", tracker_err)

        # Wire session-level persistent cost tracking (cross-invocation, saved to JSONL)
        try:
            from navig.cost_tracker import get_session_tracker  # noqa: PLC0415

            get_session_tracker().record(
                model=selection.model_name,
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
                cache_read_tokens=extra.get("cache_read_tokens", 0),
            )
        except Exception as _sct_err:
            logger.debug("session cost_tracker.record failed (non-fatal): %s", _sct_err)

        return LLMResult(
            content=content,
            model=selection.model_name,
            provider=selection.provider_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
    except Exception as e:
        logger.warning(
            "LLM call failed (%s:%s): %s",
            selection.provider_name,
            selection.model_name,
            e,
        )
        return LLMResult(
            content="",
            model=selection.model_name,
            provider=selection.provider_name,
            finish_reason=f"error:{type(e).__name__}: {e}",
        )


def _prompt_cache_enabled() -> bool:
    """Return True when Anthropic prompt caching is enabled in config (default: True).

    Reads ``config.agent.prompt_cache``.  Defaults to *True* so caching is on
    unless explicitly disabled.
    """
    try:
        from navig.core.config_loader import load_config

        config = load_config()
        agent_cfg = getattr(config, "agent", None)
        if agent_cfg is None:
            return True
        return bool(getattr(agent_cfg, "prompt_cache", True))
    except Exception as exc:
        logger.debug("_prompt_cache_enabled: config unavailable (%s)", exc)
        return True


def _load_fallback_chain() -> list[str]:
    """Read ``config.agent.fallback_chain`` from project/global config.

    Returns an empty list when the setting is absent or the config cannot be
    loaded — this is always safe (it simply disables the fallback).

    The setting accepts a list of ``"provider:model"`` specs, e.g.::

        agent:
          fallback_chain:
            - openai:gpt-4o
            - anthropic:claude-3-5-sonnet-20241022
            - openrouter:google/gemini-2.5-flash
    """
    try:
        from navig.core.config_loader import load_config

        config = load_config()
        agent_cfg = getattr(config, "agent", None)
        if agent_cfg is None:
            return []
        chain = getattr(agent_cfg, "fallback_chain", None)
        if not chain:
            return []
        return [str(s) for s in chain if s]
    except Exception as exc:
        logger.debug("_load_fallback_chain: config unavailable (%s)", exc)
        return []


def _short_err(finish_reason: object, limit: int = 160) -> str:
    """Trim a ``run_llm`` ``finish_reason`` to a one-line, human-readable cause."""
    s = str(finish_reason or "")
    if s.startswith("error:"):
        s = s[len("error:"):].strip()
    s = " ".join(s.split())  # collapse whitespace/newlines
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _split_conn(spec: str) -> tuple[str, str | None]:
    """Split a candidate spec's optional ``@conn:<id>`` suffix.

    ``"anthropic:claude-opus-4-8@conn:abc123"`` → ``("anthropic:claude-opus-4-8",
    "abc123")``; a plain ``"openai:gpt-4o"`` → ``("openai:gpt-4o", None)``. The
    suffix carries a specific connection (account) so the chain can encode "same
    model, different subscription" as an ordinary ``list[str]`` entry.
    """
    if "@conn:" in spec:
        base, _, cid = spec.partition("@conn:")
        return base, (cid.strip() or None)
    return spec, None


def _assemble_fallback_chain(
    selection: ModelSelection,
    global_chain: list[str] | None,
    model_override: str | None,
) -> list[str]:
    """Build the ordered, deduped runtime fallback chain for a dispatch.

    Order:
      1. **Sibling accounts** — the same ``provider:model`` on the operator's
         *other* routable connections (e.g. a second/third Claude Max
         subscription), encoded as ``"provider:model@conn:<id>"``. Added even
         under ``--model`` (a different account is not a different model), so a
         capped Claude hops to another account before dropping to a lesser model.
      2. the resolved **mode's** configured fallback (skipped for ``--model``),
      3. the global ``agent.fallback_chain``.

    The primary's default account and duplicates are filtered out; account-tagged
    entries dedupe on the full ``provider:model@conn:<id>`` key so distinct
    accounts are all kept.
    """
    primary_provider = selection.provider_name
    primary_spec = f"{primary_provider}:{selection.model_name}"
    raw: list[str] = []

    # 1) Sibling accounts (same provider:model, other connections).
    try:
        from navig.providers.inference import list_provider_connections

        sibs = list_provider_connections(primary_provider)
        for conn in sibs[1:]:  # [0] is the default account the primary already used
            cid = conn.get("connection_id")
            if cid:
                raw.append(f"{primary_spec}@conn:{cid}")
    except Exception as exc:  # noqa: BLE001 — never let fallback prep break a call
        logger.debug("sibling-account lookup failed (non-fatal): %s", exc)

    # 2) Mode-configured fallback (a different, cheaper model).
    if not model_override:
        try:
            from navig.llm.router import get_mode_fallback_spec

            fb = get_mode_fallback_spec(selection.metadata.get("mode"))
            if fb:
                raw.append(fb)
        except Exception as exc:  # noqa: BLE001
            logger.debug("mode fallback lookup failed (non-fatal): %s", exc)

    # 3) Global agent.fallback_chain.
    raw.extend(global_chain or [])

    seen: set[str] = set()
    out: list[str] = []
    for spec in raw:
        if not spec:
            continue
        base, cid = _split_conn(str(spec))
        try:
            provider, model = _parse_model_spec(base)
        except Exception:  # noqa: BLE001 — a malformed entry must not abort the chain
            continue
        norm = f"{provider}:{model}"
        full = f"{norm}@conn:{cid}" if cid else norm
        if full == primary_spec or full in seen:  # drop the primary's default account + dupes
            continue
        seen.add(full)
        out.append(full)
    return out


def _call_provider_rich(
    provider: str,
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    timeout: float,
    base_url: str | None = None,
    thinking_params: dict[str, Any] | None = None,
    connection_id: str | None = None,
) -> tuple[str, int, int, dict]:
    """Call a provider and return ``(content, prompt_tokens, completion_tokens, extra)``.

    *extra* is a dict that may contain Anthropic-specific fields such as
    ``cache_read_tokens`` and ``cache_write_tokens``.

    *thinking_params* are provider-specific thinking/reasoning parameters
    produced by :func:`navig.agent.effort.get_thinking_params`. They are
    stored on the :class:`CompletionRequest` as ``extra_body`` so the
    provider client can forward them.

    *connection_id* pins a specific connection (account) for credential
    resolution — the fallback layer uses it to rotate across sibling
    subscriptions of the same provider.

    Falls back to :func:`_call_provider` (returns empty token counts) if the
    rich path fails.
    """
    try:
        from navig.providers import (
            CompletionRequest,
            Message,
            create_client,
            get_builtin_provider,
        )

        provider_cfg = get_builtin_provider(provider)
        if provider_cfg is None:
            from navig.llm.router import PROVIDER_BASE_URLS
            from navig.providers.types import ModelApi, ProviderConfig

            url = base_url or PROVIDER_BASE_URLS.get(provider, "https://openrouter.ai/api/v1")
            provider_cfg = ProviderConfig(
                name=provider,
                base_url=url,
                api=ModelApi.OPENAI_COMPLETIONS,
            )
        elif base_url:
            provider_cfg = _with_base_url(provider_cfg, base_url)

        api_key, oauth_token = _resolve_dispatch_credential(provider, connection_id)

        msgs = [Message(role=m["role"], content=m["content"]) for m in messages]

        # F-12: enable Anthropic prompt caching when configured
        _use_cache_control = (
            provider.lower() == "anthropic"
            and _prompt_cache_enabled()
        )

        request = CompletionRequest(
            messages=msgs,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_body=thinking_params if thinking_params else None,
            cache_control=_use_cache_control,
        )
        client = create_client(provider_cfg, api_key=api_key, oauth_token=oauth_token, timeout=timeout)

        async def _run():
            return await client.complete(request)

        async def _close_client() -> None:
            close_fn = getattr(client, "close", None)
            if callable(close_fn):
                await close_fn()

        try:
            result = _safe_run_async(_run)
        finally:
            try:
                _safe_run_async(_close_client)
            except Exception as close_exc:  # noqa: BLE001
                logger.debug("_call_provider_rich client close skipped: %s", close_exc)
        content = result.content or ""
        prompt_tokens = getattr(result, "prompt_tokens", 0) or 0
        completion_tokens = getattr(result, "completion_tokens", 0) or 0
        extra: dict = {
            "cache_read_tokens": getattr(result, "cache_read_input_tokens", 0) or 0,
            "cache_write_tokens": getattr(result, "cache_creation_input_tokens", 0) or 0,
        }
        return content, prompt_tokens, completion_tokens, extra
    except Exception as exc:
        logger.debug("_call_provider_rich via providers system failed: %s — falling back", exc)

    # Fallback: plain string call, no token counts
    content = _call_provider(
        provider=provider,
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        base_url=base_url,
    )
    return content, 0, 0, {}


def _call_provider_rich_stream(
    provider: str,
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    timeout: float,
    base_url: str | None = None,
    thinking_params: dict[str, Any] | None = None,
):
    """Yield :class:`StreamChunk` objects from a streaming provider call.

    This is the streaming counterpart of :func:`_call_provider_rich`.
    Falls back to a single-chunk non-streaming call if the provider
    system is unavailable.
    """
    from navig.providers import (
        CompletionRequest,
        Message,
        create_client,
        get_builtin_provider,
    )
    from navig.providers.clients import StreamChunk

    provider_cfg = get_builtin_provider(provider)
    if provider_cfg is None:
        from navig.llm.router import PROVIDER_BASE_URLS
        from navig.providers.types import ModelApi, ProviderConfig

        url = base_url or PROVIDER_BASE_URLS.get(provider, "https://openrouter.ai/api/v1")
        provider_cfg = ProviderConfig(
            name=provider,
            base_url=url,
            api=ModelApi.OPENAI_COMPLETIONS,
        )
    elif base_url:
        provider_cfg = _with_base_url(provider_cfg, base_url)

    api_key, oauth_token = _resolve_dispatch_credential(provider)

    msgs = [Message(role=m["role"], content=m["content"]) for m in messages]
    request = CompletionRequest(
        messages=msgs,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
        extra_body=thinking_params if thinking_params else None,
        cache_control=(provider.lower() == "anthropic" and _prompt_cache_enabled()),
    )
    client = create_client(provider_cfg, api_key=api_key, oauth_token=oauth_token, timeout=timeout)

    async def _stream():
        chunks = []
        async for chunk in client.complete_stream(request):
            chunks.append(chunk)
        return chunks

    async def _close_client() -> None:
        close_fn = getattr(client, "close", None)
        if callable(close_fn):
            await close_fn()

    try:
        return _safe_run_async(_stream)
    except Exception as exc:
        logger.debug("_call_provider_rich_stream failed: %s — yielding single chunk fallback", exc)
        content, _, _, _ = _call_provider_rich(
            provider=provider,
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            base_url=base_url,
            thinking_params=thinking_params,
        )
        return [StreamChunk(delta=content, finish_reason="stop", provider=provider, model=model)]
    finally:
        try:
            _safe_run_async(_close_client)
        except Exception as close_exc:  # noqa: BLE001
            logger.debug("_call_provider_rich_stream client close skipped: %s", close_exc)


def llm_generate_stream(
    messages: list[dict[str, str]],
    mode: str | None = None,
    user_input: str | None = None,
    prefer_uncensored: bool | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    model_override: str | None = None,
    provider_override: str | None = None,
    timeout: float = 120.0,
):
    """Streaming variant of :func:`llm_generate`.

    Yields :class:`~navig.providers.clients.StreamChunk` objects as they
    arrive from the LLM provider.

    Usage::

        from navig.llm.generate import llm_generate_stream

        for chunk in llm_generate_stream(messages=[...], mode="chat"):
            if chunk.delta:
                print(chunk.delta, end="", flush=True)
    """
    if model_override:
        provider, model = _parse_model_spec(model_override, provider_override)
    else:
        from navig.llm.router import resolve_llm

        resolved = resolve_llm(
            mode=mode,
            user_input=user_input,
            prefer_uncensored=prefer_uncensored,
        )
        provider = resolved.provider
        model = resolved.model

    return _call_provider_rich_stream(
        provider=provider,
        model=model,
        messages=messages,
        temperature=temperature or 0.7,
        max_tokens=max_tokens or 4096,
        timeout=timeout,
    )


def _call_with_fallback(
    messages: list[dict[str, str]],
    selection: ModelSelection,
    fallback_models: list[str],
    timeout: float,
    cost_tracker: CostTracker | None = None,
    turn: int = 1,
    thinking_params: dict[str, Any] | None = None,
    primary_category: str = "unknown",
) -> LLMResult:
    """Try each fallback candidate in order, hopping on failure.

    Every candidate runs through :func:`_call_and_wrap`, so effort/thinking
    params **and** cost tracking are preserved on the model that actually
    answers (the old path used a bare string call that carried neither).

    Cooling candidates (recently failed — see
    :mod:`navig.llm.fallback_policy`) are skipped; a candidate that fails is
    itself put on cooldown. The winning result is stamped with visible fallback
    metadata (``is_fallback`` + ``raw['fallback']``) so the CLI/deck can tell the
    user *that* and *why* it fell back — never a silent swap.
    """
    from navig.llm.fallback_policy import (
        categorize_error,
        cooldown_remaining,
        is_cooling,
        mark_cooldown,
    )
    from navig.llm.types import LLMResult, ModelSelection

    primary_spec = f"{selection.provider_name}:{selection.model_name}"
    tried: list[str] = []
    last: LLMResult | None = None

    for i, spec in enumerate(fallback_models):
        base, connection_id = _split_conn(spec)
        provider, model = _parse_model_spec(base)
        norm = f"{provider}:{model}"
        # Cooldowns are keyed per ACCOUNT — cooling one Claude subscription must
        # NOT sideline the sibling account that shares its provider:model.
        cool_key = f"{norm}@conn:{connection_id}" if connection_id else norm
        label = f"{norm}#{connection_id[:8]}" if connection_id else norm

        if is_cooling(cool_key):
            logger.info(
                "skip fallback %s (cooling %.0fs left)", label, cooldown_remaining(cool_key)
            )
            tried.append(f"{label}(cooling)")
            continue

        # A fresh selection for the fallback provider — reset base_url so the
        # candidate resolves its OWN endpoint (the primary's base_url is wrong
        # for a different provider); keep sampling params + mode metadata, and
        # pin the specific account (connection_id) when the candidate names one.
        fb_meta = dict(selection.metadata)
        if connection_id:
            fb_meta["connection_id"] = connection_id
        else:
            fb_meta.pop("connection_id", None)
        fb_selection = ModelSelection(
            provider_name=provider,
            model_name=model,
            temperature=selection.temperature,
            max_tokens=selection.max_tokens,
            base_url="",
            strategy_name="runtime_fallback",
            metadata=fb_meta,
        )
        res = _call_and_wrap(
            messages=messages,
            selection=fb_selection,
            timeout=timeout,
            cost_tracker=cost_tracker,
            turn=turn,
            thinking_params=thinking_params,
        )

        if not str(getattr(res, "finish_reason", "") or "").startswith("error"):
            res.is_fallback = True
            res.attempts = i + 2  # primary + candidates tried so far + this one
            res.raw = dict(res.raw or {})
            res.raw["fallback"] = {
                "from": primary_spec,
                "to": norm,
                "connection_id": connection_id,
                "reason": primary_category,
                "skipped": tried,
            }
            logger.warning(
                "✔ fell back to %s (primary %s failed: %s)", label, primary_spec, primary_category
            )
            return res

        category = categorize_error(res.finish_reason)
        mark_cooldown(cool_key, category)
        tried.append(f"{label}({category})")
        last = res
        logger.warning("fallback candidate %s failed (%s)", label, category)

    # Nothing in the chain answered.
    if last is None:
        last = LLMResult(content="", model="", provider="")
    last.finish_reason = (
        f"all_fallbacks_failed:primary={primary_category}:tried={tried or ['<none>']}"
    )
    last.attempts = len(fallback_models) + 1
    return last


# ─────────────────────────────────────────────────────────────
# Tool execution pipeline (Step 4)
# ─────────────────────────────────────────────────────────────


def _maybe_execute_tools(result: LLMResult) -> LLMResult:
    """
    Parse LLM output for tool calls and execute via ToolRouter.

    If the LLM returned a tool_call or multi_step action, executes
    the tool(s) and attaches tool_results to result.metadata.  The
    result.content is augmented with the formatted tool output so
    downstream consumers see the combined response.

    Returns the (possibly enriched) LLMResult.
    """
    try:
        from navig.tools.router import get_tool_router
        from navig.tools.schemas import (
            MultiStepAction,
            RespondAction,
            ToolCallAction,
            format_tool_result_for_llm,
            parse_llm_action,
        )
    except ImportError:
        logger.debug("Tool modules not available — skipping tool execution")
        return result

    try:
        action = parse_llm_action(result.content)
    except Exception as exc:
        logger.debug("Failed to parse LLM output for tool actions: %s", exc)
        return result

    if isinstance(action, RespondAction):
        # LLM chose to respond directly — no tool call
        return result

    # Load safety policy from config (if available)
    safety_policy = _load_tools_safety_policy()
    router = get_tool_router(safety_policy=safety_policy)

    if isinstance(action, ToolCallAction):
        tool_result = router.execute(action)
        formatted = format_tool_result_for_llm(tool_result)
        result.metadata = result.metadata or {}
        result.metadata["tool_action"] = "tool_call"
        result.metadata["tool_results"] = [tool_result.to_dict()]
        result.content = f"{result.content}\n\n{formatted}"
        return result

    if isinstance(action, MultiStepAction):
        tool_results = router.execute_multi(action.steps)
        formatted_parts = [format_tool_result_for_llm(r) for r in tool_results]
        formatted = "\n\n".join(formatted_parts)
        result.metadata = result.metadata or {}
        result.metadata["tool_action"] = "multi_step"
        result.metadata["tool_results"] = [r.to_dict() for r in tool_results]
        result.content = f"{result.content}\n\n{formatted}"
        return result

    return result


def _load_tools_safety_policy() -> dict[str, Any]:
    """
    Load safety policy from GlobalConfig.tools section.

    Returns empty dict if config is unavailable.
    """
    try:
        from navig.core.config_loader import load_config

        config = load_config()
        tools_cfg = getattr(config, "tools", None)
        if tools_cfg is None:
            return {}
        return {
            "blocked_tools": list(getattr(tools_cfg, "blocked_tools", [])),
            "require_confirmation": list(getattr(tools_cfg, "require_confirmation", [])),
            "max_calls_per_turn": getattr(tools_cfg, "max_calls_per_turn", 10),
            "safety_mode": getattr(tools_cfg, "safety_mode", "standard"),
        }
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────
# Context pipeline helpers
# ─────────────────────────────────────────────────────────────


def _build_pipeline_context(
    user_input: str,
    caller_info: dict[str, Any] | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """
    Safely invoke ContextBuilder.  Returns EMPTY_CONTEXT on any failure.

    This ensures that if context_builder is not configured, not installed,
    or throws, the pipeline continues with an empty context dict.
    """
    try:
        from navig.memory.context_builder import build_context

        return build_context(user_input or "", caller_info, session_id)
    except Exception as exc:
        logger.debug("ContextBuilder unavailable or failed: %s", exc)
        return {
            "conversation_history": [],
            "workspace_notes": [],
            "kb_snippets": [],
            "metadata": {},
        }


def _extract_user_text(messages: list[dict[str, str]]) -> str:
    """Extract the last user message content from the messages list."""
    for m in reversed(messages):
        if m.get("role") == "user":
            return m.get("content", "")
    return ""


def _enrich_messages_with_context(
    messages: list[dict[str, str]],
    context: dict[str, Any],
) -> list[dict[str, str]]:
    """
    Inject context into the message list as a system-level context preamble.

    If context has no meaningful content, returns messages unchanged.
    The context is prepended as an additional system message so that
    the model can reference conversation history, KB snippets, etc.
    """
    parts: list[str] = []

    # Conversation history
    history = context.get("conversation_history", [])
    if history:
        lines = ["## Recent Conversation"]
        for msg in history:
            role = msg.get("role", "user").capitalize()
            content = msg.get("content", "")
            if len(content) > 300:
                content = content[:297] + "..."
            lines.append(f"{role}: {content}")
        parts.append("\n".join(lines))

    # KB snippets
    snippets = context.get("kb_snippets", [])
    if snippets:
        lines = ["## Relevant Knowledge"]
        for s in snippets:
            key = s.get("key", "")
            content = s.get("content", "")
            if key:
                lines.append(f"### {key}")
            lines.append(content[:500] if len(content) > 500 else content)
        parts.append("\n".join(lines))

    # Key facts (stored in English, translated at response time)
    key_facts = context.get("key_facts", "")
    if key_facts:
        key_facts_text = str(key_facts).strip()
        if key_facts_text:
            parts.append(
                "\n".join(
                    [
                        "The following context is in English. Use it to answer, but reply in the user's detected language.",
                        key_facts_text,
                    ]
                )
            )

    # Workspace notes
    notes = context.get("workspace_notes", [])
    if notes:
        lines = ["## Workspace Notes"]
        for n in notes:
            lines.append(n[:500] if len(n) > 500 else n)
        parts.append("\n".join(lines))

    if not parts:
        return messages

    context_text = "\n\n---\n\n".join(parts)
    context_msg: dict[str, str] = {
        "role": "system",
        "content": f"# Context\n\n{context_text}",
    }

    # Insert context as the second message if first is system, else prepend
    enriched = list(messages)
    if enriched and enriched[0].get("role") == "system":
        enriched.insert(1, context_msg)
    else:
        enriched.insert(0, context_msg)

    return enriched


def _has_llm_modes_config() -> bool:
    """Check if llm_modes is configured in config.yaml."""
    try:
        from navig.config import get_config_manager

        cm = get_config_manager()
        raw = cm.global_config or {}
        return "llm_modes" in raw or "llm_router" in raw
    except Exception:
        return False


def _parse_model_spec(spec: str, provider_override: str | None = None) -> tuple:
    """Parse 'provider:model' or just 'model' spec."""
    if provider_override:
        return provider_override, spec

    if ":" in spec and not spec.startswith("http"):
        parts = spec.split(":", 1)
        return parts[0], parts[1]

    # Try to infer provider from model name
    spec_lower = spec.lower()
    if spec_lower.startswith("gpt-") or spec_lower.startswith("o1"):
        return "openai", spec
    if spec_lower.startswith("claude"):
        return "anthropic", spec
    # An 'org/model' id is OpenRouter-style — MUST be checked before the bare-name
    # substring heuristics below, else "meta-llama/llama-3.3-70b-instruct" and
    # "deepseek/deepseek-chat" get misrouted to ollama/deepseek (localhost / wrong
    # model id). Matches the peer parsers in providers/{fallback,inference}.py.
    if "/" in spec:
        return "openrouter", spec
    if "deepseek" in spec_lower:
        return "deepseek", spec
    if "llama" in spec_lower or "phi" in spec_lower or "qwen" in spec_lower:
        return "ollama", spec

    return "openrouter", spec


def _with_base_url(provider_cfg, base_url):
    """Return a COPY of a builtin ProviderConfig with ``base_url`` overridden.

    ``get_builtin_provider`` returns the shared ``BUILTIN_PROVIDERS`` singleton;
    assigning ``base_url`` on it in place would permanently rewrite that provider's
    endpoint for every other call/thread in the process (e.g. one request routed
    through an OpenAI-compatible proxy would silently repoint *all* subsequent
    'openai' calls). Copy first, then override.
    """
    import copy as _copy

    cfg = _copy.copy(provider_cfg)
    cfg.base_url = base_url
    return cfg


def _safe_run_async(func):
    """Run an async function cleanly, even if we are already in an event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(func())

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(func())).result()


def _resolve_dispatch_credential(
    provider: str, connection_id: str | None = None
) -> tuple[str | None, str | None]:
    """Resolve ``(api_key, oauth_token)`` for a dispatch call.

    Prefers a routable NAVIG connection (a claude-max OAuth subscription, or a
    connection-stored key) and falls back to the shared AuthProfileManager key
    store. Exactly one element is set on success; ``(None, None)`` when nothing
    is configured. This is the single seam that lets every LLM call — agents,
    modes, gateway — use the operator's connections instead of only env/vault.

    *connection_id* pins a specific connection (account) — used by the fallback
    layer to rotate across sibling subscriptions of the same provider.
    """
    try:
        from navig.providers.inference import resolve_provider_credential

        return resolve_provider_credential(provider, connection_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("dispatch credential resolution failed for %s: %s", provider, exc)
        return None, None


def _call_provider(
    provider: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 4096,
    timeout: float = 120.0,
    base_url: str | None = None,
) -> str:
    """Call an LLM provider using the providers system."""
    _LOCAL_PROVIDERS = {"ollama", "llamacpp", "airllm", "mcp_bridge"}
    if provider not in _LOCAL_PROVIDERS:
        _guard_key, _guard_oauth = _resolve_dispatch_credential(provider)
        if not _guard_key and not _guard_oauth:
            raise ValueError(
                f"No credential configured for provider '{provider}'. "
                f"Connect one with `navig connect`, set the env var, or run: navig vault set {provider}"
            )
    try:
        return _call_via_providers_system(
            provider,
            model,
            messages,
            temperature,
            max_tokens,
            timeout,
            base_url,
        )
    except Exception as e:
        # If the FIRST attempt timed out, the endpoint is slow/unresponsive —
        # re-running the identical request over httpx-direct would just burn
        # another full timeout (this is what turned one stuck heartbeat into a
        # ~4-minute 120s+120s stall). Only the providers-system *transport*
        # quirks (empty-message errors that httpx-direct genuinely recovers)
        # are worth a second attempt.
        try:
            import httpx as _httpx

            _cause = getattr(e, "__cause__", None)
            _is_timeout = isinstance(e, _httpx.TimeoutException) or isinstance(
                _cause, _httpx.TimeoutException
            )
        except Exception:  # noqa: BLE001
            _is_timeout = "timed out" in str(e).lower() or "timeout" in str(e).lower()

        if _is_timeout:
            logger.warning(
                "Provider %s timed out after %.0fs — skipping httpx-direct retry "
                "(same endpoint would stall again)",
                provider,
                timeout,
            )
            raise

        # The httpx-direct fallback speaks OpenAI `/chat/completions` + Bearer.
        # anthropic (needs `/v1/messages` + x-api-key / OAuth CLI presentation) and
        # mcp_bridge (ws://) can NEVER satisfy that shape — retrying there only
        # masks the real error with a misleading 404. Surface the original instead.
        if provider in {"anthropic", "mcp_bridge"}:
            raise

        logger.warning("Provider system call failed (%s), trying httpx direct: %s", provider, e)
        return _call_direct_openai_compat(
            provider,
            model,
            messages,
            temperature,
            max_tokens,
            timeout,
            base_url,
        )


def _call_via_providers_system(
    provider: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    timeout: float,
    base_url: str | None,
) -> str:
    """Use the existing navig.providers system for the call."""
    from navig.providers import (
        CompletionRequest,
        Message,
        create_client,
        get_builtin_provider,
    )

    # Get provider config
    provider_cfg = get_builtin_provider(provider)
    if provider_cfg is None:
        # Build a minimal OpenAI-compatible config
        from navig.llm.router import PROVIDER_BASE_URLS
        from navig.providers.types import ModelApi, ProviderConfig

        url = base_url or PROVIDER_BASE_URLS.get(provider, "https://openrouter.ai/api/v1")
        provider_cfg = ProviderConfig(
            name=provider,
            base_url=url,
            api=ModelApi.OPENAI_COMPLETIONS,
        )
    elif base_url:
        provider_cfg = _with_base_url(provider_cfg, base_url)

    # Resolve auth — prefer a routable connection (claude-max OAuth / stored key),
    # fall back to the shared key store.
    api_key, oauth_token = _resolve_dispatch_credential(provider)

    # Build messages
    msgs = [Message(role=m["role"], content=m["content"]) for m in messages]
    request = CompletionRequest(
        messages=msgs,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    # Create client and call
    client = create_client(provider_cfg, api_key=api_key, oauth_token=oauth_token, timeout=timeout)

    async def _run():
        # Close the client INSIDE the same coroutine/event loop that created
        # its httpx.AsyncClient. Closing it from a second _safe_run_async()
        # (a different thread + event loop) could hang or raise "Event loop is
        # closed" while aclose() drained the connection pool — which is exactly
        # the kind of stall that turned a fast heartbeat into a 135s one.
        try:
            return await client.complete(request)
        finally:
            close_fn = getattr(client, "close", None)
            if callable(close_fn):
                try:
                    await close_fn()
                except Exception as close_exc:  # noqa: BLE001
                    logger.debug(
                        "_call_via_providers_system client close skipped: %s", close_exc
                    )

    result = _safe_run_async(_run)
    return result.content or ""


def _call_direct_openai_compat(
    provider: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    timeout: float,
    base_url: str | None,
) -> str:
    """Direct HTTP call for OpenAI-compatible APIs (fallback)."""
    import httpx

    from navig.llm.router import PROVIDER_BASE_URLS

    _LOCAL = {"ollama", "llamacpp", "airllm", "mcp_bridge"}
    api_key, oauth_token = _resolve_dispatch_credential(provider)

    # A subscription (OAuth) connection can't be presented over the generic
    # OpenAI-compatible fallback — surface a real error, don't send it wrong.
    if oauth_token:
        raise ValueError(
            f"Provider '{provider}' is backed by an OAuth subscription that the "
            f"OpenAI-compatible fallback can't route; the primary path failed."
        )
    # Never DEGRADE to an unauthenticated request against a remote provider — that
    # is the 401 'authorization missing' trap. Fail loud with a fixable message.
    if provider not in _LOCAL and not api_key:
        raise ValueError(
            f"No credential for provider '{provider}' — refusing to send a keyless "
            f"request. Connect one with `navig connect` or set its API key."
        )

    url = base_url or PROVIDER_BASE_URLS.get(provider, "https://openrouter.ai/api/v1")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if provider == "openrouter":
        headers["HTTP-Referer"] = "https://navig.run"
        headers["X-Title"] = "NAVIG"

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    # Apply the same provider-specific scrubbing as the canonical client so the
    # fallback path doesn't trip the same serde-rust errors on strict backends.
    try:
        from navig.providers.clients import _sanitize_openai_body
        payload = _sanitize_openai_body(payload, provider_name=provider)
    except Exception:  # noqa: BLE001
        pass  # best-effort; fall back to raw payload

    endpoint = f"{url.rstrip('/')}/chat/completions"
    resp = httpx.post(endpoint, json=payload, headers=headers, timeout=timeout)
    if resp.status_code != 200:
        logger.warning(
            "Direct call to %s returned %d. Payload preview: %s",
            provider, resp.status_code, str(payload)[:500],
        )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _call_legacy(
    messages: list[dict[str, str]],
    model_override: str | None = None,
    timeout: float = 120.0,
) -> str:
    """Last-resort path: direct OpenRouter HTTP call with no provider routing."""
    import requests  # noqa: PLC0415

    from navig.config import get_config_manager  # noqa: PLC0415

    cm = get_config_manager()
    global_cfg = cm.global_config
    ai_key = (
        os.environ.get("OPENROUTER_API_KEY", "").strip()
        or str(global_cfg.get("ai", {}).get("api_key") or "").strip()
        or str(global_cfg.get("openrouter_api_key") or "").strip()
    )

    if not ai_key:
        return "Error: OpenRouter API key not configured."

    model = (
        model_override
        or str(global_cfg.get("ai", {}).get("model") or "").strip()
        or "google/gemini-2.5-flash"
    )

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {ai_key}",
                "HTTP-Referer": "https://github.com/navig-run/core",
                "X-Title": "NAVIG Autonomous Agent",
            },
            json={"model": model, "messages": messages, "temperature": 0.7, "max_tokens": 4096},
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("_call_legacy request failed: %s", exc)
        return f"Error: {exc}"
