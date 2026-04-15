"""
Away summary — session recap shown when a user returns after a long absence.

Pattern ported from Claude Code's ``services/awaySummary.ts``.

Public API::

    from navig.gateway.channels.away_summary import build_away_summary

    summary = await build_away_summary(history, config=config_manager)
    # returns str (1-3 sentence recap) or None when unavailable/not warranted

Design:
  - Truncates to the most recent N messages (configurable, default 30)
  - Applies a dual cap: line count THEN byte ceiling — keeps the LLM call small
  - Uses effort="low" so the call is fast and cheap
  - Returns None on any error — never blocks the caller (/start flow)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger("navig.gateway.away_summary")

# ── Dual-cap constants (also in config/defaults.yaml for runtime override) ──
# These are the hard-coded floor values; config can raise them, never lower.
_RECAP_MAX_LINES: int = 200      # max lines across all messages
_RECAP_MAX_BYTES: int = 25_000   # max byte length of the combined text

_RECAP_SYSTEM_PROMPT = (
    "You are a context assistant. Summarize in 1–3 concise sentences what the "
    "user was working on in this conversation and what the natural next step is. "
    "Be specific and brief. If the history is empty or purely greetings, return "
    "an empty string."
)


def _truncate_history(
    messages: list[dict[str, Any]],
    max_lines: int = _RECAP_MAX_LINES,
    max_bytes: int = _RECAP_MAX_BYTES,
) -> list[dict[str, Any]]:
    """
    Apply dual cap (line + byte) to a list of message dicts.

    First takes the most recent ``max_lines`` non-empty content lines,
    then truncates at the last newline before ``max_bytes``.  Returns a
    reduced list of dicts with ``role`` and ``content`` keys kept intact.
    """
    if not messages:
        return []

    # Build combined text preserving role markers (for the LLM)
    # and keep the newest conversation context first (tail retention).
    lines_out: list[str] = []
    line_count = 0
    for msg in reversed(messages):
        role = msg.get("role", "")
        content = str(msg.get("content", "")).strip()
        if not content:
            continue
        msg_lines = content.splitlines()
        for line in reversed(msg_lines):
            lines_out.append(f"{role}: {line}")
            line_count += 1
            if line_count >= max_lines:
                break
        if line_count >= max_lines:
            break

    lines_out.reverse()

    combined = "\n".join(lines_out)

    # Byte cap: retain newest bytes (tail), then align to line boundary.
    if len(combined.encode("utf-8", errors="replace")) > max_bytes:
        raw = combined.encode("utf-8", errors="replace")[-max_bytes:]
        truncated = raw.decode("utf-8", errors="ignore")
        first_nl = truncated.find("\n")
        combined = truncated[first_nl + 1 :] if first_nl >= 0 else truncated

    if not combined.strip():
        return []

    # Re-pack into a single assistant-visible user message
    return [{"role": "user", "content": combined}]


async def build_away_summary(
    history: list[dict[str, Any]],
    *,
    config: Any | None = None,
    session_id: str | None = None,
) -> str | None:
    """
    Build a 1-3 sentence session recap for a returning user.

    Args:
        history:    Recent conversation messages (list of ``{role, content}`` dicts).
        config:     ConfigManager instance for reading ``memory.*`` tunables.
                    Falls back to defaults if None.
        session_id: When provided, structured session notes produced by
                    ``SessionMemoryExtractor`` are prepended to the recap
                    prompt to improve quality.

    Returns:
        Recap string, or ``None`` when the history is empty, the LLM
        call fails, or the result is blank.  Never raises.
    """
    try:
        # Read tunables
        window: int = _RECAP_MAX_LINES
        max_bytes: int = _RECAP_MAX_BYTES
        if config is not None:
            try:
                window = int(config.get("memory.away_summary_message_window", _RECAP_MAX_LINES))
                # bytes cap: keep internal default (no config key exposed for bytes)
            except Exception:
                pass

        # Slice to window
        recent = list(history[-window:]) if len(history) > window else list(history)
        if not recent:
            return None

        # Filter noise: skip very short exchanges (1-2 messages = not meaningful)
        content_msgs = [m for m in recent if str(m.get("content", "")).strip()]
        if len(content_msgs) < 2:
            return None

        # Apply dual cap
        truncated = _truncate_history(content_msgs, max_lines=_RECAP_MAX_LINES, max_bytes=max_bytes)
        if not truncated:
            return None

        # Enrich system prompt with structured notes when available
        system_content = _RECAP_SYSTEM_PROMPT
        if session_id:
            try:
                from navig.memory.session_memory import get_session_extractor
                notes = get_session_extractor(session_id).load_notes()
                if notes:
                    system_content = (
                        f"{_RECAP_SYSTEM_PROMPT}\n\n"
                        f"Additionally, here are structured notes from prior extractions "
                        f"that may help your summary:\n\n{notes}"
                    )
            except Exception:  # noqa: BLE001
                pass

        # LLM call — effort=low → fast, cheap, non-blocking
        from navig.llm_generate import run_llm

        messages = [
            {"role": "system", "content": system_content},
            *truncated,
        ]
        result = run_llm(messages, mode="summary", effort="low")
        summary = (result.content or "").strip()

        return summary if summary else None

    except Exception as exc:
        logger.debug("away_summary failed (non-fatal): %s", exc)
        return None
