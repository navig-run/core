"""ConversationalAgent: DI-orchestrated multi-turn chat sessions for NAVIG gateway."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Protocol

    from navig.agent.conv.soul import SoulContext

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
# Wall-clock cap for a single tool dispatch. The tool runs OFF the event loop
# (asyncio.to_thread / SpeculativeExecutor.aexecute); wait_for lets the turn
# abandon a hung live-infra call at this bound instead of wedging forever. Mirror
# of agent_tool_registry.TOOL_TIMEOUT_SECONDS (which caps the sync-on-loop path).
_TOOL_DISPATCH_TIMEOUT: float = 120.0
_COMPRESS_AFTER_TURN: int = 3        # turns before context compression starts
_BUDGET_WARN_PCT: float = 0.70       # inject budget-warning message above this
_BUDGET_HARD_PCT: float = 0.90       # disable tool_choice above this
_DISPLAY_TOOLS_LIMIT: int = 20       # max tools listed in system prompt snippet

# Identity / capability questions ("who are you", "what can you do") are short —
# they'd normally take the slim minimal prompt, which omits the tool inventory,
# so the model improvises a narrow list. When a short message matches this, we
# use the FULL prompt (with capabilities) so the answer reflects real breadth.
_CAPABILITY_QUESTION_RE = re.compile(
    r"\b(?:"
    r"who\s+are\s+you|what\s+are\s+you|"
    r"what\s+(?:can|do)\s+you\s+(?:do|help\s+with)|"
    r"what\s+(?:are|can)\s+you\s+capable|"
    r"your\s+(?:capabilit|abilit|feature|function|tool|skill)\w*|"
    r"(?:tell\s+me\s+about|introduce)\s+yourself|"
    r"what\s+kind\s+of\s+(?:things|stuff|tasks|work)\s+can\s+you"
    r")\b",
    re.IGNORECASE,
)
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


#: Toolsets offered on EVERY turn, whatever the message looks like.
#:
#: These hold state ACROSS turns, and `suggest_toolsets` returns nothing for a short
#: message — so router-gating them means a tool can be offered in turn 1 and gone by
#: turn 3, leaving the agent with a todo list it believes it recorded and cannot update.
#: An intermittent stateful tool is worse than an absent one. `get_plan_context` is the
#: existing precedent: a meta tool registered straight into `core`.
_ALWAYS_ON_TOOLSETS: tuple[str, ...] = ("skills", "todo")


def default_turn_toolsets(
    message: str,
    *,
    toolset: str | list[str] = "core",
    tier_override: str = "",
) -> list[str]:
    """Resolve which toolsets a turn puts in front of the model.

    Extracted from ``run_agentic`` so the decision is assertable on its own: the tools a
    model is *offered* are a different question from the tools that are *registered*, and
    only this function answers the first one.
    """
    explicit_toolsets = [toolset] if isinstance(toolset, str) else list(toolset)

    # Always-on meta tools — see _ALWAYS_ON_TOOLSETS.
    for _ts in _ALWAYS_ON_TOOLSETS:
        if _ts not in explicit_toolsets:
            explicit_toolsets.append(_ts)

    # The research/depth tier is an explicit request for a properly grounded
    # answer, so it must be handed the retrieval tools regardless of what the
    # message-shape router infers. Without this the merged set stayed just
    # "core" (bash/read/write/list) for a short question like "info about Fight
    # Club" — the model had no search/web_fetch/wiki/browser to answer with,
    # which is the root of the shallow-answer complaint. "browser" lets it open
    # and read a page, not just paraphrase search snippets.
    if (tier_override or "").strip() == "research":
        for _ts in ("research", "browser"):
            if _ts not in explicit_toolsets:
                explicit_toolsets.append(_ts)
    # A message carrying a URL is a request to look at that URL. The default
    # toolset is "core" (bash/read_file/write_file/list_files) — nothing in it
    # can fetch a page — and the shape router returns NOTHING for a short
    # message, so a pasted link reached a model with no way to open it. Its
    # only honest reply was "Can't access external links", which is exactly
    # what the operator got. "search" is {search, web_fetch}; web_fetch runs
    # through the SSRF guard, so this widens capability, not attack surface.
    if _MESSAGE_HAS_URL.search(message or "") and "search" not in explicit_toolsets:
        explicit_toolsets.append("search")
    try:
        from navig.llm.router import suggest_toolsets

        suggested = suggest_toolsets(user_input=message)
        merged = list(explicit_toolsets)
        for suggested_toolset in suggested:
            if suggested_toolset not in merged:
                merged.append(suggested_toolset)
        logger.debug(
            "F-20 semantic routing: explicit=%s suggested=%s → merged=%s",
            explicit_toolsets,
            suggested,
            merged,
        )
        return merged
    except Exception as exc:  # noqa: BLE001
        logger.debug("F-20 semantic routing failed, using explicit toolsets (%s)", exc)
        return explicit_toolsets


def _tool_needs_session(tool_name: str) -> bool:
    """Does this tool keep state per conversation?

    Read from the registered tool's own ``needs_session`` declaration, so a new
    session-scoped tool works the day it is registered instead of needing a line in the
    agent's dispatch loop. Never raises — an unknown or half-registered name simply does
    not get the key, which is the pre-existing behaviour for every other tool.
    """
    try:
        from navig.agent.agent_tool_registry import _AGENT_REGISTRY

        entry = _AGENT_REGISTRY.get_entry(tool_name)
        return bool(getattr(entry.tool_ref, "needs_session", False)) if entry else False
    except Exception:  # noqa: BLE001 — never break dispatch over a context nicety
        return False

#: A message carrying a link needs a toolset that can actually open it — see the
#: toolset merge in run_agentic. Deliberately narrow (an explicit scheme): a bare
#: "example.com" is far more often prose than a request to fetch something.
_MESSAGE_HAS_URL = re.compile(r"https?://\S+", re.I)

# ── StatusEvent argument/result redaction ──────────────────────────────────
# A live progress view (and the Telegram debug X-ray) shows what tool ran with
# what input, so summaries must be short AND never leak a secret. We show only
# a few known-safe keys and always truncate; anything unrecognised is omitted
# rather than dumped.
_SAFE_ARG_KEYS: tuple[str, ...] = (
    "query", "url", "path", "file", "host", "command", "q", "name", "id", "topic",
)
_ARG_SUMMARY_MAX: int = 80
_RESULT_SUMMARY_MAX: int = 120


def _summarize_tool_args(args: Any) -> str:
    """Compact, secret-safe one-line summary of a tool's arguments."""
    if not isinstance(args, dict):
        return ""
    parts: list[str] = []
    for key in _SAFE_ARG_KEYS:
        if key in args and args[key] not in (None, "", [], {}):
            val = str(args[key]).replace("\n", " ")
            if len(val) > _ARG_SUMMARY_MAX:
                val = val[: _ARG_SUMMARY_MAX - 1] + "…"
            parts.append(f"{key}={val}")
    return " · ".join(parts)


