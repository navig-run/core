"""Codex CLI sessions (``~/.codex/sessions/**/rollout-*.jsonl`` and ``archived_sessions``).

Kept: ``response_item`` messages from ``user`` and ``assistant``. Skipped: ``developer``
messages and user messages that are harness-injected context (environment, AGENTS.md
instructions, permissions). Titles come from ``session_index.jsonl``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from navig.recall.model import Turn
from navig.recall.sources.base import ParseResult, SourceRef, iter_jsonl, real_key

ADAPTER = "codex"
_INJECTED_PREFIXES = (
    "<environment_context>", "# AGENTS.md instructions", "<user_instructions>", "<permissions",
    "<INSTRUCTIONS>", "<turn_aborted>",
)
_MAX_TURN_CHARS = 60_000


def codex_home() -> Path:
    env = os.environ.get("CODEX_HOME", "").strip()
    return Path(env) if env else Path.home() / ".codex"


def _titles(home: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    p = home / "session_index.jsonl"
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict) and d.get("id") and d.get("thread_name"):
                out[str(d["id"])] = str(d["thread_name"])
    except OSError:
        pass
    return out


def discover(home: Path | None = None) -> Iterator[SourceRef]:
    home = home or codex_home()
    titles = _titles(home)
    seen: set[str] = set()
    for sub in ("sessions", "archived_sessions"):
        try:
            files = sorted((home / sub).rglob("rollout-*.jsonl"))
        except OSError:
            continue
        for f in files:
            k = real_key(f)
            sid = f.stem.split("-", 6)[-1] if f.stem.count("-") >= 6 else f.stem
            if k in seen or sid in seen:
                continue
            seen.update((k, sid))
            yield SourceRef(source_id=f"codex:{sid}", adapter=ADAPTER, path=str(f),
                            meta={"session_id": sid, "title": titles.get(sid, "")})


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts = []
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and isinstance(c.get("text"), str):
                parts.append(c["text"])
    return "\n".join(parts)


def parse(path: Path, start: int = 0, pending: dict[str, Any] | None = None) -> ParseResult:
    res = ParseResult(turns=[], end_offset=start)
    for off, nxt, rec in iter_jsonl(path, start):
        res.end_offset = nxt
        if rec is None:
            res.bad_lines += 1
            continue
        payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
        ts = str(rec.get("timestamp") or payload.get("timestamp") or "")
        if rec.get("type") == "session_meta":
            res.cwd = res.cwd or str(payload.get("cwd") or "")
            continue
        if rec.get("type") == "turn_context" and not res.cwd:
            res.cwd = str(payload.get("cwd") or "")
            continue
        if rec.get("type") != "response_item" or payload.get("type") != "message":
            continue
        role = payload.get("role")
        if role not in ("user", "assistant"):
            continue
        text = _content_text(payload.get("content")).strip()
        if not text or (role == "user" and text.lstrip().startswith(_INJECTED_PREFIXES)):
            continue
        if ts:
            res.first_ts = res.first_ts or ts
            res.last_ts = ts
        if len(text) > _MAX_TURN_CHARS:
            text = text[:_MAX_TURN_CHARS] + "\n…[truncated]"
        res.turns.append(Turn(msg_id=f"o{off}", speaker="user_typed" if role == "user" else "assistant",
                              text=text, ts=ts))
    return res
