"""
Auto-Compact Manager — Automatic context-window compression with circuit breaker.

Ported and adapted from Claude Code's TypeScript ``autoCompact.ts``.

- Monitors token usage after every LLM response.
- When usage exceeds ``context_window - buffer_tokens``, triggers a non-blocking
  forked compaction task that calls ``ConversationStore.compact_session()``.
- A circuit breaker halts auto-compaction after
  ``max_consecutive_failures`` consecutive failures so the session is never
  stuck in a compaction loop.

Usage::

    from navig.memory.auto_compact import AutoCompactManager, get_auto_compact_manager

    manager = get_auto_compact_manager(session_key="telegram:user:123")
    if manager.should_compact(tokens_used=180_000, context_window=200_000):
        await manager.trigger_compact_async(session_key, messages)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from collections.abc import Sequence

logger = logging.getLogger("navig.memory.auto_compact")

# ── Module-level constants ────────────────────────────────────────────────────
# Tokens reserved below the context-window limit before compaction fires.
_DEFAULT_BUFFER_TOKENS: int = 13_000
# How many consecutive compact failures are tolerated before the circuit opens.
_DEFAULT_MAX_CONSECUTIVE_FAILURES: int = 3
# Minimum turn count before auto-compact can fire (avoids compacting 2-line sessions).
_DEFAULT_MIN_TURNS: int = 5


# ── State dataclass (one per active session) ─────────────────────────────────

@dataclass
class _CompactState:
    consecutive_failures: int = 0
    circuit_open: bool = False
    compacting: bool = False   # True while a forked task is running
    total_compactions: int = 0


# ── Manager ──────────────────────────────────────────────────────────────────

class AutoCompactManager:
    """Per-session auto-compact state machine.

    This object is intentionally cheap to create (no I/O on init); all heavy
    work is deferred to ``trigger_compact_async``.
    """

    def __init__(
        self,
        session_key: str,
        *,
        buffer_tokens: int = _DEFAULT_BUFFER_TOKENS,
        max_consecutive_failures: int = _DEFAULT_MAX_CONSECUTIVE_FAILURES,
        min_turns: int = _DEFAULT_MIN_TURNS,
    ) -> None:
        self.session_key = session_key
        self._buffer_tokens = buffer_tokens
        self._max_failures = max_consecutive_failures
        self._min_turns = min_turns
        self._state = _CompactState()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def circuit_open(self) -> bool:
        """True when the circuit breaker has tripped (too many failures)."""
        return self._state.circuit_open

    @property
    def is_compacting(self) -> bool:
        """True while an async compaction task is running in the background."""
        return self._state.compacting

    def should_compact(
        self,
        tokens_used: int,
        context_window: int,
        turn_count: int = 0,
    ) -> bool:
        """Return True when auto-compaction should be triggered.

        Args:
            tokens_used:    Tokens consumed so far in this session.
            context_window: Model's full context window size.
            turn_count:     Number of turns in the current session (optional guard).
        """
        if self._state.circuit_open:
            logger.debug(
                "auto_compact: circuit open for %s — skipping", self.session_key
            )
            return False
        if self._state.compacting:
            return False
        if turn_count < self._min_turns:
            return False
        threshold = context_window - self._buffer_tokens
        return tokens_used >= threshold

    async def trigger_compact_async(
        self,
        messages: Sequence[object],  # list[ConversationMessage] — avoid circular import
        *,
        effort: str = "low",
        extra_instructions: str | None = None,
    ) -> None:
        """Trigger a non-blocking compaction task.

        The compaction runs in a forked ``asyncio.Task`` so it never
        suspends the main request loop. Failures are recorded for the
        circuit breaker.
        """
        if self._state.compacting:
            logger.debug(
                "auto_compact: already compacting %s — ignoring duplicate trigger",
                self.session_key,
            )
            return

        logger.info(
            "auto_compact: launching forked compact for session %s (%d messages)",
            self.session_key,
            len(messages),  # type: ignore[arg-type]
        )
        self._state.compacting = True
        asyncio.create_task(
            self._run_compact(messages, effort=effort, extra_instructions=extra_instructions),
            name=f"auto-compact:{self.session_key}",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run_compact(
        self,
        messages: Sequence[object],
        *,
        effort: str,
        extra_instructions: str | None,
    ) -> None:
        """Execute compaction and update circuit-breaker state."""
        try:
            summary = await _summarise_messages(
                messages,  # type: ignore[arg-type]
                effort=effort,
                extra_instructions=extra_instructions,
            )
            if summary:
                await _persist_compact(self.session_key, summary)
                self._state.consecutive_failures = 0
                self._state.total_compactions += 1
                logger.info(
                    "auto_compact: session %s compacted (total=%d)",
                    self.session_key,
                    self._state.total_compactions,
                )
            else:
                self._record_failure("empty summary returned")
        except Exception as exc:  # noqa: BLE001
            self._record_failure(str(exc))
        finally:
            self._state.compacting = False

    def _record_failure(self, reason: str) -> None:
        self._state.consecutive_failures += 1
        logger.warning(
            "auto_compact: failure #%d for %s: %s",
            self._state.consecutive_failures,
            self.session_key,
            reason,
        )
        if self._state.consecutive_failures >= self._max_failures:
            self._state.circuit_open = True
            logger.error(
                "auto_compact: circuit opened for %s after %d consecutive failures",
                self.session_key,
                self._state.consecutive_failures,
            )


# ── Async helpers (thin wrappers over sync library calls) ────────────────────

async def _summarise_messages(
    messages: list[object],
    *,
    effort: str,
    extra_instructions: str | None,
) -> str | None:
    """Call the LLM synchronously in an executor thread to avoid blocking."""
    import functools

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        functools.partial(
            _sync_summarise, messages, effort=effort, extra_instructions=extra_instructions
        ),
    )


def _sync_summarise(
    messages: list[object],
    *,
    effort: str,
    extra_instructions: str | None,
) -> str | None:
    try:
        from navig.llm_generate import run_llm

        system_prompt = (
            "You are a concise conversation summariser operating inside an automated "
            "system. Read the conversation below and write a clear, specific 3–6 sentence "
            "summary capturing: what was discussed, key decisions made, and the most "
            "important next step. Be concrete — name specific technologies, commands, or "
            "file paths mentioned. This summary will replace the full conversation history "
            "to preserve context within the context window."
        )
        if extra_instructions:
            system_prompt += f"\n\nAdditional focus: {extra_instructions}"

        # Build a condensed transcript (cap at 150 messages to avoid huge context calls)
        excerpt = list(messages)[-150:]
        history_lines: list[str] = []
        for m in excerpt:
            role = getattr(m, "role", "unknown")
            content = getattr(m, "content", "")
            history_lines.append(f"{role.upper()}: {str(content)[:600]}")

        llm_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "\n".join(history_lines)},
        ]
        result = run_llm(llm_messages, mode="summary", effort=effort)
        return (result.content or "").strip() or None
    except Exception as exc:  # noqa: BLE001
        logger.debug("auto_compact._sync_summarise failed: %s", exc)
        return None


async def _persist_compact(session_key: str, summary: str) -> None:
    """Write the compaction summary back to the conversation store."""
    import functools

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, functools.partial(_sync_persist, session_key, summary)
    )


def _sync_persist(session_key: str, summary: str) -> None:
    try:
        from pathlib import Path

        from navig.config import get_config_manager
        from navig.memory import ConversationStore

        cfg = get_config_manager()
        db_path = Path(cfg.global_config_dir) / "memory" / "memory.db"
        if not db_path.exists():
            logger.debug("auto_compact._sync_persist: no memory.db at %s", db_path)
            return
        store = ConversationStore(db_path)
        try:
            deleted = store.compact_session(session_key, summary)
            logger.info(
                "auto_compact._sync_persist: %d messages replaced for %s",
                deleted,
                session_key,
            )
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("auto_compact._sync_persist failed: %s", exc)


# ── Process-wide registry (one manager per session key) ──────────────────────

_managers: dict[str, AutoCompactManager] = {}


def get_auto_compact_manager(
    session_key: str,
    *,
    buffer_tokens: int | None = None,
    max_consecutive_failures: int | None = None,
) -> AutoCompactManager:
    """Return (or lazily create) the ``AutoCompactManager`` for *session_key*.

    Config values are read once on first creation; subsequent calls return
    the cached manager so circuit-breaker state persists across turns.
    """
    if session_key in _managers:
        return _managers[session_key]

    # Resolve defaults from config, falling back to module constants
    buf = _DEFAULT_BUFFER_TOKENS
    max_fail = _DEFAULT_MAX_CONSECUTIVE_FAILURES
    try:
        from navig.config import get_config_manager

        cfg = get_config_manager()
        buf = int(cfg.get("memory.auto_compact_buffer_tokens", buf) or buf)
        max_fail = int(cfg.get("memory.auto_compact_max_failures", max_fail) or max_fail)
    except Exception:  # noqa: BLE001
        pass

    if buffer_tokens is not None:
        buf = buffer_tokens
    if max_consecutive_failures is not None:
        max_fail = max_consecutive_failures

    manager = AutoCompactManager(
        session_key, buffer_tokens=buf, max_consecutive_failures=max_fail
    )
    _managers[session_key] = manager
    return manager
