"""
navig.agent.context_compressor — Context window compression for agentic sessions.

Keeps agentic conversations within the model's context window by:
1. **Cheap pass**: prune orphaned ``tool`` messages and truncate verbose outputs.
2. **Summarisation pass**: replace middle messages with an LLM-generated summary.

The first 2 messages (system + first user) and the last 4 messages are always
preserved ("frozen head + tail") for Anthropic prompt-cache stability and to
maintain recency context.

Usage::

    from navig.agent.context_compressor import ContextCompressor

    compressor = ContextCompressor(max_context_tokens=128_000)
    messages = compressor.maybe_compress(messages, model="claude-sonnet-4-20250514")
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from navig.core.tokens import estimate_tokens as _estimate_tokens_core

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Token estimation
# ─────────────────────────────────────────────────────────────

#: Default context window sizes for common model families.
_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4.1": 1_000_000,
    "gpt-4.1-mini": 1_000_000,
    "claude-sonnet-4-20250514": 200_000,
    "claude-3-5-sonnet": 200_000,
    "claude-3-haiku": 200_000,
    "claude-3-opus": 200_000,
    "claude-opus-4-20250514": 200_000,
    "deepseek-chat": 128_000,
    "deepseek-reasoner": 128_000,
    "gemini-2.5-flash": 1_000_000,
    "gemini-2.5-pro": 1_000_000,
}

#: Bash output truncation limits.
_BASH_HEAD = 500
_BASH_TAIL = 200
_TRUNCATION_MARKER = "\n[...truncated...]\n"


def _estimate_tokens(text: str) -> int:
    """Rough token count from character length (conservative 3.5 chars/token)."""
    return _estimate_tokens_core(text, chars_per_token=3.5)


def _estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """Sum estimated token cost of a message list."""
    total = 0
    for m in messages:
        content = m.get("content", "")
        total += _estimate_tokens(content) + 4  # overhead per message
        # Tool calls in assistant messages
        for tc in m.get("tool_calls", []) or []:
            fn = tc.get("function", {})
            total += _estimate_tokens(fn.get("arguments", "")) + 10
    return total


def _get_context_window(model: str) -> int:
    """Look up or estimate context window for *model*."""
    # Exact match
    if model in _MODEL_CONTEXT_WINDOWS:
        return _MODEL_CONTEXT_WINDOWS[model]
    # Prefix match (e.g. "claude-3-5-sonnet-20241022")
    for prefix, size in _MODEL_CONTEXT_WINDOWS.items():
        if model.startswith(prefix):
            return size
    # Check for provider/model format like "openai/gpt-4o"
    if "/" in model:
        bare = model.split("/", 1)[1]
        return _get_context_window(bare)
    # Safe default
    return 128_000


# ─────────────────────────────────────────────────────────────
# ContextCompressor
# ─────────────────────────────────────────────────────────────


class ContextCompressor:
    """Compress an agentic message history to stay within the context window.

    Args:
        threshold:      Fraction of context window that triggers compression
                        (default ``0.50``).
        summarise_threshold: If still above this fraction after cheap pass,
                             run LLM summarisation (default ``0.40``).
        frozen_head:    Number of leading messages to never compress (default 2).
        frozen_tail:    Number of trailing messages to never compress (default 4).
    """

    def __init__(
        self,
        threshold: float = 0.50,
        summarise_threshold: float = 0.40,
        frozen_head: int = 2,
        frozen_tail: int = 4,
    ) -> None:
        self.threshold = threshold
        self.summarise_threshold = summarise_threshold
        self.frozen_head = frozen_head
        self.frozen_tail = frozen_tail
        # Load config overrides
        try:
            from navig.config import config

            self.threshold = float(
                getattr(config, "agent", {}).get("context_compress_threshold", self.threshold)
                if hasattr(getattr(config, "agent", None), "get")
                else self.threshold
            )
        except Exception:
            pass

    # ── Public API ──

    def maybe_compress(
        self,
        messages: list[dict[str, Any]],
        model: str = "gpt-4o",
    ) -> list[dict[str, Any]]:
        """Return a (possibly compressed) copy of *messages*.

        The original list is never mutated.

        Args:
            messages: Conversation message list (system/user/assistant/tool).
            model:    Model name for context-window lookup.

        Returns:
            Compressed message list, or the original if no compression needed.
        """
        ctx_window = _get_context_window(model)
        token_count = _estimate_messages_tokens(messages)
        ratio = token_count / ctx_window if ctx_window else 0.0

        if ratio < self.threshold:
            return messages  # no compression needed

        logger.debug(
            "Context usage %.1f%% (≈%d tok / %d window) — starting compression",
            ratio * 100,
            token_count,
            ctx_window,
        )

        # Cheap pass (mutates a deep copy)
        compressed = deepcopy(messages)
        compressed = self._cheap_pass(compressed)

        # Re-check
        new_count = _estimate_messages_tokens(compressed)
        new_ratio = new_count / ctx_window if ctx_window else 0.0
        logger.debug("After cheap pass: %.1f%% (≈%d tok)", new_ratio * 100, new_count)

        if new_ratio < self.summarise_threshold:
            return compressed

        # Summarisation pass
        compressed = self._summarise_pass(compressed, model)
        final_count = _estimate_messages_tokens(compressed)
        logger.debug("After summarise pass: ≈%d tok", final_count)

        return compressed

    # ── Cheap pass: prune + truncate ──

    def _cheap_pass(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove orphaned tool messages and truncate long outputs."""
        # Collect all tool_call IDs actually referenced in assistant messages
        referenced_ids: set[str] = set()
        for m in messages:
            for tc in m.get("tool_calls", []) or []:
                tc_id = tc.get("id", "")
                if tc_id:
                    referenced_ids.add(tc_id)

        result: list[dict[str, Any]] = []
        for m in messages:
            role = m.get("role", "")

            # Drop orphaned tool messages
            if role == "tool":
                tc_id = m.get("tool_call_id", "")
                if tc_id and tc_id not in referenced_ids:
                    logger.debug("Pruning orphaned tool message %s", tc_id)
                    continue

            # Truncate long content in tool messages
            if role == "tool":
                content = m.get("content", "")
                if len(content) > (_BASH_HEAD + _BASH_TAIL + 200):
                    m["content"] = content[:_BASH_HEAD] + _TRUNCATION_MARKER + content[-_BASH_TAIL:]

            result.append(m)

        return result

    # ── Summarisation pass ──

    def _summarise_pass(
        self,
        messages: list[dict[str, Any]],
        model: str,
    ) -> list[dict[str, Any]]:
        """Replace the compressible middle with an LLM summary.

        Frozen head and tail are preserved.  The middle block is extracted,
        formatted, and summarised via :func:`~navig.llm_generate.run_llm`.
        """
        if len(messages) <= (self.frozen_head + self.frozen_tail + 1):
            return messages  # too short to compress

        head = messages[: self.frozen_head]
        tail = messages[-self.frozen_tail :]
        middle = messages[self.frozen_head : -self.frozen_tail]

        if not middle:
            return messages

        # Build a text digest of the middle conversation
        digest_lines: list[str] = []
        for m in middle:
            role = m.get("role", "unknown")
            content = m.get("content", "")
            # Truncate individual messages in digest
            if len(content) > 600:
                content = content[:300] + "..." + content[-200:]
            if role == "tool":
                tc_id = m.get("tool_call_id", "?")
                digest_lines.append(f"[tool:{tc_id}] {content[:300]}")
            elif role == "assistant":
                tool_calls = m.get("tool_calls", []) or []
                if tool_calls:
                    names = [tc.get("function", {}).get("name", "?") for tc in tool_calls]
                    digest_lines.append(f"[assistant called: {', '.join(names)}]")
                if content:
                    digest_lines.append(f"[assistant]: {content}")
            else:
                digest_lines.append(f"[{role}]: {content}")

        digest = "\n".join(digest_lines)

        # Summarise via LLM
        summary_text = self._call_summarise_llm(digest)

        # Build replacement message
        summary_msg: dict[str, Any] = {
            "role": "system",
            "content": (f"[Context Summary — {len(middle)} messages compressed]\n\n{summary_text}"),
        }

        return head + [summary_msg] + tail

    def _call_summarise_llm(self, digest: str) -> str:
        """Call LLM with a compression template.

        Falls back to a naive truncation if the LLM call fails.
        """
        template = (
            "Summarize the following conversation fragment concisely. "
            "Preserve all key decisions, tool results, facts, and pending actions. "
            "Use bullet points. Be specific about file paths, numbers, and code "
            "identifiers.\n\n"
            "--- CONVERSATION ---\n"
            f"{digest}\n"
            "--- END ---\n\n"
            "Summary:"
        )

        try:
            from navig.llm_generate import run_llm

            result = run_llm(
                prompt=template,
                temperature=0.2,
                max_tokens=800,
            )
            if result and result.content:
                return result.content.strip()
        except Exception as exc:
            logger.warning("Context summarisation LLM call failed: %s", exc)

        # Fallback: naive truncation to first 2000 chars of digest
        return digest[:2000] + "\n[...abbreviated...]"


