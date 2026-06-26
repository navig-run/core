"""ConversationalAgent: DI-orchestrated multi-turn chat sessions for NAVIG gateway."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

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

# ── Session tunable constants (single source of truth) ───────────────────────────────────────────
# USER.md chars injected into the system prompt per turn (≈ 375 tokens at 4 chars/token)
_USER_PROFILE_MAX_CHARS: int = 1_500
# Plan context snapshot keys that count as "non-null" for the log message
_PLAN_CONTEXT_KEYS: tuple[str, ...] = (
    "current_phase", "dev_plan", "wiki", "docs", "inbox_unread", "mcp_resources",
)
# Plan context TTL — re-fetch after this many seconds so /plans updates propagate
_PLAN_CTX_TTL: float = 300.0
# run_agentic defaults — all numeric knobs in one place
_MAX_ITERATIONS: int = 90            # default ReAct loop budget
_MAX_PARALLEL_TOOLS: int = 8         # semaphore width for fan-out tool calls
_COMPRESS_AFTER_TURN: int = 3        # turns before context compression starts
_BUDGET_WARN_PCT: float = 0.70       # inject budget-warning message above this
_BUDGET_HARD_PCT: float = 0.90       # disable tool_choice above this
_DISPLAY_TOOLS_LIMIT: int = 20       # max tools listed in system prompt snippet
_HISTORY_RETAIN_MESSAGES: int = 20   # run_agentic teardown message cap (unused after JSONL fix)
_AGENTIC_CLIENT_TIMEOUT: float = 120.0  # asyncio-level LLM call timeout for tool work
_AGENTIC_CHAT_TIMEOUT: float = 35.0     # tighter timeout for short chat-feel msgs (small model)
_AGENTIC_DEFAULT_PROVIDER: str = "openrouter"
_AGENTIC_DEFAULT_MODEL: str = "openai/gpt-4o"
_AGENTIC_DEFAULT_TEMP: float = 0.7
_AGENTIC_DEFAULT_MAXTOK: int = 4_096
# Tight cap for chat-feel replies. Replies to "hey" / "thanks" are 1-3
# sentences; allocating 4096 tokens to the output budget makes the model
# generate longer / slower replies on some providers. 256 still leaves
# room for a 4-sentence answer.
_AGENTIC_CHAT_MAXTOK: int = 256


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
        self._plan_context_loaded: bool = False
        self._plan_ctx_loaded_at: float = 0.0  # epoch timestamp of last plan ctx fetch
        # User profile — lazy-loaded from USER.md on first turn (cached for session lifetime)
        self._user_profile_content: str = ""
        self._user_profile_loaded: bool = False
        # Declared here for static-analysis visibility (set True in run_agentic on first call)
        self._agentic_tools_registered: bool = False

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
                        if ConversationalAgent._is_async_callable(_compat):

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
            if self._is_async_callable(cb):
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

    @staticmethod
    def _is_async_callable(cb: object) -> bool:
        """Return True if *cb* is an async function or a callable with an async ``__call__``."""
        return inspect.iscoroutinefunction(cb) or inspect.iscoroutinefunction(
            getattr(cb, "__call__", None)  # noqa: B004
        )

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

    def _get_memory_components(self):
        """Lazily build (FactRetriever, MemoryAutoExtractor) over the shared KeyFactStore.

        The store points at the default ``~/.navig/memory/key_facts.db`` — the same
        file the MCP ``memory_key_facts_*`` tools use — so facts written by either
        path are visible to the other. Returns ``(None, None)`` if the memory
        subsystem is unavailable so callers degrade gracefully.
        """
        if getattr(self, "_memory_unavailable", False):
            return None, None
        retriever = getattr(self, "_fact_retriever", None)
        extractor = getattr(self, "_mem_extractor", None)
        if retriever is not None and extractor is not None:
            return retriever, extractor
        try:
            import asyncio as _asyncio

            from navig.agent.memory_auto_extractor import MemoryAutoExtractor
            from navig.memory.fact_retriever import FactRetriever
            from navig.memory.key_facts import KeyFactStore

            store = KeyFactStore()

            async def _extractor_llm(prompt: str, **_kw: Any) -> str:
                # Cheap tier; run the sync generator off the event loop.
                from navig.llm_generate import llm_generate

                return await _asyncio.to_thread(
                    llm_generate, [{"role": "user", "content": prompt}], mode="summarize"
                )

            self._fact_retriever = FactRetriever(store)
            self._mem_extractor = MemoryAutoExtractor(store=store, llm_call=_extractor_llm)
            return self._fact_retriever, self._mem_extractor
        except Exception as exc:  # noqa: BLE001
            logger.debug("memory components unavailable: %s", exc)
            self._memory_unavailable = True
            return None, None

    def _recall_block(self, message: str) -> str:
        """Return a '## What I remember' block of facts relevant to *message*.

        Best-effort: empty string on any failure. Kept small (≈400 tokens) so it
        doesn't dominate the turn or bloat the (volatile) user-turn content.
        """
        retriever, _ = self._get_memory_components()
        if retriever is None:
            return ""
        try:
            result = retriever.retrieve(query=message, max_tokens=400)
            if result and result.formatted:
                return f"## What I remember\n{result.formatted}"
        except Exception as exc:  # noqa: BLE001
            logger.debug("fact recall skipped: %s", exc)
        return ""

    def _load_user_profile(self) -> str:
        """Return USER.md content (cached after first load), capped to _USER_PROFILE_MAX_CHARS."""
        if not self._user_profile_loaded:
            self._user_profile_loaded = True
            try:
                from navig.workspace import WorkspaceManager

                raw = WorkspaceManager().get_file_content("USER.md") or ""
                self._user_profile_content = raw.strip()
            except Exception as exc:  # noqa: BLE001
                logger.debug("_load_user_profile: USER.md unavailable (%s)", exc)
        if not self._user_profile_content:
            return ""
        profile = self._user_profile_content
        if len(profile) > _USER_PROFILE_MAX_CHARS:
            profile = profile[:_USER_PROFILE_MAX_CHARS].rstrip() + " …[profile truncated]"
        return profile

    def _get_plan_context_block(self) -> str:
        """Load plan context and return a formatted prompt block.

        Caches the snapshot in ``self.context['plan_context']``.  Auto-refreshes
        after ``_PLAN_CTX_TTL`` seconds so ``/plans update`` changes propagate
        within the same long-running session without requiring a restart.
        """
        now = time.time()
        ttl_fresh = self._plan_context_loaded and (now - self._plan_ctx_loaded_at) < _PLAN_CTX_TTL
        if ttl_fresh:
            # Serve from cache — format the already-gathered snapshot.
            cached: dict[str, Any] = self.context.get("plan_context") or {}
            if not cached:
                return ""
            try:
                from navig.plans.context import PlanContext

                return PlanContext().format_for_prompt(cached)
            except Exception as exc:  # noqa: BLE001
                logger.debug("_get_plan_context_block: cached format failed (%s)", exc)
                return ""
        # Load (or reload after TTL expiry).
        try:
            from navig.plans.context import PlanContext
            from navig.spaces.resolver import get_default_space

            space_name = get_default_space()
            pc = PlanContext()
            snapshot = pc.gather(space_name)
            if snapshot:
                self.context["plan_context"] = snapshot
                non_null = sum(
                    1 for key in _PLAN_CONTEXT_KEYS if snapshot.get(key) is not None
                )
                logger.info(
                    "plan_context_loaded space=%s non_null_keys=%d",
                    space_name,
                    non_null,
                )
            self._plan_context_loaded = True
            self._plan_ctx_loaded_at = now
            return pc.format_for_prompt(snapshot) if snapshot else ""
        except Exception as exc:
            logger.debug("plan context injection skipped: %s", exc)
            self._plan_context_loaded = True
            self._plan_ctx_loaded_at = now
        return ""

    def _build_awareness_context(self) -> str:
        now = datetime.now().astimezone()
        parts = [f"System time: {now.strftime('%H:%M %Z')}, {now.strftime('%A %d %B %Y')}."]
        if uname := self._user_identity.get("username", ""):
            parts.append(f"You are talking to {uname} (your operator). Address them naturally.")
        elif uid := self._user_identity.get("user_id", ""):
            parts.append(f"User ID: {uid}.")
        if profile := self._load_user_profile():
            parts.append(f"## About the user\n{profile}")
        # When no recent history survived the session-boundary filter, tell the
        # LLM explicitly to start fresh so it does not invent continuations of the
        # previous session (e.g. sleep reminders).  One-shot: cleared after first use.
        if getattr(self._history, "_session_is_fresh", True):
            self._history.mark_freshness_consumed()
            parts.append("Fresh session — no prior conversation loaded.")
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

    def _build_system_prompt(self, user_message: str, *, minimal: bool = False) -> str:
        code = self._resolve_prompt_language(user_message)
        lang_instruction = self._lang.build_instruction(code)
        if minimal:
            # Slim path for short chat-feel messages: ~250 chars instead
            # of ~2,900. The user doesn't need the full identity or chat
            # rules to get a "Hey, what's up?" style reply, and the LLM
            # round-trip is dramatically faster with less input to read.
            return self._soul_loader.build_minimal_prompt(lang_instruction=lang_instruction)
        return self._soul_loader.build_system_prompt(
            soul=self._soul_loader.cached_content or "",
            lang_instruction=lang_instruction,
            awareness=self._build_awareness_context(),
        )

    def _planner_fallback(self) -> str:
        """Return a planner-generated response, or an actionable 'no provider' message."""
        result = self._planner.plan(self._last_user_message)
        if result:
            return json.dumps(result)
        return "No AI provider configured — run `navig config show` to check your setup."

    async def chat(
        self, message: str, tier_override: str = "", *, on_partial=None, effort: str = ""
    ) -> str:
        """Process one user turn end-to-end and return the agent's reply string.

        Auto-escalates to the full ReAct loop (``run_agentic``) when tools can
        be registered, giving the assistant multi-step tool-calling capability.
        Falls back to single-shot conversational mode when no tools are available
        (misconfigured env, import failure) so callers never observe a regression.

        *tier_override* is forwarded to the routing layer to force a specific
        LLM tier (e.g. ``'large'``).
        """
        # Lazy-register tools on first call — idempotent after first success.
        if not self._agentic_tools_registered:
            try:
                from navig.agent.tools import register_all_tools
                register_all_tools()
                self._agentic_tools_registered = True
            except Exception as exc:
                logger.debug("chat(): tool registration skipped: %s", exc)

        # Route through the ReAct loop when tools are available. Forward the
        # tier so an explicit channel choice (TALK/REASON→small, CODE→coder_big)
        # drives model selection instead of the message-length heuristic.
        if self._agentic_tools_registered:
            return await self.run_agentic(
                message, on_partial=on_partial, tier_override=tier_override, effort=effort
            )

        # Fallback: single-shot path (no tools configured / registration failed).
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
        except Exception as exc:
            # Soul shaping is best-effort; the raw response is still valid output.
            logger.debug("soul shaping skipped: %s", exc)
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
        max_iterations: int = _MAX_ITERATIONS,
        toolset: str | list[str] = "core",
        cost_tracker=None,
        approval_policy=None,
        on_partial=None,
        tier_override: str = "",
        effort: str = "",
    ) -> str:
        """Native ReAct multi-step tool-calling loop.

        *on_partial* (optional ``Callable[[str], Awaitable[None]]``) gets the
        running accumulated text after every streamed chunk. When set AND
        the call routes through ``small_talk`` mode (no tools), the LLM is
        invoked via ``complete_stream`` and the caller (e.g. the Telegram
        channel) is free to edit the placeholder message progressively.
        For agentic tool work the callback is ignored — buffering tool-call
        deltas while keeping the edit stream coherent is out of scope here.

        This is the canonical agentic path for ``conv`` callers and mirrors the
        established behavior while using this class' state model.
        """
        from navig.agent.agent_tool_registry import _AGENT_REGISTRY
        from navig.agent.effort import (
            auto_detect_effort, get_thinking_params, resolve_effort,
        )
        from navig.agent.prompt_caching import supports_caching
        from navig.agent.tools import register_all_tools
        from navig.agent.usage_tracker import CostTracker, IterationBudget, UsageEvent
        from navig.providers import (
            CompletionRequest, CompletionResponse, Message, create_client, get_builtin_provider,
        )
        from navig.providers.auth import AuthProfileManager
        from navig.providers.clients import ToolDefinition

        if not self._agentic_tools_registered:
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
        except Exception as exc:  # noqa: BLE001
            logger.debug("F-20 semantic routing failed, using explicit toolsets (%s)", exc)
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

        # Cheap message shape lookup — used by both the model-tier decision
        # below AND the plan-context skip further down. Computed once.
        _stripped_msg = message.strip()
        _short_chat = len(_stripped_msg) < 80 and len(_stripped_msg.split()) < 12

        provider_name = _AGENTIC_DEFAULT_PROVIDER
        model_name = _AGENTIC_DEFAULT_MODEL
        temperature = _AGENTIC_DEFAULT_TEMP
        # Short messages get the tight chat budget; tool work keeps the
        # generous default so multi-step ReAct turns aren't truncated.
        max_tokens = _AGENTIC_CHAT_MAXTOK if _short_chat else _AGENTIC_DEFAULT_MAXTOK
        base_url: str | None = None
        # Mode selection: short chat-feel messages ("Hey", "thanks", "ok")
        # use the small (8b) model — fast, no 2-minute timeout. Anything
        # longer falls through to the coder model where tool use matters.
        # Without this branch, run_agentic ALWAYS resolves to mode="coding"
        # → 70b model → free-tier endpoints often hang past 120s on a
        # 1-word reply, which is the worst possible UX.
        # An explicit channel tier wins over the length heuristic. Telegram
        # TALK/REASON set "small"; CODE sets "coder_big". This fixes the trap
        # where a simple question, padded past the short-chat cutoff (REASON
        # appends an explore suffix + web context), silently fell into the 480B
        # CODER model — 40s and thousands of tokens for a one-line answer.
        _TIER_TO_MODE = {
            "small": "small_talk",
            "big": "big_tasks",
            "coder_big": "coding",
            "coder": "coding",
        }
        _forced_mode = _TIER_TO_MODE.get((tier_override or "").strip())
        _resolve_mode = _forced_mode or ("small_talk" if _short_chat else "coding")
        try:
            from navig.llm_router import resolve_llm

            resolved = resolve_llm(mode=_resolve_mode)
            provider_name = resolved.provider
            model_name = resolved.model
            temperature = resolved.temperature
            max_tokens = resolved.max_tokens
            base_url = resolved.base_url
        except Exception as exc:
            logger.debug("Exception suppressed: %s", exc)

        # ── Prefer Claude for the brain tiers when an Anthropic key is present ──
        # Only the heavy tiers (big_tasks/coding) and only when an ANTHROPIC key
        # actually resolves — keyless users keep the existing OpenRouter chain, so
        # this never introduces a failing default. Direct anthropic provider is
        # required for caching + effort/thinking to take effect.
        if _resolve_mode in ("big_tasks", "coding") and (provider_name or "").lower() != "anthropic":
            try:
                _ant_key, _ = AuthProfileManager().resolve_auth("anthropic")
                if _ant_key:
                    provider_name = "anthropic"
                    model_name = "claude-opus-4-8"
                    base_url = None
                    logger.debug("brain: switched to anthropic/claude-opus-4-8 (key present)")
            except Exception as exc:  # noqa: BLE001
                logger.debug("anthropic brain-preference probe skipped: %s", exc)

        # ── Smarter/cheaper brain: prompt caching + effort/thinking (Anthropic) ──
        # Caching is GA and harmless for non-Anthropic providers (their clients
        # ignore the flag), so enable it whenever the model is known-cacheable.
        _cache_on = supports_caching(model_name)
        # Effort: explicit override → that; short chat → LOW (cheapest, no thinking);
        # else auto-detect from the message. Only the direct Anthropic provider
        # honours these params, so gate extra_body on provider == "anthropic".
        _effort_extra: dict | None = None
        try:
            if _short_chat:
                from navig.agent.effort import EffortLevel
                _effort_level = EffortLevel.LOW
            else:
                _effort_level = resolve_effort(effort) if effort else auto_detect_effort(message)
            if (provider_name or "").lower() == "anthropic":
                _effort_extra = get_thinking_params(_effort_level, provider="anthropic") or None
        except Exception as exc:  # noqa: BLE001
            logger.debug("effort resolution skipped: %s", exc)

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
            # Chat-feel messages get a tight 35s timeout (small model, no
            # tools). Real tool-using work keeps the 120s budget. The user
            # sees a fast error on "hey" instead of staring at 3 typing
            # indicators for 2 minutes.
            _client_timeout = _AGENTIC_CHAT_TIMEOUT if _short_chat else _AGENTIC_CLIENT_TIMEOUT
            client = create_client(provider_cfg, api_key=api_key, timeout=_client_timeout)
        except Exception as exc:
            logger.error("run_agentic: could not create LLM client: %s", exc)
            return f"Couldn't connect to the LLM provider ({provider_name}): {exc}"

        self._last_user_message = message
        # For short chat-feel messages, build the slim ~250-char prompt
        # (no SOUL identity block, no chat rules, no awareness context).
        # This is the single biggest token-cost win for cold replies.
        system_prompt = self._build_system_prompt(message, minimal=_short_chat)

        # Skip plan-context injection on short chat-feel messages (mirror
        # the same guard in _get_ai_response). For "hey", "thanks", "ok"
        # the wiki search + docs scan run on the warm path otherwise; this
        # cuts ~5s+ off the first reply in a new session.
        # _short_chat was computed earlier alongside the model-tier decision.
        if not _short_chat:
            if plan_block := self._get_plan_context_block():
                system_prompt += "\n\n" + plan_block

        toolset_names = _AGENT_REGISTRY.available_names(toolsets=toolsets)
        if toolset_names:
            displayed = ", ".join(f"`{name}`" for name in toolset_names[:_DISPLAY_TOOLS_LIMIT])
            system_prompt += (
                "\n\n## Agentic Mode\n"
                f"You have access to the following tools: {displayed}.\n"
                "Use them step-by-step to fulfill the user's request, then give a final reply."
            )

        # Deeper memory (read path): pull facts relevant to this message and
        # prepend them to the *user turn* (not the cached system block — facts are
        # query-specific, so injecting them into `system` would invalidate the
        # tools+system cache every turn). Skipped on short chat for latency.
        _user_content = message
        if not _short_chat:
            if recall := self._recall_block(message):
                _user_content = f"{recall}\n\n{message}"

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
            Message(role="user", content=_user_content),
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

        # ── Hoist dispatch helpers — defined once per call, not once per turn ───────────────
        sem = asyncio.Semaphore(_MAX_PARALLEL_TOOLS)

        # Build a vault_injector for credential-secured tools (F-17)
        def _vault_injector(keys: list[str]) -> dict[str, str]:
            try:
                from navig.vault import get_vault
                v = get_vault()
                if v is not None:
                    return v.batch_get(keys)
            except Exception:
                pass
            return {}

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

            # Provable trust: adversarially verify DESTRUCTIVE tool calls before they
            # run (read-only tools skip — no latency cost on the common path). Returns
            # the verdict as the tool result so the agent can adapt instead of executing
            # an unsafe action. Best-effort; the verifier no-ops when disabled.
            try:
                from navig.tools.approval import DESTRUCTIVE_TOOLS

                if tool_call_item.name in DESTRUCTIVE_TOOLS:
                    from navig.agent.verifier import get_verifier

                    verifier = get_verifier()
                    if verifier.enabled:
                        verdict = await verifier.verify_tool_call(
                            tool_call_item.name, args, rationale="agentic"
                        )
                        if not verdict.safe:
                            logger.warning(
                                "Tool %s blocked by verifier: %s",
                                tool_call_item.name,
                                verdict.reason,
                            )
                            return (
                                tool_call_item.id,
                                f"[Verification blocked: {verdict.reason}]",
                            )
            except Exception as exc:  # noqa: BLE001
                logger.debug("tool verification skipped: %s", exc)

            try:
                from navig.agent.speculative import get_speculative_executor

                spec = get_speculative_executor()
                if spec is not None:
                    result_str = spec.execute(tool_call_item.name, args)
                else:
                    result_str = _AGENT_REGISTRY.dispatch(
                        tool_call_item.name,
                        args,
                        vault_injector=_vault_injector,
                    )
            except Exception as exc:
                result_str = f"[Tool error: {exc}]"
            return (tool_call_item.id, result_str)

        async def _sem_dispatch(tool_call_item):
            async with sem:
                return await _dispatch_single(tool_call_item)

        # Resilient fast fallback: if the resolved model hangs (timeout) or
        # errors, recover the turn ONCE on the fast small model instead of
        # losing the reply. Critical when the configured big/coder tiers point
        # at slow or unreachable endpoints (e.g. a 70B that read-times-out).
        _fell_back = False

        async def _fast_retry(on_partial_cb) -> "CompletionResponse | None":
            """Retry the current turn on the fast small model. Returns a
            CompletionResponse, or None when no DISTINCT fast model exists or
            the retry also fails. On success it re-points model/provider so
            usage records under the model that actually answered."""
            nonlocal model_name, provider_name
            try:
                from navig.llm_router import resolve_llm as _resolve_llm

                fb = _resolve_llm(mode="small_talk")
            except Exception as _exc:  # noqa: BLE001
                logger.debug("fast-retry: resolve_llm failed: %s", _exc)
                return None
            if not fb or not getattr(fb, "provider", None) or not getattr(fb, "model", None):
                return None
            if fb.provider == provider_name and fb.model == model_name:
                return None  # don't retry the same model with itself

            fb_cfg = get_builtin_provider(fb.provider)
            if fb_cfg is None:
                from navig.llm_router import PROVIDER_BASE_URLS
                from navig.providers.types import ModelApi, ProviderConfig

                fb_cfg = ProviderConfig(
                    name=fb.provider,
                    base_url=(
                        getattr(fb, "base_url", "")
                        or PROVIDER_BASE_URLS.get(fb.provider, "https://openrouter.ai/api/v1")
                    ),
                    api=ModelApi.OPENAI_COMPLETIONS,
                )
            try:
                fb_key, _ = AuthProfileManager().resolve_auth(fb.provider)
                fb_client = create_client(
                    fb_cfg, api_key=fb_key, timeout=_AGENTIC_CHAT_TIMEOUT
                )
            except Exception as _exc:  # noqa: BLE001
                logger.debug("fast-retry: client create failed: %s", _exc)
                return None

            # No tools on the fallback — the goal is a fast, clean answer.
            # Cache the (frozen) prefix when the fallback model supports it; no
            # thinking/effort on the fast path (it's the cheap recovery model).
            fb_request = CompletionRequest(
                messages=working_messages,
                model=fb.model,
                temperature=temperature,
                max_tokens=max_tokens,
                cache_control=supports_caching(fb.model),
            )
            logger.warning(
                "run_agentic: falling back to fast model %s:%s after %s:%s failed",
                fb.provider, fb.model, provider_name, model_name,
            )
            try:
                if on_partial_cb is not None and hasattr(fb_client, "complete_stream"):
                    _acc: list[str] = []
                    _fin: str | None = None
                    _usg: dict | None = None
                    _mdl: str | None = None

                    async def _drive_fb() -> None:
                        nonlocal _fin, _usg, _mdl
                        async for ch in fb_client.complete_stream(fb_request):
                            d = getattr(ch, "delta", None)
                            if d:
                                _acc.append(d)
                                try:
                                    await on_partial_cb("".join(_acc))
                                except Exception:  # noqa: BLE001
                                    pass
                            if getattr(ch, "finish_reason", None):
                                _fin = ch.finish_reason
                            if getattr(ch, "usage", None):
                                _usg = ch.usage
                            if getattr(ch, "model", None):
                                _mdl = ch.model

                    await asyncio.wait_for(_drive_fb(), timeout=_AGENTIC_CHAT_TIMEOUT)
                    result = CompletionResponse(
                        content="".join(_acc) or None,
                        tool_calls=None,
                        finish_reason=_fin,
                        usage=_usg,
                        model=_mdl or fb.model,
                        provider=fb.provider,
                    )
                else:
                    result = await asyncio.wait_for(
                        fb_client.complete(fb_request), timeout=_AGENTIC_CHAT_TIMEOUT
                    )
            except Exception as _exc:  # noqa: BLE001
                logger.warning("run_agentic: fast fallback also failed: %s", _exc)
                return None
            finally:
                _close = getattr(fb_client, "close", None)
                if callable(_close):
                    try:
                        await _close()
                    except Exception:  # noqa: BLE001
                        pass
            # Record cost under the model that actually answered.
            provider_name, model_name = fb.provider, fb.model
            return result

        while not budget.is_exhausted():
            turn += 1
            budget.consume(1)

            if compressor is not None and turn > _COMPRESS_AFTER_TURN:
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
            if pct >= _BUDGET_HARD_PCT:
                tool_choice = "none"
            elif pct >= _BUDGET_WARN_PCT:
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
                cache_control=_cache_on,
                extra_body=_effort_extra,
            )

            # Streaming path: only enabled when the caller passed an
            # on_partial callback AND this is a chat-feel turn (no tool
            # use). For agentic tool turns we stick to the blocking
            # `complete()` path because tool_call deltas can't be cleanly
            # surfaced as plain text mid-stream. The accumulated text is
            # passed to on_partial each chunk; the caller (Telegram) is
            # expected to debounce edit calls itself.
            _can_stream = (
                on_partial is not None
                and _short_chat
                and hasattr(client, "complete_stream")
            )
            try:
                if _can_stream:
                    _accum: list[str] = []
                    _final_finish: str | None = None
                    _final_usage: dict | None = None
                    _final_model: str | None = None

                    async def _drive_stream() -> None:
                        nonlocal _final_finish, _final_usage, _final_model
                        async for chunk in client.complete_stream(request):
                            delta = getattr(chunk, "delta", None)
                            if delta:
                                _accum.append(delta)
                                try:
                                    await on_partial("".join(_accum))
                                except Exception as exc:  # noqa: BLE001
                                    logger.debug(
                                        "on_partial callback raised %r; continuing",
                                        exc,
                                    )
                            if getattr(chunk, "finish_reason", None):
                                _final_finish = chunk.finish_reason
                            if getattr(chunk, "usage", None):
                                _final_usage = chunk.usage
                            if getattr(chunk, "model", None):
                                _final_model = chunk.model

                    await asyncio.wait_for(_drive_stream(), timeout=_client_timeout)
                    # Synthesise a CompletionResponse so the rest of the
                    # loop (usage tracking, history append, finish_reason
                    # handling) keeps working unchanged.
                    response = CompletionResponse(
                        content="".join(_accum) or None,
                        tool_calls=None,
                        finish_reason=_final_finish,
                        usage=_final_usage,
                        model=_final_model or model_name,
                        provider=provider_name,
                    )
                else:
                    response = await asyncio.wait_for(
                        client.complete(request), timeout=_client_timeout
                    )
            except asyncio.TimeoutError:
                logger.error(
                    "run_agentic: LLM timed out on turn %d (%.0fs)",
                    turn, _client_timeout,
                )
                _fb = None if _fell_back else await _fast_retry(on_partial)
                if _fb is not None:
                    _fell_back = True
                    response = _fb
                else:
                    final_response = (
                        f"LLM timed out after {_client_timeout:.0f}s — "
                        "provider may be unavailable."
                    )
                    break
            except Exception as exc:
                logger.error("run_agentic: LLM call failed on turn %d: %s", turn, exc)
                _fb = None if _fell_back else await _fast_retry(on_partial)
                if _fb is not None:
                    _fell_back = True
                    response = _fb
                else:
                    final_response = f"Error during agentic execution (turn {turn}): {exc}"
                    break

            usage = dict(response.usage or {})
            # Some streaming backends omit usage even with include_usage set.
            # Fall back to a char-based estimate so a streamed turn never reads
            # as a silent $0.00 — approximate cost beats blind cost.
            if not usage.get("prompt_tokens") or not usage.get("completion_tokens"):
                try:
                    from navig.core.tokens import estimate_tokens

                    if not usage.get("prompt_tokens"):
                        usage["prompt_tokens"] = estimate_tokens(
                            "\n".join(m.content or "" for m in working_messages)
                        )
                    if not usage.get("completion_tokens"):
                        usage["completion_tokens"] = estimate_tokens(response.content or "")
                except Exception:  # noqa: BLE001
                    pass  # estimate is best-effort; never block the turn
            try:
                tracker.record(
                    UsageEvent(
                        turn=turn,
                        model=model_name,
                        provider=provider_name,
                        prompt_tokens=usage.get("prompt_tokens", 0),
                        completion_tokens=usage.get("completion_tokens", 0),
                        # Cache tokens live on CompletionResponse attributes, not
                        # in the usage dict — read them directly (usage.get(...)
                        # always returned 0 here, hiding cache savings).
                        cache_read_tokens=getattr(response, "cache_read_input_tokens", 0)
                        or usage.get("cache_read_input_tokens", 0),
                        cache_write_tokens=getattr(response, "cache_creation_input_tokens", 0)
                        or usage.get("cache_creation_input_tokens", 0),
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

            parallel_batch = []
            sequential_batch = []
            for tool_call in pending_calls:
                if is_parallel_safe(tool_call.name) and len(pending_calls) > 1:
                    parallel_batch.append(tool_call)
                else:
                    sequential_batch.append(tool_call)

            collected_results: list[tuple[str, str]] = []

            if parallel_batch:
                par_results = await asyncio.gather(
                    *[_dispatch_single(tool_call) for tool_call in parallel_batch],
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
            final_response = (
                f"Agent reached the {turn}-turn limit without a final answer. "
                "Try a more specific request."
            )

        # Persist both turns through the canonical add() path so JSONL is always updated.
        # Previously the setter path was used which bypassed JSONL persistence entirely.
        self._history.add("user", message)
        self._history.add("assistant", final_response)

        # Deeper memory (write path): feed the turn to the auto-extractor and let
        # it persist durable facts in the background. Fire-and-forget so it never
        # blocks the reply; extraction only fires every N turns internally.
        try:
            _, _extractor = self._get_memory_components()
            if _extractor is not None:
                _extractor.record_turn("user", message)
                _extractor.record_turn("assistant", final_response)
                _task = asyncio.create_task(_extractor.maybe_extract())
                # Swallow background errors so an extraction failure never surfaces.
                _task.add_done_callback(
                    lambda t: t.exception() if not t.cancelled() else None
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("memory auto-extract scheduling skipped: %s", exc)

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
        return "Idle — what's next?"

    async def _get_ai_response(self, message: str) -> str:
        if self._ai_client is None:
            return self._planner_fallback()

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
        except Exception as exc:  # noqa: BLE001
            logger.debug("is_available probe failed (%s)", exc)

        # Compute message shape once. Used to pick a slim or full system
        # prompt AND to skip plan-context injection on chat-feel turns.
        # The wiki search + docs scan + inbox crawl all run on the warm
        # path otherwise; for "ok", "thanks", "so far so good" they're
        # pure latency with no value to the reply.
        _stripped = message.strip()
        _is_short_chat = len(_stripped) < 80 and len(_stripped.split()) < 12

        system_prompt = self._build_system_prompt(message, minimal=_is_short_chat)
        msgs = [
            {"role": "system", "content": system_prompt},
            *self._history.get_messages(),
        ]
        # ── Lazy plan context injection (deduped through _get_plan_context_block) ──
        if not _is_short_chat:
            if plan_block := self._get_plan_context_block():
                msgs[0]["content"] += "\n\n" + plan_block
        # Inject any remaining context entries (other than plan_context, handled above).
        other_ctx = {k: v for k, v in self.context.items() if k != "plan_context"}
        if other_ctx:
            msgs[0]["content"] += f"\nContext: {json.dumps(other_ctx)}"
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
        except (ImportError, AttributeError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug("default_provider probe failed: %s", exc)

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
                _exc_lower = exc_msg.lower()
                # Extract the "Last error: <...>" tail for a concrete hint.
                _last_err_snippet = ""
                _le_marker = "last error:"
                _le_idx = _exc_lower.rfind(_le_marker)
                if _le_idx != -1:
                    _last_err_snippet = exc_msg[_le_idx + len(_le_marker):].strip()[:160]
                # Classify the failure so we give an actionable suggestion.
                if (
                    "model" in _exc_lower
                    and (
                        "not found" in _exc_lower
                        or "does not exist" in _exc_lower
                        or "invalid model" in _exc_lower
                        or "404" in exc_msg
                        or "400" in exc_msg
                    )
                ) or (
                    _last_err_snippet
                    and ("404" in _last_err_snippet or "400" in _last_err_snippet)
                ):
                    _hint = (
                        "\nThe configured model ID may be invalid or outdated. "
                        "Use /provider to re-select your provider and reset the model list."
                    )
                elif "401" in exc_msg or "unauthorized" in _exc_lower or "invalid api key" in _exc_lower:
                    _hint = "\nThe API key appears to be invalid. Check /provider to update it."
                elif _last_err_snippet:
                    _hint = f"\nLast error: {_last_err_snippet}"
                else:
                    _hint = (
                        "\nCheck /provider to confirm your selection and verify the API key "
                        "is stored correctly (vault or env var)."
                    )
                return (
                    "\u26a0\ufe0f I couldn't reach any AI provider right now."
                    + _hint
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
                    return self._planner_fallback()
                raise
        if ai_available:
            try:
                return await self._ai_client.chat(msgs)
            except Exception as exc:
                msg = str(exc).lower()
                if "no ai provider available" in msg or "no provider available" in msg:
                    return self._planner_fallback()
                raise
        return self._planner_fallback()
