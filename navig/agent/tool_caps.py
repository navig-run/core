"""
navig.agent.tool_caps — Tool result capping with disk spillover.

Truncates oversized tool results to prevent context-window overflow, writing
the full output to a temporary file so the agent can reference it if needed.

Usage::

    from navig.agent.tool_caps import cap_result, get_cap_for_tool, cleanup_spillover

    capped = cap_result(huge_text, tool_name="read_file")
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
import time
from pathlib import Path

from navig.platform.paths import config_dir

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

#: Default maximum characters for any tool result.
DEFAULT_MAX_RESULT_CHARS: int = 30_000

#: Directory for spillover files (full results that exceeded the cap).
SPILLOVER_DIR: Path = config_dir() / "tmp" / "tool_spillover"

#: Time-to-live for spillover files, in seconds (default 1 hour).
SPILLOVER_TTL: int = 3600

#: Minimum fraction of ``max_chars`` to keep when snapping to line boundary.
#: Prevents over-cutting when the last newline is very early in the string.
_LINE_SNAP_MIN_RATIO: float = 0.8

#: Per-tool character limits.  Tools not listed here fall back to
#: :data:`DEFAULT_MAX_RESULT_CHARS`.
TOOL_SPECIFIC_CAPS: dict[str, int] = {
    "read_file": 30_000,
    "grep_search": 20_000,
    "list_files": 15_000,
    "bash_exec": 50_000,
    "search": 15_000,
    "web_fetch": 10_000,
    "wiki_read": 20_000,
    "wiki_search": 15_000,
    "navig_run": 50_000,
    "navig_db_query": 30_000,
    "navig_docker_logs": 30_000,
    "navig_file_show": 30_000,
}


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────


def get_cap_for_tool(tool_name: str) -> int:
    """Return the character cap for *tool_name*, or the global default."""
    return TOOL_SPECIFIC_CAPS.get(tool_name, DEFAULT_MAX_RESULT_CHARS)


def cap_result(
    result: str,
    *,
    max_chars: int | None = None,
    tool_name: str = "",
) -> str:
    """Cap a tool result string, spilling overflow to disk.

    If ``len(result) <= max_chars`` the string is returned unchanged.
    Otherwise:

    1. The full result is written to :data:`SPILLOVER_DIR`.
    2. The result is truncated at the last newline before *max_chars*
       (guarded by :data:`_LINE_SNAP_MIN_RATIO` so we don't cut too much).
    3. A footer is appended with the spillover file path and size info.

    Args:
        result:     Raw tool output string.
        max_chars:  Override cap.  ``None`` → ``get_cap_for_tool(tool_name)``.
        tool_name:  Tool identifier (used for per-tool limits & filename prefix).

    Returns:
        The (possibly truncated) result string.
    """
    if max_chars is None:
        max_chars = get_cap_for_tool(tool_name)
    elif max_chars < 0:
        max_chars = 0

    if len(result) <= max_chars:
        return result

    # ── Spill full result to disk ───────────────────────────
    spill_path = _write_spillover(result, tool_name)

    # ── Truncate at line boundary ───────────────────────────
    truncated = result[:max_chars]
    last_nl = truncated.rfind("\n")
    if last_nl > int(max_chars * _LINE_SNAP_MIN_RATIO):
        truncated = truncated[:last_nl]

    # ── Append footer ───────────────────────────────────────
    footer_parts = [
        f"\n\n[Truncated at {max_chars:,} chars out of {len(result):,} total.",
    ]
    if spill_path is not None:
        footer_parts.append(f" Full result saved to: {spill_path}]")
    else:
        footer_parts.append(" Full result could not be saved to disk.]")

    return truncated + "".join(footer_parts)


def cleanup_spillover(max_age: int = SPILLOVER_TTL) -> int:
    """Remove spillover files older than *max_age* seconds.

    Returns:
        Number of files removed.
    """
    if not SPILLOVER_DIR.exists():
        return 0

    now = time.time()
    removed = 0
    for fpath in SPILLOVER_DIR.iterdir():
        if not fpath.is_file():
            continue
        try:
            if now - fpath.stat().st_mtime > max_age:
                fpath.unlink()
                removed += 1
        except OSError as exc:
            logger.debug("cleanup_spillover: cannot remove %s: %s", fpath, exc)
    return removed


# ─────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────


def _write_spillover(content: str, tool_name: str) -> Path | None:
    """Write *content* to a hash-named file in :data:`SPILLOVER_DIR`.

    Returns the written path, or ``None`` on failure.
    """
    try:
        SPILLOVER_DIR.mkdir(parents=True, exist_ok=True)
        hash_id = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:12]
        if tool_name:
            prefix = re.sub(r"[^A-Za-z0-9._-]+", "_", tool_name).strip("._")
            if not prefix:
                prefix = "tool"
        else:
            prefix = "tool"
        spill_file = SPILLOVER_DIR / f"{prefix}_{hash_id}.txt"

        # Avoid re-writing identical content
        if spill_file.exists():
            return spill_file

        _tmp_spill: Path | None = None
        try:
            _fd_sp, _tmp_sp = tempfile.mkstemp(dir=SPILLOVER_DIR, suffix=".tmp")
            _tmp_spill = Path(_tmp_sp)
            with os.fdopen(_fd_sp, "w", encoding="utf-8") as _fh_sp:
                _fh_sp.write(content)
            os.replace(_tmp_spill, spill_file)
            _tmp_spill = None
        finally:
            if _tmp_spill is not None:
                _tmp_spill.unlink(missing_ok=True)
        return spill_file
    except OSError as exc:
        logger.debug("_write_spillover failed for tool %r: %s", tool_name, exc)
        return None
