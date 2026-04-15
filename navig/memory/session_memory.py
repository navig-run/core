"""
Session Memory Extractor — background structured note-taking during AI sessions.

Ported and adapted from Claude Code's TypeScript
``services/SessionMemory/sessionMemory.ts``.

Every ``extraction_interval_tool_calls`` tool invocations, a background
asyncio task fires a forked LLM call that reads the recent conversation and
extracts structured notes into ``~/.navig/memory/<session_id>.md``.

Notes are structured as three sections:
  ## What was discussed
  ## Decisions
  ## Next steps

These notes are automatically included by the away-summary builder when the
user returns after a long absence, providing richer recaps than raw history
alone.

Usage::

    from navig.memory.session_memory import get_session_extractor

    extractor = get_session_extractor("telegram:user:123")
    extractor.record_tool_call()   # call after each tool invocation
    # Background extraction fires automatically when interval is reached
    # Await is not required — extraction is fire-and-forget
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger("navig.memory.session_memory")

# ── Module-level constants ────────────────────────────────────────────────────
_DEFAULT_EXTRACTION_INTERVAL: int = 10     # tool calls between extractions
_DEFAULT_EXTRACTION_EFFORT: str = "low"
_NOTES_DIR_NAME = "memory"                 # sub-dir inside ~/.navig/
_NOTES_FILE_SUFFIX = "_notes.md"

_EXTRACTION_SYSTEM_PROMPT = """\
You are a structured note-taker operating inside an AI assistant session.
Read the conversation below and write concise, factual notes in exactly this
Markdown format (three sections, no preamble):

## What was discussed
<2-4 bullet points — specific topics, files, technologies, or problems mentioned>

## Decisions
<bullet points for any decisions, conclusions, or resolved questions; "none" if empty>

## Next steps
<bullet points for pending actions or open questions; "none" if empty>

Keep each bullet to one line. Be specific — name actual file paths, commands,
or technologies. Do NOT add a title or any text outside the three sections."""


# ── Per-session extractor ────────────────────────────────────────────────────

class SessionMemoryExtractor:
    """Tracks tool calls for a session and triggers background note extraction."""

    def __init__(
        self,
        session_id: str,
        *,
        interval: int = _DEFAULT_EXTRACTION_INTERVAL,
        effort: str = _DEFAULT_EXTRACTION_EFFORT,
        notes_dir: Path | None = None,
    ) -> None:
        self.session_id = session_id
        self._interval = interval
        self._effort = effort
        self._notes_dir = notes_dir
        self._tool_call_count: int = 0
        self._active_task: asyncio.Task | None = None  # type: ignore[type-arg]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_tool_call(
        self, messages: list[object] | None = None
    ) -> None:
        """Call this after every tool invocation.

        When the interval is reached and no extraction is running, a
        background task is spawned to extract and persist notes.  Passing
        *messages* is optional — if omitted the extractor will load them
        from the conversation store.
        """
        self._tool_call_count += 1
        if self._tool_call_count % self._interval != 0:
            return
        if self._active_task and not self._active_task.done():
            logger.debug(
                "session_memory: extraction still running for %s — skipping",
                self.session_id,
            )
            return
        self._active_task = asyncio.create_task(
            self._run_extraction(messages),
            name=f"session-memory:{self.session_id}",
        )

    def load_notes(self) -> str | None:
        """Return the latest notes markdown for this session, or None."""
        path = self._notes_path()
        if not path or not path.exists():
            return None
        try:
            return path.read_text(encoding="utf-8").strip() or None
        except OSError as exc:
            logger.debug("session_memory.load_notes: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _notes_path(self) -> Path | None:
        try:
            base = self._resolve_notes_dir()
            safe = self.session_id.replace(":", "_").replace("/", "_")
            return base / f"{safe}{_NOTES_FILE_SUFFIX}"
        except Exception as exc:  # noqa: BLE001
            logger.debug("session_memory._notes_path: %s", exc)
            return None

    def _resolve_notes_dir(self) -> Path:
        if self._notes_dir is not None:
            return self._notes_dir
        try:
            from navig.config import get_config_manager
            cfg = get_config_manager()
            base = Path(cfg.global_config_dir) / _NOTES_DIR_NAME
        except Exception:  # noqa: BLE001
            base = Path.home() / ".navig" / _NOTES_DIR_NAME
        base.mkdir(parents=True, exist_ok=True)
        return base

    async def _run_extraction(self, messages: list[object] | None) -> None:
        """Forked task: extract notes and write to disk."""
        try:
            if messages is None:
                messages = await self._load_messages_async()
            if not messages:
                return
            notes = await self._extract_notes_async(messages)
            if notes:
                await self._write_notes_async(notes)
                logger.info(
                    "session_memory: notes updated for %s (%d chars)",
                    self.session_id,
                    len(notes),
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("session_memory._run_extraction failed: %s", exc)

    async def _load_messages_async(self) -> list[object]:
        import functools
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, functools.partial(self._sync_load_messages))

    def _sync_load_messages(self) -> list[object]:
        try:
            from pathlib import Path as _Path

            from navig.config import get_config_manager
            from navig.memory import ConversationStore
            cfg = get_config_manager()
            db = _Path(cfg.global_config_dir) / "memory" / "memory.db"
            if not db.exists():
                return []
            store = ConversationStore(db)
            try:
                return store.get_history(self.session_id, limit=80)  # type: ignore[return-value]
            finally:
                store.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("session_memory._sync_load_messages: %s", exc)
            return []

    async def _extract_notes_async(self, messages: list[object]) -> str | None:
        import functools
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, functools.partial(self._sync_extract_notes, messages)
        )

    def _sync_extract_notes(self, messages: list[object]) -> str | None:
        try:
            from navig.llm_generate import run_llm

            # Build a condensed transcript
            excerpt = list(messages)[-60:]
            lines: list[str] = []
            for m in excerpt:
                role = getattr(m, "role", "unknown")
                content = str(getattr(m, "content", ""))
                lines.append(f"{role.upper()}: {content[:400]}")

            result = run_llm(
                [
                    {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": "\n".join(lines)},
                ],
                mode="summary",
                effort=self._effort,
            )
            notes = (result.content or "").strip()
            return notes or None
        except Exception as exc:  # noqa: BLE001
            logger.debug("session_memory._sync_extract_notes: %s", exc)
            return None

    async def _write_notes_async(self, notes: str) -> None:
        import functools
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, functools.partial(self._sync_write_notes, notes)
        )

    def _sync_write_notes(self, notes: str) -> None:
        path = self._notes_path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(notes, encoding="utf-8")
        except OSError as exc:
            logger.debug("session_memory._sync_write_notes: %s", exc)


# ── Process-wide registry ────────────────────────────────────────────────────

_extractors: dict[str, SessionMemoryExtractor] = {}


def get_session_extractor(session_id: str) -> SessionMemoryExtractor:
    """Return (or lazily create) the extractor for *session_id*.

    Config is read once on first creation so the interval/effort tunables
    are consistent for the lifetime of the process.
    """
    if session_id in _extractors:
        return _extractors[session_id]

    interval = _DEFAULT_EXTRACTION_INTERVAL
    effort = _DEFAULT_EXTRACTION_EFFORT
    try:
        from navig.config import get_config_manager
        cfg = get_config_manager()
        interval = int(cfg.get("memory.extraction_interval_tool_calls", interval) or interval)
        effort = str(cfg.get("memory.extraction_effort", effort) or effort)
    except Exception:  # noqa: BLE001
        pass

    extractor = SessionMemoryExtractor(session_id, interval=interval, effort=effort)
    _extractors[session_id] = extractor
    return extractor
