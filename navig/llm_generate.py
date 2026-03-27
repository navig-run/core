"""
LLM Generate — Unified LLM call interface using the mode router.

This is the primary integration point. All NAVIG subsystems should call:

    from navig.llm_generate import llm_generate
    response = llm_generate(mode="coding", messages=[...])

Or use the new typed orchestrator:

    from navig.llm_generate import run_llm
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
from typing import Any

logger = logging.getLogger("navig.llm_generate")


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
    timeout: float = 120.0,
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
    if model_override:
        # Direct model specification — skip router
        provider, model = _parse_model_spec(model_override, provider_override)
        return _call_provider(
            provider=provider,
            model=model,
            messages=messages,
            temperature=temperature or 0.7,
            max_tokens=max_tokens or 4096,
            timeout=timeout,
        )

    from navig.llm_router import resolve_llm

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
    timeout: float = 120.0,
    fallback_models: list[str] | None = None,
    caller_info: dict[str, Any] | None = None,
    session_id: str | None = None,
    enable_tools: bool = False,
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
    from navig.llm_routing_types import ModelSelection

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
            temperature=temperature or 0.7,
            max_tokens=max_tokens or 4096,
            strategy_name="model_override",
        )
    else:
        from navig.llm_router import resolve_llm

        resolved = resolve_llm(
            mode=mode,
            user_input=user_input,
            prefer_uncensored=prefer_uncensored,
        )
        selection = ModelSelection(
            provider_name=resolved.provider,
            model_name=resolved.model,
            temperature=(
                temperature if temperature is not None else resolved.temperature
            ),
            max_tokens=max_tokens if max_tokens is not None else resolved.max_tokens,
            base_url=resolved.base_url,
            api_key_env=resolved.api_key_env,
            is_uncensored=resolved.is_uncensored,
            strategy_name="llm_router",
            metadata={"mode": resolved.mode, "reason": resolved.resolution_reason},
        )

    logger.debug(
        "run_llm routing: %s -> %s:%s (strategy: %s)",
        selection.metadata.get("mode", mode),
        selection.provider_name,
        selection.model_name,
        selection.strategy_name,
    )

    # --- Step 2: Build prompt with context ---
    enriched_messages = _enrich_messages_with_context(messages, context)

    # --- Step 3: Dispatch with optional fallback ---
    if fallback_models:
        result = _call_with_fallback(
            messages=enriched_messages,
            selection=selection,
            fallback_models=fallback_models,
            timeout=timeout,
        )
    else:
        result = _call_and_wrap(
            messages=enriched_messages,
            selection=selection,
            timeout=timeout,
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
) -> LLMResult:
    """Dispatch a single LLM call and wrap the response in LLMResult."""
    from navig.llm_routing_types import LLMResult

    try:
        content = _call_provider(
            provider=selection.provider_name,
            model=selection.model_name,
            messages=messages,
            temperature=selection.temperature,
            max_tokens=selection.max_tokens,
            timeout=timeout,
            base_url=selection.base_url or None,
        )
        return LLMResult(
            content=content,
            model=selection.model_name,
            provider=selection.provider_name,
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


def _call_with_fallback(
    messages: list[dict[str, str]],
    selection: ModelSelection,
    fallback_models: list[str],
    timeout: float,
) -> LLMResult:
    """Dispatch with FallbackManager if available, else manual retry chain."""
    from navig.llm_routing_types import LLMResult

    # Try FallbackManager (providers system)
    try:
        from navig.providers.fallback import complete_with_fallback

        result = _safe_run_async(
            lambda: complete_with_fallback(
                messages=messages,
                model=f"{selection.provider_name}:{selection.model_name}",
                fallback_models=fallback_models,
                temperature=selection.temperature,
                max_tokens=selection.max_tokens,
            )
        )
        return LLMResult(
            content=result.response.content or "",
            model=result.model_used,
            provider=result.provider_used,
            prompt_tokens=result.response.prompt_tokens,
            completion_tokens=result.response.completion_tokens,
            is_fallback=result.attempts > 1,
            attempts=result.attempts,
        )
    except Exception as e:
        logger.debug("FallbackManager not available or failed: %s", e)

    # Manual fallback chain
    all_specs = [f"{selection.provider_name}:{selection.model_name}"] + fallback_models
    last_error = None
    for i, spec in enumerate(all_specs):
        provider, model = _parse_model_spec(spec)
        try:
            content = _call_provider(
                provider=provider,
                model=model,
                messages=messages,
                temperature=selection.temperature,
                max_tokens=selection.max_tokens,
                timeout=timeout,
            )
            return LLMResult(
                content=content,
                model=model,
                provider=provider,
                is_fallback=(i > 0),
                attempts=i + 1,
            )
        except Exception as e:
            last_error = e
            logger.warning(
                "Fallback candidate %d (%s:%s) failed: %s", i, provider, model, e
            )

    return LLMResult(
        content="",
        model="",
        provider="",
        finish_reason=f"all_fallbacks_failed:{last_error}",
        attempts=len(all_specs),
    )


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
            "require_confirmation": list(
                getattr(tools_cfg, "require_confirmation", [])
            ),
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
    if "deepseek" in spec_lower:
        return "deepseek", spec
    if "llama" in spec_lower or "phi" in spec_lower or "qwen" in spec_lower:
        return "ollama", spec
    if "/" in spec:
        return "openrouter", spec

    return "openrouter", spec


def _safe_run_async(func):
    """Run an async function cleanly, even if we are already in an event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(func())

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(func())).result()


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
        logger.warning(
            "Provider system call failed (%s), trying httpx direct: %s", provider, e
        )
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
    from navig.providers.auth import AuthProfileManager

    # Get provider config
    provider_cfg = get_builtin_provider(provider)
    if provider_cfg is None:
        # Build a minimal OpenAI-compatible config
        from navig.llm_router import PROVIDER_BASE_URLS
        from navig.providers.types import ModelApi, ProviderConfig

        url = base_url or PROVIDER_BASE_URLS.get(
            provider, "https://openrouter.ai/api/v1"
        )
        provider_cfg = ProviderConfig(
            name=provider,
            base_url=url,
            api=ModelApi.OPENAI_COMPLETIONS,
        )
    elif base_url:
        provider_cfg.base_url = base_url

    # Resolve auth
    auth_manager = AuthProfileManager()
    api_key, auth_source = auth_manager.resolve_auth(provider)

    # Build messages
    msgs = [Message(role=m["role"], content=m["content"]) for m in messages]
    request = CompletionRequest(
        messages=msgs,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    # Create client and call
    client = create_client(provider_cfg, api_key=api_key, timeout=timeout)

    async def _run():
        return await client.complete(request)

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

    from navig.llm_router import PROVIDER_BASE_URLS, _resolve_api_key

    url = base_url or PROVIDER_BASE_URLS.get(provider, "https://openrouter.ai/api/v1")
    api_key = _resolve_api_key(provider)

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

    endpoint = f"{url.rstrip('/')}/chat/completions"
    resp = httpx.post(endpoint, json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _call_legacy(
    messages: list[dict[str, str]],
    model_override: str | None = None,
    timeout: float = 120.0,
) -> str:
    """Legacy path using existing AIAssistant / direct OpenRouter."""
    from navig.config import get_config_manager

    cm = get_config_manager()
    ai_key = cm.global_config.get("openrouter_api_key")

    if not ai_key:
        # Try NAVIG_AI_MODEL env var
        env_model = os.environ.get("NAVIG_AI_MODEL")
        if env_model:
            logger.debug("Using NAVIG_AI_MODEL=%s via legacy path", env_model)

    # Use the existing ask_ai_with_context for simplicity
    from navig.ai import ask_ai_with_context

    # Extract system and user messages
    system_prompt = ""
    history = []
    user_msg = ""

    for m in messages:
        if m["role"] == "system":
            system_prompt = m["content"]
        elif m["role"] == "assistant":
            history.append(m)
        elif m["role"] == "user":
            user_msg = m["content"]
            history.append(m)

    # Remove last user message from history (ask_ai_with_context adds it)
    if history and history[-1]["role"] == "user":
        history = history[:-1]

    return ask_ai_with_context(
        prompt=user_msg,
        system_prompt=system_prompt,
        history=history if history else None,
        model=model_override,
    )
