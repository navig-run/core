"""ConversationalAgent: DI-orchestrated multi-turn chat sessions for NAVIG gateway."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Protocol

    class AIClientProtocol(Protocol):
        async def chat(self, messages: list[dict]) -> str: ...
        async def chat_routed(
            self, msgs: list[dict], *, user_message: str = "", tier_override: str = ""
        ) -> str: ...
        @property
        def model_router(self) -> object: ...


from navig.agent.conv.status_event import StatusEvent

logger = logging.getLogger(__name__)


class ConversationalAgent:
    """
    Stateful per-session chat agent orchestrating soul, history, language, and execution.
    Owns session metadata: user identity, focus mode, current task, conversation history.
    Guarantees: public API (chat/confirm/get_status) is backward-compatible with all callers.
    """

    def __init__(
        self,
        ai_client: AIClientProtocol | None = None,
        on_status_update: Callable | None = None,
        soul_content: str | None = None,
        *,
        soul_loader=None,
        history=None,
        language_detector=None,
        localization=None,
        task_executor=None,
        fallback_planner=None,
    ) -> None:
        from navig.agent.conv.executor import TaskExecutor
        from navig.agent.conv.history import ConversationHistory
        from navig.agent.conv.language import LanguageDetector
        from navig.agent.conv.localization import LocalizationStore
        from navig.agent.conv.planner import FallbackPlanner, PlanExtractor
        from navig.agent.conv.soul import get_soul_loader

        self._ai_client = ai_client
        self._on_status_update: Callable[[StatusEvent], Awaitable[None]] | None = None
        self._session_id: str = str(uuid.uuid4())[:8]
        self.on_status_update = on_status_update  # triggers property setter — shim applied
        self._soul_loader = soul_loader or get_soul_loader()
        self._history = history or ConversationHistory(user_id="default")
        self._lang = language_detector or LanguageDetector()
        self._loc = loc = localization or LocalizationStore()
        self._executor = task_executor or TaskExecutor(
            on_status_update=self._on_status_update, localization=loc
        )
        self._planner = fallback_planner or FallbackPlanner()
        self._plan_extractor = PlanExtractor()
        if soul_content is not None:
            self._soul_loader.override(soul_content)
        elif self._soul_loader.cached_content is None:
            # Use _sync_load() directly — avoids override() corrupting self._raw
            # with the condensed text (override sets _raw = condensed, not raw).
            self._soul_loader._sync_load()
        self._user_identity: dict[str, str] = {}
        self._active_persona: str = ""
        self._runtime_persona: str = ""
        self._detected_language_hint: str = ""
        self._last_detected_language: str = "en"
        self._session_fallback_language: str = ""
        self._has_text_detected: bool = False
        self._focus_mode = self._last_user_message = self._tier_override = ""
        self._entrypoint, self.context = "channel", {}
        self._plan_context_loaded = False
        # User profile — lazy-loaded from USER.md on first turn (cached for session lifetime)
        self._user_profile_content: str = ""
        self._user_profile_loaded: bool = False

    @property
    def ai_client(self):
        """Backward-compatible alias for ``_ai_client``."""
        return self._ai_client

    @ai_client.setter
    def ai_client(self, value) -> None:
        self._ai_client = value

    @property
    def conversation_history(self) -> list[dict[str, str]]:
        """Backward-compatible list view of the underlying conversation history."""
        return self._history.get_messages()

    @conversation_history.setter
    def conversation_history(self, value: list[dict[str, str]]) -> None:
        """Replace in-memory history from a plain message list (compat API)."""
        if not isinstance(value, list):
            return
        normalized: list[dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", ""))
            content = str(item.get("content", ""))
            if role and content:
                normalized.append({"role": role, "content": content})
        self._history._messages = normalized

    @property
    def current_task(self):
        """Backward-compatible alias for ``self._executor.current_task``."""
        return self._executor.current_task

    @current_task.setter
    def current_task(self, value) -> None:
        self._executor.current_task = value

    @property
    def on_status_update(self) -> Callable[[StatusEvent], Awaitable[None]] | None:
        """Current StatusEvent callback (may be a shim-wrapped compat callable)."""
        return self._on_status_update

    @on_status_update.setter
    def on_status_update(self, cb: Callable | None) -> None:
        """Set callback, applying compat shim if *cb* accepts a plain ``str``."""
        if cb is not None:
            try:
                sig = inspect.signature(cb)
                params = list(sig.parameters.values())
                if params:
                    ann = params[0].annotation
                    # Compatibility detection: annotation is str type or the string 'str'
                    # (from __future__ import annotations makes annotations strings at runtime)
                    is_compat = (
                        ann is str
                        or ann == "str"
                        or (ann is inspect.Parameter.empty and len(params) == 1)
                    )
                    if is_compat:
                        _compat = cb
                        if inspect.iscoroutinefunction(_compat) or inspect.iscoroutinefunction(
                            getattr(_compat, "__call__", None)  # noqa: B004
                        ):

                            async def cb(event: StatusEvent, _cb: Callable = _compat) -> None:  # noqa: E731
                                await _cb(event.message)

                        else:

                            def cb(event: StatusEvent, _cb: Callable = _compat) -> None:  # noqa: E731
                                _cb(event.message)

            except (ValueError, TypeError):
                pass  # malformed or missing value; skip
        self._on_status_update = cb  # type: ignore[assignment]
        # Sync executor if already initialised (handles post-__init__ assignment)
        if hasattr(self, "_executor"):
            self._executor._notify_cb = self._on_status_update

    async def _emit_event(self, event: StatusEvent) -> None:
        """Fire the StatusEvent callback; guards against None, awaits coroutines, swallows errors."""
        cb = self._on_status_update
        if cb is None:
            return
        try:
            # Use inspect (not deprecated asyncio.iscoroutinefunction) and also
            # detect callable class instances whose __call__ is async.
            is_coro_fn = inspect.iscoroutinefunction(cb) or inspect.iscoroutinefunction(
                getattr(cb, "__call__", None)  # noqa: B004
            )
            if is_coro_fn:
                await cb(event)
            else:
                result = cb(event)
                if asyncio.iscoroutine(result):
                    await result
        except Exception as exc:
            logger.warning("StatusEvent callback error: %s", exc)

    @staticmethod
    def load_soul_content() -> str:
        """Return the raw SOUL.md string, forcing a synchronous load if not yet cached.

        Used by callers that need the soul text before any async context is
        available (e.g. CLI pre-startup checks). Returns an empty string if
        SOUL.md cannot be found on any search path.
        """
        from navig.agent.conv.soul import get_soul_loader

        loader = get_soul_loader()
        if loader.cached_content is None:
            loader._sync_load()
        return loader.cached_content or ""

    def set_user_identity(self, user_id: str = "", username: str = "") -> None:
        """Attach the operator's identity to this session.

        Both fields are optional; presence of *username* triggers a personalised
        greeting clause in the system prompt via ``_build_awareness_context``.
        """
        self._user_identity = {"user_id": user_id, "username": username}

    def set_active_persona(self, config_or_name=None, soul_content: str | None = None) -> None:
        """Compatibility persona setter kept for older callers."""
        name = ""
        if isinstance(config_or_name, str):
            name = config_or_name.strip()
        elif config_or_name is not None:
            name = str(getattr(config_or_name, "name", "") or "").strip()
        self._active_persona = name
        self._runtime_persona = name
        if soul_content:
            self._soul_loader.override(soul_content)

    def set_language_preferences(
        self,
        detected_language: str = "",
        last_detected_language: str = "",
    ) -> None:
        """Compatibility language-hint setter for channel/session metadata injection."""
        hint = (detected_language or "").strip().lower()
        if hint:
            self._detected_language_hint = hint

        previous = (last_detected_language or "").strip().lower()
        if previous:
            self._session_fallback_language = previous

    def set_focus_mode(self, mode: str) -> None:
        """Switch the agent's mood/focus profile by name (e.g. ``'deep'``, ``'flow'``).

        Resolves the profile via ``navig.agent.soul.get_mood_profile``.
        If the soul module is unavailable or the profile name is unknown,
        silently falls back to ``'balance'`` so the agent remains functional.
        """
        try:
            from navig.agent.soul import get_mood_profile

            self._focus_mode = get_mood_profile(mode).id
        except Exception:
            # Soul module may not be installed in all deployments; fallback is safe.
            self._focus_mode = "balance"

    def _build_awareness_context(self) -> str:
        now = datetime.now().astimezone()
        parts = [f"System time: {now.strftime('%H:%M %Z')}, {now.strftime('%A %d %B %Y')}."]
        if uname := self._user_identity.get("username", ""):
            parts.append(f"You are talking to {uname} (your operator). Address them naturally.")
        elif uid := self._user_identity.get("user_id", ""):
            parts.append(f"User ID: {uid}.")
        # Lazy-load USER.md once per agent session and inject into system prompt
        if not self._user_profile_loaded:
            self._user_profile_loaded = True
            try:
                from navig.workspace import WorkspaceManager

                raw = WorkspaceManager().get_file_content("USER.md") or ""
                self._user_profile_content = raw.strip()
            except Exception:
                pass  # best-effort; no profile is fine
        if self._user_profile_content:
            # Cap profile injection to avoid burning tokens on a large USER.md.
            # 1 500 chars ≈ 375 tokens — enough for preferences & key facts.
            _MAX_PROFILE_CHARS = 1500
            _profile = self._user_profile_content
            if len(_profile) > _MAX_PROFILE_CHARS:
                _profile = _profile[:_MAX_PROFILE_CHARS].rstrip() + " …[profile truncated]"
            parts.append(f"## About the user\n{_profile}")
        # When no recent history survived the session-boundary filter, tell the
        # LLM explicitly to start fresh.  Without this notice the model may
        # invent continuations of the previous session (e.g. sleep reminders).
        # Reset the flag immediately after use so the note only fires once.
        if getattr(self._history, "_session_is_fresh", True):
            try:
                self._history._session_is_fresh = False  # one-shot: first turn only
            except AttributeError:
                pass
            parts.append(
                "IMPORTANT: This is a fresh session — no recent conversation history "
                "is loaded. Greet the user naturally and do NOT reference any previous "
                "topics, reminders, or conversations unless the user explicitly "
                "brings them up first."
            )
        return "\n".join(parts)

    def _normalize_supported_lang_code(self, code: str) -> str:
        normalized = (code or "").strip().lower()
        if not normalized:
            return ""
        mixed_instruction = self._lang.build_instruction("mixed")
        candidate_instruction = self._lang.build_instruction(normalized)
        if normalized != "mixed" and candidate_instruction == mixed_instruction:
            return ""
        return normalized

    def _resolve_prompt_language(self, user_message: str) -> str:
        detected = self._normalize_supported_lang_code(self._lang.detect(user_message))
        if detected and detected != "mixed":
            self._has_text_detected = True
            self._last_detected_language = detected

        hint = self._normalize_supported_lang_code(self._detected_language_hint)
        session_fallback = self._normalize_supported_lang_code(self._session_fallback_language)
        last_detected = self._normalize_supported_lang_code(self._last_detected_language)

        for candidate in (hint, detected, last_detected, session_fallback, "en"):
            if candidate:
                return candidate
        return "en"

    def _build_system_prompt(self, user_message: str) -> str:
        code = self._resolve_prompt_language(user_message)
        return self._soul_loader.build_system_prompt(
            soul=self._soul_loader.cached_content or "",
            lang_instruction=self._lang.build_instruction(code),
            awareness=self._build_awareness_context(),
        )

    async def chat(self, message: str, tier_override: str = "") -> str:
        """Process one user turn end-to-end and return the agent's reply string.

        Sequence:
          1. Detect language; add user message to history.
          2. Call ``_get_ai_response`` (routing → AI client).
          3. Optionally shape the response through the soul's mood profile.
          4. Strip CJK from Russian responses (anti-bleed guard).
          5. If the response contains an executable plan, hand off to executor;
             otherwise append raw response to history and return it.

        *tier_override* is forwarded to the routing layer to force a specific
        LLM tier (e.g. ``'large'``).
        """
        self._last_user_message, self._tier_override = message, tier_override
        self._history.add("user", message)
        response = await self._get_ai_response(message)
        try:
            from navig.agent.soul import ContextSignal, get_mood_profile, shape_response

            response = shape_response(
                response,
                ContextSignal.build(message),
                get_mood_profile(self._focus_mode),
            )
        except Exception:
            # Soul shaping is best-effort; the raw response is still valid output.
            pass
        plan = self._plan_extractor.extract(response)
        if plan:
            result = await self._executor.execute_plan(plan)
            self._history.add("assistant", result)
            return result
        self._history.add("assistant", response)
        return response

    async def run_agentic(
        self,
        message: str,
        max_iterations: int = 90,
        toolset: str | list[str] = "core",
        cost_tracker=None,
        approval_policy=None,
    ) -> str:
        """Native ReAct multi-step tool-calling loop.

        This is the canonical agentic path for ``conv`` callers and mirrors the
        established behavior while using this class' state model.
        """
        from navig.agent.agent_tool_registry import _AGENT_REGISTRY
        from navig.agent.tools import register_all_tools
        from navig.agent.usage_tracker import CostTracker, IterationBudget, UsageEvent
        from navig.providers import CompletionRequest, Message, create_client, get_builtin_provider
        from navig.providers.auth import AuthProfileManager
        from navig.providers.clients import ToolDefinition

        if not getattr(self, "_agentic_tools_registered", False):
            try:
                register_all_tools()
                self._agentic_tools_registered = True
            except Exception as exc:
                logger.warning("run_agentic: tool registration failed: %s", exc)

        budget = IterationBudget(max_iterations=max_iterations)
        if (
            cost_tracker is not None
            and hasattr(cost_tracker, "record")
            and hasattr(cost_tracker, "session_cost")
        ):
            tracker = cost_tracker
        else:
            tracker = CostTracker()

        if approval_policy is not None:
            try:
                from navig.tools.approval import set_approval_policy

                set_approval_policy(approval_policy)
            except Exception as exc:
                logger.debug("Exception suppressed: %s", exc)

        explicit_toolsets = [toolset] if isinstance(toolset, str) else list(toolset)
        try:
            from navig.llm_router import suggest_toolsets

            suggested = suggest_toolsets(user_input=message)
            merged = list(explicit_toolsets)
            for suggested_toolset in suggested:
                if suggested_toolset not in merged:
                    merged.append(suggested_toolset)
            toolsets = merged
            logger.debug(
                "F-20 semantic routing: explicit=%s suggested=%s → merged=%s",
                explicit_toolsets,
                suggested,
                toolsets,
            )
        except Exception:
            toolsets = explicit_toolsets

        raw_schemas = _AGENT_REGISTRY.get_openai_schemas(toolsets=toolsets)
        tool_defs: list[ToolDefinition] = [
            ToolDefinition(
                name=schema["function"]["name"],
                description=schema["function"].get("description", ""),
                parameters=schema["function"].get(
                    "parameters", {"type": "object", "properties": {}}
                ),
            )
            for schema in raw_schemas
        ]

        provider_name = "openrouter"
        model_name = "openai/gpt-4o"
        temperature = 0.7
        max_tokens = 4096
        base_url: str | None = None
        try:
            from navig.llm_router import resolve_llm

            resolved = resolve_llm(mode="coding")
            provider_name = resolved.provider
            model_name = resolved.model
            temperature = resolved.temperature
            max_tokens = resolved.max_tokens
            base_url = resolved.base_url
        except Exception as exc:
            logger.debug("Exception suppressed: %s", exc)

        try:
            provider_cfg = get_builtin_provider(provider_name)
            if provider_cfg is None:
                from navig.llm_router import PROVIDER_BASE_URLS
                from navig.providers.types import ModelApi, ProviderConfig

                url = base_url or PROVIDER_BASE_URLS.get(provider_name, "https://openrouter.ai/api/v1")
                provider_cfg = ProviderConfig(
                    name=provider_name,
                    base_url=url,
                    api=ModelApi.OPENAI_COMPLETIONS,
                )
            auth_mgr = AuthProfileManager()
            api_key, _ = auth_mgr.resolve_auth(provider_name)
            client = create_client(provider_cfg, api_key=api_key, timeout=120.0)
        except Exception as exc:
            logger.error("run_agentic: could not create LLM client: %s", exc)
            return "Sorry, I couldn't initialise the LLM client for agentic mode."

        self._last_user_message = message
        system_prompt = self._build_system_prompt(message)

        try:
            from navig.plans.context import PlanContext
            from navig.spaces.resolver import get_default_space

            space_name = get_default_space()
            plan_context = PlanContext()
            snapshot = plan_context.gather(space_name)
            plan_block = plan_context.format_for_prompt(snapshot)
            if plan_block:
                system_prompt += "\n\n" + plan_block
            non_null = sum(1 for key, value in snapshot.items() if key != "errors" and value is not None)
            logger.info("plan_context_loaded space=%s non_null_keys=%d", space_name, non_null)
        except Exception as plan_exc:
            logger.debug("plan context injection skipped (agentic): %s", plan_exc)

        toolset_names = _AGENT_REGISTRY.available_names(toolsets=toolsets)
        if toolset_names:
            displayed = ", ".join(f"`{name}`" for name in toolset_names[:20])
            system_prompt += (
                "\n\n## Agentic Mode\n"
                f"You have access to the following tools: {displayed}.\n"
                "Use them step-by-step to fulfill the user's request, then give a final reply."
            )

        history_messages = list(self.conversation_history)
        working_messages: list[Message] = [
            Message(role="system", content=system_prompt),
            *[
                Message(
                    role=history_message["role"],
                    content=history_message.get("content", ""),
                    tool_call_id=history_message.get("tool_call_id"),
                    tool_calls=history_message.get("tool_calls"),
                )
                for history_message in history_messages
            ],
            Message(role="user", content=message),
        ]

        final_response = ""
        turn = 0
        past_tool_calls_this_turn: list[list[tuple[str, str]]] = []

        compressor = None
        try:
            from navig.agent.context_compressor import ContextCompressor

            compressor = ContextCompressor()
        except Exception as exc:
            logger.debug("Exception suppressed: %s", exc)

        while not budget.is_exhausted():
            turn += 1
            budget.consume(1)

            if compressor is not None and turn > 3:
                try:
                    msg_dicts = [
                        {
                            "role": msg.role,
                            "content": msg.content or "",
                            "tool_call_id": getattr(msg, "tool_call_id", None),
                            "tool_calls": getattr(msg, "tool_calls", None),
                        }
                        for msg in working_messages
                    ]
                    compressed = compressor.maybe_compress(msg_dicts, model=model_name)
                    if compressed is not msg_dicts:
                        working_messages = [
                            Message(
                                role=msg_dict["role"],
                                content=msg_dict.get("content", ""),
                                tool_call_id=msg_dict.get("tool_call_id"),
                                tool_calls=msg_dict.get("tool_calls"),
                            )
                            for msg_dict in compressed
                        ]
                except Exception as exc:
                    logger.debug("Context compression skipped: %s", exc)

            pct = budget.budget_used_pct()
            tool_choice: str | None = "auto"
            if pct >= 0.90:
                tool_choice = "none"
            elif pct >= 0.70:
                working_messages.append(
                    Message(
                        role="system",
                        content=(
                            "[Budget warning: >70% of iteration budget used. "
                            "Please finalise your response now.]"
                        ),
                    )
                )

            request = CompletionRequest(
                messages=working_messages,
                model=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tool_defs if (tool_choice != "none" and tool_defs) else None,
                tool_choice=tool_choice if tool_choice != "none" else None,
            )

            try:
                response = await client.complete(request)
            except Exception as exc:
                logger.error("run_agentic: LLM call failed on turn %d: %s", turn, exc)
                final_response = f"Error during agentic execution: {exc}"
                break

            usage = response.usage or {}
            try:
                tracker.record(
                    UsageEvent(
                        turn=turn,
                        model=model_name,
                        provider=provider_name,
                        prompt_tokens=usage.get("prompt_tokens", 0),
                        completion_tokens=usage.get("completion_tokens", 0),
                        cache_read_tokens=usage.get("cache_read_input_tokens", 0),
                        cache_write_tokens=usage.get("cache_creation_input_tokens", 0),
                    )
                )
            except Exception as exc:
                logger.debug("Exception suppressed: %s", exc)

            if not response.tool_calls:
                final_response = response.content or ""
                working_messages.append(Message(role="assistant", content=final_response))
                break

            current_calls = [(tool_call.name, tool_call.arguments) for tool_call in response.tool_calls]
            if current_calls in past_tool_calls_this_turn:
                logger.warning(
                    "Duplicate tool calls detected in recent history. Breaking to prevent infinite loop."
                )
                final_response = response.content or "[Agent halted to prevent duplicate tool call loop]"
                break
            past_tool_calls_this_turn.append(current_calls)

            assistant_tool_calls_raw = [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {"name": tool_call.name, "arguments": tool_call.arguments},
                }
                for tool_call in (response.tool_calls or [])
            ]
            working_messages.append(
                Message(
                    role="assistant",
                    content=response.content or "",
                    tool_calls=assistant_tool_calls_raw,
                )
            )

            from navig.agent.toolsets import is_parallel_safe

            pending_calls = response.tool_calls or []

            async def _dispatch_single(tool_call_item):
                try:
                    args = (
                        json.loads(tool_call_item.arguments)
                        if isinstance(tool_call_item.arguments, str)
                        else (tool_call_item.arguments or {})
                    )
                except json.JSONDecodeError:
                    args = {}

                try:
                    from navig.tools.approval import (
                        ApprovalDecision,
                        get_approval_gate,
                        needs_approval,
                    )

                    if needs_approval(tool_call_item.name):
                        gate = get_approval_gate()
                        decision = await gate.check(
                            tool_name=tool_call_item.name,
                            safety_level="moderate",
                            reason="agentic",
                        )
                        if decision == ApprovalDecision.DENIED:
                            return (
                                tool_call_item.id,
                                f"[Denied: operator did not approve '{tool_call_item.name}']",
                            )
                except Exception as exc:
                    logger.debug("Exception suppressed: %s", exc)

                try:
                    from navig.agent.speculative import get_speculative_executor

                    spec = get_speculative_executor()
                    if spec is not None:
                        result_str = spec.execute(tool_call_item.name, args)
                    else:
                        result_str = _AGENT_REGISTRY.dispatch(tool_call_item.name, args)
                except Exception as exc:
                    result_str = f"[Tool error: {exc}]"
                return (tool_call_item.id, result_str)

            parallel_batch = []
            sequential_batch = []
            for tool_call in pending_calls:
                if is_parallel_safe(tool_call.name) and len(pending_calls) > 1:
                    parallel_batch.append(tool_call)
                else:
                    sequential_batch.append(tool_call)

            collected_results: list[tuple[str, str]] = []

            if parallel_batch:
                max_parallel = 8
                sem = asyncio.Semaphore(max_parallel)

                async def _sem_dispatch(tool_call_item, _sem=sem):
                    async with _sem:
                        return await _dispatch_single(tool_call_item)

                par_results = await asyncio.gather(
                    *[_sem_dispatch(tool_call_item) for tool_call_item in parallel_batch],
                    return_exceptions=True,
                )
                for idx, result in enumerate(par_results):
                    if isinstance(result, BaseException):
                        collected_results.append((parallel_batch[idx].id, f"[Tool error: {result}]"))
                    else:
                        collected_results.append(result)

            for tool_call in sequential_batch:
                collected_results.append(await _dispatch_single(tool_call))

            id_to_result = dict(collected_results)
            for tool_call in pending_calls:
                working_messages.append(
                    Message(
                        role="tool",
                        content=id_to_result.get(tool_call.id, "[Tool error: result missing]"),
                        tool_call_id=tool_call.id,
                    )
                )

        if not final_response:
            final_response = "Agent iteration budget exhausted without producing a final response."

        history_messages.append({"role": "user", "content": message})
        history_messages.append({"role": "assistant", "content": final_response})
        self.conversation_history = self._truncate_history(history_messages, max_messages=20)

        try:
            from navig.agent.speculative import get_speculative_executor, reset_speculative_executor

            spec = get_speculative_executor()
            if spec is not None:
                await spec.cancel_speculations()
                stats = spec.stats
                cache_stats = stats.get("cache") or {}
                if cache_stats.get("hits", 0) > 0:
                    logger.info(
                        "speculative cache stats: hits=%d misses=%d hit_rate=%.1f%%",
                        cache_stats.get("hits", 0),
                        cache_stats.get("misses", 0),
                        float(cache_stats.get("hit_rate", 0.0)) * 100,
                    )
                reset_speculative_executor()
        except Exception as exc:
            logger.debug("Exception suppressed during speculative cache integration reset: %s", exc)

        cost = tracker.session_cost()
        logger.info("run_agentic completed: %s", cost.summary_str())
        return final_response

    @staticmethod
    def _truncate_history(
        history: list[dict[str, str]], max_messages: int = 20
    ) -> list[dict[str, str]]:
        """Safely truncate conversation history preserving tool-call boundaries."""
        if len(history) <= max_messages:
            return history

        idx = len(history) - max_messages
        while idx < len(history):
            if history[idx].get("role") == "user":
                if (
                    idx == 0
                    or history[idx - 1].get("role") != "assistant"
                    or not history[idx - 1].get("tool_calls")
                ):
                    return history[idx:]
            idx += 1

        return history[len(history) - max_messages :]

    async def confirm(self, confirmed: bool) -> str:
        """Accept or reject the pending task that was left in ``PLANNING`` state.

        Returns a status string in all cases:
         - ``True`` → executes the task and returns the result.
         - ``False`` → cancels the task and returns a cancellation message.
         - No pending task → returns a 'nothing to confirm' message.
        """
        from navig.agent.conv.executor import TaskStatus

        task = self._executor.current_task
        if task is None or task.status != TaskStatus.PLANNING:
            return "No pending task to confirm."
        if confirmed:
            result = await self._executor.execute(task)
            self._history.add("assistant", result)
            return result
        task.status = TaskStatus.CANCELLED
        self._executor.current_task = None
        return "Task cancelled. What else can I help with? 😊"

    def get_status(self) -> str:
        """Return a human-readable one-liner describing the agent's current state.

        If a task is in progress, reports the goal, status, and step progress.
        Otherwise signals that the agent is idle and ready.
        """
        task = self._executor.current_task
        if task is not None:
            return f"Working on: {task.goal}\nStatus: {task.status.name}\nProgress: {task.current_step + 1}/{len(task.plan)}"
        return "Ready and waiting for your next task! 🤖"

    async def _get_ai_response(self, message: str) -> str:
        if self._ai_client is None:
            result = self._planner.plan(message)
            return json.dumps(result) if result else "I'm ready to help."

        # Track whether the *legacy* ai_client has a detected provider.
        # When False we skip chat_stream / chat_routed / chat (which rely on
        # the legacy client's provider), but we **still try the UnifiedRouter**
        # which discovers providers directly from config (including the user's
        # Telegram /models selection written to llm_router.llm_modes) and has
        # its own per-provider availability checks.  Previously this was an
        # early-return gate that blocked NVIDIA (and any other provider
        # configured via Telegram) from ever being reached.
        ai_available: bool = True
        try:
            if hasattr(self._ai_client, "is_available") and not self._ai_client.is_available():
                ai_available = False
        except Exception:
            pass  # best-effort; failure is non-critical

        def _deterministic_fallback() -> str:
            result = self._planner.plan(message)
            return json.dumps(result) if result else "I'm ready to help."

        system_prompt = self._build_system_prompt(message)
        msgs = [
            {"role": "system", "content": system_prompt},
            *self._history.get_messages(),
        ]
        # ── Lazy plan context injection ──
        if not self._plan_context_loaded:
            try:
                from navig.plans.context import PlanContext
                from navig.spaces.resolver import get_default_space

                active_space = get_default_space()
                space_name = active_space
                pc = PlanContext()
                snapshot = pc.gather(space_name)
                if snapshot:
                    self.context["plan_context"] = snapshot
                    non_null = sum(
                        1
                        for key in (
                            "current_phase",
                            "dev_plan",
                            "wiki",
                            "docs",
                            "inbox_unread",
                            "mcp_resources",
                        )
                        if snapshot.get(key) is not None
                    )
                    logger.info(
                        "plan_context_loaded space=%s non_null_keys=%d",
                        space_name,
                        non_null,
                    )
                self._plan_context_loaded = True
            except Exception as exc:
                logger.debug("plan context injection skipped: %s", exc)
                self._plan_context_loaded = True

        if self.context:
            msgs[0]["content"] += f"\nContext: {json.dumps(self.context)}"
        tier = self._tier_override
        await self._emit_event(
            StatusEvent(
                type="thinking",
                task_id=self._session_id,
                message="Thinking\u2026",
                timestamp=datetime.now(),
            )
        )
        # Optional streaming path — only safe when the legacy AIClient's detected
        # provider matches the user's config-active provider.
        # If the user activated NVIDIA via /models but GITHUB_TOKEN is in env,
        # ai_client.provider becomes "github_models" while ai.default_provider is
        # "nvidia" — skip chat_stream so the UnifiedRouter can reach NVIDIA.
        _config_default_provider = ""
        try:
            from navig.config import get_config_manager as _gcm

            _config_default_provider = (
                (_gcm().global_config or {}).get("ai") or {}
            ).get("default_provider", "")
        except Exception:  # noqa: BLE001
            pass

        _legacy_provider = getattr(self._ai_client, "provider", "")
        _skip_stream = bool(
            _config_default_provider
            and _legacy_provider
            and _config_default_provider != _legacy_provider
        )

        if ai_available and not _skip_stream and hasattr(self._ai_client, "chat_stream"):
            try:
                tokens: list[str] = []
                async for token in self._ai_client.chat_stream(
                    msgs, user_message=message, tier_override=tier
                ):  # type: ignore[union-attr]
                    tokens.append(token)
                    await self._emit_event(
                        StatusEvent(
                            type="streaming_token",
                            task_id=self._session_id,
                            message="",
                            timestamp=datetime.now(),
                            metadata={"token": token},
                        )
                    )
                if tokens:
                    return "".join(tokens)
            except Exception as exc:
                logger.warning("chat_stream failed, falling through to UnifiedRouter: %s", exc)

        # Unified Router — always tried regardless of legacy client state.
        # _discover_user_providers() reads config["llm_router"]["llm_modes"] and
        # config["ai"]["default_provider"] (both written by Telegram /models), so
        # user's provider selection is respected even when ai_client.provider=="none".
        try:
            from navig.routing.router import RouteRequest, get_router

            _req_meta: dict[str, Any] = {}
            _sto = getattr(self, "_session_tier_overrides", None)
            if _sto:
                _req_meta["session_tier_overrides"] = _sto

            text = (
                await get_router().run(
                    RouteRequest(
                        messages=msgs,
                        text=message,
                        tier_override=tier,
                        entrypoint=self._entrypoint,
                        metadata=_req_meta or None,
                    )
                )
            )[0]
            if text:
                return text
        except Exception as exc:
            exc_msg = str(exc)
            if "no provider available" in exc_msg.lower() or "no ai provider" in exc_msg.lower():
                logger.warning("Unified router: all providers failed: %s", exc_msg)
                return (
                    "\u26a0\ufe0f I couldn't reach any AI provider right now.\n"
                    "Check /provider to confirm your selection and verify the API key "
                    "is stored correctly (vault or env var)."
                )
            logger.warning("Unified router failed: %s", exc)
        if ai_available and hasattr(self._ai_client, "chat_routed"):
            try:
                return await self._ai_client.chat_routed(
                    msgs, user_message=message, tier_override=tier
                )
            except Exception as exc:
                msg = str(exc).lower()
                if "no ai provider available" in msg or "no provider available" in msg:
                    return _deterministic_fallback()
                raise
        if ai_available:
            try:
                return await self._ai_client.chat(msgs)
            except Exception as exc:
                msg = str(exc).lower()
                if "no ai provider available" in msg or "no provider available" in msg:
                    return _deterministic_fallback()
                raise
        return _deterministic_fallback()
