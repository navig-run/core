"""ConversationHistory: rolling message buffer with token-budget enforcement."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from navig.platform.paths import config_dir


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
        max_tokens: int = 4096,
        summarizer: Optional[Callable[[List[Dict[str, str]]], str]] = None,
    ) -> None:
        if max_tokens <= 0:
            raise ValueError(f"max_tokens must be a positive integer, got {max_tokens!r}")
        # Sanitize user_id so it is safe to use as a filename (no path traversal).
        safe_id = re.sub(r"[^\w\-]", "_", user_id) or "_"
        self._user_id = safe_id
        self._max_tokens = max_tokens
        self._summarizer = summarizer
        self._messages: list[dict[str, str]] = []
        self._jsonl_path = config_dir() / "history" / f"{safe_id}.jsonl"
        if self._jsonl_path.exists():
            self.load_recent()

    # ── Public API ──────────────────────────────────────────────────────────

    def add(self, role: str, content: str) -> None:
        """Append a message, persist it to JSONL, then truncate if over budget."""
        self._messages.append({"role": role, "content": content})
        self._jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with self._jsonl_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"role": role, "content": content, "ts": time.time()}) + "\n")
        if self.token_count() > self._max_tokens:
            self._truncate()

    def get_messages(self) -> List[Dict[str, str]]:
        """Return a shallow copy of the current in-memory message list."""
        return list(self._messages)

    def clear(self) -> None:
        """Clear the in-memory message list (JSONL file is unchanged)."""
        self._messages = []

    def token_count(self) -> int:
        """Return total estimated tokens across all messages currently in memory."""
        return sum(_estimate_tokens(m["content"]) for m in self._messages)

    def load_recent(self, n: int = 10) -> None:
        """
        Read the last *n* complete lines from the JSONL file into memory.
        Does not trigger truncation and does not re-append to the file.
        """
        try:
            lines = self._jsonl_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        loaded: list[dict[str, str]] = []
        for line in lines[-n:]:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                loaded.append({"role": obj["role"], "content": obj["content"]})
            except (json.JSONDecodeError, KeyError):
                continue
        self._messages = loaded

    # ── Private helpers ─────────────────────────────────────────────────────

    def _truncate(self) -> None:
        """
        Reduce history to stay within *max_tokens*.

        Preservation rules:
          - messages[0:2]  — context anchor (always kept)
          - messages[-4:]  — recency window (always kept)
          - messages[2:-4] — middle slice: summarised or dropped
        """
        msgs = self._messages
        # Need at least 7 messages before there is a non-empty middle slice
        # (2 anchor + ≥1 middle + 4 recency).  Nothing to collapse below that.
        if len(msgs) < 7:
            return
        anchor = msgs[0:2]
        recency = msgs[-4:]
        middle = msgs[2:-4]  # guaranteed non-empty given len(msgs) >= 7
        if self._summarizer is not None:
            summary_text = self._summarizer(middle)
            replacement = [{"role": "system", "content": f"[Summary: {summary_text}]"}]
        else:
            replacement = []
        self._messages = anchor + replacement + recency
