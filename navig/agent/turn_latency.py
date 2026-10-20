"""Per-turn latency ledger — the data behind the X-ray's stage bars.

One JSONL line per ReAct turn: how much of the turn was navig preparing, how long
the model took to say its first word, how long it kept generating, and how long
the tools (and any approval wait) took. Reading the last N entries answers
"why was that slow?" without log archaeology — the question this session spent
hours answering by hand.

Design notes:

* **A file, not memory** — agent runs happen in more than one process (the
  Telegram worker, the gateway's `/llm/chat`, missions), so a per-process ring
  buffer would show each reader only its own turns, and a restart would erase
  the evidence of what was slow before it.
* **Bounded** — trimmed to :data:`KEEP` lines so the ledger can never grow
  without limit on a long-lived daemon.
* **Best-effort** — telemetry must never take a turn down with it: every public
  call swallows and logs at debug.
* **`config_dir()`** — honours ``NAVIG_CONFIG_DIR``, so tests write to their own
  sandbox instead of the operator's real history.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: File holding the ledger, under the install's ``perf/`` directory.
FILE_NAME = "turn_latency.jsonl"
#: Lines kept. 20 turns is what the X-ray shows; 200 covers a few busy sessions.
KEEP = 200
#: Append this many lines before trimming back to KEEP.
_TRIM_EVERY = 50

_lock = threading.Lock()
_appends_since_trim = 0


def _path() -> Path:
    from navig.platform.paths import config_dir

    return config_dir() / "perf" / FILE_NAME


def record(entry: dict[str, Any]) -> None:
    """Append one turn's stage timings. Never raises."""
    global _appends_since_trim
    if not entry:
        return
    try:
        row = dict(entry)
        row.setdefault("ts", time.time())
        line = json.dumps(row, ensure_ascii=False, default=str)
        with _lock:
            path = _path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            _appends_since_trim += 1
            if _appends_since_trim >= _TRIM_EVERY:
                _appends_since_trim = 0
                _trim(path)
    except Exception as exc:  # noqa: BLE001 — telemetry never breaks a turn
        logger.debug("turn_latency.record failed: %s", exc)


def _trim(path: Path) -> None:
    """Keep the newest KEEP lines. Caller holds the lock."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) <= KEEP:
            return
        path.write_text("\n".join(lines[-KEEP:]) + "\n", encoding="utf-8")
    except OSError as exc:
        logger.debug("turn_latency trim failed: %s", exc)


def read_last(n: int = 20) -> list[dict[str, Any]]:
    """The newest *n* turns, **newest first**. Never raises."""
    try:
        path = _path()
        if not path.is_file():
            return []
        with _lock:
            raw = path.read_text(encoding="utf-8").splitlines()[-max(n, 1) :]
    except Exception as exc:  # noqa: BLE001
        logger.debug("turn_latency.read_last failed: %s", exc)
        return []

    out: list[dict[str, Any]] = []
    for line in reversed(raw):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def record_turn(
    *,
    turn: int,
    session_key: str,
    mode: str,
    provider: str,
    model: str,
    prep_ms: int,
    ttft_ms: int,
    llm_ms: int,
    tools_ms: int,
    approval_ms: int,
    turn_ms: int,
    prompt_chars: int,
    tools: list[str] | None = None,
) -> None:
    """Build and store one turn entry (kept separate so callers stay tidy)."""
    record(
        {
            "turn": turn,
            "session": session_key or "",
            "mode": mode,
            "model": f"{provider}:{model}" if provider else model,
            # Stage splits. gen_ms is generation AFTER the first token, so
            # prep + ttft + gen + tools reads as one wall-clock bar.
            "prep_ms": int(prep_ms),
            "ttft_ms": int(ttft_ms),
            "gen_ms": max(int(llm_ms) - int(ttft_ms), 0),
            "llm_ms": int(llm_ms),
            "tools_ms": int(tools_ms),
            "approval_ms": int(approval_ms),
            "turn_ms": int(turn_ms),
            "prompt_chars": int(prompt_chars),
            "tools": list(tools or []),
        }
    )


def render_bars(turns: list[dict[str, Any]], *, width: int = 28) -> list[str]:
    """Plain-text stage bars, newest first — the `/trace` view.

    Each bar is one turn split into prep │ ttft │ gen │ tools, so a turn that
    waited 14 s on the model is visibly different from one that spent 14 s in
    prep. Approval waits beyond the tool time are called out on the row.
    """
    if not turns:
        return []
    lines: list[str] = []
    for t in turns:
        prep = max(int(t.get("prep_ms") or 0), 0)
        ttft = max(int(t.get("ttft_ms") or 0), 0)
        gen = max(int(t.get("gen_ms") or 0), 0)
        tools = max(int(t.get("tools_ms") or 0), 0)
        approval = max(int(t.get("approval_ms") or 0), 0)
        total = max(prep + ttft + gen + tools, 1)

        n_prep, n_ttft, n_gen, n_tools = _segment_blocks(prep, ttft, gen, tools, total, width)
        bar = "█" * n_prep + "▓" * n_ttft + "▒" * n_gen + "░" * n_tools
        turn_no = int(t.get("turn") or 0)
        row = f"  {turn_no:>3} │{bar.ljust(width)}│ {_fmt_ms(int(t.get('turn_ms') or total))}"
        detail = (
            f"  prep {_fmt_ms(prep)} · first token {_fmt_ms(ttft)} · "
            f"gen {_fmt_ms(gen)} · tools {_fmt_ms(tools)}"
        )
        if approval > 0:
            detail += f" · ⏸ approval {_fmt_ms(approval)}"
        lines.append(row + detail)
    return lines


def _segment_blocks(
    prep: int, ttft: int, gen: int, tools: int, total: int, width: int
) -> tuple[int, int, int, int]:
    """Block counts for one bar: proportional, at least one per non-zero stage,
    never wider than *width*."""

    def n(ms: int) -> int:
        if ms <= 0:
            return 0
        # A non-zero stage is always visible, even on a short bar.
        return max(1, round(ms / total * width))

    counts = [n(prep), n(ttft), n(gen), n(tools)]
    while sum(counts) > width:
        # Shrink the largest stage first so the bar keeps its shape.
        biggest = max(range(4), key=lambda i: counts[i])
        if counts[biggest] <= 1:
            break
        counts[biggest] -= 1
    return counts[0], counts[1], counts[2], counts[3]


def render_trace_block(
    used_chars: int,
    turns: list[dict[str, Any]],
    *,
    width: int = 24,
    budget: int = 4000,
) -> list[str]:
    """The `/trace` **Latency** section, sized to the message it goes into.

    *used_chars* is the length of everything already composed. Rows are added
    oldest-last until the budget runs out, so the X-ray can never be the reason
    `/trace` exceeds Telegram's 4096-character cap — the block is simply shorter
    (or absent) on a long trace. Returns an empty list when nothing fits.
    """
    if not turns:
        return []
    title = "<b>Latency</b> - last turns, newest first (█ prep · ▓ first token · ▒ gen · ░ tools)"
    # Title + fences + the newlines between the five block lines.
    reserve = len(title) + len("<code>") + len("</code>") + 5
    room = budget - used_chars - reserve
    if room <= 0:
        return []
    rows: list[str] = []
    for row in render_bars(turns, width=width):
        if len(row) + 1 > room:
            break
        rows.append(row)
        room -= len(row) + 1
    if not rows:
        return []
    return [title, "<code>", *rows, "</code>"]


def _fmt_ms(ms: int) -> str:
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms / 1000:.1f}s"
