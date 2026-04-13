"""ConversationHistory: rolling message buffer with token-budget enforcement."""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable

from navig.platform.paths import config_dir

logger = logging.getLogger(__name__)

# ── Module-level constants (single source of truth) ────────────────────────────
_DEFAULT_MAX_TOKENS: int = 4_096       # token budget before truncation fires
_SESSION_LOAD_DEPTH: int = 10          # max JSONL lines replayed on session start
_TRUNCATE_ANCHOR: int = 2              # messages[0:N] always kept (context anchor)
_TRUNCATE_RECENCY: int = 4             # messages[-N:] always kept (recency window)
_TRUNCATE_MIN_MSGS: int = _TRUNCATE_ANCHOR + _TRUNCATE_RECENCY + 1  # = 7
_VALID_ROLES: frozenset[str] = frozenset({"user", "assistant", "system", "tool"})


def _estimate_tokens(text: str) -> int:
    """Approximate token count using word-split heuristic."""
    return int(len(text.split()) * 1.3)


def _strip_cjk(text: str) -> str:
    """Remove CJK blocks from *text*, preserving everything else."""
    return re.sub(r"[\u4E00-\u9FFF\u3400-\u4DBF\u3000-\u303F]+", "", text).strip()


class ConversationHistory:
    """
    Rolling conversation buffer with JSONL persistence and summarizer-based truncation.

    Parameters
    ----------
    user_id:    Identifies the user; also determines the JSONL file path.
    max_tokens: Token budget — truncation fires when exceeded after an add().
    summarizer: Optional callable that receives a List[Dict[str, str]] and returns a summary
                string.  When None, the middle slice is dropped instead of summarised.
    """

    def __init__(
        self,
        user_id: str,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        summarizer: Callable[[list[dict[str, str]]], str] | None = None,
    ) -> None:
        if max_tokens <= 0:
            raise ValueError(f"max_tokens must be a positive integer, got {max_tokens!r}")
        # Sanitize user_id so it is safe to use as a filename (no path traversal).
        safe_id = re.sub(r"[^\w\-]", "_", user_id) or "_"
        self._user_id = safe_id
        self._max_tokens = max_tokens
        self._summarizer = summarizer
        self._messages: list[dict[str, str]] = []
        # True when no history was loaded within the session boundary — signals
        # the LLM to start fresh and not continue any prior conversation topic.
        self._session_is_fresh: bool = True
        self._jsonl_path = config_dir() / "history" / f"{safe_id}.jsonl"
        if self._jsonl_path.exists():
            self.load_recent()

    # ── Public API ──────────────────────────────────────────────────────────

    def add(self, role: str, content: str) -> None:
        """Append a message, persist it to JSONL, then truncate if over budget."""
        self._prune_stale()
        now = time.time()
        self._messages.append({"role": role, "content": content, "ts": now})
        self._jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with self._jsonl_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"role": role, "content": content, "ts": now}) + "\n")
        if self.token_count() > self._max_tokens:
            self._truncate()

    def get_messages(self) -> list[dict[str, str]]:
        """Return a shallow copy of the current in-memory message list."""
        self._prune_stale()
        return [{"role": m["role"], "content": str(m["content"])} for m in self._messages]

    def clear(self) -> None:
        """Clear the in-memory message list (JSONL file is unchanged)."""
        self._messages = []

    def mark_freshness_consumed(self) -> None:
        """Signal that the fresh-session notice has been shown to the LLM.

        Called by :class:`ConversationalAgent` after injecting the
        ``'Fresh session \u2014 no prior conversation loaded.'`` notice into the system
        prompt.  Centralises ownership of ``_session_is_fresh`` so external
        code never needs to poke a private attribute directly.
        """
        self._session_is_fresh = False

    def token_count(self) -> int:
        """Return total estimated tokens across all messages currently in memory."""
        return sum(_estimate_tokens(m["content"]) for m in self._messages)

    # ── Session-boundary constant ───────────────────────────────────────────
    #: Messages older than this are not loaded into a fresh in-memory session.
    #: 12 h covers overnight gaps without breaking long same-day sessions.
    SESSION_MAX_AGE_SECONDS: float = 12 * 3600  # 12 hours

    def load_recent(self, n: int = _SESSION_LOAD_DEPTH) -> None:
        """
        Read the last *n* complete lines from the JSONL file into memory,
        skipping any message whose ``ts`` timestamp is older than
        ``SESSION_MAX_AGE_SECONDS``.  This prevents yesterday's language /
        topic context from bleeding into a fresh session after an overnight
        gap.
        """
        try:
            lines = self._jsonl_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        cutoff = time.time() - self.SESSION_MAX_AGE_SECONDS
        loaded: list[dict[str, str]] = []
        for i, line in enumerate(lines[-n:]):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                # Drop messages that pre-date the session boundary.
                if obj.get("ts", 0) < cutoff:
                    continue
                role = obj.get("role", "")
                content = obj.get("content")
                if role not in _VALID_ROLES or not isinstance(content, str):
                    logger.debug("history: skipped invalid JSONL entry at line %d", i)
                    continue
                loaded.append({"role": role, "content": content, "ts": obj.get("ts", time.time())})
            except (json.JSONDecodeError, KeyError) as exc:
                logger.debug("history: skipped corrupt JSONL line %d: %s", i, exc)
                continue
        self._messages = loaded
        # Mark the session as fresh when no messages survived the cutoff filter.
        # This flag is read by ConversationalAgent to inject a system-prompt notice.
        self._session_is_fresh = len(self._messages) == 0

    # ── Private helpers ─────────────────────────────────────────────────────


    def _prune_stale(self) -> None:
        """
        Drop any in-memory messages whose timestamp is older than
        SESSION_MAX_AGE_SECONDS. This ensures that long-running processes
        won't bleed yesterday's conversation state into a fresh turn today.
        """
        if not self._messages:
            return
            
        cutoff = time.time() - self.SESSION_MAX_AGE_SECONDS
        active_msgs = []
        for m in self._messages:
            if m.get("ts", time.time()) >= cutoff:
                active_msgs.append(m)
                
        if not active_msgs and self._messages:
            # We just cleared the entire in-memory state because it was stale.
            self._session_is_fresh = True
            
        self._messages = active_msgs

    def _truncate(self) -> None:
        """
        Reduce history to stay within *max_tokens*.

        Preservation rules:
          - messages[0:2]  — context anchor (always kept)
          - messages[-4:]  — recency window (always kept)
          - messages[2:-4] — middle slice: summarised or dropped
        """
        msgs = self._messages
        # Need at least _TRUNCATE_MIN_MSGS (= 7) messages before there is a
        # non-empty middle slice (anchor + ≥1 middle + recency).
        if len(msgs) < _TRUNCATE_MIN_MSGS:
            return
        anchor = msgs[:_TRUNCATE_ANCHOR]
        recency = msgs[-_TRUNCATE_RECENCY:]
        middle = msgs[_TRUNCATE_ANCHOR:-_TRUNCATE_RECENCY]  # guaranteed non-empty
        if self._summarizer is not None:
            middle_clean = [{"role": m["role"], "content": str(m["content"])} for m in middle]
            summary_text = self._summarizer(middle_clean)
            replacement = [{"role": "system", "content": f"[Summary: {summary_text}]", "ts": time.time()}]
        else:
            # No summarizer — insert an explicit omission marker so the LLM knows
            # context was elided rather than silently losing it.
            replacement = [{"role": "system", "content": f"[{len(middle)} messages omitted]", "ts": time.time()}]
        self._messages = anchor + replacement + recency