# ─────────────────────────────────────────────────────────────
# ReactiveCompactor — automatic threshold-based compaction
# ─────────────────────────────────────────────────────────────

#: Default summarisation prompt for compaction.
_REACTIVE_SUMMARY_TEMPLATE = (
    "Summarize the following conversation chunk concisely. "
    "Preserve: key decisions, files modified, tool results, errors encountered, "
    "and current task state. Use bullet points. Be brief but complete.\n\n"
    "--- CONVERSATION ---\n{digest}\n--- END ---\n\n"
    "Summary:"
)


class ReactiveCompactor:
    """Auto-trigger compaction when context usage exceeds a threshold.

    Unlike :class:`ContextCompressor` (manual/sync), this class is designed for
    automatic invocation after every agent turn.  It:

    * Fires at **90 %** context fill (``TRIGGER_THRESHOLD``).
    * Compacts down to **60 %** (``TARGET_FILL``).
    * Preserves the last *MIN_KEEP_TURNS* messages verbatim.
    * Respects prompt-cache breakpoints so cached prefixes remain valid.
    * Tracks cumulative stats (compact count, tokens saved).

    Args:
        max_context_tokens: Maximum context window size in tokens.
        summarizer:         Optional async callback ``(str) -> str`` used to
                            generate the summary text.  When *None*, falls back
                            to :func:`~navig.agent.context_compressor._default_summarizer`.
                            Injecting a callback keeps the class testable
                            without real LLM calls.
    """

    TRIGGER_THRESHOLD: float = 0.90
    TARGET_FILL: float = 0.60
    MIN_KEEP_TURNS: int = 4

    def __init__(
        self,
        max_context_tokens: int,
        summarizer: Callable[[str], str] | None = None,
    ) -> None:
        if max_context_tokens <= 0:
            raise ValueError("max_context_tokens must be positive")
        self.max_tokens = max_context_tokens
        self._summarizer = summarizer
        self._compact_count: int = 0
        self._tokens_saved: int = 0

    # ── Public helpers ──────────────────────────────────────

    def should_compact(self, current_tokens: int) -> bool:
        """Return ``True`` when *current_tokens* exceeds the trigger threshold."""
        if self.max_tokens <= 0:
            return False
        return (current_tokens / self.max_tokens) >= self.TRIGGER_THRESHOLD

    def compute_target(self) -> int:
        """Token budget to aim for after compaction."""
        return int(self.max_tokens * self.TARGET_FILL)

    # ── Main entry point ────────────────────────────────────

    async def compact(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        """Compact *messages* while preserving cache boundaries.

        The method detects cache breakpoints automatically using
        :func:`~navig.agent.prompt_caching.has_cache_breakpoint`.

        Returns:
            A ``(compacted_messages, tokens_saved)`` tuple.  If nothing can
            be compacted the original list is returned unchanged with
            ``tokens_saved == 0``.
        """
        if len(messages) <= self.MIN_KEEP_TURNS + 1:
            return messages, 0  # too short — nothing to summarise

        # 1. Determine the safe range
        safe_start = self._find_safe_start(messages)
        safe_end = len(messages) - self.MIN_KEEP_TURNS

        if safe_end <= safe_start:
            return messages, 0  # nothing compactable

        # 2. Split
        preserved_before = messages[:safe_start]
        to_summarize = messages[safe_start:safe_end]
        preserved_after = messages[safe_end:]

        if not to_summarize:
            return messages, 0

        # 3. Build digest + summarise
        digest = self._build_digest(to_summarize)
        try:
            summary_text = await self._run_summarizer(digest)
        except Exception as exc:
            logger.warning("Reactive compaction summary failed: %s", exc)
            return messages, 0  # keep originals on failure

        # 4. Build compacted list
        summary_msg: dict[str, Any] = {
            "role": "system",
            "content": (
                f"[Conversation Summary — {len(to_summarize)} messages compacted]\n\n"
                f"{summary_text}\n\n"
                "[End Summary — recent messages follow]"
            ),
        }

        compacted = preserved_before + [summary_msg] + preserved_after

        # 5. Track stats
        original_tokens = _estimate_messages_tokens(to_summarize)
        summary_tokens = _estimate_messages_tokens([summary_msg])
        saved = max(0, original_tokens - summary_tokens)

        self._compact_count += 1
        self._tokens_saved += saved

        logger.info(
            "Reactive compaction #%d: %d messages → summary, saved ≈%d tokens",
            self._compact_count,
            len(to_summarize),
            saved,
        )

        return compacted, saved

    # ── Stats ───────────────────────────────────────────────

    @property
    def stats(self) -> dict[str, Any]:
        """Return cumulative compaction statistics."""
        return {
            "compact_count": self._compact_count,
            "tokens_saved": self._tokens_saved,
            "estimated_cost_saved": f"${self._tokens_saved * 0.000003:.4f}",
        }

    # ── Internals ───────────────────────────────────────────

    def _find_safe_start(self, messages: list[dict[str, Any]]) -> int:
        """Earliest index we can compact from, respecting cache breakpoints.

        Rules:
        * Index 0 (system prompt) is always preserved.
        * Any message with a cache breakpoint is preserved (we start *after* it).
        * Only considers breakpoints that leave room before the frozen tail.
        """
        from navig.agent.prompt_caching import has_cache_breakpoint

        safe = 1  # always preserve system prompt

        tail_boundary = len(messages) - self.MIN_KEEP_TURNS
        for idx, msg in enumerate(messages):
            if idx >= tail_boundary:
                break
            if has_cache_breakpoint(msg):
                safe = max(safe, idx + 1)

        return safe

    @staticmethod
    def _build_digest(messages: list[dict[str, Any]]) -> str:
        """Format *messages* into a concise text digest for summarisation."""
        lines: list[str] = []
        for m in messages:
            role = m.get("role", "unknown")
            content = m.get("content", "")
            if isinstance(content, list):
                # Flatten structured content blocks
                parts = []
                for block in content:
                    if isinstance(block, dict):
                        parts.append(block.get("text", ""))
                    elif isinstance(block, str):
                        parts.append(block)
                content = "\n".join(parts)
            # Truncate per-message to keep digest bounded
            if len(content) > 600:
                content = content[:300] + "..." + content[-200:]

            if role == "tool":
                tc_id = m.get("tool_call_id", "?")
                lines.append(f"[tool:{tc_id}] {content[:300]}")
            elif role == "assistant":
                tool_calls = m.get("tool_calls") or []
                if tool_calls:
                    names = [tc.get("function", {}).get("name", "?") for tc in tool_calls]
                    lines.append(f"[assistant called: {', '.join(names)}]")
                if content:
                    lines.append(f"[assistant]: {content}")
            else:
                lines.append(f"[{role}]: {content}")
        return "\n".join(lines)

    async def _run_summarizer(self, digest: str) -> str:
        """Invoke the summarizer callback, or the default LLM fallback."""
        if self._summarizer is not None:
            result = self._summarizer(digest)
            # Support both sync and async callables
            if hasattr(result, "__await__"):
                return await result  # type: ignore[misc]
            return result  # type: ignore[return-value]
        return _default_summarizer(digest)


def _default_summarizer(digest: str) -> str:
    """Fallback summarizer using :func:`~navig.llm_generate.run_llm`."""
    prompt = _REACTIVE_SUMMARY_TEMPLATE.format(digest=digest)
    try:
        from navig.llm_generate import run_llm

        result = run_llm(prompt=prompt, temperature=0.2, max_tokens=800)
        if result and result.content:
            return result.content.strip()
    except Exception as exc:
        logger.warning("Default reactive summarizer LLM call failed: %s", exc)
    # Fallback: naive truncation
    return digest[:2000] + "\n[...abbreviated...]"


# ─────────────────────────────────────────────────────────────
# Module helpers
# ─────────────────────────────────────────────────────────────


def get_context_compressor(**kwargs: Any) -> ContextCompressor:
    """Return a :class:`ContextCompressor` with config-driven defaults."""
    return ContextCompressor(**kwargs)


def get_reactive_compactor(
    max_context_tokens: int = 200_000,
    summarizer: Callable[[str], str] | None = None,
) -> ReactiveCompactor:
    """Factory for :class:`ReactiveCompactor` with sensible defaults."""
    return ReactiveCompactor(
        max_context_tokens=max_context_tokens,
        summarizer=summarizer,
    )
