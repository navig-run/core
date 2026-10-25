"""
navig.agent.tools.ask_user_tools — structured operator questions (Mission B 4a).

A single model-facing ``ask_user`` tool over the EXISTING request
infrastructure (:class:`navig.requests.RequestRegistry`) — the same surface the
deck's /api/deck/requests stream and the OS question card render.  No second
request manager: this module only binds a registry+loop and bridges the tool's
worker thread onto it.

Contract:
- Only available where a gateway bound the registry (schema-time ``check_fn``
  hides it elsewhere; a dispatched call still returns an honest refusal —
  never a fabricated answer).
- One question per call, ≤6 label options, optional free text, timeout 5–90 s
  (always below the 120 s tool-dispatch cap).
- Correlation is by the registry's own request id — never arrival order.
- ``ask()`` pops its own pending entry in a ``finally``, so a call can never
  leave a dangling request behind; timeouts/cancels surface as honest
  "no answer" results.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)

#: Hard bounds (module-level so tests can tighten them).
_TIMEOUT_DEFAULT_S = 60
_TIMEOUT_MIN_S = 5
_TIMEOUT_MAX_S = 90  # < conv/agent.py _TOOL_DISPATCH_TIMEOUT (120 s)
_MAX_OPTIONS = 6

#: Bound by the gateway at boot (registry + the loop it lives on).
_REGISTRY: Any | None = None
_LOOP: asyncio.AbstractEventLoop | None = None

#: Honest refusal shown wherever the registry is not bound.
_UNBOUND_MESSAGE = (
    "ask_user is not available in this session (no live gateway question "
    "channel). Ask the user in plain text instead, then stop and wait for "
    "their reply — never invent an answer."
)


def bind_request_registry(
    registry: Any, loop: asyncio.AbstractEventLoop | None = None
) -> None:
    """Bind the gateway's registry (and its loop) for the ``ask_user`` tool.

    Called from gateway startup next to ``bind_approval_manager``.  The loop
    is captured at bind time so tool dispatches on worker threads can bridge
    onto it with :func:`asyncio.run_coroutine_threadsafe`.
    """
    global _REGISTRY, _LOOP
    _REGISTRY = registry
    if loop is not None:
        _LOOP = loop
    else:
        try:
            _LOOP = asyncio.get_running_loop()
        except RuntimeError:
            _LOOP = None
    logger.debug("ask_user: request registry bound (loop=%s)", _LOOP is not None)


def unbind_request_registry() -> None:
    """Drop the binding (gateway shutdown)."""
    global _REGISTRY, _LOOP
    _REGISTRY = None
    _LOOP = None


def _is_bound() -> bool:
    """Schema-time availability: a live registry on a non-closed loop."""
    return (
        _REGISTRY is not None
        and _LOOP is not None
        and not _LOOP.is_closed()
    )


def _clamp_timeout(raw: Any) -> int:
    try:
        value = int(raw) if raw is not None else _TIMEOUT_DEFAULT_S
    except (TypeError, ValueError):
        value = _TIMEOUT_DEFAULT_S
    return max(_TIMEOUT_MIN_S, min(value, _TIMEOUT_MAX_S))


class AskUserTool(BaseTool):
    """Ask the operator one structured question and wait for their answer."""

    name = "ask_user"
    description = (
        "Ask the operator a single question and wait for their answer. Provide "
        "short option labels (comma-separated) plus optional free text. Returns "
        "the operator's choice. On timeout or when no question channel exists, "
        "it fails honestly — then ask in plain text and stop; never invent an "
        "answer."
    )
    owner_only = False
    needs_session = True
    parameters = [
        {
            "name": "question",
            "type": "string",
            "description": "The question to ask the operator",
            "required": True,
        },
        {
            "name": "options",
            "type": "string",
            "description": (
                "Comma-separated option labels (max 6), e.g. 'Yes, No, Not sure'"
            ),
            "required": False,
        },
        {
            "name": "allow_free_text",
            "type": "boolean",
            "description": "Also allow a typed (free-text) answer (default: true)",
            "required": False,
        },
        {
            "name": "timeout_seconds",
            "type": "integer",
            "description": f"Seconds to wait (default {_TIMEOUT_DEFAULT_S}, max {_TIMEOUT_MAX_S})",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        t0 = time.monotonic()

        def _fail(msg: str) -> ToolResult:
            return ToolResult(
                name=self.name,
                success=False,
                output=msg,
                error=msg,
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        question = str(args.get("question") or "").strip()
        if not question:
            return _fail("'question' arg is required")

        options_raw = str(args.get("options") or "").strip()
        labels: list[str] = []
        if options_raw:
            seen: set[str] = set()
            for piece in options_raw.split(","):
                label = piece.strip()
                if label and label not in seen:
                    seen.add(label)
                    labels.append(label)
            if len(labels) > _MAX_OPTIONS:
                return _fail(
                    f"Too many options ({len(labels)}; max {_MAX_OPTIONS}) — "
                    "shorten the option list"
                )

        registry, loop = _REGISTRY, _LOOP
        if registry is None or loop is None or loop.is_closed():
            return _fail(_UNBOUND_MESSAGE)

        timeout_s = _clamp_timeout(args.get("timeout_seconds"))
        allow_free_text = bool(args.get("allow_free_text", True))
        session_id = str(args.get("_session_id") or "default")
        source = f"agent-ask:{session_id}"

        coro = registry.ask(
            title=question,
            body="",
            options=[{"label": label} for label in labels],
            allow_custom=allow_free_text,
            allow_multi=False,
            kind="question",
            source=source,
            timeout=timeout_s,
        )

        try:
            if asyncio.get_running_loop() is loop:
                # Called on the registry's own loop (tests / future in-loop
                # callers): await directly — run_coroutine_threadsafe from the
                # loop's own thread would deadlock.
                answer = await coro
            else:
                future = asyncio.run_coroutine_threadsafe(coro, loop)
                try:
                    answer = future.result(timeout=timeout_s + 5)
                except TimeoutError:  # concurrent.futures.Future.result (py3.11+: builtin)
                    future.cancel()
                    return _fail(
                        f"The operator did not answer within {timeout_s}s — ask "
                        "in plain text instead and stop; never invent an answer."
                    )
        except Exception as exc:  # noqa: BLE001 — the tool reports, never raises
            logger.debug("ask_user bridge failed: %s", exc)
            return _fail(
                f"The question channel failed ({exc}) — ask in plain text "
                "instead and stop."
            )

        elapsed_note = f" ({(time.monotonic() - t0):.1f}s)"
        if answer is None:
            return _fail(
                f"The operator did not answer within {timeout_s}s{elapsed_note} — "
                "ask in plain text instead and stop; never invent an answer."
            )
        if isinstance(answer, dict) and answer.get("auto"):
            return ToolResult(
                name=self.name,
                success=True,
                output=f"Auto-dispatched without waiting.{elapsed_note}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        if not isinstance(answer, dict):
            return _fail(f"Unexpected answer shape from the question channel: {answer!r}")

        choice = answer.get("choice")
        custom = answer.get("custom")
        parts: list[str] = []
        if isinstance(choice, list):
            parts.append("Chosen: " + ", ".join(str(c) for c in choice))
        elif choice:
            parts.append(f"Chosen: {choice}")
        if custom:
            parts.append(f"Typed: {custom}")
        if not parts:
            return _fail("The operator submitted an empty answer — ask again in plain text.")
        return ToolResult(
            name=self.name,
            success=True,
            output="\n".join(parts) + elapsed_note,
            elapsed_ms=(time.monotonic() - t0) * 1000,
        )


def register_ask_user_tool() -> None:
    """Register ``ask_user`` (toolset ``questions``; hidden when unbound)."""
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY

    _AGENT_REGISTRY.register(
        AskUserTool(), toolset="questions", check_fn=_is_bound
    )
    logger.debug("Agent tool registered: ask_user")
