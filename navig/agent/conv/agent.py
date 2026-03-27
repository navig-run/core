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
        self._focus_mode = self._last_user_message = self._tier_override = ""
        self._entrypoint, self.context = "channel", {}

    @property
    def on_status_update(self) -> Callable[[StatusEvent], Awaitable[None]] | None:
        """Current StatusEvent callback (may be a shim-wrapped legacy callable)."""
        return self._on_status_update

    @on_status_update.setter
    def on_status_update(self, cb: Callable | None) -> None:
        """Set callback, applying legacy-shim if *cb* accepts a plain ``str``."""
        if cb is not None:
            try:
                sig = inspect.signature(cb)
                params = list(sig.parameters.values())
                if params:
                    ann = params[0].annotation
                    # Legacy detection: annotation is str type or the string 'str'
                    # (from __future__ import annotations makes annotations strings at runtime)
                    is_legacy = (
                        ann is str
                        or ann == "str"
                        or (ann is inspect.Parameter.empty and len(params) == 1)
                    )
                    if is_legacy:
                        _legacy = cb
                        if inspect.iscoroutinefunction(_legacy) or inspect.iscoroutinefunction(
                            getattr(_legacy, "__call__", None)  # noqa: B004
                        ):

                            async def cb(event: StatusEvent, _cb: Callable = _legacy) -> None:  # noqa: E731
                                await _cb(event.message)

                        else:

                            def cb(event: StatusEvent, _cb: Callable = _legacy) -> None:  # noqa: E731
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
        now = datetime.now()
        h = now.hour
        tod = (
            "morning"
            if 5 <= h < 12
            else ("afternoon" if 12 <= h < 18 else "evening" if 18 <= h < 22 else "late night")
        )
        parts = [f"Current time: {now.strftime('%H:%M')} ({tod}), {now.strftime('%A %d %B %Y')}."]
        if uname := self._user_identity.get("username", ""):
            parts.append(f"You are talking to {uname} (your operator). Address them naturally.")
        elif uid := self._user_identity.get("user_id", ""):
            parts.append(f"User ID: {uid}.")
        return "\n".join(parts)

    def _build_system_prompt(self, user_message: str) -> str:
        code = self._lang.detect(user_message)
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
        lang = self._lang.detect(message)
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
        if lang == "ru":
            from navig.agent.conv.history import _strip_cjk

            response = _strip_cjk(response)
        plan = self._plan_extractor.extract(response)
        if plan:
            result = await self._executor.execute_plan(plan)
            self._history.add("assistant", result)
            return result
        self._history.add("assistant", response)
        return response

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
        system_prompt = self._build_system_prompt(message)
        msgs = [
            {"role": "system", "content": system_prompt},
            *self._history.get_messages(),
        ]
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
        # Optional streaming path — only activates if ai_client exposes chat_stream
        if hasattr(self._ai_client, "chat_stream"):
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
        try:
            from navig.routing.router import RouteRequest, get_router

            text = (
                await get_router().run(
                    RouteRequest(
                        messages=msgs,
                        text=message,
                        tier_override=tier,
                        entrypoint=self._entrypoint,
                    )
                )
            )[0]
            if text:
                return text
        except Exception as exc:
            logger.warning("Unified router failed: %s", exc)
        if hasattr(self._ai_client, "chat_routed"):
            return await self._ai_client.chat_routed(msgs, user_message=message, tier_override=tier)
        return await self._ai_client.chat(msgs)