def _summarize_tool_result(result: Any) -> str:
    """First line of a tool result, truncated — never the whole payload."""
    text = str(result).strip().replace("\n", " ")
    if len(text) > _RESULT_SUMMARY_MAX:
        text = text[: _RESULT_SUMMARY_MAX - 1] + "…"
    return text


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
        # This session's resolved identity (soul + persona traits + guardrails).
        # Lives on the INSTANCE: the loader is a process-wide singleton, so state
        # kept there is shared by every chat in the daemon.
        self._soul_ctx = None
        self._soul_key: tuple[str, str, str] = ("", "", "")
        self._detected_language_hint: str = ""
        self._last_detected_language: str = "en"
        self._session_fallback_language: str = ""
        self._has_text_detected: bool = False
        self._last_user_message = self._tier_override = ""
        # Set by run_agentic when it rotated to a sibling account (e.g. a capped
        # Claude Max subscription → another one). Callers may read it to surface
        # "answered with <account>"; reset at the start of every turn.
        self._last_account_fallback: dict[str, Any] | None = None
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

    async def _emit(
        self,
        type_: str,
        task_id: str,
        message: str,
        *,
        step_index: int | None = None,
        total_steps: int | None = None,
        **meta: Any,
    ) -> None:
        """Best-effort StatusEvent emit helper.

        Cheap no-op when no callback is registered — the fast common case — so
        the ReAct loop can emit freely without guarding each call site. Never
        raises; a status failure must never break a reply.
        """
        if self._on_status_update is None:
            return
        try:
            await self._emit_event(
                StatusEvent(
                    type=type_,  # type: ignore[arg-type]
                    task_id=task_id,
                    message=message,
                    timestamp=datetime.now(),
                    step_index=step_index,
                    total_steps=total_steps,
                    metadata=meta,
                )
            )
        except Exception as exc:  # noqa: BLE001 — status is never load-bearing
            logger.debug("status emit skipped: %s", exc)

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

    def set_active_persona(
        self,
        config_or_name=None,
        soul_content: str | None = None,
        *,
        space: str = "",
        cwd: "Path | None" = None,
    ) -> None:
        """Bind THIS session's identity: persona soul, tone, banned phrases.

        Resolves through the unified chain and stores the result on the instance.
        Callers used to pass a bare persona *name* and no ``soul_content``, so this
        recorded the name and nothing else — a persona's ``soul.md``, ``tone`` and
        ``banned_phrases`` were parsed, validated, and then reached no prompt at all.

        *soul_content* remains supported for callers that pre-resolved identity
        themselves (the CLI single-agent path, tests); note it goes through the
        process-global loader override and is therefore not per-session.
        """
        name = ""
        if isinstance(config_or_name, str):
            name = config_or_name.strip()
        elif config_or_name is not None:
            name = str(getattr(config_or_name, "name", "") or "").strip()
        self._active_persona = name
        self._runtime_persona = name
        if soul_content:
            self._soul_loader.override(soul_content)
            self._soul_ctx = None
            self._soul_key = ("", "", "")
            return

        key = (name.lower(), space or "", str(cwd) if cwd else "")
        if key == self._soul_key and self._soul_ctx is not None:
            return
        try:
            self._soul_ctx = self._soul_loader.resolve(persona=name, space=space, cwd=cwd)
            self._soul_key = key
            logger.debug(
                "identity resolved: source=%s persona=%s shadowed=%d",
                self._soul_ctx.source,
                self._soul_ctx.persona or "-",
                len(self._soul_ctx.shadowed),
            )
        except Exception as exc:  # noqa: BLE001 — identity must never break a turn
            logger.warning("identity resolution failed (%s); using loader default", exc)
            self._soul_ctx = None
            self._soul_key = ("", "", "")

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
                from navig.llm.generate import llm_generate

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

    def _build_session_context(self) -> str:
        """The STABLE half of session awareness — who we're talking to.

        Everything here holds for the life of the session, so it can sit in the
        cached system prefix. The clock and the one-shot freshness line moved to
        :meth:`_build_turn_context`.
        """
        parts: list[str] = []
        if uname := self._user_identity.get("username", ""):
            parts.append(f"You are talking to {uname} (your operator). Address them naturally.")
        elif uid := self._user_identity.get("user_id", ""):
            parts.append(f"User ID: {uid}.")
        if profile := self._load_user_profile():
            parts.append(f"## About the user\n{profile}")
        return "\n".join(parts)

    def _build_turn_context(self) -> str:
        """The VOLATILE half — rides the user turn, never the system prompt.

        ``System time`` used to open ``## Session Context`` in the *system* block.
        Since the Anthropic cache breakpoint sits on the system message and its
        prefix spans ``tools → system``, a minute-granularity timestamp there
        capped the cache lifetime at ~60s and re-billed the whole tool schema
        block at write price. Same reason the freshness line moved: it is a
        one-shot, so it differed between turn 1 and turn 2 — a guaranteed miss.

        The freshness line ALSO has a side effect (``mark_freshness_consumed``),
        which is why it must never live in a memoised system prompt.
        """
        now = datetime.now().astimezone()
        parts = [f"System time: {now.strftime('%H:%M %Z')}, {now.strftime('%A %d %B %Y')}."]
        # When no recent history survived the session-boundary filter, tell the
        # LLM explicitly to start fresh so it does not invent continuations of the
        # previous session (e.g. sleep reminders).  One-shot: cleared after first use.
        if getattr(self._history, "_session_is_fresh", True):
            self._history.mark_freshness_consumed()
            parts.append("Fresh session — no prior conversation loaded.")
        return "\n".join(parts)

    def _build_awareness_context(self) -> str:
        """Deprecated compat shim — prefer the explicit stable/volatile split.

        Kept so out-of-tree callers keep working; it returns the combined block
        the way it always did. Nothing in navig calls it on the hot path.
        """
        return "\n".join(p for p in (self._build_turn_context(), self._build_session_context()) if p)

    def _build_skills_section(self, user_message: str) -> str:
        """Auto-activate matching SKILL.md skills for this turn and render them.

        Wires the (formerly unplugged) SkillsContext into the agent: a skill
        whose activation keywords / CC ``description`` overlap the user's request
        is injected as instructions — so installed skills *and* plugin-provided
        skills become discoverable without the user naming a command (e.g.
        "edit a video" activates a video skill that runs ``navig generate``).
        Bounded (``max_active``) and degrade-safe — never blocks a turn.
        """
        try:
            from navig.agent.skills_context import get_skills_context
            from navig.spaces.active import get_active_working_dir

            # Resolve the active space's working dir. In the daemon the process
            # never chdirs, so relying on cwd would resolve wherever the daemon was
            # launched rather than the space the operator selected.
            #
            # The context is SHARED (one per workspace dir, process-wide) rather than
            # private to this agent: `manage_skills` force-activates on the very same
            # instance, and on a private copy that activation would change nothing here.
            # include_installed=True → also match plugin-provided + block skills, not
            # just project/global stores. Caching keeps load() off the per-turn path.
            ctx = get_skills_context(str(get_active_working_dir()))
            active = ctx.activate(user_message=user_message)
            return ctx.format_for_system_prompt(active)
        except Exception as exc:  # noqa: BLE001
            logger.debug("skills auto-activation skipped: %s", exc)
            return ""

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
        # "Who are you?" / "What can you do?" are short messages, so they'd take
        # the slim path — but the slim prompt omits the tool inventory, which is
        # exactly what these questions need. Promote them to the full prompt so
        # the answer reflects real breadth instead of an improvised narrow list.
        if minimal and _CAPABILITY_QUESTION_RE.search(user_message or ""):
            minimal = False
        if minimal:
            # Slim path for short chat-feel messages: ~250 chars instead
            # of ~2,900. The user doesn't need the full identity or chat
            # rules to get a "Hey, what's up?" style reply, and the LLM
            # round-trip is dramatically faster with less input to read.
            # A compact capability line rides along (~50 tokens) so a short
            # "what can you do?" in ANY language still knows the real breadth.
            return self._soul_loader.build_minimal_prompt(
                lang_instruction=lang_instruction,
                capabilities=self._capability_summary(compact=True),
            )
        # NB: matched SKILL.md skills, recalled facts and the clock are injected
        # into the *user turn* (see run_agentic and _build_turn_context), NOT here
        # — they're query- or time-specific, so appending them to the cached system
        # block would bust the tools+system prompt cache every turn.
        ctx = self._soul_context()
        return self._soul_loader.build_prompt(
            ctx,
            lang_instruction=lang_instruction,
            awareness=self._build_session_context(),
            capabilities=self._capability_summary(),
        )

    def _soul_context(self) -> "SoulContext":
        """This session's resolved identity, falling back to the loader default.

        The fallback covers the ``soul_content=`` constructor path (CLI, tests),
        where identity was injected rather than resolved.
        """
        if self._soul_ctx is not None:
            return self._soul_ctx
        from navig.agent.conv.guardrails import guardrail_block  # noqa: PLC0415
        from navig.agent.conv.soul import SoulContext  # noqa: PLC0415

        return SoulContext(
            condensed=self._soul_loader.cached_content or "",
            source="override",
            guardrails=guardrail_block(),
        )

    def _capability_summary(self, *, compact: bool = False) -> str:
        """Live summary of the agent's real tools (empty when none registered).

        Stable across a session (the registry is populated once at startup), so
        it rides the cached system block without busting the prompt cache.
        *compact* returns the one-line comma-joined form for the minimal prompt."""
        try:
            from navig.agent.agent_tool_registry import _AGENT_REGISTRY

            return _AGENT_REGISTRY.capability_summary(compact=compact)
        except Exception as exc:  # noqa: BLE001 — never let this break the prompt
            logger.debug("_capability_summary skipped: %s", exc)
            return ""

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
        # Fresh per turn (covers BOTH the ReAct and single-shot paths, and an
        # early return before run_agentic's own reset) so a caller never reads a
        # stale rotation from a previous message.
        self._last_account_fallback = None
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
        # (The former soul `shape_response`/`ContextSignal`/`get_mood_profile` post-processing
        # was removed from navig.agent.soul; this block always fell into its except and
        # returned the raw response, which is what happens now without the dead import.)
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
        session_key: str = "",
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
            auto_detect_effort,
            get_thinking_params,
            resolve_effort,
        )
        from navig.agent.prompt_caching import supports_caching
        from navig.agent.tools import register_all_tools
        from navig.agent.usage_tracker import CostTracker, IterationBudget, UsageEvent
        from navig.providers import (
            CompletionRequest,
            CompletionResponse,
            Message,
            create_client,
            get_builtin_provider,
        )
        from navig.providers.clients import ToolDefinition, merge_tool_call_deltas

        if not self._agentic_tools_registered:
            try:
                register_all_tools()
                self._agentic_tools_registered = True
            except Exception as exc:
                logger.warning("run_agentic: tool registration failed: %s", exc)

        # Stable key for the stateful browser tool's per-chat persistent browser.
        # Callers (e.g. the Telegram channel) pass a chat-stable session_key; CLI
        # falls back to this instance's ephemeral id (per-run scope). Used for the
        # approval gate's session scope, and injected as `_session_id` into any tool
        # declaring `needs_session` — never declared in a tool schema, so the LLM never
        # sees it and cannot spoof another conversation's state.
        _session_key = session_key or self._session_id

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

        toolsets = default_turn_toolsets(
            message, toolset=toolset, tier_override=tier_override
        )

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
        # The research tier is an EXPLICIT "answer this properly" signal. It must
        # never be treated as short chat regardless of message length, or a terse
        # question ("info about Fight Club") would still get the minimal prompt,
        # the 256-token cap, skipped skills/recall and the 35s timeout — i.e. all
        # the shallow-answer shortcuts the depth path exists to bypass.
        if (tier_override or "").strip() == "research":
            _short_chat = False

        provider_name = _AGENTIC_DEFAULT_PROVIDER
        model_name = _AGENTIC_DEFAULT_MODEL
        temperature = _AGENTIC_DEFAULT_TEMP
        # Short messages get the tight chat budget; tool work keeps the generous
        # default so multi-step ReAct turns aren't truncated. NOTE this is only
        # the FALLBACK: resolve_llm() below overwrites max_tokens with the mode's
        # configured value, so these two constants apply only when resolution
        # raises. Either way the run opens on a chat-sized budget and the
        # tool-call escalation further down lifts it — read that, not this line,
        # for what a tool-using turn actually gets.
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
        # "research" is the depth tier for real information questions asked from a
        # chat surface: tool-using and web-grounded. It maps to the big_tasks MODEL
        # (not the "research" llm_mode) on purpose: big_tasks is the tier users
        # actually configure for smart work and is already wired to Claude Opus, so
        # a deep Telegram answer reaches the same model class as the desktop. The
        # standalone "research" llm_mode defaults to niche providers (nvidia/deepseek)
        # most users have never keyed — routing there silently degraded a deep answer
        # to a failed call → fast-retry → shallow model. The research TOOLSETS
        # (search/web_fetch/wiki/browser) are still seeded above off tier_override,
        # so grounding is unaffected by this model choice.
        _TIER_TO_MODE = {
            "small": "small_talk",
            "big": "big_tasks",
            "coder_big": "coding",
            "coder": "coding",
            "research": "big_tasks",
        }
        _forced_mode = _TIER_TO_MODE.get((tier_override or "").strip())
        _resolve_mode = _forced_mode or ("small_talk" if _short_chat else "coding")
        try:
            from navig.llm.router import resolve_llm

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
        # required for caching + effort/thinking to take effect. The Telegram depth
        # tier reaches this via big_tasks (see _TIER_TO_MODE above).
        if _resolve_mode in ("big_tasks", "coding") and (
            provider_name or ""
        ).lower() != "anthropic":
            try:
                from navig.providers.inference import resolve_provider_credential

                _ant_key, _ant_oauth = resolve_provider_credential("anthropic")
                if _ant_key or _ant_oauth:  # API key OR a claude-max OAuth subscription
                    provider_name = "anthropic"
                    model_name = "claude-opus-4-8"
                    base_url = None
                    logger.debug("brain: switched to anthropic/claude-opus-4-8 (credential present)")
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
        # A short *character count* does not mean a shallow *question*: "info about
        # Fight Club" is 21 chars but wants real reasoning. The research tier already
        # cleared _short_chat above precisely so it escapes the LOW-effort floor and
        # gets real thinking; chit-chat keeps LOW effort for speed.
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

        # The connection (account) the primary client is using; None for a
        # shared-key provider. Drives per-account cooldowns + sibling rotation.
        _used_connection_id: str | None = None
        try:
            provider_cfg = get_builtin_provider(provider_name)
            if provider_cfg is None:
                from navig.llm.router import PROVIDER_BASE_URLS
                from navig.providers.types import ModelApi, ProviderConfig

                url = base_url or PROVIDER_BASE_URLS.get(provider_name, "https://openrouter.ai/api/v1")
                provider_cfg = ProviderConfig(
                    name=provider_name,
                    base_url=url,
                    api=ModelApi.OPENAI_COMPLETIONS,
                )
            from navig.providers.inference import resolve_rotating_credential

            # Connection-first (claude-max OAuth subscription / stored key), then
            # the shared key store — so a Claude subscription with NO API key
            # works. Rotating variant SKIPS an account that's cooling down (e.g.
            # a Claude Max subscription capped earlier this session — including by
            # the CLI/deck, which share this cooldown map) and returns which
            # account it used so a mid-turn failure can hop to a sibling.
            api_key, oauth_token, _used_connection_id = resolve_rotating_credential(
                provider_name, model_name
            )
            # Chat-feel messages get a tight 35s timeout (small model, no
            # tools). Real tool-using work keeps the 120s budget. The user
            # sees a fast error on "hey" instead of staring at 3 typing
            # indicators for 2 minutes.
            _client_timeout = _AGENTIC_CHAT_TIMEOUT if _short_chat else _AGENTIC_CLIENT_TIMEOUT
            client = create_client(
                provider_cfg, api_key=api_key, oauth_token=oauth_token, timeout=_client_timeout
            )
        except Exception as exc:
            logger.error("run_agentic: could not create LLM client: %s", exc)
            return f"Couldn't connect to the LLM provider ({provider_name}): {exc}"

        self._last_user_message = message
        self._last_account_fallback = None  # fresh per turn; set only on a rotation
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
        # Time- and query-specific context rides the *user turn* (not the cached
        # system block). The clock goes first: it is unconditional (a short "what
        # time is it" previously had no clock at all, because the slim prompt
        # carried no awareness block) and recency is highest at the front.
        prefix_parts: list[str] = []
        if now_block := self._build_turn_context():
            prefix_parts.append(f"## Now\n{now_block}")
        if not _short_chat:
            # matched SKILL.md skills first, then recalled facts.
            if skills_block := self._build_skills_section(message):
                prefix_parts.append(skills_block)
            if recall := self._recall_block(message):
                prefix_parts.append(recall)
        if prefix_parts:
            _user_content = "\n\n".join([*prefix_parts, message])

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
        #: Set once the run makes its first tool call — see the escalation below.
        _budget_escalated = False

        # Per-run id ties every StatusEvent of this turn together so a renderer can
        # group them. Emissions are best-effort and no-op without a callback, so a
        # surface that wants live progress (Telegram debug X-ray, the minimal
        # progress line) just registers on_status_update; nothing else changes.
        _run_task_id = uuid.uuid4().hex[:8]
        _tool_step = 0
        await self._emit(
            "task_start",
            _run_task_id,
            "Working…",
            provider=provider_name,
            model=model_name,
            tier=(tier_override or _resolve_mode),
            toolsets=list(toolsets),
            effort=getattr(locals().get("_effort_level", None), "name", ""),
        )

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
            from navig.agent.agent_tool_registry import is_failure_result

            try:
                args = (
                    json.loads(tool_call_item.arguments)
                    if isinstance(tool_call_item.arguments, str)
                    else (tool_call_item.arguments or {})
                )
            except json.JSONDecodeError:
                args = {}

            # Approval interlock — FAIL CLOSED. A gated tool must never execute
            # because the gate itself broke; this used to swallow every gate
            # exception and proceed (the agent-loop fail-open seam #299's
            # policy_check fix did not cover).
            try:
                from navig.tools.approval import gate_agent_tool_call

                denial = await gate_agent_tool_call(
                    tool_call_item.name,
                    parameters=args,
                    session_key=_session_key,
                )
            except Exception as exc:  # noqa: BLE001 — interlock unavailable → deny
                logger.error(
                    "approval interlock unavailable for '%s' — failing closed: %s",
                    tool_call_item.name,
                    exc,
                )
                denial = (
                    f"[Denied: approval interlock unavailable for '{tool_call_item.name}']"
                )
            if denial is not None:
                return (tool_call_item.id, denial)

            # Provable trust: adversarially verify DESTRUCTIVE tool calls before they
            # run (read-only tools skip — no latency cost on the common path). Returns
            # the verdict as the tool result so the agent can adapt instead of executing
            # an unsafe action. Best-effort; the verifier no-ops when disabled.
            try:
                # `is_destructive_tool`, not raw `in DESTRUCTIVE_TOOLS`: the set can
                # only ever hold names core knows at import time, and three other kinds
                # of tool are destructive — generated connector writes
                # (`connector_*_act`), a plugin's self-declared `safety = "dangerous"`,
                # and anything an external MCP server offers. Reading the set directly
                # meant the verifier skipped every one of them while the approval gate
                # held them, so the two disagreed about what "destructive" means.
                from navig.tools.approval import is_destructive_tool

                if is_destructive_tool(tool_call_item.name):
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

            # Inject the stable per-chat session key for any tool that declares it keeps
            # per-conversation state (`BaseTool.needs_session`). Undeclared in the tool
            # schema ⇒ invisible to the LLM; both dispatch paths forward it.
            #
            # Read from the tool's own declaration rather than a hardcoded name: this was
            # `if name == "browser_tool"`, so every future session-scoped tool needed
            # another line here — and the todo tools, which are per-chat by nature, could
            # not be wired at all without one.
            if _tool_needs_session(tool_call_item.name):
                args = {**args, "_session_id": _session_key}

            nonlocal _tool_step
            _tool_step += 1
            _my_step = _tool_step
            _arg_summary = _summarize_tool_args(args)
            _t_tool = time.monotonic()
            await self._emit(
                "step_start",
                _run_task_id,
                f"{tool_call_item.name}",
                step_index=_my_step,
                tool=tool_call_item.name,
                args_summary=_arg_summary,
            )
            try:
                from navig.agent.speculative import get_speculative_executor

                spec = get_speculative_executor()
                # dispatch()/spec.execute() are SYNCHRONOUS and can block for a
                # long time on live infra (SSH/DB/HTTP). Run them OFF the event
                # loop so a slow tool can't freeze it — and, with it, every
                # concurrent session and the "parallel" batch (which otherwise
                # isn't parallel at all). wait_for abandons a hung tool at
                # _TOOL_DISPATCH_TIMEOUT instead of wedging the turn forever.
                # (spec.aexecute keeps cache-check + speculation ON the loop while
                # offloading only the blocking dispatch; mirrors plan_execute.py.)
                if spec is not None:
                    result_str = await asyncio.wait_for(
                        spec.aexecute(tool_call_item.name, args),
                        timeout=_TOOL_DISPATCH_TIMEOUT,
                    )
                else:
                    result_str = await asyncio.wait_for(
                        asyncio.to_thread(
                            _AGENT_REGISTRY.dispatch,
                            tool_call_item.name,
                            args,
                            _vault_injector,
                        ),
                        timeout=_TOOL_DISPATCH_TIMEOUT,
                    )
            except (TimeoutError, asyncio.TimeoutError):
                # to_thread can't cancel the worker thread, but the loop is freed
                # here so the turn + every concurrent session proceed. The model
                # is told honestly the tool was abandoned (not a phantom success).
                result_str = (
                    f"[Tool error: {tool_call_item.name} exceeded "
                    f"{_TOOL_DISPATCH_TIMEOUT:.0f}s and was abandoned]"
                )
                await self._emit(
                    "step_failed",
                    _run_task_id,
                    f"{tool_call_item.name} timed out",
                    step_index=_my_step,
                    tool=tool_call_item.name,
                    args_summary=_arg_summary,
                    error=f"timeout after {_TOOL_DISPATCH_TIMEOUT:.0f}s",
                    duration_ms=int((time.monotonic() - _t_tool) * 1000),
                )
                return (tool_call_item.id, result_str)
            except Exception as exc:
                result_str = f"[Tool error: {exc}]"
                await self._emit(
                    "step_failed",
                    _run_task_id,
                    f"{tool_call_item.name} failed",
                    step_index=_my_step,
                    tool=tool_call_item.name,
                    args_summary=_arg_summary,
                    error=str(exc),
                    duration_ms=int((time.monotonic() - _t_tool) * 1000),
                )
                return (tool_call_item.id, result_str)
            # "[ERROR" = the registry's marker for a ToolResult(success=False)
            # non-raising tool failure (navig_run exit!=0, a failed db query or
            # dump, a permission-denied write). Without it, those render as a
            # green step_done in the /trace X-ray — a false-green over a failed
            # live-infra op, which NAVIG's own doctor-honesty doctrine forbids.
            # The prefix list lives in agent_tool_registry beside the code that
            # PRODUCES it: this call site used to keep a private copy, and the two
            # other consumers (plan_execute, speculative) had none at all.
            _failed = is_failure_result(result_str)
            await self._emit(
                "step_failed" if _failed else "step_done",
                _run_task_id,
                f"{tool_call_item.name}",
                step_index=_my_step,
                tool=tool_call_item.name,
                args_summary=_arg_summary,
                result_summary=_summarize_tool_result(result_str),
                duration_ms=int((time.monotonic() - _t_tool) * 1000),
            )
            return (tool_call_item.id, result_str)

        async def _sem_dispatch(tool_call_item):
            async with sem:
                return await _dispatch_single(tool_call_item)

        # Resilient fast fallback: if the resolved model hangs (timeout) or
        # errors, recover the turn ONCE on the fast small model instead of
        # losing the reply. Critical when the configured big/coder tiers point
        # at slow or unreachable endpoints (e.g. a 70B that read-times-out).
        #
        # NOTE: this flag tracks *model degradation* (dropping to the cheap
        # model) — it is deliberately NOT set by account rotation. Rotating to a
        # sibling subscription (same model, full quality) can happen repeatedly
        # across turns (A→B→C); it's self-bounding because each capped account is
        # cooled and skipped. Only the fast-model drop is once-per-message.
        _fell_back = False

        async def _fast_retry(on_partial_cb) -> "CompletionResponse | None":
            """Retry the current turn on the fast small model. Returns a
            CompletionResponse, or None when no DISTINCT fast model exists or
            the retry also fails. On success it re-points model/provider so
            usage records under the model that actually answered."""
            nonlocal model_name, provider_name
            try:
                from navig.llm.router import resolve_llm as _resolve_llm

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
                from navig.llm.router import PROVIDER_BASE_URLS
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
                from navig.providers.inference import resolve_provider_credential

                fb_key, fb_oauth = resolve_provider_credential(fb.provider)
                fb_client = create_client(
                    fb_cfg, api_key=fb_key, oauth_token=fb_oauth, timeout=_AGENTIC_CHAT_TIMEOUT
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
                    _tc: list[Any] = []
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
                            # Same contract as the main streamed turn: the
                            # fallback request carries tools too, so dropping
                            # tool-call deltas here would resurrect the empty
                            # reply on exactly the path taken when the primary
                            # model already failed.
                            if getattr(ch, "tool_call_delta", None) is not None:
                                _tc.append(ch)
                            if getattr(ch, "finish_reason", None):
                                _fin = ch.finish_reason
                            if getattr(ch, "usage", None):
                                _usg = ch.usage
                            if getattr(ch, "model", None):
                                _mdl = ch.model

                    await asyncio.wait_for(_drive_fb(), timeout=_AGENTIC_CHAT_TIMEOUT)
                    result = CompletionResponse(
                        content="".join(_acc) or None,
                        tool_calls=merge_tool_call_deltas(_tc) or None,
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

        async def _account_retry(req, reason: str, *, stream: bool = False) -> "CompletionResponse | None":
            """Retry the SAME request (model + tools) on a SIBLING account of the
            same provider — recovering a capped Claude Max subscription WITHIN the
            turn, before dropping to the fast model. When *stream* is set (a
            chat-feel turn with an ``on_partial`` sink), the sibling **streams**
            too so the Telegram edit stays live through the rotation; otherwise a
            single blocking call.

            On success it **adopts** the sibling as the turn's ``client`` (closing
            the capped one) so later iterations don't keep hitting the dead
            account, updates ``_used_connection_id`` (keeping per-account cooldown
            marking correct), and records the winning account on
            ``self._last_account_fallback``. Returns the response, or None."""
            nonlocal _used_connection_id, client
            try:
                from navig.llm.fallback_policy import (
                    account_cool_key,
                    categorize_error,
                    mark_cooldown,
                )
                from navig.providers.inference import (
                    accounts_to_try,
                    credential_for_connection,
                    list_provider_connections,
                )
            except Exception:  # noqa: BLE001
                return None

            async def _aclose(c) -> None:
                _c = getattr(c, "close", None)
                if callable(_c):
                    try:
                        await _c()
                    except Exception:  # noqa: BLE001
                        pass

            # Sibling accounts to try — shared selection with the CLI/ask path via
            # `accounts_to_try` (skips the just-failed account + any cooling; no
            # re-probe here — a mid-turn dead end drops to the fast model).
            siblings = accounts_to_try(
                list_provider_connections(provider_name), provider_name, model_name,
                exclude_connection_id=_used_connection_id, reprobe_when_all_cooling=False,
            )
            for acct in siblings:
                cid = acct.get("connection_id")
                cool_key = account_cool_key(provider_name, model_name, cid)
                cred = credential_for_connection(acct, provider_name)
                if not cred or not (cred[0] or cred[1]):
                    continue
                acct_client = create_client(
                    provider_cfg, api_key=cred[0], oauth_token=cred[1], timeout=_client_timeout
                )
                try:
                    if stream and on_partial is not None and hasattr(acct_client, "complete_stream"):
                        _acc: list[str] = []
                        _tc: list[Any] = []
                        _fin: str | None = None
                        _usg: dict | None = None
                        _mdl: str | None = None

                        # Bind the loop-varying client + accumulators as defaults so the
                        # closure captures THIS iteration's values (ruff B023), even though
                        # it is awaited immediately below.
                        async def _drive_acct(_client=acct_client, _out=_acc, _calls=_tc) -> None:
                            nonlocal _fin, _usg, _mdl
                            async for ch in _client.complete_stream(req):
                                d = getattr(ch, "delta", None)
                                if d:
                                    _out.append(d)
                                    try:
                                        await on_partial("".join(_out))
                                    except Exception:  # noqa: BLE001
                                        pass
                                # Rotating to a sibling account must not cost the
                                # turn its tool calls — same contract as above.
                                if getattr(ch, "tool_call_delta", None) is not None:
                                    _calls.append(ch)
                                if getattr(ch, "finish_reason", None):
                                    _fin = ch.finish_reason
                                if getattr(ch, "usage", None):
                                    _usg = ch.usage
                                if getattr(ch, "model", None):
                                    _mdl = ch.model

                        await asyncio.wait_for(_drive_acct(), timeout=_client_timeout)
                        resp = CompletionResponse(
                            content="".join(_acc) or None,
                            tool_calls=merge_tool_call_deltas(_tc) or None,
                            finish_reason=_fin, usage=_usg,
                            model=_mdl or model_name, provider=provider_name,
                        )
                    else:
                        resp = await asyncio.wait_for(
                            acct_client.complete(req), timeout=_client_timeout
                        )
                except Exception as _exc:  # noqa: BLE001
                    mark_cooldown(cool_key, categorize_error(_exc))
                    logger.warning("run_agentic: sibling account %s also failed (%s)", cid[:8], _exc)
                    await _aclose(acct_client)  # only close on FAILURE
                    continue
                # Success — adopt the sibling client for the rest of the turn and
                # retire the capped one.
                await _aclose(client)
                client = acct_client
                _used_connection_id = cid
                self._last_account_fallback = {
                    "to": acct.get("name") or f"account {cid[:8]}",
                    "connection_id": cid,
                    "provider": provider_name,
                    "model": model_name,
                    "reason": reason,
                }
                logger.warning(
                    "run_agentic: rotated to sibling account %s for %s:%s (primary %s)",
                    acct.get("name") or cid[:8], provider_name, model_name, reason,
                )
                return resp
            return None

        while not budget.is_exhausted():
            turn += 1
            budget.consume(1)

            # One "thinking" beat per ReAct turn. The renderer shows a calm
            # "thinking…" in normal mode and a numbered turn in the debug X-ray.
            await self._emit(
                "thinking",
                _run_task_id,
                "Thinking…",
                step_index=turn,
                provider=provider_name,
                model=model_name,
            )

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
                        # Compaction just dropped older turns. Hand back the slice of
                        # what was dropped that is still relevant to the current ask —
                        # "" when the feature is off or nothing matched, so this is a
                        # no-op by default (memory.session_index.enabled).
                        from navig.memory.session_index import (
                            recover_context,
                            session_index_enabled,
                        )

                        # Flag first (see the tool-result site): off by default, and then
                        # this costs nothing. When on it runs off the loop thread — a BM25
                        # search over a long session is tens of milliseconds, mid-turn.
                        if session_index_enabled():
                            # Query from the PRE-compaction list. `recover_context` picks
                            # the newest user message to search for, and compaction is
                            # precisely what removes it: measured under a full-suite run,
                            # a 12-message compaction left 7 messages with no user turn
                            # among them, so the query was empty and recovery returned ""
                            # every time — the feature silently doing nothing in exactly
                            # the case it exists for. `msg_dicts` is the same conversation
                            # one step earlier and always still contains the current ask.
                            # Its own handler. The compaction above has ALREADY been
                            # applied to working_messages by this point, so a failure here
                            # is not "compression skipped" — that succeeded. Reporting it
                            # under the outer message described a step that had worked and
                            # sent anyone reading the log to the wrong subsystem.
                            try:
                                _recovered = await asyncio.to_thread(
                                    recover_context, self._session_id, msg_dicts
                                )
                                if _recovered:
                                    working_messages.append(
                                        Message(role="system", content=_recovered)
                                    )
                            except Exception as exc:  # noqa: BLE001
                                logger.debug(
                                    "Context compaction succeeded; recovering dropped "
                                    "context failed (%s) — continuing without it",
                                    exc,
                                )
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

            # Streaming path: enabled when the caller passed an on_partial
            # callback AND this is a chat-feel turn. The accumulated text is
            # passed to on_partial each chunk; the caller (Telegram) is
            # expected to debounce edit calls itself.
            #
            # "Chat-feel" is a guess about SHAPE, never a guarantee of NO TOOL
            # USE — the request still advertises the full toolset with
            # tool_choice="auto", so the model may answer a short message with a
            # tool call. This branch used to hardcode `tool_calls=None`, so it
            # did: a one-line message like a bare TikTok URL made the model call
            # a tool, every tool_call delta was dropped on the floor, and the
            # turn came back with empty content and no calls — which the loop
            # below reads as "the model had nothing to say" and ends. The reply
            # then blamed a turn limit that was nowhere near reached (1 turn of
            # 90). Tool-call deltas are reassembled instead; the transport has
            # always carried them.
            _can_stream = (
                on_partial is not None
                and _short_chat
                and hasattr(client, "complete_stream")
            )
            try:
                if _can_stream:
                    _accum: list[str] = []
                    _tool_chunks: list[Any] = []
                    _final_finish: str | None = None
                    _final_usage: dict | None = None
                    _final_model: str | None = None

                    async def _drive_stream() -> None:
                        nonlocal _final_finish, _final_usage, _final_model
                        async for chunk in client.complete_stream(request):  # noqa: B023 — awaited in-iteration; closure never outlives the loop step
                            delta = getattr(chunk, "delta", None)
                            if delta:
                                _accum.append(delta)  # noqa: B023 — same
                                try:
                                    await on_partial("".join(_accum))  # noqa: B023 — same
                                except Exception as exc:  # noqa: BLE001
                                    logger.debug(
                                        "on_partial callback raised %r; continuing",
                                        exc,
                                    )
                            # Keep tool-call fragments — they are reassembled
                            # after the stream ends. Never surfaced to
                            # on_partial: a half-built JSON argument is not text
                            # a user should see.
                            if getattr(chunk, "tool_call_delta", None) is not None:
                                _tool_chunks.append(chunk)  # noqa: B023 — same
                            if getattr(chunk, "finish_reason", None):
                                _final_finish = chunk.finish_reason
                            if getattr(chunk, "usage", None):
                                _final_usage = chunk.usage
                            if getattr(chunk, "model", None):
                                _final_model = chunk.model

                    await asyncio.wait_for(_drive_stream(), timeout=_client_timeout)
                    _streamed_calls = merge_tool_call_deltas(_tool_chunks)
                    # Synthesise a CompletionResponse so the rest of the
                    # loop (usage tracking, history append, finish_reason
                    # handling) keeps working unchanged.
                    response = CompletionResponse(
                        content="".join(_accum) or None,
                        tool_calls=_streamed_calls or None,
                        finish_reason=_final_finish,
                        usage=_final_usage,
                        model=_final_model or model_name,
                        provider=provider_name,
                    )
                    if not _accum and not _streamed_calls:
                        # Neither text nor a tool call: there is nothing to reply
                        # with and nothing to execute, so the turn is a dead end
                        # for the caller. Retry it ONCE unstreamed rather than
                        # surfacing the void — this also covers any provider
                        # whose delta shape we don't parse. Bounded: one extra
                        # call, only when the stream produced literally nothing.
                        logger.warning(
                            "run_agentic: streamed turn %d yielded no text and no "
                            "tool calls (%s/%s) — retrying unstreamed",
                            turn, provider_name, model_name,
                        )
                        response = await asyncio.wait_for(
                            client.complete(request), timeout=_client_timeout
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
                _fb = None
                if not _fell_back:
                    from navig.llm.fallback_policy import (
                        account_cool_key,
                        categorize_error,
                        mark_cooldown,
                        should_rotate_account,
                    )

                    _cat = categorize_error(exc)
                    # Record the cap on the account we used so later turns — and
                    # the CLI/deck, which share this cooldown map — skip it.
                    if _used_connection_id:
                        mark_cooldown(
                            account_cool_key(provider_name, model_name, _used_connection_id), _cat
                        )
                    # 1) Same model on a SIBLING account (full quality — e.g. a
                    #    second Claude Max subscription). Repeatable across turns
                    #    (A→B→C) since it's NOT a quality degradation and is
                    #    self-bounding: each capped account is cooled + skipped.
                    if should_rotate_account(_cat):
                        _fb = await _account_retry(request, _cat, stream=_can_stream)
                    # 2) No sibling account left → drop to the fast model, ONCE.
                    if _fb is None:
                        _fb = await _fast_retry(on_partial)
                        if _fb is not None:
                            _fell_back = True
                if _fb is not None:
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

            # The moment the model reaches for a tool, this run stopped being
            # small talk — whatever the message LOOKED like. The chat budget
            # (256 tokens, 35s) is sized for "hey"/"thanks", and it was decided
            # once, from message shape, before anyone knew a tool was coming: a
            # bare link is 33 characters, so a link → fetch → summarise run got
            # 256 tokens to answer in and was cut off mid-sentence. Escalate to
            # the tool-work budget for the REST of the run (once — the flag makes
            # it idempotent, and it never lowers a budget the caller already set
            # higher).
            if not _budget_escalated:
                _budget_escalated = True
                if max_tokens < _AGENTIC_DEFAULT_MAXTOK:
                    logger.debug(
                        "run_agentic: tool call on turn %d — raising max_tokens "
                        "%d→%d and timeout %.0fs→%.0fs for the rest of the run",
                        turn, max_tokens, _AGENTIC_DEFAULT_MAXTOK,
                        _client_timeout, _AGENTIC_CLIENT_TIMEOUT,
                    )
                    max_tokens = _AGENTIC_DEFAULT_MAXTOK
                if _client_timeout < _AGENTIC_CLIENT_TIMEOUT:
                    _client_timeout = _AGENTIC_CLIENT_TIMEOUT

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

            # Route each result back by the tool-call OBJECT, never the provider
            # id string. Some OpenAI-compatible providers omit or duplicate
            # tool-call ids (ToolCall.id defaults to "" in providers/clients), and
            # an id-keyed dict(collected_results) then collapses: every call
            # sharing that id reads the one surviving result, so the model is
            # handed tool A's live-infra output labeled as tool B's (e.g. host A's
            # disk usage reported for host B) with no error — the [result missing]
            # fallback can't even fire because the "" key exists. id() is unique
            # per live pending call (all are referenced for this whole block, so
            # none can be GC'd and have its address reused).
            results_by_call: dict[int, str] = {}

            if parallel_batch:
                par_results = await asyncio.gather(
                    # Via the semaphore so _MAX_PARALLEL_TOOLS is actually enforced
                    # (the batch previously called _dispatch_single directly).
                    *[_sem_dispatch(tool_call) for tool_call in parallel_batch],
                    return_exceptions=True,
                )
                for idx, result in enumerate(par_results):
                    tc = parallel_batch[idx]
                    if isinstance(result, BaseException):
                        results_by_call[id(tc)] = f"[Tool error: {result}]"
                    else:
                        # _dispatch_single -> (tool_call.id, result_str); keep the text
                        results_by_call[id(tc)] = result[1]

            for tool_call in sequential_batch:
                _res = await _dispatch_single(tool_call)
                results_by_call[id(tool_call)] = _res[1]

            for tool_call in pending_calls:
                _tool_result = results_by_call.get(id(tool_call), "[Tool error: result missing]")
                working_messages.append(
                    Message(
                        role="tool",
                        content=_tool_result,
                        # Echo the provider's own id back (even if empty/duplicate)
                        # so id-correlating providers can still match; the content
                        # routing above never depends on it.
                        tool_call_id=tool_call.id,
                    )
                )
                # Index the result so a later compaction can hand back the parts that
                # still matter. Never raises.
                # The flag is checked HERE, not just inside record_event, so the default
                # (disabled) path costs one cached dict lookup and nothing else — an
                # unconditional `await asyncio.to_thread(...)` would add a thread hop to
                # every tool result whether or not the feature is on, which is real
                # latency in the hot loop. When enabled it runs off the loop thread: the
                # write is sub-millisecond, but a contended database waits out its
                # busy_timeout, and that must not stall the turn.
                from navig.memory.session_index import (
                    event_kind_for_tool,
                    record_event,
                    session_index_enabled,
                )

                try:
                    if session_index_enabled():
                        await asyncio.to_thread(
                            record_event,
                            self._session_id,
                            event_kind_for_tool(tool_call.name),
                            _tool_result,
                        )
                except Exception as exc:  # noqa: BLE001
                    # Memory is an augmentation, so a failure here must never take the
                    # user's turn with it — the same contract memory_auto_extractor
                    # states ("errors never interrupt the conversation"), and the shape
                    # the compaction block above already has. record_event is best-effort
                    # for the sqlite errors it expects, but it is reached through
                    # to_thread, which re-raises into this coroutine: without this, one
                    # UNexpected error aborts a turn that had already done its work.
                    logger.debug("session index recording skipped: %s", exc)

        if not final_response:
            # Two unrelated failures used to share one message, and only one of
            # them was ever a turn limit. Blaming the limit when the budget was
            # untouched (the operator saw "the 1-turn limit" with 89 of 90 turns
            # still available) points at the user's phrasing for something no
            # rephrasing can fix, and hides the real event. Say which happened.
            if budget.is_exhausted():
                final_response = (
                    f"Agent reached the {turn}-turn limit without a final answer. "
                    "Try a more specific request."
                )
            else:
                final_response = (
                    f"No answer came back — {provider_name}/{model_name} returned "
                    f"an empty response on turn {turn}. Try again, or switch model."
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
            from navig.agent.speculative import get_speculative_executor

            # Read-only stats snapshot ONLY. Do NOT cancel_speculations() or
            # reset the SHARED singleton per turn — that raced concurrent turns
            # (each cancelling the other's in-flight speculations + nulling the
            # global mid-turn) AND defeated the cross-turn cache (every turn
            # restarted cold). The executor is process-lived with a TTL cache.
            spec = get_speculative_executor()
            if spec is not None:
                cache_stats = (spec.stats.get("cache") or {})
                if cache_stats.get("hits", 0) > 0:
                    logger.info(
                        "speculative cache stats: hits=%d misses=%d hit_rate=%.1f%%",
                        cache_stats.get("hits", 0),
                        cache_stats.get("misses", 0),
                        float(cache_stats.get("hit_rate", 0.0)) * 100,
                    )
        except Exception as exc:
            logger.debug("speculative stats read skipped: %s", exc)

        # Close the primary LLM client's httpx pool — otherwise every agentic turn
        # leaks an open connection pool / sockets until GC (the fast-retry fb_client
        # is already closed; the primary client was not).
        try:
            _close = getattr(client, "close", None)
            if _close:
                await _close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("agent client close skipped: %s", exc)

        cost = tracker.session_cost()
        logger.info("run_agentic completed: %s", cost.summary_str())

        # Final beat with the totals a debug X-ray shows. All of this was already
        # collected (CostTracker) and previously just logged and discarded; now a
        # subscribed surface can render it. Best-effort as ever.
        try:
            _fb = self._last_account_fallback or {}
            await self._emit(
                "task_done",
                _run_task_id,
                cost.summary_str(),
                total_steps=_tool_step,
                turns=turn,
                tools_run=_tool_step,
                model=model_name,
                provider=provider_name,
                total_tokens=getattr(cost, "total_tokens", 0),
                cost_usd=round(float(getattr(cost, "total_usd", 0.0)), 6),
                account_fallback=(_fb.get("to") if _fb else ""),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("task_done emit skipped: %s", exc)
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
        # The clock lives on the user turn in run_agentic, but this single-shot
        # path builds no user turn of its own — the message is already in history.
        # Append it LAST so the stable sections above keep their order, and so it
        # stays after any future cache breakpoint. This path sets no
        # ``cache_control`` (it predates the agentic loop), so the volatility is
        # free here; without it a fallback turn has no idea what day it is.
        if now_block := self._build_turn_context():
            msgs[0]["content"] += f"\n\n## Now\n{now_block}"
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
            from navig.llm.routing.router import RouteRequest, get_router

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
